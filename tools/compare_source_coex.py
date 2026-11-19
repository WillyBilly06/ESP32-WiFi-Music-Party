"""Measure sender RF preference with both real A2DP inputs and BLE unchanged."""
import bench_ids
import concurrent.futures,time,traceback
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair
from qualify_party_long import PHONES,peer,media
def main():
    out=ROOT/'evidence'/('source-coex-'+str(time.time_ns()));out.mkdir();r=dict(diagnostic_only=True,audio_qualified=False,cases=[],errors=[],acquisition_complete=False);source=None;original=None;print(out,flush=True)
    try:
        from adb_local import require_testing,SAMSUNG
        require_testing(SAMSUNG)  # local admission check before any board/phone action
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            initial=pair(pool);r['initial']=initial;source=next(p for p,v in initial.items() if v['lines']['WMP_RADIO']['mode']==2);receiver=next(p for p in initial if p!=source);original=initial[source]['lines']['WMP_COPY']['coex_hint'];phones=[PHONES[peer(v['lines'])] for v in initial.values()]
            if set(phones)!={bench_ids.PHONE_MAIN,bench_ids.PHONE_SECOND}:raise RuntimeError('Need two real admitted phones')
            for policy in [0,1,0,1]:
                c=dict(policy=policy,samples=[],media_before=[media(p) for p in phones]);r['cases'].append(c)
                if any(v['youtube_state'] not in ('PLAYING','3') for v in c['media_before']):raise RuntimeError('Real music stopped before window')
                c['control']=command(source,'COEX '+str(policy),'WMP_COEX')
                if 'status=0' not in c['control']['reply']:raise RuntimeError('RF preference rejected')
                time.sleep(.25);first=pair(pool);start=time.monotonic()
                while True:
                    ports=first if not c['samples'] else pair(pool);elapsed=time.monotonic()-start;c['samples'].append(dict(elapsed=elapsed,ports=ports));save(out/'receipt.json',r)
                    for p,v in ports.items():
                        s=v['lines'];base=initial[p]['lines']
                        if not all(s['WMP_BT'][k] for k in ['phone','ble','transport_started']):raise RuntimeError('Actual radio workload disappeared: '+p)
                        if s['WMP_SESSION']['generation']!=base['WMP_SESSION']['generation'] or s['WMP_RADIO']['mode']!=base['WMP_RADIO']['mode'] or s['WMP_DMA']['start_us32']!=base['WMP_DMA']['start_us32']:raise RuntimeError('Role/session/DAC changed')
                    if ports[source]['lines']['WMP_COPY']['coex_hint']!=policy:raise RuntimeError('Source preference not applied')
                    if elapsed>=30:break
                    time.sleep(2)
                c['elapsed']=elapsed
                fields={'WMP_BT':['frames','rtp_gaps','blocked_frames'],'WMP_AUDIO':['concealed','late','underrun'],'WMP_RADIO':['accepted','done','late','qfull'],'WMP_REPAIR_AIR':['accepted'],'WMP_GATT':['applied','notifications'],'WMP_SOURCE_QUEUE':['full']}
                c['delta']={p:{s+'.'+k:ports[p]['lines'][s][k]-first[p]['lines'][s][k] for s,keys in fields.items() for k in keys} for p in ports};seconds=(ports[source]['lines']['WMP_RADIO']['time_us']-first[source]['lines']['WMP_RADIO']['time_us'])/1e6;c['raw_pps']=c['delta'][source]['WMP_RADIO.accepted']/seconds
                if any(c['delta'][p]['WMP_BT.frames']<=0 or c['delta'][p]['WMP_GATT.applied']<=0 for p in ports):raise RuntimeError('Music/BLE did not progress')
                save(out/'receipt.json',r);print(dict(policy=policy,raw_pps=round(c['raw_pps'],1),source_gaps=c['delta'][source]['WMP_BT.rtp_gaps'],source_drops=c['delta'][source]['WMP_BT.blocked_frames'],source_conceals=c['delta'][source]['WMP_AUDIO.concealed'],receiver_conceals=c['delta'][receiver]['WMP_AUDIO.concealed']),flush=True)
            r['acquisition_complete']=True
    except Exception as e:r['errors'].append(str(e));r['traceback']=traceback.format_exc()
    finally:
        if source and original is not None:
            try:r['restore']=command(source,'COEX '+str(original),'WMP_COEX')
            except Exception as e:r['errors'].append('restore '+str(e))
        save(out/'receipt.json',r)
    print(dict(acquisition_complete=r['acquisition_complete'],errors=r['errors']),flush=True)
    if r['errors']:raise SystemExit(1)
if __name__=='__main__':main()
