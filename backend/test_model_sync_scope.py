"""67 inert synthetic weights, no user data or network."""
import hashlib,json,tempfile,threading,time,unittest,uuid
from unittest import mock
from pathlib import Path
from backend.civitai import CivitaiError
from desktop.service import LibraryService
from worker.management import read,write

class Client:
    def __init__(self):self.lookups=[];self.fail={};self.observe=None
    def version_by_hash(self,digest,cancelled):
        self.lookups.append(digest)
        if self.observe:self.observe()
        if digest in self.fail:raise CivitaiError(self.fail[digest])
        return {'model_id':1,'version_id':int(digest[:7],16)+1,'model_name':'Published','version_name':'v1','type':'Checkpoint','family':'FLUX','base_model':'Flux.1 D','files':[{'name':'synthetic','sha256':digest,'autov2':digest[:10]}],'matched_sha256':digest,'matched_hash_kind':'sha256','images':[]}

class SyncScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.models=self.base/'models';self.models.mkdir()
        for i in range(67):(self.models/f'fixture-{i:03}.safetensors').write_bytes(f'inert weight {i}'.encode())
        self.client=Client();self.service=LibraryService(self.base/'index',civitai_client=self.client,automatic_recovery=False);self.addCleanup(self.service.close)
        self.event=threading.Event();root=self.action('models.add',{'path':str(self.models)});self.root=root['id']
        self.settled(self.action('models.scan',{'rootId':self.root}))
    def action(self,name,args):return write(self.service,name,args,uuid.uuid4().hex,self.event)
    def settled(self,task):
        end=time.monotonic()+15
        while time.monotonic()<end:
            result=self.service.job(task['id'])
            if result['status'] not in ('queued','running'):return result
            time.sleep(.01)
        self.fail('Own sync task timed out')
    def sync(self,scope):return self.settled(self.action('models.sync',{'scope':scope,'downloadCovers':False}))
    def test_directory_scope_updates_all_67_despite_60_item_page(self):
        page=read(self.service,'models',{'rootId':self.root},self.event)
        self.assertEqual((page['total'],len(page['items'])),(67,60))
        task=self.sync({'rootId':self.root})
        self.assertEqual((task['total'],task['processed'],task['errors']),(67,67,0));self.assertEqual(len(self.client.lookups),67)
        with self.service.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM luma_model_sync_selection').fetchone()[0],0)
    def test_type_family_and_search_scope_stays_fixed_during_updates(self):
        task=self.sync({'rootId':self.root,'query':'fixture-00','types':['Unknown'],'families':['Unknown']})
        self.assertEqual((task['total'],task['processed']), (10,10));self.assertEqual(len(self.client.lookups),10)
    def test_single_selected_model_does_not_expand_to_directory(self):
        item=read(self.service,'models',{},self.event)['items'][0]
        task=self.settled(self.action('models.sync',{'ids':[item['id']],'downloadCovers':False}))
        self.assertEqual((task['total'],task['processed']), (1,1))
    def test_error_reason_survives_next_progress_and_job_read(self):
        self.client.fail[hashlib.sha256(b'inert weight 0').hexdigest()]='not_found'
        observed=[]
        def observe():
            jobs=read(self.service,'jobs',{},self.event)
            if len(self.client.lookups)>1:observed.extend(j.get('issues',[]) for j in jobs if j['kind']=='model_sync')
        self.client.observe=observe;task=self.sync({'rootId':self.root})
        job=next(j for j in read(self.service,'jobs',{},self.event) if j['id']==task['id'])
        self.assertEqual(job['issues'],[{'code':'not_found','count':1}]);self.assertTrue(any(rows==job['issues'] for rows in observed))
        self.assertEqual((task['processed'],task['errors']),(67,1));self.assertNotIn('fixture-',json.dumps(job))
    def test_auth_failure_keeps_remaining_count_and_reason(self):
        self.client.fail[hashlib.sha256(b'inert weight 0').hexdigest()]='unauthorized'
        task=self.sync({'rootId':self.root});self.assertEqual((task['total'],task['processed'],task['errors']),(67,1,1))
        self.assertEqual(task['issues'],[{'code':'unauthorized','count':1}])
    def test_scope_and_ids_are_mutually_exclusive(self):
        with self.assertRaises(ValueError):self.action('models.sync',{'ids':[1],'scope':{}})

    def test_cancel_removes_selection_snapshot(self):
        def cancel():
            if len(self.client.lookups)==2:
                task=next(j for j in self.service.jobs() if j['kind']=='model_sync');self.service.cancel(task['id'])
        self.client.observe=cancel;task=self.sync({'rootId':self.root})
        self.assertEqual(task['status'],'cancelled')
        with self.service.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM luma_model_sync_selection').fetchone()[0],0)

    def test_shutdown_discards_unstarted_scope_snapshot(self):
        from backend.model_sync_scope import prepare
        prepare(self.service.model_library,{'root_id':self.root});self.service.close()
        with self.service.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM luma_model_sync_selection').fetchone()[0],0)

    def test_cancel_before_handler_discards_snapshot_immediately(self):
        with mock.patch.object(self.service.manager,'cancelled',return_value=True):
            task=self.sync({'rootId':self.root})
            self.assertEqual(task['status'],'cancelled');self.assertEqual(self.client.lookups,[])
            for _ in range(20):
                with self.service.store.connect() as db:remaining=db.execute('SELECT count(*) FROM luma_model_sync_selection').fetchone()[0]
                if not remaining:break
                time.sleep(.01)
            self.assertEqual(remaining,0)

if __name__=='__main__':unittest.main()
