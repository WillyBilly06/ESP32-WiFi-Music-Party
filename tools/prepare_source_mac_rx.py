"""Move the beacon transmitter's feedback RX to public MAC capture.

This is planned preparation with an explicit source pause, not a continuous
audio test. Both original audio and repairs remain broadcast.
"""
import argparse,concurrent.futures,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair,sample
from firmware_evidence import installed_pair,live_pair
from audio_fixture import require,check_sources
from qualify_party_long import PHONES,peer
from adb_local import shell

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',type=Path,required=True);a=p.parse_args();out=ROOT/'evidence'/('source-mac-rx-'+str(time.time_ns()));out.mkdir();(out/'prepare.py').write_bytes(Path(__file__).read_bytes())
    r=dict(controls=[],errors=[],pass_all=False,audio_qualified=False,planned_media_pause=True,pc_network_changes=False);locked=[];paused=None;print(out,flush=True)
    def control(port,text,marker):
        c=command(port,text,marker);r['controls'].append(dict(port=port,command=text,result=c));save(out/'receipt.json',r)
        if 'accepted=1' not in c['reply']:raise RuntimeError('Rejected '+port+' '+text)
    def wait(port,condition,seconds=25):
        end=time.monotonic()+seconds
        while True:
            s=sample(port);r.setdefault('observations',{})[port]=s;save(out/'receipt.json',r)
            if condition(s['lines']):return
            if time.monotonic()>end:raise TimeoutError('State did not settle '+port)
            time.sleep(.2)
    try:
        r['fixture']=require('redmagic-samsung');r['binding']=installed_pair(a.app);r['live']=live_pair(r['binding'],command)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:before=pair(pool)
        r['before']=before;leaders=[p for p,v in before.items() if v['lines']['WMP_RADIO']['mode']==2]
        if len(leaders)!=1:raise RuntimeError('Need one source')
        source=leaders[0];receiver=next(p for p in before if p!=source);r.update(source=source,receiver=receiver)
        check_sources(r['fixture'],[PHONES[peer(v['lines'])] for v in before.values()])
        if not before[source]['lines']['WMP_ANCHOR_STAT']['active']:raise RuntimeError('This diagnostic requires a fixed beacon transmitter')
        control(receiver,'FOLLOWLOCK 1','WMP_FOLLOWLOCK_REQUEST');locked.append(receiver)
        paused=PHONES[peer(before[source]['lines'])];shell(paused,['cmd','media_session','dispatch','pause'])
        wait(source,lambda s:not s['WMP_BT']['transport_started'],35)
        control(source,'STOP','WMP_STOP_REQUEST');wait(source,lambda s:not s['WMP_RADIO']['mode'])
        control(source,'FOLLOWLOCK 1','WMP_FOLLOWLOCK_REQUEST');locked.append(source)
        for text,marker in [('DIRECTRX 0','WMP_DIRECT_RX_REQUEST'),('RXWINDOW 1','WMP_RECEIVE_WINDOW_REQUEST'),('RXPAUSE 0','WMP_RX_PAUSE_REQUEST'),('NACKGATE 0','WMP_NACK_GATE')]:control(source,text,marker)
        wait(source,lambda s:not s['WMP_DIRECT_RX']['enabled'] and s['WMP_WIFI_STATE']['sniffer'] and s['WMP_RECEIVE_WINDOW']['enabled'])
        control(source,'FOLLOWLOCK 0','WMP_FOLLOWLOCK_REQUEST');locked.remove(source)
        shell(paused,['am','start','-n','com.google.android.youtube/com.google.android.youtube.HomeActivity']);shell(paused,['cmd','media_session','dispatch','play']);paused=None
        wait(source,lambda s:s['WMP_RADIO']['mode']==2 and s['WMP_AUDIO']['running'] and s['WMP_BT']['streaming'])
        control(receiver,'FOLLOWLOCK 0','WMP_FOLLOWLOCK_REQUEST');locked.remove(receiver)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:r['after']=pair(pool)
        for port,v in r['after'].items():
            s=v['lines']
            if s['WMP_REPAIR_LINK']['unicast'] or s['WMP_RADIO']['copies']!=4 or s['WMP_PHY']['selected_mbps']!=26 or s['WMP_PHY_CONFIG']['bandwidth_code']!=1:raise RuntimeError('Broadcast/MCS3 profile differs')
        r['pass_all']=True
    except Exception as e:r['errors'].append(repr(e))
    finally:
        for port in locked:
            try:control(port,'FOLLOWLOCK 0','WMP_FOLLOWLOCK_REQUEST')
            except Exception as e:r['errors'].append('unlock '+port+' '+repr(e))
        if paused:
            try:shell(paused,['cmd','media_session','dispatch','play'])
            except Exception as e:r['errors'].append('resume '+repr(e))
        save(out/'receipt.json',r)
    print(dict(pass_all=r['pass_all'],errors=r['errors']),flush=True)
    if not r['pass_all']:raise SystemExit(1)

if __name__=='__main__':main()
