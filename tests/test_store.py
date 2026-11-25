import concurrent.futures
import tempfile
import time
import unittest
from pathlib import Path
from atlas_cluster.store import Store


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'test.sqlite3')

    def tearDown(self): self.tmp.cleanup()

    def test_concurrent_owners_cannot_claim_same_task(self):
        def claim(i):
            try: return self.store.task_claim('run','task',f'worker-{i}')
            except ValueError: return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(claim,range(16)))
        self.assertEqual(sum(r is not None for r in results),1)

    def test_expired_claim_cannot_complete_reassigned_work(self):
        first = self.store.task_claim('r','t','alice')
        with self.store.db() as db: db.execute('UPDATE tasks SET deadline=?',(time.time()-1,))
        next_claim = self.store.task_claim('r','t','bob',expected_revision=1)
        with self.assertRaises(ValueError):
            self.store.task_finish('r','t','alice',first['token'],'stale')
        self.store.task_finish('r','t','bob',next_claim['token'],'current')
        self.assertEqual(self.store.status('r')['tasks'][0]['state'],'complete')

    def test_messages_survive_restart_and_ack_only_correct_recipient(self):
        message = self.store.message('r','alice','bob','hello')
        second = Store(self.store.path)
        self.assertEqual(second.inbox('r','bob')['messages'][0]['body'],'hello')
        self.assertEqual(second.ack('r','alice',[message['message_id']])['acknowledged'],0)
        self.assertEqual(second.ack('r','bob',[message['message_id']])['acknowledged'],1)
        self.assertEqual(second.inbox('r','bob')['messages'],[])

    def test_artifacts_are_versioned_and_reject_lost_updates(self):
        self.store.artifact_put('r','proof','alice','original')
        with self.assertRaises(ValueError): self.store.artifact_put('r','proof','bob','overwrite')
        self.store.artifact_put('r','proof','bob','revised',expected_revision=1)
        self.assertEqual(self.store.artifact_get('r','proof',revision=1)['content'],'original')
        self.assertEqual(self.store.artifact_get('r','proof')['content'],'revised')

    def test_namespace_separation_and_invalid_names(self):
        self.store.message('r1','alice','bob','one')
        self.assertEqual(self.store.inbox('r2','bob')['messages'],[])
        with self.assertRaises(ValueError): self.store.artifact_put('../r','x','y','bad')


if __name__ == '__main__': unittest.main()
