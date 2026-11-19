"""Same-image RF comparisons with paired channels and real continuous A2DP/BLE."""
import argparse,concurrent.futures,json,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair
from analyze_audio import assess

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seconds',type=int,default=90);a=p.parse_args()
    if not 60<=a.seconds<=300:p.error('60..300 seconds per case')
    out=ROOT/'evidence'/('rf-'+str(time.time_ns()));out.mkdir();(out/'sweep.py').write_bytes(Path(__file__).read_bytes())
    row=dict(cases=[],errors=[],acquisition_complete=False,audio_qualified=False)
    print('RF sweep:',out,flush=True)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            ports=pair(pool);sources=[p for p,v in ports.items() if v['lines']['WMP_RADIO']['mode']==2]
            if len(sources)!=1:raise RuntimeError('Ambiguous source')
            source=sources[0];receiver='COM6' if source=='COM7' else 'COM7';row.update(source=source,receiver=receiver)
            row['original']=dict(channels={p:v['lines']['WMP_WIFI_MODE']['channel'] for p,v in ports.items()},receiver_beacon_tu=ports[receiver]['lines']['WMP_WIFI_MODE']['beacon_tu']);save(out/'receipt.json',row)
            for channel,tu in [(6,10000),(6,100),(6,1000)]:
                c=dict(channel=channel,receiver_beacon_tu=tu,samples=[],acquisition_complete=False,audio_qualified=False)
                row['cases'].append(c);c['beacon_control']=command(receiver,f'RXBEACON {tu}','WMP_RXBEACON')
                if 'accepted=1' not in c['beacon_control']['reply']:raise RuntimeError('Beacon request rejected')
                jobs={p:pool.submit(command,p,f'CHANNEL {channel}','WMP_CHANNEL') for p in [source,receiver]};c['channel_controls']={p:f.result() for p,f in jobs.items()}
                if any('status=0' not in r['reply'] for r in c['channel_controls'].values()):raise RuntimeError('Paired retune failed')
                time.sleep(2) # preserve transition in persistent raw log; separate measurement window
                start=time.monotonic()
                while True:
                    ports=pair(pool);elapsed=time.monotonic()-start;c['samples'].append(dict(elapsed=elapsed,ports=ports));save(out/'receipt.json',row)
                    if any(v['lines']['WMP_WIFI_MODE']['channel']!=channel for v in ports.values()):raise RuntimeError('Channel mismatch')
                    if ports[receiver]['lines']['WMP_WIFI_MODE']['beacon_tu']!=tu:raise RuntimeError('Beacon config not applied')
                    b=ports[source]['lines']['WMP_BT']
                    if not all(b[k] for k in ['streaming','phone','ble']):raise RuntimeError('Actual workload disappeared')
                    first=c['samples'][0]['ports'][source]['lines']
                    if any(ports[source]['lines']['WMP_AUDIO'][k]!=first['WMP_AUDIO'][k] for k in ['concealed','late','underrun']) or b['blocked_frames']!=first['WMP_BT']['blocked_frames']:
                        raise RuntimeError('Source playback/input fault; abort immediately')
                    if elapsed>=a.seconds:c['acquisition_complete']=True;break
                    time.sleep(min(5,a.seconds-elapsed))
                c['assessment']=assess(c);save(out/'receipt.json',row)
                r=c['assessment'];print(json.dumps(dict(channel=channel,beacon_tu=tu,seconds=r.get('seconds'),digital_pass=r['digital_pass'],failures=r['failures'])),flush=True)
            def metric(c):
                r=c['assessment'];d=r['deltas'];a=d[receiver]['WMP_AUDIO'];s=d[source]['WMP_AUDIO']
                return sum(a[k] for k in ['concealed','late','underrun'])+100*sum(s[k] for k in ['concealed','late','underrun'])
            selected=min(row['cases'],key=metric);row['selected']={k:selected[k] for k in ['channel','receiver_beacon_tu']}
            row['restore_beacon']=command(receiver,f"RXBEACON {selected['receiver_beacon_tu']}",'WMP_RXBEACON')
            jobs={p:pool.submit(command,p,f"CHANNEL {selected['channel']}",'WMP_CHANNEL') for p in [source,receiver]};row['restore_channels']={p:f.result() for p,f in jobs.items()};row['acquisition_complete']=True
    except Exception as e:
        row['errors'].append(repr(e))
        # Restore the read-back original values; if they were never read nothing was changed.
        if 'original' in row:
            try:
                o=row['original'];row['failure_restore_beacon']=command(row['receiver'],f"RXBEACON {o['receiver_beacon_tu']}",'WMP_RXBEACON')
                row['failure_restore_channels']={p:command(p,f'CHANNEL {c}','WMP_CHANNEL') for p,c in o['channels'].items()}
                if 'accepted=1' not in row['failure_restore_beacon']['reply'] or any('status=0' not in r['reply'] for r in row['failure_restore_channels'].values()):raise RuntimeError('Original RF settings not accepted')
            except Exception as restore:row['errors'].append('restore: '+repr(restore))
    save(out/'receipt.json',row);print(json.dumps(dict(evidence=str(out),complete=row['acquisition_complete'],selected=row.get('selected'),errors=row['errors'])),flush=True)
if __name__=='__main__':main()
