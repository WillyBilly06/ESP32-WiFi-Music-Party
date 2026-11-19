"""Owned Linux adapter: bounded AFH assessment diagnostic with restoration.

No bond, connection, packet type, codec or service changes. One admitted ACL
must already be active. A detached instance restores the read-back original
assessment mode after the finite duration even if the Windows caller exits.
"""
import argparse,json,pathlib,re,subprocess,time,os,socket,struct

ADAPTER='E8:47:3A:EC:51:9A';PEER='6C:C8:40:42:BF:66'

def run(args):
    r=subprocess.run(args,capture_output=True,text=True,timeout=8)
    if r.returncode:raise RuntimeError(str(args[:3])+': '+r.stderr.strip())
    return r.stdout

def guard(adapter):
    if not re.fullmatch(r'hci\d+',adapter):raise ValueError('Invalid adapter')
    info=run(['hciconfig',adapter])
    if not re.search(r'BD Address:\s*'+ADAPTER,info):raise RuntimeError('Physical adapter changed')
    text=run(['hcitool','con']);peers=re.findall(r'ACL\s+([0-9A-Fa-f:]{17})\s+handle\s+(\d+)',text)
    if len(peers)!=1 or peers[0][0].upper()!=PEER:raise RuntimeError('Need only admitted active ACL')
    return int(peers[0][1])

def completion(packet,opcode):
    if len(packet)<3 or packet[0]!=4:return None
    if packet[1]==15 and len(packet)>=7 and int.from_bytes(packet[5:7],'little')==opcode:
        if packet[3]:raise RuntimeError('Controller rejected command status='+str(packet[3]))
        return None
    if packet[1]!=14 or len(packet)<7 or int.from_bytes(packet[4:6],'little')!=opcode:return None
    if packet[2]!=len(packet)-3:raise RuntimeError('Truncated matching Command Complete')
    if packet[6]:raise RuntimeError('Controller rejected command status='+str(packet[6]))
    return packet[7:]

def command(adapter,ogf,ocf,payload=b''):
    opcode=ogf<<10|ocf;index=int(adapter[3:])
    with socket.socket(socket.AF_BLUETOOTH,socket.SOCK_RAW,socket.BTPROTO_HCI) as channel:
        channel.bind((index,))
        # Kernel opcode filter plus explicit matching below: asynchronous
        # events cannot satisfy or abort another command's restoration.
        channel.setsockopt(0,2,struct.pack('<IIIH2x',1<<4,(1<<14)|(1<<15),0,opcode))
        channel.settimeout(3);packet=bytes([1])+struct.pack('<HB',opcode,len(payload))+payload
        if channel.send(packet)!=len(packet):raise RuntimeError('Short command write; outcome uncertain')
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            channel.settimeout(max(.01,deadline-time.monotonic()))
            result=completion(channel.recv(260),opcode)
            if result is not None:return result
        raise TimeoutError('Matching Command Complete absent; outcome uncertain')

def assessment(adapter):
    value=command(adapter,3,0x48)
    if len(value)!=1 or value[0]>1:raise RuntimeError('Assessment mode invalid')
    return value[0]

def snapshot(adapter,handle):
    data=command(adapter,5,6,handle.to_bytes(2,'little'))
    if len(data)!=13 or int.from_bytes(data[:2],'little')!=handle:raise RuntimeError('AFH map handle/length mismatch')
    return dict(at=time.time(),mode=data[2],map_octets=data[3:].hex(),reported_used_channels=sum(v.bit_count() for v in data[3:]),handle=handle)

def main():
    global PEER
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--adapter',default='hci0');p.add_argument('--port',choices=['COM6','COM7'],default='COM6');p.add_argument('--seconds',type=int,default=75);p.add_argument('--sample-interval',type=int,default=1);p.add_argument('--directory',type=pathlib.Path,required=True);a=p.parse_args()
    PEER={'COM6':'6C:C8:40:42:BF:66','COM7':'08:B6:1F:B8:66:26'}[a.port]
    if not 30<=a.seconds<=7200 or not 1<=a.sample_interval<=10:p.error('Finite30..7200seconds; sampling1..10seconds')
    base=pathlib.Path('/home/billydakidz/wmp-linux-source').resolve();out=a.directory.resolve()
    if out.parent!=base or not out.name.startswith('afh-assessment-'):raise RuntimeError('Unowned output directory')
    out.mkdir(exist_ok=False);(out/'probe.py').write_bytes(pathlib.Path(__file__).read_bytes());row=dict(pid=os.getpid(),adapter=ADAPTER,peer=PEER,samples=[],sample_count=0,errors=[],restored=False,diagnostic_only=True,audio_qualified=False)
    def save():
        row['updated']=time.time();tmp=out/'status.tmp';tmp.write_text(json.dumps(row,indent=2));tmp.replace(out/'status.json')
    original=None;attempted=False
    try:
        handle=guard(a.adapter);original=assessment(a.adapter);row['original_mode']=original;row['before']=snapshot(a.adapter,handle);save()
        if original!=1:raise RuntimeError('Expected known enabled assessment; no change made')
        attempted=True;command(a.adapter,3,0x49,b'\x00');row['readback_mode']=assessment(a.adapter)
        if row['readback_mode']!=0:raise RuntimeError('Disable not independently observed')
        row['active']=True;save();start=time.monotonic()
        while time.monotonic()-start<a.seconds and not (out/'stop').exists():
            if guard(a.adapter)!=handle:raise RuntimeError('Link handle changed')
            sample=snapshot(a.adapter,handle);row['samples'].append(sample);row['sample_count']+=1
            if len(row['samples'])>120:row['samples'].pop(0)
            with (out/'samples.jsonl').open('a') as log:log.write(json.dumps(sample)+'\n')
            save();wake=time.monotonic()+a.sample_interval
            while time.monotonic()<wake and not (out/'stop').exists():time.sleep(min(.25,max(0,wake-time.monotonic())))
    except Exception as error:row['errors'].append(repr(error))
    finally:
        if attempted:
            try:
                if assessment(a.adapter)!=original:command(a.adapter,3,0x49,bytes([original]))
                row['restored_mode']=assessment(a.adapter);row['restored']=row['restored_mode']==original
            except Exception as error:row['errors'].append('restore '+repr(error))
        row.update(active=False,finished=time.time());save();print(json.dumps(row),flush=True)
    if row['errors'] or attempted and not row['restored']:raise SystemExit(1)

if __name__=='__main__':main()
