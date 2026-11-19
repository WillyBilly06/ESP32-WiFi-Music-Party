"""Live four-copy audio window test; keep actual A2DP/BLE and track heap."""
import argparse,concurrent.futures,time,traceback
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair
from qualify_party_long import PHONES,peer,media
from firmware_evidence import installed_pair,live_pair
from audio_fixture import FIXTURES,require as require_fixture,check_sources
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--burst-shape',action='store_true');parser.add_argument('--app',type=Path,required=True);parser.add_argument('--fixture',choices=FIXTURES,default='redmagic-linux');parser.add_argument('--windows',default='16,8,16');parser.add_argument('--seconds',type=int,default=45);args=parser.parse_args()
    windows=[int(v) for v in args.windows.split(',')]
    if not 30<=args.seconds<=180 or not 1<=len(windows)<=4 or any(v<2 or v>32 for v in windows):parser.error('Finite30..180s cases,2..32 native buffers')
    out=ROOT/'evidence'/('audio-tx-window-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes());r=dict(diagnostic_only=True,audio_qualified=False,cases=[],errors=[],acquisition_complete=False);source=None;original=None;print(out,flush=True)
    original_pace=None
    try:
        r['fixture']=require_fixture(args.fixture);r['binding']=installed_pair(args.app);r['live']=live_pair(r['binding'],command)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            initial=pair(pool);r['initial']=initial;source=next(p for p,v in initial.items() if v['lines']['WMP_RADIO']['mode']==2);receiver=next(p for p in initial if p!=source);original=initial[source]['lines']['WMP_TX_WINDOW']['active_limit'];phones=[PHONES[peer(v['lines'])] for v in initial.values()]
            check_sources(r['fixture'],phones)
            original_pace=initial[source]['lines']['WMP_TX_SCHEDULE']['period_us']
            if args.burst_shape and any(not v['lines']['WMP_RECEIVE_WINDOW']['enabled'] for v in initial.values()):raise RuntimeError('Burst experiment requires wake registration')
            capacity=initial[source]['lines']['WMP_TX_WINDOW']['allocated']
            plans=[(min(24,capacity),0),(8,1000),(min(24,capacity),0)] if args.burst_shape else [(w,original_pace) for w in windows]
            if any(w>capacity for w,_ in plans):raise RuntimeError('Requested native window exceeds installed metadata capacity')
            for window,pace in plans:
                before_control=pair(pool);c=dict(window=window,pace_us=pace,samples=[],before_control=before_control,media=[media(p) for p in phones]);r['cases'].append(c)
                if any(v.get('playback_state',v.get('youtube_state')) not in ('PLAYING','3') for v in c['media']):raise RuntimeError('Actual music stopped')
                c['control']=command(source,'TXWINDOW '+str(window),'WMP_TX_WINDOW_REQUEST')
                if 'accepted=1' not in c['control']['reply']:raise RuntimeError('Window rejected')
                c['pacing_control']=command(source,'TXPACE '+str(pace),'WMP_TXPACE')
                if 'accepted=1' not in c['pacing_control']['reply']:raise RuntimeError('Pace rejected')
                time.sleep(2);first=pair(pool);start=time.monotonic()
                while True:
                    ports=first if not c['samples'] else pair(pool);elapsed=time.monotonic()-start;c['samples'].append(dict(elapsed=elapsed,ports=ports));save(out/'receipt.json',r)
                    for p,v in ports.items():
                        s=v['lines'];base=initial[p]['lines']
                        if not all(s['WMP_BT'][k] for k in ['phone','ble','transport_started','streaming']):raise RuntimeError('Required transport disappeared: '+p)
                        if s['WMP_RADIO']['copies']!=4 or s['WMP_COPY']['fec'] or s['WMP_REPAIR_GATE']['enabled']!=1:raise RuntimeError('Required original/repair policy changed')
                        if s['WMP_SESSION']['generation']!=base['WMP_SESSION']['generation'] or s['WMP_RADIO']['mode']!=base['WMP_RADIO']['mode'] or s['WMP_DMA']['start_us32']!=base['WMP_DMA']['start_us32']:raise RuntimeError('Role/session/DAC changed')
                    if ports[source]['lines']['WMP_TX_WINDOW']['active_limit']!=window or ports[source]['lines']['WMP_TX_SCHEDULE']['period_us']!=pace:raise RuntimeError('Window/pace not applied')
                    if any(ports[source]['lines'][sec][key]!=before_control[source]['lines'][sec][key] for sec,keys in {'WMP_AUDIO':['concealed','late','underrun'],'WMP_BT':['rtp_gaps','blocked_frames'],'WMP_SOURCE_QUEUE':['full']}.items() for key in keys):raise RuntimeError('Source path fault under native window; restoring')
                    if elapsed>=args.seconds:break
                    time.sleep(2)
                fields={'WMP_BT':['frames','rtp_gaps','blocked_frames'],'WMP_AUDIO':['concealed','late','underrun'],'WMP_RADIO':['accepted','done','qfull','late'],'WMP_REPAIR_AIR':['accepted'],'WMP_GATT':['applied','notifications'],'WMP_SOURCE_QUEUE':['full']}
                c['delta']={p:{s+'.'+k:ports[p]['lines'][s][k]-before_control[p]['lines'][s][k] for s,keys in fields.items() for k in keys} for p in ports};seconds=(ports[source]['lines']['WMP_RADIO']['time_us']-before_control[source]['lines']['WMP_RADIO']['time_us'])/1e6;c['seconds_including_transition']=seconds;c['raw_pps']=c['delta'][source]['WMP_RADIO.accepted']/seconds;c['heap_min_current']=min(v['ports'][source]['lines']['WMP_MEMORY']['free'] for v in c['samples'])
                if any(c['delta'][p]['WMP_BT.frames']<=0 or c['delta'][p]['WMP_GATT.applied']<=0 or c['delta'][p]['WMP_GATT.notifications']<=0 for p in ports):raise RuntimeError('Real input/BLE made no progress')
                save(out/'receipt.json',r);print(dict(window=window,raw_pps=round(c['raw_pps'],1),source_drops=c['delta'][source]['WMP_BT.blocked_frames'],source_conceals=c['delta'][source]['WMP_AUDIO.concealed'],receiver_conceals=c['delta'][receiver]['WMP_AUDIO.concealed'],heap=c['heap_min_current']),flush=True)
            r['acquisition_complete']=True
    except Exception as e:r['errors'].append(str(e));r['traceback']=traceback.format_exc()
    finally:
        if source and original is not None:
            try:r['restore']=command(source,'TXWINDOW '+str(original),'WMP_TX_WINDOW_REQUEST')
            except Exception as e:r['errors'].append('restore '+str(e))
        if source and original_pace is not None:
            try:r['restore_pace']=command(source,'TXPACE '+str(original_pace),'WMP_TXPACE')
            except Exception as e:r['errors'].append('restore pace '+str(e))
        save(out/'receipt.json',r)
    print(dict(acquisition_complete=r['acquisition_complete'],errors=r['errors']),flush=True)
    if r['errors']:raise SystemExit(1)
if __name__=='__main__':main()
