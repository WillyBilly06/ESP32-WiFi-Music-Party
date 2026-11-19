"""Bounded receiver-only BT-off isolation; never a full-workload pass."""
import argparse,concurrent.futures,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair,sample
from firmware_evidence import installed_pair,live_pair
from audio_fixture import require,check_sources
from qualify_party_long import PHONES,peer,media

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',type=Path,required=True);p.add_argument('--seconds',type=int,default=40);p.add_argument('--ble-only',action='store_true');a=p.parse_args()
    if not 20<=a.seconds<=90:p.error('20..90 seconds')
    out=ROOT/'evidence'/('receiver-bt-isolation-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes());print(out,flush=True)
    row=dict(samples=[],controls=[],errors=[],audio_qualified=False,acquisition_complete=False,scope='Source actual A2DP/BLE unchanged; receiver Bluetooth deliberately stopped, Wi-Fi/DAC remain active. Re-enable BT afterward; full fixture reconnection is separate.')
    if a.ble_only:row['scope']='Source actual A2DP/BLE unchanged; receiver BLE connected with real transactions, own Classic A2DP absent. Receiver Wi-Fi/DAC remain active. Diagnostic only.'
    owned=False;receiver=None;original=None
    try:
        fixture=require('redmagic-linux');row['binding']=installed_pair(a.app);row['live']=live_pair(row['binding'],command)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            first=pair(pool);leaders=[port for port,v in first.items() if v['lines']['WMP_RADIO']['mode']==2]
            if len(leaders)!=1:raise RuntimeError('Need one source')
            source=leaders[0];receiver=next(port for port in first if port!=source);row.update(source=source,receiver=receiver,before=first)
            check_sources(fixture,[PHONES[peer(v['lines'])] for v in first.values()])
            if not all(first[source]['lines']['WMP_BT'][k] for k in ('phone','streaming','ble')):raise RuntimeError('Source workload absent')
            row['source_media_before']=media(PHONES[peer(first[source]['lines'])])
            # Read the original receiver profile; firmware replies only while the controller runs, so skip a no-op request.
            original=first[receiver]['lines']['WMP_PROFILE_STAT']['enabled'];row['original_a2dp_coex']=original
            if original!=2:
                r=command(receiver,'A2DPCOEX 2','WMP_A2DP_COEX');row['controls'].append(r)
                if 'status=0' not in r['reply']:raise RuntimeError('Receiver baseline profile request failed')
            if not a.ble_only:
                r=command(receiver,'BT OFF','WMP_BT_REQUEST');row['controls'].append(r)
                if 'accepted=1' not in r['reply']:raise RuntimeError('Receiver BT stop rejected')
                owned=True
            deadline=time.monotonic()+20
            while True:
                state=sample(receiver)['lines'];row['stop_state']=state;save(out/'receipt.json',row)
                ready=(state['WMP_BT']['working'] and state['WMP_BT']['ble'] and not state['WMP_BT']['phone']) if a.ble_only else not any(state['WMP_BT'][k] for k in ('working','phone','ble'))
                if ready:break
                if time.monotonic()>=deadline:raise RuntimeError('Receiver Bluetooth did not stop')
                time.sleep(.2)
            initial=pair(pool);start=time.monotonic();generation=initial[source]['lines']['WMP_SESSION']['generation']
            while True:
                current=initial if not row['samples'] else pair(pool);elapsed=time.monotonic()-start;row['samples'].append(dict(elapsed=elapsed,ports=current));save(out/'receipt.json',row)
                if not all(current[source]['lines']['WMP_BT'][k] for k in ('phone','streaming','ble')):raise RuntimeError('Source workload disappeared')
                rb=current[receiver]['lines']['WMP_BT']
                if a.ble_only:
                    if not rb['working'] or not rb['ble'] or rb['phone']:raise RuntimeError('Receiver BLE-only state changed')
                elif any(rb[k] for k in ('working','phone','ble')):raise RuntimeError('Receiver BT unexpectedly active')
                if any(not v['lines']['WMP_AUDIO']['running'] or v['lines']['WMP_SESSION']['generation']!=generation for v in current.values()):raise RuntimeError('DAC/session changed')
                if elapsed>=a.seconds:break
                time.sleep(5)
            before=initial[receiver]['lines'];after=current[receiver]['lines'];row['receiver_delta']={s+'.'+k:after[s][k]-before[s][k] for s,keys in {'WMP_AUDIO':['decoded','concealed','late','underrun'],'WMP_DIRECT_RX':['queued'],'WMP_DIRECT_AIR':['frames','bad','same_sequence']}.items() for k in keys}
            row['source_delta']={s+'.'+k:current[source]['lines'][s][k]-initial[source]['lines'][s][k] for s,keys in {'WMP_RADIO':['accepted','done'],'WMP_AUDIO':['concealed'],'WMP_BT':['frames','rtp_gaps'],'WMP_GATT':['applied','notifications']}.items() for k in keys}
            row['acquisition_complete']=True
    except Exception as error:row['errors'].append(repr(error))
    finally:
        if owned:
            try:
                r=command(receiver,'BT ON','WMP_BT_REQUEST');row['controls'].append(r)
                if 'accepted=1' not in r['reply']:raise RuntimeError('Receiver BT re-enable rejected')
            except Exception as error:row['errors'].append('restore '+repr(error))
        if receiver and original is not None:
            try:
                deadline=time.monotonic()+20
                while not sample(receiver)['lines']['WMP_BT']['working']:
                    if time.monotonic()>=deadline:raise RuntimeError('Receiver controller not running; A2DPCOEX reply would never arrive')
                    time.sleep(.2)
                if sample(receiver)['lines']['WMP_PROFILE_STAT']['enabled']!=original:
                    r=command(receiver,'A2DPCOEX '+str(original),'WMP_A2DP_COEX');row['controls'].append(r)
                    if 'status=0' not in r['reply']:raise RuntimeError('Receiver profile restore rejected')
                if sample(receiver)['lines']['WMP_PROFILE_STAT']['enabled']!=original:raise RuntimeError('Receiver profile readback differs from original')
                row['restored_a2dp_coex']=original
            except Exception as error:row['errors'].append('profile restore '+repr(error))
    save(out/'receipt.json',row);print(dict(evidence=str(out),complete=row['acquisition_complete'],receiver_delta=row.get('receiver_delta'),source_delta=row.get('source_delta'),errors=row['errors']),flush=True)
    if row['errors']:raise SystemExit(1)

if __name__=='__main__':main()
