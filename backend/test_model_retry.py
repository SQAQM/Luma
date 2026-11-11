"""Retry just the failed model IDs through the public worker facade."""
import hashlib,json,unittest,uuid
from backend import test_model_sync_scope as helpers
from worker.management import read

class RetryModelSyncTests(unittest.TestCase):
    setUp=helpers.SyncScopeTests.setUp
    action=helpers.SyncScopeTests.action
    settled=helpers.SyncScopeTests.settled
    sync=helpers.SyncScopeTests.sync
    def test_retry_uses_only_failed_ids_and_exposes_no_private_leaves(self):
        for i in (0,66):self.client.fail[hashlib.sha256(f'inert weight {i}'.encode()).hexdigest()]='not_found'
        first=self.sync({'rootId':self.root})
        public=next(j for j in read(self.service,'jobs',{},self.event) if j['id']==first['id'])
        self.assertEqual(public.get('retryable_count'),2)
        self.client.fail.clear();self.client.lookups.clear()
        retried=self.settled(self.action('models.retrySync',{'jobId':first['id']}))
        self.assertEqual((retried['total'],retried['processed'],retried['errors']),(2,2,0))
        self.assertEqual(len(self.client.lookups),2)
        self.assertNotIn('fixture-',json.dumps(read(self.service,'jobs',{},self.event)))
        with self.service.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM luma_model_sync_selection').fetchone()[0],0)
    def test_non_model_or_unknown_task_cannot_be_retried(self):
        for ident in (uuid.uuid4().hex, self.service.jobs()[0]['id']):
            with self.assertRaises(ValueError):self.action('models.retrySync',{'jobId':ident})
    def test_sidecar_reason_is_preserved_in_job_summary(self):
        (self.models/'fixture-000.civitai.info').write_bytes(b'{broken')
        task=self.sync({'rootId':self.root,'query':'fixture-000'})
        self.assertEqual(task['issues'],[{'code':'sidecar_format','count':1}])
        self.assertEqual(task.get('retryable_count'),1)
        self.assertTrue((self.models/'fixture-000.json').exists())
        self.assertTrue((self.models/'fixture-000.safetensors.rgthree-info.json').exists())

if __name__=='__main__':unittest.main()
