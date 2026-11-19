from local_ipc import connection as task_connection
"""Paired copy-stage evidence. TX completion is not an RF timestamp or ACK."""
import concurrent.futures,json,re,secrets,socket,subprocess,time,math,statistics,struct,zlib
from pathlib import Path
from board_io import ROOT,save

def command(port,line,reply):
    active=ROOT/'hardware/active-session.json'
    state=json.loads(active.read_text()) if active.exists() else {}
    if state and state.get('state')!='running':raise RuntimeError('Owned capture is stopped; start it explicitly. UART will not be reopened by a diagnostic.')
    if state.get('state')=='running':
        if port not in state['ports']:raise RuntimeError('Port absent from current owned capture')
        request=dict(token=state['token'],action='command',port=port,command=line,marker=reply)
        with task_connection(('127.0.0.1',state['control_port']),timeout=30) as s:
            s.sendall((json.dumps(request)+'\n').encode())
            r=json.loads(s.makefile('rb').readline(262145))
        if not r.get('ok'):raise RuntimeError(r)
        return dict(pass_all=True,reply=r['reply'],evidence=str(Path(state['evidence'])/(port+'.raw')))
    p=subprocess.run(['python',str(ROOT/'tools/runtime.py'),'--port',port,'--command',line,'--reply',reply],cwd=ROOT,capture_output=True,text=True,timeout=35)
    if p.returncode:raise RuntimeError(p.stdout+p.stderr)
    r=json.loads(p.stdout)
    if not r['pass_all']:raise RuntimeError(r)
    return r

def parse(r,nonce,require_crc=False):
    meta={};rows={};end=None
    for line in r['reply'].splitlines():
        if not line.startswith(('WMP_TRACE_META ','WMP_TRACE ','WMP_TRACE_END ')):continue
        values=dict(re.findall(r'(\w+)=([^\s]+)',line))
        if values.get('query')!=nonce:raise RuntimeError('Stale trace response')
        if line.startswith('WMP_TRACE_META '):meta=values
        elif line.startswith('WMP_TRACE '):
            row={k:(list(map(int,v.split(','))) if k in ['a','b'] else int(v)) for k,v in values.items() if k!='query'}
            if not {'seq','raw','accepted','done','failed','repaired','a','b'}.issubset(row) or len(row['a'])!=4 or len(row['b'])!=4:
                raise RuntimeError('Incomplete copy trace row')
            if meta.get('row_crc')=='1':
                if not {'pts','injected','crc'}.issubset(row):raise RuntimeError('Missing trace CRC fields')
                packed=struct.pack('<QQ8I7B',row['seq'],row['pts'],*row['a'],*row['b'],row['raw'],row['accepted'],row['done'],row['failed'],row['repaired'],1,row['injected'])
                if meta.get('route_bits')=='1':
                    if 'route' not in row:raise RuntimeError('Routing journal absent')
                    packed+=struct.pack('B',row['route'])
                if zlib.crc32(packed)!=row['crc']:raise RuntimeError('Corrupted trace row CRC')
            rows[row['seq']]=row
        elif line.startswith('WMP_TRACE_END '):end=values
    if not meta or not rows or not end:raise RuntimeError('No complete populated copy trace')
    if require_crc and meta.get('row_crc')!='1':raise RuntimeError('Missing required trace CRC capability')
    if int(end.get('rows',-1))!=len(rows) or end.get('allocation_failed')=='1':raise RuntimeError('Truncated or allocation-failed copy trace')
    return dict(meta=meta,rows=rows,evidence=r['evidence'])

