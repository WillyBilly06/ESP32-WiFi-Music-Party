"""Bounded, full A2DP/BLE/music test of native connectionless wake windows."""
import argparse,concurrent.futures,json,re,secrets,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair
from audio_fixture import FIXTURES,require as require_fixture,check_sources
from qualify_party_long import PHONES,peer,media
from firmware_evidence import installed_pair,live_pair

def wake(port):
    nonce=secrets.token_hex(4);r=command(port,'WAKESTAT '+nonce,'WMP_WAKE query='+nonce)
    lines=[line for line in r['reply'].splitlines() if line.startswith('WMP_WAKE ')]
    if len(lines)!=1:raise RuntimeError('Missing/ambiguous wake snapshot')
    fields=dict(re.findall(r'(\w+)=([0-9a-f]+)(?:\s|$)',lines[0]))
    if fields.pop('query',None)!=nonce:raise RuntimeError('Wake nonce mismatch')
    v={k:int(value) for k,value in fields.items()}
    if not {'events','local_us','source_tsf','age_us','tx_span_us','interval_ms','window_ms'}<=v.keys():raise RuntimeError('Wake fields incomplete')
    return v

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',type=Path,required=True);p.add_argument('--fixture',choices=FIXTURES,default='redmagic-linux');p.add_argument('--seconds',type=int,default=60);p.add_argument('--interval',type=int,default=20);p.add_argument('--source-window',type=int,default=6);p.add_argument('--receiver-window',type=int,default=14);p.add_argument('--tx-span',type=int,default=2500);a=p.parse_args()
    if not 30<=a.seconds<=180 or not 10<=a.interval<=1000 or not 0<a.source_window<a.receiver_window<a.interval or not 0<a.tx_span<a.source_window*1000:p.error('Bounded duration and source-window < receiver-window < interval; TX span within source window')
    out=ROOT/'evidence'/('wake-windows-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes());print(out,flush=True)
    row=dict(controls=[],wake_samples=[],samples=[],errors=[],acquisition_complete=False,audio_qualified=False,diagnostic_only=True);changed=[];source=None;original={}
    def control(port,line,marker):
        r=command(port,line,marker);row['controls'].append(dict(port=port,line=line,result=r));save(out/'receipt.json',row)
        if 'accepted=1' not in r['reply'] and 'status=0' not in r['reply']:raise RuntimeError('Wake control rejected '+port+' '+line)
    try:
        row['fixture']=require_fixture(a.fixture);row['binding']=installed_pair(a.app);row['live']=live_pair(row['binding'],command)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            before=pair(pool);leaders=[port for port,v in before.items() if v['lines']['WMP_RADIO']['mode']==2]
            if len(leaders)!=1:raise RuntimeError('Need one active source')
            source=leaders[0];receiver=next(port for port in before if port!=source);row.update(source=source,receiver=receiver,before=before)
            phones=[PHONES[peer(v['lines'])] for v in before.values()];check_sources(row['fixture'],phones);row['media_before']=[media(phone) for phone in phones]
            if any(v.get('playback_state',v.get('youtube_state')) not in ('PLAYING','3') for v in row['media_before']):raise RuntimeError('Actual music absent')
            for port,v in before.items():
                s=v['lines'];original[port]=dict(window=s['WMP_RECEIVE_WINDOW']['window_ms'],interval=s['WMP_RECEIVE_WINDOW']['interval_ms'])
                if not all(s['WMP_BT'][k] for k in ('phone','streaming','ble')) or not s['WMP_AUDIO']['running'] or not s['WMP_ASSOC']['connected']:raise RuntimeError('Full fixture absent '+port)
                if s['WMP_RADIO']['copies']!=4 or s['WMP_WAKE']['tx_span_us']:raise RuntimeError('Need four copies and initially disabled gate')
            for port,window in [(receiver,a.receiver_window),(source,a.source_window)]:
                changed.append(port)
                control(port,'RXWINDOWMS '+str(window),'WMP_RECEIVE_SIZE_REQUEST')
                control(port,'RXINTERVAL '+str(a.interval),'WMP_RECEIVE_INTERVAL')
            deadline=time.monotonic()+1.5;aligned=[]
            while True:
                futures={port:pool.submit(wake,port) for port in before};w={port:f.result() for port,f in futures.items()};row['wake_samples'].append(dict(phase='admission',ports=w));save(out/'receipt.json',row)
                if all(v['events']>=3 and v['source_tsf'] and v['age_us']<a.interval*2000 for v in w.values()):
                    period=a.interval*1000;phase=(w[source]['source_tsf']-w[receiver]['source_tsf'])%period
                    row['wake_samples'][-1]['source_phase_in_receiver_period_us']=phase
                    # Compare the complete TX submission span with the wider
                    # observed receiver window. Equal callback times are not
                    # required. Leave2ms at its closing edge for uncertainty;
                    # these are event-dispatch timestamps, not RF captures.
                    if phase+a.tx_span+2000<=a.receiver_window*1000:aligned.append(phase)
                    else:aligned=[]
                    if len(aligned)>=3:break
                if time.monotonic()>deadline:raise RuntimeError('Native wake events missing/stale; gate never enabled')
                time.sleep(.05)
            row['admitted_source_phase_us']=aligned
            control(source,'TXWAKE '+str(a.tx_span),'WMP_TX_WAKE_REQUEST');row['gate_readback']=wake(source)
            if row['gate_readback']['tx_span_us']!=a.tx_span:raise RuntimeError('Gate not applied')
            first=pair(pool);start=time.monotonic();generation=first[source]['lines']['WMP_SESSION']['generation']
            while True:
                ports=first if not row['samples'] else pair(pool);elapsed=time.monotonic()-start;row['samples'].append(dict(elapsed=elapsed,ports=ports));save(out/'receipt.json',row)
                for port,v in ports.items():
                    s=v['lines'];base=before[port]['lines']
                    if not all(s['WMP_BT'][k] for k in ('phone','streaming','ble')) or not s['WMP_AUDIO']['running'] or s['WMP_SESSION']['generation']!=generation or s['WMP_RADIO']['mode']!=base['WMP_RADIO']['mode']:raise RuntimeError('Workload/role/session disappeared '+port)
                    if s['WMP_RADIO']['copies']!=4 or s['WMP_SOURCE_QUEUE']['full']!=base['WMP_SOURCE_QUEUE']['full'] or s['WMP_BT']['blocked_frames']!=base['WMP_BT']['blocked_frames']:raise RuntimeError('Original data path stopped/dropped '+port)
                    if s['WMP_BT_LINKS']['classic_disconnects']!=base['WMP_BT_LINKS']['classic_disconnects'] or s['WMP_BT_LINKS']['ble_disconnects']!=base['WMP_BT_LINKS']['ble_disconnects']:raise RuntimeError('Bluetooth link changed '+port)
                if ports[source]['lines']['WMP_BT']['rtp_gaps']!=before[source]['lines']['WMP_BT']['rtp_gaps']:raise RuntimeError('Source incoming A2DP gaps under custom window')
                if elapsed>=a.seconds:break
                time.sleep(1)
            sections={'WMP_AUDIO':['concealed','late','underrun','decoded'],'WMP_BT':['frames','rtp_gaps'],'WMP_RADIO':['accepted','done','late'],'WMP_GATT':['applied','notifications']}
            row['delta']={port:{section+'.'+key:ports[port]['lines'][section][key]-first[port]['lines'][section][key] for section,keys in sections.items() for key in keys} for port in before}
            for port,d in row['delta'].items():
                if any(d[key]<=0 for key in ['WMP_BT.frames','WMP_AUDIO.decoded','WMP_GATT.applied','WMP_GATT.notifications']):raise RuntimeError('Audio/BLE failed to progress '+port)
            row['media_after']=[media(phone) for phone in phones]
            if any(v.get('playback_state',v.get('youtube_state')) not in ('PLAYING','3') for v in row['media_after']):raise RuntimeError('Actual music ended')
            row['acquisition_complete']=True
    except Exception as error:row['errors'].append(repr(error))
    finally:
        if source:
            try:control(source,'TXWAKE 0','WMP_TX_WAKE_REQUEST')
            except Exception as error:row['errors'].append('disable gate '+repr(error))
        for port in reversed(changed):
            for line,marker in [('RXINTERVAL '+str(original[port]['interval']),'WMP_RECEIVE_INTERVAL'),('RXWINDOWMS '+str(original[port]['window']),'WMP_RECEIVE_SIZE_REQUEST')]:
                try:control(port,line,marker)
                except Exception as error:row['errors'].append('restore '+port+' '+repr(error))
        if original:
            try:
                row['restored_wake']={port:wake(port) for port in original}
                if any(v['tx_span_us'] or v['window_ms']!=original[port]['window'] or v['interval_ms']!=original[port]['interval'] for port,v in row['restored_wake'].items()):raise RuntimeError('Wake settings differ from baseline')
            except Exception as error:row['errors'].append('restore readback '+repr(error))
    save(out/'receipt.json',row);print(dict(evidence=str(out),complete=row['acquisition_complete'],errors=row['errors'],delta=row.get('delta')),flush=True)
    if row['errors']:raise SystemExit(1)

if __name__=='__main__':main()
