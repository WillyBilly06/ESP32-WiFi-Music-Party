"""Real-board rate setup and active-retune rejection; no RF-rate claim."""
import argparse,concurrent.futures,json,time
from pathlib import Path
from board_io import ROOT,save
from capture_copies import command
from firmware_evidence import installed_pair,live_pair
from sweep_live_pacing import pair,sample
from audio_fixture import require as require_fixture,check_sources
from qualify_party_long import PHONES,peer,media

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',type=Path,required=True);p.add_argument('--active',action='store_true');a=p.parse_args()
    out=ROOT/'evidence'/('rate-lifecycle-'+str(time.time_ns()));out.mkdir();(out/'test.py').write_bytes(Path(__file__).read_bytes())
    r=dict(diagnostic_only=True,audio_qualified=False,actual_rf_rate_qualified=False,active=a.active,cases=[],controls=[],errors=[],pass_all=False);print(out,flush=True)
    touched=[];original={}
    try:
        if a.active:r['fixture']=require_fixture('redmagic-linux')
        r['binding']=installed_pair(a.app);r['info']=live_pair(r['binding'],command)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            initial=pair(pool);r['initial']=initial
            if a.active:
                phones=[PHONES[peer(v['lines'])] for v in initial.values()];check_sources(r['fixture'],phones)
                r['media']=[media(x) for x in phones]
                if any(x.get('playback_state',x.get('youtube_state')) not in ('PLAYING','3') for x in r['media']):raise RuntimeError('Both actual streams must play')
            for port in ['COM6','COM7']:
                s=initial[port]['lines'];original[port]=s['WMP_PHY']['selected_mbps'];r['original_mbps']=original
                if a.active:
                    if not all(s['WMP_BT'][k] for k in ['phone','ble','streaming','transport_started']):raise RuntimeError('Full fixture absent '+port)
                    # Only the unchanged original rate (normally MCS7/HT20 65) is accepted while active.
                    rates=[original[port],24 if original[port]!=24 else 54]
                else:
                    if any(s['WMP_BT'][k] for k in ['phone','ble','transport_started']) or s['WMP_RADIO']['mode'] or s['WMP_NETWORK']['infrastructure'] or not s['WMP_WIFI_MODE']['ap']:raise RuntimeError('Cold AP required before phones/network '+port)
                    if s['WMP_BT']['working'] or s['WMP_BT']['requested']:
                        r['controls'].append(dict(port=port,result=command(port,'BT OFF','WMP_BT_REQUEST')))
                        deadline=time.monotonic()+10
                        while True:
                            stopped=sample(port)['lines']['WMP_BT']
                            if not stopped['working'] and not stopped['requested']:break
                            if time.monotonic()>deadline:raise RuntimeError('Bluetooth did not stop before cold rate check '+port)
                            time.sleep(.1)
                    touched.append(port);rates=[6,9,12,18,24,36,48,54,65]
                for mbps in rates:
                    before=sample(port);reply=command(port,'RATE '+str(mbps),'WMP_RATE');after=sample(port)
                    c=dict(port=port,mbps=mbps,before=before,control=reply,after=after);r['cases'].append(c);save(out/'receipt.json',r)
                    state=after['lines']['WMP_RATE_SETUP'];old=before['lines']['WMP_RATE_SETUP']
                    rejected=a.active and mbps!=original[port]
                    if ('status=259' if rejected else 'status=0') not in reply['reply']:raise RuntimeError('Unexpected rate command result '+port)
                    expected=original[port] if a.active else mbps
                    if state['selected_mbps']!=expected or state['configured_mbps']!=expected or not state['started'] or state['precondition_rejections']:raise RuntimeError('Invalid configured lifecycle '+port)
                    if state['starts']!=state['stops']+1 or state['prestart_calls']!=state['starts']:raise RuntimeError('Unconfigured start or failed prestart '+port)
                    if a.active:
                        if any(state[k]!=old[k] for k in ['starts','stops','prestart_calls']):raise RuntimeError('Active command restarted/reconfigured Wi-Fi '+port)
                        for section,key in [('WMP_SESSION','generation'),('WMP_DMA','start_us32'),('WMP_SOURCE_CLOCK','domain_epoch')]:
                            if before['lines'][section][key]!=after['lines'][section][key]:raise RuntimeError('Active epoch changed '+port)
                        if not all(after['lines']['WMP_BT'][k] for k in ['phone','ble','streaming','transport_started']):raise RuntimeError('Active links stopped '+port)
                    print(dict(port=port,mbps=mbps,rejected=rejected,starts=state['starts'],stops=state['stops']),flush=True)
            r['pass_all']=True
    except Exception as e:r['errors'].append(repr(e))
    finally:
        for port in touched:
            try:
                current=sample(port)
                if current['lines']['WMP_RATE_SETUP']['configured_mbps']!=original[port]:
                    restore=command(port,'RATE '+str(original[port]),'WMP_RATE');r.setdefault('restores',[]).append(dict(port=port,result=restore))
                    if 'status=0' not in restore['reply']:raise RuntimeError('Cold restoration rejected')
            except Exception as e:r['errors'].append('restore '+port+': '+repr(e));r['pass_all']=False
        save(out/'receipt.json',r)
    print(dict(pass_all=r['pass_all'],errors=r['errors'],actual_rf_rate_qualified=False),flush=True)
    if not r['pass_all']:raise SystemExit(1)

if __name__=='__main__':main()
