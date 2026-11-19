"""Source-only continuous control measurement while the peer is investigated.

This cannot qualify receiver playback, handover, or the complete party system.
"""
import bench_ids
import argparse,json,time
from pathlib import Path
from board_io import ROOT,save
from sweep_live_pacing import sample
from qualify_party_long import media

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--seconds',type=int,default=3900);a=parser.parse_args()
    out=ROOT/'evidence'/('source-long-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes())
    row=dict(scope='COM7 source only; peer intentionally under diagnostics',samples=[],media=[],failures=[],errors=[],acquisition_complete=False,audio_qualified=False,party_qualified=False,requested_seconds=a.seconds)
    print('Source long:',out,flush=True)
    try:
        start=time.monotonic();first=None;last_media=-60
        while True:
            item=sample('COM7');now=item['lines'];elapsed=time.monotonic()-start
            row['samples'].append(dict(elapsed=elapsed,source=item));first=first or now;failures=[]
            if not all(now['WMP_BT'][k] for k in ['phone','streaming','ble']):failures.append('required live source workload disappeared')
            if now['WMP_RADIO']['mode']!=2 or not now['WMP_AUDIO']['running']:failures.append('source or DAC stopped')
            for section,keys in {'WMP_AUDIO':['concealed','late','underrun','write_errors','codec_bad','pcm_bad','qfull'],
                'WMP_BT':['blocked_frames','rtp_gaps','rtp_bad'],'WMP_DMA':['queue_overflow'],
                'WMP_BT_LINKS':['classic_disconnects','ble_disconnects','connection_failures'],'WMP_TIMELINE':['recoveries'],'WMP_VHCI':['dropped']}.items():
                for key in keys:
                    if now[section][key]!=first[section][key]:failures.append(section+'.'+key)
            if now['WMP_COPY']['generation']!=first['WMP_COPY']['generation'] or now['WMP_DMA']['start_us32']!=first['WMP_DMA']['start_us32']:failures.append('source session or DAC restarted')
            if elapsed>15 and now['WMP_BT']['ble_writes']-first['WMP_BT']['ble_writes']<elapsed/2:failures.append('insufficient actual BLE writes')
            if elapsed-last_media>=60:
                state=media(bench_ids.PHONE_MAIN);row['media'].append(dict(elapsed=elapsed,phone=state));last_media=elapsed
                if state['youtube_state'] not in ['PLAYING','3']:failures.append('YouTube is not playing')
                print(json.dumps(dict(elapsed=round(elapsed,1),failures=failures,heap=now['WMP_MEMORY']['free'])),flush=True)
            row['failures']=failures;save(out/'receipt.json',row)
            if failures:row['terminal_reason']='source_fault';break
            if elapsed>=a.seconds:row['acquisition_complete']=True;row['terminal_reason']='requested_duration';break
            time.sleep(min(5,a.seconds-elapsed))
        row['source_only_pass']=row['acquisition_complete'] and not row['failures']
    except Exception as error:row['errors'].append(repr(error));row['source_only_pass']=False
    save(out/'receipt.json',row);print(json.dumps(dict(evidence=str(out),source_only_pass=row['source_only_pass'],failures=row['failures'],errors=row['errors'],party_qualified=False)),flush=True)
if __name__=='__main__':main()
