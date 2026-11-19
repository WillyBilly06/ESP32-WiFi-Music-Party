"""Finite unchanged live audio assessment through the owned persistent UARTs."""
import argparse,concurrent.futures,json,time
from pathlib import Path
from board_io import ROOT,save
from sweep_live_pacing import pair
from analyze_audio import assess
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seconds',type=int,default=300);a=p.parse_args()
    if not 30<=a.seconds<=14400:p.error('30..14400 actual seconds')
    out=ROOT/'evidence'/('qualified-window-'+str(time.time_ns()));out.mkdir();(out/'capture.py').write_bytes(Path(__file__).read_bytes())
    row=dict(samples=[],acquisition_complete=False,audio_qualified=False,errors=[],requested_seconds=a.seconds);print('Window:',out,flush=True)
    try:
        start=time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            while True:
                ports=pair(pool);elapsed=time.monotonic()-start;row['samples'].append(dict(elapsed=elapsed,ports=ports));save(out/'receipt.json',row)
                if elapsed>=a.seconds:row['acquisition_complete']=True;break
                if len(row['samples'])>=2:
                    initial=row['samples'][0]['ports'];faults=[]
                    sources=[p for p,v in ports.items() if v['lines']['WMP_RADIO']['mode']==2]
                    if len(sources)!=1:faults.append('ambiguous source')
                    else:
                        b=ports[sources[0]]['lines']['WMP_BT']
                        if not all(b.get(k) for k in ['phone','streaming','ble']):faults.append('required live workload disappeared')
                    for port,v in ports.items():
                        old=initial[port]['lines'];now=v['lines']
                        for section,keys in {'WMP_AUDIO':['concealed','late','underrun','codec_bad','pcm_bad','write_errors','qfull'],'WMP_BT':['blocked_frames','rtp_gaps','rtp_bad'],'WMP_RX':['qfull']}.items():
                            for k in keys:
                                if now[section][k]!=old[section][k]:faults.append(port+' '+section+'.'+k)
                        if now['WMP_COPY']['generation']!=old['WMP_COPY']['generation']:faults.append(port+' stream generation changed')
                        if 'WMP_DMA' in now and now['WMP_DMA']['queue_overflow']!=old['WMP_DMA']['queue_overflow']:faults.append(port+' DMA free-buffer queue overflow')
                    if faults:row['terminal_reason']='fault_detected';row['detected_faults']=faults;break
                time.sleep(min(5,a.seconds-elapsed))
        row['assessment']=assess(row)
    except Exception as e:row['errors'].append(repr(e))
    save(out/'receipt.json',row);print(json.dumps(dict(evidence=str(out),assessment=row.get('assessment'),errors=row['errors'])),flush=True)
if __name__=='__main__':main()
