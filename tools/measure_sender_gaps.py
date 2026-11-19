"""Finite cumulative sender-gap measurement with actual A2DP/Wi-Fi/BLE load."""
import argparse,concurrent.futures,json,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair
from analyze_audio import assess
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seconds',type=int,default=120);a=p.parse_args()
    if not 30<=a.seconds<=3600:p.error('30..3600 seconds')
    out=ROOT/'evidence'/('sender-gaps-'+str(time.time_ns()));out.mkdir();(out/'capture.py').write_bytes(Path(__file__).read_bytes())
    row=dict(samples=[],errors=[],acquisition_complete=False,audio_qualified=False,requested_seconds=a.seconds,
             limits=['Largest observed in this finite workload; no absolute future worst-case guarantee',
                     'Sender completion callbacks are not hardware RF timestamps or peer ACKs',
                     'Source counters cover original audio copies; useful receiver gap includes valid recovery'])
    print('Gap window:',out,flush=True)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            initial=pair(pool);sources=[p for p,v in initial.items() if v['lines']['WMP_RADIO']['mode']==2]
            if len(sources)!=1:raise RuntimeError('Ambiguous source')
            source=sources[0];receiver='COM6' if source=='COM7' else 'COM7';row.update(source=source,receiver=receiver)
            reset={p:pool.submit(command,p,'GAPRESET','WMP_GAP_RESET') for p in [source,receiver]};row['reset']={p:f.result() for p,f in reset.items()}
            if any('accepted=1' not in r['reply'] for r in row['reset'].values()):raise RuntimeError('Gap reset rejected')
            time.sleep(.15);start=time.monotonic();generation=None
            while True:
                ports=pair(pool);elapsed=time.monotonic()-start;row['samples'].append(dict(elapsed=elapsed,ports=ports));save(out/'receipt.json',row)
                b=ports[source]['lines']['WMP_BT'];gen=ports[source]['lines']['WMP_COPY']['generation']
                if not all(b.get(k) for k in ['phone','streaming','ble']):raise RuntimeError('Required live A2DP/BLE workload disappeared')
                if generation is None:generation=gen
                if gen!=generation:raise RuntimeError('Source generation changed; gap window is not continuous')
                if elapsed>=a.seconds:row['acquisition_complete']=True;break
                time.sleep(min(5,a.seconds-elapsed))
            s=row['samples'][-1]['ports'][source]['lines']['WMP_GAPS'];r=row['samples'][-1]['ports'][receiver]['lines']['WMP_GAPS']
            row['maximum_observed_us']={k:s[k] for k in ['submit_max_us','accepted_max_us','completion_max_us','firstcopy_max_us']}
            row['maximum_observed_us']['useful_receiver_gap']=r['useful_rx_max_us']
            row['source_events']=s['submit_events'];row['source_measured_span_us']=s['span_us'];row['assessment']=assess(row)
    except Exception as e:row['errors'].append(repr(e))
    save(out/'receipt.json',row);print(json.dumps(dict(evidence=str(out),complete=row['acquisition_complete'],maximum_observed_us=row.get('maximum_observed_us'),events=row.get('source_events'),digital_pass=row.get('assessment',{}).get('digital_pass'),errors=row['errors'])),flush=True)
if __name__=='__main__':main()
