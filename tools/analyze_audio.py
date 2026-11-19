"""Assess a finite live capture without treating counters as acoustic proof."""
import argparse,json
from pathlib import Path

def assess(row):
    failures=[];warnings=[];samples=row.get('samples',[])
    if len(samples)<2:
        return dict(digital_pass=False,failures=['Incomplete acquisition'],warnings=[],audio_qualified=False)
    if not row.get('acquisition_complete'):failures.append('Planned acquisition did not complete: '+str(row.get('terminal_reason','incomplete')))
    roles=[]
    for sample in samples:
        source=[p for p,v in sample['ports'].items() if v['lines']['WMP_RADIO']['mode']==2]
        roles.append(source[0] if len(source)==1 else None)
    if not roles[0] or any(p!=roles[0] for p in roles):
        return dict(digital_pass=False,failures=['Source role changed or was ambiguous; split the windows'],warnings=[],audio_qualified=False)
    source=roles[0];receiver=next(p for p in samples[0]['ports'] if p!=source)
    duration=samples[-1]['elapsed']-samples[0]['elapsed']
    for s in samples:
        b=s['ports'][source]['lines']['WMP_BT'];a=s['ports'][receiver]['lines']['WMP_AUDIO']
        if not all(b.get(k) for k in ['working','phone','streaming','ble']):
            failures.append('A2DP and BLE were not active on the source in every sample');break
        if not a.get('running') or not a.get('clock_valid'):
            failures.append('Receiver playback/clock unavailable in a sample');break
    first,last=samples[0]['ports'],samples[-1]['ports'];deltas={}
    for port in first:
        deltas[port]={}
        for section,keys in {
          'WMP_BT':['frames','ble_writes','rtp_gaps','rtp_bad','blocked_frames'],
          'WMP_RADIO':['qfull','api_error','timeout','fail','callback_qfull','late'],
          'WMP_RX':['qfull','bad','conflict','unique'],
          'WMP_AUDIO':['qfull','decoded','codec_bad','pcm_bad','concealed','late','write_errors','underrun','output_frames']}.items():
            deltas[port][section]={}
            for key in keys:
                a=first[port]['lines'][section][key];b=last[port]['lines'][section][key];delta=b-a
                deltas[port][section][key]=delta
                if delta<0:failures.append(port+' counter reset: '+section+'.'+key)
                elif delta and ((section=='WMP_AUDIO' and key not in ['decoded','output_frames']) or
                               (section=='WMP_BT' and key in ['rtp_gaps','rtp_bad','blocked_frames']) or
                               (section=='WMP_RADIO' and key not in ['late']) or
                               (section=='WMP_RX' and key=='qfull')):
                    failures.append(f'{port} {section}.{key} increased by {delta}')
                elif delta and ((section=='WMP_RADIO' and key=='late') or (section=='WMP_RX' and key in ['bad','conflict'])):
                    warnings.append(f'{port} {section}.{key} increased by {delta}; delivery-stage diagnostic')
    source_bt=deltas[source]['WMP_BT']
    for port in [source,receiver]:
        if 'WMP_DMA' in first[port]['lines'] and 'WMP_DMA' in last[port]['lines']:
            before=first[port]['lines']['WMP_DMA'];after=last[port]['lines']['WMP_DMA']
            deltas[port]['WMP_DMA']={k:after[k]-before[k] for k in ['submitted','emitted','queue_overflow','events']}
            deltas[port]['WMP_DMA'].update(pending=after['pending'],deficit_max=after['deficit_max'],gap_max_us=after['gap_max_us'])
            if deltas[port]['WMP_DMA']['queue_overflow']:failures.append(port+' I2S free-buffer queue overflowed')
        if 'WMP_REPLAY' in first[port]['lines'] and 'WMP_REPLAY' in last[port]['lines']:
            before=first[port]['lines']['WMP_REPLAY'];after=last[port]['lines']['WMP_REPLAY']
            deltas[port]['WMP_REPLAY']={k:after[k]-before[k] for k in ['nack_queued','nack_sent','nack_heard','suppressed','queued','cache_miss','deferred','too_late','received','delivered','played']}
            for key in ['cache_miss','deferred','too_late']:
                if deltas[port]['WMP_REPLAY'][key]:warnings.append(f'{port} full-packet repair {key}: {deltas[port]["WMP_REPLAY"][key]}')
        if 'WMP_SOURCE_QUEUE' in first[port]['lines'] and 'WMP_SOURCE_QUEUE' in last[port]['lines']:
            before=first[port]['lines']['WMP_SOURCE_QUEUE'];after=last[port]['lines']['WMP_SOURCE_QUEUE']
            deltas[port]['WMP_SOURCE_QUEUE']={k:after[k]-before[k] for k in ['deferred','full','playback_deferred']}
            deltas[port]['WMP_SOURCE_QUEUE'].update(groups=after['groups'],bytes=after['bytes'],max_groups=after['max_groups'],max_bytes=after['max_bytes'],max_age_us=after['max_age_us'])
            if deltas[port]['WMP_SOURCE_QUEUE']['full']:failures.append(port+' source encoded byte queue reached capacity')
        if 'WMP_BT_LINKS' in first[port]['lines'] and 'WMP_BT_LINKS' in last[port]['lines']:
            a=first[port]['lines']['WMP_BT_LINKS'];b=last[port]['lines']['WMP_BT_LINKS'];deltas[port]['WMP_BT_LINKS']={k:b[k]-a[k] for k in ['classic_disconnects','ble_disconnects','connection_failures']}
            if any(deltas[port]['WMP_BT_LINKS'].values()):failures.append(port+' Bluetooth link failure/reconnection during window')
        if 'WMP_BUFFER' in first[port]['lines'] and 'WMP_BUFFER' in last[port]['lines']:
            before=first[port]['lines']['WMP_BUFFER'];after=last[port]['lines']['WMP_BUFFER']
            deltas[port]['WMP_BUFFER']={k:after[k]-before[k] for k in ['queued_on_time_late','input_late','startup_skips']}
            deltas[port]['WMP_BUFFER']['held_max']=after['held_max'];deltas[port]['WMP_BUFFER']['queue_wait_max_us']=after['queue_wait_max_us']
    # FEC is removed from delivered firmware (WMP_COPY prints fec=0); parity scheduling is a failure, not a feature.
    if any(s['ports'][source]['lines'].get('WMP_COPY',{}).get('fec',0) for s in samples):failures.append('Source reports FEC parity enabled; delivered firmware must not schedule parity')
    if source_bt['ble_writes']<max(1,duration/2):failures.append('Insufficient source BLE writes for sustained workload coverage')
    if source_bt['frames']<=0:failures.append('Source SBC did not advance')
    if deltas[receiver]['WMP_RX']['unique']<=0:failures.append('Receiver useful packet count did not advance')
    for port in [source,receiver]:
        rate=last[port]['lines']['WMP_AUDIO']['rate'];output=deltas[port]['WMP_AUDIO']['output_frames']
        if not rate or not .95*duration*rate<=output<=1.05*duration*rate:
            failures.append(port+' output sample progression outside5% of elapsed playback time')
        for a,b in zip(samples,samples[1:]):
            if b['ports'][port]['lines']['WMP_RADIO']['time_us']<=a['ports'][port]['lines']['WMP_RADIO']['time_us']:
                failures.append(port+' reboot/nonmonotonic device time');break
    return dict(digital_pass=not failures,failures=failures,warnings=warnings,source=source,receiver=receiver,
                seconds=duration,deltas=deltas,audio_qualified=False,
                limits=['No microphone or electrical output capture','No physical latency or inter-speaker skew measurement',
                        'Not a one-hour result unless actual duration covers it','No100-receiver capacity proof'])

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('receipt',type=Path);a=p.parse_args()
    result=assess(json.loads(a.receipt.read_text()));out=a.receipt.parent/'digital-assessment.json'
    out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(dict(evidence=str(out),**result),indent=2))
if __name__=='__main__':main()
