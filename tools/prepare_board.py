"""Prepare one freshly installed admitted board; never replay live networking.

No phone media action is performed. --hold-standby is a temporary rolling-PHY
update fence and must be cleared before any qualification.
"""
import argparse,json,subprocess,sys,time
from pathlib import Path
from board_io import ROOT,board,save
from firmware_evidence import candidate
from capture_copies import command
from sweep_live_pacing import sample
from configure_reference_network import configure

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',type=Path,required=True);p.add_argument('--port',choices=['COM6','COM7'],required=True);p.add_argument('--rate',type=int,choices=[54,65],default=65);p.add_argument('--reserve-ms',type=int,default=200);p.add_argument('--spacing-us',type=int,default=24000);p.add_argument('--dither-us',type=int,default=6000);p.add_argument('--hold-standby',action='store_true');a=p.parse_args()
    if not 120<=a.reserve_ms<=300 or not 1000<=a.spacing_us<=160000 or not 0<=a.dither_us<=min(16000,a.spacing_us//4):p.error('Invalid latency/copy profile')
    proof=candidate(a.app);identity=board(a.port);deployed=json.loads((ROOT/'hardware'/('deployed-'+a.port+'.json')).read_text())
    if deployed.get('build_id')!=proof['build_id'] or not deployed.get('boot_verified') or deployed.get('state')!='installed':raise RuntimeError('Need this exact verified installation')
    info=command(a.port,'INFO','WMP_INFO')['reply']
    if 'build='+proof['build_id'] not in info or 'base_mac='+identity['base_mac'] not in info:raise RuntimeError('Live board/image mismatch')
    out=ROOT/'evidence'/('prepare-board-'+str(time.time_ns())+'-'+a.port);out.mkdir();(out/'prepare.py').write_bytes(Path(__file__).read_bytes())
    r=dict(port=a.port,build_id=proof['build_id'],profile=vars(a).copy(),controls=[],pass_all=False,errors=[],audio_qualified=False);r['profile']['app']=str(a.app);print(out,flush=True)
    def control(text,marker):
        c=command(a.port,text,marker);r['controls'].append(dict(command=text,result=c));save(out/'receipt.json',r)
        if 'accepted=1' not in c['reply'] and 'status=0' not in c['reply']:raise RuntimeError('Rejected '+text)
    try:
        initial=sample(a.port);r['before']=initial;s=initial['lines']
        if s['WMP_NETWORK']['infrastructure'] or s['WMP_ASSOC']['requested'] or s['WMP_RADIO']['mode'] or s['WMP_DIRECT_RX']['enabled']:raise RuntimeError('Already active/provisioned; network setup will not be replayed')
        if a.hold_standby:control('FOLLOWLOCK 1','WMP_FOLLOWLOCK_REQUEST')
        control('BT OFF','WMP_BT_REQUEST');deadline=time.monotonic()+20
        while True:
            b=sample(a.port)['lines']['WMP_BT']
            if not any(b[k] for k in ['working','requested','phone','ble']):break
            if time.monotonic()>deadline:raise RuntimeError('Bluetooth did not stop for cold setup')
            time.sleep(.2)
        r['network']=configure(a.port,disable_bt=True,raw=True,rate=a.rate)
        subprocess.run([sys.executable,'-u',str(ROOT/'tools/provision_audio_path.py'),'--port',a.port,'--rate',str(a.rate)],check=True,cwd=ROOT)
        # Automatic role policy owns the preference. A later manual COEX0
        # overwrote a follower's Wi-Fi preference while its policy cache still
        # considered that preference applied, until the next role transition.
        # Firmware requires dither <= spacing/4 for both requests: dither first only if it fits the current spacing.
        timing=[('SCHEDULE '+str(a.spacing_us)+' 0','WMP_SCHEDULE'),('TXDITHER '+str(a.dither_us),'WMP_TX_DITHER_REQUEST')]
        if a.dither_us<=sample(a.port)['lines']['WMP_COPY']['spacing']//4:timing.reverse()
        for text,marker in [('BT ON','WMP_BT_REQUEST'),('A2DPCOEX 1','WMP_A2DP_COEX'),('TXWINDOW 6','WMP_TX_WINDOW_REQUEST'),('NACKDELAY 40000','WMP_NACK_DELAY')]+timing+[('LATENCY '+str(a.reserve_ms),'WMP_LATENCY_REQUEST')]:control(text,marker)
        r['after']=sample(a.port);s=r['after']['lines']
        if s['WMP_PHY_CONFIG']['bandwidth_code']!=1 or s['WMP_PHY_CONFIG']['bandwidth_status'] or s['WMP_RATE_SETUP']['configured_mbps']!=a.rate:raise RuntimeError('HT20/rate readback failed')
        if s['WMP_COPY']['spacing']!=a.spacing_us or s['WMP_TX_DITHER']['us']!=a.dither_us or bool(s['WMP_ROLE_POLICY']['follower_lock'])!=a.hold_standby:raise RuntimeError('Profile/rolling-update fence mismatch')
        if s['WMP_PROFILE_STAT']['enabled']!=1 or s['WMP_PROFILE_STAT']['status']:raise RuntimeError('Automatic role coexistence unavailable')
        r['pass_all']=True
    except Exception as e:r['errors'].append(repr(e))
    save(out/'receipt.json',r);print(dict(pass_all=r['pass_all'],errors=r['errors'],temporary_follower_lock=a.hold_standby),flush=True)
    if not r['pass_all']:raise SystemExit(1)

if __name__=='__main__':main()
