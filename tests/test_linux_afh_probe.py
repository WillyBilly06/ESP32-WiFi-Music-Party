"""No adapter access: verify filtering that protects AFH restoration."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from linux_afh_probe import completion

class Completion(unittest.TestCase):
    def test_unrelated_events_do_not_complete_or_abort_restore(self):
        for event in ['04130100','040e0502480c0000','040f040002480c']:
            self.assertIsNone(completion(bytes.fromhex(event),0x0c49))
        self.assertEqual(completion(bytes.fromhex('040e0402490c00'),0x0c49),b'')
        self.assertEqual(completion(bytes.fromhex('040e0502480c0001'),0x0c48),b'\x01')
    def test_matching_failure_and_truncated_complete_are_errors(self):
        for event in ['040e0502490c00','040e0402490c12','040f041202490c']:
            with self.assertRaises(RuntimeError):completion(bytes.fromhex(event),0x0c49)

if __name__=='__main__':unittest.main()
