"""Strict continuous dual-source party qualification; handovers run separately.

No synthetic pauses, fault resets, loss injections or automatic reconnections.
"""
import bench_ids
import argparse,concurrent.futures,json,re,secrets,subprocess,time,urllib.request
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command,capture
from sweep_live_pacing import pair
from analyze_audio import assess
from firmware_evidence import installed_pair,live_pair
from audio_fixture import FIXTURES,require as require_fixture,check_sources
from network_clock import failure as network_failure

PHONES={bench_ids.PHONE_MAIN_BT:bench_ids.PHONE_MAIN,bench_ids.PHONE_SECOND_BT:bench_ids.PHONE_SECOND,'e8:47:3a:ec:51:9a':'linux'}
def _linux_request(path,data=None):
    import http.client
    from local_ipc import connection
    c=json.loads((ROOT/'hardware/linux-session.json').read_text())
    client=http.client.HTTPConnection(c['bind'],c['port'],timeout=10)
    try:
        client.sock=connection((c['bind'],c['port']),10)
        headers={'X-WMP-Token':c['token']}
        if data is not None:headers['Content-Type']='application/json'
        client.request('POST' if data is not None else 'GET',path,body=data,headers=headers)
        response=client.getresponse();body=response.read()
        if response.status!=200:raise RuntimeError('Linux fixture bridge HTTP '+str(response.status))
        return json.loads(body)
    finally:client.close()

def linux_status():return _linux_request('/status')

def linux_control(action):
    c=json.loads((ROOT/'hardware/linux-session.json').read_text());payload=json.dumps(dict(id=secrets.token_hex(8),action=action)).encode()
    ack=_linux_request('/control',payload)
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        state=linux_status().get('playback') or {}
        if state.get('errors') or state.get('finished'):raise RuntimeError('Linux music source ended before applying control: '+str(state.get('errors')))
        applied=state.get('last_control') or {}
        audio=state.get('audio') or state.get('video') or {}
        if applied.get('id')==ack['id'] and (action=='pause' and audio.get('paused') or action=='play' and state.get('ready') and not audio.get('paused',True)):
            return dict(ack,applied=True,applied_at=applied['at'])
        time.sleep(.25)
    raise TimeoutError('Linux music control was queued but not applied: '+action)

def media(serial):
    if serial=='linux':
        p=linux_status().get('playback') or {};v=p.get('audio') or p.get('video') or {}
        playing=p.get('ready') and not p.get('errors') and not p.get('finished') and time.time()-p.get('updated',0)<15 and not v.get('paused',True) and v.get('ready',0)>=2
        return dict(serial='linux',playback_state='PLAYING' if playing else 'STOPPED',kind=p.get('kind','youtube'),media_time=v.get('time'),title=p.get('title'))
    if serial not in (bench_ids.PHONE_MAIN,bench_ids.PHONE_SECOND):raise RuntimeError('Phone is not admitted for current testing')
    from adb_local import shell
    output=shell(serial,['dumpsys','media_session'])
    m=re.search(r'package=com.google.android.youtube.*?state=PlaybackState \{state=([A-Z]+|\d+)',output,re.S)
    return dict(serial=serial,youtube_state=m.group(1) if m else None)
