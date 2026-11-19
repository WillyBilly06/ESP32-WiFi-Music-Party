"""Compare quiet versus dense STAT output; music and BLE never pause."""
import concurrent.futures,time,traceback
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair
from qualify_party_long import linux_status

def main():
    out=ROOT/'evidence'/('logging-load-'+str(time.time_ns()));out.mkdir();report=dict(diagnostic_only=True,audio_qualified=False,cases=[],errors=[],acquisition_complete=False);source=None;original=None;print(out,flush=True)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            initial=pair(pool);report['initial']=initial;source=next(p for p,v in initial.items() if v['lines']['WMP_RADIO']['mode']==2);receiver=next(p for p in initial if p!=source);original=initial[source]['lines']['WMP_TX_DITHER']['us']
            r=command(source,'TXDITHER 8000','WMP_TX_DITHER_REQUEST')
            if 'accepted=1' not in r['reply']:raise RuntimeError('Cannot hold copy dephasing constant')
            time.sleep(.25)
            for dense in [0,1,0,1]:
                c=dict(dense_stat=dense,samples=[]);report['cases'].append(c);c['linux_before']=linux_status()['playback'];first=pair(pool);c['samples'].append(first);start=time.monotonic()
                if not c['linux_before'].get('ready'):raise RuntimeError('Linux source not route/render ready')
                if dense:
                    while time.monotonic()-start<30:c['samples'].append(pair(pool))
                else:time.sleep(30)
                last=pair(pool);c['samples'].append(last);c['elapsed']=time.monotonic()-start;c['linux_after']=linux_status()['playback']
                if not c['linux_after'].get('ready'):raise RuntimeError('Linux rendering stopped')
                for p in last:
                    a,b=first[p]['lines'],last[p]['lines']
                    if b['WMP_SESSION']['generation']!=a['WMP_SESSION']['generation'] or b['WMP_DMA']['start_us32']!=a['WMP_DMA']['start_us32']:raise RuntimeError('Epoch/DAC changed')
                    if b['WMP_PROFILE_STAT']['enabled']!=a['WMP_PROFILE_STAT']['enabled'] or b['WMP_TX_DITHER']['us']!=a['WMP_TX_DITHER']['us']:raise RuntimeError('Scheduling policy changed')
                    if not all(b['WMP_BT'][k] for k in ['phone','ble','transport_started']):raise RuntimeError('Radio workload disappeared')
                    for k in ['classic_disconnects','ble_disconnects']:
                        if b['WMP_BT_LINKS'][k]!=a['WMP_BT_LINKS'][k]:raise RuntimeError('Connection dropped')
                fields={'WMP_AUDIO':['concealed','late','underrun'],'WMP_BT':['rtp','rtp_gaps','blocked_frames'],'WMP_GATT':['applied','notifications'],'WMP_RADIO':['qfull','attempt','late'],'WMP_SOURCE_QUEUE':['full'],'WMP_RX':['unique','qfull','bad'],'WMP_DISPATCH':['over5','over10']}
                c['delta']={p:{s+'.'+k:last[p]['lines'][s][k]-first[p]['lines'][s][k] for s,keys in fields.items() for k in keys} for p in last}
                c['rejected_reads']=sum(len(v.get('telemetry_read_errors',[])) for row in c['samples'] for v in row.values())
                if any(c['delta'][p]['WMP_BT.rtp']<=0 or c['delta'][p]['WMP_GATT.applied']<=0 for p in last):raise RuntimeError('Real A2DP/BLE did not progress')
                save(out/'receipt.json',report);print(dict(dense=dense,seconds=round(c['elapsed'],2),snapshots=len(c['samples'])*2,source_conceals=c['delta'][source]['WMP_AUDIO.concealed'],receiver_conceals=c['delta'][receiver]['WMP_AUDIO.concealed'],source_queue_full=c['delta'][source]['WMP_SOURCE_QUEUE.full'],rejected=c['rejected_reads']),flush=True)
            report['acquisition_complete']=True
    except Exception as e:report['errors'].append(str(e));report['traceback']=traceback.format_exc()
    finally:
        if source and original is not None:
            try:report['restore']=command(source,'TXDITHER '+str(original),'WMP_TX_DITHER_REQUEST')
            except Exception as e:report['errors'].append('restore '+str(e))
        save(out/'receipt.json',report)
    print(dict(acquisition_complete=report['acquisition_complete'],errors=report['errors']),flush=True)
    if report['errors']:raise SystemExit(1)

if __name__=='__main__':main()
