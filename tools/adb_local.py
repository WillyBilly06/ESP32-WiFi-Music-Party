"""Use the existing authenticated ADB server with an explicit local source port.

Restricted to the admitted REDMAGIC/Samsung phones and task-owned operations.
"""
import bench_ids
import json,shlex
from pathlib import Path
from local_ipc import connection

SERIAL=bench_ids.PHONE_MAIN
SAMSUNG=bench_ids.PHONE_SECOND

def require_testing(serial):
    if serial not in (SERIAL,SAMSUNG):raise ValueError('Phone is not admitted for this task')
    if serial==SAMSUNG:
        admission=Path(__file__).resolve().parents[1]/'hardware/admitted-phones.json'
        records=[p for p in json.loads(admission.read_text())['phones'] if p['serial']==serial]
        if len(records)!=1 or not records[0].get('testing_enabled',False):raise PermissionError('Samsung is reserved by the user; no ADB access')

def _read(sock,count):
    data=bytearray()
    while len(data)<count:
        part=sock.recv(count-len(data))
        if not part:raise EOFError('ADB server closed the response')
        data.extend(part)
    return bytes(data)

def _service(sock,name):
    data=name.encode();sock.sendall(f'{len(data):04x}'.encode()+data)
    result=_read(sock,4)
    if result==b'FAIL':raise RuntimeError('ADB: '+_read(sock,int(_read(sock,4),16)).decode(errors='replace'))
    if result!=b'OKAY':raise RuntimeError('Invalid ADB server status')

def shell(serial,args,timeout=20):
    require_testing(serial)
    args=list(args)
    radio_control=serial==SERIAL and args in (['svc','bluetooth','enable'],['svc','bluetooth','disable'])
    allowed=(radio_control or args[:1]==['dumpsys'] or args[:3]==['cmd','media_session','dispatch'] or args[:3]==['cmd','media_session','volume'] or
             args==['getprop','ro.product.model'] or args==['pm','path','org.wmp.partyprobe'] or
             args in (['cmd','bluetooth_manager','help'],['cmd','media_router','help']) or
             args[:2]==['uiautomator','dump'] or args[:2]==['input','tap'] or args[:2]==['input','swipe'] or args==['input','keyevent','KEYCODE_BACK'] or
             args[:2]==['am','start'] and ('org.wmp.partyprobe/.PartyProbe' in args or 'android.settings.BLUETOOTH_SETTINGS' in args or 'com.google.android.youtube/com.google.android.youtube.HomeActivity' in args) or
             args[:2]==['am','force-stop'] and args[2:]==['org.wmp.partyprobe'] or
             args[:2]==['run-as','org.wmp.partyprobe'] and args[2:3]==['cat'] or
             args[:1]==['cat'] and len(args)==2 and args[1].startswith('/sdcard/wmp-'))
    if not allowed or any('://' in value for value in args):raise ValueError('Operation outside the owned test fixture')
    with connection(('127.0.0.1',5037),timeout) as sock:
        _service(sock,'host:transport:'+serial);_service(sock,'shell:'+shlex.join(args))
        data=bytearray()
        while True:
            part=sock.recv(65536)
            if not part:break
            data.extend(part)
        return data.decode('utf-8',errors='replace')

def screenshot(serial,path):
    require_testing(serial)
    target=Path(path).resolve();target.relative_to(Path(__file__).resolve().parents[1]/'evidence')
    with connection(('127.0.0.1',5037),20) as sock:
        _service(sock,'host:transport:'+serial);_service(sock,'exec:screencap -p');raw=bytearray()
        while True:
            part=sock.recv(65536)
            if not part:break
            raw.extend(part)
    if not raw.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('ADB screenshot did not return PNG')
    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw);return target
