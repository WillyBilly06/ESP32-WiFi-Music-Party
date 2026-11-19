"""Delay early missing-group requests while retaining four originals and repair."""
import argparse,concurrent.futures,json,subprocess,sys,time,traceback
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--extended',action='store_true');args=parser.parse_args()
    out=ROOT/'evidence'/('nack-delay-awake-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes());r=dict(cases=[],errors=[],diagnostic_only=True,audio_qualified=False,acquisition_complete=False);original={};print(out,flush=True)
    try:
        from adb_local import require_testing,SAMSUNG
        require_testing(SAMSUNG)  # local admission check before any board/phone action
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:initial=pair(pool)
        original={p:s['lines']['WMP_REPLAY']['nack_delay_us'] for p,s in initial.items()};r.update(before=initial,original=original)
        if any(not s['lines']['WMP_RECEIVE_WINDOW']['enabled'] for s in initial.values()):raise RuntimeError('Keep connectionless wake registration enabled')
        for delay in ([120000,240000,120000] if args.extended else [60000,120000,60000]):
            c=dict(delay_us=delay,controls=[]);r['cases'].append(c)
            for port in ['COM6','COM7']:
                v=command(port,'NACKDELAY '+str(delay),'WMP_NACK_DELAY');c['controls'].append(v);save(out/'receipt.json',r)
                if 'accepted=1' not in v['reply']:raise RuntimeError('Delay rejected '+port)
            time.sleep(3);cmd=[sys.executable,str(ROOT/'tools/measure_audio_pipeline.py'),'--seconds','30'];v=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True);c.update(command=cmd,exit_code=v.returncode,stdout=v.stdout,stderr=v.stderr)
            path=Path(v.stdout.splitlines()[0].strip()).resolve();path.relative_to((ROOT/'evidence').resolve());t=json.loads((path/'receipt.json').read_text());c['measurement']=str(path/'receipt.json');save(out/'receipt.json',r)
            if v.returncode or not t['acquisition_complete']:raise RuntimeError('Actual workload observation failed: '+str(t['errors']))
            source=t['source'];receiver=t['receiver'];c.update(summary=t['summary'],delta=t['delta']);save(out/'receipt.json',r)
            print(dict(delay=delay,source_conceals=t['delta'][source]['WMP_AUDIO.concealed'],receiver_conceals=t['delta'][receiver]['WMP_AUDIO.concealed'],receiver_nacks=t['delta'][receiver]['WMP_REPLAY.nack_sent'],repair_submissions=t['delta'][source]['WMP_REPAIR_AIR.accepted']),flush=True)
        r['acquisition_complete']=True
    except Exception as e:r['errors'].append(str(e));r['traceback']=traceback.format_exc()
    finally:
        r['restore']=[]
        for port,delay in original.items():
            try:
                v=command(port,'NACKDELAY '+str(delay),'WMP_NACK_DELAY');r['restore'].append(v)
                if 'accepted=1' not in v['reply']:raise RuntimeError('Restore rejected')
            except Exception as e:r['errors'].append('restore '+port+' '+str(e))
        save(out/'receipt.json',r)
    print(dict(acquisition_complete=r['acquisition_complete'],errors=r['errors']),flush=True)
    if r['errors']:raise SystemExit(1)
if __name__=='__main__':main()
