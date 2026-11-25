import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock
from atlas_cluster.bridge import Bridge
from atlas_cluster.store import Store
from atlas_cluster.protocol import Remote
from rollback import remaining_settings


class MultiHostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root/'.state').mkdir()
        hosts = {h:{'remote_root':'/tmp/setup','projects':{'atlas':{'remote_cwd':'/tmp/project'}}} for h in ('dell1','dell2','spark1')}
        (self.root/'settings.json').write_text(json.dumps({'default_host':'dell1','model':'m','provider':'p','hosts':hosts}))
        (self.root/'REMOTE_INSTRUCTIONS.md').write_text('Bounded work')
        self.bridge = Bridge(self.root)
        for h in hosts:
            remote = Mock()
            remote.loaded = set()
            remote.last_error = None
            remote.connected.return_value = True
            remote.active_threads.return_value = []
            remote.request.side_effect = self.replies(h)
            self.bridge.remotes[h] = remote

    def replies(self,h):
        def reply(method,params):
            if method == 'thread/start':return {'thread':{'id':h+'-thread'}}
            if method == 'turn/start':return {'turn':{'id':h+'-turn'}}
            return {}
        return reply

    def tearDown(self):self.tmp.cleanup()

    def test_three_hosts_can_share_run_without_sharing_capacity(self):
        for h in self.bridge.remotes: self.bridge.dispatch('shared','work',host=h)
        self.assertEqual(len(self.bridge.store.status('shared')['runs']),3)
        for h in self.bridge.remotes:
            with self.assertRaises(ValueError):self.bridge.dispatch('second','work',host=h)

    def test_disconnected_host_does_not_block_others(self):
        self.bridge.store.save_run('shared','atlas','t1','turn1','running','dell1')
        self.bridge.store.save_run('shared','atlas','t2','turn2','running','dell2')
        self.bridge.store.disconnect_host('dell2')
        self.assertEqual(self.bridge.store.run('shared','dell1')['status'],'running')
        self.assertEqual(self.bridge.store.run('shared','dell2')['status'],'disconnected')
        self.bridge.dispatch('shared','work',host='spark1')

    def test_steering_is_routed_by_coordinator_mailbox(self):
        for h in self.bridge.remotes: self.bridge.dispatch('shared','work',host=h)
        for remote in self.bridge.remotes.values():remote.request.reset_mock()
        out=self.bridge.message('shared','laptop','remote-coordinator:spark1','hello',steer=True)
        self.assertEqual(out['delivery'],'queued_and_steered')
        self.bridge.remotes['dell1'].request.assert_not_called()
        self.bridge.remotes['dell2'].request.assert_not_called()
        method,params=self.bridge.remotes['spark1'].request.call_args.args
        self.assertEqual((method,params['threadId']),('turn/steer','spark1-thread'))

    def test_interrupt_only_selected_host(self):
        for h in self.bridge.remotes:self.bridge.dispatch('r','work',host=h)
        for remote in self.bridge.remotes.values():remote.request.reset_mock()
        self.bridge.interrupt('r','dell2')
        self.assertEqual(self.bridge.store.run('r','dell2')['status'],'interrupted')
        self.assertEqual(self.bridge.store.run('r','dell1')['status'],'running')
        self.bridge.remotes['spark1'].request.assert_not_called()

    def test_run_events_are_host_scoped(self):
        for h in ('dell1','dell2'):self.bridge.store.save_run('r','atlas','same-id','turn','running',h)
        real=Remote({'host':'dell2'},self.root/'socket',self.root,self.bridge.store)
        real._event({'method':'turn/completed','params':{'threadId':'same-id','turn':{'id':'turn','status':'completed'}}})
        self.assertEqual(self.bridge.store.run('r','dell2')['status'],'completed')
        self.assertEqual(self.bridge.store.run('r','dell1')['status'],'running')
        self.assertEqual(self.bridge.store.events('r')['events'][0]['data']['host'],'dell2')

    def test_dispatch_locks_do_not_block_other_hosts(self):
        self.bridge.control_locks['dell2'].acquire()
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                result=pool.submit(self.bridge.dispatch,'r','work',host='spark1').result(timeout=2)
            self.assertEqual(result['host'],'spark1')
        finally:self.bridge.control_locks['dell2'].release()

    def test_claims_and_artifacts_stay_shared_across_hosts(self):
        self.bridge.store.task_claim('r','shared-proof','dell2:worker')
        with self.assertRaises(ValueError):self.bridge.store.task_claim('r','shared-proof','spark1:worker')
        self.bridge.store.artifact_put('r','proof','dell2:worker','one')
        self.bridge.store.artifact_put('r','proof','spark1:worker','two',expected_revision=1)
        self.assertEqual(self.bridge.store.artifact_get('r','proof')['revision'],2)

    def test_legacy_run_migration_preserves_history(self):
        path=self.root/'legacy.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE runs (run TEXT PRIMARY KEY,project TEXT,thread TEXT,turn TEXT,status TEXT,updated REAL)')
            db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?)',('old','atlas','thread','turn','completed',123.0))
        migrated=Store(path)
        self.assertEqual(migrated.run('old')['thread'],'thread')
        self.assertEqual(migrated.run('old')['host'],'dell1')
        migrated.save_run('old','atlas','second',None,'completed','dell2')
        again=Store(path)
        self.assertEqual(len(again.status('old')['runs']),2)

    def test_unknown_host_rejected_before_any_remote_call(self):
        with self.assertRaises(ValueError):self.bridge.dispatch('r','work',host='unknown')
        for remote in self.bridge.remotes.values():remote.connect.assert_not_called()

    def test_targeted_rollback_keeps_original_host_and_settings(self):
        old = self.bridge.settings
        kept = remaining_settings(old,['dell2','spark1'])
        self.assertEqual(list(kept['hosts']),['dell1'])
        self.assertEqual(kept['model'],old['model'])
        self.assertEqual(len(old['hosts']),3)
        with self.assertRaises(ValueError): remaining_settings(old,['dell1'])
        with self.assertRaises(ValueError): remaining_settings(old,['unknown'])

    def test_capacity_reads_loaded_coordinators_and_attached_children(self):
        remote=Remote({'host':'dell2'},self.root/'socket',self.root,self.bridge.store)
        remote.request=Mock(side_effect=[
            {'data':['coordinator','child'],'nextCursor':'next'},
            {'thread':{'id':'coordinator','source':'vscode','status':{'type':'active'}}},
            {'thread':{'id':'child','parentThreadId':'coordinator','status':{'type':'active'}}},
            {'data':['finished']},
            {'thread':{'id':'finished','status':{'type':'idle'}}},
        ])
        active=remote.active_threads()
        self.assertEqual([t['thread_id'] for t in active],['coordinator','child'])
        self.assertEqual(remote.request.call_args_list[0].args,('thread/loaded/list',{'limit':100}))
        self.assertEqual(remote.request.call_args_list[3].args,('thread/loaded/list',{'limit':100,'cursor':'next'}))


if __name__=='__main__':unittest.main()
