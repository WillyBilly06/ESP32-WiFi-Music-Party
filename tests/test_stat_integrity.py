import sys,unittest,zlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from stat_integrity import validate

NONCE='0123abcd'
def fixture(body=None):
    body=body or 'WMP_TX_WINDOW query='+NONCE+' active_limit=24 allocated=32\nWMP_STAT_END query='+NONCE+' heap=35000\n'
    return 'WMP_STAT_BEGIN query='+NONCE+' schema=2\n'+body+f'WMP_STAT_CRC query={NONCE} valid=1 bytes={len(body.encode())} crc={zlib.crc32(body.encode())} allocation_failed=0\n'

class Integrity(unittest.TestCase):
    def test_valid_and_uart_line_endings(self):
        self.assertEqual(validate(fixture(),NONCE)['schema'],2)
        self.assertEqual(validate(fixture(),NONCE),validate(fixture().replace('\n','\r\r\n'),NONCE))
    def test_dropped_digit_is_rejected(self):
        with self.assertRaises(ValueError):validate(fixture().replace('active_limit=24','active_limit=2'),NONCE)
    def test_same_length_numeric_corruption_is_rejected(self):
        with self.assertRaises(ValueError):validate(fixture().replace('heap=35000','heap=95000'),NONCE)
    def test_missing_or_stale_row_is_rejected(self):
        with self.assertRaises(ValueError):validate(fixture().replace('WMP_TX_WINDOW query='+NONCE,'WMP_TX_WINDOW query=ffffffff'),NONCE)
        with self.assertRaises(ValueError):validate('\n'.join(fixture().splitlines()[:-1]),NONCE)
    def test_missing_terminal_row_is_rejected_even_with_matching_crc(self):
        with self.assertRaises(ValueError):validate(fixture('WMP_TX_WINDOW query='+NONCE+' active_limit=24\n'),NONCE)
    def test_other_task_console_noise_is_not_part_of_stat(self):
        good=fixture();noisy=good.replace('WMP_STAT_END','WMP_A2DP_COEX status=0\nWMP_STAT_END')
        self.assertEqual(validate(good,NONCE),validate(noisy,NONCE))
    def test_overflow_and_allocation_failures_rejected(self):
        with self.assertRaises(ValueError):validate(fixture().replace('valid=1','valid=0'),NONCE)
        with self.assertRaises(ValueError):validate(fixture().replace('allocation_failed=0','allocation_failed=1'),NONCE)
    def test_duplicate_envelope_is_rejected(self):
        with self.assertRaises(ValueError):validate(fixture()+fixture(),NONCE)

if __name__=='__main__':unittest.main()
