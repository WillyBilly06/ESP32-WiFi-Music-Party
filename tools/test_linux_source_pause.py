"""Planned standby-source pauses; verify actual AVDTP and live HTTP reuse."""
import sys,time,json
from board_io import ROOT,save
from qualify_party_long import linux_control,linux_status
from sweep_live_pacing import sample
from audio_fixture import require

def main():
    require('redmagic-linux');out=ROOT/'evidence'/('linux-source-pause-'+str(time.time_ns()));out.mkdir();row=dict(cycles=[],errors=[],audio_qualified=False);print(out,flush=True);paused=False
    try:
        for index in range(2):
            cycle=dict(index=index,before=sample('COM6'));row['cycles'].append(cycle)
            cycle['pause']=linux_control('pause');paused=True;deadline=time.monotonic()+12
            while True:
                state=sample('COM6');cycle['paused_board']=state
                if not state['lines']['WMP_BT']['transport_started']:break
                if time.monotonic()>deadline:raise RuntimeError('Actual A2DP transport did not suspend')
                time.sleep(.3)
            time.sleep(1);cycle['play']=linux_control('play');paused=False;deadline=time.monotonic()+15
            while True:
                state=sample('COM6');cycle['playing_board']=state
                if state['lines']['WMP_BT']['transport_started'] and state['lines']['WMP_BT']['streaming']:break
                if time.monotonic()>deadline:raise RuntimeError('Actual A2DP did not resume')
                time.sleep(.3)
            playback=linux_status()['playback'];cycle['playback']=playback
            if playback.get('errors') or playback.get('finished') or not playback.get('ready') or playback.get('http_reopens')!=0:raise RuntimeError('Source failed planned pause')
            save(out/'receipt.json',row)
        row['complete']=True
    except Exception as error:row['errors'].append(repr(error))
    finally:
        if paused:
            try:row['resume']=linux_control('play')
            except Exception as error:row['errors'].append('resume '+repr(error))
        save(out/'receipt.json',row);print(dict(evidence=str(out),complete=row.get('complete',False),errors=row['errors']),flush=True)
    if row['errors']:raise SystemExit(1)

if __name__=='__main__':main()
