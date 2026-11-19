"""Verify raw broadcast playback and hardware-beacon clocking without joining."""
import argparse,concurrent.futures,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair,sample
from firmware_evidence import installed_pair,live_pair

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',type=Path,required=True);p.add_argument('--receiver',choices=['COM6','COM7'],required=True);a=p.parse_args();source='COM7' if a.receiver=='COM6' else 'COM6'
    out=ROOT/'evidence'/('passive-receiver-'+str(time.time_ns()));out.mkdir();(out/'prepare.py').write_bytes(Path(__file__).read_bytes());r=dict(source=source,receiver=a.receiver,controls=[],errors=[],pass_all=False,audio_qualified=False,pc_network_changes=False);print(out,flush=True)
    def control(text,marker):
        v=command(a.receiver,text,marker);r['controls'].append(dict(command=text,result=v));save(out/'receipt.json',r)
        if 'accepted=1' not in v['reply'] and 'status=0' not in v['reply']:raise RuntimeError('Rejected '+text)
    def wait(condition,seconds=30):
        end=time.monotonic()+seconds
        while True:
            v=sample(a.receiver);r['last_receiver']=v;save(out/'receipt.json',r)
            if condition(v['lines']):return v
            if time.monotonic()>end:raise TimeoutError('Passive receiver did not settle')
            time.sleep(.2)
    try:
        r['binding']=installed_pair(a.app);r['live']=live_pair(r['binding'],command)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:r['before']=pair(pool)
        first=r['before'];s=first[source]['lines'];v=first[a.receiver]['lines']
        if s['WMP_RADIO']['mode']!=2 or not s['WMP_WIFI_MODE']['ap'] or v['WMP_RADIO']['mode']!=0:raise RuntimeError('Need an active AP beacon transmitter and follower')
        control('BT OFF','WMP_BT_REQUEST');wait(lambda s:not any(s['WMP_BT'][k] for k in ['working','requested','phone','ble']))
        if v['WMP_DIRECT_RX']['enabled']:control('DIRECTRX 0','WMP_DIRECT_RX_REQUEST')
        if v['WMP_NETWORK']['infrastructure']:control('NETWORK OFF','WMP_NETWORK_REQUEST')
        wait(lambda s:not s['WMP_NETWORK']['infrastructure'] and not s['WMP_RADIO']['mode'])
        control('RXMODE 0','WMP_RXMODE');wait(lambda s:not s['WMP_WIFI_MODE']['ap'])
        # The station-oriented action callback does not deliver the long
        # unassociated audio frames. Use the MAC promiscuous callback for
        # both raw audio and latched beacon timestamps, with PM registration.
        control('RATE 26','WMP_RATE');control('RXWINDOW 1','WMP_RECEIVE_WINDOW_REQUEST');control('RXPAUSE 0','WMP_RX_PAUSE_REQUEST')
        control('SCHEDULE 24000 0','WMP_SCHEDULE');control('TXDITHER 6000','WMP_TX_DITHER_REQUEST')
        wait(lambda s:s['WMP_AUDIO']['clock_valid'] and s['WMP_AUDIO']['running'] and s['WMP_AUDIO']['clock_pairs']>=8,45)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:r['after']=pair(pool)
        after=r['after'];v=after[a.receiver]['lines'];s=after[source]['lines']
        if v['WMP_ASSOC']['requested'] or v['WMP_ASSOC']['connected'] or v['WMP_NETWORK']['infrastructure']:raise RuntimeError('Receiver is still associated')
        if s['WMP_ANCHOR_STAT']['peers']:raise RuntimeError('An associated client remains at transmitter')
        if v['WMP_PHY']['selected_mbps']!=26 or v['WMP_PHY_CONFIG']['bandwidth_code']!=1:raise RuntimeError('MCS3/HT20 changed')
        old=first[source]['lines']
        if s['WMP_SESSION']['generation']!=old['WMP_SESSION']['generation'] or s['WMP_DMA']['start_us32']!=old['WMP_DMA']['start_us32']:raise RuntimeError('Source session/DAC changed')
        control('BT ON','WMP_BT_REQUEST');wait(lambda s:s['WMP_BT']['working'] and s['WMP_BT']['controller']==2);control('A2DPCOEX 1','WMP_A2DP_COEX')
        r['pass_all']=True;r['limits']=['Only one passive receiver physically tested','Bluetooth receiver input and BLE must be restored before full qualification','Hardware-beacon mapping accuracy and100-device behavior remain unqualified']
    except Exception as e:r['errors'].append(repr(e))
    save(out/'receipt.json',r);print(dict(pass_all=r['pass_all'],errors=r['errors']),flush=True)
    if not r['pass_all']:raise SystemExit(1)

if __name__=='__main__':main()
