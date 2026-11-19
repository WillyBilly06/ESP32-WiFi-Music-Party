"""Real automatic leadership handovers; BLE stays live, planned preparation is recorded.

Uses admitted REDMAGIC/Samsung sources and the persistent admitted board session. No radio
role commands, resets, unpairing, or synthetic audio are used.
"""
import bench_ids
import argparse,concurrent.futures,json,subprocess,time
from pathlib import Path
from board_io import ROOT,save
from sweep_live_pacing import pair
from qualify_party_long import PHONES,peer,media,linux_control
from capture_copies import command
from firmware_evidence import installed_pair,live_pair
from audio_fixture import FIXTURES,require as require_fixture,check_sources

def phone(serial,action):
    if serial=='linux':return linux_control(action)
    if serial not in (bench_ids.PHONE_MAIN,bench_ids.PHONE_SECOND):raise RuntimeError('Phone is not admitted for current testing')
    from adb_local import shell
    if serial==bench_ids.PHONE_SECOND and action=='play':
        # Samsung is released at 20% media volume: set 3/15, then read it back before playing.
        shell(serial,['cmd','media_session','volume','--stream','3','--set','3'])
        if 'volume is 3 in range [0..15]' not in shell(serial,['cmd','media_session','volume','--stream','3','--get']):raise RuntimeError('Samsung is not at 20 percent')
    return shell(serial,['cmd','media_session','dispatch',action])

def playback_ready(ports,leader,follower):
    a,b=ports[leader]['lines'],ports[follower]['lines'];x,y=a['WMP_SESSION'],b['WMP_SESSION'];rate=a['WMP_BT']['rate']
    shared=bool(x['generation'] and x['generation']==y['generation'] and x['source']==1 and y['source']==0)
    return bool(shared and rate and a['WMP_RADIO']['mode']==2 and b['WMP_RADIO']['mode']==0 and b['WMP_AUDIO']['clock_valid'] and
                all(v['lines']['WMP_AUDIO']['running'] and v['lines']['WMP_AUDIO']['rate']==rate and not v['lines']['WMP_PREPARATION']['source_wait'] and not v['lines']['WMP_PREPARATION']['receiver_wait'] for v in ports.values()))

