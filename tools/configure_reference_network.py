"""Provision the two-board reference without printing or logging its key."""
import argparse,json,subprocess,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import sample

def configure(port,disable_bt=False,raw=False,rate=54):
    config=json.loads((ROOT/'hardware/reference-ap.json').read_text(encoding='utf-8-sig'))
    if config['state']!='active' or not config['diagnostic_only']:raise RuntimeError('No active test-owned AP configuration')
    check=subprocess.run([r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'tools/reference_hotspot.ps1'),'-Action','Inspect'],capture_output=True,text=True,check=True)
    if json.loads(check.stdout)['State']!='On':raise RuntimeError('Host AP is not currently on')
    out=ROOT/'evidence'/('network-config-'+str(time.time_ns())+'-'+port);out.mkdir()
    receipt=dict(port=port,diagnostic_only=True,audio_qualified=False,pass_all=False,controls=[])
    try:
        initial=sample(port);receipt['before']=initial
        if initial['lines']['WMP_RADIO']['mode'] or initial['lines']['WMP_BT']['phone'] or initial['lines']['WMP_BT']['ble']:
            raise RuntimeError('Provision only before media and GATT connections')
        if initial['lines']['WMP_NETWORK']['infrastructure']:raise RuntimeError('Already provisioned; do not replay network setup')
        if disable_bt and (initial['lines']['WMP_BT']['working'] or initial['lines']['WMP_BT']['requested']):
            receipt['controls'].append(command(port,'BT OFF','WMP_BT_REQUEST'))
            for _ in range(20):
                current=sample(port)['lines']['WMP_BT']
                if not current['working'] and not current['requested']:break
                time.sleep(.1)
            else:raise RuntimeError('Bluetooth did not stop before cold rate selection')
        # Configure a different rate only in the firmware's cold, idle AP
        # lifecycle. NETWORK then applies it to STA before starting Wi-Fi.
        if 'WMP_RATE_SETUP' not in initial['lines']:raise RuntimeError('Firmware lacks prestart rate lifecycle telemetry')
        result=command(port,'RATE '+str(rate),'WMP_RATE');receipt['controls'].append(result)
        if 'status=0' not in result['reply']:raise RuntimeError('Cold rate selection rejected')
        # The command is sent through the existing authenticated local RPC.
        # It is never passed on a process command line or stored in receipts.
        reply=command(port,'NETWORK '+config['ssid']+' '+config['passphrase'],'WMP_NETWORK_REQUEST');receipt['controls'].append(reply)
        if 'accepted=1' not in reply['reply']:raise RuntimeError('Network request rejected')
        for _ in range(30):
            time.sleep(.5);state=sample(port);receipt['after']=state
            s=state['lines'];save(out/'receipt.json',receipt)
            if s['WMP_NETWORK']['status']:raise RuntimeError('Driver rejected network configuration')
            if s['WMP_ASSOC']['connected'] and s['WMP_ASSOC_EVENTS']['connected'] and s['WMP_SOURCE_CLOCK']['valid'] and s['WMP_SOURCE_CLOCK']['interface_sta']:break
        else:raise TimeoutError('No established association and STA hardware clock')
        if s['WMP_WIFI_MODE']['ap']:raise RuntimeError('Reference must remain a station')
        reply=command(port,'ETHMODE '+('0' if raw else '1'),'WMP_ETH_REQUEST');receipt['controls'].append(reply)
        if 'accepted=1' not in reply['reply']:raise RuntimeError('Native data path rejected')
        if disable_bt:
            receipt['controls'].append(command(port,'BT OFF','WMP_BT_REQUEST'))
        for _ in range(20):
            state=sample(port);receipt['after']=state;s=state['lines']
            if s['WMP_ETH']['enabled']==(0 if raw else 1) and s['WMP_SOURCE_CLOCK']['valid'] and (not disable_bt or not s['WMP_BT']['working']):break
            time.sleep(.25)
        else:raise RuntimeError('Data path or Bluetooth state did not apply')
        receipt['pass_all']=True
    except Exception as error:receipt['error']=repr(error);raise
    finally:save(out/'receipt.json',receipt)
    return dict(port=port,evidence=str(out),connected=True,channel=s['WMP_WIFI_MODE']['channel'],rssi=s['WMP_NETWORK'].get('rssi'),native_auto=s['WMP_ETH_RATE'].get('native_auto'),raw=raw)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--ports',nargs='+',choices=['COM6','COM7'],default=['COM6','COM7']);parser.add_argument('--bt-off',action='store_true');parser.add_argument('--raw',action='store_true');parser.add_argument('--rate',type=int,choices=[6,9,12,18,24,36,48,54,65],default=54);a=parser.parse_args()
    for port in a.ports:print(json.dumps(configure(port,a.bt_off,a.raw,a.rate)),flush=True)
