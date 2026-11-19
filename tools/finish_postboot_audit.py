"""Read-only completion of a terminal install's failed post-runtime ROM audit.

Requires prior exact image readback and two identity-checked boots. Never writes
flash or replaces the failed receipt. A changed image aborts this audit.
"""
import argparse,hashlib,json,secrets,shutil,sys,time
from pathlib import Path
from board_io import ROOT,board,backup,dashboard,open_serial,save


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--parent',type=Path,required=True);a=p.parse_args()
    parent=a.parent.resolve();old=json.loads(parent.read_text());port=old['port'];value=board(port)
    if port!='COM7':raise RuntimeError('This stub audit is restricted to admitted COM7')
    if old.get('stage')!='terminal' or old.get('failed_stage')!='post-runtime-audit' or not old.get('flash_verified') or len(old.get('boots',[]))!=2 or not all(v.get('identity_verified') for v in old['boots']):raise RuntimeError('Parent does not establish the required earlier stages')
    backup(value);out=ROOT/'evidence'/('postboot-audit-'+str(time.time_ns())+'-'+port);out.mkdir();token=secrets.token_hex(16)
    row=dict(old,parent_receipt=str(parent),parent_sha256=hashlib.sha256(parent.read_bytes()).hexdigest(),parent_errors=old['errors'],errors=[],pass_all=False,flash_writes=False,read_only_completion=True,started_ns=time.time_ns())
    row.pop('failed_stage',None);loader=None;leased=False
    try:
        sys.path.insert(0,str(Path.home()/r'.espressif\python_env\idf6.1_py3.12_env\Lib\site-packages'))
        from esptool.targets import ESP32ROM
        from esptool.cmds import attach_flash
        dashboard(port,'LEASE '+token);leased=True
        handle=open_serial(port);handle.timeout=1;loader=ESP32ROM(handle,115200);loader.connect(attempts=3)
        if loader.IS_STUB or loader.sync_stub_detected or ':'.join(f'{x:02x}' for x in loader.read_mac())!=value['base_mac']:raise RuntimeError('Fresh ROM identity failed')
        attach_flash(loader)
        if 1<<((loader.flash_id()>>16)&255)!=value['flash_bytes']:raise RuntimeError('Flash capacity mismatch')
        loader.flash_set_parameters(value['flash_bytes']);loader=loader.run_stub();loader.flash_set_parameters(value['flash_bytes']);loader.change_baud(921600)
        row['fresh_image_md5']=[]
        for image in row['images']:
            path=parent.parent/('readback-'+hex(image['offset'])+'.bin');raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=image['readback_sha256']:raise RuntimeError('Prior exact readback changed')
            actual=loader.flash_md5sum(image['offset'],len(raw)).lower()
            if actual!=hashlib.md5(raw).hexdigest():raise RuntimeError('Current image differs from verified image')
            row['fresh_image_md5'].append(dict(offset=image['offset'],bytes=len(raw),md5=actual))
            shutil.copyfile(path,out/path.name)
        row['outside_runtime']=[dict(x,md5=loader.flash_md5sum(x['offset'],x['bytes']).lower()) for x in old['outside_before']]
        if row['outside_runtime']!=old['outside_before']:raise RuntimeError('Runtime changed protected extents')
        raw=bytes(loader.read_flash(0x9000,0x6000));(out/'nvs-after.bin').write_bytes(raw)
        if hashlib.md5(raw).hexdigest()!=loader.flash_md5sum(0x9000,len(raw)).lower():raise RuntimeError('NVS read digest mismatch')
        row['nvs_after']=hashlib.sha256(raw).hexdigest();row['pass_all']=True
    except Exception as error:row['errors'].append(repr(error))
    finally:
        if loader:
            try:loader._port.baudrate=115200;loader.hard_reset()
            except Exception as error:row['errors'].append('reset '+repr(error));row['pass_all']=False
            loader._port.close()
        if leased:
            try:dashboard(port,'UNLEASE '+token)
            except Exception as error:row['errors'].append('lease '+repr(error));row['pass_all']=False
        row['stage']='terminal';row['finished_ns']=time.time_ns();save(out/'receipt.json',row)
        if row['pass_all']:
            save(ROOT/'hardware'/('deployed-'+port+'.json'),dict(port=port,build_id=row['build_id'],base_mac=value['base_mac'],boot_verified=True,receipt=str(out/'receipt.json'),parent_receipt=str(parent),state='installed',radio_qualified=False,audio_qualified=False))
    print(json.dumps(dict(evidence=str(out),pass_all=row['pass_all'],flash_writes=False,errors=row['errors'])),flush=True)
    raise SystemExit(0 if row['pass_all'] else 1)


if __name__=='__main__':main()