def peer(lines):
    p=lines['WMP_PEERS'];raw=f"{p['classic_hi']:08x}{p['classic_lo']:04x}"
    return ':'.join(raw[i:i+2] for i in range(0,12,2))
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--seconds',type=int,default=3900);parser.add_argument('--app',type=Path,required=True);parser.add_argument('--fixture',choices=FIXTURES,default='redmagic-samsung');parser.add_argument('--passive-receiver',action='store_true');a=parser.parse_args()
    if not 60<=a.seconds<=10800:parser.error('60..10800 real seconds')
    out=ROOT/'evidence'/('party-long-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes())
    row=dict(requested_seconds=a.seconds,samples=[],media=[],errors=[],failures=[],acquisition_complete=False,audio_qualified=False,
             note='Unchanged actual YouTube on REDMAGIC and Samsung Classic A2DP streams + Wi-Fi broadcast + BLE transactions; Samsung media volume20%; handover preparation is tested outside this continuous window.')
    print('Party long:',out,flush=True)
    try:
        row['fixture']=require_fixture(a.fixture);row['note']=row['fixture']['description']+'; handover preparation is outside this continuous window'
        row['firmware_binding']=installed_pair(a.app)
        row['live_firmware']=live_pair(row['firmware_binding'],command)
        if json.loads((ROOT/'hardware/active-session.json').read_text()).get('state')!='running':raise RuntimeError('Need running persistent UART owner; no per-query serial reopening')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first=pair(pool);sources=[p for p,v in first.items() if v['lines']['WMP_RADIO']['mode']==2]
            row['installed_images']={p:json.loads((ROOT/'hardware'/('deployed-'+p+'.json')).read_text()) for p in first}
            if len(sources)!=1:raise RuntimeError('Need exactly one source')
            source=sources[0];receiver='COM6' if source=='COM7' else 'COM7';row.update(source=source,receiver=receiver)
            if first[source]['lines']['WMP_RADIO']['copies']!=4:raise RuntimeError('Need four actual original copies')
            if not first[source]['lines']['WMP_SESSION']['generation'] or first[source]['lines']['WMP_SESSION']['generation']!=first[receiver]['lines']['WMP_SESSION']['generation']:raise RuntimeError('Need one real shared session independent of optional logging')
            phones=[PHONES[peer(v['lines'])] for v in first.values()]
            check_sources(row['fixture'],phones)
            start=time.monotonic();last_media=-60;last_report=-60
            while True:
                require_fixture(a.fixture)
                ports=first if not row['samples'] else pair(pool);elapsed=time.monotonic()-start
                row['samples'].append(dict(elapsed=elapsed,ports=ports));failure=[]
                for port,v in ports.items():
                    now=v['lines'];old=first[port]['lines'];bt=now['WMP_BT']
                    if now.get('WMP_ROLE_POLICY',{}).get('follower_lock'):failure.append(port+' diagnostic follower lock cannot qualify delivery')
                    if not all(bt[k] for k in ['phone','streaming','ble']):failure.append(port+' full Classic/A2DP/BLE workload disappeared')
                    if not now['WMP_AUDIO']['running']:failure.append(port+' DAC stopped')
                    if now['WMP_MEMORY']['free']<30000:failure.append(port+' free heap below30000bytes')
                    if old.get('WMP_NETWORK',{}).get('infrastructure'):
                        clock=now['WMP_SOURCE_CLOCK']
                        network_issue=network_failure(now)
                        if network_issue:failure.append(port+' '+network_issue)
                        if now['WMP_WIFI_MODE']['ap']!=old['WMP_WIFI_MODE']['ap'] or now.get('WMP_ANCHOR_STAT',{}).get('requested',0)!=old.get('WMP_ANCHOR_STAT',{}).get('requested',0):failure.append(port+' fixed network role changed')
                        if clock['domain_epoch']!=old['WMP_SOURCE_CLOCK']['domain_epoch']:failure.append(port+' network clock domain restarted')
                        if now['WMP_ASSOC_EVENTS']['connected']!=old['WMP_ASSOC_EVENTS']['connected'] or now['WMP_ASSOC_EVENTS']['disconnected']!=old['WMP_ASSOC_EVENTS']['disconnected']:failure.append(port+' infrastructure association changed')
                    if now['WMP_WIFI_STATE']['sniffer']!=old['WMP_WIFI_STATE']['sniffer']:failure.append(port+' sniffer state changed')
                    if (now['WMP_RADIO']['mode']==2 and now['WMP_PREPARATION']['source_wait']) or now['WMP_PREPARATION']['receiver_wait']:failure.append(port+' unexpected preparation/mute')
                    if now['WMP_PROFILE_STAT']['status']:failure.append(port+' controller rejected coexistence status')
                    expected_preference=0 if port==source else 1
                    expected_stream=1 if port==source else 0
                    if now['WMP_COPY']['coex_hint']!=expected_preference or now['WMP_PROFILE_STAT']['enabled']!=1 or now['WMP_PROFILE_STAT']['applied']!=expected_stream:failure.append(port+' role coexistence policy differs from the intended source/follower profile')
                    if now['WMP_PHY_CONFIG']['bandwidth_status'] or now['WMP_PHY_CONFIG']['bandwidth_code']!=1 or now['WMP_PHY']['selected_mbps']!=26:failure.append(port+' required MCS3/HT20 profile unavailable')
                    for key in ['original_late_submit','original_late_done']:
                        if now['WMP_TX_LIFETIME'][key]!=old['WMP_TX_LIFETIME'][key]:failure.append(port+' WMP_TX_LIFETIME.'+key)
                    for section,key in [('WMP_STORAGE','tx_stack_free'),('WMP_STORAGE','rx_stack_free'),('WMP_POOL','audio_stack_free'),('WMP_BT_MEMORY','host_stack_free')]:
                        if now[section][key]<512:failure.append(port+' stack reserve below512bytes: '+key)
                    for section,keys in {'WMP_COPY':['paced','spacing','fec'],'WMP_PHY':['selected_mbps'],'WMP_RAW_PATH':['action_frames']}.items():
                        if any(now[section][k]!=old[section][k] for k in keys):failure.append(port+' radio configuration changed')
                    if now['WMP_RADIO']['copies']!=old['WMP_RADIO']['copies'] or now['WMP_TX_SCHEDULE']['period_us']!=old['WMP_TX_SCHEDULE']['period_us']:failure.append(port+' copy count or send period changed')
                    if now.get('WMP_TX_WINDOW',{}).get('active_limit')!=old.get('WMP_TX_WINDOW',{}).get('active_limit'):failure.append(port+' transmit window changed')
                    if now['WMP_TX_DITHER']['us']!=old['WMP_TX_DITHER']['us']:failure.append(port+' copy dither changed')
                    if now['WMP_SESSION']['generation']!=old['WMP_SESSION']['generation']:failure.append(port+' unplanned session change')
                    if now.get('WMP_ETH',{}).get('enabled',0)!=old.get('WMP_ETH',{}).get('enabled',0):failure.append(port+' Ethernet receive path changed')
                    if now.get('WMP_NATIVE_RX',{}).get('requested')!=old.get('WMP_NATIVE_RX',{}).get('requested') or now.get('WMP_LISTEN',{}).get('requested')!=old.get('WMP_LISTEN',{}).get('requested'):failure.append(port+' receive reservation changed')
                    if now.get('WMP_DIRECT_RX',{}).get('enabled',0)!=old.get('WMP_DIRECT_RX',{}).get('enabled',0):failure.append(port+' direct Action receive path changed')
                    if now['WMP_DMA']['start_us32']!=old['WMP_DMA']['start_us32']:failure.append(port+' DAC restarted')
                    for section,keys in {'WMP_AUDIO':['concealed','late','underrun','write_errors','codec_bad','pcm_bad','qfull'],
                        'WMP_BT':['blocked_frames','rtp_gaps','rtp_bad'],'WMP_RX':['qfull','bad','conflict'],
                        'WMP_DMA':['queue_overflow'],'WMP_BT_LINKS':['classic_disconnects','ble_disconnects','connection_failures'],
                        'WMP_SOURCE_QUEUE':['full'],'WMP_TIMELINE':['recoveries'],'WMP_VHCI':['dropped'],
                        'WMP_CLOCK_HEALTH':['expirations','unavailable_skips'],
                        'WMP_PREPARATION':['source_skipped_frames','receiver_skipped_groups']}.items():
                        for key in keys:
                            if now[section][key]!=old[section][key]:failure.append(port+' '+section+'.'+key)
                if not ports[receiver]['lines']['WMP_AUDIO']['clock_valid']:failure.append('receiver clock invalid')
                receiver_lines=ports[receiver]['lines']
                if a.passive_receiver:
                    passive_issue=network_failure(receiver_lines)
                    if passive_issue:failure.append('passive receiver '+passive_issue)
                    if receiver_lines['WMP_NETWORK']['infrastructure'] or receiver_lines['WMP_ASSOC']['requested'] or receiver_lines['WMP_ASSOC']['connected']:failure.append('receiver unexpectedly associated')
                    if ports[source]['lines'].get('WMP_ANCHOR_STAT',{}).get('peers',0):failure.append('transmitter has an associated client')
                if any(v['lines']['WMP_REPAIR_LINK']['unicast'] for v in ports.values()):failure.append('addressed audio repairs cannot qualify broadcast transport')
                if receiver_lines.get('WMP_ETH',{}).get('enabled') or receiver_lines.get('WMP_DIRECT_RX',{}).get('enabled'):
                    clock=receiver_lines['WMP_SOURCE_CLOCK']
                    network_issue=network_failure(receiver_lines)
                    if network_issue:failure.append('receiver '+network_issue)
                elif receiver_lines['WMP_BEACONS']['match_age_us']>5000000:failure.append('receiver hardware clock feed stale')
                phase=[ports[p]['lines']['WMP_AUDIO']['phase_us'] for p in [source,receiver]]
                if abs(phase[0]-phase[1])>2000:failure.append('estimated renderer phase differs by over2ms; not an acoustic measurement')
                if elapsed-last_media>=60:
                    states=[media(p) for p in phones];row['media'].append(dict(elapsed=elapsed,phones=states));last_media=elapsed
                    if any(v.get('playback_state',v.get('youtube_state')) not in ['PLAYING','3'] for v in states):failure.append('Required actual music playback stopped')
                if len(row['samples'])>1:
                    previous=row['samples'][-2]['ports']
                    if any(ports[p]['lines']['WMP_BT']['frames']<=previous[p]['lines']['WMP_BT']['frames'] for p in ports):failure.append('incoming phone SBC stopped')
                    if ports[receiver]['lines']['WMP_PEERS']['standby_frames']<=previous[receiver]['lines']['WMP_PEERS']['standby_frames']:failure.append('standby discard workload stopped')
                if elapsed>=15:
                    for p in ports:
                        writes=ports[p]['lines']['WMP_BT']['ble_writes']-first[p]['lines']['WMP_BT']['ble_writes']
                        if writes<elapsed/2:failure.append(p+' insufficient actual BLE writes')
                row['failures']=sorted(set(failure));save(out/'receipt.json',row)
                if failure:
                    row['terminal_reason']='fault_detected';nonce=secrets.token_hex(4)
                    (out/'repair.raw').write_text(command(source,'REPAIRTRACE '+nonce,'WMP_REPAIR_END query='+nonce)['reply'])
                    copies=out/'copies';copies.mkdir()
                    try:capture(source,receiver,copies);row['copy_evidence']=str(copies/'receipt.json')
                    except Exception as diagnostic:row.setdefault('diagnostic_errors',[]).append(repr(diagnostic))
                    break
                if elapsed>=a.seconds:row['acquisition_complete']=True;row['terminal_reason']='requested_duration';break
                if elapsed-last_report>=60:print(json.dumps(dict(elapsed=round(elapsed,1),failures=[],heap={p:ports[p]['lines']['WMP_MEMORY']['free'] for p in ports})),flush=True);last_report=elapsed
                time.sleep(min(5,a.seconds-elapsed))
        row['assessment']=assess(row)
        row['pass_all']=bool(row['acquisition_complete'] and not row['failures'] and row['assessment']['digital_pass'])
        row['one_hour_digital_pass']=bool(row['pass_all'] and row['samples'][-1]['elapsed']>=3600)
    except Exception as e:row['errors'].append(repr(e));row['pass_all']=False;__import__('traceback').print_exc(file=(out/'exception.txt').open('w'))
    save(out/'receipt.json',row);print(json.dumps(dict(evidence=str(out),pass_all=row.get('pass_all'),one_hour_digital_pass=row.get('one_hour_digital_pass'),failures=row['failures'],errors=row['errors'])),flush=True)
if __name__=='__main__':main()
