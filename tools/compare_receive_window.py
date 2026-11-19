"""Always-awake connectionless-window diagnostic on the unchanged stream."""
import concurrent.futures,json,subprocess,sys,time,traceback
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair

def main():
    out=ROOT/'evidence'/('receive-window-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes());r=dict(cases=[],errors=[],diagnostic_only=True,audio_qualified=False,acquisition_complete=False);original={};print(out,flush=True)
    try:
        from adb_local import require_testing,SAMSUNG
        require_testing(SAMSUNG)  # local admission check before any board/phone action
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:initial=pair(pool)
        original={p:s['lines']['WMP_RECEIVE_WINDOW']['enabled'] for p,s in initial.items()};r.update(before=initial,original=original)
        for enabled in [0,1,0]:
            c=dict(enabled=enabled,controls=[]);r['cases'].append(c)
            for port in ['COM6','COM7']:
                reply=command(port,'RXWINDOW '+str(enabled),'WMP_RECEIVE_WINDOW_REQUEST');c['controls'].append(reply);save(out/'receipt.json',r)
                if 'accepted=1' not in reply['reply']:raise RuntimeError('Wake-window command rejected '+port)
            time.sleep(2)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:s=pair(pool)
            if any(v['lines']['WMP_RECEIVE_WINDOW']['enabled']!=enabled or v['lines']['WMP_RECEIVE_WINDOW']['status'] for v in s.values()):raise RuntimeError('Wake-window state failed')
            cmd=[sys.executable,str(ROOT/'tools/measure_audio_pipeline.py'),'--seconds','30'];v=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True);c.update(command=cmd,exit_code=v.returncode,stdout=v.stdout,stderr=v.stderr)
            path=Path(v.stdout.splitlines()[0].strip()).resolve();path.relative_to((ROOT/'evidence').resolve());receipt=path/'receipt.json';t=json.loads(receipt.read_text());c['measurement']=str(receipt);save(out/'receipt.json',r)
            if v.returncode or not t['acquisition_complete']:raise RuntimeError('Actual workload observation failed: '+str(t['errors']))
            source=t['source'];receiver=t['receiver'];c.update(summary=t['summary'],source_conceals=t['delta'][source]['WMP_AUDIO.concealed'],source_blocked=t['delta'][source]['WMP_BT.blocked_frames'],source_input_gaps=t['delta'][source]['WMP_BT.rtp_gaps'],receiver_conceals=t['delta'][receiver]['WMP_AUDIO.concealed'],receiver_late=t['delta'][receiver]['WMP_AUDIO.late']);save(out/'receipt.json',r)
            print(dict(enabled=enabled,original_pps=c['summary'][source]['original_completed_pps'],source_conceals=c['source_conceals'],receiver_conceals=c['receiver_conceals']),flush=True)
        r['acquisition_complete']=True
    except Exception as e:r['errors'].append(str(e));r['traceback']=traceback.format_exc()
    finally:
        r['restore']=[]
        for p,value in original.items():
            try:
                reply=command(p,'RXWINDOW '+str(value),'WMP_RECEIVE_WINDOW_REQUEST');r['restore'].append(reply)
                if 'accepted=1' not in reply['reply']:raise RuntimeError('Restore rejected')
            except Exception as e:r['errors'].append('restore '+p+' '+str(e))
        save(out/'receipt.json',r)
    print(dict(acquisition_complete=r['acquisition_complete'],errors=r['errors']),flush=True)
    if r['errors']:raise SystemExit(1)
if __name__=='__main__':main()
