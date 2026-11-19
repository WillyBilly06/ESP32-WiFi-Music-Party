"""Actual two-phone music/BLE, changing only the concurrent repair-loan budget."""
import bench_ids
import concurrent.futures,json,time,traceback
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair
from qualify_party_long import PHONES,peer,media

def main():
    out=ROOT/'evidence'/('repair-admission-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes())
    r=dict(diagnostic_only=True,audio_qualified=False,cases=[],errors=[],acquisition_complete=False);source=None;original=None;print(out,flush=True)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            initial=pair(pool);r['initial']=initial;source=next(p for p,v in initial.items() if v['lines']['WMP_RADIO']['mode']==2);receiver=next(p for p in initial if p!=source);original=initial[source]['lines']['WMP_REPAIR_GATE']['limit']
            phones={p:PHONES[peer(v['lines'])] for p,v in initial.items()}
            if set(phones.values())!={bench_ids.PHONE_MAIN,bench_ids.PHONE_SECOND}:raise RuntimeError('Two real admitted phones required')
            if initial[source]['lines']['WMP_RADIO']['copies']!=4:raise RuntimeError('Need four actual originals')
            r.update(source=source,receiver=receiver,installed_images={p:json.loads((ROOT/'hardware'/('deployed-'+p+'.json')).read_text()) for p in initial})
            for limit in [4,16,4,16]:
                c=dict(limit=limit,samples=[],media_before=[media(p) for p in phones.values()]);r['cases'].append(c)
                if any(v.get('youtube_state') not in ('PLAYING','3') for v in c['media_before']):raise RuntimeError('Real YouTube playback not active')
                c['control']=command(source,'REPAIRLIMIT '+str(limit),'WMP_REPAIR_LIMIT')
                if 'accepted=1' not in c['control']['reply']:raise RuntimeError('Repair limit rejected')
                time.sleep(.3);first=pair(pool);start=time.monotonic()
                while True:
                    ports=first if not c['samples'] else pair(pool);elapsed=time.monotonic()-start;c['samples'].append(dict(elapsed=elapsed,ports=ports));save(out/'receipt.json',r)
                    for p,v in ports.items():
                        s=v['lines'];base=initial[p]['lines']
                        if not all(s['WMP_BT'][k] for k in ('phone','transport_started','ble')):raise RuntimeError('Required radio workload disappeared: '+p)
                        if s['WMP_SESSION']['generation']!=base['WMP_SESSION']['generation'] or s['WMP_RADIO']['mode']!=base['WMP_RADIO']['mode'] or s['WMP_DMA']['start_us32']!=base['WMP_DMA']['start_us32']:raise RuntimeError('Role/epoch/DAC changed')
                        for section,keys in {'WMP_RADIO':['copies','spacing'],'WMP_PHY':['selected_mbps'],'WMP_TX_DITHER':['us'],'WMP_TX_WINDOW':['active_limit'],'WMP_PROFILE_STAT':['enabled','applied'],'WMP_ASSOC_EVENTS':['connected','disconnected']}.items():
                            if any(s[section][k]!=base[section][k] for k in keys):raise RuntimeError('Unrelated policy/connection changed')
                    gate=ports[source]['lines']['WMP_REPAIR_GATE']
                    if gate['limit']!=limit or not gate['enabled']:raise RuntimeError('Repair loan policy not applied')
                    if elapsed>=30:break
                    time.sleep(2)
                c['elapsed']=elapsed;c['media_after']=[media(p) for p in phones.values()]
                fields={'WMP_AUDIO':['decoded','concealed','late','underrun'],'WMP_BT':['rtp','rtp_gaps','frames','groups','blocked_frames','discontinuity'],'WMP_GATT':['applied','notifications'],'WMP_SOURCE_QUEUE':['full','deferred'],'WMP_RADIO':['qfull','accepted','done','late'],'WMP_REPAIR_GATE':['admission_deferred'],'WMP_RX':['unique','qfull','bad']}
                c['delta']={p:{s+'.'+k:ports[p]['lines'][s][k]-first[p]['lines'][s][k] for s,keys in fields.items() for k in keys} for p in ports}
                c['pool_max']={k:max(v['ports'][source]['lines']['WMP_POOL'][k] for v in c['samples']) for k in ['repair_pending','retired_repair','retired','borrowed']}
                if any(c['delta'][p]['WMP_BT.rtp']<=0 or c['delta'][p]['WMP_GATT.applied']<=0 for p in ports):raise RuntimeError('Actual input/BLE progress missing')
                save(out/'receipt.json',r);print(dict(limit=limit,source_conceals=c['delta'][source]['WMP_AUDIO.concealed'],receiver_conceals=c['delta'][receiver]['WMP_AUDIO.concealed'],source_drops=c['delta'][source]['WMP_BT.blocked_frames'],pool_max=c['pool_max']),flush=True)
            r['acquisition_complete']=True
    except Exception as e:r['errors'].append(str(e));r['traceback']=traceback.format_exc()
    finally:
        if source and original is not None:
            try:r['restore']=command(source,'REPAIRLIMIT '+str(original),'WMP_REPAIR_LIMIT')
            except Exception as e:r['errors'].append('restore '+str(e))
        save(out/'receipt.json',r)
    print(dict(acquisition_complete=r['acquisition_complete'],errors=r['errors']),flush=True)
    if r['errors']:raise SystemExit(1)

if __name__=='__main__':main()
