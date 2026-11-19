import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from network_clock import failure

def fixture(anchor=False):
    return {'WMP_NETWORK':{'infrastructure':1,'status':0},'WMP_SOURCE_CLOCK':{'valid':1,'age_us':100,'span_us':25,'native_calls':30,'interface_sta':int(not anchor)},'WMP_CLOCK_DOMAIN':{'shared':1,'anchor_ap':int(anchor)},'WMP_ANCHOR_STAT':{'requested':int(anchor),'active':int(anchor)},'WMP_WIFI_MODE':{'ap':int(anchor)},'WMP_ASSOC':{'connected':int(not anchor)}}

class NetworkClock(unittest.TestCase):
    def passive_fixture(self):
        s=fixture();s['WMP_NETWORK']['infrastructure']=0;s['WMP_ASSOC']={'requested':0,'connected':0};s['WMP_SOURCE_CLOCK']['valid']=0;s['WMP_SOURCE_CLOCK']['native_calls']=0
        s.update(WMP_WIFI_STATE={'sniffer':1},WMP_RECEIVE_WINDOW={'enabled':1},WMP_AUDIO={'clock_valid':1,'clock_pairs':20},WMP_CLOCK_QUALITY={'fit_us':1,'ppm':23},WMP_BEACONS={'match_age_us':1000});return s
    def test_passive_hardware_clock_does_not_fake_native_tsf(self):
        s=self.passive_fixture();self.assertIsNone(failure(s));self.assertEqual(s['WMP_SOURCE_CLOCK']['native_calls'],0)
    def test_passive_clock_requires_actual_fresh_captures(self):
        for section,key,value in [('WMP_WIFI_STATE','sniffer',0),('WMP_AUDIO','clock_pairs',7),('WMP_BEACONS','match_age_us',1500001),('WMP_CLOCK_QUALITY','fit_us',1001)]:
            s=self.passive_fixture();s[section][key]=value;self.assertIsNotNone(failure(s))
    def test_passive_label_cannot_hide_an_association(self):
        s=self.passive_fixture();s['WMP_ASSOC']['connected']=1;self.assertIsNotNone(failure(s))
    def test_station_has_real_association(self):
        s=fixture();self.assertIsNone(failure(s));s['WMP_ASSOC']['connected']=0;self.assertIsNotNone(failure(s))
    def test_anchor_uses_ap_clock_without_faking_association(self):
        s=fixture(True);self.assertIsNone(failure(s));s['WMP_ASSOC']['connected']=1;self.assertIsNotNone(failure(s))
    def test_anchor_requires_ap_radio_and_native_ap_domain(self):
        for section,key in [('WMP_WIFI_MODE','ap'),('WMP_CLOCK_DOMAIN','anchor_ap'),('WMP_ANCHOR_STAT','active')]:
            s=fixture(True);s[section][key]=0;self.assertIsNotNone(failure(s))
    def test_clock_valid_flag_cannot_hide_missing_native_samples(self):
        for anchor in [False,True]:
            s=fixture(anchor);s['WMP_SOURCE_CLOCK']['native_calls']=0;self.assertIsNotNone(failure(s))
    def test_old_or_wide_clock_measurements_fail_both_roles(self):
        for anchor in [False,True]:
            for key,value in [('age_us',1000001),('span_us',1001),('valid',0)]:
                s=fixture(anchor);s['WMP_SOURCE_CLOCK'][key]=value;self.assertIsNotNone(failure(s))
    def test_unshared_clock_and_stopped_network_cannot_qualify(self):
        for section,key in [('WMP_CLOCK_DOMAIN','shared'),('WMP_NETWORK','infrastructure')]:
            s=fixture(True);s[section][key]=0;self.assertIsNotNone(failure(s))

if __name__=='__main__':unittest.main()
