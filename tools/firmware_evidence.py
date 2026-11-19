"""Bind qualification to one explicit candidate and its two exact installs.

The file checks are offline. Live INFO is supplied by an already-owned UART
caller after phone admission; this module never opens a device or socket.
"""
import hashlib,json,re
from pathlib import Path
from board_io import ROOT

def _sha(raw):return hashlib.sha256(raw).hexdigest()

def image_erase_end(offset,size):
    # Explicit image areas leave NVS9000..efff and otadata/f000 untouched.
    limits={0x1000:0x8000,0x8000:0x9000,0x10000:0x190000}
    if offset not in limits or size<=0:raise ValueError('Unadmitted/empty image extent')
    end=offset+((size+4095)//4096)*4096
    if end>limits[offset]:raise ValueError('Image erase overlaps a protected flash area')
    return end

def candidate(app,root=ROOT):
    root=Path(root).resolve();app=Path(app).resolve();app.relative_to(root/'build')
    manifest=app/'app.json';m=json.loads(manifest.read_text())
    if not m.get('pass_all') or m.get('profile')!='btstack-custom-wifi-audio-candidate':raise ValueError('Candidate build did not pass')
    if not re.fullmatch('[0-9a-f]{16}',m.get('build_id','')):raise ValueError('Invalid candidate build identity')
    if m['dependencies']!=json.loads((root/'dependencies.lock.json').read_text()):raise ValueError('Candidate dependency lock differs')
    own={}
    for name,digest in m['sources'].items():
        p=(root/name).resolve();parts=p.relative_to(root).parts
        if parts and parts[0] in ('firmware','protocol'):
            if not p.is_file() or _sha(p.read_bytes())!=digest:raise ValueError('Current source differs from candidate: '+name)
            own[str(p)]=digest
    files={str(p.resolve()) for folder in ['firmware','protocol'] for p in (root/folder).rglob('*') if p.is_file()}
    if set(own)!=files:raise ValueError('Candidate own-source coverage differs')
    artifacts={}
    for a in m['artifacts']:
        p=Path(a['path']).resolve();p.relative_to(app);raw=p.read_bytes();offset=a['offset']
        if offset in artifacts or offset not in (0x1000,0x8000,0x10000):raise ValueError('Duplicate/invalid image extent')
        padded=raw+b'\xff'*((-len(raw))%4096)
        if len(raw)!=a['bytes'] or _sha(raw)!=a['sha256']:raise ValueError('Candidate artifact hash differs')
        image_erase_end(offset,len(raw))
        artifacts[offset]=dict(bytes=len(raw),sha256=a['sha256'],readback_sha256=_sha(padded))
    if set(artifacts)!={0x1000,0x8000,0x10000}:raise ValueError('Missing candidate images')
    extents=sorted((offset,offset+((a['bytes']+4095)//4096)*4096) for offset,a in artifacts.items())
    if any(end>following for (_,end),(following,_) in zip(extents,extents[1:])):raise ValueError('Overlapping image erases')
    return dict(build_id=m['build_id'],manifest=str(manifest),artifacts=artifacts,source_hashes_checked=len(own),hardware_tested=False)

def installed_pair(app,root=ROOT):
    root=Path(root).resolve();proof=candidate(app,root);admitted=json.loads((root/'hardware/admitted-boards-reference.json').read_text())['boards'];boards={}
    for port in ['COM6','COM7']:
        identities=[b for b in admitted if b['port']==port]
        if len(identities)!=1:raise ValueError('Ambiguous board admission '+port)
        identity=identities[0];d=json.loads((root/'hardware'/('deployed-'+port+'.json')).read_text())
        if d.get('port')!=port or d.get('build_id')!=proof['build_id'] or not d.get('boot_verified') or d.get('state')!='installed':raise ValueError(port+' is not an exact verified installation of candidate '+proof['build_id'])
        receipt_path=Path(d['receipt']).resolve();receipt_path.relative_to(root/'evidence');r=json.loads(receipt_path.read_text())
        if r.get('esptool_verified'):
            # User-approved 2026-11-17: plain esptool write-flash with per-image
            # device hash verification, used where the guarded installer's
            # readback is unreliable. No outside-extent/NVS audit is implied.
            if not r.get('pass_all') or r.get('errors') or r.get('port')!=port or r.get('build_id')!=proof['build_id']:raise ValueError('Failed/incomplete esptool installation '+port)
            if r.get('actual_mac','').lower()!=identity['base_mac'].lower() or d.get('base_mac','').lower()!=identity['base_mac'].lower():raise ValueError('Installation identity differs '+port)
            if Path(r['manifest']).resolve()!=Path(proof['manifest']):raise ValueError('Installation used another manifest '+port)
            images={a['offset']:a for a in r.get('images',[])}
            if set(images)!=set(proof['artifacts']) or any(images[o].get('sha256')!=e['sha256'] or images[o].get('bytes')!=e['bytes'] for o,e in proof['artifacts'].items()):raise ValueError('esptool image coverage differs '+port)
            boards[port]=dict(base_mac=identity['base_mac'].lower(),installation_receipt=str(receipt_path),build_id=proof['build_id'],verification='esptool_hash');continue
        if not r.get('pass_all') or r.get('errors') or not r.get('flash_verified') or r.get('stage')!='terminal':raise ValueError('Failed/incomplete installation '+port)
        if r.get('port')!=port or r.get('build_id')!=proof['build_id'] or r.get('actual_mac','').lower()!=identity['base_mac'].lower():raise ValueError('Installation identity differs '+port)
        if d.get('base_mac','').lower()!=identity['base_mac'].lower():raise ValueError('Deployment MAC differs '+port)
        if Path(r['manifest']).resolve()!=Path(proof['manifest']):raise ValueError('Installation used another manifest '+port)
        if len(r.get('boots',[]))!=2 or not all(b.get('identity_verified') for b in r['boots']):raise ValueError('Missing two verified boots '+port)
        if r.get('outside_before')!=r.get('outside_after') or r.get('outside_before')!=r.get('outside_runtime') or not r.get('outside_before'):raise ValueError('Outside-extent audit differs '+port)
        images={a['offset']:a for a in r['images']}
        if len(r['images'])!=len(images) or set(images)!=set(proof['artifacts']):raise ValueError('Installation image coverage differs '+port)
        for offset,expected in proof['artifacts'].items():
            if any(images[offset].get(k)!=v for k,v in expected.items()):raise ValueError('Image/readback hash differs '+port+' '+hex(offset))
        boards[port]=dict(base_mac=identity['base_mac'].lower(),installation_receipt=str(receipt_path),build_id=proof['build_id'])
    return dict(candidate=proof,boards=boards,live_info_checked=False,audio_qualified=False)

def live_info(binding,port,reply):
    if port not in binding['boards']:raise ValueError('Unadmitted live board')
    rows=[line for line in reply.splitlines() if line.startswith('WMP_INFO ')]
    if len(rows)!=1:raise ValueError('Missing/ambiguous fresh INFO '+port)
    fields=dict(re.findall(r'(\w+)=([^\s]+)',rows[0]))
    if fields.get('build')!=binding['candidate']['build_id'] or fields.get('base_mac','').lower()!=binding['boards'][port]['base_mac']:raise ValueError('Live board/build differs '+port)
    return dict(port=port,build_id=fields['build'],base_mac=fields['base_mac'].lower())

def live_pair(binding,command):
    # Caller must establish phone authorization before invoking this function.
    rows={}
    for port in ['COM6','COM7']:
        response=command(port,'INFO','WMP_INFO');rows[port]=dict(identity=live_info(binding,port,response['reply']),evidence=response['evidence'])
    binding['live_info_checked']=True
    return rows

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--app',type=Path,required=True);parser.add_argument('--installed',action='store_true');args=parser.parse_args()
    try:
        proof=installed_pair(args.app) if args.installed else candidate(args.app)
        print(json.dumps(dict(pass_all=True,offline_only=True,proof=proof)))
    except (ValueError,OSError,KeyError) as e:
        print(json.dumps(dict(pass_all=False,offline_only=True,error=str(e))));raise SystemExit(1)
