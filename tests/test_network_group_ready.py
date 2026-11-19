"""Regression for the captured two-leader Wi-Fi recovery result."""
import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from test_network_liveness import ready

def group():
    def row(source):
        return {'lines':{'WMP_ASSOC':{'connected':1},'WMP_SOURCE_CLOCK':{'valid':1},
          'WMP_SESSION':{'generation':77,'source':source},'WMP_RADIO':{'mode':2 if source else 0},
          'WMP_BT':{'rate':44100},'WMP_AUDIO':{'running':1,'clock_valid':1,'rate':44100},
          'WMP_PREPARATION':{'source_wait':0,'receiver_wait':0}}}
    return {'COM6':row(1),'COM7':row(0)}

class Recovery(unittest.TestCase):
    def test_one_shared_group_is_ready(self):self.assertTrue(ready(group()))
    def test_captured_rejoining_node_must_not_play_its_own_stream(self):
        s=group();v=s['COM7']['lines'];v['WMP_RADIO']['mode']=2;v['WMP_SESSION']={'generation':88,'source':1};v['WMP_AUDIO']['rate']=v['WMP_BT']['rate']=48000
        self.assertFalse(ready(s))
    def test_independent_epochs_are_not_group_recovery(self):
        s=group();s['COM7']['lines']['WMP_SESSION']['generation']=88;self.assertFalse(ready(s))

if __name__=='__main__':unittest.main()
