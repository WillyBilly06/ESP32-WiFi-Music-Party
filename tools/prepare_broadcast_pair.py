"""Prepare a beacon transmitter and passive MAC listener; zero Wi-Fi joins.

The transmitter uses its hardware beacon generator for time packets. The
receiver gets no SSID/key and never calls NETWORK/ASSOC. Phone playback starts
after this setup; its temporary follower lock must then be cleared.
"""
import argparse,concurrent.futures,secrets,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair,sample
from firmware_evidence import installed_pair,live_pair

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',type=Path,required=True);p.add_argument('--source',choices=['COM6','COM7'],default='COM7');a=p.parse_args();receiver='COM6' if a.source=='COM7' else 'COM7'
    out=ROOT/'evidence'/('broadcast-pair-'+str(time.time_ns()));out.mkdir();(out/'prepare.py').write_bytes(Path(__file__).read_bytes());r=dict(source=a.source,receiver=receiver,controls=[],errors=[],pass_all=False,audio_qualified=False,pc_network_changes=False,receiver_join_commands=0,temporary_follower_lock=False);print(out,flush=True)
    def control(port,text,marker,private=False):
        v=command(port,text,marker);r['controls'].append(dict(port=port,command='private beacon-generator configuration' if private else text,result=v));save(out/'receipt.json',r)
        if 'accepted=1' not in v['reply'] and 'status=0' not in v['reply']:raise RuntimeError('Rejected '+port+' '+('beacon configuration' if private else text))
    def wait(port,condition,seconds=25):
        end=time.monotonic()+seconds
        while True:
            v=sample(port);r.setdefault('observations',{})[port]=v;save(out/'receipt.json',r)
            if condition(v['lines']):return
            if time.monotonic()>end:raise TimeoutError('State did not settle '+port)
            time.sleep(.2)
    try:
        r['binding']=installed_pair(a.app);r['live']=live_pair(r['binding'],command)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:before=pair(pool)
        r['before']=before
        for port,v in before.items():
            s=v['lines']
            if s['WMP_NETWORK']['infrastructure'] or s['WMP_RADIO']['mode'] or s['WMP_DIRECT_RX']['enabled']:raise RuntimeError('Need fresh disabled configuration '+port)
        for port in before:
            control(port,'BT OFF','WMP_BT_REQUEST');wait(port,lambda s:not any(s['WMP_BT'][k] for k in ['working','requested','phone','ble']))
        private=dict(source=a.source,ssid='WMP-Clock-'+secrets.token_hex(3),passphrase=secrets.token_hex(10),purpose='beacon generator only; receiver does not join')
        save(ROOT/'hardware/broadcast-clock.json',private)
        control(a.source,'ANCHOR '+private['ssid']+' '+private['passphrase'],'WMP_ANCHOR_REQUEST',True)
        wait(a.source,lambda s:s['WMP_ANCHOR_STAT']['active'] and s['WMP_SOURCE_CLOCK']['valid'])
        control(receiver,'RXMODE 0','WMP_RXMODE');wait(receiver,lambda s:not s['WMP_WIFI_MODE']['ap'])
        actual=sample(receiver)['lines']['WMP_WIFI_CHANNEL'];desired=sample(a.source)['lines']['WMP_WIFI_CHANNEL']['primary']
        if actual['primary']!=desired:
            # Firmware's cached channel could survive a restart that reset
            # the hardware to1. Synchronize that cache while BT is stopped.
            control(receiver,'CHANNEL '+str(actual['primary']),'WMP_CHANNEL')
            control(receiver,'CHANNEL '+str(desired),'WMP_CHANNEL')
        for port in before:
            for text,marker in [('RXWINDOW 1','WMP_RECEIVE_WINDOW_REQUEST'),('RXPAUSE 0','WMP_RX_PAUSE_REQUEST'),('TXWINDOW 6','WMP_TX_WINDOW_REQUEST'),('SCHEDULE 24000 0','WMP_SCHEDULE'),('TXDITHER 6000','WMP_TX_DITHER_REQUEST'),('NACKDELAY 40000','WMP_NACK_DELAY'),('NACKGATE 0','WMP_NACK_GATE'),('LATENCY 200','WMP_LATENCY_REQUEST'),('REPAIRCAST 0','WMP_REPAIR_CAST')]:control(port,text,marker)
        control(receiver,'FOLLOWLOCK 1','WMP_FOLLOWLOCK_REQUEST');r['temporary_follower_lock']=True
        for port in before:
            control(port,'BT ON','WMP_BT_REQUEST');wait(port,lambda s:s['WMP_BT']['working'] and s['WMP_BT']['controller']==2);control(port,'A2DPCOEX 1','WMP_A2DP_COEX')
        with concurrent.futures.ThreadPoolExecutor(2) as pool:r['after']=pair(pool)
        for port,v in r['after'].items():
            s=v['lines']
            if s['WMP_ASSOC']['requested'] or s['WMP_ASSOC']['connected'] or s['WMP_ANCHOR_STAT']['peers']:raise RuntimeError('Unexpected Wi-Fi association '+port)
            if s['WMP_RATE_SETUP']['configured_mbps']!=26 or s['WMP_PHY_CONFIG']['bandwidth_code']!=1 or s['WMP_DIRECT_RX']['enabled'] or not s['WMP_WIFI_STATE']['sniffer']:raise RuntimeError('MAC/MCS3/HT20 setup differs '+port)
            if s['WMP_WIFI_CHANNEL']['primary']!=desired:raise RuntimeError('Actual radio channel differs '+port)
        r['pass_all']=True;r['next']='Start the source phone; wait for passive clock/playback, clear receiver FOLLOWLOCK0, then restore standby phone/BLE.'
    except Exception as e:r['errors'].append(repr(e))
    if r['errors'] and r['temporary_follower_lock']:
        try:control(receiver,'FOLLOWLOCK 0','WMP_FOLLOWLOCK_REQUEST');r['temporary_follower_lock']=False
        except Exception as e:r['errors'].append('unlock '+repr(e))
    save(out/'receipt.json',r);print(dict(pass_all=r['pass_all'],errors=r['errors'],receiver_join_commands=0,temporary_follower_lock=r['temporary_follower_lock']),flush=True)
    if not r['pass_all']:raise SystemExit(1)

if __name__=='__main__':main()
