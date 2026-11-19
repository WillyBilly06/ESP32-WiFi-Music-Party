"""Bounded live carrier/rate/pacing comparison; never an hour qualification.

Both admitted phones must keep real YouTube A2DP and BLE transactions running.
The packet's original SBC, generation and presentation timeline are unchanged.
"""
import bench_ids
import argparse,concurrent.futures,json,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair
from qualify_party_long import media

CASES=[(1,24,32000,1),(1,12,32000,1),(1,6,32000,1),
       (0,6,32000,1),(0,6,24000,1),(0,6,32000,0)]
def configure(path,rate,spacing,paced):
    responses=[command('COM7',f'RAWPATH {path}','WMP_RAW_PATH_REQUEST'),command('COM7',f'RATE {rate}','WMP_RATE'),
               command('COM7',f'SCHEDULE {spacing} {paced}','WMP_SCHEDULE')]
    for response,expected in zip(responses,['accepted=1','status=0','accepted=1']):
        if expected not in response['reply']:raise RuntimeError('Firmware rejected comparison setting: '+response['reply'])
    return responses
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seconds',type=int,default=45);a=p.parse_args()
    if not 30<=a.seconds<=180:p.error('30..180 seconds per case')
    out=ROOT/'evidence'/('radio-delivery-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes())
    row=dict(scope=__doc__,cases=[],errors=[],acquisition_complete=False,audio_qualified=False)
    print('Comparison:',out,flush=True)
    original=None
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            # Read COM7's live path/rate/schedule so every exit restores them (MCS7/HT20 65 Mbps is the required profile).
            s=pair(pool)['COM7']['lines'];original=row['original']=dict(path=s['WMP_RAW_PATH']['action_frames'],rate=s['WMP_PHY']['selected_mbps'],spacing=s['WMP_COPY']['spacing'],paced=s['WMP_COPY']['paced']);save(out/'receipt.json',row)
            for path,rate,spacing,paced in CASES:
                states=[media(s) for s in [bench_ids.PHONE_MAIN,bench_ids.PHONE_SECOND]]
                if any(s['youtube_state'] not in ['PLAYING','3'] for s in states):raise RuntimeError('YouTube not playing on both admitted phones')
                case=dict(path=path,rate=rate,spacing=spacing,paced=paced,phone_states=states,controls=configure(path,rate,spacing,paced),samples=[]);row['cases'].append(case)
                time.sleep(5);start=time.monotonic()
                while True:
                    ports=pair(pool);elapsed=time.monotonic()-start;case['samples'].append(dict(elapsed=elapsed,ports=ports));save(out/'receipt.json',row)
                    if any(not all(v['lines']['WMP_BT'][k] for k in ['phone','streaming','ble']) for v in ports.values()):raise RuntimeError('Required phone A2DP/BLE workload disappeared')
                    if elapsed>=a.seconds:break
                    time.sleep(min(5,a.seconds-elapsed))
                first,last=case['samples'][0]['ports'],case['samples'][-1]['ports'];d={}
                for port in first:
                    d[port]={s:{k:last[port]['lines'][s][k]-first[port]['lines'][s][k] for k in keys} for s,keys in {
                      'WMP_AUDIO':['concealed','late','underrun','write_errors','clock_pairs','output_frames'],
                      'WMP_BT':['rtp_gaps','rtp_bad','blocked_frames','ble_writes','frames'],
                      'WMP_RX':['qfull','unique','bad'],'WMP_RADIO':['qfull','late','timeout','api_error'],
                      'WMP_CLOCK_HEALTH':['expirations'],'WMP_DMA':['queue_overflow']}.items()}
                case['delta']=d;case['acquisition_complete']=True
                source=d['COM7'];case['source_clean']=not any(source[s][k] for s,keys in {
                  'WMP_AUDIO':['concealed','late','underrun','write_errors'],'WMP_BT':['rtp_gaps','rtp_bad','blocked_frames'],'WMP_DMA':['queue_overflow']}.items() for k in keys)
                print(json.dumps(dict(config=[path,rate,spacing,paced],source_clean=case['source_clean'],receiver=d['COM6'],source_tx=source['WMP_RADIO'])),flush=True)
                save(out/'receipt.json',row)
                if not case['source_clean']:raise RuntimeError('Source playback/input fault; abort comparison')
            selected=min(row['cases'],key=lambda c:sum(c['delta']['COM6']['WMP_AUDIO'][k] for k in ['concealed','late','underrun']))
            # Report the best case only; the live profile is restored below, never replaced by the comparison result.
            row['selected']={k:selected[k] for k in ['path','rate','spacing','paced']};row['acquisition_complete']=True
    except Exception as error:row['errors'].append(repr(error))
    if original is not None:
        try:row['restore']=configure(**original)
        except Exception as restore:row['errors'].append('restore '+repr(restore))
    save(out/'receipt.json',row);print(json.dumps(dict(evidence=str(out),selected=row.get('selected'),errors=row['errors'])),flush=True)
if __name__=='__main__':main()
