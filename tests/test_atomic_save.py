"""Concurrent receipt writers must not share a temporary filename."""
import concurrent.futures,json,sys,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from board_io import save

class AtomicSave(unittest.TestCase):
    def test_concurrent_writers_and_readers(self):
        with tempfile.TemporaryDirectory(prefix='atomic-save-',dir=ROOT/'build') as folder:
            directory=Path(folder).resolve();self.assertTrue(directory.is_relative_to((ROOT/'build').resolve()))
            path=directory/'receipt.json';save(path,{'writer':-1,'iteration':-1,'payload':'x'*4096})
            def write(writer):
                for i in range(25):save(path,{'writer':writer,'iteration':i,'payload':str(writer)*4096})
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                jobs=[pool.submit(write,i) for i in range(8)]
                while not all(j.done() for j in jobs):
                    # Windows may temporarily deny opening a file being
                    # replaced. Successful reads must still be whole JSON.
                    try:r=json.loads(path.read_text())
                    except PermissionError:time.sleep(.002);continue
                    self.assertEqual(len(r['payload']),4096)
                    time.sleep(.002)
                for j in jobs:j.result()
            self.assertEqual(json.loads(path.read_text())['iteration'],24)
            self.assertEqual(list(directory.glob('*.tmp')),[])
if __name__=='__main__':unittest.main()
