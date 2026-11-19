"""Freeze and build the new BTstack/custom-Wi-Fi audio application."""
import hashlib,json,os,secrets,shutil,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    lock=json.loads((ROOT/'dependencies.lock.json').read_text(encoding='utf-8'))
    sdk=Path(lock['esp_idf']['path']);bt=ROOT/lock['btstack']['path']
    if not sdk.is_absolute():sdk=ROOT/sdk
    for name,path in [('esp_idf',sdk),('btstack',bt)]:
        if subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],text=True).strip()!=lock[name]['revision']:raise RuntimeError('Dependency pin changed: '+name)
        if subprocess.check_output(['git','-C',str(path),'status','--porcelain','--untracked-files=no'],text=True).strip():raise RuntimeError('Dependency modified: '+name)
    # Scratch reuse is tied to these audited SDK copy implementations.
    raw_audit=json.loads((ROOT/'research/raw-tx-copy-v61/audit.json').read_text())
    if lock['esp_idf']['revision']!=raw_audit['sdk_revision']:raise RuntimeError('Raw TX copy ABI must be audited for this SDK')
    for archive,digest in raw_audit['archives'].items():
        if sha(sdk/Path(archive).relative_to('third_party/esp-idf'))!=digest:raise RuntimeError('Raw TX copy archive changed: '+archive)
    out=ROOT/'build'/('app-'+str(time.time_ns()));frozen=out/'sources';frozen.mkdir(parents=True)
    paths=[p for directory in ['firmware','protocol'] for p in (ROOT/directory).rglob('*') if p.is_file()]
    tracked=subprocess.check_output(['git','-C',str(bt),'ls-files','-z']).decode().split('\0')
    prefixes=('src/','platform/','3rd-party/','chipset/','port/esp32/components/','tool/cmake/')
    paths += [bt/p for p in tracked if p and (p.startswith(prefixes) or '/' not in p and ('LICENSE' in p or p.startswith('README')))]
    # Non-SBC decoders (Helix AAC, libfreeaptx): sparse checkout pinned to one upstream commit.
    codec_dep=ROOT/'third_party/esp32-hifi-a2dp-sink';codec_rev='7dcb71a0c3ffea973322fcec1f8f46abecd6b079'
    if subprocess.check_output(['git','-C',str(codec_dep),'rev-parse','HEAD'],text=True).strip()!=codec_rev:raise RuntimeError('Dependency pin changed: esp32-hifi-a2dp-sink')
    if subprocess.check_output(['git','-C',str(codec_dep),'status','--porcelain','--untracked-files=no'],text=True).strip():raise RuntimeError('Dependency modified: esp32-hifi-a2dp-sink')
    paths += [p for p in codec_dep.rglob('*') if p.is_file() and '.git' not in p.relative_to(codec_dep).parts]
    pins={}
    for path in paths:
        relative=path.relative_to(ROOT);dest=frozen/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,dest);pins[str(relative)]=sha(dest)
    build_id=hashlib.sha256((json.dumps(pins,sort_keys=True)+secrets.token_hex(8)).encode()).hexdigest()[:16]
    esp=Path.home()/'.espressif';python_dir=esp/'python_env/idf6.1_py3.12_env/Scripts'
    dirs=[python_dir,esp/'tools/xtensa-esp-elf/esp-15.2.0_20251204/xtensa-esp-elf/bin',esp/'tools/cmake/4.0.3/bin',esp/'tools/ninja/1.12.1']
    env=os.environ.copy();env.update(IDF_PATH=str(sdk),IDF_TOOLS_PATH=str(esp),IDF_PYTHON_ENV_PATH=str(python_dir.parent),BTSTACK_ROOT=str(frozen/'third_party/btstack'),IDF_COMPONENT_MANAGER='0')
    env['PATH']=os.pathsep.join([*[str(p) for p in dirs],env.get('PATH','')])
    env['ESP_ROM_ELF_DIR']=str(esp/'tools/esp-rom-elfs/20241011')
    cache=esp/'tools/ccache/4.12.1/ccache-4.12.1-windows-x86_64/ccache.exe'
    env['PATH']=str(cache.parent)+os.pathsep+env['PATH']
    env.update(CCACHE_DIR=str(ROOT/'build/compiler-cache'),CCACHE_BASEDIR=str(out),CCACHE_NOHASHDIR='1',CCACHE_COMPILERCHECK='content')
    # IDF enables RULE_LAUNCH_COMPILE itself. Do not also wrap each C/C++
    # compiler with a launcher: that would nest ccache twice per compilation.
    command=[str(python_dir/'python.exe'),str(ROOT/'tools/idf_entry.py'),str(sdk/'tools/idf.py'),'--ccache','-C',str(frozen/'firmware/app'),'-B',str(out/'idf-build'),'-D','WMP_BUILD_ID='+build_id,'build']
    def cache_stats(name):
        stats=subprocess.run([str(cache),'-s'],env=env,capture_output=True,text=True)
        (out/name).write_text(stats.stdout+stats.stderr,encoding='utf-8')
        return dict(exit_code=stats.returncode,path=str(out/name))
    record=dict(build_id=build_id,profile='btstack-custom-wifi-audio-candidate',pass_all=False,audio_qualified=False,radio_qualified=False,sources=pins,dependencies=lock,command=command,started_ns=time.time_ns(),ccache=dict(explicit_idf_flag=True,single_wrapper=True,executable=str(cache),cache_dir=env['CCACHE_DIR'],before=cache_stats('ccache-before.txt')))
    manifest=out/'app.json';manifest.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8');print('Build:',out,flush=True)
    with (out/'build.log').open('w',encoding='utf-8') as log:r=subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
    record.update(exit_code=r.returncode,finished_ns=time.time_ns())
    record['ccache']['after']=cache_stats('ccache-after.txt')
    if r.returncode==0:
        record['artifacts']=[]
        for offset,name in [(0x1000,'bootloader/bootloader.bin'),(0x8000,'partition_table/partition-table.bin'),(0x10000,'wmp_audio.bin')]:
            path=out/'idf-build'/name;record['artifacts'].append(dict(offset=offset,path=str(path),bytes=path.stat().st_size,sha256=sha(path)))
        record['elf']=dict(path=str(out/'idf-build/wmp_audio.elf'),sha256=sha(out/'idf-build/wmp_audio.elf'));record['pass_all']=True
    manifest.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(pass_all=record['pass_all'],manifest=str(manifest),log=str(out/'build.log'))),flush=True)
    raise SystemExit(r.returncode)
if __name__=='__main__':main()
