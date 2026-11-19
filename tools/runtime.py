"""One bounded, identity-checked UART command without resetting firmware."""
import argparse,json,secrets,time
from pathlib import Path
from board_io import ROOT,board,dashboard,open_serial,exchange,save
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',choices=['COM6','COM7'],required=True);p.add_argument('--command',required=True);p.add_argument('--reply',required=True);a=p.parse_args()
    if len(a.command)>95 or any(c in a.command for c in '\r\n') or not a.reply.startswith('WMP_'):raise ValueError('One bounded WMP command required')
    value=board(a.port);deployment=json.loads((ROOT/'hardware'/('deployed-'+a.port+'.json')).read_text())
    if not deployment.get('boot_verified'):raise RuntimeError('Firmware installation not verified')
    out=ROOT/'evidence'/('command-'+str(time.time_ns())+'-'+a.port);out.mkdir();path=out/(a.port+'.raw');path.touch();token=secrets.token_hex(16);handle=None;leased=False
    row=dict(port=a.port,command=a.command,build_id=deployment['build_id'],pass_all=False,errors=[])
    try:
        dashboard(a.port,'LEASE '+token);leased=True;dashboard(a.port,'FOLLOWLEASE '+token+' '+str(path));handle=open_serial(a.port)
        with path.open('ab') as log:
            info=exchange(handle,log,'INFO',b' audio_qualified=0',5)
            if ('build='+deployment['build_id']).encode() not in info or ('base_mac='+value['base_mac']).encode() not in info:raise RuntimeError('Runtime identity mismatch')
            nonce=secrets.token_hex(4);exchange(handle,log,'PING '+nonce,('nonce='+nonce).encode(),5)
            row['reply']=exchange(handle,log,a.command,a.reply.encode(),15).decode('ascii','replace');row['pass_all']=True
    except Exception as error:row['errors'].append(repr(error))
    finally:
        if handle:handle.close()
        if leased:
            try:row['dashboard_restored']=dashboard(a.port,'UNLEASE '+token)
            except Exception as error:row['errors'].append(repr(error));row['pass_all']=False
        save(out/'receipt.json',row)
    print(json.dumps(dict(evidence=str(out),pass_all=row['pass_all'],reply=row.get('reply',''),errors=row['errors'])))
    raise SystemExit(0 if row['pass_all'] else 1)
if __name__=='__main__':main()
