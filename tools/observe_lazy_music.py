"""Bounded single-source music/BLE observation, not full party qualification."""
import concurrent.futures,time
from board_io import ROOT,save
from sweep_live_pacing import pair

def main():
    out=ROOT/'evidence'/('lazy-music-observation-'+str(time.time_ns()));out.mkdir()
    report=dict(scope='REDMAGIC A2DP on COM7, Wi-Fi audio to COM6, BLE clients on both. Linux Classic absent.',requested_seconds=120,audio_qualified=False,observation_pass=False,samples=[],errors=[])
    print(out,flush=True)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first=pair(pool);start=time.monotonic()
            while True:
                value=first if not report['samples'] else pair(pool);elapsed=time.monotonic()-start
                report['samples'].append(dict(elapsed=elapsed,ports=value));save(out/'receipt.json',report)
                for p,v in value.items():
                    a=v['lines'];b=first[p]['lines']
                    if not a['WMP_BT']['ble'] or not a['WMP_AUDIO']['running']:raise RuntimeError(p+' BLE or audio disappeared')
                    if p=='COM7' and not a['WMP_BT']['streaming']:raise RuntimeError('Actual source A2DP stopped')
                    if a['WMP_MEMORY']['minimum']<30000:raise RuntimeError(p+' measured heap low-water below30000bytes')
                    if any(a['WMP_AUDIO_RESOURCES'][k]!=b['WMP_AUDIO_RESOURCES'][k] for k in ('starts','stops','codec_starts','codec_stops')):raise RuntimeError(p+' resource lifecycle changed during music')
                    for key in ('concealed','late','underrun','codec_bad','pcm_bad','write_errors'):
                        if a['WMP_AUDIO'][key]!=b['WMP_AUDIO'][key]:raise RuntimeError(p+' audio counter increased: '+key)
                if elapsed>=120:break
                time.sleep(2)
            for p,v in value.items():
                a=v['lines'];b=first[p]['lines']
                if a['WMP_AUDIO']['decoded']<=b['WMP_AUDIO']['decoded'] or a['WMP_BT']['ble_writes']<=b['WMP_BT']['ble_writes']:raise RuntimeError(p+' music or BLE did not advance')
            report['observation_pass']=True
    except Exception as error:report['errors'].append(str(error))
    finally:save(out/'receipt.json',report)
    print(dict(observation_pass=report['observation_pass'],completed_seconds=report['samples'][-1]['elapsed'] if report['samples'] else 0,errors=report['errors']),flush=True)
    if not report['observation_pass']:raise SystemExit(1)

if __name__=='__main__':main()
