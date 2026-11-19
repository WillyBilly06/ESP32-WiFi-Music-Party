"""Apply a measured copy/NACK profile without restarting either board."""
import argparse,concurrent.futures,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair
from firmware_evidence import installed_pair,live_pair
from audio_fixture import require,check_sources
from qualify_party_long import PHONES,peer

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',type=Path,required=True)
    p.add_argument('--spacing-us',type=int,required=True);p.add_argument('--dither-us',type=int,required=True);p.add_argument('--nack-us',type=int,required=True);a=p.parse_args()
    if not 1000<=a.spacing_us<=160000 or not 0<=a.dither_us<=min(16000,a.spacing_us//4) or not 20000<=a.nack_us<=300000:p.error('Invalid copy/NACK profile')
    out=ROOT/'evidence'/('copy-profile-'+str(time.time_ns()));out.mkdir();(out/'set_profile.py').write_bytes(Path(__file__).read_bytes())
    r=dict(pass_all=False,audio_qualified=False,controls=[],errors=[],profile=dict(spacing_us=a.spacing_us,dither_us=a.dither_us,nack_us=a.nack_us));print(out,flush=True)
    try:
        r['fixture']=require('redmagic-samsung');r['binding']=installed_pair(a.app);r['live']=live_pair(r['binding'],command)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            before=pair(pool);r['before']=before;check_sources(r['fixture'],[PHONES[peer(v['lines'])] for v in before.values()])
            leaders=[p for p,v in before.items() if v['lines']['WMP_RADIO']['mode']==2]
            if len(leaders)!=1:raise RuntimeError('Need one established source')
            for port,v in before.items():
                s=v['lines']
                if not all(s['WMP_BT'][k] for k in ['phone','streaming','ble']) or not s['WMP_AUDIO']['running']:raise RuntimeError('Full workload absent '+port)
                if s['WMP_PHY_CONFIG']['bandwidth_code']!=1 or s['WMP_PHY']['selected_mbps']!=26:raise RuntimeError('MCS3/HT20 must remain fixed')
                timing=[('SCHEDULE '+str(a.spacing_us)+' 0','WMP_SCHEDULE'),('TXDITHER '+str(a.dither_us),'WMP_TX_DITHER_REQUEST')]
                if a.dither_us<=s['WMP_COPY']['spacing']//4:timing.reverse()
                for text,marker in timing+[('NACKDELAY '+str(a.nack_us),'WMP_NACK_DELAY')]:
                    reply=command(port,text,marker);r['controls'].append(dict(port=port,command=text,result=reply));save(out/'receipt.json',r)
                    if 'accepted=1' not in reply['reply']:raise RuntimeError('Profile request rejected '+port+' '+text)
            after=pair(pool);r['after']=after
            for port,v in after.items():
                s=v['lines'];old=before[port]['lines']
                if s['WMP_COPY']['spacing']!=a.spacing_us or s['WMP_COPY']['paced'] or s['WMP_TX_DITHER']['us']!=a.dither_us or s['WMP_REPLAY']['nack_delay_us']!=a.nack_us:raise RuntimeError('Profile readback differs '+port)
                for sec,keys in {'WMP_SESSION':['generation','source'],'WMP_DMA':['start_us32'],'WMP_ASSOC_EVENTS':['connected','disconnected'],'WMP_RATE_SETUP':['starts','stops','prestart_calls'],'WMP_PHY_CONFIG':['bandwidth_code'],'WMP_PHY':['selected_mbps'],'WMP_TX_WINDOW':['active_limit']}.items():
                    if any(s[sec][k]!=old[sec][k] for k in keys):raise RuntimeError('Unexpected session/radio change '+port+' '+sec)
            r['pass_all']=True
    except Exception as e:r['errors'].append(repr(e))
    save(out/'receipt.json',r);print(dict(pass_all=r['pass_all'],errors=r['errors']),flush=True)
    if not r['pass_all']:raise SystemExit(1)

if __name__=='__main__':main()
