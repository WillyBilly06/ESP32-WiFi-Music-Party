"""Finite four-copy spacing comparison on unchanged live music and BLE."""
import concurrent.futures,time,traceback
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair

def main():
    out=ROOT/'evidence'/('copy-diversity-'+str(time.time_ns()));out.mkdir()
    row=dict(cases=[],errors=[],audio_qualified=False,acquisition_complete=False,scope='One live REDMAGIC A2DP source, two BLE clients, direct Wi-Fi; no pause, reset or role command during comparison.')
    owned=[];print(out,flush=True)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            initial=pair(pool);row['initial']=initial
            if initial['COM7']['lines']['WMP_RADIO']['mode']!=2 or initial['COM7']['lines']['WMP_RADIO']['copies']!=4:raise RuntimeError('Need running four-copy source')
            for p in ('COM7','COM6'):
                if not initial[p]['lines']['WMP_RADIO_RESOURCES']['history_bytes']:
                    if 'accepted=1' not in command(p,'COPYLOG 1','WMP_TRACE_RESOURCE')['reply']:raise RuntimeError('History rejected')
                    owned.append(p)
            command('COM7','REPAIRCFG 8000 20000 40000 40000 0','WMP_REPAIR_CONFIG')
            for spacing in (32000,48000,64000,96000):
                case=dict(spacing_us=spacing,samples=[],faults=[]);row['cases'].append(case)
                case['control']=command('COM7','SCHEDULE '+str(spacing)+' 0','WMP_SCHEDULE')
                if 'accepted=1' not in case['control']['reply']:raise RuntimeError('Schedule rejected')
                time.sleep(1)
                for p in ('COM7','COM6'):command(p,'GAPRESET','WMP_GAP_RESET')
                first=pair(pool);case['before']=first;start=time.monotonic()
                while True:
                    states=first if not case['samples'] else pair(pool);elapsed=time.monotonic()-start
                    case['samples'].append(dict(elapsed=elapsed,ports=states));save(out/'receipt.json',row)
                    for p,v in states.items():
                        a=v['lines'];b=first[p]['lines']
                        if not a['WMP_BT']['ble'] or not a['WMP_AUDIO']['running'] or (p=='COM7' and not a['WMP_BT']['streaming']):raise RuntimeError('Live workload disappeared')
                        if a['WMP_SESSION']['generation']!=b['WMP_SESSION']['generation']:raise RuntimeError('Session changed')
                        for k in ('concealed','late','qfull','codec_bad','pcm_bad','underrun','write_errors'):
                            if a['WMP_AUDIO'][k]!=b['WMP_AUDIO'][k]:case['faults'].append(p+' '+k)
                        for section,key in [('WMP_SOURCE_QUEUE','full'),('WMP_VHCI','dropped'),('WMP_RX','qfull')]:
                            if a[section][key]!=b[section][key]:case['faults'].append(p+' '+section+'.'+key)
                    if case['faults'] or elapsed>=45:break
                    time.sleep(1)
                case['completed_seconds']=elapsed;case['digital_window_pass']=not case['faults'] and elapsed>=45
                print(dict(spacing_us=spacing,seconds=round(elapsed,2),pass_window=case['digital_window_pass'],faults=case['faults']),flush=True)
                save(out/'receipt.json',row)
            row['acquisition_complete']=True
    except Exception as error:row['errors'].append(str(error));row['traceback']=traceback.format_exc()
    finally:
        for p in owned:
            try:command(p,'COPYLOG 0','WMP_TRACE_RESOURCE')
            except Exception as error:row['errors'].append('History cleanup '+p+': '+str(error))
        save(out/'receipt.json',row)
    print(dict(acquisition_complete=row['acquisition_complete'],errors=row['errors']),flush=True)
    if row['errors']:raise SystemExit(1)

if __name__=='__main__':main()
