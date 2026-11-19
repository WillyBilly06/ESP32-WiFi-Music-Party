"""Finite real-phone pause/resume check through both owned persistent UARTs.

This is deliberately not a continuous-music or acoustic qualification run.
"""
import bench_ids
import concurrent.futures,json,re,subprocess,time
from pathlib import Path
from board_io import ROOT,save
from sweep_live_pacing import pair

PHONE=bench_ids.PHONE_MAIN
def dispatch(action):
    r=subprocess.run(['adb','-s',PHONE,'shell','cmd','media_session','dispatch',action],capture_output=True,text=True,timeout=20)
    if r.returncode:raise RuntimeError(r.stderr)
    expected='PAUSED' if action=='pause' else 'PLAYING';observed=None
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        text=subprocess.check_output(['adb','-s',PHONE,'shell','dumpsys','media_session'],text=True,encoding='utf-8')
        match=re.search(r'package=com.google.android.youtube.*?state=PlaybackState \{state=([A-Z]+)',text,re.S)
        observed=match.group(1) if match else None
        if observed==expected:break
        time.sleep(.1)
    if observed!=expected:raise RuntimeError('YouTube did not enter '+expected+': '+str(observed))
    return dict(action=action,stdout=r.stdout,stderr=r.stderr,youtube_state=observed)

def main():
    out=ROOT/'evidence'/('pause-resume-'+str(time.time_ns()));out.mkdir()
    (out/'test.py').write_bytes(Path(__file__).read_bytes())
    row=dict(pass_all=False,acquisition_complete=False,audio_qualified=False,workload='real YouTube pause/resume with BLE and continuous Wi-Fi silence markers',samples=[],commands=[],errors=[],failures=[])
    print('Pause/resume:',out,flush=True)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first=pair(pool);sources=[p for p,v in first.items() if v['lines']['WMP_RADIO']['mode']==2]
            if len(sources)!=1:raise RuntimeError('Ambiguous source')
            source=sources[0];receiver='COM6' if source=='COM7' else 'COM7';row.update(source=source,receiver=receiver)
            if not all(first[source]['lines']['WMP_BT'][k] for k in ['phone','streaming','ble']):raise RuntimeError('Actual A2DP + BLE workload not ready')
            start=time.monotonic()
            def sample(phase):
                ports=pair(pool);v=dict(elapsed=time.monotonic()-start,phase=phase,ports=ports);row['samples'].append(v);save(out/'receipt.json',row);return ports
            sample('music')
            for duration in [2,15,30]:
                row['commands'].append(dispatch('pause'));deadline=time.monotonic()+duration
                while time.monotonic()<deadline:
                    time.sleep(min(1,deadline-time.monotonic()));sample('paused-'+str(duration))
                row['commands'].append(dispatch('play'));deadline=time.monotonic()+12
                while time.monotonic()<deadline:
                    time.sleep(min(2,deadline-time.monotonic()));sample('resumed-'+str(duration))
            last=row['samples'][-1]['ports'];row['acquisition_complete']=True
            baseline=row['samples'][0]['ports'];failure=row['failures']
            for sample_row in row['samples']:
                for port,entry in sample_row['ports'].items():
                    old=baseline[port]['lines'];now=entry['lines']
                    for section,keys in {'WMP_AUDIO':['concealed','late','underrun','codec_bad','pcm_bad','write_errors','qfull'],'WMP_BT':['blocked_frames','rtp_gaps','rtp_bad'],'WMP_RX':['qfull'],'WMP_DMA':['queue_overflow'],'WMP_BT_LINKS':['classic_disconnects','ble_disconnects','connection_failures']}.items():
                        for key in keys:
                            if now[section].get(key)!=old[section].get(key):failure.append(port+' '+section+'.'+key)
                    if now['WMP_COPY']['generation']!=old['WMP_COPY']['generation']:failure.append(port+' stream generation changed')
                    if not now['WMP_AUDIO']['running']:failure.append(port+' DAC stopped')
                src=sample_row['ports'][source]['lines']
                if not src['WMP_BT']['phone'] or not src['WMP_BT']['ble']:failure.append('phone/BLE disconnected')
                if src['WMP_RADIO']['mode']!=2:failure.append('source role released')
                if not src['WMP_PHY']['source_tsf']:failure.append('source clock stopped')
                if not sample_row['ports'][receiver]['lines']['WMP_AUDIO']['clock_valid']:failure.append('receiver clock lost')
            for duration in [2,15,30]:
                paused=[v for v in row['samples'] if v['phase']=='paused-'+str(duration)]
                suspended=any(not v['ports'][source]['lines']['WMP_BT']['streaming'] for v in paused)
                if duration>=15 and not suspended:failure.append('phone never suspended for '+str(duration))
                resumed=[v for v in row['samples'] if v['phase']=='resumed-'+str(duration)]
                if not resumed[-1]['ports'][source]['lines']['WMP_BT']['streaming']:failure.append('phone did not resume for '+str(duration))
                for port in [source,receiver]:
                    key='played_frames'
                    if suspended:
                        if paused[-1]['ports'][port]['lines']['WMP_SILENCE'][key]<=paused[0]['ports'][port]['lines']['WMP_SILENCE'][key]:failure.append(port+' silence did not advance for '+str(duration))
                    elif paused[-1]['ports'][port]['lines']['WMP_AUDIO']['decoded']<=paused[0]['ports'][port]['lines']['WMP_AUDIO']['decoded']:failure.append(port+' retained phone SBC stopped during short pause')
                    if resumed[-1]['ports'][port]['lines']['WMP_AUDIO']['decoded']<=paused[-1]['ports'][port]['lines']['WMP_AUDIO']['decoded']:failure.append(port+' music did not decode after '+str(duration))
            if last[source]['lines']['WMP_BT']['ble_writes']<=baseline[source]['lines']['WMP_BT']['ble_writes']:failure.append('no actual BLE transactions')
            row['summary']={p:{'intentional_silence_samples':last[p]['lines']['WMP_SILENCE']['played_frames']-baseline[p]['lines']['WMP_SILENCE']['played_frames'],'music_decoded':last[p]['lines']['WMP_AUDIO']['decoded']-baseline[p]['lines']['WMP_AUDIO']['decoded'],'dma_events':last[p]['lines']['WMP_DMA']['events']-baseline[p]['lines']['WMP_DMA']['events']} for p in [source,receiver]}
            row['ble_writes']=last[source]['lines']['WMP_BT']['ble_writes']-baseline[source]['lines']['WMP_BT']['ble_writes']
            row['failures']=sorted(set(failure));row['pass_all']=not row['failures']
    except Exception as e:row['errors'].append(repr(e))
    finally:
        # Leave the user's existing music session playing, even after a failure.
        try:row['commands'].append(dispatch('play'))
        except Exception as e:row['errors'].append('restore play: '+repr(e));row['pass_all']=False
        save(out/'receipt.json',row)
    print(json.dumps(dict(evidence=str(out),pass_all=row['pass_all'],failures=row['failures'],errors=row['errors'])),flush=True)
if __name__=='__main__':main()
