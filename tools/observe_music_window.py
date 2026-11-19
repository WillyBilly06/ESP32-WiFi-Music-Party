"""Unchanged single-source music/BLE window; never substitutes for full goal."""
import bench_ids
import argparse,concurrent.futures,time
from board_io import ROOT,save
from sweep_live_pacing import pair
from qualify_party_long import media

def main():
    p=argparse.ArgumentParser();p.add_argument('--seconds',type=int,default=180);a=p.parse_args()
    if not 60<=a.seconds<=3900:p.error('60..3900 seconds')
    out=ROOT/'evidence'/('music-window-'+str(time.time_ns()));out.mkdir()
    row=dict(scope='REDMAGIC A2DP on COM7, direct Wi-Fi receiver COM6, continuous BLE on both; Linux incoming Classic absent.',requested_seconds=a.seconds,samples=[],errors=[],faults=[],audio_qualified=False,digital_window_pass=False)
    print(out,flush=True)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first=pair(pool);row['phone_before']=media(bench_ids.PHONE_MAIN);start=time.monotonic()
            gen=first['COM7']['lines']['WMP_SESSION']['generation']
            if not gen or gen!=first['COM6']['lines']['WMP_SESSION']['generation']:raise RuntimeError('No shared session')
            while True:
                states=first if not row['samples'] else pair(pool);elapsed=time.monotonic()-start;row['samples'].append(dict(elapsed=elapsed,ports=states));save(out/'receipt.json',row)
                for port,v in states.items():
                    now=v['lines'];old=first[port]['lines']
                    if not now['WMP_BT']['ble'] or not now['WMP_AUDIO']['running']:row['faults'].append(port+' live BLE/audio disappeared')
                    if port=='COM7' and (not now['WMP_BT']['streaming'] or now['WMP_RADIO']['copies']!=4):row['faults'].append('Source actual A2DP/four-copy load disappeared')
                    if now['WMP_SESSION']['generation']!=gen:row['faults'].append(port+' session changed')
                    if now['WMP_MEMORY']['free']<30000:row['faults'].append(port+' current heap below30000B')
                    for section,keys in {'WMP_AUDIO':['concealed','late','qfull','codec_bad','pcm_bad','underrun','write_errors'],'WMP_BT':['rtp_gaps','rtp_bad'],'WMP_VHCI':['dropped'],'WMP_SOURCE_QUEUE':['full'],'WMP_RX':['qfull','bad','conflict'],'WMP_AUDIO_RESOURCES':['starts','stops','failures'],'WMP_ASSOC_EVENTS':['connected','disconnected']}.items():
                        for key in keys:
                            if now[section][key]!=old[section][key]:row['faults'].append(port+' '+section+'.'+key)
                    if now['WMP_COPY']['spacing']!=old['WMP_COPY']['spacing']:row['faults'].append(port+' schedule changed')
                if row['faults'] or elapsed>=a.seconds:break
                time.sleep(min(2,a.seconds-elapsed))
            row['phone_after']=media(bench_ids.PHONE_MAIN)
            for port,v in states.items():
                now=v['lines'];old=first[port]['lines']
                if now['WMP_BT']['ble_writes']<=old['WMP_BT']['ble_writes'] or now['WMP_AUDIO']['decoded']<=old['WMP_AUDIO']['decoded']:row['faults'].append(port+' actual BLE/audio did not advance')
            row['digital_window_pass']=elapsed>=a.seconds and not row['faults']
    except Exception as error:row['errors'].append(str(error))
    finally:save(out/'receipt.json',row)
    print(dict(pass_window=row['digital_window_pass'],seconds=row['samples'][-1]['elapsed'] if row['samples'] else 0,faults=row['faults'],errors=row['errors']),flush=True)
    if not row['digital_window_pass']:raise SystemExit(1)

if __name__=='__main__':main()
