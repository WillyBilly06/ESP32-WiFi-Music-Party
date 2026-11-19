"""Compare promiscuous and native WMP callbacks under unchanged actual music.

The observer changes RF/coexistence conditions and RXPAUSE may reprepare the
follower DAC. This is a receive-path diagnostic, never audio qualification.
"""
import argparse,concurrent.futures,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair
from firmware_evidence import installed_pair,live_pair
from audio_fixture import FIXTURES,require,check_sources
from qualify_party_long import PHONES,peer,media

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',type=Path,required=True);p.add_argument('--fixture',choices=FIXTURES,default='redmagic-samsung');p.add_argument('--seconds',type=int,default=30);a=p.parse_args()
    if not 15<=a.seconds<=90:p.error('15..90seconds per condition')
    out=ROOT/'evidence'/('rx-callbacks-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes())
    r=dict(diagnostic_only=True,audio_qualified=False,cases=[],controls=[],errors=[],acquisition_complete=False);receiver=None;original=None;changed=False
    print(out,flush=True)
    try:
        r['fixture']=require(a.fixture);r['binding']=installed_pair(a.app);r['live']=live_pair(r['binding'],command)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            initial=pair(pool);r['initial']=initial;leaders=[p for p,v in initial.items() if v['lines']['WMP_RADIO']['mode']==2]
            if len(leaders)!=1:raise RuntimeError('Need exactly one real source')
            source=leaders[0];receiver=next(p for p in initial if p!=source);r.update(source=source,receiver=receiver)
            phones=[PHONES[peer(v['lines'])] for v in initial.values()];check_sources(r['fixture'],phones)
            original=initial[receiver]['lines']['WMP_WIFI_STATE']['sniffer']
            for observed in [0,1,0]:
                c=dict(observer=observed,samples=[],media=[media(p) for p in phones]);r['cases'].append(c)
                if any(v.get('playback_state',v.get('youtube_state')) not in ('PLAYING','3') for v in c['media']):raise RuntimeError('Actual music absent')
                state=pair(pool)
                if state[receiver]['lines']['WMP_WIFI_STATE']['sniffer']!=observed:
                    changed=True
                    c['control']=command(receiver,'RXPAUSE '+str(1-observed),'WMP_RX_PAUSE_REQUEST')
                    if 'accepted=1' not in c['control']['reply']:raise RuntimeError('Observer control rejected')
                deadline=time.monotonic()+15
                while True:
                    first=pair(pool);s=first[receiver]['lines']
                    if s['WMP_WIFI_STATE']['sniffer']==observed and s['WMP_AUDIO']['running'] and not s['WMP_PREPARATION']['receiver_wait']:break
                    if time.monotonic()>deadline:raise RuntimeError('Observer/path did not become ready')
                c['transition_after']=first;start=time.monotonic()
                while True:
                    state=first if not c['samples'] else pair(pool);elapsed=time.monotonic()-start;c['samples'].append(dict(elapsed=elapsed,ports=state));save(out/'receipt.json',r)
                    for port,v in state.items():
                        s=v['lines'];b=initial[port]['lines']
                        if not all(s['WMP_BT'][k] for k in ['phone','streaming','ble']):raise RuntimeError('Full actual workload disappeared '+port)
                        if s['WMP_SESSION']['generation']!=b['WMP_SESSION']['generation'] or s['WMP_RADIO']['mode']!=b['WMP_RADIO']['mode']:raise RuntimeError('Role/session changed')
                        if s['WMP_ASSOC_EVENTS']!={**b['WMP_ASSOC_EVENTS'],'query':s['WMP_ASSOC_EVENTS']['query']}:raise RuntimeError('Association changed')
                    if state[source]['lines']['WMP_DMA']['start_us32']!=initial[source]['lines']['WMP_DMA']['start_us32']:raise RuntimeError('Observer interrupted source DAC')
                    if state[receiver]['lines']['WMP_DIRECT_RX']['enabled']!=1 or state[receiver]['lines']['WMP_WIFI_STATE']['sniffer']!=observed:raise RuntimeError('Receive paths changed')
                    if elapsed>=a.seconds:break
                    time.sleep(2)
                c['elapsed']=elapsed
                fields={'WMP_DIRECT_AIR':['frames','bad','same_sequence'],'WMP_DIRECT_RX':['callbacks','queued'],'WMP_RX':['callbacks','qfull','bad','unique'],'WMP_AUDIO':['concealed','late','underrun'],'WMP_BT':['frames','rtp_gaps','rtp_bad'],'WMP_GATT':['applied','notifications']}
                c['delta']={port:{sec+'.'+k:state[port]['lines'][sec][k]-first[port]['lines'][sec][k] for sec,keys in fields.items() for k in keys} for port in first}
                d=c['delta'][receiver];c['air_minus_native']=d['WMP_DIRECT_AIR.frames']-d['WMP_DIRECT_RX.queued'] if observed else None
                if any(c['delta'][port]['WMP_BT.frames']<=0 or c['delta'][port]['WMP_GATT.applied']<=0 for port in first):raise RuntimeError('A2DP/BLE did not progress')
                print(dict(observer=observed,air=d['WMP_DIRECT_AIR.frames'],native=d['WMP_DIRECT_RX.queued'],air_minus_native=c['air_minus_native'],air_bad=d['WMP_DIRECT_AIR.bad'],same_sequence=d['WMP_DIRECT_AIR.same_sequence'],concealed=d['WMP_AUDIO.concealed']),flush=True)
            r['acquisition_complete']=True
    except Exception as e:r['errors'].append(repr(e))
    finally:
        if receiver and original is not None and changed:
            try:
                from sweep_live_pacing import sample
                if sample(receiver)['lines']['WMP_WIFI_STATE']['sniffer']!=original:
                    r['restore']=command(receiver,'RXPAUSE '+str(1-original),'WMP_RX_PAUSE_REQUEST')
                    if 'accepted=1' not in r['restore']['reply']:raise RuntimeError('Observer restore rejected')
            except Exception as e:r['errors'].append('restore '+repr(e))
        save(out/'receipt.json',r)
    print(dict(complete=r['acquisition_complete'],errors=r['errors']),flush=True)
    if r['errors']:raise SystemExit(1)

if __name__=='__main__':main()
