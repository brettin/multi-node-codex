import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from atlas_cluster.bridge import Bridge
from rollback import remove_block


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'.state').mkdir()
        (self.root/'settings.json').write_text(json.dumps({'host':'test','remote_root':'/tmp/test','model':'model','provider':'provider','projects':{'atlas':{'remote_cwd':'/tmp/project'}}}))
        (self.root/'REMOTE_INSTRUCTIONS.md').write_text('Bounded test')
        self.bridge=Bridge(self.root)
        self.bridge.remote=Mock()
        self.bridge.remote.loaded=set()
        self.bridge.remote.active_threads.return_value=[]
        self.bridge.remote.connected.return_value=True

    def tearDown(self):self.tmp.cleanup()

    def test_message_never_starts_an_idle_turn(self):
        result=self.bridge.message('r','local','remote-coordinator','hello',steer=True)
        self.assertIn('steer_error',result)
        self.bridge.remote.request.assert_not_called()
        self.assertEqual(len(self.bridge.store.inbox('r','remote-coordinator')['messages']),1)

    def test_steer_checks_active_turn_and_keeps_inbox_copy(self):
        self.bridge.store.save_run('r','atlas','thread','turn','running')
        result=self.bridge.message('r','local','remote-coordinator','hello',steer=True)
        self.assertEqual(result['delivery'],'queued_and_steered')
        method,params=self.bridge.remote.request.call_args.args
        self.assertEqual(method,'turn/steer');self.assertEqual(params['expectedTurnId'],'turn')
        self.assertEqual(len(self.bridge.store.inbox('r','remote-coordinator')['messages']),1)

    def test_dispatch_refuses_a_second_active_coordinator(self):
        self.bridge.store.save_run('r','atlas','t','turn','running')
        with self.assertRaises(ValueError):self.bridge.dispatch('another','work')
        self.bridge.remote.request.assert_not_called()

    def test_dispatch_refuses_native_children_still_running(self):
        self.bridge.remote.active_threads.return_value=[{'thread_id':'child'}]
        with self.assertRaises(ValueError):self.bridge.dispatch('r','work')
        self.bridge.remote.request.assert_not_called()

    def test_unknown_dispatch_outcome_blocks_blind_retry(self):
        self.bridge.remote.request.side_effect=[{'thread':{'id':'t'}},TimeoutError('unknown outcome')]
        with self.assertRaises(TimeoutError):self.bridge.dispatch('r','work')
        self.assertEqual(self.bridge.store.run('r')['status'],'disconnected')
        with self.assertRaises(ValueError):self.bridge.dispatch('r','work')

    def test_disconnect_marks_running_turn_indeterminate(self):
        self.bridge.store.save_run('r','atlas','t','turn','running')
        after=Bridge(self.root)
        self.assertEqual(after.store.run('r')['status'],'disconnected')

    def test_rollback_preserves_unrelated_changes(self):
        block='\n\n# BEGIN\n[mcp_servers.atlas_team]\ncommand="python"\n# END\n'
        original='model="existing"\n'
        added='\n[unrelated]\nvalue=42\n'
        self.assertEqual(remove_block(original+block+added,block),original+added)
        with self.assertRaises(ValueError):remove_block(original+block.replace('python','changed'),block)


if __name__=='__main__':unittest.main()
