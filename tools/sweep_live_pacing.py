"""Bounded no-FEC pacing comparisons over an already admitted persistent UART session."""
import argparse,concurrent.futures,json,re,secrets,time,struct,zlib
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from analyze_audio import assess
from stat_integrity import validate as validate_stat

def _sample_once(port):
    nonce=secrets.token_hex(4);reply=command(port,'STAT '+nonce,'WMP_STAT_CRC');lines={}
    try:integrity=validate_stat(reply['reply'],nonce)
    except ValueError as error:raise ValueError(str(error)+'; nonce='+nonce+'; retained at '+reply['evidence']) from error
    for line in reply['reply'].splitlines():
        if not line.startswith('WMP_') or ' query=' not in line:continue
        values={k:int(v,16 if k=='query' else 10) for k,v in re.findall(r'(\w+)=(-?[0-9a-f]+)(?:\s|$)',line)}
        if values.get('query')!=int(nonce,16):continue
        lines[line.split()[0]]=values
    required={
      'WMP_AUDIO':['running','rate','qfull','decoded','codec_bad','pcm_bad','concealed','late','write_errors','clock_valid','underrun','output_frames','phase_us'],
      'WMP_BT':['working','phone','streaming','ble','ble_writes','frames','rtp_gaps','rtp_bad','blocked_frames'],
      'WMP_RX':['qfull','bad','conflict','unique'],'WMP_RADIO':['mode','time_us'],
      'WMP_DMA':['start_us32','queue_overflow'],'WMP_PEERS':['classic_hi','classic_lo','standby_frames'],
      'WMP_MEMORY':['free','minimum','largest'],'WMP_SOURCE_QUEUE':['full','deferred','playback_deferred','groups','bytes','max_groups','max_bytes','max_age_us'],'WMP_TIMELINE':['recoveries'],
      'WMP_BT_LINKS':['classic_disconnects','ble_disconnects','connection_failures'],
      'WMP_BEACONS':['match_age_us'],'WMP_TX_SCHEDULE':['period_us'],
      'WMP_REPLAY':['enabled','nack_queued','nack_sent','nack_heard','suppressed','queued','cache_miss','deferred','too_late','received','delivered','played'],
      'WMP_BUFFER':['queued_on_time_late','input_late','startup_skips','queue_wait_max_us','held_max'],
      'WMP_ETH':['enabled'],'WMP_ASSOC':['connected'],
      'WMP_SOURCE_CLOCK':['valid','interface_sta','age_us','span_us'],
      'WMP_CLOCK_HEALTH':['expirations','unavailable_skips'],'WMP_PREPARATION':['source_wait','receiver_wait','source_skipped_frames','receiver_skipped_groups'],
      'WMP_COPY':['generation','paced','spacing','fec'],'WMP_PHY':['selected_mbps'],
      'WMP_PROFILE_STAT':['status'],'WMP_RAW_PATH':['action_frames'],'WMP_VHCI':['dropped'],
      'WMP_STORAGE':['tx_stack_free','rx_stack_free'],'WMP_POOL':['audio_stack_free'],'WMP_BT_MEMORY':['host_stack_free'],
      'WMP_ASSOC_EVENTS':['connected','disconnected'],'WMP_NETWORK':['infrastructure','status'],
      'WMP_WIFI_MODE':['ap','channel'],'WMP_WIFI_STATE':['sniffer'],
      'WMP_STAT_END':['heap']}
    required['WMP_SESSION']=['generation','source','crc']
    required['WMP_AUDIO_RESOURCES']=['ready','wanted','stopping','starting','arena_bytes','pcm_bytes','codec_bytes','starts','stops','codec_starts','codec_stops','failures']
    required['WMP_BT_RESOURCES']=['source_bytes','assembler_bytes','starts','stops','failures']
    required['WMP_RADIO_RESOURCES']=['source_slots_bytes','starts','stops','failures','history_bytes']
    required['WMP_GATT']=['applied','notifications']
    required['WMP_TX_DITHER']=['us','first_copy_unchanged','absolute']
    required['WMP_CONSOLE']=['buffered','rx_bytes','tx_bytes']
    missing=[section+'.'+key for section,keys in required.items() for key in keys if key not in lines.get(section,{})]
    if missing:raise ValueError('Incomplete STAT telemetry retained at '+reply['evidence']+': '+','.join(missing))
    session=lines['WMP_SESSION']
    if session['source'] not in (0,1) or zlib.crc32(struct.pack('<QB',session['generation'],session['source']))!=session['crc']:raise ValueError('Session telemetry CRC mismatch retained at '+reply['evidence'])
    return dict(evidence=reply['evidence'],lines=lines,integrity=integrity)

