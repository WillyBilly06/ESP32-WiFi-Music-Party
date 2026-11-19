"""Real two-board allocation lifecycle, separate from audio qualification."""
import concurrent.futures,json,time,traceback
from adb_local import shell,SERIAL
from board_io import ROOT,save
from capture_copies import command
from sweep_live_pacing import pair

def main():
    out=ROOT/'evidence'/('lazy-lifecycle-'+str(time.time_ns()));out.mkdir();owned_histories=[]
    report=dict(resource_lifecycle_pass=False,audio_qualified=False,phases=[],errors=[],note='One active REDMAGIC A2DP source, two Wi-Fi audio outputs and two BLE clients. Any connected Linux Classic source must be paused; this does not qualify the complete dual-source workload.')
    def wait(pool,label,predicate,seconds=25):
        deadline=time.monotonic()+seconds
        while True:
            value=pair(pool);report['phases'].append(dict(label=label,at=time.time(),ports=value));save(out/'receipt.json',report)
            if any(v['lines']['WMP_RADIO_RESOURCES'].get('history_bytes',-1)!=0 for v in value.values()):raise RuntimeError('Playback unexpectedly allocated optional copy history')
            if predicate(value):print(label,'PASS',flush=True);return value
            if time.monotonic()>deadline:raise TimeoutError(label)
            time.sleep(.2)
    def resources(value):return [value[p]['lines']['WMP_AUDIO_RESOURCES'] for p in ('COM7','COM6')]
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            admission=pair(pool)
            if admission['COM7']['lines']['WMP_RADIO']['mode']!=2 or admission['COM7']['lines']['WMP_RADIO']['copies']!=4 or admission['COM6']['lines']['WMP_BT']['streaming']:
                raise RuntimeError('Need existing COM7 four-copy party with the alternate Linux source paused')
            if not all(v['lines']['WMP_BT']['ble'] for v in admission.values()):raise RuntimeError('Both BLE clients must remain connected')
            report['installed_images']={p:json.loads((ROOT/'hardware'/('deployed-'+p+'.json')).read_text()) for p in admission}
            silent=wait(pool,'continuous-silence-codec-unloaded',lambda v:all(not r['codec_bytes'] and r['ready'] for r in resources(v)))
            until=time.monotonic()+8
            while time.monotonic()<until:
                value=pair(pool);report['phases'].append(dict(label='stable-silence-no-resource-churn',at=time.time(),ports=value));save(out/'receipt.json',report)
                for p in value:
                    before=silent[p]['lines']['WMP_AUDIO_RESOURCES'];after=value[p]['lines']['WMP_AUDIO_RESOURCES']
                    if not after['ready'] or any(after[k]!=before[k] for k in ('starts','stops','codec_starts','codec_stops')):raise RuntimeError(p+' resource churn during continuous silence')
                    if any(value[p]['lines']['WMP_BT_RESOURCES'][k]!=silent[p]['lines']['WMP_BT_RESOURCES'][k] for k in ('starts','stops')):raise RuntimeError(p+' source storage churn during continuous silence')
                time.sleep(.2)
            for p in value:
                if value[p]['lines']['WMP_AUDIO']['output_frames']<=silent[p]['lines']['WMP_AUDIO']['output_frames']:raise RuntimeError(p+' silence output did not advance')
            for p in ('COM7','COM6'):
                result=command(p,'COPYLOG 1','WMP_TRACE_RESOURCE')
                if 'accepted=1' not in result['reply']:raise RuntimeError(p+' history allocation rejected')
                owned_histories.append(p)
            traced=pair(pool);report['phases'].append(dict(label='optional-history-loaded',at=time.time(),ports=traced));save(out/'receipt.json',report)
            if not all(v['lines']['WMP_RADIO_RESOURCES']['history_bytes']>0 for v in traced.values()):raise RuntimeError('History not allocated on request')
            for p in ('COM7','COM6'):
                result=command(p,'COPYLOG 0','WMP_TRACE_RESOURCE')
                if 'accepted=1' not in result['reply']:raise RuntimeError(p+' history release rejected')
                owned_histories.remove(p)
            wait(pool,'optional-history-released',lambda v:all(not x['lines']['WMP_RADIO_RESOURCES']['history_bytes'] for x in v.values()))
            shell(SERIAL,['cmd','media_session','dispatch','play'])
            first=wait(pool,'music-codec-reloaded',lambda v:v['COM7']['lines']['WMP_BT']['streaming'] and all(r['codec_bytes'] and r['codec_starts']>silent[p]['lines']['WMP_AUDIO_RESOURCES']['codec_starts'] for p,r in zip(('COM7','COM6'),resources(v))))
            time.sleep(3);playing=pair(pool)
            for p in first:
                if playing[p]['lines']['WMP_AUDIO']['decoded']<=first[p]['lines']['WMP_AUDIO']['decoded']:raise RuntimeError(p+' decoded audio did not advance')
            if not playing['COM7']['lines']['WMP_BT_RESOURCES'].get('assembler_bytes') or playing['COM6']['lines']['WMP_BT_RESOURCES'].get('source_bytes'):
                raise RuntimeError('Source assembly storage was not confined to the source role')
            report['phases'].append(dict(label='music-progress',at=time.time(),ports=playing));save(out/'receipt.json',report)
            shell(SERIAL,['cmd','media_session','dispatch','pause'])
            wait(pool,'pause-preserves-silence-output',lambda v:not v['COM7']['lines']['WMP_BT']['streaming'] and all(not r['codec_bytes'] and r['ready'] for r in resources(v)))
            for p in ('COM7','COM6'):
                result=command(p,'STOP','WMP_STOP_REQUEST');report.setdefault('commands',[]).append(dict(port=p,result=result));save(out/'receipt.json',report)
                if 'accepted=1' not in result['reply']:raise RuntimeError(p+' STOP rejected')
            idle=wait(pool,'unused-pools-released',lambda v:all(not r['ready'] and not r['arena_bytes'] and not r['pcm_bytes'] and not r['codec_bytes'] for r in resources(v)) and all(not x['lines']['WMP_BT_RESOURCES']['source_bytes'] and not x['lines']['WMP_RADIO_RESOURCES']['source_slots_bytes'] for x in v.values()))
            for p in idle:
                before=first[p]['lines']['WMP_AUDIO_RESOURCES'];after=idle[p]['lines']['WMP_AUDIO_RESOURCES']
                if after['starts']!=before['starts'] or after['stops']!=before['stops']+1:raise RuntimeError(p+' unexpected resource restart before explicit shutdown')
                if idle[p]['lines']['WMP_BT_RESOURCES'].get('assembler_bytes',-1)!=0:raise RuntimeError(p+' unused SBC assembler retained')
            report['heap_recovered_bytes']={p:idle[p]['lines']['WMP_MEMORY']['free']-playing[p]['lines']['WMP_MEMORY']['free'] for p in idle}
            shell(SERIAL,['cmd','media_session','dispatch','play'])
            resumed=wait(pool,'full-resources-reallocated',lambda v:v['COM7']['lines']['WMP_BT']['streaming'] and all(r['ready'] and r['codec_bytes'] and r['starts']>idle[p]['lines']['WMP_AUDIO_RESOURCES']['starts'] for p,r in zip(('COM7','COM6'),resources(v))))
            for p in resumed:
                for key in ('WMP_AUDIO_RESOURCES','WMP_BT_RESOURCES','WMP_RADIO_RESOURCES'):
                    if resumed[p]['lines'][key]['failures']:raise RuntimeError(p+' allocation failure: '+key)
                if not resumed[p]['lines']['WMP_BT']['ble'] or resumed[p]['lines']['WMP_GATT']['applied']<=admission[p]['lines']['WMP_GATT']['applied']:raise RuntimeError(p+' BLE did not continue through the lifecycle')
            source_old=idle['COM7']['lines']['WMP_BT_RESOURCES'];source_new=resumed['COM7']['lines']['WMP_BT_RESOURCES']
            report['source_restart_allocations']={k:source_new[k]-source_old[k] for k in ('starts','stops')}
            report['single_source_activation_pass']=source_new['starts']==source_old['starts']+1 and source_new['stops']==source_old['stops']
            report['source_transition_note']='Single-activation result is retained separately; the steady-music gate below cannot explain a START/SUSPEND transition before media resumes.'
            if not source_new['source_bytes'] or source_new['starts']<=source_old['starts']:raise RuntimeError('Source storage did not reload')
            # Android may START/SUSPEND while resuming its upstream player.
            # Qualify no churn once actual music has reached both renderers;
            # retain every transition above rather than hiding its count.
            time.sleep(3);stable=pair(pool);report['phases'].append(dict(label='resumed-music-no-resource-churn',at=time.time(),ports=stable))
            for p in stable:
                if stable[p]['lines']['WMP_AUDIO']['decoded']<=resumed[p]['lines']['WMP_AUDIO']['decoded']:raise RuntimeError(p+' resumed audio did not advance')
                for key,fields in [('WMP_AUDIO_RESOURCES',('starts','stops','codec_starts','codec_stops')),('WMP_BT_RESOURCES',('starts','stops'))]:
                    if any(stable[p]['lines'][key][k]!=resumed[p]['lines'][key][k] for k in fields):raise RuntimeError(p+' resources churned during resumed music')
            report['resource_lifecycle_pass']=True
    except Exception as error:
        report['errors'].append(str(error));report['traceback']=traceback.format_exc()
    finally:
        for p in owned_histories:
            try:command(p,'COPYLOG 0','WMP_TRACE_RESOURCE')
            except Exception as error:report['errors'].append('history cleanup '+p+': '+str(error));report['resource_lifecycle_pass']=False
        save(out/'receipt.json',report)
    print(out,flush=True)
    if not report['resource_lifecycle_pass']:raise SystemExit(1)

if __name__=='__main__':main()
