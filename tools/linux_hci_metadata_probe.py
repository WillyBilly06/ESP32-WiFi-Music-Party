"""Read-only Linux adapter metadata probe for admitted COM6; run on owned guest as root.
The adapter is located by its MAC (E8:47:3A:EC:51:9A); an explicit --adapter hciN is
MAC-checked. No audio payload or link key is written. Results are a bounded host boundary observation.
"""
import argparse,socket,struct,time,subprocess,re,json
parser=argparse.ArgumentParser();parser.add_argument('--adapter',default='auto');args=parser.parse_args()
found={}
for block in re.split(r'(?m)^(?=hci[0-9]+:)',subprocess.check_output(['hciconfig'],text=True)):
 m=re.match(r'(hci[0-9]+):',block);a=re.search(r'BD Address:\s*([0-9A-Fa-f:]{17})',block)
 if m and a:found[m.group(1)]=a.group(1).upper()
if args.adapter=='auto':args.adapter=next((k for k,v in found.items() if v=='E8:47:3A:EC:51:9A'),'')
if not re.fullmatch('hci[0-9]+',args.adapter):raise RuntimeError('Admitted adapter not found / invalid HCI interface')
if found.get(args.adapter)!='E8:47:3A:EC:51:9A':raise RuntimeError('Unadmitted Bluetooth adapter')
index_expected=int(args.adapter[3:])
text=subprocess.check_output(['hcitool','-i',args.adapter,'con'],text=True);line=next(x for x in text.splitlines() if '6C:C8:40:42:BF:66' in x.upper());handle=int(re.search(r'handle (\d+)',line).group(1))
s=socket.socket(socket.AF_BLUETOOTH,socket.SOCK_RAW,socket.BTPROTO_HCI);s.bind((65535,2));s.settimeout(.5)
end=time.monotonic()+8;sequences=[];gaps=0;completed=0;events=[];part=bytearray();need=0
while time.monotonic()<end:
 try:p=s.recv(65536)
 except socket.timeout:continue
 if len(p)<6:continue
 op,index,n=struct.unpack_from('<HHH',p);data=p[6:]
 if index!=index_expected:continue
 if op==3 and len(data)>=3 and data[0]==0x13:
  for i in range(data[2]):
   if len(data)<3+4*(i+1):break
   h,count=struct.unpack_from('<HH',data,3+4*i)
   if h==handle:completed+=count;events.append(time.monotonic())
 if op!=4 or len(data)<4:continue
 flags,length=struct.unpack_from('<HH',data)
 if flags&4095!=handle:continue
 chunk=data[4:4+length];pb=(flags>>12)&3
 if pb!=1:
  if len(chunk)<4:continue
  total,cid=struct.unpack_from('<HH',chunk);need=total;part=bytearray(chunk[4:])
 else:part.extend(chunk)
 if not need or len(part)<need:continue
 body=bytes(part[:need]);need=0;part.clear()
 if len(body)<14 or body[0]!=0x80 or body[13]!=0x9c:continue
 seq=int.from_bytes(body[2:4],'big')
 if sequences:
  d=(seq-sequences[-1])&65535
  if 1<d<32768:gaps+=d-1
 sequences.append(seq)
s.close();print(json.dumps({'seconds':8,'rtp_packets_at_hci_tx':len(sequences),'hci_tx_rtp_sequence_gaps':gaps,'first_seq':sequences[0] if sequences else None,'last_seq':sequences[-1] if sequences else None,'completed_acl':completed,'completion_events':len(events),'max_completion_gap_ms':max([(b-a)*1000 for a,b in zip(events,events[1:])],default=0)}))

