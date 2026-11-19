from local_ipc import connection as task_connection
"""Command the current persistent session without reopening an ESP32 UART."""
import argparse,json,socket
from board_io import ROOT
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stop',action='store_true');p.add_argument('--release-port',action='store_true');p.add_argument('--resume-port',action='store_true');p.add_argument('--port',choices=['COM6','COM7']);p.add_argument('--command');p.add_argument('--reply');a=p.parse_args()
    state=json.loads((ROOT/'hardware/active-session.json').read_text())
    if state.get('state')!='running':raise RuntimeError('No running owned session')
    req=dict(token=state['token'],action='stop' if a.stop else ('release' if a.release_port else ('resume' if a.resume_port else 'command')),port=a.port,command=a.command,marker=a.reply)
    if sum([a.stop,a.release_port,a.resume_port])>1:p.error('One ownership action at a time')
    if (a.release_port or a.resume_port) and not a.port:p.error('Ownership action needs port')
    if not any([a.stop,a.release_port,a.resume_port]) and not all([a.port,a.command,a.reply]):p.error('Command needs port/command/reply')
    with task_connection(('127.0.0.1',state['control_port']),timeout=30) as s:
        s.sendall((json.dumps(req)+'\n').encode());f=s.makefile('rb');r=json.loads(f.readline(262145))
    if not r.get('ok'):raise RuntimeError(r)
    print(json.dumps(r))
if __name__=='__main__':main()
