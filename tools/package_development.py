"""Create a source review archive under515MiB; never labels it a qualified release."""
import argparse,hashlib,json,time,zipfile
from pathlib import Path
from board_io import ROOT,save
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',type=Path,required=True);a=p.parse_args()
    app=json.loads((a.app/'app.json').read_text())
    if not app.get('pass_all'):raise RuntimeError('Candidate must build first')
    for name,digest in app['sources'].items():
        if not name.startswith(('firmware','protocol')):continue
        path=ROOT/name
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise RuntimeError('Current source differs from frozen firmware: '+name)
    out=ROOT/'deliverables'/('development-'+str(time.time_ns()));out.mkdir(parents=True)
    files=[p for folder in ['firmware','protocol','tests','tools','research'] for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix.lower() in ['.asm','.c','.h','.py','.ps1','.java','.xml','.json','.md','.txt','.cmake','.csv','.defaults']]
    files += [p for p in ROOT.iterdir() if p.is_file() and p.suffix.lower() in ['.md','.json']]
    files += [ROOT/'hardware'/name for name in ['admitted-boards-reference.json','deployed-COM6.json','deployed-COM7.json']]
    license_files=[p for p in (ROOT/'third_party/btstack').iterdir() if p.is_file() and ('LICENSE' in p.name.upper() or 'COPYING' in p.name.upper())]
    files+=license_files
    notice=('DEVELOPMENT SOURCE REVIEW ARCHIVE\nNot an audio-qualified release.\n'
            'ESP-IDF and BTstack source dependencies are pinned by dependencies.lock.json.\n'
            'They must be fetched separately; unchanged vendor SDK source is not bundled.\n'
            'Only COM6/COM7 and the currently admitted phone/Linux sources may be used by hardware scripts.\n'
            'No acoustic latency, physical clock precision or100-speaker claim.\n')
    archive=out/'ESP32-WIFI-MUSIC-PARTY-source.zip';entries={}
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        z.writestr('DEVELOPMENT-NOTICE.txt',notice)
        for path in sorted(set(files)):
            raw=path.read_bytes();name=str(path.relative_to(ROOT)).replace('\\','/');z.writestr(name,raw);entries[name]=hashlib.sha256(raw).hexdigest()
        for artifact in app['artifacts']:
            path=Path(artifact['path']);raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=artifact['sha256']:raise RuntimeError('Firmware artifact hash changed')
            z.writestr('candidate-images/'+path.name,raw)
        z.writestr('candidate-images/app.json',json.dumps(app,indent=2))
        z.writestr('SOURCE-SHA256.json',json.dumps(entries,indent=2))
    if archive.stat().st_size>=515*1024*1024:raise RuntimeError('Archive exceeded requested size ceiling')
    result=dict(archive=str(archive),bytes=archive.stat().st_size,sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),build_id=app['build_id'],files=len(entries),development_only=True,audio_qualified=False)
    save(out/'receipt.json',result);print(json.dumps(result))
if __name__=='__main__':main()
