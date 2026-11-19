"""Bounded unchanged music capture with optional histories and fresh counters."""
import concurrent.futures,secrets,time,traceback
from board_io import ROOT,save
from capture_copies import command,_capture
from sweep_live_pacing import pair

def main():
    out=ROOT/'evidence'/('loss-stages-'+str(time.time_ns()));out.mkdir()
    row=dict(scope='One live REDMAGIC source, both Wi-Fi outputs and two BLE clients; optional copy history enabled for this diagnostic.',samples=[],captures=[],errors=[],audio_qualified=False,acquisition_complete=False)
    owned=[];print(out,flush=True)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            before=pair(pool);row['before']=before
            for p,v in before.items():
                if not v['lines']['WMP_BT']['ble'] or not v['lines']['WMP_AUDIO']['running']:raise RuntimeError('Need existing music/BLE: '+p)
                if not v['lines']['WMP_RADIO_RESOURCES']['history_bytes']:
                    r=command(p,'COPYLOG 1','WMP_TRACE_RESOURCE')
                    if 'accepted=1' not in r['reply']:raise RuntimeError('History rejected: '+p)
                    owned.append(p)
            time.sleep(1);start=time.monotonic()
            while time.monotonic()-start<45:
                value=pair(pool);row['samples'].append(dict(elapsed=time.monotonic()-start,ports=value))
                if not value['COM7']['lines']['WMP_BT']['streaming']:raise RuntimeError('Live source stopped')
                if not all(v['lines']['WMP_BT']['ble'] for v in value.values()):raise RuntimeError('BLE disappeared')
                path=out/('capture-'+str(len(row['captures'])));path.mkdir()
                try:
                    result=_capture('COM7','COM6',path)
                    row['captures'].append(dict(path=str(path),coverage=result['coverage'],all_completed_none_seen=result['all_expected_completed_but_none_observed']))
                except Exception as error:row['captures'].append(dict(path=str(path),error=str(error)))
                if len(row['samples'])>1 and value['COM6']['lines']['WMP_AUDIO']['concealed']>row['samples'][-2]['ports']['COM6']['lines']['WMP_AUDIO']['concealed']:
                    nonce=secrets.token_hex(4);r=command('COM7','REPAIRTRACE '+nonce,'WMP_REPAIR_END query='+nonce);(path/'repair.raw').write_text(r['reply'])
                save(out/'receipt.json',row)
            row['acquisition_complete']=True
    except Exception as error:row['errors'].append(str(error));row['traceback']=traceback.format_exc()
    finally:
        for p in owned:
            try:command(p,'COPYLOG 0','WMP_TRACE_RESOURCE')
            except Exception as error:row['errors'].append('History cleanup '+p+': '+str(error))
        save(out/'receipt.json',row)
    print(dict(acquisition_complete=row['acquisition_complete'],captures=len(row['captures']),errors=row['errors']),flush=True)

if __name__=='__main__':main()
