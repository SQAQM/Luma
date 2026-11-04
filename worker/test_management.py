import json, threading, time, uuid
from pathlib import Path
from unittest.mock import patch
from worker.test_gallery import GalleryTests

class ManagementTests(GalleryTests):
    def test_cache_quota_preferences_protocol(self):
        values={'theme':'dark','density':'compact','sort':'name','showTags':True,'thumbnailSize':320,'reduceMotion':True,'thumbnailCacheMiB':128}
        self.assertEqual(self.write('settings.save',values=values)['thumbnailCacheMiB'],128)
        self.assertEqual(self.service.settings()['thumbnail_cache_limit_bytes'],128*1024**2)
        self.assertEqual(self.read('settings')['thumbnailCacheMiB'],128)
        self.assertEqual(self.read('settings')['thumbnail_cache_limit_bytes'],128*1024**2)
        for invalid in (True,63,8193,128.5,'128',None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):self.write('settings.save',values={'thumbnailCacheMiB':invalid})
    def read(self, section, **args):
        return self.call('manage.read', {'section':section,'args':args})
    def write(self, action, operation=None, **args):
        return self.call('manage.write', {'operationId':operation or uuid.uuid4().hex,'action':action,'args':args})
    def settled(self, job):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            result=self.service.job(job['id'])
            if result['status'] not in ('queued','running'):return result
            time.sleep(.02)
        self.fail('owned synthetic task did not finish')
    def test_tag_definitions_and_snapshot_are_semantic(self):
        tags=self.read('tags');self.assertGreater(len(tags),0)
        state=self.read('labels',ids=[1,2]);self.assertEqual(state['image_count'],2)
        original=self.service.detail(1)['prompt']
        result=self.write('labels.apply',ids=[1,2],changes=[{'tag_id':tags[0]['id'],'action':'include'}],snapshot=state['snapshot_token'])
        self.assertEqual(result['image_count'],2)
        self.assertGreater(result['changed_image_count'],0)
        self.assertEqual(self.service.detail(1)['prompt'],original)
        with self.assertRaises(ValueError):self.write('labels.apply',ids=[1,2],changes=[{'tag_id':tags[0]['id'],'action':'exclude'}],snapshot=state['snapshot_token'])
        state=self.read('labels',ids=[1,2]);self.write('labels.apply',ids=[1,2],changes=[{'tag_id':tags[0]['id'],'action':'auto'}],snapshot=state['snapshot_token'])
        self.assertEqual(self.read('labels',ids=[1,2])['tags'][0]['manual_count'],0)
    def test_receipts_replay_same_operation_without_second_job(self):
        op=uuid.uuid4().hex
        with patch.object(self.service,'reread_metadata',return_value={'id':'owned-job','status':'queued','payload':{'private':'NO'}}) as submit:
            first=self.write('metadata.refresh',operation=op,ids=[1]);second=self.write('metadata.refresh',operation=op,ids=[1])
            self.assertEqual(first,second);submit.assert_called_once()
        self.assertNotIn('payload',first)
        status=self.call('manage.status',{'operationId':op});self.assertEqual(status['state'],'completed')
        with self.assertRaises(ValueError):self.write('metadata.refresh',operation=op,ids=[2])
    def test_receipt_survives_service_restart(self):
        op=uuid.uuid4().hex;self.write('settings.save',operation=op,values={'sort':'name'})
        self.service.close()
        from desktop.service import LibraryService
        self.service=LibraryService(self.data)
        self.assertEqual(self.call('manage.status',{'operationId':op})['state'],'completed')
    def test_unknown_pending_and_wrong_shapes_fail_without_calls(self):
        for payload in [dict(action='import',operationId='bad',args={}),dict(action='organize',operationId=uuid.uuid4().hex,args={}),dict(action='metadata.refresh',operationId=uuid.uuid4().hex,args={'ids':[True]}),dict(action='metadata.refresh',operationId=uuid.uuid4().hex,args={'ids':list(range(1,122))})]:
            with self.assertRaises(ValueError):self.call('manage.write',payload)
        with self.assertRaises(ValueError):self.read('labels',ids=[1],extra='reject')
        self.assertEqual(self.call('manage.status',{'operationId':uuid.uuid4().hex})['state'],'unknown')
    def test_patch_checks_identity_and_only_favorite_rating(self):
        row=self.call('catalog.query',{'limit':1})['items'][0]
        self.write('image.patch',imageId=row['id'],identity=row['identity'],favorite=True,rating=4)
        self.assertEqual(self.service.detail(row['id'])['rating'],4)
        with self.assertRaises(ValueError):self.write('image.patch',imageId=row['id'],identity='0'*64,rating=2)
        with self.assertRaises(ValueError):self.write('image.patch',imageId=row['id'],identity=row['identity'],tags=['not-semantic'])
    def test_import_explicit_job_and_safe_projection(self):
        folder=Path(self.temp.name)/'import-only';folder.mkdir()
        from PIL import Image
        Image.new('RGB',(24,24),'navy').save(folder/'sample.png')
        before=(folder/'sample.png').read_bytes()
        job=self.write('catalog.import',path=str(folder),recursive=True)
        self.assertEqual(self.settled(job)['status'],'completed')
        jobs=self.read('jobs');self.assertTrue(any(j['id']==job['id'] for j in jobs))
        self.assertNotIn(str(folder),json.dumps(jobs));self.assertNotIn('payload',jobs[0])
        self.assertEqual((folder/'sample.png').read_bytes(),before)
    def test_metadata_job_is_explicit_and_original_unchanged(self):
        path=Path(self.service.detail(1)['path']);before=path.read_bytes()
        job=self.write('metadata.refresh',ids=[1]);self.assertEqual(self.settled(job)['status'],'completed')
        self.assertEqual(path.read_bytes(),before)
    def test_credentials_never_return_or_receipt_plaintext(self):
        token='SyntheticTokenForSessionOnly0123456789'
        result=self.write('credential.set',token=token,remember=False)
        self.assertNotIn(token,json.dumps(result));self.assertNotIn(token,json.dumps(self.read('credential')))
        for path in self.data.glob('management-operations.sqlite3*'):self.assertNotIn(token.encode(),path.read_bytes())
        self.write('credential.clear');self.assertFalse(self.read('credential')['configured'])
    def test_model_registration_does_not_scan_hash_or_connect(self):
        folder=Path(self.temp.name)/'models-only';folder.mkdir();(folder/'fake.safetensors').write_bytes(b'not-loaded')
        with patch.object(self.service.model_library,'scan',side_effect=AssertionError('implicit scan')),patch.object(self.service.civitai_client,'version_by_hash',side_effect=AssertionError('implicit network')):
            root=self.write('models.add',path=str(folder));self.assertEqual(root['count'],0)
            self.assertEqual(self.read('models')['total'],0)
            self.write('models.remove',rootId=root['id'])
        self.assertEqual((folder/'fake.safetensors').read_bytes(),b'not-loaded')
    def test_registry_save_no_implicit_cached_reclassification(self):
        with patch.object(self.service,'reclassify_models',side_effect=AssertionError('implicit reclassification')):
            result=self.write('registry.save',entries=[{'kind':'name','match':'synthetic-checkpoint','family':'Anima','enabled':True}])
            self.assertEqual(result[0]['family'],'Anima')
        report=self.read('diagnosis');self.assertIsInstance(report,dict)
    def test_no_download_or_inference_on_read(self):
        with patch.object(self.service,'install_vision_model',side_effect=AssertionError('implicit download')),patch.object(self.service,'classify_contents',side_effect=AssertionError('implicit inference')):
            self.read('tags');self.read('vision');self.read('modelRoots');self.read('credential')
    def test_cancel_before_write_no_side_effect(self):
        self.event.set()
        with patch.object(self.service,'put_settings',side_effect=AssertionError('cancelled write')):
            with self.assertRaises(ValueError):self.write('settings.save',values={'sort':'name'})
    def test_receipt_retention_never_blocks_new_work_or_replays_expired(self):
        first=uuid.uuid4().hex
        with patch.object(self.service,'put_settings',return_value={'sort':'name'}) as save:
            self.write('settings.save',operation=first,values={'sort':'name'})
            for _ in range(140):self.write('settings.save',values={'sort':'name'})
            self.assertEqual(self.call('manage.status',{'operationId':first})['state'],'expired')
            with self.assertRaises(ValueError):self.write('settings.save',operation=first,values={'sort':'name'})
            self.assertEqual(save.call_count,141)
    def test_runtime_imports_without_weight_download(self):
        value=self.read('runtime');self.assertTrue(value['onnxruntime'] and value['tokenizers'] and value['numpy'])
    def test_cover_cache_is_sealed_and_regenerates(self):
        from PIL import Image
        folder=self.data/'model-covers';folder.mkdir();source=folder/'fixture.png';Image.new('RGB',(16,16),'navy').save(source)
        before=source.read_bytes()
        with patch.object(self.service,'model_cover_path',return_value=str(source)):
            first=self.read('modelCover',modelId=1);cached=self.data/first['relativePath'];cached.write_bytes(b'corrupt')
            second=self.read('modelCover',modelId=1);self.assertEqual(cached.read_bytes(),before);self.assertEqual(first,second)
        self.assertEqual(source.read_bytes(),before)
    def test_fake_civitai_sync_exact_and_candidate_image_scopes(self):
        import hashlib
        folder=Path(self.temp.name)/'weights';folder.mkdir();weight=folder/'same.safetensors';weight.write_bytes(b'inert synthetic weight')
        root=self.write('models.add',path=str(folder));self.assertEqual(self.settled(self.write('models.scan',rootId=root['id']))['status'],'completed')
        model=self.read('models')['items'][0];digest=hashlib.sha256(weight.read_bytes()).hexdigest();lookups=[]
        class Client:
            def version_by_hash(_,value,cancelled):
                lookups.append(value)
                return {'model_id':1,'version_id':2,'model_name':'Published synthetic','version_name':'v1','type':'Checkpoint','family':'FLUX','base_model':'Flux.1 D','files':[{'name':'original','sha256':digest,'autov2':digest[:10]}],'matched_sha256':digest,'matched_hash_kind':'sha256'}
        self.service.model_library.client=Client()
        outcome=self.settled(self.write('models.sync',ids=[model['id']],downloadCovers=False));self.assertEqual(outcome['status'],'completed');self.assertEqual(lookups,[digest])
        self.assertEqual(self.read('modelDetail',modelId=model['id'])['family'],'FLUX')
        from PIL import Image
        for n,params in [('exact',{'model_sha256':digest}),('name',{})]:
            path=folder/(n+'.png');Image.new('RGB',(16,16),'white').save(path)
            self.service.store.upsert(path,None,path.stat(),{'model':'same','parameters':params})
        self.assertEqual(self.read('modelImages',modelId=model['id'],match='verified')['total'],1)
        self.assertEqual(self.read('modelImages',modelId=model['id'],match='candidate')['total'],2)
        self.assertEqual(weight.read_bytes(),b'inert synthetic weight')
    def test_persistent_cancel_reaches_safe_task_point(self):
        from backend.organizer import Cancelled
        folder=Path(self.temp.name)/'cancel-weights';folder.mkdir();root=self.write('models.add',path=str(folder));entered=threading.Event()
        def delayed(manager,job_id,payload):
            entered.set()
            while not manager.cancelled(job_id):time.sleep(.01)
            raise Cancelled()
        with patch.object(self.service.model_library,'scan',side_effect=delayed):
            task=self.write('models.scan',rootId=root['id']);self.assertTrue(entered.wait(2));self.write('job.cancel',jobId=task['id']);self.assertEqual(self.settled(task)['status'],'cancelled')
    def test_missing_model_hash_rejects_without_network(self):
        with patch.object(self.service.civitai_client,'version_by_hash',side_effect=AssertionError('unexpected network')):
            with self.assertRaises(ValueError) as failure:self.write('models.lookup',ids=[1])
            self.assertEqual(getattr(failure.exception,'code',''),'model_hash_missing')
