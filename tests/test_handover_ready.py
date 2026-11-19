import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from test_two_phone_handover import playback_ready

def state():
    def row(role):return dict(lines={'WMP_SESSION':dict(generation=77,source=role),'WMP_BT':dict(rate=48000),'WMP_RADIO':dict(mode=2 if role else 0),'WMP_AUDIO':dict(running=1,rate=48000,clock_valid=1),'WMP_PREPARATION':dict(source_wait=0,receiver_wait=0)})
    return {'COM6':row(1),'COM7':row(0)}
class Ready(unittest.TestCase):
    def test_shared_valid_session_with_matching_output_is_ready(self):self.assertTrue(playback_ready(state(),'COM6','COM7'))
    def test_zero_or_different_epochs_cannot_pass(self):
        for gen in (0,78):
            s=state();s['COM7']['lines']['WMP_SESSION']['generation']=gen
            if not gen:s['COM6']['lines']['WMP_SESSION']['generation']=0
            self.assertFalse(playback_ready(s,'COM6','COM7'))
    def test_source_preparation_and_wrong_output_rate_cannot_pass(self):
        s=state();s['COM6']['lines']['WMP_PREPARATION']['source_wait']=1;self.assertFalse(playback_ready(s,'COM6','COM7'))
        s=state();s['COM7']['lines']['WMP_AUDIO']['rate']=44100;self.assertFalse(playback_ready(s,'COM6','COM7'))
    def test_wrong_role_or_invalid_receiver_clock_cannot_pass(self):
        s=state();s['COM7']['lines']['WMP_SESSION']['source']=1;self.assertFalse(playback_ready(s,'COM6','COM7'))
        s=state();s['COM7']['lines']['WMP_AUDIO']['clock_valid']=0;self.assertFalse(playback_ready(s,'COM6','COM7'))
if __name__=='__main__':unittest.main()
