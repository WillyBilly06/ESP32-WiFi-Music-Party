"""Compare only the RX-age NACK gate with uninterrupted two-phone audio/BLE."""
import argparse,concurrent.futures,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair
from firmware_evidence import installed_pair,live_pair
from audio_fixture import require,check_sources
from qualify_party_long import PHONES,peer,media

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',type=Path,required=True);p.add_argument('--seconds',type=int,default=40);a=p.parse_args()
    if not 30<=a.seconds<=120:p.error('30..120seconds per arm')
    out=ROOT/'evidence'/('nack-gate-'+str(time.time_ns()));out.mkdir();(out/'compare.py').write_bytes(Path(__file__).read_bytes())
    r=dict(cases=[],controls=[],errors=[],diagnostic_only=True,audio_qualified=False,acquisition_complete=False);original={};print(out,flush=True)
    def control(port,value):
        c=command(port,'NACKGATE '+str(value),'WMP_NACK_GATE accepted=');r['controls'].append(dict(port=port,value=value,result=c));save(out/'receipt.json',r)
        if 'accepted=1' not in c['reply']:raise RuntimeError('NACK gate rejected '+port)
    try:
        r['fixture']=require('redmagic-samsung');r['binding']=installed_pair(a.app);r['live']=live_pair(r['binding'],command)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            initial=pair(pool);r['initial']=initial;leaders=[p for p,v in initial.items() if v['lines']['WMP_RADIO']['mode']==2]
            if len(leaders)!=1:raise RuntimeError('Need exactly one source')
            source=leaders[0];receiver=next(p for p in initial if p!=source);r.update(source=source,receiver=receiver)
            phones=[PHONES[peer(v['lines'])] for v in initial.values()];check_sources(r['fixture'],phones)
            original={p:v['lines']['WMP_NACK_GATE_STAT']['us'] for p,v in initial.items()}
            for gate in [8000,0,8000,0]:
                require('redmagic-samsung');states=[media(p) for p in phones]
                if any(v.get('youtube_state') not in ['PLAYING','3'] for v in states):raise RuntimeError('Both real music streams must remain playing')
                for port in initial:control(port,gate)
                first=pair(pool);case=dict(gate_us=gate,media_before=states,samples=[],delta={});r['cases'].append(case);start=time.monotonic()
                while True:
                    current=first if not case['samples'] else pair(pool);elapsed=time.monotonic()-start;case['samples'].append(dict(elapsed=elapsed,ports=current));save(out/'receipt.json',r)
                    for port,v in current.items():
                        s=v['lines'];base=initial[port]['lines']
                        if not all(s['WMP_BT'][k] for k in ['phone','streaming','ble']):raise RuntimeError('Full workload disappeared '+port)
                        if s['WMP_NACK_GATE_STAT']['us']!=gate:raise RuntimeError('Gate readback differs '+port)
                        invariants={'WMP_SESSION':['generation','source'],'WMP_DMA':['start_us32'],'WMP_PHY':['selected_mbps'],'WMP_PHY_CONFIG':['bandwidth_code'],'WMP_COPY':['spacing','paced','fec','coex_hint'],'WMP_TX_DITHER':['us'],'WMP_PROFILE_STAT':['enabled','applied'],'WMP_TX_WINDOW':['active_limit'],'WMP_ASSOC_EVENTS':['connected','disconnected'],'WMP_RATE_SETUP':['starts','stops','prestart_calls']}
                        if any(s[sec][k]!=base[sec][k] for sec,keys in invariants.items() for k in keys):raise RuntimeError('Unrelated state changed '+port)
                        if s['WMP_PHY']['selected_mbps']!=26 or s['WMP_PHY_CONFIG']['bandwidth_code']!=1:raise RuntimeError('MCS3/HT20 required')
                    if elapsed>=a.seconds:break
                    time.sleep(3)
                fields={'WMP_AUDIO':['concealed','late','underrun','decoded'],'WMP_BT':['rtp','frames','ble_writes','blocked_frames','rtp_gaps','rtp_bad'],'WMP_GATT':['applied','notifications'],'WMP_REPLAY':['nack_sent','nack_heard','queued','delivered','played'],'WMP_REPAIR_AIR':['window_wait'],'WMP_TX_LIFETIME':['original_late_submit','original_late_done','repair_late_submit','repair_late_done'],'WMP_SOURCE_QUEUE':['full'],'WMP_RX':['qfull','bad']}
                case.update(elapsed=elapsed,delta={p:{sec+'.'+k:current[p]['lines'][sec][k]-first[p]['lines'][sec][k] for sec,keys in fields.items() for k in keys} for p in current},media_after=[media(p) for p in phones])
                if any(case['delta'][p]['WMP_BT.frames']<=0 or case['delta'][p]['WMP_GATT.applied']<=0 for p in current):raise RuntimeError('Actual A2DP/BLE did not progress')
                save(out/'receipt.json',r);print(dict(gate_us=gate,seconds=elapsed,source_late=case['delta'][source]['WMP_TX_LIFETIME.original_late_submit'],receiver_conceals=case['delta'][receiver]['WMP_AUDIO.concealed'],receiver_late=case['delta'][receiver]['WMP_AUDIO.late'],window_wait=case['delta'][receiver]['WMP_REPAIR_AIR.window_wait']),flush=True)
            r['acquisition_complete']=True
    except Exception as e:r['errors'].append(repr(e))
    finally:
        for port,gate in original.items():
            try:control(port,gate)
            except Exception as e:r['errors'].append('restore '+port+' '+repr(e))
        save(out/'receipt.json',r)
    print(dict(complete=r['acquisition_complete'],errors=r['errors']),flush=True)
    if r['errors']:raise SystemExit(1)

if __name__=='__main__':main()