def join_traces(tx,rx,copies,settle_groups=12):
    if copies not in (3,4):raise ValueError('Expected three or four original copies')
    if tx['meta']['kind']!='1' or rx['meta']['kind']!='2' or tx['meta']['generation']!=rx['meta']['generation']:
        raise RuntimeError('Trace roles or stream generations differ')
    # Keep away from both moving capture fronts. Missing means no copy was
    # observed in this bounded trace, never proof of unlimited-time RF loss.
    ceiling=min(int(tx['meta']['highest']),int(rx['meta']['highest']))-settle_groups
    floor=max(0,int(tx['meta']['highest'])-63,int(rx['meta']['highest'])-63,min(tx['rows']),min(rx['rows']))
    if floor>ceiling:raise RuntimeError('No overlapping settled trace coverage')
    joined=[]
    for seq in sorted(tx['rows']):
        if not floor<=seq<=ceiling:continue
        a=tx['rows'][seq];b=rx['rows'].get(seq,dict(raw=0,accepted=0,repaired=0,a=[0]*4,b=[0]*4))
        if (a['raw']|b['raw'])&~((1<<copies)-1):raise RuntimeError('Mixed copy configuration in trace window')
        def gaps(stamps,mask):
            seen=[(i,v) for i,v in enumerate(stamps) if mask&(1<<i)]
            return [{'from':i,'to':j,'us':((w-v+0x80000000)&0xffffffff)-0x80000000} for (i,v),(j,w) in zip(seen,seen[1:])]
        joined.append(dict(sequence=seq,tx_attempt=a['raw'],tx_accepted=a['accepted'],tx_completed=a['done'],tx_failed=a['failed'],
                           rx_callback=b['raw'],rx_integrity_valid=b['accepted'],full_packet_replay=b['repaired'],rx_route=b.get('route'),
                           received_copies=[i for i in range(copies) if b['raw']&(1<<i)],missing_copies=[i for i in range(copies) if not b['raw']&(1<<i)],
                           tx_submit_gaps=gaps(a['a'],a['raw']),tx_callback_gaps=gaps(a['b'],a['done']),rx_mac_gaps=gaps(b['a'],b['raw']),
                           tx_callback_delay_us=[((a['b'][i]-a['a'][i])&0xffffffff) if a['done']&(1<<i) else None for i in range(4)]))
    if not joined:raise RuntimeError('No settled sender rows inside common coverage')
    return joined,dict(first=floor,last=ceiling)

def presentation_slack(joined,tx,source_state):
    phy=source_state['lines']['WMP_PHY']
    if not phy['source_tsf']:return
    # Unwrap each recent32-bit ESP timestamp around this same-board64-bit
    # sample. This estimates application submission headroom, not RF airtime.
    local=phy['clock_local_us'];source=phy['source_tsf']
    def mapped(stamp):return source+(((stamp-(local&0xffffffff)+0x80000000)&0xffffffff)-0x80000000)
    for row in joined:
        packet=tx['rows'][row['sequence']];pts=packet.get('pts',0)
        if not pts:continue
        row['tx_submit_headroom_us']=[pts-mapped(stamp) if packet['raw']&(1<<i) else None for i,stamp in enumerate(packet['a'])]
        row['tx_callback_headroom_us']=[pts-mapped(stamp) if packet['done']&(1<<i) else None for i,stamp in enumerate(packet['b'])]

def capture(source,receiver,out):
    from sweep_live_pacing import sample
    owned=[];controls=[]
    try:
        for port in (source,receiver):
            if not sample(port)['lines']['WMP_RADIO_RESOURCES'].get('history_bytes',0):
                result=command(port,'COPYLOG 1','WMP_TRACE_RESOURCE');controls.append(dict(port=port,action='enable',result=result))
                if 'accepted=1' not in result['reply']:raise RuntimeError('Copy history allocation declined: '+port)
                owned.append(port)
        if owned:time.sleep(1)
        return _capture(source,receiver,out)
    finally:
        errors=[]
        for port in owned:
            try:
                result=command(port,'COPYLOG 0','WMP_TRACE_RESOURCE');controls.append(dict(port=port,action='disable',result=result))
                if 'accepted=1' not in result['reply']:raise RuntimeError('Copy history release declined: '+port)
            except Exception as error:errors.append(port+': '+str(error))
        save(out/'trace-resource-controls.json',controls)
        if errors:raise RuntimeError('Copy history cleanup failed: '+'; '.join(errors))

