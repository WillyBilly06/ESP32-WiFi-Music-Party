"""Verify project dependencies/backup references and current USB identity.

No firmware write, serial open, reset or claim of fresh ROM MAC admission.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]
def git(path,*args):return subprocess.check_output(['git','-C',str(path),*args],text=True).strip()
def main():
    out=ROOT/'evidence'/('bootstrap-'+str(time.time_ns()));out.mkdir(parents=True)
    result=dict(pass_all=False,flash_writes=False,serial_open=False,radio_qualified=False,errors=[])
    try:
        lock=json.loads((ROOT/'dependencies.lock.json').read_text(encoding='utf-8'))
        for name in ['btstack','esp_idf']:
            dependency=lock[name];path=Path(dependency['path'])
            if not path.is_absolute():path=ROOT/path
            revision=git(path,'rev-parse','HEAD')
            if revision!=dependency['revision'] or git(path,'status','--porcelain','--untracked-files=no'):
                raise RuntimeError('Dependency revision/working tree mismatch: '+name)
            result[name]=dict(path=str(path),revision=revision)
        from serial.tools import list_ports
        observed={p.device:p for p in list_ports.comports()}
        admitted=json.loads((ROOT/'hardware/admitted-boards-reference.json').read_text(encoding='utf-8'))
        result['boards']=[]
        for board in admitted['boards']:
            port=observed.get(board['port'])
            if port is None or port.vid!=int(board['usb_vid'],16) or port.pid!=int(board['usb_pid'],16) or port.serial_number!=board['usb_serial']:
                raise RuntimeError('USB admission failed: '+board['port'])
            backup=board['original_flash_backup'];file=Path(backup['file'])
            if not file.is_absolute():raise RuntimeError('Backup reference must be absolute')
            digest=hashlib.sha256(file.read_bytes()).hexdigest()
            if digest!=backup['sha256'] or file.stat().st_size!=board['flash_bytes']:
                raise RuntimeError('Original backup identity/size/hash failed: '+board['port'])
            result['boards'].append(dict(port=board['port'],usb_serial=port.serial_number,backup=str(file),sha256=digest,rom_mac_rechecked=False))
        result['pass_all']=True
    except Exception as error:result['errors'].append(repr(error))
    (out/'receipt.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(evidence=str(out),pass_all=result['pass_all'],errors=result['errors'])))
    raise SystemExit(0 if result['pass_all'] else 1)
if __name__=='__main__':main()
