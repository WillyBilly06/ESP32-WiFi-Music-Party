"""Unchanged two-source music/BLE, comparing fixed versus dephased copies.

No pauses, role commands, resets, loss injections or parity are used.
"""
import concurrent.futures,json,time,traceback
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair
from qualify_party_long import linux_status

def main():
    out=ROOT/'evidence'/('copy-dither-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes())
    report=dict(diagnostic_only=True,audio_qualified=False,cases=[],errors=[],acquisition_complete=False);original=None;source=None
    print(out,flush=True)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            initial=pair(pool);report['initial']=initial;sources=[p for p,v in initial.items() if v['lines']['WMP_RADIO']['mode']==2]
            if len(sources)!=1:raise RuntimeError('Need one existing source')
            source=sources[0];receiver=next(p for p in initial if p!=source);original=initial[source]['lines']['WMP_TX_DITHER']['us'];generation=initial[source]['lines']['WMP_SESSION']['generation']
            if not generation or initial[source]['lines']['WMP_RADIO']['copies']!=4 or initial[source]['lines']['WMP_RADIO']['spacing']!=64000 or initial[source]['lines']['WMP_COPY']['paced']:raise RuntimeError('Need four absolute64ms original copies')
            report.update(source=source,receiver=receiver,installed_images={p:json.loads((ROOT/'hardware'/('deployed-'+p+'.json')).read_text()) for p in initial})
            for jitter in [0,8000,0,8000]:
                c=dict(dither_us=jitter,samples=[],lapses={p:0 for p in initial});report['cases'].append(c)
                c['linux_before']=linux_status()['playback']
                if not c['linux_before'].get('ready'):raise RuntimeError('Linux music not route/render ready')
                c['control']=command(source,'TXDITHER '+str(jitter),'WMP_TX_DITHER_REQUEST')
                if 'accepted=1' not in c['control']['reply']:raise RuntimeError('Dither request rejected')
                time.sleep(.25);first=pair(pool);start=time.monotonic()
                while True:
                    ports=first if not c['samples'] else pair(pool);elapsed=time.monotonic()-start;c['samples'].append(dict(elapsed=elapsed,ports=ports));save(out/'receipt.json',report)
                    for p,v in ports.items():
                        s=v['lines'];base=initial[p]['lines']
                        if not s['WMP_BT']['streaming']:c['lapses'][p]+=1
                        if not all(s['WMP_BT'][k] for k in ['phone','ble','transport_started']):raise RuntimeError('Actual input/LE transport disappeared: '+p)
                        if s['WMP_SESSION']['generation']!=generation or s['WMP_RADIO']['mode']!=base['WMP_RADIO']['mode'] or s['WMP_DMA']['start_us32']!=base['WMP_DMA']['start_us32']:raise RuntimeError('Session/role/DAC changed')
                        for section,keys in {'WMP_RADIO':['copies','spacing'],'WMP_PHY':['selected_mbps'],'WMP_TX_WINDOW':['active_limit'],'WMP_PROFILE_STAT':['enabled','applied'],'WMP_REPLAY':['enabled','nack_delay_us'],'WMP_ASSOC_EVENTS':['connected','disconnected']}.items():
                            if any(s[section][k]!=base[section][k] for k in keys):raise RuntimeError('Unrelated configuration/link change: '+p+' '+section)
                    if ports[source]['lines']['WMP_TX_DITHER']['us']!=jitter:raise RuntimeError('Dither not applied')
                    if elapsed>=60:break
                    time.sleep(3)
                c['linux_after']=linux_status()['playback']
                if not c['linux_after'].get('ready'):raise RuntimeError('Linux rendering stopped')
                fields={'WMP_AUDIO':['concealed','late','underrun','qfull','decoded'],'WMP_BT':['rtp','rtp_gaps','frames','blocked_frames'],'WMP_GATT':['applied','notifications'],'WMP_RX':['unique','qfull','bad'],'WMP_RADIO':['attempt','accepted','done','api_error','timeout'],'WMP_SOURCE_QUEUE':['full']}
                c['delta']={p:{sec+'.'+k:ports[p]['lines'][sec][k]-first[p]['lines'][sec][k] for sec,keys in fields.items() for k in keys} for p in ports};c['elapsed']=elapsed
                if any(c['delta'][p]['WMP_BT.rtp']<=0 or c['delta'][p]['WMP_GATT.applied']<=0 for p in ports):raise RuntimeError('Source/BLE progress disappeared')
                save(out/'receipt.json',report);print(dict(jitter=jitter,seconds=round(elapsed,2),receiver_conceals=c['delta'][receiver]['WMP_AUDIO.concealed'],source_conceals=c['delta'][source]['WMP_AUDIO.concealed'],standby_rtp_gaps=c['delta'][receiver]['WMP_BT.rtp_gaps']),flush=True)
            report['acquisition_complete']=True
    except Exception as e:report['errors'].append(str(e));report['traceback']=traceback.format_exc()
    finally:
        if source and original is not None:
            try:
                report['restore']=command(source,'TXDITHER '+str(original),'WMP_TX_DITHER_REQUEST')
                if 'accepted=1' not in report['restore']['reply']:raise RuntimeError('Restore rejected')
            except Exception as e:report['errors'].append('restore: '+str(e))
        save(out/'receipt.json',report)
    print(dict(acquisition_complete=report['acquisition_complete'],errors=report['errors']),flush=True)
    if report['errors']:raise SystemExit(1)

if __name__=='__main__':main()
