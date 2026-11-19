"""Connect admitted Samsung to the selected board; enforce20% media."""
import argparse,json,re,time,xml.etree.ElementTree as ET
from adb_local import shell,SAMSUNG
from board_io import ROOT,save
from sweep_live_pacing import sample

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',choices=['COM6','COM7'],default='COM6');args=parser.parse_args();port=args.port
    name={'COM6':'WMP-Sink-BF64','COM7':'WMP-Sink-6624'}[port]
    out=ROOT/'evidence'/('samsung-connect-'+str(time.time_ns()));out.mkdir();r=dict(port=port,pass_all=False,audio_qualified=False,volume_percent=20,errors=[]);print(out,flush=True)
    try:
        if shell(SAMSUNG,['getprop','ro.product.model']).strip()!='SM-S948U1':raise RuntimeError('Samsung identity changed')
        first=sample(port);r['before']=first;b=first['lines']['WMP_BT'];p=first['lines']['WMP_PEERS']
        if b['phone'] and (p['classic_hi'],p['classic_lo'])!=(0x74f441e0,0xa5aa):raise RuntimeError('Another Classic source already owns '+port)
        if not b['phone']:
            shell(SAMSUNG,['am','start','-a','android.settings.BLUETOOTH_SETTINGS'])
            time.sleep(.5)
            for scroll in range(7):
                path='/sdcard/wmp-samsung-bt-'+str(time.time_ns())+'.xml';dump=shell(SAMSUNG,['uiautomator','dump',path])
                if path not in dump:raise RuntimeError('Fresh Bluetooth UI dump failed; no stale coordinates used')
                tree=ET.fromstring(shell(SAMSUNG,['cat',path]));nodes=[n for n in tree.iter('node') if name in n.attrib.get('text','')]
                if nodes:break
                containers=[n for n in tree.iter('node') if n.attrib.get('scrollable')=='true' and n.attrib.get('package')=='com.android.settings']
                if len(containers)==1:bounds=containers[0].attrib['bounds']
                else:
                    roots=[n for n in tree.iter('node') if n.attrib.get('package')=='com.android.settings' and n.attrib.get('bounds')]
                    if not roots:raise RuntimeError('Bluetooth settings is not the active app')
                    def area(n):
                        x,y,z,w=map(int,re.findall(r'\d+',n.attrib['bounds']));return (z-x)*(w-y)
                    bounds=max(roots,key=area).attrib['bounds']
                a,b,c,d=map(int,re.findall(r'\d+',bounds));top=b+(d-b)//3;bottom=b+(d-b)*4//5
                begin,end=(top,bottom) if scroll<2 else (bottom,top)
                shell(SAMSUNG,['input','swipe',str((a+c)//2),str(begin),str((a+c)//2),str(end),'400']);time.sleep(.3)
            if len(nodes)!=1:raise RuntimeError('No unique saved '+port+' row')
            a,b,c,d=map(int,re.findall(r'\d+',nodes[0].attrib['bounds']));shell(SAMSUNG,['input','tap',str((a+c)//2),str((b+d)//2)]);r['saved_connect_action_sent']=True
            for _ in range(15):
                value=sample(port);r['connected']=value
                if value['lines']['WMP_BT']['phone']:break
                time.sleep(.4)
            else:raise TimeoutError('Normal saved-device connect did not complete; no action replayed')
        r['volume']=shell(SAMSUNG,['cmd','media_session','volume','--stream','3','--set','3','--get'])
        if not re.search(r'volume is 3 in range \[0\.\.15\]',r['volume']):raise RuntimeError('Samsung20% volume readback failed')
        shell(SAMSUNG,['am','start','-n','com.google.android.youtube/com.google.android.youtube.HomeActivity']);shell(SAMSUNG,['cmd','media_session','dispatch','play'])
        old=sample(port)['lines']['WMP_BT']['rtp']
        for _ in range(20):
            value=sample(port);r['after']=value;s=value['lines'];p=s['WMP_PEERS']
            if (p['classic_hi'],p['classic_lo'])!=(0x74f441e0,0xa5aa):raise RuntimeError('Classic peer is not the admitted Samsung')
            if s['WMP_BT']['rate'] and s['WMP_BT']['rtp']>old and s['WMP_BT']['streaming']:break
            time.sleep(.4)
        else:raise TimeoutError('Samsung did not deliver actual audio')
        r['volume_after_stream']=shell(SAMSUNG,['cmd','media_session','volume','--stream','3','--get'])
        if 'volume is 3 in range [0..15]' not in r['volume_after_stream']:
            # AVRCP route establishment can restore a remembered level after
            # the first volume request. Apply the authorized20% to the actual
            # active Bluetooth route, then independently read it back.
            r['volume_after_route_set']=shell(SAMSUNG,['cmd','media_session','volume','--stream','3','--set','3'])
            r['volume_after_route_readback']=shell(SAMSUNG,['cmd','media_session','volume','--stream','3','--get'])
            if 'volume is 3 in range [0..15]' not in r['volume_after_route_readback']:raise RuntimeError('Bluetooth-route volume is not20%')
        r['pass_all']=True
    except Exception as e:r['errors'].append(str(e))
    finally:save(out/'receipt.json',r)
    print(dict(pass_all=r['pass_all'],volume_percent=20,errors=r['errors']),flush=True)
    if not r['pass_all']:raise SystemExit(1)

if __name__=='__main__':main()
