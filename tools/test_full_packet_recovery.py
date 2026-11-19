"""Real music/BLE test: suppress one original group, verify cached full replay."""
import concurrent.futures,json,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command,capture
from sweep_live_pacing import pair
from analyze_audio import assess
def main():
    out=ROOT/'evidence'/('recovery-test-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes())
    row=dict(diagnostic_loss_injection=True,acquisition_complete=False,audio_qualified=False,samples=[],errors=[])
    print('Recovery test:',out,flush=True)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            initial=pair(pool);sources=[p for p,v in initial.items() if v['lines']['WMP_RADIO']['mode']==2]
            if len(sources)!=1:raise RuntimeError('Ambiguous source')
            source=sources[0];receiver='COM6' if source=='COM7' else 'COM7';row.update(source=source,receiver=receiver)
            b=initial[source]['lines']['WMP_BT'];a=initial[receiver]['lines']['WMP_AUDIO']
            if not all(b[k] for k in ['phone','streaming','ble']) or not a['running'] or not a['clock_valid']:raise RuntimeError('Live workload/receiver not ready')
            start=time.monotonic();row['samples'].append(dict(elapsed=0,ports=initial))
            row['control']=command(source,'DROPONE 10','WMP_DROP_TEST')
            if 'accepted=1' not in row['control']['reply']:raise RuntimeError('Loss injection not accepted')
            time.sleep(.45);trace_out=out/'paired-copy-trace';trace_out.mkdir();trace=capture(source,receiver,trace_out)
            row['trace_evidence']=str(trace_out/'receipt.json')
            row['controlled_groups']=[v for v in trace['joined'] if v['tx_attempt']==0 and v['tx_accepted']==0 and v['rx_callback']==0 and v['full_packet_replay']]
            while True:
                ports=pair(pool);elapsed=time.monotonic()-start;row['samples'].append(dict(elapsed=elapsed,ports=ports));save(out/'receipt.json',row)
                if elapsed>=30:break
                time.sleep(min(2,30-elapsed))
            row['acquisition_complete']=True;row['assessment']=assess(row)
            first,last=row['samples'][0]['ports'],row['samples'][-1]['ports']
            row['suppressed_original_copies']=last[source]['lines']['WMP_DIAGNOSTIC']['intentionally_dropped_copies']-first[source]['lines']['WMP_DIAGNOSTIC']['intentionally_dropped_copies']
            replay={p:{k:last[p]['lines']['WMP_REPLAY'][k]-first[p]['lines']['WMP_REPLAY'][k] for k in ['queued','delivered','played','cache_miss','too_late']} for p in [source,receiver]};row['replay']=replay
            row['recovery_pass']=bool(row['assessment']['digital_pass'] and row['suppressed_original_copies']==4 and replay[source]['queued']>0 and replay[receiver]['delivered']>0 and replay[receiver]['played']>0 and row['controlled_groups'])
    except Exception as e:row['errors'].append(repr(e));row['recovery_pass']=False
    save(out/'receipt.json',row);print(json.dumps(dict(evidence=str(out),pass_all=row.get('recovery_pass'),controlled_groups=[r['sequence'] for r in row.get('controlled_groups',[])],replay=row.get('replay'),failures=row.get('assessment',{}).get('failures'),errors=row['errors'])),flush=True)
if __name__=='__main__':main()
