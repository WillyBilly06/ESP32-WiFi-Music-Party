"""Selected-group repair test with retained physical tags; intentional loss."""
import argparse,concurrent.futures,json,re,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from delivery_trace import read,stable,stages,control_state
from test_burst_repair import read_records
from sweep_live_pacing import pair,sample
from audio_fixture import FIXTURES,require as require_fixture,check_sources
from qualify_party_long import PHONES,peer,media
from firmware_evidence import installed_pair,live_pair

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',type=Path,required=True);p.add_argument('--fixture',choices=FIXTURES,default='redmagic-linux');p.add_argument('--cases',type=int,default=8);p.add_argument('--burst',type=int,default=1);p.add_argument('--offset',type=int,default=0);p.add_argument('--natural',action='store_true');a=p.parse_args()
    if not 1<=a.cases<=20 or not 1<=a.burst<=7 or not 0<=a.offset<a.burst:p.error('cases1..20, burst1..7, offset inside burst')
    if a.natural and (a.burst!=1 or a.offset):p.error('Natural observation has no injected burst/offset')
    out=ROOT/'evidence'/('delivery-path-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes());print(out,flush=True)
    row=dict(cases=[],errors=[],controls=[],diagnostic_loss_injection=not a.natural,natural_observation=a.natural,audio_qualified=False,acquisition_complete=False)
    history_owned=False;fault_owned=False;focused=[];source=None
    try:
        row['fixture']=require_fixture(a.fixture);row['binding']=installed_pair(a.app);row['live']=live_pair(row['binding'],command)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            before=pair(pool);leaders=[port for port,v in before.items() if v['lines']['WMP_RADIO']['mode']==2]
            if len(leaders)!=1:raise RuntimeError('Need one actual source')
            source=leaders[0];receiver=next(port for port in before if port!=source);row.update(source=source,receiver=receiver,before=before)
            phones=[PHONES[peer(v['lines'])] for v in before.values()];check_sources(row['fixture'],phones);row['media_before']=[media(phone) for phone in phones]
            if any(v.get('playback_state',v.get('youtube_state')) not in ('PLAYING','3') for v in row['media_before']):raise RuntimeError('Actual music absent')
            if any(not all(v['lines']['WMP_BT'][k] for k in ('phone','streaming','ble')) for v in before.values()):raise RuntimeError('Full A2DP/BLE fixture absent')
            for port in (source,receiver):
                if read(port)['tail']['loaded']:raise RuntimeError('Existing focus belongs to another observation')
            if not before[source]['lines']['WMP_RADIO_RESOURCES']['history_bytes']:
                r=command(source,'COPYLOG 1','WMP_TRACE_RESOURCE');row['controls'].append(r)
                if 'accepted=1' not in r['reply']:raise RuntimeError('Source history allocation rejected')
                history_owned=True
            # This global ring can legitimately change continuously. The
            # selected-identity delivery trace, with per-event CRC, is the
            # primary recovery evidence; a moving unrelated fault is not a
            # reason to discard that retained packet's complete trace.
            initial=read_records(receiver,'FAULTTRACE','WMP_FAULT','WMP_FAULT_END');row['initial_fault_read']=initial
            if not initial['tail']['loaded']:
                r=command(receiver,'FAULTLOG 1','WMP_FAULT_RESOURCE');row['controls'].append(r)
                if 'accepted=1' not in r['reply']:raise RuntimeError('Fault history allocation rejected')
                fault_owned=True
            armed=sample(source)['lines']
            if armed['WMP_COPY']['kind']!=1 or armed['WMP_RADIO']['copies']!=4:raise RuntimeError('Need armed actual four-copy source')
            generation=armed['WMP_SESSION']['generation']
            for index in range(a.cases):
                case=dict(index=index,burst=a.burst,offset=a.offset,controls=[]);row['cases'].append(case);save(out/'receipt.json',row)
                if a.natural:
                    state=sample(source)['lines'];gen=state['WMP_SESSION']['generation'];first=state['WMP_COPY']['highest']+100;end=first+1;count=4
                else:
                    r=command(source,'DROPRANGE 100 '+str(a.burst),'WMP_DROP_TEST');case['injection']=r
                    if 'accepted=1' not in r['reply']:raise RuntimeError('Diagnostic suppression rejected')
                    m=re.search(r'WMP_DROP_TARGET generation=(\d+) first=(\d+) end=(\d+) copies=(\d+)',r['reply'])
                    if not m:raise RuntimeError('No exact injection identity')
                    gen,first,end,count=map(int,m.groups())
                seq=first+a.offset;case['target']=dict(generation=gen,sequence=seq,first=first,end=end)
                if gen!=generation or count!=a.burst*4:raise RuntimeError('Wrong epoch or copy count')
                for port in (source,receiver):
                    r=command(port,'FOCUS '+str(gen)+' '+str(seq),'WMP_FOCUS accepted=');case['controls'].append(r)
                    if 'accepted=1' not in r['reply'] or ('generation='+str(gen)+' seq='+str(seq)) not in r['reply']:raise RuntimeError('Focus rejected or wrong identity '+port)
                    focused.append(port)
                time.sleep(3)
                srcf=pool.submit(stable,source);rxf=pool.submit(stable,receiver);tx=srcf.result();rx=rxf.result();case.update(sender=tx,receiver=rx)
                case['source_control_states']=[control_state(v['status']) for v in stages(tx,12)]
                case['receiver_control_tx_signatures']=[v['status'] for v in stages(rx,1,replay=False)]
                for trace in (tx,rx):
                    if trace['meta']['generation']!=gen or trace['meta']['seq']!=seq:raise RuntimeError('Focus identity mismatch')
                if not a.natural and len(stages(tx,11,replay=False))!=4:raise RuntimeError('Target passed before focus or originals not fully suppressed')
                if not tx['meta']['known']:raise RuntimeError('Source target metadata missing')
                faults=read_records(receiver,'FAULTTRACE','WMP_FAULT','WMP_FAULT_END');case['faults']=faults
                sample_first=tx['meta']['sample'];sample_end=sample_first+tx['meta']['samples']
                case['target_faults']=[f for f in faults['rows'] if f['generation']==gen and sample_first<=f['sample']<sample_end]
                case['fault_scan_limit']='Rolling16-entry global ring; empty target matches do not prove fault absence. Positive target recovery requires retained CRC-checked handoff and intact decode events.'
                accepted={v['tag'] for v in stages(tx,2,True,0)};completed={v['tag'] for v in stages(tx,3,True,0)};callbacks={v['tag'] for v in stages(rx,4,True)};valid={v['tag'] for v in stages(rx,6,True,0)}
                case['repair_tags']=dict(api_accepted=sorted(accepted),driver_completed=sorted(completed),receiver_callbacks=sorted(callbacks),integrity_valid=sorted(valid),completed_without_callback=sorted(completed-callbacks))
                case['target_recovered']=bool(stages(rx,7,status=1) and stages(rx,8,status=1) and not case['target_faults'])
                case['receiver_dedup_decisions']=[v for v in rx['rows'] if v['stage'] in (13,14)]
                case['note']='Driver completion is not RF receipt; missing callback is scoped to this retained identity and capture lifetime.'
                for port in tuple(focused):
                    r=command(port,'FOCUSCLEAR','WMP_FOCUS_CLEAR');case['controls'].append(r)
                    if 'accepted=1' not in r['reply']:raise RuntimeError('Focus cleanup rejected')
                    focused.remove(port)
                if not a.natural:command(source,'DROPCLEAR','WMP_DROP_CLEAR')
                save(out/'receipt.json',row)
                print(dict(case=index,recovered=case['target_recovered'],accepted=len(accepted),completed=len(completed),callbacks=len(callbacks),faults=len(case['target_faults'])),flush=True)
            after=pair(pool);row['after']=after;row['media_after']=[media(phone) for phone in phones]
            fields={'WMP_BT':['frames','rtp_gaps','blocked_frames'],'WMP_GATT':['applied','notifications'],'WMP_AUDIO':['decoded','concealed','late','underrun'],'WMP_SOURCE_QUEUE':['full']}
            row['workload_delta']={port:{section+'.'+key:after[port]['lines'][section][key]-before[port]['lines'][section][key] for section,keys in fields.items() for key in keys} for port in before}
            if any(row['workload_delta'][port][key]<=0 for port in before for key in ('WMP_BT.frames','WMP_GATT.applied','WMP_GATT.notifications')):raise RuntimeError('Actual A2DP/BLE workload did not progress')
            if any(v['lines']['WMP_SESSION']['generation']!=generation or not all(v['lines']['WMP_BT'][k] for k in ('phone','streaming','ble')) for v in after.values()):raise RuntimeError('Full fixture/session changed')
            if any(read(port)['tail']['loaded'] for port in before):raise RuntimeError('Focus allocation remained after cleanup')
            row['acquisition_complete']=True
    except Exception as error:row['errors'].append(repr(error))
    finally:
        cleanup=[(port,'FOCUSCLEAR','WMP_FOCUS_CLEAR') for port in focused]
        if source and not a.natural:cleanup.append((source,'DROPCLEAR','WMP_DROP_CLEAR'))
        if fault_owned:cleanup.append((receiver,'FAULTLOG 0','WMP_FAULT_RESOURCE'))
        if history_owned:cleanup.append((source,'COPYLOG 0','WMP_TRACE_RESOURCE'))
        for port,line,marker in cleanup:
            try:
                r=command(port,line,marker);row['controls'].append(r)
                if 'accepted=1' not in r['reply']:raise RuntimeError('Cleanup rejected')
            except Exception as error:row['errors'].append('cleanup '+port+': '+repr(error))
    save(out/'receipt.json',row);print(dict(evidence=str(out),complete=row['acquisition_complete'],errors=row['errors']),flush=True)
    if row['errors']:raise SystemExit(1)

if __name__=='__main__':main()
