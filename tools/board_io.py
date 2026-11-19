from local_ipc import connection as task_connection
"""Admitted-port leases and non-resetting serial I/O for the new project."""
import hashlib,json,os,secrets,socket,threading,time
from pathlib import Path
from serial.tools import list_ports
ROOT=Path(__file__).resolve().parents[1]
_save_lock=threading.RLock()
def save(path,value):
    with _save_lock:_save(path,value)

def _save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    # Independent RPC handlers may publish the same state file. Each replace
    # must own its scratch file; one fixed .tmp races on Windows sharing rules.
    tmp=path.with_name(path.name+'.'+secrets.token_hex(8)+'.tmp')
    try:
        tmp.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
        for attempt in range(6):
            try:os.replace(tmp,path);break
            except PermissionError:
                if attempt==5:raise
                time.sleep(.001*(2**attempt))
    finally:
        if tmp.exists():tmp.unlink()
def board(port):
    if port not in ['COM6','COM7']:raise ValueError('Unadmitted port')
    value=next(x for x in json.loads((ROOT/'hardware/admitted-boards-reference.json').read_text())['boards'] if x['port']==port)
    match=next((x for x in list_ports.comports() if x.device==port),None)
    if match is None or match.vid!=int(value['usb_vid'],16) or match.pid!=int(value['usb_pid'],16) or match.serial_number!=value['usb_serial']:raise RuntimeError('USB identity mismatch')
    return value
def dashboard(port,command):
    if port not in ['COM6','COM7'] or any(c in command for c in ['\r','\n']):raise ValueError('Invalid lease command')
    with task_connection(('127.0.0.1',47000+int(port[3:])),timeout=3) as s:
        s.sendall((command+'\n').encode());raw=bytearray()
        while not raw.endswith(b'\n'):
            data=s.recv(256)
            if not data or len(raw)+len(data)>2048:raise RuntimeError('Dashboard response incomplete')
            raw.extend(data)
    result=raw.decode().strip()
    if not result.startswith('OK '):raise RuntimeError(result)
    return result
def backup(value):
    proof=value['original_flash_backup'];path=Path(proof['file']);digest=hashlib.sha256(path.read_bytes()).hexdigest()
    if digest!=proof['sha256'] or path.stat().st_size!=value['flash_bytes']:raise RuntimeError('Original backup verification failed')
    return dict(file=str(path),sha256=digest,bytes=path.stat().st_size)
def open_serial(port):
    import serial
    handle=serial.Serial(port=None,baudrate=115200,timeout=.05,write_timeout=3);handle.dtr=False;handle.rts=False;handle.port=port;handle.open();handle.set_buffer_size(rx_size=131072,tx_size=65536);return handle

def require_direct_release(port):
    """Verify a live capture explicitly closed this UART; never infer from a file."""
    if port not in ['COM6','COM7']:raise ValueError('Unadmitted port')
    state=json.loads((ROOT/'hardware/active-session.json').read_text())
    if state.get('state')!='running':raise RuntimeError('Need live direct capture handoff')
    with task_connection(('127.0.0.1',state['control_port']),5) as s:
        s.sendall((json.dumps(dict(token=state['token'],action='status'))+'\n').encode())
        reply=json.loads(s.makefile('rb').readline(4097))
    target=reply.get('ports',{}).get(port,{})
    if not reply.get('ok') or not reply.get('direct_owner') or target.get('suspended') is not True or target.get('uart_open') is not False:
        raise RuntimeError('Capture has not explicitly released '+port)
    return dict(live_capture_verified=True,suspended=True,uart_open=False)
def require_idle_port():
    """No dashboard (47006/47007) or capture (48006) may own a UART. Used only
    when a board runs non-WMP firmware, so no capture can admit it first."""
    import subprocess
    state=json.loads((ROOT/'hardware/active-session.json').read_text())
    if state.get('state')=='running':raise RuntimeError('A capture session claims the UARTs')
    result=subprocess.run(['powershell.exe','-NoProfile','-Command',"@(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -in 47006,47007,48006).Count"],capture_output=True,text=True,timeout=15)
    if result.returncode or result.stdout.strip()!='0':raise RuntimeError('Dashboard or capture listener present')
    return dict(idle_verified=True,session_state=state.get('state'),listeners=0)
def exchange(handle,log,command,marker,timeout=15):
    handle.write((command+'\n').encode());handle.flush();raw=bytearray();deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        data=handle.read(4096)
        if data:raw.extend(data);log.write(data);log.flush()
        if marker in raw and raw.endswith(b'\n'):return bytes(raw)
    raise TimeoutError('Reply absent: '+marker.decode())
