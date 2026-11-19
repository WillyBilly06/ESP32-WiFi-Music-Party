"""Reuse prior exact readback only after a fresh matching device MD5.

NVS is never cached. New image readback always remains a complete fresh read.
"""
import hashlib,json
from pathlib import Path
def verified_cache(deployment,board):
    result={}
    try:
        old=json.loads(Path(deployment).read_text())
        if not old.get('boot_verified') or old.get('port')!=board['port']:return result
        receipt_path=Path(old['receipt']);receipt=json.loads(receipt_path.read_text())
        if not receipt.get('pass_all') or receipt.get('port')!=board['port'] or receipt.get('actual_mac')!=board['base_mac']:return result
        for item in receipt.get('images',[]):
            offset=item['offset']
            if offset not in [0x1000,0x8000,0x10000]:continue
            path=receipt_path.parent/('readback-'+hex(offset)+'.bin');raw=path.read_bytes()
            if raw and len(raw)%4096==0 and hashlib.sha256(raw).hexdigest()==item.get('readback_sha256'):result[offset]=dict(raw=raw,path=str(path))
    except (OSError,ValueError,KeyError,TypeError):return {}
    return result
def preserve(loader,offset,length,label,out,cache,fresh_read):
    entry=cache.get(offset)
    if not entry:return fresh_read(offset,length,label),dict(origin='fresh-read')
    prefix=min(len(entry['raw']),length);expected=hashlib.md5(entry['raw'][:prefix]).hexdigest()
    if loader.flash_md5sum(offset,prefix).lower()!=expected:return fresh_read(offset,length,label),dict(origin='fresh-read-cache-device-mismatch')
    raw=entry['raw'][:prefix]
    if prefix<length:raw+=fresh_read(offset+prefix,length-prefix,label+'-uncached-tail')
    if len(raw)!=length or hashlib.md5(raw).hexdigest()!=loader.flash_md5sum(offset,length).lower():raise RuntimeError('Cached prior reconstruction/device hash mismatch')
    (Path(out)/(label+'.bin')).write_bytes(raw)
    return raw,dict(origin='verified-readback-cache-and-fresh-device-md5',cache=entry['path'],cached_bytes=prefix,fresh_tail_bytes=length-prefix)
