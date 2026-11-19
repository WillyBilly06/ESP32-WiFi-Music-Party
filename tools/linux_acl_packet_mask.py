"""Owned Linux adapter per-link diagnostic for admitted COM6 only.
Requires root. The adapter is located by its MAC (E8:47:3A:EC:51:9A); an explicit
--adapter hciN is MAC-checked. Default restores the all-BR/EDR baseline; --mask0xee1c
(with a space) excludes3Mbps EDR formats. --restore writes back the baseline mask saved
in the reference file. Acceptance does not prove exact on-air packet type.
"""
import argparse,socket,struct,subprocess,re,time,json
from pathlib import Path
ADDRESS='E8:47:3A:EC:51:9A';TARGET='6C:C8:40:42:BF:66'
def locate(name):
 """Return the hciN whose BD Address is the admitted adapter; the index is not stable."""
 found={}
 for block in re.split(r'(?m)^(?=hci\d+:)',subprocess.check_output(['hciconfig'],text=True)):
  m=re.match(r'(hci\d+):',block);a=re.search(r'BD Address:\s*([0-9A-Fa-f:]{17})',block)
  if m and a:found[m.group(1)]=a.group(1).upper()
 if name=='auto':name=next((k for k,v in found.items() if v==ADDRESS),None)
 if not name or not re.fullmatch(r'hci\d+',name) or found.get(name)!=ADDRESS:raise RuntimeError('Admitted adapter '+ADDRESS+' not present at '+str(name)+': '+json.dumps(found))
 return name
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--adapter',default='auto');parser.add_argument('--mask',type=lambda v:int(v,0),default=0xcc18);parser.add_argument('--restore',action='store_true',help='Write back the baseline mask saved in the reference file');args=parser.parse_args()
p=Path('/home/billydakidz/wmp-linux-source/packet-type-reference.json')
if args.restore:
 if not p.exists():raise RuntimeError('No saved packet-type reference; nothing to restore')
 ref=json.loads(p.read_text())
 if ref.get('adapter')!=ADDRESS or ref.get('target')!=TARGET:raise RuntimeError('Reference belongs to another adapter/target')
 mask=int(ref['requested_baseline_mask'])
else:mask=args.mask
if mask not in (0xcc18,0xee1c):raise RuntimeError('Only baseline or3Mbps-excluded masks are admitted')
adapter=locate(args.adapter)
line=next(x for x in subprocess.check_output(['hcitool','-i',adapter,'con'],text=True).splitlines() if TARGET in x.upper());handle=int(re.search(r'handle (\d+)',line).group(1))
s=socket.socket(socket.AF_BLUETOOTH,socket.SOCK_RAW,socket.BTPROTO_HCI);s.bind((int(adapter[3:]),));s.setsockopt(0,2,struct.pack('<IIIH2x',16,(1<<14)|(1<<15)|(1<<29),0,0));s.settimeout(6)
if not p.exists():p.write_text(json.dumps({'target':TARGET,'adapter':ADDRESS,'requested_baseline_mask':0xcc18,'at':time.time()}))
params=struct.pack('<HH',handle,mask);s.send(bytes([1,15,4,len(params)])+params);deadline=time.monotonic()+8
while time.monotonic()<deadline:
 data=s.recv(260)
 if len(data)>=7 and data[0]==4 and data[1]==15 and int.from_bytes(data[5:7],'little')==0x040f:
  print('command_status',data[3],flush=True)
  if data[3]:raise RuntimeError('Controller rejected packet type change')
 if len(data)>=8 and data[0]==4 and data[1]==29 and int.from_bytes(data[4:6],'little')==handle:
  print('packet_type_changed',{'adapter':adapter,'status':data[3],'handle':handle,'reported_mask':int.from_bytes(data[6:8],'little'),'requested_mask':mask,'restore':args.restore},flush=True)
  if data[3]:raise RuntimeError('Packet type change failed status='+str(data[3]))
  break
else:raise TimeoutError('No terminal packet-type event')
s.close()
