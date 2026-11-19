"""Four-copy callback-spacing crossover with actual two-phone/BLE input."""
import concurrent.futures,json,subprocess,sys,time,traceback
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair

def main():
    out=ROOT/'evidence'/('completion-spacing-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes())
    r=dict(cases=[],errors=[],diagnostic_only=True,audio_qualified=False,acquisition_complete=False);source=None;original=None;print(out,flush=True)
    try:
        from adb_local import require_testing,SAMSUNG
        require_testing(SAMSUNG)  # local admission check before any board/phone action
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:s=pair(pool)
        sources=[p for p,v in s.items() if v['lines']['WMP_RADIO']['mode']==2]
        if len(sources)!=1:raise RuntimeError('Need one existing source')
        source=sources[0];base=s[source]['lines'];original=dict(spacing=base['WMP_COPY']['spacing'],paced=base['WMP_COPY']['paced'],dither=base['WMP_TX_DITHER']['us']);r.update(source=source,before=s,original=original)
        r['disable_dither']=command(source,'TXDITHER 0','WMP_TX_DITHER_REQUEST')
        if 'accepted=1' not in r['disable_dither']['reply']:raise RuntimeError('Dither change rejected')
        for spacing,paced in [(64000,0),(16000,1),(64000,0)]:
            c=dict(spacing=spacing,paced=paced);r['cases'].append(c);c['control']=command(source,f'SCHEDULE {spacing} {paced}','WMP_SCHEDULE');save(out/'receipt.json',r)
            if 'accepted=1' not in c['control']['reply']:raise RuntimeError('Schedule rejected')
            time.sleep(3)
            cmd=[sys.executable,str(ROOT/'tools/measure_audio_pipeline.py'),'--seconds','30'];result=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True);c.update(command=cmd,exit_code=result.returncode,stdout=result.stdout,stderr=result.stderr)
            path=Path(result.stdout.splitlines()[0].strip()).resolve();path.relative_to((ROOT/'evidence').resolve());receipt=path/'receipt.json';t=json.loads(receipt.read_text());c['measurement']=str(receipt)
            save(out/'receipt.json',r)
            if result.returncode or not t['acquisition_complete']:raise RuntimeError('Actual workload observation failed: '+str(t['errors']))
            d=t['delta'];c['summary']=t['summary'];c['fault_deltas']={p:{k:v for k,v in values.items() if k in ['WMP_BT.rtp_gaps','WMP_BT.blocked_frames','WMP_AUDIO.concealed','WMP_AUDIO.late','WMP_AUDIO.underrun','WMP_SOURCE_QUEUE.full']} for p,values in d.items()};save(out/'receipt.json',r)
            print(dict(spacing=spacing,paced=paced,original_pps=c['summary'][source]['original_completed_pps'],source_conceals=d[source]['WMP_AUDIO.concealed'],receiver_conceals=d[next(p for p in d if p!=source)]['WMP_AUDIO.concealed']),flush=True)
        r['acquisition_complete']=True
    except Exception as e:r['errors'].append(str(e));r['traceback']=traceback.format_exc()
    finally:
        if source and original:
            try:
                r['restore_schedule']=command(source,f"SCHEDULE {original['spacing']} {original['paced']}",'WMP_SCHEDULE')
                r['restore_dither']=command(source,'TXDITHER '+str(original['dither']),'WMP_TX_DITHER_REQUEST')
                if any('accepted=1' not in r[k]['reply'] for k in ['restore_schedule','restore_dither']):raise RuntimeError('Restore rejected')
            except Exception as e:r['errors'].append('restore '+str(e))
        save(out/'receipt.json',r)
    print(dict(acquisition_complete=r['acquisition_complete'],errors=r['errors']),flush=True)
    if r['errors']:raise SystemExit(1)
if __name__=='__main__':main()
