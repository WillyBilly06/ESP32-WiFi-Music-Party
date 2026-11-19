"""One normal EN reset of an identity-admitted board; never flashes or restores an image."""
import argparse,json,secrets,time
from board_io import ROOT,board,dashboard,open_serial,exchange,save
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',required=True,choices=['COM6','COM7']);a=p.parse_args()
    active=ROOT/'hardware/active-session.json'
    if active.exists() and json.loads(active.read_text()).get('state')!='stopped':raise RuntimeError('Stop the current persistent UART owner first')
    value=board(a.port);deployed=json.loads((ROOT/'hardware'/('deployed-'+a.port+'.json')).read_text());assert deployed['boot_verified']
    out=ROOT/'evidence'/('reboot-'+str(time.time_ns())+'-'+a.port);out.mkdir();path=out/(a.port+'.raw');path.touch();token=secrets.token_hex(16);leased=False;uart=None
    r=dict(port=a.port,build_id=deployed['build_id'],pass_all=False,reset_sent=False,errors=[])
    try:
        dashboard(a.port,'LEASE '+token);leased=True;dashboard(a.port,'FOLLOWLEASE '+token+' '+str(path));uart=open_serial(a.port)
        with path.open('ab') as log:
            info=exchange(uart,log,'INFO',b' audio_qualified=0',5)
            if ('build='+deployed['build_id']).encode() not in info or ('base_mac='+value['base_mac']).encode() not in info:raise RuntimeError('Live identity mismatch')
            uart.dtr=False;uart.rts=True;r['reset_sent']=True;time.sleep(.1);uart.rts=False
            raw=bytearray();deadline=time.monotonic()+25;marker=('WMP_READY build='+deployed['build_id']).encode()
            while time.monotonic()<deadline:
                data=uart.read(4096)
                if data:log.write(data);log.flush();raw.extend(data)
                if marker in raw:break
            else:raise RuntimeError('Expected current image did not boot')
            info=exchange(uart,log,'INFO',b' audio_qualified=0',5)
            if ('build='+deployed['build_id']).encode() not in info or ('base_mac='+value['base_mac']).encode() not in info:raise RuntimeError('Post-reset identity mismatch')
            nonce=secrets.token_hex(4);exchange(uart,log,'PING '+nonce,('nonce='+nonce).encode(),5);r['pass_all']=True
    except Exception as e:r['errors'].append(repr(e))
    finally:
        if uart:uart.close()
        if leased:
            try:r['dashboard_restored']=dashboard(a.port,'UNLEASE '+token)
            except Exception as e:r['errors'].append('lease: '+repr(e));r['pass_all']=False
        save(out/'receipt.json',r)
    print(json.dumps(dict(evidence=str(out),**r)))
    raise SystemExit(0 if r['pass_all'] else 1)
if __name__=='__main__':main()
