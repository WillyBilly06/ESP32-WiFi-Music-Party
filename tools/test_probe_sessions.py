"""Restart only owned GATT services twice; demand fresh writes on both links."""
import concurrent.futures,secrets,time
from board_io import ROOT,save
from adb_local import SERIAL,shell
from audio_fixture import require
from sweep_live_pacing import pair

def main():
    require('redmagic-linux');out=ROOT/'evidence'/('probe-session-repeat-'+str(time.time_ns()));out.mkdir();row=dict(phases=[],errors=[],complete=False,audio_qualified=False);print(out,flush=True)
    try:
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            initial=pair(pool)
            for index in range(2):
                before=pair(pool);run,second=secrets.token_hex(4),secrets.token_hex(4);phase=dict(index=index,runs=[run,second],before=before);row['phases'].append(phase)
                shell(SERIAL,['am','start','-f','0x10008000','-n','org.wmp.partyprobe/.PartyProbe','--es','run',run,'--es','target','08:B6:1F:B8:66:26','--es','run2',second,'--es','target2','6C:C8:40:42:BF:66','--ei','seconds','7200'])
                deadline=time.monotonic()+20
                while True:
                    states=pair(pool)
                    if all(s['lines']['WMP_BT']['ble'] and s['lines']['WMP_GATT']['applied']>before[p]['lines']['WMP_GATT']['applied'] for p,s in states.items()):break
                    if time.monotonic()>deadline:raise RuntimeError('New sessions did not deliver both write streams')
                    time.sleep(.2)
                time.sleep(8);last=pair(pool);phase['after']=last;phase['logs']={run:shell(SERIAL,['run-as','org.wmp.partyprobe','cat','files/gatt-'+run+'.log']) for run in [run,second]}
                phase['delta']={p:{key:last[p]['lines']['WMP_GATT'][key]-before[p]['lines']['WMP_GATT'][key] for key in ['applied','notifications']} for p in last}
                for p,s in last.items():
                    if min(phase['delta'][p].values())<3:raise RuntimeError('Fresh per-session GATT progress absent '+p)
                    if s['lines']['WMP_BT_LINKS']['classic_disconnects']!=initial[p]['lines']['WMP_BT_LINKS']['classic_disconnects']:raise RuntimeError('Classic link changed during owned service restart')
                    if s['lines']['WMP_RADIO']['mode']!=initial[p]['lines']['WMP_RADIO']['mode'] or s['lines']['WMP_SESSION']['generation']!=initial[p]['lines']['WMP_SESSION']['generation']:raise RuntimeError('Leadership/session changed during test-client renewal')
                if any('END reason=' in text or 'WRITE_TIMEOUT ' in text for text in phase['logs'].values()):raise RuntimeError('New session ended/failed')
                save(out/'receipt.json',row);print(dict(phase=index,runs=phase['runs'],delta=phase['delta']),flush=True)
            row['complete']=True
    except Exception as error:row['errors'].append(repr(error))
    save(out/'receipt.json',row);print(dict(evidence=str(out),complete=row['complete'],errors=row['errors']),flush=True)
    if row['errors']:raise SystemExit(1)

if __name__=='__main__':main()
