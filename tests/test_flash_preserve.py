import hashlib,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from flash_preserve import preserve
class Device:
    def __init__(self,data):self.data=data
    def flash_md5sum(self,offset,n):return hashlib.md5(self.data[offset:offset+n]).hexdigest()
class Preserve(unittest.TestCase):
    def run_case(self,data,cached,n):
        calls=[];device=Device(data)
        def fresh(offset,length,label):calls.append((offset,length));return data[offset:offset+length]
        with tempfile.TemporaryDirectory() as directory:
            raw,origin=preserve(device,0,n,'prior',directory,{0:{'raw':cached,'path':'verified-image.bin'}},fresh)
        self.assertEqual(raw,data[:n]);return calls,origin
    def test_verified_match_uses_device_hash(self):self.assertEqual(self.run_case(b'a'*4096,b'a'*4096,4096)[0],[])
    def test_changed_device_forces_full_fresh_read(self):self.assertEqual(self.run_case(b'b'*4096,b'a'*4096,4096)[0],[(0,4096)])
    def test_growth_reads_uncached_tail(self):self.assertEqual(self.run_case(b'a'*4096+b'b'*4096,b'a'*4096,8192)[0],[(4096,4096)])
    def test_shrinking_reads_matching_prefix(self):self.assertEqual(self.run_case(b'a'*4096+b'b'*4096,b'a'*4096+b'b'*4096,4096)[0],[])
    def test_final_device_mutation_is_rejected(self):
        class Changing(Device):
            count=0
            def flash_md5sum(self,offset,n):
                self.count+=1;return super().flash_md5sum(offset,n) if self.count==1 else 'bad'
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):preserve(Changing(b'a'*4096),0,4096,'prior',directory,{0:{'raw':b'a'*4096,'path':'verified'}},lambda *a:b'')
if __name__=='__main__':unittest.main()
