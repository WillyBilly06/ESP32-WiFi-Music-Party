"""Original(MCS7/HT20 65)/24/original Mbps comparison with automatic wake registration and intact source.

The original selected rate (WMP_PHY.selected_mbps) is read first and restored afterwards."""
import concurrent.futures,json,subprocess,sys,time,traceback
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair
# ESP-IDF wifi_phy_rate_t values reported in WMP_PHY.tx_rate; 65 is MCS7 HT20 long GI.
PHY_RATE={6:0x0b,9:0x0f,12:0x0a,18:0x0e,24:0x09,36:0x0d,48:0x08,54:0x0c,65:0x17}

def main():
    out=ROOT/'evidence'/('awake-phy-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes());r=dict(cases=[],errors=[],diagnostic_only=True,audio_qualified=False,acquisition_complete=False);original={};print(out,flush=True)
    try:
        from adb_local import require_testing,SAMSUNG
        require_testing(SAMSUNG)  # local admission check before any board/phone action
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:initial=pair(pool)
        original={p:v['lines']['WMP_PHY']['selected_mbps'] for p,v in initial.items()};r.update(before=initial,original=original)
        if any(not v['lines']['WMP_RECEIVE_WINDOW']['enabled'] or v['lines']['WMP_RECEIVE_WINDOW']['interval_ms'] for v in initial.values()):raise RuntimeError('Require registered windows and default interval')
        if len(set(original.values()))!=1 or original['COM6'] not in PHY_RATE or original['COM6']==24:raise RuntimeError('Both boards must share one known non-24 original rate: '+str(original))
        for rate in [original['COM6'],24,original['COM6']]:
            c=dict(rate_mbps=rate,controls=[]);r['cases'].append(c)
            for port in ['COM6','COM7']:
                v=command(port,'RATE '+str(rate),'WMP_RATE');c['controls'].append(v);save(out/'receipt.json',r)
                if 'status=0' not in v['reply']:raise RuntimeError('Rate rejected '+port)
            time.sleep(3);cmd=[sys.executable,str(ROOT/'tools/measure_audio_pipeline.py'),'--seconds','30'];v=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True);c.update(command=cmd,exit_code=v.returncode,stdout=v.stdout,stderr=v.stderr)
            path=Path(v.stdout.splitlines()[0].strip()).resolve();path.relative_to((ROOT/'evidence').resolve());t=json.loads((path/'receipt.json').read_text());c['measurement']=str(path/'receipt.json');save(out/'receipt.json',r)
            if v.returncode or not t['acquisition_complete']:raise RuntimeError('Actual workload observation failed: '+str(t['errors']))
            for sample in t['samples']:
                if any(v['lines']['WMP_PHY']['selected_mbps']!=rate or v['lines']['WMP_PHY']['tx_rate']!=PHY_RATE[rate] for v in sample['ports'].values()):raise RuntimeError('Actual PHY callback does not match requested rate')
            source=t['source'];receiver=t['receiver'];c.update(summary=t['summary'],delta=t['delta']);save(out/'receipt.json',r)
            print(dict(rate=rate,source_conceals=t['delta'][source]['WMP_AUDIO.concealed'],receiver_conceals=t['delta'][receiver]['WMP_AUDIO.concealed'],receiver_late=t['delta'][receiver]['WMP_AUDIO.late'],source_blocked=t['delta'][source]['WMP_BT.blocked_frames']),flush=True)
        r['acquisition_complete']=True
    except Exception as e:r['errors'].append(str(e));r['traceback']=traceback.format_exc()
    finally:
        r['restore']=[]
        for p,value in original.items():
            try:
                v=command(p,'RATE '+str(value),'WMP_RATE');r['restore'].append(v)
                if 'status=0' not in v['reply']:raise RuntimeError('Restore rejected')
            except Exception as e:r['errors'].append('restore '+p+' '+str(e))
        save(out/'receipt.json',r)
    print(dict(acquisition_complete=r['acquisition_complete'],errors=r['errors']),flush=True)
    if r['errors']:raise SystemExit(1)
if __name__=='__main__':main()
