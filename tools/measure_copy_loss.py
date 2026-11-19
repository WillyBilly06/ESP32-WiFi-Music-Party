"""Measure settled four-copy receipt masks during actual A2DP/Wi-Fi/BLE.

Optional histories consume diagnostic heap. No loss injection, role/radio change,
codec change or qualification claim. Independence comparison is descriptive.
"""
import argparse,concurrent.futures,json,math,re,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair
from firmware_evidence import installed_pair,live_pair
from audio_fixture import FIXTURES,require,check_sources
from qualify_party_long import PHONES,peer,media

def distribution(masks):
    if len(masks)!=16 or any(not isinstance(n,int) or n<0 for n in masks):raise ValueError('Sixteen nonnegative counts required')
    n=sum(masks)
    if not n:raise ValueError('No retired groups')
    delivered=[sum(count for mask,count in enumerate(masks) if mask&(1<<i))/n for i in range(4)]
    expected=math.prod(1-p for p in delivered);actual=masks[0]/n
    return dict(groups=n,per_copy_receipt=delivered,all_four_absent=masks[0],all_four_received=masks[15],observed_all_absent_rate=actual,
        independent_prediction=expected,observed_to_independent=actual/expected if expected else None,descriptive_only=True)

def summary(sample):
    nonce=f"{sample['lines']['WMP_COPY']['query']:08x}";path=Path(sample['evidence'])
    with path.open('rb') as f:f.seek(max(0,path.stat().st_size-131072));raw=f.read().decode('ascii','replace')
    rows=[l for l in raw.splitlines() if l.startswith('WMP_COPY query='+nonce+' ')]
    if len(rows)!=1:raise ValueError('Missing/ambiguous copy summary from CRC-verified STAT')
    result=dict(query=nonce)
    for name,size in [('raw',4),('accepted',4),('done',4),('retired_masks',16)]:
        m=re.search(r'\b'+name+r'=([0-9,]+)(?:\s|$)',rows[0])
        if not m:raise ValueError('Copy-summary array absent: '+name)
        result[name]=list(map(int,m.group(1).split(',')))
        if len(result[name])!=size:raise ValueError('Invalid copy array length')
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',type=Path,required=True);p.add_argument('--fixture',choices=FIXTURES,default='redmagic-samsung');p.add_argument('--seconds',type=int,default=60);a=p.parse_args()
    if not 30<=a.seconds<=300:p.error('30..300seconds')
    out=ROOT/'evidence'/('copy-loss-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes());print(out,flush=True)
    r=dict(diagnostic_only=True,audio_qualified=False,samples=[],controls=[],errors=[],acquisition_complete=False);owned=[]
    try:
        r['fixture']=require(a.fixture);r['binding']=installed_pair(a.app);r['live']=live_pair(r['binding'],command)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            initial=pair(pool);r['initial']=initial;leaders=[p for p,v in initial.items() if v['lines']['WMP_RADIO']['mode']==2]
            if len(leaders)!=1:raise RuntimeError('Need exactly one source')
            source=leaders[0];receiver=next(p for p in initial if p!=source);r.update(source=source,receiver=receiver)
            phones=[PHONES[peer(v['lines'])] for v in initial.values()];check_sources(r['fixture'],phones);r['media_before']=[media(p) for p in phones]
            if any(v.get('playback_state',v.get('youtube_state')) not in ('PLAYING','3') for v in r['media_before']):raise RuntimeError('Actual music absent')
            for port,v in initial.items():
                if v['lines']['WMP_RADIO_RESOURCES']['history_bytes']:raise RuntimeError('Existing history owner '+port)
                c=command(port,'COPYLOG 1','WMP_TRACE_RESOURCE');r['controls'].append(c)
                if 'accepted=1' not in c['reply']:raise RuntimeError('History allocation rejected')
                owned.append(port)
            first=pair(pool);r['first_summary']={p:summary(v) for p,v in first.items()};start=time.monotonic()
            while True:
                current=first if not r['samples'] else pair(pool);elapsed=time.monotonic()-start;r['samples'].append(dict(elapsed=elapsed,ports=current));save(out/'receipt.json',r)
                for port,v in current.items():
                    s=v['lines'];b=first[port]['lines']
                    if not all(s['WMP_BT'][k] for k in ['phone','streaming','ble']):raise RuntimeError('Actual full workload absent '+port)
                    if s['WMP_SESSION']['generation']!=b['WMP_SESSION']['generation'] or s['WMP_RADIO']['mode']!=b['WMP_RADIO']['mode'] or s['WMP_DMA']['start_us32']!=b['WMP_DMA']['start_us32']:raise RuntimeError('Session/role/DAC changed')
                    for sec,keys in {'WMP_COPY':['spacing','paced','fec'],'WMP_TX_DITHER':['us'],'WMP_PHY':['selected_mbps'],'WMP_PHY_CONFIG':['bandwidth_code'],'WMP_TX_WINDOW':['active_limit'],'WMP_ASSOC_EVENTS':['connected','disconnected'],'WMP_WIFI_STATE':['sniffer']}.items():
                        if any(s[sec][k]!=b[sec][k] for k in keys):raise RuntimeError('Configuration/link changed')
                if elapsed>=a.seconds:break
                time.sleep(2)
            r['last_summary']={p:summary(v) for p,v in current.items()};r['delta']={p:{name:[x-y for x,y in zip(r['last_summary'][p][name],r['first_summary'][p][name])] for name in ['raw','accepted','done','retired_masks']} for p in first}
            r['receiver_distribution']=distribution(r['delta'][receiver]['retired_masks']);r['source_distribution']=distribution(r['delta'][source]['retired_masks'])
            fields={'WMP_BT':['rtp_gaps','rtp_bad','blocked_frames','frames'],'WMP_GATT':['applied','notifications'],'WMP_AUDIO':['concealed','late','underrun'],'WMP_RADIO':['fail','api_error','timeout','late','qfull'],'WMP_RX':['bad','qfull'],'WMP_SOURCE_QUEUE':['full'],'WMP_DIAGNOSTIC':['intentionally_dropped_copies']}
            fields['WMP_TX_LIFETIME']=['original_late_submit','repair_late_submit','expired_repair_trains','original_late_done','repair_late_done']
            r['workload_delta']={p:{sec+'.'+k:current[p]['lines'][sec][k]-first[p]['lines'][sec][k] for sec,keys in fields.items() for k in keys} for p in first}
            r['media_after']=[media(p) for p in phones]
            if any(r['workload_delta'][p]['WMP_BT.frames']<=0 or r['workload_delta'][p]['WMP_GATT.applied']<=0 or r['workload_delta'][p]['WMP_DIAGNOSTIC.intentionally_dropped_copies'] for p in first):raise RuntimeError('Workload did not progress or loss was injected')
            r['completed_seconds']=elapsed;r['acquisition_complete']=True
    except Exception as e:r['errors'].append(repr(e))
    finally:
        for port in owned:
            try:
                c=command(port,'COPYLOG 0','WMP_TRACE_RESOURCE');r['controls'].append(c)
                if 'accepted=1' not in c['reply']:raise RuntimeError('History cleanup rejected')
            except Exception as e:r['errors'].append('cleanup '+repr(e))
        save(out/'receipt.json',r)
    print(dict(complete=r['acquisition_complete'],distribution=r.get('receiver_distribution'),errors=r['errors']),flush=True)
    if r['errors']:raise SystemExit(1)

if __name__=='__main__':main()
