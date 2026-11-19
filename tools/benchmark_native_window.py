"""Finite queue-depth comparison; no Bluetooth/audio qualification is implied."""
import argparse,concurrent.futures,json,time
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair

def snapshot():
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:return pair(pool)

def control(receipt,port,text,marker):
    answer=command(port,text,marker);receipt['controls'].append(dict(port=port,command=text,**answer))
    if 'accepted=1' not in answer['reply']:raise RuntimeError(text+' rejected on '+port)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seconds',type=float,default=8)
    p.add_argument('--windows',nargs='+',type=int,default=[8,16,24]);p.add_argument('--pps',type=int,default=100)
    p.add_argument('--payload',type=int,default=476);a=p.parse_args()
    out=ROOT/'evidence'/('native-window-'+str(time.time_ns()));out.mkdir()
    r=dict(diagnostic_only=True,audio_qualified=False,controls=[],cases=[],complete=False)
    baseline=snapshot();r['initial']=baseline;save(out/'receipt.json',r)
    for port,state in baseline.items():
        s=state['lines']
        if s['WMP_RADIO']['mode'] or s['WMP_BT']['working'] or s['WMP_BT']['phone'] or s['WMP_BT']['ble']:
            raise RuntimeError('Requires stopped radio and disabled Bluetooth on '+port)
        if not(s['WMP_NETWORK']['infrastructure'] and s['WMP_ASSOC']['connected'] and s['WMP_ETH']['enabled']==1):
            raise RuntimeError('Requires associated native reference on '+port)
    old_window=baseline['COM7']['lines']['WMP_TX_WINDOW']['active_limit']
    try:
        control(r,'COM6','LOAD 2 5 3 32000 '+str(a.payload),'WMP_LOAD_REQUEST')
        control(r,'COM7',f'LOAD 1 {a.pps} 3 32000 {a.payload}','WMP_LOAD_REQUEST')
        for window in a.windows:
            control(r,'COM7','TXWINDOW '+str(window),'WMP_TX_WINDOW_REQUEST');time.sleep(1.5)
            before=snapshot();time.sleep(a.seconds);after=snapshot();result=dict(window=window,before=before,after=after)
            for port in ('COM7','COM6'):
                b=before[port]['lines'];e=after[port]['lines'];seconds=(e['WMP_RADIO']['time_us']-b['WMP_RADIO']['time_us'])/1e6
                result[port]=dict(seconds=seconds,heap=e['WMP_MEMORY']['free'],minimum=e['WMP_MEMORY']['minimum'])
                for section,names in [('WMP_RADIO',['produced','accepted','done','fail','api_error','timeout','callback_qfull']),('WMP_RX',['unique','bad','qfull','conflict'])]:
                    for name in names:result[port][name]=e[section][name]-b[section][name]
                result[port]['pps']=result[port]['done' if port=='COM7' else 'unique']/seconds
            r['cases'].append(result);save(out/'receipt.json',r)
            print(json.dumps(dict(window=window,source=result['COM7'],receiver=result['COM6'])),flush=True)
        r['complete']=True
    except Exception as error:r['error']=repr(error);raise
    finally:
        for port in ('COM7','COM6'):
            try:control(r,port,'STOP','WMP_STOP_REQUEST')
            except Exception as error:r.setdefault('cleanup_errors',[]).append(repr(error))
        try:control(r,'COM7','TXWINDOW '+str(old_window),'WMP_TX_WINDOW_REQUEST')
        except Exception as error:r.setdefault('cleanup_errors',[]).append(repr(error))
        r['final']=snapshot();save(out/'receipt.json',r);print(json.dumps(dict(receipt=str(out/'receipt.json'),complete=r['complete'])),flush=True)

if __name__=='__main__':main()
