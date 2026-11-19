"""Digital assessment treats transmitter queue overflow and enabled FEC as failures."""
import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from analyze_audio import assess

def lines(mode,t,n,qfull=0,fec=0):
    return {'WMP_RADIO':dict(mode=mode,time_us=t,qfull=qfull,api_error=0,timeout=0,fail=0,callback_qfull=0,late=0),
            'WMP_BT':dict(working=1,phone=1,streaming=1,ble=1,frames=100*n,ble_writes=10*n,rtp_gaps=0,rtp_bad=0,blocked_frames=0),
            'WMP_RX':dict(qfull=0,bad=0,conflict=0,unique=50*n),
            'WMP_AUDIO':dict(running=1,clock_valid=1,rate=48000,qfull=0,decoded=0,codec_bad=0,pcm_bad=0,concealed=0,late=0,write_errors=0,underrun=0,output_frames=48000*10*n),
            'WMP_COPY':dict(fec=fec),'WMP_SOURCE_QUEUE':dict(deferred=0,full=0,playback_deferred=0,groups=0,bytes=0,max_groups=0,max_bytes=0,max_age_us=0)}

def row(qfull=0,fec=0):
    samples=[dict(elapsed=10*n,ports={'COM7':dict(lines=lines(2,10_000_000*(n+1),n,qfull*n,fec)),'COM6':dict(lines=lines(1,10_000_000*(n+1),n))}) for n in range(2)]
    return dict(acquisition_complete=True,samples=samples)

class Assessment(unittest.TestCase):
    def test_clean_window_passes(self):
        r=assess(row());self.assertTrue(r['digital_pass'],r['failures']);self.assertNotIn('fec_exercised',r)
    def test_radio_qfull_fails_even_with_source_queue_present(self):
        r=assess(row(qfull=3));self.assertFalse(r['digital_pass']);self.assertIn('COM7 WMP_RADIO.qfull increased by 3',r['failures'])
    def test_enabled_fec_is_a_failure(self):
        r=assess(row(fec=1));self.assertFalse(r['digital_pass']);self.assertTrue(any('FEC' in f for f in r['failures']))

if __name__=='__main__':unittest.main()
