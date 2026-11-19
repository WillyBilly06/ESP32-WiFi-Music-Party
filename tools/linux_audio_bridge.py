"""Private VM status/playback control for the explicitly owned Linux test stream."""
import argparse,hmac,json,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);a=p.parse_args();c=json.loads(a.config.read_text())
    playback=Path(c['playback']);gatt=Path(c['gatt'])
    def read(path):
        try:return json.loads(path.read_text())
        except (FileNotFoundError,json.JSONDecodeError):return None
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def reply(self,status,value):
            raw=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def allowed(self):return hmac.compare_digest(self.headers.get('X-WMP-Token',''),c['token'])
        def do_GET(self):
            if not self.allowed():return self.reply(403,{'error':'unauthorized'})
            if self.path!='/status':return self.reply(404,{'error':'not found'})
            return self.reply(200,dict(at=time.time(),playback=read(playback/'playback.json'),gatt=read(gatt)))
        def do_POST(self):
            if not self.allowed():return self.reply(403,{'error':'unauthorized'})
            if self.path!='/control':return self.reply(404,{'error':'not found'})
            size=int(self.headers.get('Content-Length','0'))
            if not 1<=size<=1024:return self.reply(400,{'error':'size'})
            try:
                command=json.loads(self.rfile.read(size))
                if command.get('action') not in ['play','pause']:raise ValueError('play or pause only')
                if not isinstance(command.get('id'),str) or len(command['id'])>64:raise ValueError('bounded id required')
                path=playback/'control.json';tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(command));tmp.replace(path)
                return self.reply(200,dict(queued=True,id=command['id']))
            except (ValueError,json.JSONDecodeError) as error:return self.reply(400,dict(error=str(error)))
    print(json.dumps(dict(bind=c['bind'],port=c['port'],password_stored=False)),flush=True)
    ThreadingHTTPServer((c['bind'],c['port']),Handler).serve_forever()
if __name__=='__main__':main()
