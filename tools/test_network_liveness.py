"""Admitted-AP keepalive/recovery diagnostic; never a gap-free qualification."""
import argparse,concurrent.futures,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair
from firmware_evidence import installed_pair,live_pair
from audio_fixture import FIXTURES,require as require_fixture,check_sources
from qualify_party_long import PHONES,peer,media
from test_two_phone_handover import playback_ready

def ready(ports):
    leaders=[p for p,v in ports.items() if v['lines']['WMP_RADIO']['mode']==2]
    if len(leaders)!=1:return False
    source=leaders[0];follower=next(p for p in ports if p!=source)
    return all(v['lines']['WMP_ASSOC']['connected'] and v['lines']['WMP_SOURCE_CLOCK']['valid'] for v in ports.values()) and playback_ready(ports,source,follower)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',type=Path,required=True);p.add_argument('--recover-port',choices=['COM6','COM7']);p.add_argument('--fixture',choices=FIXTURES,default='redmagic-samsung');a=p.parse_args()
    out=ROOT/'evidence'/('network-liveness-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes())
    r=dict(diagnostic_only=True,audio_qualified=False,pass_all=False,errors=[],samples=[],recover_port=a.recover_port,intentional_disconnect=bool(a.recover_port));print(out,flush=True)
    try:
        if a.recover_port:r['fixture']=require_fixture(a.fixture)
        r['binding']=installed_pair(a.app);r['info']=live_pair(r['binding'],command)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            before=pair(pool);r['before']=before
            for port,v in before.items():
                s=v['lines']
                if not s['WMP_ASSOC']['connected'] or not s['WMP_NETWORK_LIVENESS']['admitted']:raise RuntimeError('No admitted AP '+port)
            if a.recover_port:
                phones=[PHONES[peer(v['lines'])] for v in before.values()];check_sources(r['fixture'],phones);r['media']=[media(x) for x in phones]
                if not ready(before) or any(x.get('playback_state',x.get('youtube_state')) not in ('PLAYING','3') for x in r['media']):raise RuntimeError('Both actual streams/output must be active before intentional drop')
                original_source=next(p for p,v in before.items() if v['lines']['WMP_RADIO']['mode']==2)
                r['control']=command(a.recover_port,'NETDROP','WMP_NETWORK_DROP')
                if 'status=0' not in r['control']['reply']:raise RuntimeError('Known-AP diagnostic drop rejected')
            start=time.monotonic()
            while True:
                ports=pair(pool);elapsed=time.monotonic()-start;r['samples'].append(dict(elapsed=elapsed,ports=ports));save(out/'receipt.json',r)
                for port,v in ports.items():
                    s=v['lines'];b=before[port]['lines']
                    if any(s['WMP_RATE_SETUP'][k]!=b['WMP_RATE_SETUP'][k] for k in ['starts','stops','prestart_calls']):raise RuntimeError('Recovery restarted/reconfigured Wi-Fi '+port)
                    if s['WMP_NETWORK_LIVENESS']['keepalive_failed']>b['WMP_NETWORK_LIVENESS']['keepalive_failed'] or s['WMP_RADIO']['timeout']>b['WMP_RADIO']['timeout']:raise RuntimeError('Keepalive failed or lost completion '+port)
                    if a.recover_port and (not s['WMP_BT']['phone'] or not s['WMP_BT']['ble'] or any(s['WMP_BT_LINKS'][k]!=b['WMP_BT_LINKS'][k] for k in ['classic_disconnects','ble_disconnects'])):raise RuntimeError('Bluetooth disconnected during known-AP recovery '+port)
                    if a.recover_port and s['WMP_RADIO']['mode']!=b['WMP_RADIO']['mode']:raise RuntimeError('Music leadership changed during one-node network recovery '+port)
                    if a.recover_port and a.recover_port!=original_source and port==original_source:
                        if s['WMP_DMA']['start_us32']!=b['WMP_DMA']['start_us32'] or any(s['WMP_AUDIO'][k]!=b['WMP_AUDIO'][k] for k in ['concealed','late','underrun','write_errors']):raise RuntimeError('Follower recovery interrupted the source DAC '+port)
                if a.recover_port:
                    s=ports[a.recover_port]['lines'];b=before[a.recover_port]['lines']
                    rejoined=s['WMP_ASSOC_EVENTS']['disconnected']>b['WMP_ASSOC_EVENTS']['disconnected'] and s['WMP_ASSOC_EVENTS']['connected']>b['WMP_ASSOC_EVENTS']['connected']
                    if rejoined and ready(ports) and s['WMP_NETWORK_LIVENESS']['keepalive_done']>b['WMP_NETWORK_LIVENESS']['keepalive_done']:
                        other=next(x for x in ports if x!=a.recover_port)
                        if a.recover_port!=original_source and ports[other]['lines']['WMP_SESSION']['generation']!=before[other]['lines']['WMP_SESSION']['generation']:raise RuntimeError('Peer source session changed during follower recovery')
                        r['recovered_after_seconds']=elapsed;break
                elif all(v['lines']['WMP_NETWORK_LIVENESS']['keepalive_done']>=before[port]['lines']['WMP_NETWORK_LIVENESS']['keepalive_done']+2 for port,v in ports.items()):break
                if elapsed>40:raise RuntimeError('Periodic keepalive/recovery not proven within40seconds')
                time.sleep(1)
            r['pass_all']=True
    except Exception as e:r['errors'].append(repr(e))
    save(out/'receipt.json',r);print(dict(pass_all=r['pass_all'],errors=r['errors'],recovered_after_seconds=r.get('recovered_after_seconds'),audio_qualified=False),flush=True)
    if not r['pass_all']:raise SystemExit(1)

if __name__=='__main__':main()
