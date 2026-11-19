"""A/B repair pacing with labelled seven-group loss, live music and BLE."""
import argparse,concurrent.futures,re,secrets,time,traceback
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair

def read_records(port,verb,prefix,end):
    n=secrets.token_hex(4);reply=command(port,verb+' '+n,end+' query='+n)
    rows=[];tail=None
    for line in reply['reply'].splitlines():
        if not line.startswith((prefix+' ',end+' ')):continue
        values=dict(re.findall(r'(\w+)=(-?[0-9a-f]+)(?:\s|$)',line))
        if values.pop('query',None)!=n:raise RuntimeError('Wrong diagnostic nonce')
        fields={k:int(v) for k,v in values.items()}
        if line.startswith(prefix+' '):
            required={'WMP_FAULT':{'kind','generation','sample','pts','at_us','arrived_us','lead_us','frames','ready','held'},'WMP_DROP_RECORD':{'seq','sample','pts','mask'}}.get(prefix,set())
            if not required<=fields.keys():raise RuntimeError('Malformed diagnostic fields')
            rows.append(fields)
        else:tail=fields
    if tail is None:raise RuntimeError('Incomplete diagnostic')
    return dict(rows=rows,tail=tail,raw=reply['reply'])

def matched_records(port,verb,prefix,end,attempts):
    previous=None
    for _ in range(4):
        try:current=read_records(port,verb,prefix,end)
        except (RuntimeError,ValueError) as error:attempts.append(dict(error=str(error)));previous=None;continue
        attempts.append(current)
        if previous is not None and (previous['rows'],previous['tail'])==(current['rows'],current['tail']):return current
        previous=current
    raise RuntimeError('Diagnostic changed or lost serial bytes across four fresh reads')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--nack-delays',nargs='+',type=int);parser.add_argument('--light-repair',action='store_true');parser.add_argument('--dedup-comparison',action='store_true');args=parser.parse_args()
    if args.dedup_comparison and (args.nack_delays or args.light_repair):parser.error('Choose one comparison mode')
    if args.nack_delays and any(not 20000<=v<=120000 for v in args.nack_delays):parser.error('NACK delay20000..120000us')
    out=ROOT/'evidence'/('burst-repair-ab-'+str(time.time_ns()));out.mkdir()
    row=dict(diagnostic_loss_injection=True,source='COM7',receiver='COM6',cases=[],errors=[],audio_qualified=False,acquisition_complete=False)
    owned=[];fault_owned=False;original=None;print(out,flush=True)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            initial=pair(pool);row['before']=initial
            if not all(initial['COM7']['lines']['WMP_BT'][k] for k in ('phone','streaming','ble')) or not initial['COM6']['lines']['WMP_BT']['ble']:raise RuntimeError('Need actual A2DP plus both BLE clients')
            if initial['COM7']['lines']['WMP_RADIO']['copies']!=4:raise RuntimeError('Four actual original copies are mandatory')
            if not initial['COM7']['lines']['WMP_SESSION']['generation'] or initial['COM7']['lines']['WMP_SESSION']['generation']!=initial['COM6']['lines']['WMP_SESSION']['generation']:raise RuntimeError('Need one shared live session')
            # Read the live repair policy, NACK delay and dedup mode so cleanup restores them, not assumed defaults.
            tail=read_records('COM7','DROPTRACE','WMP_DROP_RECORD','WMP_DROP_END')['tail']
            original=row['original']=dict(repair=[tail[k] for k in ('pace_us','guard_us','budget_us','spacing_us','paced')],nack_delay_us=initial['COM6']['lines']['WMP_REPLAY']['nack_delay_us'],dedup=initial['COM7']['lines']['WMP_REPAIR_GATE']['enabled']);save(out/'receipt.json',row)
            for p in ('COM7','COM6'):
                if not initial[p]['lines']['WMP_RADIO_RESOURCES']['history_bytes']:
                    if 'accepted=1' not in command(p,'COPYLOG 1','WMP_TRACE_RESOURCE')['reply']:raise RuntimeError('History rejected')
                    owned.append(p)
            time.sleep(1)
            policies=[(80000,80000,110000,40000,1),(8000,80000,110000,40000,1),(8000,80000,110000,40000,0),(8000,80000,40000,40000,0),(8000,20000,40000,40000,0)]
            if args.light_repair:policies=[(8000,80000,200000,40000,0),(16000,80000,200000,40000,0),(24000,80000,200000,40000,0),(8000,20000,200000,40000,0)]
            if args.nack_delays:policies=[(8000,20000,40000,40000,0)]*len(args.nack_delays)
            delays=args.nack_delays or [60000 if args.light_repair else 120000]*len(policies)
            dedup_modes=[1]*len(policies)
            if args.dedup_comparison:
                policies=[(8000,80000,40000,40000,0)]*4;delays=[60000]*4;dedup_modes=[0,1,0,1]
            for policy,nack_delay,dedup in zip(policies,delays,dedup_modes):
                pace,guard,budget,spacing,paced=policy
                case=dict(pace_us=pace,policy=policy,nack_delay_us=nack_delay,repair_dedup=dedup,samples=[],errors=[]);row['cases'].append(case);save(out/'receipt.json',row)
                if args.dedup_comparison and 'accepted=1' not in command('COM7','REPAIRDEDUP '+str(dedup),'WMP_REPAIR_DEDUP')['reply']:raise RuntimeError('Repair ownership mode rejected')
                case['pace_control']=command('COM7','REPAIRCFG '+' '.join(map(str,policy)),'WMP_REPAIR_CONFIG')
                if 'accepted=1' not in case['pace_control']['reply']:raise RuntimeError('Pacing control rejected')
                if 'accepted=1' not in command('COM6','NACKDELAY '+str(nack_delay),'WMP_NACK_DELAY')['reply']:raise RuntimeError('NACK delay rejected')
                command('COM6','FAULTLOG 0','WMP_FAULT_RESOURCE')
                if 'accepted=1' not in command('COM6','FAULTLOG 1','WMP_FAULT_RESOURCE')['reply']:raise RuntimeError('Fault log rejected')
                fault_owned=True;time.sleep(.15)
                before=pair(pool);case['before']=before
                if before['COM6']['lines']['WMP_REPLAY']['nack_delay_us']!=nack_delay:raise RuntimeError('Actual NACK delay differs')
                if args.dedup_comparison and before['COM7']['lines']['WMP_REPAIR_GATE']['enabled']!=dedup:raise RuntimeError('Actual ownership mode differs')
                result=command('COM7','DROPRANGE 50 7','WMP_DROP_TEST');case['injection']=result
                if 'accepted=1' not in result['reply']:raise RuntimeError('Loss injection rejected')
                m=re.search(r'WMP_DROP_TARGET generation=(\d+) first=(\d+) end=(\d+) copies=(\d+)',result['reply'])
                if not m:raise RuntimeError('No exact injection identity')
                gen,first,end,copies=map(int,m.groups());case['target']=dict(generation=gen,first=first,end=end,copies=copies)
                start=time.monotonic()
                while time.monotonic()-start<3:
                    state=pair(pool);case['samples'].append(dict(elapsed=time.monotonic()-start,ports=state));save(out/'receipt.json',row)
                    if not state['COM7']['lines']['WMP_BT']['streaming'] or not all(v['lines']['WMP_BT']['ble'] for v in state.values()):raise RuntimeError('Actual music/BLE workload disappeared')
                    time.sleep(.1)
                drop=matched_records('COM7','DROPTRACE','WMP_DROP_RECORD','WMP_DROP_END',case.setdefault('drop_reads',[]));case['dropped']=drop
                faults=matched_records('COM6','FAULTTRACE','WMP_FAULT','WMP_FAULT_END',case.setdefault('fault_reads',[]));case['faults']=faults
                case['repair']=read_records('COM7','REPAIRTRACE','WMP_REPAIR_TRACE','WMP_REPAIR_END')
                after=pair(pool);case['after']=after
                records=drop['rows']
                complete=(len(records)==7 and {v['seq'] for v in records}==set(range(first,end)) and all(v['mask']==15 for v in records) and drop['tail']['generation']==gen and drop['tail']['remaining']==0 and not drop['tail']['active'] and all(drop['tail'][k]==v for k,v in zip(('pace_us','guard_us','budget_us','spacing_us','paced'),policy)))
                if not complete:raise RuntimeError('Incomplete or mismatched suppression plan')
                if not faults['tail']['loaded'] or faults['tail']['count']!=len(faults['rows']) or any(f['generation']!=gen for f in faults['rows']):raise RuntimeError('Fault log incomplete or wrong generation')
                target_samples={v['sample'] for v in records}
                target_pts=[v['pts'] for v in records]
                matches=lambda f:f['sample'] in target_samples or min(abs(f['pts']-pts) for pts in target_pts)<1000
                case['controlled_missing']=[f for f in faults['rows'] if matches(f) and f['kind']==0]
                case['controlled_late']=[f for f in faults['rows'] if matches(f) and f['kind']==1]
                delta=lambda p,s,k:after[p]['lines'][s][k]-before[p]['lines'][s][k]
                case['suppressed_copies']=delta('COM7','WMP_DIAGNOSTIC','intentionally_dropped_copies')
                case['replays_played']=delta('COM6','WMP_REPLAY','played')
                case['conceal_delta']=delta('COM6','WMP_AUDIO','concealed')
                case['source_conceal_delta']=delta('COM7','WMP_AUDIO','concealed')
                if args.dedup_comparison:case['pending_repair_suppression_delta']=delta('COM7','WMP_REPAIR_GATE','pending_suppressed')
                case['other_loss_stages']={p:{s+'.'+k:delta(p,s,k) for s,ks in {'WMP_SOURCE_QUEUE':['full'],'WMP_TIMELINE':['recoveries'],'WMP_BT':['rtp_gaps','rtp_bad'],'WMP_VHCI':['dropped'],'WMP_RX':['qfull','bad','conflict'],'WMP_BUFFER':['startup_skips'],'WMP_AUDIO_RESOURCES':['starts']}.items() for k in ks} for p in ('COM7','COM6')}
                case['recovery_pass']=case['suppressed_copies']==28 and not case['controlled_missing'] and case['replays_played']>=7 and not case['source_conceal_delta'] and not any(v for fields in case['other_loss_stages'].values() for v in fields.values()) and all(delta(p,'WMP_PREPARATION','receiver_skipped_groups')==0 for p in ('COM7','COM6'))
                target_path_ok=not any(value for port,fields in case['other_loss_stages'].items() for name,value in fields.items() if not (port=='COM6' and name in ('WMP_BT.rtp_gaps','WMP_BT.rtp_bad')))
                case['target_only_recovery']=case['suppressed_copies']==28 and not case['controlled_missing'] and case['replays_played']>=7 and not case['source_conceal_delta'] and target_path_ok and all(delta(port,'WMP_PREPARATION','receiver_skipped_groups')==0 for port in ('COM7','COM6'))
                print(dict(policy=policy,nack_delay_us=nack_delay,dedup=dedup,recovery_pass=case['recovery_pass'],controlled_missing=len(case['controlled_missing']),played=case['replays_played'],all_conceals=case['conceal_delta'],pending_suppressed=case.get('pending_repair_suppression_delta')),flush=True)
                save(out/'receipt.json',row)
            row['acquisition_complete']=True
    except Exception as error:row['errors'].append(str(error));row['traceback']=traceback.format_exc()
    finally:
        restore=[] if original is None else ([('COM7','REPAIRDEDUP '+str(original['dedup']),'WMP_REPAIR_DEDUP')] if args.dedup_comparison else [])+[('COM7','REPAIRCFG '+' '.join(map(str,original['repair'])),'WMP_REPAIR_CONFIG'),('COM6','NACKDELAY '+str(original['nack_delay_us']),'WMP_NACK_DELAY')]
        for p,line,marker in restore+[('COM7','DROPCLEAR','WMP_DROP_CLEAR')]+([('COM6','FAULTLOG 0','WMP_FAULT_RESOURCE')] if fault_owned else [])+[(p,'COPYLOG 0','WMP_TRACE_RESOURCE') for p in owned]:
            try:
                r=command(p,line,marker)
                if (p,line,marker) in restore and 'accepted=1' not in r['reply']:raise RuntimeError('original value rejected')
            except Exception as error:row['errors'].append('Cleanup '+p+' '+line+': '+str(error))
        save(out/'receipt.json',row)
    print(dict(acquisition_complete=row['acquisition_complete'],errors=row['errors']),flush=True)
    if row['errors']:raise SystemExit(1)

if __name__=='__main__':main()
