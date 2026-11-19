"""Direct real-music A2DP source with observable GStreamer state; no auto-restart."""
import argparse,json,signal,subprocess,time,urllib.request,os
from pathlib import Path
import gi
gi.require_version('Gst','1.0')
from gi.repository import Gst

running=True
def stop(*_):
    global running
    running=False
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--sink',required=True);p.add_argument('--directory',type=Path,required=True);p.add_argument('--seconds',type=int,default=7200);a=p.parse_args()
    a.directory.mkdir(parents=True,exist_ok=True);Gst.init(None)
    row=dict(kind='direct-music-stream',title='SomaFM Groove Salad',started=time.time(),ready=False,errors=[],audio_qualified=False,pause_mode='decoded-output-valve',http_reopens=0)
    def save():
        path=a.directory/'playback.json';tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(row,indent=2)+'\n');tmp.replace(path)
    player=Gst.ElementFactory.make('playbin','wmp_music');sink=Gst.ElementFactory.make('pulsesink','wmp_bluetooth');sink.set_property('device',a.sink)
    gate=Gst.ElementFactory.make('valve','wmp_output_gate');gate.set_property('drop-mode',1)
    output=Gst.Bin.new('wmp_bluetooth_output');output.add(gate);output.add(sink)
    if not gate.link(sink):raise RuntimeError('Cannot link owned Bluetooth output gate')
    output.add_pad(Gst.GhostPad.new('sink',gate.get_static_pad('sink')));player.set_property('audio-sink',output)
    # A suspended Bluetooth clock must not freeze the live HTTP timeline.
    player.use_clock(Gst.SystemClock.obtain())
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        playlist=urllib.request.urlopen('https://somafm.com/groovesalad256.pls',timeout=15).read().decode()
        url=next(line.split('=',1)[1].strip() for line in playlist.splitlines() if line.startswith('File1='));row['url']=url
        player.set_property('uri',url);player.set_state(Gst.State.PLAYING);bus=player.get_bus();start=time.monotonic();last=None;paused=False;rendered_before=0;last_rendered=None;route_was_valid=False
        while running and time.monotonic()-start<a.seconds:
            path=a.directory/'control.json'
            if path.exists():
                control=json.loads(path.read_text())
                if control.get('id')!=last:
                    action=control['action']
                    if action=='stop':break
                    if action not in ['play','pause']:raise ValueError('Unknown media control')
                    was_paused=paused;paused=action=='pause';last=control['id']
                    # Keep the single live HTTP connection/decoder running.
                    # Planned pause discards only pre-encoding host PCM and
                    # suspends this exact Bluetooth transport. Repeated HTTP
                    # teardown/reopen previously ended a handover fixture.
                    if paused:
                        gate.set_property('drop',True)
                        # Disconnect only this output client. An uncorked
                        # pulsesink otherwise keeps AVDTP started despite its
                        # suspended sink. Upstream HTTP stays in PLAYING.
                        sink.set_locked_state(True);sink.set_state(Gst.State.READY)
                    # Explicitly suspend just this test sink so handovers test
                    # transport pause rather than PipeWire's idle timeout.
                    if not paused or not was_paused:
                        subprocess.run(['pactl','suspend-sink',a.sink,'1' if paused else '0'],check=True,capture_output=True,timeout=5)
                    if not paused:
                        sink.set_locked_state(False);sink.sync_state_with_parent()
                        gate.set_property('drop',False)
                    row['last_control']=dict(id=last,action=action,at=time.time())
            message=bus.timed_pop_filtered(0,Gst.MessageType.ERROR|Gst.MessageType.EOS)
            if message:
                if message.type==Gst.MessageType.ERROR:
                    error,debug=message.parse_error();raise RuntimeError(str(error)+' '+str(debug))
                raise RuntimeError('Music stream ended')
            _,state,_=player.get_state(0);ok,position=player.query_position(Gst.Format.TIME)
            playing=state==Gst.State.PLAYING and not paused
            stats=sink.get_property('stats');rendered=int(stats.get_value('rendered'))
            if rendered<rendered_before:last_rendered=None
            if rendered>rendered_before:last_rendered=time.monotonic()
            rendered_before=rendered
            sinks=json.loads(subprocess.check_output(['pactl','--format=json','list','sinks'],text=True,timeout=5))
            expected={v['index'] for v in sinks if v['name']==a.sink}
            inputs=json.loads(subprocess.check_output(['pactl','--format=json','list','sink-inputs'],text=True,timeout=5))
            routes=[v for v in inputs if str(v.get('properties',{}).get('application.process.id'))==str(os.getpid())]
            routed=bool(expected and routes and all(v['sink'] in expected and not v.get('corked',False) for v in routes))
            if playing and route_was_valid and not routed:raise RuntimeError('Owned Bluetooth route disappeared; refusing fallback output')
            route_was_valid=playing and routed
            fresh=last_rendered is not None and time.monotonic()-last_rendered<3
            actual=playing and routed and fresh
            row.update(updated=time.time(),elapsed=time.monotonic()-start,ready=actual,audio=dict(paused=not playing,ready=4 if actual else 0,time=position/Gst.SECOND if ok else None,error=None,buffers_rendered=rendered,sink_route_verified=routed,render_fresh=fresh),state=state.value_nick)
            save();time.sleep(1)
    except Exception as error:row['errors'].append(repr(error))
    finally:
        sink.set_locked_state(False);player.set_state(Gst.State.NULL);row['finished']=time.time();row['ready']=False
        row.setdefault('audio',{})['paused']=True
        save();print(json.dumps(row),flush=True)
if __name__=='__main__':main()
