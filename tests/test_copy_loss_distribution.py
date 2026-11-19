"""Known receipt populations distinguish independent and joint outages."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from measure_copy_loss import distribution

class CopyPopulation(unittest.TestCase):
    def test_uniform_independent_four_bit_outcomes(self):
        r=distribution([10]*16)
        self.assertEqual(r['per_copy_receipt'],[.5]*4)
        self.assertEqual(r['observed_to_independent'],1)
    def test_common_outages_are_not_four_independent_chances(self):
        masks=[0]*16;masks[0]=masks[15]=100;r=distribution(masks)
        self.assertEqual(r['independent_prediction'],.0625)
        self.assertEqual(r['observed_all_absent_rate'],.5)
        self.assertEqual(r['observed_to_independent'],8)
    def test_no_groups_does_not_become_a_zero_loss_result(self):
        with self.assertRaises(ValueError):distribution([0]*16)

if __name__=='__main__':unittest.main()
