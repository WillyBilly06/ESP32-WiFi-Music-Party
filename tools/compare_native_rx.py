"""Bounded, same-image comparison of actual dual-source/BLE receive paths.

Does not reconnect, pause media, clear counters, or change source scheduling.
Native receive copy timestamps are callback MAC reads, not RF captures.
"""
import bench_ids
import argparse, concurrent.futures, json, time
from board_io import ROOT, save
from capture_copies import command
from sweep_live_pacing import pair
from qualify_party_long import media


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=30)
    args=parser.parse_args()
    if not 20<=args.seconds<=120:parser.error('20..120 seconds per path')
    out=ROOT/'evidence'/('native-rx-comparison-'+str(time.time_ns()));out.mkdir()
    row=dict(seconds_per_path=args.seconds,phases=[],errors=[],audio_qualified=False)
    print(str(out),flush=True);original=None
    try:
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            initial=pair(pool)
            if initial['COM6']['lines']['WMP_WIFI_MODE']['ap']!=0:
                raise RuntimeError('Comparison requires already-configured COM6 STA receive context')
            original=row['original_native_rx']=initial['COM6']['lines']['WMP_NATIVE_RX']['requested']
            for native in [0,1]:
                reply=command('COM6',f'NATIVERX {native}','WMP_NATIVE_RX_REQUEST')
                phase=dict(native=native,command=reply,samples=[],media=[media(bench_ids.PHONE_MAIN),media('linux')])
                row['phases'].append(phase)
                if 'accepted=1' not in reply['reply']:raise RuntimeError('NATIVERX '+str(native)+' rejected')
                start=time.monotonic()
                while True:
                    ports=pair(pool);elapsed=time.monotonic()-start
                    for port,value in ports.items():
                        lines=value['lines'];bt=lines['WMP_BT']
                        if not (bt['phone'] and bt['streaming'] and bt['ble']):
                            raise RuntimeError(port+' required workload inactive: '+json.dumps(bt))
                        expected=2 if port=='COM7' else 0
                        if lines['WMP_RADIO']['mode']!=expected:raise RuntimeError(port+' role changed')
                    phase['samples'].append(dict(elapsed=elapsed,ports=ports))
                    save(out/'receipt.json',row)
                    if elapsed>=args.seconds:break
                    time.sleep(min(5,args.seconds-elapsed))
                first=phase['samples'][0]['ports'];last=phase['samples'][-1]['ports']
                phase['delta']={}
                for port in first:
                    phase['delta'][port]={}
                    for section,fields in {
                        'WMP_AUDIO':['concealed','late','underrun','decoded'],
                        'WMP_BT':['frames','rtp_gaps','ble_writes'],
                        'WMP_RX':['qfull','unique','duplicate','bad'],
                        'WMP_NATIVE_RX':['callbacks','queued','done','failed'],
                    }.items():
                        phase['delta'][port][section]={key:last[port]['lines'][section][key]-first[port]['lines'][section][key] for key in fields}
                phase['media_end']=[media(bench_ids.PHONE_MAIN),media('linux')]
                print(json.dumps(dict(native=native,delta=phase['delta'])),flush=True)
        row['acquisition_complete']=True
    except Exception as error:
        row['errors'].append(repr(error));raise
    finally:
        # Restore the read-back original request; nothing was changed if it was never read.
        if original is not None:
            try:
                row['restore']=command('COM6',f'NATIVERX {original}','WMP_NATIVE_RX_REQUEST')
                if 'accepted=1' not in row['restore']['reply']:raise RuntimeError('NATIVERX restore rejected')
            except Exception as error:row['errors'].append('restore: '+repr(error))
        save(out/'receipt.json',row)


if __name__=='__main__':main()
