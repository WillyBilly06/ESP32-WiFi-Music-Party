import sys,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import local_ipc

class LocalConnection(unittest.TestCase):
    def test_post_bind_collision_retries_without_sending_or_replaying_request(self):
        first,second=Mock(),Mock();first.connect.side_effect=OSError(10048,'tuple still in TIME_WAIT')
        with patch.object(local_ipc.os,'name','nt'),patch.object(local_ipc.socket,'socket',side_effect=[first,second]),patch.object(local_ipc,'bind_task_port'):
            self.assertIs(local_ipc.connection(('127.0.0.1',48006)),second)
        first.close.assert_called_once();second.close.assert_not_called()
        first.sendall.assert_not_called();second.sendall.assert_not_called()
    def test_refused_server_is_not_restarted_or_retried(self):
        first=Mock();first.connect.side_effect=OSError(10061,'refused')
        with patch.object(local_ipc.os,'name','nt'),patch.object(local_ipc.socket,'socket',return_value=first) as create,patch.object(local_ipc,'bind_task_port'):
            with self.assertRaises(OSError):local_ipc.connection(('127.0.0.1',48006))
        self.assertEqual(create.call_count,1);first.close.assert_called_once();first.sendall.assert_not_called()

if __name__=='__main__':unittest.main()
