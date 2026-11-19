"""Retained one-identity delivery evidence; no audio payload is recorded."""
import re,secrets,struct,zlib
from capture_copies import command

def parse(reply,nonce):
    meta=None;tail=None;rows=[]
    for line in reply.splitlines():
        if not line.startswith(('WMP_DELIVERY_META ','WMP_DELIVERY ','WMP_DELIVERY_END ')):continue
        fields=dict(re.findall(r'(\w+)=(-?[0-9a-f]+)(?:\s|$)',line))
        if fields.pop('query',None)!=nonce:raise ValueError('Delivery nonce mismatch')
        values={k:int(v) for k,v in fields.items()}
        if line.startswith('WMP_DELIVERY_META '):meta=values
        elif line.startswith('WMP_DELIVERY_END '):tail=values
        else:
            if not {'index','stage','copy','flags','tag','at_us32','status','crc'}<=values.keys():raise ValueError('Incomplete delivery event')
            packed=struct.pack('<IIiBBB',values['at_us32'],values['tag'],values['status'],values['stage'],values['copy'],values['flags'])
            if zlib.crc32(packed)!=values['crc']:raise ValueError('Delivery event CRC mismatch')
            if values['index']!=len(rows):raise ValueError('Missing or duplicated delivery event')
            rows.append(values)
    if tail is None or tail.get('allocation_failed'):raise ValueError('Delivery snapshot incomplete/allocation failed')
    if tail.get('count')!=len(rows):raise ValueError('Truncated delivery snapshot')
    if tail.get('loaded') and (meta is None or meta.get('overflow')):raise ValueError('Delivery metadata absent/overflow')
    return dict(meta=meta,rows=rows,tail=tail,raw=reply)

def read(port):
    nonce=secrets.token_hex(4);r=command(port,'DELIVERYTRACE '+nonce,'WMP_DELIVERY_END query='+nonce)
    return parse(r['reply'],nonce)

def stable(port):
    first=read(port);second=read(port)
    if any(first[k]!=second[k] for k in ('meta','rows','tail')):raise ValueError('Selected identity still changing across fresh reads')
    return second

def stages(trace,stage,replay=None,status=None):
    return [r for r in trace['rows'] if r['stage']==stage and (replay is None or bool(r['flags']&1)==replay) and (status is None or r['status']==status)]

def control_state(value):
    return dict(type=value&15,mode=(value>>4)&15,replay_enabled=bool(value&(1<<8)),transition_pending=bool(value&(1<<9)),signature=[(value>>10)&255,(value>>18)&255],payload_length_capped=(value>>26)&15)
