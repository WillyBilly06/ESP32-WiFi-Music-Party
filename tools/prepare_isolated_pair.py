"""Provision only the admitted ESP32s as AP clock anchor and station.

No Windows hotspot, Internet sharing, adapter or routing operation is used.
Credentials stay in hardware/isolated-network.json and bounded local UART RPC;
they are never printed or placed on a process command line.
"""
import argparse,concurrent.futures,json,secrets,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair,sample
from firmware_evidence import installed_pair,live_pair

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',type=Path,required=True);p.add_argument('--anchor',choices=['COM6','COM7'],default='COM7');a=p.parse_args()
    station='COM6' if a.anchor=='COM7' else 'COM7';out=ROOT/'evidence'/('isolated-pair-'+str(time.time_ns()));out.mkdir();(out/'prepare.py').write_bytes(Path(__file__).read_bytes())
    r=dict(anchor=a.anchor,station=station,controls=[],errors=[],pass_all=False,audio_qualified=False,pc_network_changes=False,limitations=['Two-board associated test network; not100-receiver capacity proof','Runtime provisioning; persistent standalone boot not yet qualified']);print(out,flush=True)
    def control(port,text,marker,private=False):
        result=command(port,text,marker);r['controls'].append(dict(port=port,command='private network provisioning' if private else text,result=result));save(out/'receipt.json',r)
        if 'accepted=1' not in result['reply'] and 'status=0' not in result['reply']:raise RuntimeError('Rejected '+port+' '+('network provisioning' if private else text))
    def wait(port,condition,seconds=25):
        deadline=time.monotonic()+seconds
        while True:
            s=sample(port);r.setdefault('observations',{})[port]=s;save(out/'receipt.json',r)
            if condition(s['lines']):return s
            if time.monotonic()>=deadline:raise TimeoutError('ESP32 state did not settle '+port)
            time.sleep(.25)
    try:
        r['binding']=installed_pair(a.app);r['live']=live_pair(r['binding'],command)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:before=pair(pool)
        r['before']=before
        for port,v in before.items():
            s=v['lines']
            if s['WMP_NETWORK']['infrastructure'] or s['WMP_RADIO']['mode'] or s['WMP_DIRECT_RX']['enabled'] or s['WMP_ASSOC']['requested']:raise RuntimeError('Need fresh disabled network; configuration is not replayed '+port)
        for port in before:
            control(port,'BT OFF','WMP_BT_REQUEST')
            wait(port,lambda s:not any(s['WMP_BT'][k] for k in ['working','requested','phone','ble']))
            control(port,'RATE 26','WMP_RATE')
        private=dict(ssid='WMP-Local-'+secrets.token_hex(3),passphrase=secrets.token_hex(10),anchor=a.anchor,station=station,state='configuring',pc_network_changes=False)
        save(ROOT/'hardware/isolated-network.json',private)
        control(a.anchor,'ANCHOR '+private['ssid']+' '+private['passphrase'],'WMP_ANCHOR_REQUEST',True)
        wait(a.anchor,lambda s:s['WMP_ANCHOR_STAT']['requested'] and s['WMP_ANCHOR_STAT']['active'] and s['WMP_WIFI_MODE']['ap'] and not s['WMP_NETWORK']['status'])
        control(station,'NETWORK '+private['ssid']+' '+private['passphrase'],'WMP_NETWORK_REQUEST',True)
        wait(station,lambda s:s['WMP_ASSOC']['connected'] and s['WMP_SOURCE_CLOCK']['valid'] and s['WMP_SOURCE_CLOCK']['interface_sta'])
        for port in before:
            for text,marker in [('DIRECTRX 1','WMP_DIRECT_RX_REQUEST'),('RXPAUSE 1','WMP_RX_PAUSE_REQUEST'),('TXWINDOW 6','WMP_TX_WINDOW_REQUEST'),('SCHEDULE 24000 0','WMP_SCHEDULE'),('TXDITHER 6000','WMP_TX_DITHER_REQUEST'),('NACKDELAY 40000','WMP_NACK_DELAY'),('LATENCY 200','WMP_LATENCY_REQUEST')]:control(port,text,marker)
            wait(port,lambda s:s['WMP_DIRECT_RX']['enabled'] and s['WMP_RECEIVE_WINDOW']['enabled'] and s['WMP_WIFI_STATE']['sniffer']==0)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:after=pair(pool)
        r['after']=after
        for port,v in after.items():
            s=v['lines'];clock=s['WMP_SOURCE_CLOCK']
            if s['WMP_NETWORK']['status'] or s['WMP_PHY_CONFIG']['bandwidth_code']!=1 or s['WMP_PHY']['selected_mbps']!=26:raise RuntimeError('Network/MCS3/HT20 readback failed '+port)
            if not clock['valid'] or clock['span_us']>1000 or clock['age_us']>1000000 or not s['WMP_CLOCK_DOMAIN']['shared']:raise RuntimeError('Hardware clock not qualified '+port)
            if port==a.anchor:
                if not s['WMP_WIFI_MODE']['ap'] or not s['WMP_ANCHOR_STAT']['active'] or s['WMP_ANCHOR_STAT']['peers']!=1 or s['WMP_ASSOC']['connected']:raise RuntimeError('Anchor/peer count or honest association readback failed')
            elif s['WMP_WIFI_MODE']['ap'] or not s['WMP_ASSOC']['connected']:raise RuntimeError('Station did not join isolated anchor')
        private['state']='active';save(ROOT/'hardware/isolated-network.json',private)
        for port in before:
            control(port,'BT ON','WMP_BT_REQUEST')
            wait(port,lambda s:s['WMP_BT']['working'] and s['WMP_BT']['controller']==2)
            control(port,'A2DPCOEX 1','WMP_A2DP_COEX')
        r['pass_all']=True
    except Exception as e:r['errors'].append(repr(e))
    save(out/'receipt.json',r);print(dict(pass_all=r['pass_all'],errors=r['errors'],pc_network_changes=False),flush=True)
    if not r['pass_all']:raise SystemExit(1)

if __name__=='__main__':main()
