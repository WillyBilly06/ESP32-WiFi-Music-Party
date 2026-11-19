"""Bounded live music/BLE observation; freeze repair evidence on a new fault."""
import argparse,concurrent.futures,json,re,secrets,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command,parse
from sweep_live_pacing import pair
from test_burst_repair import read_records
from firmware_evidence import installed_pair,live_pair
from audio_fixture import FIXTURES,require as require_fixture,check_sources
from qualify_party_long import PHONES,peer,media

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seconds',type=int,default=180);p.add_argument('--app',type=Path,required=True);p.add_argument('--fixture',choices=FIXTURES,default='redmagic-linux');p.add_argument('--fault-first',action='store_true',help='Prioritize receiver fault polling over repeated repair dumps so64-group copy history survives');p.add_argument('--allow-idle-standby',action='store_true',help='Diagnostic only: standby Classic/BLE stays connected but its player may be idle');a=p.parse_args()
    if not 30<=a.seconds<=3600:p.error('30..3600 seconds')
    out=ROOT/'evidence'/('replay-watch-'+str(time.time_ns()));out.mkdir()
    (out/'watch.py').write_bytes(Path(__file__).read_bytes())
    row=dict(samples=[],errors=[],acquisition_complete=False,audio_qualified=False,requested_seconds=a.seconds,controls=[],fault_reads=[],repair_events=[],diagnostic_only=True,
             fault_first=a.fault_first,poll_max_seconds=0,
             note='History armed before observation; copy snapshots on first new fault. Standby input gaps are retained separately, never accepted as full qualification. No loss injection or timing change. Fault-first mode does not claim continuous repair-event coverage.')
    owned=[];fault_owned=False
    print('Replay watch:',out,flush=True)
    try:
        row['fixture']=require_fixture(a.fixture);row['firmware_binding']=installed_pair(a.app);row['live_firmware']=live_pair(row['firmware_binding'],command)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first=pair(pool);sources=[p for p,v in first.items() if v['lines']['WMP_RADIO']['mode']==2]
            if len(sources)!=1:raise RuntimeError('Ambiguous source')
            source=sources[0];receiver='COM6' if source=='COM7' else 'COM7';row.update(source=source,receiver=receiver)
            phones=[PHONES[peer(v['lines'])] for v in first.values()];check_sources(row['fixture'],phones)
            row['media_before']=[media(phone) for phone in phones]
            row['idle_standby_allowed']=a.allow_idle_standby
            source_phone=PHONES[peer(first[source]['lines'])]
            required_media=[v for v in row['media_before'] if not a.allow_idle_standby or v['serial']==source_phone]
            if any(v.get('playback_state',v.get('youtube_state')) not in ('PLAYING','3') for v in required_media):raise RuntimeError('Actual music not playing')
            for port in (source,receiver):
                needed=('phone','ble') if a.allow_idle_standby and port==receiver else ('phone','streaming','ble')
                if not all(first[port]['lines']['WMP_BT'][k] for k in needed):raise RuntimeError('Required incoming A2DP/BLE fixture absent '+port)
                if not first[port]['lines']['WMP_RADIO_RESOURCES']['history_bytes']:
                    r=command(port,'COPYLOG 1','WMP_TRACE_RESOURCE');row['controls'].append(dict(port=port,command='COPYLOG 1',result=r))
                    if 'accepted=1' not in r['reply']:raise RuntimeError('History allocation rejected '+port)
                    owned.append(port)
            existing=read_records(receiver,'FAULTTRACE','WMP_FAULT','WMP_FAULT_END');row['fault_before']=existing
            first=pair(pool);row['samples'].append(dict(elapsed=0,ports=first))
            sf=pool.submit(read_records,source,'REPAIRTRACE','WMP_REPAIR_TRACE','WMP_REPAIR_END')
            primed_repairs=sf.result()
            if not existing['tail'].get('loaded'):
                # Arm the empty fault ring LAST. Arming before slow STAT
                # dumps filled16 rows, making each poll too slow to retain
                # a500ms-old group inside the64-group source history.
                r=command(receiver,'FAULTLOG 1','WMP_FAULT_RESOURCE');row['controls'].append(dict(port=receiver,command='FAULTLOG 1',result=r))
                if 'accepted=1' not in r['reply']:raise RuntimeError('Fault history allocation rejected')
                fault_owned=True;primed_faults=dict(rows=[],tail=dict(loaded=1,count=0),freshly_armed=True)
            else:primed_faults=read_records(receiver,'FAULTTRACE','WMP_FAULT','WMP_FAULT_END')
            row['armed_before_window']=dict(repairs=primed_repairs,faults=primed_faults)
            start=time.monotonic();generation=first[source]['lines']['WMP_SESSION']['generation'];baseline={(v['generation'],v['sample'],v['pts'],v['at_us'],v['kind']) for v in primed_faults['rows']}
            seen={tuple(v.get(k) for k in ('generation','seq','at_us32','outcome')) for v in primed_repairs['rows']};iterations=0
            if not generation or any(v['lines']['WMP_SESSION']['generation']!=generation for v in first.values()):raise RuntimeError('No shared live session')
            while True:
                require_fixture(a.fixture)
                polled_at=time.monotonic()
                sf=None if a.fault_first else pool.submit(read_records,source,'REPAIRTRACE','WMP_REPAIR_TRACE','WMP_REPAIR_END')
                rf=pool.submit(read_records,receiver,'FAULTTRACE','WMP_FAULT','WMP_FAULT_END')
                faults=rf.result();repairs=sf.result() if sf else {'rows':[]};elapsed=time.monotonic()-start
                row['poll_max_seconds']=max(row['poll_max_seconds'],time.monotonic()-polled_at)
                for v in repairs['rows']:
                    key=tuple(v.get(k) for k in ('generation','seq','at_us32','outcome'))
                    if key in seen:continue
                    seen.add(key);v['observed_elapsed']=elapsed;v['remaining_us']=v['pts']-v['source_clock'] if v['pts'] else None;row['repair_events'].append(v)
                fresh=[v for v in faults['rows'] if (v['generation'],v['sample'],v['pts'],v['at_us'],v['kind']) not in baseline]
                iterations+=1;row['trace_polls']=iterations;row['observed_seconds']=elapsed
                if fresh:
                    row['faults']=fresh
                    row['terminal_reason']='new renderer fault'
                    # Snapshot already-armed histories before slower STAT reads.
                    # A fault may still be outside the64-group retained window;
                    # record explicit coverage rather than enabling new history.
                    copies={}
                    for port in (source,receiver):
                        nonce=secrets.token_hex(4);copies[port]=(nonce,pool.submit(command,port,'TRACE '+nonce,'WMP_TRACE_END query='+nonce))
                    row['copies']={}
                    for port,(nonce,future) in copies.items():
                        reply=future.result();(out/(port+'-copies.raw')).write_text(reply['reply'])
                        row['copies'][port]=parse(reply,nonce,require_crc=True)
                    if a.fault_first:
                        row['repair_after_copies']=read_records(source,'REPAIRTRACE','WMP_REPAIR_TRACE','WMP_REPAIR_END')
                    # Preserve the fault that TRIGGERED these snapshots.
                    # A later rolling ring can contain entirely different
                    # failures after the UART copy tables finish printing.
                    row['faults_after_copies']=read_records(receiver,'FAULTTRACE','WMP_FAULT','WMP_FAULT_END')
                    row['terminal_reason']='new renderer fault';break
                if elapsed>=a.seconds:row['acquisition_complete']=True;row['terminal_reason']='requested duration';break
                save(out/'receipt.json',row);time.sleep(.075 if a.fault_first else .1)
            last=pair(pool);row['samples'].append(dict(elapsed=time.monotonic()-start,ports=last))
            row['upstream_rtp_gaps']={port:last[port]['lines']['WMP_BT']['rtp_gaps']-first[port]['lines']['WMP_BT']['rtp_gaps'] for port in (source,receiver)}
            row['ble_progress']={port:last[port]['lines']['WMP_GATT']['applied']-first[port]['lines']['WMP_GATT']['applied'] for port in (source,receiver)}
            row['source_fault_delta']={section+'.'+key:last[source]['lines'][section][key]-first[source]['lines'][section][key] for section,keys in {'WMP_AUDIO':['concealed','late','underrun'],'WMP_BT':['blocked_frames','rtp_gaps'],'WMP_SOURCE_QUEUE':['full']}.items() for key in keys}
            if any(v['lines']['WMP_SESSION']['generation']!=generation or v['lines']['WMP_RADIO']['mode']!=first[port]['lines']['WMP_RADIO']['mode'] for port,v in last.items()):raise RuntimeError('Session/role changed during diagnostic')
            for port,v in last.items():
                needed=('phone','ble') if a.allow_idle_standby and port==receiver else ('phone','streaming','ble')
                if not all(v['lines']['WMP_BT'][k] for k in needed):raise RuntimeError('Required fixture disappeared '+port)
            row['media_after']=[media(phone) for phone in phones]
    except Exception as e:row['errors'].append(repr(e))
    finally:
        for port,line,marker in ([(receiver,'FAULTLOG 0','WMP_FAULT_RESOURCE')] if fault_owned else [])+[(port,'COPYLOG 0','WMP_TRACE_RESOURCE') for port in owned]:
            try:
                r=command(port,line,marker);row['controls'].append(dict(port=port,command=line,result=r))
                if 'accepted=1' not in r['reply']:raise RuntimeError('Diagnostic history cleanup rejected')
            except Exception as e:row['errors'].append('cleanup '+port+': '+repr(e))
    save(out/'receipt.json',row)
    print(json.dumps(dict(evidence=str(out),terminal=row.get('terminal_reason'),faults=row.get('faults'),repair_events=len(row.get('repair_events',[])),errors=row['errors'])),flush=True)
    if row['errors']:raise SystemExit(1)
if __name__=='__main__':main()
