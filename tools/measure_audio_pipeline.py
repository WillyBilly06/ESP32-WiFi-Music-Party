"""Unchanged full two-phone/BLE/four-copy workload, with stage timing."""
import argparse,concurrent.futures,json,time,traceback
from pathlib import Path
from board_io import ROOT,save
from sweep_live_pacing import pair
from qualify_party_long import PHONES,peer,media
from adb_local import shell,SAMSUNG
from firmware_evidence import installed_pair,live_pair
from capture_copies import command
from audio_fixture import FIXTURES,require as require_fixture,check_sources

def main():
    p=argparse.ArgumentParser();p.add_argument('--seconds',type=int,default=60);p.add_argument('--app',type=Path);p.add_argument('--fixture',choices=FIXTURES,default='redmagic-samsung');a=p.parse_args()
    if not 30<=a.seconds<=600:p.error('30..600 seconds')
    out=ROOT/'evidence'/('pipeline-timing-'+str(time.time_ns()));out.mkdir();(out/'capture.py').write_bytes(Path(__file__).read_bytes())
    r=dict(requested_seconds=a.seconds,samples=[],errors=[],acquisition_complete=False,audio_qualified=False,diagnostic_only=True);print(out,flush=True)
    try:
        r['fixture']=require_fixture(a.fixture)
        r['candidate_target_asserted']=bool(a.app)
        if a.app:
            r['firmware_binding']=installed_pair(a.app);r['live_firmware']=live_pair(r['firmware_binding'],command)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first=pair(pool);sources=[p for p,v in first.items() if v['lines']['WMP_RADIO']['mode']==2]
            if len(sources)!=1:raise RuntimeError('Need exactly one current source')
            source=sources[0];receiver=next(p for p in first if p!=source);phones=[PHONES[peer(v['lines'])] for v in first.values()]
            check_sources(r['fixture'],phones)
            generation=first[source]['lines']['WMP_SESSION']['generation']
            if not generation or any(v['lines']['WMP_SESSION']['generation']!=generation for v in first.values()):raise RuntimeError('No shared nonzero session')
            r.update(source=source,receiver=receiver,media_before=[media(p) for p in phones],images={p:json.loads((ROOT/'hardware'/('deployed-'+p+'.json')).read_text()) for p in first})
            if a.fixture=='redmagic-samsung':
                r['samsung_volume']=shell(SAMSUNG,['cmd','media_session','volume','--stream','3','--get']).strip()
                if 'volume is 3 in range [0..15]' not in r['samsung_volume']:raise RuntimeError('Samsung is not at20 percent')
            if any(v.get('playback_state',v.get('youtube_state')) not in ('PLAYING','3') for v in r['media_before']):raise RuntimeError('Actual music not playing')
            start=time.monotonic()
            while True:
                require_fixture(a.fixture)
                ports=first if not r['samples'] else pair(pool);elapsed=time.monotonic()-start;r['samples'].append(dict(elapsed=elapsed,ports=ports));save(out/'receipt.json',r)
                for p,v in ports.items():
                    s=v['lines'];base=first[p]['lines']
                    if not all(k in s for k in ['WMP_TX_DWELL','WMP_TX_WAKE','WMP_DECODE','WMP_LOANS']):raise RuntimeError('Missing timing coverage')
                    if not all(s['WMP_BT'][k] for k in ['phone','transport_started','ble','streaming']):raise RuntimeError('Actual phone/BLE workload lapse '+p)
                    if not s['WMP_AUDIO']['running']:raise RuntimeError('DAC stopped '+p)
                    if s['WMP_PREPARATION']['source_wait'] or s['WMP_PREPARATION']['receiver_wait']:raise RuntimeError('Still preparing '+p)
                    if s['WMP_SESSION']['generation']!=base['WMP_SESSION']['generation'] or s['WMP_RADIO']['mode']!=base['WMP_RADIO']['mode'] or s['WMP_DMA']['start_us32']!=base['WMP_DMA']['start_us32']:raise RuntimeError('Session/role/DAC changed '+p)
                    if s['WMP_RADIO']['copies']!=4:raise RuntimeError('Four original copies required')
                    for section,keys in {'WMP_COPY':['spacing','paced','fec','coex_hint'],'WMP_TX_DITHER':['us'],'WMP_TX_WINDOW':['active_limit'],'WMP_RECEIVE_WINDOW':['enabled','interval_ms'],'WMP_PROFILE_STAT':['enabled','applied'],'WMP_PHY':['selected_mbps'],'WMP_ASSOC_EVENTS':['connected','disconnected']}.items():
                        if any(s[section][k]!=base[section][k] for k in keys):raise RuntimeError('Configuration/link changed '+p+' '+section)
                if elapsed>=a.seconds:break
                time.sleep(2)
            fields={'WMP_BT':['rtp','rtp_gaps','frames','blocked_frames','discontinuity'],'WMP_AUDIO':['decoded','concealed','late','underrun','codec_bad','pcm_bad','write_errors'],'WMP_RADIO':['accepted','done','qfull','late','api_error','timeout'],'WMP_SOURCE_QUEUE':['full'],'WMP_RX':['unique','qfull','bad','conflict'],'WMP_GATT':['applied','notifications'],'WMP_REPAIR_AIR':['accepted'],'WMP_REPLAY':['nack_sent','queued','received','played'],'WMP_VHCI':['dropped'],'WMP_DECODE':['calls','total_wall_us','over1ms'],'WMP_TX_DWELL':['original_completions','total_us','over20ms','over100ms','credit_waits'],'WMP_TX_WAKE':['calls','total_late_us','over1ms','over5ms'],'WMP_TX_API':['calls','total_us']}
            r['delta']={p:{s+'.'+k:ports[p]['lines'][s][k]-first[p]['lines'][s][k] for s,keys in fields.items() for k in keys} for p in ports};r['summary']={}
            for p in ports:
                d=r['delta'][p];seconds=(ports[p]['lines']['WMP_RADIO']['time_us']-first[p]['lines']['WMP_RADIO']['time_us'])/1e6
                r['summary'][p]=dict(seconds=seconds,incoming_sbc_fps=d['WMP_BT.frames']/seconds,raw_accepted_pps=d['WMP_RADIO.accepted']/seconds,original_completed_pps=d['WMP_TX_DWELL.original_completions']/seconds,decode_wall_percent=d['WMP_DECODE.total_wall_us']/(seconds*10000),decode_mean_wall_us=d['WMP_DECODE.total_wall_us']/max(1,d['WMP_DECODE.calls']),native_mean_dwell_us=d['WMP_TX_DWELL.total_us']/max(1,d['WMP_TX_DWELL.original_completions']),api_mean_us=d['WMP_TX_API.total_us']/max(1,d['WMP_TX_API.calls']),max_observed_original_age_us=max(v['ports'][p]['lines']['WMP_LOANS']['oldest_original_us'] for v in r['samples']),oldest_sequence_first=first[p]['lines']['WMP_LOANS']['oldest_sequence'],oldest_sequence_last=ports[p]['lines']['WMP_LOANS']['oldest_sequence'],minimum_observed_free_heap=min(v['ports'][p]['lines']['WMP_MEMORY']['free'] for v in r['samples']))
                if any(d[k]<=0 for k in ['WMP_BT.rtp','WMP_GATT.applied','WMP_GATT.notifications','WMP_DECODE.calls']):raise RuntimeError('Actual audio/BLE made no progress '+p)
            r['media_after']=[media(p) for p in phones];r['acquisition_complete']=True
    except Exception as e:r['errors'].append(str(e));r['traceback']=traceback.format_exc()
    save(out/'receipt.json',r);print(json.dumps(dict(acquisition_complete=r['acquisition_complete'],errors=r['errors'],summary=r.get('summary'))),flush=True)
    if r['errors']:raise SystemExit(1)
if __name__=='__main__':main()
