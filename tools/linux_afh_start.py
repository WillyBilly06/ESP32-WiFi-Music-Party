"""Launch one finite, identity-guarded Linux fixture diagnostic; run in guest."""
import argparse,json,os,pathlib,subprocess,sys,time

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',choices=['COM6','COM7'],default='COM6');p.add_argument('--seconds',type=int,default=120);p.add_argument('--sample-interval',type=int,default=1);a=p.parse_args()
    if not 30<=a.seconds<=7200 or not 1<=a.sample_interval<=10:p.error('Finite duration/sampling required')
    base=pathlib.Path('/home/billydakidz/wmp-linux-source').resolve();script=base/'linux_afh_probe.py';job=base/'afh-active-job.json'
    if job.exists():
        old=json.loads(job.read_text());proc=pathlib.Path('/proc')/str(old['pid'])/'cmdline'
        if proc.exists() and str(script).encode() in proc.read_bytes().split(b'\x00'):raise RuntimeError('Prior owned AFH process is still live; no second owner')
    out=base/('afh-assessment-'+str(time.time_ns()));log=out.with_suffix('.stdout')
    with log.open('wb') as output:
        child=subprocess.Popen([sys.executable,str(script),'--adapter','hci0','--port',a.port,'--seconds',str(a.seconds),'--sample-interval',str(a.sample_interval),'--directory',str(out)],stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
    record=dict(pid=child.pid,directory=str(out),log=str(log),seconds=a.seconds,sample_interval=a.sample_interval,port=a.port);tmp=job.with_suffix('.tmp');tmp.write_text(json.dumps(record));tmp.replace(job)
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if (out/'status.json').exists():
            state=json.loads((out/'status.json').read_text())
            if state.get('active') and state.get('readback_mode')==0:
                print(json.dumps(dict(record,active=True,original_mode=state['original_mode'],readback_mode=0,restoration_owned_by_child=True)),flush=True);return
            if state.get('finished'):raise RuntimeError('Diagnostic terminal before readiness: '+str(state.get('errors')))
        if child.poll() is not None:raise RuntimeError('Child terminal before readiness: '+log.read_text()[-2000:])
        time.sleep(.1)
    raise TimeoutError('No admission reply; bounded child still owns restoration; do not replay')

if __name__=='__main__':main()
