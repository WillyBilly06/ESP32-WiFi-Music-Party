"""Validate the exact ASCII STAT body after normalizing UART CR/LF endings."""
import re,zlib

def validate(reply,nonce):
    lines=reply.splitlines();query=int(nonce,16)
    def fields(line):return {k:int(v,16 if k=='query' else 10) for k,v in re.findall(r'(\w+)=([0-9a-f]+)(?:\s|$)',line)}
    begins=[(i,fields(l)) for i,l in enumerate(lines) if l.startswith('WMP_STAT_BEGIN ') and fields(l).get('query')==query]
    ends=[(i,fields(l)) for i,l in enumerate(lines) if l.startswith('WMP_STAT_CRC ') and fields(l).get('query')==query]
    if len(begins)!=1 or len(ends)!=1:raise ValueError('STAT integrity envelope missing or duplicated')
    begin,b=begins[0];end,e=ends[0]
    if begin>=end or b.get('schema')!=2 or e.get('valid')!=1 or e.get('allocation_failed')!=0:raise ValueError('STAT firmware integrity/format/allocation failure')
    body=''.join(l+'\n' for l in lines[begin+1:end] if l.startswith('WMP_') and re.search(r'\bquery='+re.escape(nonce)+r'(?:\s|$)',l)).encode('ascii')
    if not any(l.startswith('WMP_STAT_END ') for l in body.decode().splitlines()):raise ValueError('STAT body missing terminal row')
    if len(body)!=e.get('bytes') or zlib.crc32(body)!=e.get('crc'):raise ValueError('STAT body byte count or CRC mismatch')
    return dict(schema=2,bytes=len(body),crc=e['crc'])
