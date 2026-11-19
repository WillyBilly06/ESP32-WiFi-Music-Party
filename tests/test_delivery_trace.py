import struct,sys,unittest,zlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from delivery_trace import parse,control_state

class DeliveryTraceTests(unittest.TestCase):
    def sample(self):
        crc=zlib.crc32(struct.pack('<IIiBBB',0xfffffff0,123,-1,3,2,1))
        return ('WMP_DELIVERY_META query=00abcd12 generation=4 seq=9 known=1 sample=128 pts=1000 rate=44100 samples=512 overflow=0 bytes=1640\n'
                f'WMP_DELIVERY query=00abcd12 index=0 stage=3 copy=2 flags=1 tag=123 at_us32=4294967280 status=-1 crc={crc}\n'
                'WMP_DELIVERY_END query=00abcd12 loaded=1 count=1 allocation_failed=0\n')
    def test_external_crc_reference_and_signed_status(self):
        self.assertEqual(parse(self.sample(),'00abcd12')['rows'][0]['status'],-1)
    def test_documented_control_state_word(self):
        self.assertEqual(control_state(537135397),dict(type=5,mode=2,replay_enabled=True,transition_pending=False,signature=[2,1],payload_length_capped=8))
    def test_corruption_truncation_wrong_nonce_and_overflow_are_not_evidence(self):
        for raw,n in [(self.sample().replace('tag=123','tag=124'),'00abcd12'),(self.sample().replace('count=1','count=2'),'00abcd12'),(self.sample(),'00abcd13'),(self.sample().replace('overflow=0','overflow=1'),'00abcd12')]:
            with self.subTest(raw=raw,n=n),self.assertRaises(ValueError):parse(raw,n)

if __name__=='__main__':unittest.main()
