"""Continuous full-fixture A/B/A test of repair-reference release headroom."""
import argparse,concurrent.futures,json,re,secrets,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair
from firmware_evidence import installed_pair,live_pair
from audio_fixture import require as require_fixture,check_sources
from qualify_party_long import PHONES,peer,media

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',type=Path,required=True);p.add_argument('--seconds',type=int,default=45);p.add_argument('--compare-spacing',action='store_true');p.add_argument('--compare-dedup',action='store_true');p.add_argument('--compare-nack-delay',action='store_true');a=p.parse_args()
    if not 30<=a.seconds<=180:p.error('30..180 real seconds per unchanged case')
    if sum([a.compare_spacing,a.compare_dedup,a.compare_nack_delay])>1:p.error('Change one repair policy at a time')
    out=ROOT/'evidence'/('repair-release-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes());print(out,flush=True)
    row=dict(cases=[],controls=[],errors=[],acquisition_complete=False,audio_qualified=False,diagnostic_only=True);source=None;receiver=None;mutated=False;original_delay=None;original=None
    def policy():
        nonce=secrets.token_hex(4);read=command(source,'DROPTRACE '+nonce,'WMP_DROP_END query='+nonce);row['controls'].append(dict(readback=read))
        m=re.search(r'WMP_DROP_END query='+nonce+r' .*?pace_us=(\d+) guard_us=(\d+) budget_us=(\d+) spacing_us=(\d+) paced=(\d+)',read['reply'])
        if not m:raise RuntimeError('Repair policy readback missing')
        return tuple(map(int,m.groups()))
    def config(values):
        r=command(source,'REPAIRCFG '+' '.join(map(str,values)),'WMP_REPAIR_CONFIG');row['controls'].append(dict(policy=values,result=r));save(out/'receipt.json',row)
        if 'accepted=1' not in r['reply']:raise RuntimeError('Repair policy rejected')
        if policy()!=tuple(values):raise RuntimeError('Repair policy readback differs')
    try:
        row['fixture']=require_fixture('redmagic-linux');row['binding']=installed_pair(a.app);row['live']=live_pair(row['binding'],command)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            first=pair(pool);sources=[port for port,v in first.items() if v['lines']['WMP_RADIO']['mode']==2]
            if len(sources)!=1:raise RuntimeError('Need one current source')
            source=sources[0];receiver=next(port for port in first if port!=source);row.update(source=source,receiver=receiver)
            original_delay=first[receiver]['lines']['WMP_REPLAY']['nack_delay_us']
            # Original pace/guard/budget/spacing/paced and dedup; cases change only guard/spacing and keep the original budget.
            original=row['original_policy']=policy();row['original_dedup']=first[source]['lines']['WMP_REPAIR_GATE']['enabled']
            phones=[PHONES[peer(v['lines'])] for v in first.values()];check_sources(row['fixture'],phones)
            generation=first[source]['lines']['WMP_SESSION']['generation'];row['media_before']=[media(phone) for phone in phones]
            if any(v.get('playback_state',v.get('youtube_state')) not in ('PLAYING','3') for v in row['media_before']):raise RuntimeError('Actual music absent')
            fields={'WMP_AUDIO':['decoded','concealed','late','underrun'],'WMP_BT':['frames','rtp_gaps','blocked_frames'],'WMP_SOURCE_QUEUE':['full'],'WMP_GATT':['applied','notifications'],'WMP_REPLAY':['nack_sent','queued','received','played','too_late'],'WMP_REPAIR_GATE':['pending_suppressed'],'WMP_RADIO':['accepted','qfull','late']}
            if a.compare_dedup and first[source]['lines']['WMP_REPAIR_GATE']['enabled']!=1:raise RuntimeError('Dedup comparison requires original enabled policy')
            policies=[(80000,40000)]*3 if a.compare_dedup or a.compare_nack_delay else [(80000,40000),(80000,20000),(80000,40000)] if a.compare_spacing else [(80000,40000),(160000,40000),(80000,40000)]
            for case_index,(guard,spacing) in enumerate(policies):
                before_control=pair(pool)
                mutated=True;config((8000,guard,original[2],spacing,0));case=dict(guard_us=guard,spacing_us=spacing,samples=[],before_control=before_control);row['cases'].append(case)
                if a.compare_nack_delay:
                    delay=[120000,40000,120000][case_index];result=command(receiver,'NACKDELAY '+str(delay),'WMP_NACK_DELAY');row['controls'].append(result);case['nack_delay_us']=delay
                    if 'accepted=1' not in result['reply']:raise RuntimeError('NACK delay control rejected')
                if a.compare_dedup:
                    enabled=[1,0,1][case_index];result=command(source,'REPAIRDEDUP '+str(enabled),'WMP_REPAIR_DEDUP');row['controls'].append(result);case['dedup']=enabled
                    if 'accepted=1' not in result['reply']:raise RuntimeError('Repair suppression control rejected')
                # The preceding train may still be in flight: observe it, then
                # start the comparison after one presentation-reserve period.
                case['settling_seconds']=.6;time.sleep(.6);before=pair(pool);start=time.monotonic()
                while True:
                    ports=before if not case['samples'] else pair(pool);elapsed=time.monotonic()-start;case['samples'].append(dict(elapsed=elapsed,ports=ports));save(out/'receipt.json',row)
                    for port,v in ports.items():
                        s=v['lines'];b=before[port]['lines']
                        if not all(s['WMP_BT'][key] for key in ('phone','streaming','ble')) or not s['WMP_AUDIO']['running'] or s['WMP_SESSION']['generation']!=generation or s['WMP_RADIO']['mode']!=first[port]['lines']['WMP_RADIO']['mode']:raise RuntimeError('Fixture/session lapse '+port)
                        if s['WMP_RADIO']['copies']!=4 or s['WMP_COPY']['fec'] or s['WMP_WAKE']['tx_span_us'] or s['WMP_RECEIVE_WINDOW']['interval_ms']:raise RuntimeError('Required original/radio settings changed')
                        if any(s['WMP_BT_LINKS'][key]!=b['WMP_BT_LINKS'][key] for key in ['classic_disconnects','ble_disconnects']):raise RuntimeError('Bluetooth link changed '+port)
                    if a.compare_dedup and ports[source]['lines']['WMP_REPAIR_GATE']['enabled']!=case['dedup']:raise RuntimeError('Suppression control readback differs')
                    if a.compare_nack_delay and ports[receiver]['lines']['WMP_REPLAY']['nack_delay_us']!=case['nack_delay_us']:raise RuntimeError('NACK delay readback differs')
                    if any(ports[source]['lines'][section][key]!=before_control[source]['lines'][section][key] for section,keys in {'WMP_AUDIO':['concealed','late','underrun'],'WMP_BT':['rtp_gaps','blocked_frames'],'WMP_SOURCE_QUEUE':['full']}.items() for key in keys):raise RuntimeError('Source path fault under repair policy, including transition')
                    if elapsed>=a.seconds:break
                    time.sleep(2)
                case['delta']={port:{section+'.'+key:ports[port]['lines'][section][key]-before_control[port]['lines'][section][key] for section,keys in fields.items() for key in keys} for port in ports};case['seconds']=elapsed
                case['total_seconds_including_transition']=(ports[source]['lines']['WMP_RADIO']['time_us']-before_control[source]['lines']['WMP_RADIO']['time_us'])/1e6
                if any(case['delta'][port][key]<=0 for port in ports for key in ['WMP_BT.frames','WMP_GATT.applied','WMP_GATT.notifications']):raise RuntimeError('Audio/BLE did not progress')
                print(dict(guard=guard,spacing=spacing,dedup=case.get('dedup'),nack_delay=case.get('nack_delay_us'),seconds=elapsed,receiver_concealed=case['delta'][receiver]['WMP_AUDIO.concealed'],pending_suppressed=case['delta'][source]['WMP_REPAIR_GATE.pending_suppressed']),flush=True)
            row['media_after']=[media(phone) for phone in phones];row['acquisition_complete']=True
    except Exception as error:row['errors'].append(repr(error))
    finally:
        if mutated:
            try:config(original)
            except Exception as error:row['errors'].append('restore '+repr(error))
            if a.compare_dedup:
                try:
                    result=command(source,'REPAIRDEDUP '+str(row['original_dedup']),'WMP_REPAIR_DEDUP');row['controls'].append(result)
                    if 'accepted=1' not in result['reply']:raise RuntimeError('Suppression restore rejected')
                except Exception as error:row['errors'].append('restore suppression '+repr(error))
            if a.compare_nack_delay and receiver and original_delay is not None:
                try:
                    result=command(receiver,'NACKDELAY '+str(original_delay),'WMP_NACK_DELAY');row['controls'].append(result)
                    if 'accepted=1' not in result['reply']:raise RuntimeError('NACK delay restoration rejected')
                except Exception as error:row['errors'].append('restore NACK delay '+repr(error))
    save(out/'receipt.json',row);print(dict(evidence=str(out),complete=row['acquisition_complete'],errors=row['errors']),flush=True)
    if row['errors']:raise SystemExit(1)

if __name__=='__main__':main()
