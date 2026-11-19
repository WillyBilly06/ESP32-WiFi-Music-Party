"""Install one candidate with per-board records, exact readback and NVS audits."""
import argparse,hashlib,io,json,secrets,sys,time
from pathlib import Path
from board_io import ROOT,board,backup,dashboard,open_serial,exchange,save,require_direct_release,require_idle_port
from flash_preserve import verified_cache,preserve
from firmware_evidence import image_erase_end
def sha(raw):return hashlib.sha256(raw).hexdigest()
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',type=Path,required=True);p.add_argument('--port',choices=['COM6','COM7'],required=True);p.add_argument('--rom-only',action='store_true');p.add_argument('--direct-owner',action='store_true',help='Require an explicit live direct-capture UART release instead of a dashboard lease');p.add_argument('--idle-port',action='store_true',help='Board runs non-WMP firmware: require no dashboard/capture listener and a stopped session; the exclusive serial open is the ownership proof');p.add_argument('--baud',type=int,choices=[115200,230400,460800,921600],default=921600);a=p.parse_args()
    proof=json.loads((a.app/'app.json').read_text(encoding='utf-8'))
    if not proof.get('pass_all') or proof['profile']!='btstack-custom-wifi-audio-candidate':raise RuntimeError('Unqualified build or wrong profile')
    if proof['dependencies']!=json.loads((ROOT/'dependencies.lock.json').read_text()):raise RuntimeError('Build dependency pins changed')
    value=board(a.port);original=backup(value);images=[]
    for image in proof['artifacts']:
        raw=Path(image['path']).read_bytes()
        if len(raw)!=image['bytes'] or sha(raw)!=image['sha256'] or image['offset'] not in [0x1000,0x8000,0x10000]:raise RuntimeError('Artifact identity/extent failed')
        padded=raw+b'\xff'*((-len(raw))%4096)
        image_erase_end(image['offset'],len(raw))
        images.append(dict(**image,padded=padded))
    images.sort(key=lambda x:x['offset'])
    for x,y in zip(images,images[1:]):
        if x['offset']+len(x['padded'])>y['offset']:raise RuntimeError('Overlapping image erases')
    out=ROOT/'evidence'/('install-'+str(time.time_ns())+'-'+a.port);out.mkdir();token=secrets.token_hex(16);path=out/(a.port+'.raw');path.touch()
    row=dict(port=a.port,build_id=proof['build_id'],manifest=str((a.app/'app.json').resolve()),original_backup=original,pass_all=False,errors=[],flash_writes=False,rom_only=a.rom_only,baud=a.baud,lease=token,started_ns=time.time_ns())
    (out/'installer.py').write_bytes(Path(__file__).read_bytes())
    deployed=ROOT/'hardware'/('deployed-'+a.port+'.json');cache=verified_cache(deployed,value);loader=None;leased=False;log=path.open('ab');recent=bytearray()
    def persist(stage):row['stage']=stage;save(out/'receipt.json',row)
    def reconnect(baud):
        nonlocal loader
        time.sleep(.3);handle.reset_input_buffer()
        fresh=ESP32ROM(handle,115200);fresh.connect(attempts=3)
        if ':'.join(f'{x:02x}' for x in fresh.read_mac())!=value['base_mac']:raise RuntimeError('Identity changed during read recovery')
        attach_flash(fresh);fresh.flash_set_parameters(value['flash_bytes'])
        if not a.rom_only:fresh=fresh.run_stub();fresh.flash_set_parameters(value['flash_bytes'])
        fresh.change_baud(baud);loader=fresh
        row.setdefault('read_recoveries',[]).append(dict(baud=baud,at_ns=time.time_ns()));save(out/'receipt.json',row)
    def read_checked(offset,n,label):
        raw=bytearray();partial=out/(label+'.bin')
        with partial.open('wb') as f:
            for at in range(0,n,16384):
                size=min(16384,n-at);part=None;failures=row.setdefault('read_retries',[])
                # The host UART occasionally drops bytes while receiving long
                # stub read streams at 921600 (esptool reports Corrupt data or
                # Digest mismatch). Each chunk is re-read until its bytes match
                # the device's own MD5 of that exact flash range.
                for attempt in range(6):
                    try:
                        candidate=bytes(loader.read_flash(offset+at,size))
                        if len(candidate)!=size:raise RuntimeError('Short checked read')
                        if hashlib.md5(candidate).hexdigest()!=loader.flash_md5sum(offset+at,size).lower():raise RuntimeError('Chunk digest mismatch')
                        part=candidate;break
                    except Exception as error:
                        failures.append(dict(label=label,offset=offset+at,bytes=size,attempt=attempt,error=str(error)[:200]));save(out/'receipt.json',row)
                        # A dropped byte leaves the stub blocked inside the old
                        # read stream, so a plain retry only times out. Return
                        # to the ROM loader, re-admit identity, reload the stub
                        # and continue at the cable's sustainable read rate.
                        reconnect(460800 if attempt>=1 else a.baud)
                if part is None:raise RuntimeError('Checked read failed after retries at '+hex(offset+at))
                raw.extend(part);f.write(part);f.flush()
                if at%65536==0:row['read_progress']=dict(offset=offset,stream_bytes_read=len(raw),total_bytes=n,label=label);save(out/'receipt.json',row)
        expected=loader.flash_md5sum(offset,n).lower()
        if hashlib.md5(raw).hexdigest()!=expected:raise RuntimeError('Read/device digest mismatch')
        return bytes(raw)
    app=next(x for x in images if x['offset']==0x10000);end=app['offset']+len(app['padded'])
    def outside():return [dict(offset=offset,bytes=n,md5=loader.flash_md5sum(offset,n).lower()) for offset,n in [(0,0x1000),(0xf000,0x1000),(end,value['flash_bytes']-end)]]
    print('Install:',out,flush=True);persist('admission')
    try:
        sys.path.insert(0,str(Path.home()/r'.espressif\python_env\idf6.1_py3.12_env\Lib\site-packages'))
        from esptool.targets import ESP32ROM
        from esptool.cmds import attach_flash
        from esptool.bin_image import ESP32FirmwareImage
        image=ESP32FirmwareImage(io.BytesIO(Path(app['path']).read_bytes()))
        if image.stored_digest!=image.calc_digest or image.checksum!=image.calculate_checksum():raise RuntimeError('Application image checksum invalid')
        if a.direct_owner and a.idle_port:raise ValueError('Choose one ownership mode')
        if a.idle_port:row['idle_handoff']=require_idle_port()
        elif a.direct_owner:row['direct_handoff']=require_direct_release(a.port)
        else:
            dashboard(a.port,'LEASE '+token);leased=True;dashboard(a.port,'FOLLOWLEASE '+token+' '+str(path))
        handle=open_serial(a.port);handle.timeout=1;loader=ESP32ROM(handle,115200);read=handle.read
        def record_read(n=1):
            data=read(n);recent.extend(data)
            if len(recent)>65536:del recent[:-65536]
            return data
        handle.read=record_read;loader.connect(attempts=3)
        actual=':'.join(f'{x:02x}' for x in loader.read_mac());row['actual_mac']=actual
        if loader.IS_STUB or loader.sync_stub_detected or actual!=value['base_mac'] or b'DOWNLOAD_BOOT' not in recent:raise RuntimeError('Fresh ROM/USB/MAC admission failed')
        if loader.get_flash_encryption_enabled() or loader.get_secure_boot_enabled():raise RuntimeError('Protected chip not admitted')
        attach_flash(loader);flash_id=loader.flash_id();row['flash_id']=flash_id
        if 1<<((flash_id>>16)&255)!=value['flash_bytes']:raise RuntimeError('Physical flash capacity changed')
        loader.flash_set_parameters(value['flash_bytes'])
        if not a.rom_only:loader=loader.run_stub();loader.flash_set_parameters(value['flash_bytes'])
        loader.change_baud(a.baud)
        row['outside_before']=outside();persist('preserve-nvs')
        before_nvs=read_checked(0x9000,0x6000,'nvs-before');row['nvs_before']=sha(before_nvs)
        save(deployed,dict(port=a.port,build_id=proof['build_id'],boot_verified=False,installation_receipt=str(out/'receipt.json'),state='installing'))
        row['images']=[]
        for image in images:
            offset=image['offset'];persist('preserve-'+hex(offset));prior,origin=preserve(loader,offset,len(image['padded']),'prior-'+hex(offset),out,cache,read_checked)
            entry={k:v for k,v in image.items() if k!='padded'};entry['prior_sha256']=sha(prior);entry['prior_origin']=origin;row['images'].append(entry)
            if prior!=image['padded']:
                row['flash_writes']=True;persist('write-'+hex(offset))
                # A dropped UART byte aborts the write stream; recover like reads:
                # re-enter the ROM loader and rewrite this image from its start.
                # Exact readback below still decides success.
                for attempt in range(4):
                    try:
                        blocks=loader.flash_begin(len(image['padded']),offset)
                        for index in range(blocks):loader.flash_block(image['padded'][index*loader.FLASH_WRITE_SIZE:(index+1)*loader.FLASH_WRITE_SIZE],index)
                        break
                    except Exception as error:
                        row.setdefault('write_retries',[]).append(dict(offset=offset,attempt=attempt,error=str(error)[:200]));save(out/'receipt.json',row)
                        if attempt==3:raise
                        reconnect(460800)
            persist('readback-'+hex(offset));actual=read_checked(offset,len(image['padded']),'readback-'+hex(offset))
            if actual!=image['padded']:raise RuntimeError('Exact image readback failed')
            entry['readback_sha256']=sha(actual)
        row['outside_after']=outside()
        if row['outside_before']!=row['outside_after']:raise RuntimeError('Flash changed outside admitted extents')
        row['flash_verified']=True;row['boots']=[]
        for index in range(2):
            persist('boot-'+str(index));loader._port.baudrate=115200;loader._port.timeout=.05;loader.hard_reset()
            raw=bytearray();deadline=time.monotonic()+25;marker=('WMP_READY build='+proof['build_id']).encode()
            while time.monotonic()<deadline and marker not in raw:
                data=loader._port.read(4096);raw.extend(data);log.write(data);log.flush()
            (out/('boot-'+str(index)+'.raw')).write_bytes(raw)
            if marker not in raw:raise TimeoutError('Application did not become ready')
            response=exchange(loader._port,log,'INFO',b' audio_qualified=0',5)
            if ('build='+proof['build_id']).encode() not in response or ('base_mac='+value['base_mac']).encode() not in response:raise RuntimeError('Runtime identity mismatch')
            nonce=secrets.token_hex(4);exchange(loader._port,log,'PING '+nonce,('nonce='+nonce).encode(),5);row['boots'].append(dict(identity_verified=True,nonce=nonce))
        persist('post-runtime-audit');loader._port.timeout=1;loader=ESP32ROM(loader._port,115200);loader.connect(attempts=3)
        if ':'.join(f'{x:02x}' for x in loader.read_mac())!=value['base_mac']:raise RuntimeError('Post-boot MAC changed')
        attach_flash(loader);loader.flash_set_parameters(value['flash_bytes'])
        if not a.rom_only:loader=loader.run_stub();loader.flash_set_parameters(value['flash_bytes'])
        row['outside_runtime']=outside()
        if row['outside_runtime']!=row['outside_before']:raise RuntimeError('Runtime wrote outside NVS/image extents')
        row['nvs_after']=sha(read_checked(0x9000,0x6000,'nvs-after'));row['pass_all']=True
        save(deployed,dict(port=a.port,build_id=proof['build_id'],base_mac=value['base_mac'],boot_verified=True,receipt=str(out/'receipt.json'),radio_qualified=False,audio_qualified=False,state='installed'))
    except Exception as error:
        row['failed_stage']=row.get('stage');row['errors'].append(repr(error));(out/'failed-stage.raw').write_bytes(recent)
        save(deployed,dict(port=a.port,build_id=proof['build_id'],boot_verified=False,state='incomplete',receipt=str(out/'receipt.json')))
    finally:
        if loader:
            try:loader._port.baudrate=115200;loader.hard_reset()
            except Exception as error:row['errors'].append('reset: '+repr(error));row['pass_all']=False
            loader._port.close()
        log.close()
        if leased:
            try:row['dashboard_restored']=dashboard(a.port,'UNLEASE '+token)
            except Exception as error:row['errors'].append('lease: '+repr(error));row['pass_all']=False
        row['finished_ns']=time.time_ns();persist('terminal')
    print(json.dumps(dict(pass_all=row['pass_all'],receipt=str(out/'receipt.json'),errors=row['errors'])),flush=True)
    raise SystemExit(0 if row['pass_all'] else 1)
if __name__=='__main__':main()
