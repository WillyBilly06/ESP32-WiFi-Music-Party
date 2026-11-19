"""Finite paired live-music counters; never pauses music or resets a board."""
import argparse,concurrent.futures,json,re,secrets,subprocess,time
from pathlib import Path
from board_io import ROOT,save
def sample(port):
    from sweep_live_pacing import sample as persistent_verified_sample
    return persistent_verified_sample(port)
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seconds',type=int,default=60);p.add_argument('--interval',type=float,default=5);a=p.parse_args()
    if not 10<=a.seconds<=4600 or not 2<=a.interval<=30:p.error('Finite10..4600s with2..30s samples')
    out=ROOT/'evidence'/('live-audio-'+str(time.time_ns()));out.mkdir();row=dict(pass_all=False,acquisition_complete=False,audio_qualified=False,samples=[],errors=[],seconds=a.seconds)
    (out/'capture.py').write_bytes(Path(__file__).read_bytes());print('Capture:',out,flush=True)
    start=time.monotonic()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            while True:
                futures={port:pool.submit(sample,port) for port in ['COM6','COM7']}
                pair={port:f.result() for port,f in futures.items()};row['samples'].append(dict(elapsed=time.monotonic()-start,ports=pair));save(out/'receipt.json',row)
                print(json.dumps(dict(elapsed=round(time.monotonic()-start,2),ports={port:{'mode':v['lines']['WMP_RADIO']['mode'],'phone':v['lines']['WMP_BT']['phone'],'streaming':v['lines']['WMP_BT']['streaming'],'ble_writes':v['lines']['WMP_BT']['ble_writes'],'decoded':v['lines']['WMP_AUDIO']['decoded'],'concealed':v['lines']['WMP_AUDIO']['concealed'],'qfull':v['lines']['WMP_AUDIO']['qfull'],'clock_valid':v['lines']['WMP_AUDIO']['clock_valid']} for port,v in pair.items()})),flush=True)
                if time.monotonic()-start>=a.seconds:break
                time.sleep(min(a.interval,a.seconds-(time.monotonic()-start)))
        row['acquisition_complete']=True
        first,last=row['samples'][0]['ports'],row['samples'][-1]['ports'];row['deltas']={}
        for port in ['COM6','COM7']:
            row['deltas'][port]={}
            for section,names in {
              'WMP_BT':['rtp','rtp_gaps','rtp_bad','frames','blocked_frames','ble_writes'],
              'WMP_RADIO':['produced','qfull','attempt','accepted','done','api_error','timeout','late'],
              'WMP_RX':['callbacks','qfull','bad','unique','duplicate','bytes','sbc_frames'],
              'WMP_AUDIO':['queued','qfull','decoded','codec_bad','pcm_bad','concealed','late','write_errors','underrun','output_frames']}.items():
                row['deltas'][port][section]={k:last[port]['lines'][section][k]-first[port]['lines'][section][k] for k in names}
        # Acquisition success is deliberately distinct from audio acceptance.
        row['pass_all']=True
    except Exception as error:row['errors'].append(repr(error))
    finally:save(out/'receipt.json',row)
    print(json.dumps(dict(evidence=str(out),acquisition_complete=row['acquisition_complete'],audio_qualified=False,errors=row['errors'])),flush=True)
    raise SystemExit(0 if row['acquisition_complete'] else 1)
if __name__=='__main__':main()
