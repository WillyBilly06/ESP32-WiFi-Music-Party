"""Compare broadcast originals with broadcast versus addressed repairs.

The four original copies, real phone music, BLE traffic and source presentation
timeline stay unchanged. This is a finite comparison, not an hour qualification.
"""
import bench_ids
import argparse,concurrent.futures,json,time
from pathlib import Path
from board_io import ROOT,save
from sweep_live_pacing import pair
from capture_copies import command
from qualify_party_long import media,PHONES,peer
from firmware_evidence import installed_pair,live_pair
from audio_fixture import require,check_sources

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--app',type=Path,required=True);parser.add_argument('--seconds',type=int,default=30);args=parser.parse_args()
    if not 30<=args.seconds<=120:parser.error('30..120seconds per arm')
    out=ROOT/'evidence'/('repair-destination-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes());r=dict(cases=[],errors=[],audio_qualified=False,diagnostic_only=True,note='Addressed NACK plus addressed audio repairs is a two-node diagnostic only, not the final100-receiver broadcast transport.')
    print('Repair comparison:',out,flush=True);source=None;original={}
    try:
        r['fixture']=require('redmagic-samsung');r['binding']=installed_pair(args.app);r['live']=live_pair(r['binding'],command)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            initial=pair(pool);sources=[p for p,v in initial.items() if v['lines']['WMP_RADIO']['mode']==2]
            if len(sources)!=1:raise RuntimeError('Expected one source')
            source=sources[0];r['source']=source;r['initial']=initial;check_sources(r['fixture'],[PHONES[peer(v['lines'])] for v in initial.values()])
            original={p:v['lines']['WMP_REPAIR_LINK']['unicast'] for p,v in initial.items()}
            if any(v['lines']['WMP_PHY']['selected_mbps']!=26 or v['lines']['WMP_PHY_CONFIG']['bandwidth_code']!=1 or v['lines']['WMP_RADIO']['copies']!=4 for v in initial.values()):raise RuntimeError('Need four originals atMCS3/HT20')
            for enabled in [0,1,0]:
                response={p:command(p,f'REPAIRCAST {enabled}','WMP_REPAIR_CAST') for p in initial}
                if any('accepted=1' not in v['reply'] for v in response.values()):raise RuntimeError('Setting rejected')
                time.sleep(2);case=dict(unicast=enabled,control=response,samples=[]);r['cases'].append(case);start=time.monotonic()
                while True:
                    ports=pair(pool);elapsed=time.monotonic()-start;case['samples'].append(dict(elapsed=elapsed,ports=ports));save(out/'receipt.json',r)
                    if any(not all(v['lines']['WMP_BT'][k] for k in ['streaming','phone','ble']) for v in ports.values()):raise RuntimeError('Actual phone A2DP/BLE workload disappeared')
                    first=case['samples'][0]['ports']
                    if any(v['lines']['WMP_SESSION']['generation']!=first[p]['lines']['WMP_SESSION']['generation'] or v['lines']['WMP_RADIO']['mode']!=first[p]['lines']['WMP_RADIO']['mode'] or v['lines']['WMP_DMA']['start_us32']!=first[p]['lines']['WMP_DMA']['start_us32'] for p,v in ports.items()):raise RuntimeError('Role, generation orDAC changed')
                    for section,keys in {'WMP_AUDIO':['concealed','late','underrun'],'WMP_BT':['rtp_gaps','blocked_frames'],'WMP_VHCI':['dropped']}.items():
                        if any(ports[source]['lines'][section][k]!=first[source]['lines'][section][k] for k in keys):raise RuntimeError('Source fault; stop comparison')
                    if elapsed>=args.seconds:break
                    time.sleep(min(5,args.seconds-elapsed))
                states=[media(s) for s in [bench_ids.PHONE_MAIN,bench_ids.PHONE_SECOND]];case['phone_states']=states
                if any(s['youtube_state'] not in ['PLAYING','3'] for s in states):raise RuntimeError('YouTube stopped')
                a,b=case['samples'][0]['ports'],case['samples'][-1]['ports']
                case['delta']={p:{s:{k:b[p]['lines'][s][k]-a[p]['lines'][s][k] for k in keys} for s,keys in {
                    'WMP_AUDIO':['concealed','late','underrun'],'WMP_RX':['qfull','unique'],
                    'WMP_BT':['rtp_gaps','rtp_bad','blocked_frames','frames','ble_writes'],'WMP_PHY':['unmatched'],
                    'WMP_REPLAY':['nack_sent','nack_heard','queued','received','delivered','played'],
                    'WMP_TX_LIFETIME':['original_late_submit','original_late_done'],
                    'WMP_GATT':['applied','notifications'],
                    'WMP_REPAIR_LINK':['submitted','completed','failed']}.items()} for p in a}
                case['acquisition_complete']=True;print(json.dumps(dict(unicast=enabled,delta=case['delta'])),flush=True);save(out/'receipt.json',r)
            r['acquisition_complete']=True
    except Exception as error:
        r['errors'].append(repr(error))
    finally:
        r['restore']={}
        for p,value in original.items():
            try:
                result=command(p,'REPAIRCAST '+str(value),'WMP_REPAIR_CAST');r['restore'][p]=result
                if 'accepted=1' not in result['reply']:raise RuntimeError('Cast restoration rejected')
            except Exception as restore:r['errors'].append('restore '+p+' '+repr(restore))
    save(out/'receipt.json',r);print(json.dumps(dict(evidence=str(out),errors=r['errors'])),flush=True)
if __name__=='__main__':main()
