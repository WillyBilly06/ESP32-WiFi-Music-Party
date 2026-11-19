"""Persistent admitted UART capture; one lease/open per board for the whole run."""
import argparse,concurrent.futures,json,re,secrets,socketserver,threading,time
from pathlib import Path
from board_io import ROOT,board,dashboard,open_serial,save

class Channel:
    def __init__(self,port,out,use_dashboard=True):
        self.port=port;self.board=board(port);self.out=out;self.token=secrets.token_hex(16);self.use_dashboard=use_dashboard
        self.path=out/(port+'.raw');self.path.touch();self.uart=None;self.leased=False
        self.condition=threading.Condition();self.command_lock=threading.RLock();self.rx=bytearray();self.base=0;self.stopping=threading.Event();self.error=None;self.suspended=False
        self.build=json.loads((ROOT/'hardware'/('deployed-'+port+'.json')).read_text())
        if not self.build.get('boot_verified'):raise RuntimeError('Unverified deployment')
    def start(self):
        if self.use_dashboard:
            dashboard(self.port,'LEASE '+self.token);self.leased=True;dashboard(self.port,'FOLLOWLEASE '+self.token+' '+str(self.path))
        self.log=self.path.open('ab');self.uart=open_serial(self.port)
        self.reader=threading.Thread(target=self.read,daemon=True);self.reader.start()
        info=self.send('INFO',' audio_qualified=0')
        if 'build='+self.build['build_id'] not in info or 'base_mac='+self.board['base_mac'] not in info:raise RuntimeError('Live firmware/board identity mismatch')
        nonce=secrets.token_hex(4);self.send('PING '+nonce,'nonce='+nonce)
    def read(self):
        try:
            while not self.stopping.is_set():
                data=self.uart.read(4096)
                if not data:continue
                self.log.write(data);self.log.flush()
                with self.condition:
                    self.rx.extend(data)
                    if len(self.rx)>262144:
                        remove=len(self.rx)-262144;del self.rx[:remove];self.base+=remove
                    self.condition.notify_all()
        except Exception as e:
            self.error=repr(e)
            with self.condition:self.condition.notify_all()
    def send(self,command,marker,timeout=15):
        if len(command)>95 or '\n' in command or '\r' in command:raise ValueError('One bounded command')
        with self.command_lock:
            if self.suspended:raise RuntimeError(self.port+' released for hardware update')
            with self.condition:start=self.base+len(self.rx)
            self.uart.write((command+'\n').encode());self.uart.flush();deadline=time.monotonic()+timeout
            with self.condition:
                while True:
                    if self.error:raise RuntimeError(self.error)
                    if start<self.base:raise RuntimeError('Response buffer overflow')
                    raw=bytes(self.rx[start-self.base:])
                    # A prior response tail may arrive after this command was
                    # written. Only a complete, nonce-matching terminal line
                    # completes a query; never accept a substring in a partial.
                    parts=command.split();expected=None
                    if len(parts)==2 and parts[0] in ['STAT','TRACE','HCICTRACE','REPAIRTRACE','FAULTTRACE','DROPTRACE','CLOCKTRACE']:
                        expected=('query='+parts[1].lower()).encode()
                    for completed in raw.split(b'\n')[:-1]:
                        if marker.encode() in completed and (expected is None or expected in completed):return raw.decode('ascii','replace')
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise TimeoutError(self.port+' reply absent: '+marker)
                    self.condition.wait(min(remaining,.5))
    def _sample(self):
        nonce=secrets.token_hex(4);reply=self.send('STAT '+nonce,'WMP_STAT_END');lines={}
        for line in reply.splitlines():
            if not line.startswith('WMP_'):continue
            fields={k:int(v,16 if k=='query' else 10) for k,v in re.findall(r'(\w+)=(-?[0-9a-f]+)(?:\s|$)',line)}
            if 'query' not in fields:continue  # asynchronous boot/profile notices remain in raw log
            if fields['query']!=int(nonce,16):continue # retained in raw; cannot satisfy this query
            lines[line.split()[0]]=fields
        if not {'WMP_RADIO','WMP_RX','WMP_BT','WMP_AUDIO','WMP_STAT_END'}.issubset(lines):raise RuntimeError('Incomplete statistics')
        if not {'decoded','concealed','late','qfull'}.issubset(lines['WMP_AUDIO']):raise RuntimeError('Malformed audio statistics retained in raw UART log')
        return dict(evidence=str(self.path),lines=lines)
    def sample(self):
        with self.command_lock:
            if self.suspended:return dict(suspended=True,evidence=str(self.path),lines={})
            return self._sample()
    def close(self):
        self.stopping.set()
        if self.uart:
            self.reader.join(timeout=2);self.uart.close();self.log.close()
        if self.leased:dashboard(self.port,'UNLEASE '+self.token);self.leased=False

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seconds',type=int,default=600);p.add_argument('--interval',type=float,default=5);p.add_argument('--direct-owner',action='store_true',help='Exclusive UART ownership when the optional dashboard is stopped; never bypass an active dashboard');a=p.parse_args()
    if a.direct_owner:
        import subprocess
        result=subprocess.run(['powershell.exe','-NoProfile','-Command',"@(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -in 47006,47007,48006).Count"],capture_output=True,text=True,timeout=15)
        if result.returncode or result.stdout.strip()!='0':raise RuntimeError('Direct ownership requires verified absence of dashboard/capture listeners')
    if not 10<=a.seconds<=14400 or not (a.interval==0 or 2<=a.interval<=30):p.error('Finite duration; interval0 means raw capture/RPC only, otherwise2..30seconds')
    out=ROOT/'evidence'/('persistent-audio-'+str(time.time_ns()));out.mkdir();(out/'capture.py').write_bytes(Path(__file__).read_bytes())
    stop=threading.Event();token=secrets.token_hex(16);channels={};servers=[]
    row=dict(pass_all=False,acquisition_complete=False,audio_qualified=False,samples=[],errors=[],persistent=True,open_count={},seconds=a.seconds)
    active=dict(state='starting',token=token,evidence=str(out),ports=['COM6','COM7'],control_port=48006)
    save(ROOT/'hardware/active-session.json',active);print('Persistent:',out,flush=True)
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                self.request.settimeout(30);raw=self.rfile.readline(4097)
                if len(raw)>4096:raise ValueError('Control request too long')
                r=json.loads(raw)
                if r.get('token')!=token:raise ValueError('Session token mismatch')
                if r.get('action')=='status':
                    result=dict(ok=True,direct_owner=a.direct_owner,ports={p:dict(suspended=c.suspended,uart_open=bool(c.uart and c.uart.is_open)) for p,c in channels.items()})
                elif r.get('action')=='stop':stop.set();result=dict(ok=True,stopping=True)
                elif r.get('action') in ['release','resume']:
                    port=r['port']
                    if port not in channels:raise ValueError('Unadmitted port')
                    c=channels[port]
                    if r['action']=='release':
                        with c.command_lock:
                            if not c.suspended:c.suspended=True;c.close()
                        active.setdefault('released_ports',[])
                        if port not in active['released_ports']:active['released_ports'].append(port)
                    elif c.suspended:
                        c=Channel(port,out,use_dashboard=not a.direct_owner)
                        with c.command_lock:
                            channels[port]=c
                            try:c.start()
                            except Exception:c.suspended=True;c.close();raise
                        row['open_count'][port]+=1
                        if port in active.get('released_ports',[]):active['released_ports'].remove(port)
                    row.setdefault('ownership_events',[]).append(dict(at_ns=time.time_ns(),port=port,action=r['action']))
                    save(ROOT/'hardware/active-session.json',active);result=dict(ok=True,port=port,action=r['action'])
                else:
                    port=r['port']
                    if port not in channels:raise ValueError('Unadmitted port')
                    if not r['marker'].startswith('WMP_'):raise ValueError('Invalid reply marker')
                    result=dict(ok=True,reply=channels[port].send(r['command'],r['marker']))
                self.wfile.write((json.dumps(result)+'\n').encode())
            except Exception as e:self.wfile.write((json.dumps(dict(ok=False,error=repr(e)))+'\n').encode())
    try:
        for port in ['COM6','COM7']:
            c=Channel(port,out,use_dashboard=not a.direct_owner);channels[port]=c;c.start();row['open_count'][port]=1
        server=socketserver.ThreadingTCPServer(('127.0.0.1',48006),Handler);server.daemon_threads=True;servers.append(server)
        threading.Thread(target=server.serve_forever,daemon=True).start();active['state']='running';save(ROOT/'hardware/active-session.json',active)
        start=time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            while not stop.is_set():
                if not a.interval:
                    if time.monotonic()-start>=a.seconds:row['acquisition_complete']=True;break
                    stop.wait(min(1,a.seconds-(time.monotonic()-start)));continue
                jobs={port:pool.submit(c.sample) for port,c in channels.items()};pair={}
                for port,future in jobs.items():
                    try:pair[port]=future.result()
                    except Exception as error:
                        row['errors'].append(dict(port=port,elapsed=time.monotonic()-start,error=repr(error)))
                        pair[port]=dict(sample_error=repr(error),lines={})
                row['samples'].append(dict(elapsed=time.monotonic()-start,ports=pair));save(out/'receipt.json',row)
                print(json.dumps(dict(elapsed=round(time.monotonic()-start,1),ports={p:({'error':v['sample_error']} if v.get('sample_error') else ({'suspended':True} if v.get('suspended') else {k:v['lines']['WMP_AUDIO'][k] for k in ['decoded','concealed','late','qfull']})) for p,v in pair.items()})),flush=True)
                if time.monotonic()-start>=a.seconds:row['acquisition_complete']=True;break
                stop.wait(min(a.interval,a.seconds-(time.monotonic()-start)))
        row['pass_all']=row['acquisition_complete'] and not row['errors'];row['stop_reason']='duration' if row['acquisition_complete'] else 'explicit_session_stop'
    except Exception as e:row['errors'].append(repr(e))
    finally:
        for s in servers:s.shutdown();s.server_close()
        for c in channels.values():
            try:c.close()
            except Exception as e:row['errors'].append('close: '+repr(e));row['pass_all']=False
        active['state']='stopped';save(ROOT/'hardware/active-session.json',active);save(out/'receipt.json',row)
    print(json.dumps(dict(evidence=str(out),acquisition_complete=row['acquisition_complete'],errors=row['errors'])),flush=True)
if __name__=='__main__':main()
