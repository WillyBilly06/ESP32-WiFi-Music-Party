"""Play actual YouTube music in a dedicated Linux Firefox profile/audio stream.

Local JSON control supports play/pause for the explicitly requested handover
tests. Playback failures are recorded; interrupted music is not auto-restarted.
"""
import argparse,json,os,signal,time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.ui import WebDriverWait

running=True
def stop(*_):
    global running
    running=False
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--sink',required=True);p.add_argument('--seconds',type=int,default=7200);p.add_argument('--directory',type=Path,required=True);p.add_argument('--url',default='https://www.youtube.com/results?search_query=lofi+hip+hop+radio+live');a=p.parse_args()
    a.directory.mkdir(parents=True,exist_ok=True);os.environ['PULSE_SINK']=a.sink
    row=dict(sink=a.sink,started=time.time(),ready=False,errors=[],audio_qualified=False);driver=None
    def save():
        path=a.directory/'playback.json';temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(row,indent=2)+'\n');temporary.replace(path)
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        options=Options();options.add_argument('-headless');options.binary_location='/snap/firefox/current/usr/lib/firefox/firefox'
        options.set_preference('media.autoplay.default',0);options.set_preference('media.autoplay.blocking_policy',0)
        options.set_preference('media.block-autoplay-until-in-foreground',False)
        driver=webdriver.Firefox(options=options,service=Service('/snap/bin/geckodriver',log_output=str(a.directory/'geckodriver.log')))
        (a.directory/'webdriver.json').write_text(json.dumps(dict(session=driver.session_id,url=driver.service.service_url)))
        driver.set_page_load_timeout(60);driver.get(a.url)
        if '/results?' in a.url:
            WebDriverWait(driver,40).until(lambda d:len(d.find_elements(By.CSS_SELECTOR,'ytd-video-renderer a#video-title'))>0)
            candidates=[]
            for renderer in driver.find_elements(By.CSS_SELECTOR,'ytd-video-renderer')[:15]:
                links=renderer.find_elements(By.CSS_SELECTOR,'a#video-title')
                if links:candidates.append(dict(title=links[0].get_attribute('title') or links[0].text,url=links[0].get_attribute('href'),live=bool(renderer.find_elements(By.CSS_SELECTOR,'.badge-style-type-live-now'))))
            row['candidates']=candidates;save()
            choices=[v for v in candidates if v['live']] or [v for v in candidates if 'radio' in v['title'].lower()]
            if not choices:raise RuntimeError('No live music result found; manual review required')
            driver.get(choices[0]['url'])
        WebDriverWait(driver,40).until(lambda d:d.find_elements(By.TAG_NAME,'video'))
        driver.execute_script('const v=document.querySelector("video"); v.muted=false; v.volume=1; const p=document.getElementById("movie_player"); if(p&&p.playVideo)p.playVideo();else v.play();')
        start=time.monotonic();last_command=None
        while running and time.monotonic()-start<a.seconds:
            control=a.directory/'control.json'
            if control.exists():
                command=json.loads(control.read_text())
                if command.get('id')!=last_command:
                    action=command['action']
                    if action not in ['play','pause','stop']:raise ValueError('Unknown playback control')
                    if action=='stop':break
                    driver.execute_script('const p=document.getElementById("movie_player"); if(p&&p.'+('playVideo' if action=='play' else 'pauseVideo')+')p.'+('playVideo' if action=='play' else 'pauseVideo')+'();else document.querySelector("video").'+action+'();')
                    last_command=command.get('id');row['last_control']=dict(id=last_command,action=action,at=time.time())
            state=driver.execute_script('const v=document.querySelector("video");const p=document.getElementById("movie_player");return v?{paused:v.paused,ended:v.ended,time:v.currentTime,ready:v.readyState,error:v.error?v.error.code:null,playerState:p&&p.getPlayerState?p.getPlayerState():null,playerText:p?p.innerText.substring(0,1200):"",hasSource:!!v.currentSrc}:null;')
            row.update(updated=time.time(),elapsed=time.monotonic()-start,url=driver.current_url,title=driver.title,video=state)
            if state and state['ready']>=2 and not state['paused']:row['ready']=True
            save();time.sleep(2)
    except Exception as error:
        row['errors'].append(type(error).__name__+': '+str(error))
        if driver:
            try:row['page_text']=driver.find_element(By.TAG_NAME,'body').text[:2500];driver.save_screenshot(str(a.directory/'failure.png'))
            except Exception:pass
    finally:
        row['finished']=time.time();save()
        if driver:driver.quit()
        print(json.dumps(row),flush=True)
if __name__=='__main__':main()
