"""Owned Linux adapter page-timeout setup with MAC, connection and readback guards.
The adapter is located by its MAC (E8:47:3A:EC:51:9A); an explicit --adapter hciN is
MAC-checked. Previous setting is saved under the owned Linux test folder; --restore
writes it back and verifies the readback. No other adapter is changed.
"""
import argparse,socket,struct,time,subprocess,re,json
from pathlib import Path
ADDRESS='E8:47:3A:EC:51:9A'
def locate(name):
 """Return the hciN whose BD Address is the admitted adapter; the index is not stable."""
 found={}
 for block in re.split(r'(?m)^(?=hci\d+:)',subprocess.check_output(['hciconfig'],text=True)):
  m=re.match(r'(hci\d+):',block);a=re.search(r'BD Address:\s*([0-9A-Fa-f:]{17})',block)
  if m and a:found[m.group(1)]=a.group(1).upper()
 if name=='auto':name=next((k for k,v in found.items() if v==ADDRESS),None)
 if not name or not re.fullmatch(r'hci\d+',name) or found.get(name)!=ADDRESS:raise RuntimeError('Admitted adapter '+ADDRESS+' not present at '+str(name)+': '+json.dumps(found))
 return name
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--adapter',default='auto');parser.add_argument('--restore',action='store_true',help='Write back the saved previous page timeout');args=parser.parse_args()
adapter=locate(args.adapter);path=Path('/home/billydakidz/wmp-linux-source/page-timeout-reference.json')
if args.restore:
 if not path.exists():raise RuntimeError('No saved page-timeout reference; nothing to restore')
 ref=json.loads(path.read_text())
 if ref.get('address')!=ADDRESS:raise RuntimeError('Reference belongs to another adapter')
 target=int(ref['previous_units'])
 if not 1<=target<=0xffff:raise RuntimeError('Invalid saved page timeout')
else:target=0x8000
sock=socket.socket(socket.AF_BLUETOOTH,socket.SOCK_RAW,socket.BTPROTO_HCI);sock.bind((int(adapter[3:]),));sock.setsockopt(0,2,struct.pack('<IIIH2x',1<<4,(1<<14)|(1<<15),0,0));sock.settimeout(5)
def request(op,params=b''):
 start=time.monotonic();sock.send(bytes([1,op&255,op>>8,len(params)])+params)
 while True:
  p=sock.recv(260)
  if len(p)>=7 and p[0]==4 and p[1]==14 and int.from_bytes(p[4:6],'little')==op:
   if p[6]:raise RuntimeError('command status '+str(p[6]))
   return p[7:],round((time.monotonic()-start)*1000,2)
old,delay=request(0x0c17);print('adapter',adapter,'page_timeout_units',int.from_bytes(old,'little'),'response_ms',delay,flush=True)
if not args.restore:
 peers=subprocess.check_output(['hcitool','-i',adapter,'con'],text=True)
 if re.search('[0-9A-Fa-f]{2}:',peers):raise RuntimeError('Active connection present; page timeout unchanged')
 if not path.exists():path.write_text(json.dumps({'address':ADDRESS,'previous_units':int.from_bytes(old,'little'),'at':time.time()}))
_,delay=request(0x0c18,struct.pack('<H',target));value,delay2=request(0x0c17)
print('page_timeout_now_units',int.from_bytes(value,'little'),'write_ms',delay,'read_ms',delay2,'restore',args.restore,flush=True);sock.close()
if int.from_bytes(value,'little')!=target:raise RuntimeError('Page timeout readback mismatch')
