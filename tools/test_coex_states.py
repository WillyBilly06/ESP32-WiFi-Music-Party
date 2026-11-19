"""Compare real controller profile/sleep states with continuous music and BLE."""
import argparse,concurrent.futures,json,time
from pathlib import Path
from board_io import ROOT,save
from capture_audio import sample
from capture_copies import command
from analyze_audio import assess

def control(port,line,reply):
    r=command(port,line,reply)
    if 'status=0' not in r['reply'] and 'accepted=1' not in r['reply']:raise RuntimeError(r)
    return r['evidence']
def pair():
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jobs={p:pool.submit(sample,p) for p in ['COM6','COM7']}
        return {p:f.result() for p,f in jobs.items()}
def main():
    raise SystemExit('Retired FEC-era experiment. Current no-FEC priority comparisons use compare_standby_priority.py; failed historical receipts remain preserved.')
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seconds',type=int,default=60);a=p.parse_args()
    if not 30<=a.seconds<=180:p.error('Finite30..180s per condition')
    out=ROOT/'evidence'/('coex-states-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes())
    report=dict(pass_all=False,audio_qualified=False,cases=[],errors=[]);print('States:',out,flush=True)
    def settings(sleep,profile):
        refs=[]
        for port in ['COM7','COM6']:
            refs.append(control(port,'BTSLEEP '+str(sleep),'WMP_POWER'))
            refs.append(control(port,'A2DPCOEX '+str(profile),'WMP_A2DP_COEX'))
        return refs
    try:
        control('COM7','FEC 1','WMP_FEC_REQUEST');control('COM7','SCHEDULE 6000 0','WMP_SCHEDULE')
        for port in ['COM6','COM7']:control(port,'COEX 0','WMP_COEX')
        for index,(sleep,profile) in enumerate([(1,0),(1,1),(0,1)]):
            case=out/str(index);case.mkdir();refs=settings(sleep,profile);time.sleep(8)
            row=dict(pass_all=False,acquisition_complete=False,audio_qualified=False,sleep=sleep,profile=profile,controls=refs,samples=[],errors=[])
            start=time.monotonic()
            while True:
                current=pair();row['samples'].append(dict(elapsed=time.monotonic()-start,ports=current));save(case/'receipt.json',row)
                bt=current['COM7']['lines']['WMP_BT']
                if current['COM7']['lines']['WMP_RADIO']['mode']!=2 or not all(bt[k] for k in ['working','phone','streaming','ble']):raise RuntimeError('Required actual workload disappeared')
                elapsed=time.monotonic()-start
                if elapsed>=a.seconds:break
                time.sleep(min(5,a.seconds-elapsed))
            row['pass_all']=row['acquisition_complete']=True;save(case/'receipt.json',row);result=assess(row);save(case/'digital-assessment.json',result)
            summary=dict(path=str(case),sleep=sleep,profile=profile,digital_pass=result['digital_pass'],failures=result['failures'],source_ble_writes=result['deltas']['COM7']['WMP_BT']['ble_writes'],receiver_concealed=result['deltas']['COM6']['WMP_AUDIO']['concealed'],receiver_late=result['deltas']['COM6']['WMP_AUDIO']['late'],repair_qfull=result['deltas']['COM7'].get('WMP_FEC',{}).get('qfull'))
            report['cases'].append(summary);save(out/'receipt.json',report);print(json.dumps(summary),flush=True)
        report['pass_all']=True
    except Exception as error:report['errors'].append(repr(error))
    finally:
        try:report['restored']=settings(0,1)
        except Exception as error:report['errors'].append('restore: '+repr(error));report['pass_all']=False
        save(out/'receipt.json',report)
    print(json.dumps(dict(evidence=str(out),pass_all=report['pass_all'],errors=report['errors'])),flush=True)
if __name__=='__main__':main()
