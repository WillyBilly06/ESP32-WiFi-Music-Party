"""Bounded Samsung A2DP activity comparison; COM7 music and both BLE tests stay live.

This deliberately changes the standby workload and cannot qualify continuous party audio.
"""
import bench_ids
import json,subprocess,time
from pathlib import Path
from board_io import ROOT,save
from sweep_live_pacing import sample
from qualify_party_long import media

PHONE=bench_ids.PHONE_SECOND
def control(action):
    subprocess.run(['adb','-s',PHONE,'shell','cmd','media_session','dispatch',action],check=True,capture_output=True,timeout=20)

def main():
    out=ROOT/'evidence'/('clock-load-'+str(time.time_ns()));out.mkdir()
    row=dict(scope=__doc__,phases=[],errors=[],continuous_qualification=False)
    print('Clock load:',out,flush=True)
    try:
        if media(PHONE)['youtube_state'] not in ['3','PLAYING']:raise RuntimeError('Samsung YouTube must initially be playing')
        for name,action in [('active_before',None),('paused','pause'),('active_after','play')]:
            if action:control(action)
            phase=dict(name=name,samples=[]);row['phases'].append(phase);start=time.monotonic()
            while True:
                snap=sample('COM6');elapsed=time.monotonic()-start
                phase['samples'].append(dict(elapsed=elapsed,receiver=snap));save(out/'receipt.json',row)
                if elapsed>=25:break
                time.sleep(min(5,25-elapsed))
            phase['phone_state']=media(PHONE)
            first=phase['samples'][0]['receiver']['lines'];last=phase['samples'][-1]['receiver']['lines']
            phase['summary']={k:last[k] for k in ['WMP_BT','WMP_BEACONS','WMP_PROFILE_STAT','WMP_AUDIO','WMP_CLOCK_HEALTH']}
            phase['delta']={s:{k:last[s][k]-first[s][k] for k in keys} for s,keys in {
                'WMP_BEACONS':['seen','matched'], 'WMP_BT':['rtp','ble_writes'],
                'WMP_AUDIO':['concealed','late','clock_pairs']}.items()}
            print(json.dumps(dict(phase=name,delta=phase['delta'],state=phase['phone_state'])),flush=True)
    except Exception as error:row['errors'].append(repr(error))
    finally:
        try:control('play')
        except Exception as error:row['errors'].append('resume '+repr(error))
    row['acquisition_complete']=len(row['phases'])==3 and not row['errors']
    save(out/'receipt.json',row);print(json.dumps(dict(evidence=str(out),errors=row['errors'])),flush=True)
if __name__=='__main__':main()