def prepare_input(pool,port,serial):
    before=pair(pool)[port]['lines']['WMP_BT'];state=media(serial)
    playing=state.get('playback_state',state.get('youtube_state')) in ['PLAYING','3']
    if not playing or not before['transport_started'] or not before['streaming']:phone(serial,'play')
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        now=pair(pool)[port]['lines']['WMP_BT']
        if now['streaming'] and now['transport_started'] and now['rtp']>before['rtp']:return
        time.sleep(.3)
    raise RuntimeError('Test-source preparation failed; no active RTP from '+port)
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--app',type=Path,required=True);parser.add_argument('--fixture',choices=FIXTURES,default='redmagic-samsung');parser.add_argument('--switches',type=int,choices=[1,2],default=2);args=parser.parse_args()
    out=ROOT/'evidence'/('two-phone-handover-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes())
    row=dict(scope=__doc__,phases=[],errors=[],audio_qualified=False);paused=None
    print('Handover:',out,flush=True)
    try:
        row['fixture']=require_fixture(args.fixture)
        row['firmware_binding']=installed_pair(args.app)
        row['live_firmware']=live_pair(row['firmware_binding'],command)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            initial=pair(pool);phones={p:PHONES[peer(v['lines'])] for p,v in initial.items()}
            check_sources(row['fixture'],list(phones.values()))
            for p,serial in phones.items():
                prepare_input(pool,p,serial)
                state=media(serial)
                if state.get('playback_state',state.get('youtube_state')) not in ['PLAYING','3']:raise RuntimeError('Both real music streams must be playing')
            for _ in range(args.switches):
                before=pair(pool);sources=[p for p,v in before.items() if v['lines']['WMP_RADIO']['mode']==2]
                if len(sources)!=1:raise RuntimeError('Expected exactly one current source')
                source=sources[0];target=next(p for p in before if p!=source)
                # Preparation is outside the measured handover: a phone that
                # stopped its player cannot be expected to become a source.
                prepare_input(pool,target,phones[target]);before=pair(pool);paused=phones[source]
                phase=dict(old_source=source,expected_source=target,before=before,samples=[]);row['phases'].append(phase);start=time.monotonic();phone(paused,'pause');stable_at=None
                while True:
                    require_fixture(args.fixture)
                    ports=pair(pool);elapsed=time.monotonic()-start;phase['samples'].append(dict(elapsed=elapsed,ports=ports));save(out/'receipt.json',row)
                    for port in before:
                        for section,key in [('WMP_COPY','spacing'),('WMP_COPY','paced'),('WMP_TX_DITHER','us')]:
                            if ports[port]['lines'][section][key]!=before[port]['lines'][section][key]:
                                raise RuntimeError('Automatic handover overwrote the selected copy schedule: '+port+' '+section+'.'+key)
                    if any(not v['lines']['WMP_BT']['phone'] or not v['lines']['WMP_BT']['ble'] for v in ports.values()):raise RuntimeError('Classic/BLE connection lost during handover')
                    if not ports[target]['lines']['WMP_BT']['transport_started']:raise RuntimeError('Required target A2DP transport stopped; handover workload disappeared')
                    if not ports[target]['lines']['WMP_BT']['streaming']:phase['target_rtp_stall_samples']=phase.get('target_rtp_stall_samples',0)+1
                    ready=playback_ready(ports,target,source)
                    if ready and stable_at is None:stable_at=elapsed;phase['ready_after_seconds']=elapsed
                    if not ready and stable_at is not None:raise RuntimeError('Established handover playback lost readiness')
                    if elapsed>20 and stable_at is None:raise RuntimeError('Automatic handover/playback failed to stabilize within20seconds')
                    if stable_at is not None and elapsed-stable_at>=20:break
                    time.sleep(1)
                phone(paused,'play');paused=None;resume=time.monotonic()
                while time.monotonic()-resume<15:
                    require_fixture(args.fixture)
                    ports=pair(pool);phase['samples'].append(dict(elapsed=time.monotonic()-start,ports=ports));save(out/'receipt.json',row)
                    if not playback_ready(ports,target,source):raise RuntimeError('Resuming standby phone displaced the active leader or interrupted playback readiness')
                    time.sleep(1)
                last=phase['samples'][-1]['ports'];phase['role_pass']=True
                phase['transition']={p:last[p]['lines'].get('WMP_TRANSITION') for p in before}
                phase['delta']={p:{s:{k:last[p]['lines'][s][k]-before[p]['lines'][s][k] for k in keys} for s,keys in {
                    'WMP_AUDIO':['concealed','late','underrun'],'WMP_RX':['qfull'],
                    'WMP_BT':['ble_writes','blocked_frames','rtp_gaps'],'WMP_PEERS':['standby_frames'],
                    'WMP_DMA':['queue_overflow']}.items()} for p in before}
                # DMA counters restart when the role changes descriptor layout.
                # Count new overflows within each actual DMA session instead of
                # subtracting counters belonging to different I2S instances.
                snapshots=[before]+[s['ports'] for s in phase['samples']]
                for p in before:
                    overflow=0
                    for a,b in zip(snapshots,snapshots[1:]):
                        old,new=a[p]['lines']['WMP_DMA'],b[p]['lines']['WMP_DMA']
                        overflow+=new['queue_overflow'] if new['start_us32']!=old['start_us32'] else max(0,new['queue_overflow']-old['queue_overflow'])
                    phase['delta'][p]['WMP_DMA']['queue_overflow']=overflow
                phase['audio_counter_pass']=all(not phase['delta'][p][s][k] for p in before for s,keys in {
                    'WMP_AUDIO':['concealed','late','underrun'],'WMP_RX':['qfull'],
                    'WMP_BT':['blocked_frames'],'WMP_DMA':['queue_overflow']}.items() for k in keys)
                # The new leader's input becomes the audible stream. Missing
                # input must fail even if its gap was filled with silence.
                phase['audio_counter_pass']=phase['audio_counter_pass'] and not phase['delta'][target]['WMP_BT']['rtp_gaps']
                print(json.dumps(dict(old_source=source,new_source=target,ready_after=phase['ready_after_seconds'],audio_counter_pass=phase['audio_counter_pass'],delta=phase['delta'])),flush=True)
            row['acquisition_complete']=True;row['role_pass']=all(p['role_pass'] for p in row['phases']);row['audio_counter_pass']=all(p['audio_counter_pass'] for p in row['phases'])
    except Exception as error:row['errors'].append(repr(error))
    finally:
        if paused:
            try:phone(paused,'play')
            except Exception as error:row['errors'].append('resume '+repr(error))
    save(out/'receipt.json',row);print(json.dumps(dict(evidence=str(out),role_pass=row.get('role_pass'),audio_counter_pass=row.get('audio_counter_pass'),errors=row['errors'])),flush=True)
if __name__=='__main__':main()
