"""Flash a built candidate with plain esptool write-flash (per-image device hash
verification), for boards where install_app.py's full readback is unreliable.

User-approved 2026-11-17 for COM6. Requires a running direct-owner persistent
capture: the port is released for flashing and resumed afterwards, and the
resume itself re-verifies live INFO build/MAC identity. NVS is never written.
"""
import argparse,json,re,subprocess,time
from pathlib import Path
from board_io import ROOT,board,save
ESPTOOL=[r'C:\Users\ngocv\.espressif\python_env\idf6.1_py3.12_env\Scripts\python.exe','-m','esptool']

def session(action,port):
    r=subprocess.run(['python',str(ROOT/'tools/session_command.py'),'--'+action+'-port','--port',port],capture_output=True,text=True,timeout=60)
    if r.returncode:raise RuntimeError(action+' failed: '+r.stdout+r.stderr)
    return json.loads(r.stdout.strip().splitlines()[-1])

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',type=Path,required=True);p.add_argument('--port',choices=['COM6','COM7'],required=True);p.add_argument('--baud',type=int,choices=[115200,460800,921600],default=921600);a=p.parse_args()
    m=json.loads((a.app/'app.json').read_text());identity=board(a.port)
    if not m.get('pass_all') or m['dependencies']!=json.loads((ROOT/'dependencies.lock.json').read_text()):raise RuntimeError('Unqualified build or changed dependency pins')
    out=ROOT/'evidence'/('esptool-flash-'+str(time.time_ns())+'-'+a.port);out.mkdir();(out/'flasher.py').write_bytes(Path(__file__).read_bytes())
    r=dict(port=a.port,build_id=m['build_id'],manifest=str((a.app/'app.json').resolve()),esptool_verified=False,pass_all=False,errors=[],baud=a.baud,
           images=[dict(offset=x['offset'],bytes=x['bytes'],sha256=x['sha256']) for x in m['artifacts']],not_performed='full readback, outside-extent and NVS audits')
    save(out/'receipt.json',r);released=False
    try:
        r['release']=session('release',a.port);released=True
        args=[*ESPTOOL,'--chip','esp32','-p',a.port,'-b',str(a.baud),'--before','default-reset','--after','hard-reset','write-flash','--flash-mode','dio','--flash-freq','40m','--flash-size','4MB']
        for x in sorted(m['artifacts'],key=lambda x:x['offset']):args+=[hex(x['offset']),x['path']]
        result=subprocess.run(args,capture_output=True,text=True,timeout=300);(out/'esptool.log').write_text(result.stdout+result.stderr,encoding='utf-8')
        mac=re.search(r'MAC:\s*([0-9a-f:]{17})',result.stdout,re.I);r['actual_mac']=mac.group(1).lower() if mac else ''
        verified=result.stdout.count('Hash of data verified')
        if result.returncode or verified!=len(m['artifacts']):raise RuntimeError('esptool write/verify failed (%d verified)'%verified)
        if r['actual_mac']!=identity['base_mac'].lower():raise RuntimeError('Chip MAC differs from admitted board')
        r['esptool_verified']=True
        save(ROOT/'hardware'/('deployed-'+a.port+'.json'),dict(port=a.port,build_id=m['build_id'],base_mac=identity['base_mac'].lower(),boot_verified=True,receipt=str(out/'receipt.json'),radio_qualified=False,audio_qualified=False,state='installed'))
        time.sleep(2);r['resume']=session('resume',a.port);released=False  # resume re-checks live INFO build/MAC
        r['pass_all']=True
    except Exception as e:
        r['errors'].append(repr(e));r['esptool_verified']=False
        save(ROOT/'hardware'/('deployed-'+a.port+'.json'),dict(port=a.port,build_id=m['build_id'],boot_verified=False,state='incomplete',receipt=str(out/'receipt.json')))
    save(out/'receipt.json',r);print(json.dumps(dict(pass_all=r['pass_all'],receipt=str(out/'receipt.json'),errors=r['errors'],port_still_released=released)))
    raise SystemExit(0 if r['pass_all'] else 1)
if __name__=='__main__':main()
