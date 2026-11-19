"""Enable the admitted associated raw path and verify automatic PM registration."""
import argparse,time
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import sample

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',choices=['COM6','COM7'],required=True);p.add_argument('--rate',type=int,choices=[6,9,12,18,24,36,48,54,65],default=54);a=p.parse_args();out=ROOT/'evidence'/('auto-wake-provision-'+str(time.time_ns())+'-'+a.port);out.mkdir();r=dict(port=a.port,before=sample(a.port),controls=[],pass_all=False,errors=[])
    try:
        s=r['before']['lines']
        if s['WMP_RADIO']['mode'] or s['WMP_BT']['phone'] or s['WMP_BT']['ble'] or not s['WMP_ASSOC']['connected']:raise RuntimeError('Provision before phones/BLE, after association')
        if s['WMP_DIRECT_RX']['enabled'] or s['WMP_RECEIVE_WINDOW']['enabled']:raise RuntimeError('Need fresh disabled path to prove automatic registration')
        rate=s.get('WMP_RATE_SETUP',{})
        if rate.get('configured_mbps')!=a.rate or rate.get('sta_status') or rate.get('precondition_rejections'):raise RuntimeError('Need selected prestart STA rate before path registration')
        for text,marker in [('DIRECTRX 1','WMP_DIRECT_RX_REQUEST'),('RXPAUSE 1','WMP_RX_PAUSE_REQUEST'),('SCHEDULE 64000 0','WMP_SCHEDULE'),('TXDITHER 8000','WMP_TX_DITHER_REQUEST'),('NACKDELAY 120000','WMP_NACK_DELAY')]:
            c=command(a.port,text,marker);r['controls'].append(c);save(out/'receipt.json',r)
            if 'accepted=1' not in c['reply'] and 'status=0' not in c['reply']:raise RuntimeError('Path control rejected')
        r['after']=sample(a.port);s=r['after']['lines']
        if not s['WMP_DIRECT_RX']['enabled'] or not s['WMP_RECEIVE_WINDOW']['enabled'] or s['WMP_RECEIVE_WINDOW']['status']:raise RuntimeError('Automatic PM registration failed')
        if s['WMP_TX_DITHER']['us']!=8000 or s['WMP_REPLAY']['nack_delay_us']!=120000:raise RuntimeError('Pacing controls failed')
        r['pass_all']=True
    except Exception as e:r['errors'].append(str(e))
    save(out/'receipt.json',r);print(dict(evidence=str(out),pass_all=r['pass_all'],errors=r['errors']))
    if not r['pass_all']:raise SystemExit(1)
if __name__=='__main__':main()