def _capture(source,receiver,out):
    # Read the configured copy count, rather than inferring it from completed
    # packets: an unsent fourth copy must not be relabeled a three-copy run.
    from sweep_live_pacing import sample
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jobs={p:pool.submit(sample,p) for p in [source,receiver]}
        state={p:f.result() for p,f in jobs.items()}
    copies=state[source]['lines']['WMP_RADIO']['copies'];mask=(1<<copies)-1
    native=bool(state[receiver]['lines']['WMP_ETH']['enabled'] or state[receiver]['lines'].get('WMP_NATIVE_RX',{}).get('active') or state[receiver]['lines'].get('WMP_DIRECT_RX',{}).get('enabled'))
    nonce=secrets.token_hex(4)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jobs={p:pool.submit(command,p,'TRACE '+nonce,'WMP_TRACE_END') for p in [source,receiver]}
        replies={p:f.result() for p,f in jobs.items()}
    for p,reply in replies.items():(out/(p+'.raw')).write_text(reply['reply'])
    try:traces={p:parse(reply,nonce,require_crc=True) for p,reply in replies.items()}
    except Exception as error:
        save(out/'receipt.json',dict(pass_all=False,source=source,receiver=receiver,nonce=nonce,state=state,error=repr(error),stage='trace-parse'))
        raise
    tx,rx=traces[source],traces[receiver]
    records=sorted((seq,v['pts']) for seq,v in tx['rows'].items() if v.get('pts'))
    periods=[(y-b)/(x-a) for (a,b),(x,y) in zip(records,records[1:]) if x>a and y>b]
    group_us=statistics.median(periods) if periods else 512000000/state[source]['lines']['WMP_BT']['rate']
    span=(copies-1)*state[source]['lines']['WMP_COPY']['spacing']
    settle=max(12,math.ceil((span+100000)/group_us)+2)
    try:joined,coverage=join_traces(tx,rx,copies,settle)
    except Exception as error:
        save(out/'receipt.json',dict(pass_all=False,source=source,receiver=receiver,nonce=nonce,state=state,error=repr(error),stage='trace-coverage'))
        raise
    presentation_slack(joined,tx,state[source])
    row=dict(pass_all=True,acquisition_complete=True,audio_qualified=False,source=source,receiver=receiver,nonce=nonce,traces=traces,joined=joined,
             configured_copies=copies,expected_mask=mask,coverage=coverage,state=state,
             settling_groups=settle,estimated_group_us=group_us,
             all_expected_completed_but_none_observed=[v['sequence'] for v in joined if v['tx_accepted']==mask and v['tx_completed']==mask and v['rx_callback']==0],
             receiver_timestamp_kind='callback_mac_clock' if native else 'packet_capture_mac_clock',
             limits=['Callback timestamps are not exact TX RF timestamps','Native RX samples the MAC clock at callback time; it is not an RF capture','Headroom uses a nearby same-board clock mapping; it is not acoustic latency',
                     'A missing native callback cannot distinguish RF unavailability from internal driver loss',
                     'Trace covers a bounded moving window; not acoustic or latency qualification'])
    save(out/'receipt.json',row);return row

def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',choices=['COM6','COM7'],default='COM7');a=p.parse_args()
    receiver='COM6' if a.source=='COM7' else 'COM7';out=ROOT/'evidence'/('copies-'+str(time.time_ns()));out.mkdir()
    (out/'capture.py').write_bytes(Path(__file__).read_bytes());r=capture(a.source,receiver,out)
    interesting=[x for x in r['joined'] if x['rx_callback']!=r['expected_mask'] or x['tx_completed']!=r['expected_mask']]
    print(json.dumps(dict(evidence=str(out),matched=len(r['joined']),interesting=interesting[:12]),indent=2))
if __name__=='__main__':main()
