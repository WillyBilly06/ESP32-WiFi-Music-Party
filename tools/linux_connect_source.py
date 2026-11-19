"""Connect the identity-admitted Linux adapter to COM6 using normal BlueZ APIs.

Run inside the owned Linux guest. No adapter reset, bond deletion or request
replay after uncertain connection outcome. Music starts separately.
"""
import argparse,json,re,subprocess,time

TARGET='6C:C8:40:42:BF:66'
ADAPTER='/org/bluez/hci0'  # resolved by MAC in main(); the hci index is not stable
DEVICE=ADAPTER+'/dev_'+TARGET.replace(':','_')
SINK='bluez_output.6C_C8_40_42_BF_66.1'

def call(path,method,*args,timeout=12):
    return subprocess.run(['gdbus','call','--system','--dest','org.bluez','--object-path',path,'--method',method,*args],capture_output=True,text=True,timeout=timeout)

def connected():
    r=call(DEVICE,'org.freedesktop.DBus.Properties.Get','org.bluez.Device1','Connected')
    return r.returncode==0 and '<true>' in r.stdout

def device_present():
    return call(DEVICE,'org.freedesktop.DBus.Properties.Get',
                'org.bluez.Device1','Connected').returncode==0

def sink_ready():
    return SINK in subprocess.check_output(['pactl','list','sinks','short'],text=True,timeout=5)

def locate(name):
    """Return the hciN whose BD Address is the admitted adapter; explicit names are MAC-checked."""
    found={}
    for block in re.split(r'(?m)^(?=hci\d+:)',subprocess.check_output(['hciconfig'],text=True,timeout=5)):
        m=re.match(r'(hci\d+):',block);a=re.search(r'BD Address:\s*([0-9A-Fa-f:]{17})',block)
        if m and a:found[m.group(1)]=a.group(1).upper()
    if name=='auto':name=next((k for k,v in found.items() if v=='E8:47:3A:EC:51:9A'),None)
    if not name or not re.fullmatch(r'hci\d+',name) or found.get(name)!='E8:47:3A:EC:51:9A':raise RuntimeError('Admitted adapter not present at '+str(name))
    return name

def main():
    global ADAPTER,DEVICE,TARGET,SINK
    parser=argparse.ArgumentParser();parser.add_argument('--adapter',default='auto',help='hciN (MAC-checked) or auto to locate E8:47:3A:EC:51:9A');parser.add_argument('--port',choices=['COM6','COM7'],default='COM6');parser.add_argument('--direct',action='store_true',help='Use documented experimental Adapter1.ConnectDevice; requires BlueZ --experimental');a=parser.parse_args()
    TARGET={'COM6':'6C:C8:40:42:BF:66','COM7':'08:B6:1F:B8:66:26'}[a.port]
    SINK='bluez_output.'+TARGET.replace(':','_')+'.1'
    if a.adapter!='auto' and not re.fullmatch(r'hci\d+',a.adapter):raise RuntimeError('Invalid adapter interface')
    report=dict(adapter='E8:47:3A:EC:51:9A',target=TARGET,steps=[],audio_qualified=False)
    try:
        a.adapter=locate(a.adapter);report['interface']=a.adapter
        ADAPTER='/org/bluez/'+a.adapter;DEVICE=ADAPTER+'/dev_'+TARGET.replace(':','_')
        info=subprocess.check_output(['hciconfig',a.adapter],text=True,timeout=5)
        if not re.search(r'BD Address:\s*E8:47:3A:EC:51:9A',info):raise RuntimeError('Adapter identity changed')
        if a.direct and not connected():
            r=call(ADAPTER,'org.bluez.Adapter1.ConnectDevice',"{'Address': <'"+TARGET+"'>}",timeout=30)
            report['steps'].append(dict(direct_connect_status=r.returncode,response=r.stdout.strip(),error=r.stderr.strip(),discovery_bypassed=True))
            if r.returncode:raise RuntimeError('Direct physical admission failed; no automatic retry')
        if not a.direct and not connected():
            scanner=None
            try:
                if not device_present():
                    # Keep our discovery client alive until BlueZ publishes Device1.
                    # Adapter1.ConnectDevice is experimental and may be absent.
                    scanner=subprocess.Popen(
                        ['bluetoothctl','--timeout','25','scan','bredr'],
                        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    deadline=time.monotonic()+20
                    while time.monotonic()<deadline and not device_present():
                        if scanner.poll() is not None:
                            raise RuntimeError('Owned discovery client ended before target admission')
                        time.sleep(.3)
                    if not device_present():raise RuntimeError('Admitted target not discovered')
                    report['steps'].append(dict(standard_discovery=True,admitted_target_found=True))
                if not connected():
                    r=call(DEVICE,'org.bluez.Device1.Connect',timeout=30)
                    report['steps'].append(dict(connect_status=r.returncode,response=r.stdout.strip(),error=r.stderr.strip()))
                    if r.returncode:raise RuntimeError('Physical admission failed; no automatic retry')
            finally:
                if scanner is not None and scanner.poll() is None:
                    scanner.terminate()
                    scanner.wait(timeout=3)
        deadline=time.monotonic()+10;profile_requested=False
        while time.monotonic()<deadline:
            if connected() and sink_ready():
                report.update(connected=True,sink_ready=True,sink=SINK);break
            if connected() and not profile_requested and time.monotonic()>deadline-7:
                r=call(DEVICE,'org.bluez.Device1.ConnectProfile','0000110b-0000-1000-8000-00805f9b34fb')
                report['steps'].append(dict(profile_status=r.returncode,response=r.stdout.strip(),error=r.stderr.strip()));profile_requested=True
                if r.returncode and 'InProgress' not in r.stderr:raise RuntimeError('A2DP profile admission failed')
            time.sleep(.3)
        else:raise RuntimeError('No resolved Bluetooth audio sink')
    except subprocess.TimeoutExpired as error:
        report.update(error=str(error),outcome_unknown=True)
    except Exception as error:report['error']=str(error)
    print(json.dumps(report),flush=True)
    if not report.get('sink_ready'):raise SystemExit(1)

if __name__=='__main__':main()
