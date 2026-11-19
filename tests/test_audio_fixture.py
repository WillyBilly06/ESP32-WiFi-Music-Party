import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import audio_fixture as fixture

class Fixture(unittest.TestCase):
    def test_linux_replacement_never_requests_samsung_access(self):
        with tempfile.TemporaryDirectory(prefix='audio-fixture-',dir=ROOT/'build') as folder:
            root=Path(folder).resolve();self.assertTrue(root.is_relative_to((ROOT/'build').resolve()));(root/'hardware').mkdir();p=root/'hardware/admitted-linux-host.json'
            p.write_text(json.dumps(dict(testing_enabled=True,bluetooth_controller='E8:47:3A:EC:51:9A')))
            with patch.object(fixture,'require_testing') as guard:
                r=fixture.require('redmagic-linux',root);guard.assert_called_once_with(fixture.SERIAL)
            self.assertFalse(r['two_real_phones']);fixture.check_sources(r,[fixture.SERIAL,'linux'])
            with self.assertRaises(RuntimeError):fixture.check_sources(r,[fixture.SERIAL,fixture.SAMSUNG])
            p.write_text(json.dumps(dict(testing_enabled=False,bluetooth_controller='E8:47:3A:EC:51:9A')))
            with patch.object(fixture,'require_testing'),self.assertRaises(PermissionError):fixture.require('redmagic-linux',root)

    def test_phone_fixture_still_requires_samsung_release(self):
        def guard(serial):
            if serial==fixture.SAMSUNG:raise PermissionError('reserved')
        with patch.object(fixture,'require_testing',side_effect=guard),self.assertRaises(PermissionError):fixture.require('redmagic-samsung')

if __name__=='__main__':unittest.main()
