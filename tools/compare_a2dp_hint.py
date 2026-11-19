"""Change only the audible source's manual A2DP coexistence hint."""
import bench_ids
import concurrent.futures,json,time,traceback
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair
from qualify_party_long import PHONES,peer,media

def main():
    out=ROOT/'evidence'/('a2dp-hint-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes());r=dict(diagnostic_only=True,audio_qualified=False,cases=[],errors=[],acquisition_complete=False);source=None;original=None;print(out,flush=True)
    try:
        from adb_local import require_testing,SAMSUNG
        require_testing(SAMSUNG)  # local admission check before any board/phone action
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            initial=pair(pool);r['initial']=initial;leaders=[p for p,v in initial.items() if v['lines']['WMP_RADIO']['mode']==2]
            if len(leaders)!=1:raise RuntimeError('Need one existing source')
            source=leaders[0];receiver=next(p for p in initial if p!=source);original=initial[source]['lines']['WMP_PROFILE_STAT']['enabled'];phones=[PHONES[peer(v['lines'])] for v in initial.values()]
            if set(phones)!={bench_ids.PHONE_MAIN,bench_ids.PHONE_SECOND}:raise RuntimeError('Both real phones required')
            r.update(source=source,receiver=receiver,images={p:json.loads((ROOT/'hardware'/('deployed-'+p+'.json')).read_text()) for p in initial})
            for profile in [2,0,2,0]:
                c=dict(profile=profile,samples=[],media_before=[media(p) for p in phones]);r['cases'].append(c)
                if any(v['youtube_state'] not in ('PLAYING','3') for v in c['media_before']):raise RuntimeError('Actual music not playing')
                c['control']=command(source,'A2DPCOEX '+str(profile),'WMP_A2DP_COEX')
                if 'status=0' not in c['control']['reply']:raise RuntimeError('Hint rejected')
                time.sleep(.3);first=pair(pool);start=time.monotonic()
                while True:
                    ports=first if not c['samples'] else pair(pool);elapsed=time.monotonic()-start;c['samples'].append(dict(elapsed=elapsed,ports=ports));save(out/'receipt.json',r)
                    for p,v in ports.items():
                        s=v['lines'];base=initial[p]['lines']
                        if not all(s['WMP_BT'][k] for k in ['phone','transport_started','ble']):raise RuntimeError('Actual dual-mode workload disappeared: '+p)
                        if s['WMP_SESSION']['generation']!=base['WMP_SESSION']['generation'] or s['WMP_RADIO']['mode']!=base['WMP_RADIO']['mode'] or s['WMP_DMA']['start_us32']!=base['WMP_DMA']['start_us32']:raise RuntimeError('Role/epoch/DAC changed')
                        for section,keys in {'WMP_RADIO':['copies','spacing'],'WMP_PHY':['selected_mbps'],'WMP_TX_DITHER':['us'],'WMP_TX_WINDOW':['active_limit'],'WMP_COPY':['coex_hint'],'WMP_REPLAY':['enabled','nack_delay_us'],'WMP_REPAIR_GATE':['limit'],'WMP_ASSOC_EVENTS':['connected','disconnected']}.items():
                            if any(s[section][k]!=base[section][k] for k in keys):raise RuntimeError('Unrelated policy/link change '+p+' '+section)
                    selected=ports[source]['lines']['WMP_PROFILE_STAT']
                    if selected['enabled']!=profile or selected['applied']!=(1 if profile==2 else 0) or selected['status']:raise RuntimeError('Requested hint not applied')
                    if elapsed>=30:break
                    time.sleep(2)
                fields={'WMP_BT':['rtp','rtp_gaps','frames','blocked_frames','discontinuity'],'WMP_AUDIO':['concealed','late','underrun','decoded'],'WMP_RADIO':['accepted','done','qfull','late'],'WMP_REPAIR_AIR':['accepted'],'WMP_SOURCE_QUEUE':['full'],'WMP_GATT':['applied','notifications'],'WMP_RX':['unique','qfull','bad']}
                c['delta']={p:{s+'.'+k:ports[p]['lines'][s][k]-first[p]['lines'][s][k] for s,keys in fields.items() for k in keys} for p in ports}
                seconds=(ports[source]['lines']['WMP_RADIO']['time_us']-first[source]['lines']['WMP_RADIO']['time_us'])/1e6;c['raw_pps']=c['delta'][source]['WMP_RADIO.accepted']/seconds;c['heap_min_current']=min(s['ports'][source]['lines']['WMP_MEMORY']['free'] for s in c['samples'])
                if any(c['delta'][p]['WMP_BT.rtp']<=0 or c['delta'][p]['WMP_GATT.applied']<=0 for p in ports):raise RuntimeError('Actual audio/BLE failed to progress')
                save(out/'receipt.json',r);print(dict(profile=profile,raw_pps=round(c['raw_pps'],1),input_gaps=c['delta'][source]['WMP_BT.rtp_gaps'],source_drops=c['delta'][source]['WMP_BT.blocked_frames'],source_conceals=c['delta'][source]['WMP_AUDIO.concealed'],receiver_conceals=c['delta'][receiver]['WMP_AUDIO.concealed']),flush=True)
            r['acquisition_complete']=True
    except Exception as e:r['errors'].append(str(e));r['traceback']=traceback.format_exc()
    finally:
        if source and original is not None:
            try:r['restore']=command(source,'A2DPCOEX '+str(original),'WMP_A2DP_COEX')
            except Exception as e:r['errors'].append('restore '+str(e))
        save(out/'receipt.json',r)
    print(dict(acquisition_complete=r['acquisition_complete'],errors=r['errors']),flush=True)
    if r['errors']:raise SystemExit(1)
if __name__=='__main__':main()
