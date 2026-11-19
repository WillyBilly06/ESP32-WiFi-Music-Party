"""Finite live-music spacing/pacing sweep; never pauses media or restarts radio."""
import argparse,concurrent.futures,json,time
from pathlib import Path
from board_io import ROOT,save
from capture_audio import sample
from capture_copies import command,capture
from analyze_audio import assess

def pair():
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jobs={p:pool.submit(sample,p) for p in ['COM6','COM7']}
        return {p:f.result() for p,f in jobs.items()}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',choices=['COM6','COM7'],default='COM7');p.add_argument('--seconds',type=int,default=20);a=p.parse_args()
    if not 15<=a.seconds<=120:p.error('15..120s per finite window')
    source=a.source;receiver='COM6' if source=='COM7' else 'COM7'
    out=ROOT/'evidence'/('copy-sweep-'+str(time.time_ns()));out.mkdir();(out/'sweep.py').write_bytes(Path(__file__).read_bytes())
    report=dict(pass_all=False,audio_qualified=False,source=source,receiver=receiver,cases=[],errors=[],started_ns=time.time_ns())
    print('Sweep:',out,flush=True)
    def control(port,line,reply,needle):
        r=command(port,line,reply)
        if needle not in r['reply']:raise RuntimeError(r)
        return r['evidence']
    def run_case(spacing,paced,hint,index):
        case=out/('case-'+str(index));case.mkdir();controls=[]
        controls.append(control(receiver,'COEX '+str(hint),'WMP_COEX','status=0'))
        controls.append(control(source,f'SCHEDULE {spacing} {paced}','WMP_SCHEDULE','accepted=1'))
        time.sleep(3)  # application capture settles; music and BLE continue
        row=dict(pass_all=False,acquisition_complete=False,audio_qualified=False,samples=[],errors=[],spacing=spacing,paced=paced,receiver_coex_hint=hint,controls=controls)
        start=time.monotonic()
        while True:
            current=pair();row['samples'].append(dict(elapsed=time.monotonic()-start,ports=current));save(case/'receipt.json',row)
            b=current[source]['lines']['WMP_BT'];r=current[source]['lines']['WMP_RADIO']
            if r['mode']!=2 or not all(b[k] for k in ['working','phone','streaming','ble']):raise RuntimeError('Required source workload/role disappeared')
            first=row['samples'][0]['ports'][source]['lines'];now=current[source]['lines']
            source_fault=any(now[s][k]>first[s][k] for s,k in [('WMP_BT','blocked_frames'),('WMP_AUDIO','concealed'),('WMP_AUDIO','qfull')])
            if source_fault:row['ended_early']='Source playback/input fault; stop this setting';break
            elapsed=time.monotonic()-start
            if elapsed>=a.seconds:break
            time.sleep(min(5,a.seconds-elapsed))
        row['pass_all']=row['acquisition_complete']=True;save(case/'receipt.json',row)
        assessment=assess(row);save(case/'digital-assessment.json',assessment)
        traces=capture(source,receiver,case)
        # capture() uses receipt.json; preserve acquisition separately.
        save(case/'copies.json',traces);save(case/'receipt.json',row)
        d=assessment['deltas'];s=d[source];r=d[receiver]
        faults=sum(s['WMP_AUDIO'][k] for k in ['qfull','codec_bad','pcm_bad','concealed','late','write_errors','underrun'])+s['WMP_BT']['blocked_frames']
        conceal=r['WMP_AUDIO']['concealed'];groups=r['WMP_AUDIO']['decoded']/4+conceal
        result=dict(path=str(case),spacing=spacing,paced=paced,receiver_coex_hint=hint,source_faults=faults,receiver_concealed=conceal,conceal_fraction=conceal/groups if groups else 1,receiver_late=r['WMP_AUDIO']['late'],rx_qfull=r['WMP_RX']['qfull'],ble_writes=s['WMP_BT']['ble_writes'],digital_pass=assessment['digital_pass'],matched_copy_groups=len(traces['joined']),no_copy_observed=len(traces['all_expected_completed_but_none_observed']))
        report['cases'].append(result);save(out/'receipt.json',report);print(json.dumps(result),flush=True);return result
    def score(c):return (bool(c['source_faults']),c['source_faults'],c['conceal_fraction'],c['receiver_late'],c['rx_qfull'])
    original=None
    try:
        # Read back the live settings first so a failure restores them, not assumed defaults.
        initial=pair();original=dict(spacing=initial[source]['lines']['WMP_COPY']['spacing'],paced=initial[source]['lines']['WMP_COPY']['paced'],source_hint=initial[source]['lines']['WMP_COPY']['coex_hint'],receiver_hint=initial[receiver]['lines']['WMP_COPY']['coex_hint']);report['original']=original;save(out/'receipt.json',report)
        control(source,'COEX 0','WMP_COEX','status=0')
        for spacing in [6000,4000,8000,10000,12000]:
            for paced in [0,1]:run_case(spacing,paced,0,len(report['cases']))
        # Test the public coexistence preference hint on the best three source
        # schedules; a hint is not a guaranteed hardware RF reservation.
        best=sorted(report['cases'],key=score)[:3]
        for c in best:run_case(c['spacing'],c['paced'],1,len(report['cases']))
        chosen=min(report['cases'],key=score)
        control(receiver,'COEX '+str(chosen['receiver_coex_hint']),'WMP_COEX','status=0')
        control(source,f'SCHEDULE {chosen["spacing"]} {chosen["paced"]}','WMP_SCHEDULE','accepted=1')
        report['selected']=chosen;report['pass_all']=True
    except Exception as error:
        report['errors'].append(repr(error))
        # Restore only the read-back original settings; if they were never read, change nothing. No reset or music pause.
        if original is not None:
            try:
                control(source,f'SCHEDULE {original["spacing"]} {original["paced"]}','WMP_SCHEDULE','accepted=1')
                control(source,'COEX '+str(original['source_hint']),'WMP_COEX','status=0')
                control(receiver,'COEX '+str(original['receiver_hint']),'WMP_COEX','status=0')
                report['restored']=original
            except Exception as restore:report['errors'].append('restore: '+repr(restore))
    finally:report['finished_ns']=time.time_ns();save(out/'receipt.json',report)
    print(json.dumps(dict(evidence=str(out),pass_all=report['pass_all'],selected=report.get('selected'),errors=report['errors'])),flush=True)
if __name__=='__main__':main()
