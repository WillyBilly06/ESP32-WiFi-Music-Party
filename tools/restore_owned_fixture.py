"""Restore admitted REDMAGIC Classic, owned BLE clients and existing music."""
import argparse,concurrent.futures,re,secrets,time,xml.etree.ElementTree as ET
from adb_local import shell,SERIAL
from sweep_live_pacing import sample,pair
from network_clock import failure as network_failure

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--classic-refresh',action='store_true');parser.add_argument('--source-port',choices=['COM6','COM7'],default='COM7');parser.add_argument('--connections-only',action='store_true',help='Restore Classic/BLE without starting music; allows a controlled PHY-profile handoff');args=parser.parse_args()
    target={'COM6':'6C:C8:40:42:BF:66','COM7':'08:B6:1F:B8:66:26'}[args.source_port]
    for port in ['COM6','COM7']:
        s=sample(port)['lines']
        receive_path=s['WMP_DIRECT_RX']['enabled'] or (s['WMP_WIFI_STATE']['sniffer'] and s['WMP_RECEIVE_WINDOW']['enabled'])
        issue=network_failure(s)
        # A zero-join passive listener locks its beacon clock only once source
        # audio flows; the broadcast receipt requires starting the phone first.
        if issue=='hardware-beacon clock unavailable or stale' and not s['WMP_NETWORK']['infrastructure'] and s['WMP_BEACONS']['matched']:issue=None
        # Pure broadcast: phones connect before any board is source, so no clock exists yet.
        # Clock-fit quality does not gate a phone connection in pure broadcast.
        if issue in ('hardware-beacon clock unavailable or stale','hardware-beacon clock mapping outside bounds') and s.get('WMP_PURE_BROADCAST',{}).get('enabled') and not s['WMP_WIFI_MODE']['ap'] and not s['WMP_NETWORK']['infrastructure']:issue=None
        if issue or not receive_path:
            raise RuntimeError('Provision the admitted Wi-Fi/audio path before phone restoration: '+port)
    suffix={'COM6':'BF64','COM7':'6624'}[args.source_port];names=('WMP-Control-'+suffix,'WMP-Sink-'+suffix)
    bt=sample(args.source_port)['lines']['WMP_BT']
    if not (bt['phone'] and bt['rate']):
        if args.classic_refresh:
            probe=secrets.token_hex(4)
            shell(SERIAL,['am','start','-f','0x10008000','-n','org.wmp.partyprobe/.PartyProbe','--es','mode','pair','--es','target',target,'--es','run',probe])
            deadline=time.monotonic()+25
            while time.monotonic()<deadline:
                result=shell(SERIAL,['run-as','org.wmp.partyprobe','cat','files/pair-'+probe+'.log'])
                if 'AFTER type=' in result:break
                if 'ERROR ' in result:raise RuntimeError('Owned public Classic SDP probe failed')
                time.sleep(.5)
            else:raise TimeoutError('Public Classic SDP probe did not finish')
        shell(SERIAL,['am','start','-a','android.settings.BLUETOOTH_SETTINGS'])
        shell(SERIAL,['uiautomator','dump','/sdcard/wmp-restore-bt.xml'])
        tree=ET.fromstring(shell(SERIAL,['cat','/sdcard/wmp-restore-bt.xml']))
        if any(any('connect to '+name in n.attrib.get('text','') for name in ['WMP-Control-6624','WMP-Sink-6624','WMP-Control-BF64','WMP-Sink-BF64']) for n in tree.iter('node')):
            buttons=[n for n in tree.iter('node') if n.attrib.get('text')=='OK']
            if len(buttons)!=1:raise RuntimeError('Ambiguous own-device error dialog')
            x1,y1,x2,y2=map(int,re.findall(r'\d+',buttons[0].attrib['bounds']));shell(SERIAL,['input','tap',str((x1+x2)//2),str((y1+y2)//2)])
            shell(SERIAL,['uiautomator','dump','/sdcard/wmp-restore-bt.xml']);tree=ET.fromstring(shell(SERIAL,['cat','/sdcard/wmp-restore-bt.xml']))
        rows=[n for n in tree.iter('node') if n.attrib.get('text') in names]
        if len(rows)!=1:raise RuntimeError('No unique admitted '+args.source_port+' saved-device row')
        x1,y1,x2,y2=map(int,re.findall(r'\d+',rows[0].attrib['bounds']))
        shell(SERIAL,['input','tap',str((x1+x2)//2),str((y1+y2)//2)])
        for _ in range(80):  # the phone can take ~11 s to bring A2DP up after the tap
            bt=sample(args.source_port)['lines']['WMP_BT']
            if bt['phone'] and bt['rate']:break
            time.sleep(.25)
        else:raise TimeoutError(args.source_port+' Classic did not connect; no UI action replayed')
    run,run2=secrets.token_hex(4),secrets.token_hex(4)
    shell(SERIAL,['am','start','-f','0x10008000','-n','org.wmp.partyprobe/.PartyProbe','--es','run',run,'--es','target','08:B6:1F:B8:66:26','--es','run2',run2,'--es','target2','6C:C8:40:42:BF:66','--ei','seconds','7200'])
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for _ in range(25):
            states=pair(pool)
            if all(v['lines']['WMP_BT']['ble'] for v in states.values()):break
            time.sleep(.2)
        else:raise TimeoutError('Both admitted BLE clients did not connect')
        if args.connections_only:
            print(dict(Classic_source=args.source_port,BLE_both=True,music_control_sent=False,runs=[run,run2],full_dual_source_qualified=False),flush=True)
            return
        shell(SERIAL,['am','start','-n','com.google.android.youtube/com.google.android.youtube.HomeActivity'])
        shell(SERIAL,['cmd','media_session','dispatch','play'])
        for _ in range(25):
            states=pair(pool)
            if states[args.source_port]['lines']['WMP_BT']['streaming'] and all(v['lines']['WMP_AUDIO']['running'] for v in states.values()):break
            time.sleep(.2)
        else:raise TimeoutError('Existing music did not reach both outputs')
    print(dict(Classic_source=args.source_port,BLE_both=True,audio_running_both=True,runs=[run,run2],full_dual_source_qualified=False),flush=True)

if __name__=='__main__':main()
