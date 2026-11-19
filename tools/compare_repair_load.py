"""Diagnostic on/off/on repair comparison; never a replacement release mode."""
import argparse,concurrent.futures,time,traceback
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--rate-sweep',action='store_true');args=parser.parse_args()
    out=ROOT/'evidence'/('repair-load-'+str(time.time_ns()));out.mkdir()
    row=dict(diagnostic_only=True,audio_qualified=False,cases=[],errors=[],acquisition_complete=False,note='Four original copies remain on throughout. Repair-off is only a causal diagnostic; final mode restores repairs.')
    original_rates={};original_replay={}
    print(out,flush=True)
    try:
        from adb_local import require_testing,SAMSUNG
        require_testing(SAMSUNG)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            baseline=pair(pool);row['initial']=baseline
            if baseline['COM7']['lines']['WMP_RADIO']['mode']!=2 or baseline['COM6']['lines']['WMP_RADIO']['mode']!=0:raise RuntimeError('Legacy comparator requires COM7 source; no controls applied')
            if args.rate_sweep:original_rates={port:v['lines']['WMP_PHY']['selected_mbps'] for port,v in baseline.items()}
            original_replay={port:v['lines']['WMP_REPLAY']['enabled'] for port,v in baseline.items()}
            plans=[(1,54),(1,24),(1,12)] if args.rate_sweep else [(1,None),(0,None),(1,None)]
            for enabled,rate in plans:
                case=dict(repair_enabled=enabled,rate_mbps=rate,samples=[]);row['cases'].append(case)
                for p in ('COM7','COM6'):
                    if rate is not None:
                        result=command(p,'RATE '+str(rate),'WMP_RATE')
                        if 'status=0' not in result['reply']:raise RuntimeError('PHY rate rejected')
                    r=command(p,'REPLAY '+str(enabled),'WMP_REPLAY_REQUEST')
                    if 'accepted=1' not in r['reply']:raise RuntimeError('Repair mode rejected')
                time.sleep(1);first=pair(pool);start=time.monotonic()
                gen=first['COM7']['lines']['WMP_SESSION']['generation']
                if first['COM7']['lines']['WMP_RADIO']['copies']!=4 or not gen:raise RuntimeError('Wrong source load')
                if rate is not None and first['COM7']['lines']['WMP_PHY']['tx_rate']!={54:12,24:9,12:10}[rate]:raise RuntimeError('Reported PHY differs from requested rate')
                while True:
                    ports=first if not case['samples'] else pair(pool);elapsed=time.monotonic()-start
                    case['samples'].append(dict(elapsed=elapsed,ports=ports));save(out/'receipt.json',row)
                    for p,v in ports.items():
                        s=v['lines']
                        if not all(s['WMP_BT'][k] for k in ('phone','streaming','ble')):raise RuntimeError('Full two-source workload disappeared: '+p)
                        if s['WMP_SESSION']['generation']!=gen or s['WMP_REPLAY']['enabled']!=enabled:raise RuntimeError('Session or requested mode changed')
                    if elapsed>=30:break
                    time.sleep(2)
                case['elapsed']=elapsed
                case['delta']={p:{section+'.'+key:ports[p]['lines'][section][key]-first[p]['lines'][section][key] for section,keys in {'WMP_AUDIO':['concealed','late','qfull'],'WMP_BT':['rtp_gaps','frames','ble_writes'],'WMP_GATT':['notifications'],'WMP_RADIO':['attempt','accepted','done','api_error','qfull'],'WMP_REPLAY':['nack_sent','queued','played'],'WMP_SOURCE_QUEUE':['full'],'WMP_RX':['qfull','bad']}.items() for key in keys} for p in ('COM7','COM6')}
                print(dict(enabled=enabled,rate_mbps=rate,elapsed=round(elapsed,2),source_conceals=case['delta']['COM7']['WMP_AUDIO.concealed'],receiver_conceals=case['delta']['COM6']['WMP_AUDIO.concealed'],tx=case['delta']['COM7']['WMP_RADIO.attempt'],repairs=case['delta']['COM7']['WMP_REPLAY.queued']),flush=True)
                save(out/'receipt.json',row)
            row['acquisition_complete']=True
    except Exception as error:row['errors'].append(str(error));row['traceback']=traceback.format_exc()
    finally:
        for p,enabled in original_replay.items():
            try:command(p,'REPLAY '+str(enabled),'WMP_REPLAY_REQUEST')
            except Exception as error:row['errors'].append('Restore '+p+': '+str(error))
        for p,rate in original_rates.items():
            try:
                r=command(p,'RATE '+str(rate),'WMP_RATE')
                if 'status=0' not in r['reply']:raise RuntimeError('Rate restore rejected')
            except Exception as error:row['errors'].append('Rate restore '+p+': '+str(error))
        save(out/'receipt.json',row)
    print(dict(acquisition_complete=row['acquisition_complete'],errors=row['errors']),flush=True)
    if row['errors']:raise SystemExit(1)

if __name__=='__main__':main()
