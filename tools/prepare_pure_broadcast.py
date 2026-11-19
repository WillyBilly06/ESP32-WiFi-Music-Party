"""Prepare a pure broadcast pair: no SoftAP, no association, no ANCHOR (ESP-NOW style without ESP-NOW).

Both boards become unconnected stations on one fixed channel and exchange only
raw broadcast action frames. Whichever phone streams first makes its board the
source; the source stamps its own clock into ANNOUNCE frames and followers fit
their playback clock to those stamps.
"""
import argparse,concurrent.futures,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair,sample
from firmware_evidence import installed_pair,live_pair

PORTS=['COM6','COM7']
SETTINGS=[('RXWINDOW 1','WMP_RECEIVE_WINDOW_REQUEST'),('RXPAUSE 0','WMP_RX_PAUSE_REQUEST'),('TXWINDOW 6','WMP_TX_WINDOW_REQUEST'),
          ('SCHEDULE 24000 0','WMP_SCHEDULE'),('TXDITHER 6000','WMP_TX_DITHER_REQUEST'),('NACKDELAY 40000','WMP_NACK_DELAY'),
          ('NACKGATE 0','WMP_NACK_GATE'),('LATENCY 300','WMP_LATENCY_REQUEST'),('REPAIRCAST 0','WMP_REPAIR_CAST')]

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',type=Path,required=True);p.add_argument('--channel',type=int,default=6);a=p.parse_args()
    out=ROOT/'evidence'/('pure-broadcast-'+str(time.time_ns()));out.mkdir();(out/'prepare.py').write_bytes(Path(__file__).read_bytes())
    r=dict(channel=a.channel,controls=[],errors=[],pass_all=False,audio_qualified=False,pc_network_changes=False,join_commands=0,softap_commands=0);print(out,flush=True)
    def control(port,text,marker):
        v=command(port,text,marker);r['controls'].append(dict(port=port,command=text,result=v));save(out/'receipt.json',r)
        if 'accepted=1' not in v['reply'] and 'status=0' not in v['reply']:raise RuntimeError('Rejected '+port+' '+text)
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
        for port,v in before.items():
            s=v['lines']
            if s['WMP_NETWORK']['infrastructure'] or s['WMP_RADIO']['mode'] or s['WMP_DIRECT_RX']['enabled']:raise RuntimeError('Need fresh disabled configuration '+port)
            if not s.get('WMP_PURE_BROADCAST',{}).get('enabled'):raise RuntimeError('Firmware without pure broadcast '+port)
        for port in PORTS:
            control(port,'BT OFF','WMP_BT_REQUEST');wait(port,lambda s:not any(s['WMP_BT'][k] for k in ['working','requested','phone','ble']))
        for port in PORTS:
            if sample(port)['lines']['WMP_WIFI_MODE']['ap']:control(port,'RXMODE 0','WMP_RXMODE');wait(port,lambda s:not s['WMP_WIFI_MODE']['ap'])
            lines=sample(port)['lines'];actual=lines['WMP_WIFI_CHANNEL']['primary']
            if not lines.get('WMP_AUTOCHANNEL',{}).get('enabled') and actual!=a.channel:
                # Synchronize the cached channel while BT is stopped.
                control(port,'CHANNEL '+str(actual),'WMP_CHANNEL');control(port,'CHANNEL '+str(a.channel),'WMP_CHANNEL')
        for port in PORTS:
            for text,marker in SETTINGS:control(port,text,marker)
        for port in PORTS:
            control(port,'BT ON','WMP_BT_REQUEST');wait(port,lambda s:s['WMP_BT']['working'] and s['WMP_BT']['controller']==2);control(port,'A2DPCOEX 1','WMP_A2DP_COEX')
        with concurrent.futures.ThreadPoolExecutor(2) as pool:r['after']=pair(pool)
        for port,v in r['after'].items():
            s=v['lines']
            if s['WMP_ASSOC']['requested'] or s['WMP_ASSOC']['connected'] or s['WMP_WIFI_MODE']['ap'] or s['WMP_ANCHOR_STAT']['requested']:raise RuntimeError('SoftAP or association present '+port)
            if s['WMP_RATE_SETUP']['configured_mbps']!=26 or s['WMP_PHY_CONFIG']['bandwidth_code']!=1 or s['WMP_DIRECT_RX']['enabled'] or not s['WMP_WIFI_STATE']['sniffer']:raise RuntimeError('MCS3/HT20 raw setup differs '+port)
            auto=s.get('WMP_AUTOCHANNEL',{}).get('enabled')
            if (s['WMP_WIFI_CHANNEL']['primary'] not in (1,6,11)) if auto else (s['WMP_WIFI_CHANNEL']['primary']!=a.channel):raise RuntimeError('Actual radio channel differs '+port)
        r['pass_all']=True;r['next']='Start a phone on either board; the first streaming board becomes source. Followers lock to its ANNOUNCE clock.'
    except Exception as e:r['errors'].append(repr(e))
    save(out/'receipt.json',r);print(dict(pass_all=r['pass_all'],errors=r['errors'],join_commands=0,softap_commands=0),flush=True)
    if not r['pass_all']:raise SystemExit(1)

if __name__=='__main__':main()