def sample(port):
    errors=[]
    for attempt in range(4):
        try:
            result=_sample_once(port)
            if errors:result['telemetry_read_errors']=errors
            return result
        except (ValueError,RuntimeError) as error:
            # Only fresh-nonce, read-only STAT requests may be retried.
            if isinstance(error,RuntimeError) and 'reply absent: WMP_STAT_CRC' not in str(error):raise
            errors.append(repr(error))
            if attempt==3:raise

def pair(pool):
    jobs={p:pool.submit(sample,p) for p in ['COM6','COM7']}
    return {p:f.result() for p,f in jobs.items()}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seconds',type=int,default=60);a=p.parse_args()
    if not 30<=a.seconds<=300:p.error('30..300 seconds per finite case')
    active=json.loads((ROOT/'hardware/active-session.json').read_text())
    if active.get('state')!='running':raise RuntimeError('Start the owned persistent capture first')
    out=ROOT/'evidence'/('pacing-'+str(time.time_ns()));out.mkdir()
    (out/'sweep.py').write_bytes(Path(__file__).read_bytes());print('Sweep:',out,flush=True)
    row=dict(cases=[],errors=[],acquisition_complete=False,audio_qualified=False,
             note='All cases require actual phone A2DP and BLE; no pause or synthetic audio. Compare original-copy train spans against observed47..63ms gaps.')
    cases=[(2000,1,2000),(6000,1,2000),(16000,0,2000),(28000,0,2000),(16000,1,1000)]
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            initial=pair(pool);sources=[p for p,v in initial.items() if v['lines']['WMP_RADIO']['mode']==2]
            if len(sources)!=1:raise RuntimeError('Ambiguous source')
            source=sources[0];row['source']=source;row['persistent_evidence']=active['evidence']
            for spacing,paced,period in cases:
                controls=[command(source,f'SCHEDULE {spacing} {paced}','WMP_SCHEDULE'),command(source,f'TXPACE {period}','WMP_TXPACE')]
                if any('accepted=1' not in c['reply'] for c in controls):raise RuntimeError('Control rejected')
                time.sleep(.35)  # old finite copies drain, music continues
                case=dict(spacing_us=spacing,paced=paced,period_us=period,controls=controls,samples=[],acquisition_complete=False,audio_qualified=False)
                row['cases'].append(case);start=time.monotonic()
                while True:
                    ports=pair(pool);elapsed=time.monotonic()-start
                    case['samples'].append(dict(elapsed=elapsed,ports=ports));save(out/'receipt.json',row)
                    b=ports[source]['lines']['WMP_BT']
                    if not all(b.get(k) for k in ['phone','streaming','ble']):raise RuntimeError('Required live workload disappeared')
                    first=case['samples'][0]['ports'][source]['lines']
                    if b['blocked_frames']!=first['WMP_BT']['blocked_frames'] or ports[source]['lines']['WMP_AUDIO']['concealed']!=first['WMP_AUDIO']['concealed']:
                        raise RuntimeError('Source backpressure/output fault; stop this configuration')
                    if elapsed>=a.seconds:case['acquisition_complete']=True;break
                    time.sleep(min(5,a.seconds-elapsed))
                case['assessment']=assess(case);save(out/'receipt.json',row)
                r=case['assessment'];print(json.dumps(dict(spacing=spacing,paced=paced,period=period,digital_pass=r['digital_pass'],failures=r['failures'],seconds=r.get('seconds'))),flush=True)
            clean=[c for c in row['cases'] if c['assessment']['digital_pass']]
            def faults(c):
                r=c['assessment'];receiver=r['receiver'];d=r['deltas'][receiver]['WMP_AUDIO'];return d['concealed']+d['late']+d['underrun']
            selected=min(clean or row['cases'],key=faults)
            row['selected']={k:selected[k] for k in ['spacing_us','paced','period_us']}
            row['restore_controls']=[command(source,f"SCHEDULE {selected['spacing_us']} {selected['paced']}",'WMP_SCHEDULE'),command(source,f"TXPACE {selected['period_us']}",'WMP_TXPACE')]
            row['acquisition_complete']=True
    except Exception as e:
        row['errors'].append(repr(e))
        if row.get('source'):
            try:
                row['failure_restore']=[command(row['source'],'SCHEDULE 2000 1','WMP_SCHEDULE'),command(row['source'],'TXPACE 2000','WMP_TXPACE')]
            except Exception as restore:row['errors'].append('restore: '+repr(restore))
    save(out/'receipt.json',row);print(json.dumps(dict(evidence=str(out),complete=row['acquisition_complete'],selected=row.get('selected'),errors=row['errors'])),flush=True)
if __name__=='__main__':main()
