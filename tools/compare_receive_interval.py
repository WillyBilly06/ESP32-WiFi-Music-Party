"""Compare default/20ms/default wake cadence without reducing actual audio/BLE."""
import concurrent.futures,json,subprocess,sys,time,traceback
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair

def main():
    out=ROOT/'evidence'/('wake-interval-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes());r=dict(cases=[],errors=[],diagnostic_only=True,audio_qualified=False,acquisition_complete=False);original={};print(out,flush=True)
    try:
        from adb_local import require_testing,SAMSUNG
        require_testing(SAMSUNG)  # local admission check before any board/phone action
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:initial=pair(pool)
        original={p:v['lines']['WMP_RECEIVE_WINDOW']['interval_ms'] for p,v in initial.items()};r.update(before=initial,original=original)
        if any(not v['lines']['WMP_RECEIVE_WINDOW']['enabled'] for v in initial.values()):raise RuntimeError('Keep wake windows registered')
        for interval in [0,20,0]:
            c=dict(interval_ms=interval,controls=[]);r['cases'].append(c)
            for port in ['COM6','COM7']:
                v=command(port,'RXINTERVAL '+str(interval),'WMP_RECEIVE_INTERVAL');c['controls'].append(v);save(out/'receipt.json',r)
                if 'status=0' not in v['reply']:raise RuntimeError('Interval rejected '+port)
            time.sleep(3);cmd=[sys.executable,str(ROOT/'tools/measure_audio_pipeline.py'),'--seconds','30'];v=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True);c.update(command=cmd,exit_code=v.returncode,stdout=v.stdout,stderr=v.stderr)
            path=Path(v.stdout.splitlines()[0].strip()).resolve();path.relative_to((ROOT/'evidence').resolve());t=json.loads((path/'receipt.json').read_text());c['measurement']=str(path/'receipt.json');save(out/'receipt.json',r)
            if v.returncode or not t['acquisition_complete']:raise RuntimeError('Actual workload observation failed: '+str(t['errors']))
            source=t['source'];receiver=t['receiver'];c.update(summary=t['summary'],delta=t['delta']);save(out/'receipt.json',r)
            print(dict(interval=interval,original_pps=c['summary'][source]['original_completed_pps'],source_conceals=t['delta'][source]['WMP_AUDIO.concealed'],receiver_conceals=t['delta'][receiver]['WMP_AUDIO.concealed'],input_gaps={p:d['WMP_BT.rtp_gaps'] for p,d in t['delta'].items()}),flush=True)
        r['acquisition_complete']=True
    except Exception as e:r['errors'].append(str(e));r['traceback']=traceback.format_exc()
    finally:
        r['restore']=[]
        for p,value in original.items():
            try:
                v=command(p,'RXINTERVAL '+str(value),'WMP_RECEIVE_INTERVAL');r['restore'].append(v)
                if 'status=0' not in v['reply']:raise RuntimeError('Restore rejected')
            except Exception as e:r['errors'].append('restore '+p+' '+str(e))
        save(out/'receipt.json',r)
    print(dict(acquisition_complete=r['acquisition_complete'],errors=r['errors']),flush=True)
    if r['errors']:raise SystemExit(1)
if __name__=='__main__':main()
