"""Fixed-load receiver-priority crossover; diagnostic, never a qualification.

Both real A2DP senders, four originals and both BLE clients remain enabled.
RTP inactivity/gaps are retained as workload failures, not hidden as silence.
"""
import bench_ids
import concurrent.futures,json,time,traceback
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair,sample
from qualify_party_long import PHONES,peer,media

def main():
    out=ROOT/'evidence'/('standby-priority-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes())
    report=dict(diagnostic_only=True,audio_qualified=False,cases=[],errors=[],acquisition_complete=False)
    original=None;receiver=None;print(out,flush=True)
    try:
        from adb_local import require_testing,SAMSUNG
        require_testing(SAMSUNG)  # local admission check before any board/phone action
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            initial=pair(pool);report['initial']=initial
            sources=[p for p,v in initial.items() if v['lines']['WMP_RADIO']['mode']==2]
            if len(sources)!=1:raise RuntimeError('Need one existing leader')
            source=sources[0];receiver=next(p for p in initial if p!=source)
            phones=[PHONES[peer(v['lines'])] for v in initial.values()]
            if set(phones)!={bench_ids.PHONE_MAIN,bench_ids.PHONE_SECOND}:raise RuntimeError('Both real admitted phones required')
            original=initial[receiver]['lines']['WMP_PROFILE_STAT']['enabled']
            generation=initial[source]['lines']['WMP_SESSION']['generation']
            if not generation or initial[source]['lines']['WMP_RADIO']['copies']!=4:raise RuntimeError('Need current four-copy stream')
            report.update(source=source,receiver=receiver,installed_images={p:json.loads((ROOT/'hardware'/('deployed-'+p+'.json')).read_text()) for p in initial})
            for enabled in [2,1,2,1]:
                case=dict(receiver_profile=enabled,samples=[],workload_lapse_samples={p:0 for p in initial});report['cases'].append(case)
                case['media_before']=[media(p) for p in phones]
                if any(v['youtube_state'] not in ('PLAYING','3') for v in case['media_before']):raise RuntimeError('Actual phone music is not playing')
                # Firmware replies WMP_A2DP_COEX only while the controller runs; skip a no-op request.
                if sample(receiver)['lines']['WMP_PROFILE_STAT']['enabled']==enabled:case['control']=dict(skipped=True,already_enabled=enabled)
                else:
                    case['control']=command(receiver,'A2DPCOEX '+str(enabled),'WMP_A2DP_COEX')
                    if 'status=0' not in case['control']['reply']:raise RuntimeError('Controller profile rejected')
                time.sleep(.25);first=pair(pool);start=time.monotonic()
                while True:
                    ports=first if not case['samples'] else pair(pool);elapsed=time.monotonic()-start
                    case['samples'].append(dict(elapsed=elapsed,ports=ports));save(out/'receipt.json',report)
                    for p,v in ports.items():
                        s=v['lines'];base=initial[p]['lines']
                        if not s['WMP_BT']['streaming']:case['workload_lapse_samples'][p]+=1
                        if not all(s['WMP_BT'][k] for k in ['phone','ble','transport_started']):raise RuntimeError(p+' actual Classic/LE transport disappeared')
                        if s['WMP_SESSION']['generation']!=generation or s['WMP_RADIO']['mode']!=base['WMP_RADIO']['mode']:raise RuntimeError('Session or ownership changed')
                        if s['WMP_DMA']['start_us32']!=base['WMP_DMA']['start_us32']:raise RuntimeError('DAC restarted')
                        for section,keys in {'WMP_RADIO':['copies','spacing'],'WMP_PHY':['selected_mbps'],'WMP_TX_WINDOW':['active_limit'],'WMP_TX_SCHEDULE':['period_us'],'WMP_REPLAY':['enabled','nack_delay_us'],'WMP_WIFI_MODE':['channel'],'WMP_ASSOC_EVENTS':['connected','disconnected']}.items():
                            if any(s[section][k]!=base[section][k] for k in keys):raise RuntimeError('Unrelated configuration/link change: '+p+' '+section)
                    selected=ports[receiver]['lines']['WMP_PROFILE_STAT']
                    if selected['enabled']!=enabled or selected['applied']!=(0 if enabled==1 else 1) or selected['status']:raise RuntimeError('Requested receiver profile not applied')
                    if elapsed>=30:break
                    time.sleep(2)
                case['media_after']=[media(p) for p in phones]
                if any(v['youtube_state'] not in ('PLAYING','3') for v in case['media_after']):raise RuntimeError('Actual phone music ceased playing')
                case['elapsed']=elapsed
                fields={'WMP_AUDIO':['concealed','late','underrun','qfull','decoded'],'WMP_BT':['rtp','rtp_gaps','frames','ble_writes','blocked_frames'],'WMP_GATT':['applied','notifications'],'WMP_RX':['unique','qfull','bad'],'WMP_SOURCE_QUEUE':['full'],'WMP_RADIO':['attempt','accepted','done','api_error','timeout'],'WMP_VHCI':['dropped']}
                case['delta']={p:{section+'.'+k:ports[p]['lines'][section][k]-first[p]['lines'][section][k] for section,keys in fields.items() for k in keys} for p in ports}
                if any(case['delta'][p]['WMP_BT.rtp']<=0 or case['delta'][p]['WMP_GATT.applied']<=0 for p in ports):raise RuntimeError('Real source/BLE workload made no progress')
                save(out/'receipt.json',report);print(dict(profile=enabled,seconds=round(elapsed,2),receiver_conceals=case['delta'][receiver]['WMP_AUDIO.concealed'],source_conceals=case['delta'][source]['WMP_AUDIO.concealed'],standby_rtp_gaps=case['delta'][receiver]['WMP_BT.rtp_gaps'],lapses=case['workload_lapse_samples']),flush=True)
            report['acquisition_complete']=True
    except Exception as e:report['errors'].append(str(e));report['traceback']=traceback.format_exc()
    finally:
        if receiver and original is not None:
            try:
                if sample(receiver)['lines']['WMP_PROFILE_STAT']['enabled']==original:report['restore']=dict(skipped=True,already_enabled=original)
                else:
                    report['restore']=command(receiver,'A2DPCOEX '+str(original),'WMP_A2DP_COEX')
                    if 'status=0' not in report['restore']['reply']:raise RuntimeError('Profile restore rejected')
            except Exception as e:report['errors'].append('restore: '+str(e))
        save(out/'receipt.json',report)
    print(dict(acquisition_complete=report['acquisition_complete'],errors=report['errors']),flush=True)
    if report['errors']:raise SystemExit(1)

if __name__=='__main__':main()
