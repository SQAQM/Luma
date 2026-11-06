import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.store import Store
from backend.organizer import Cancelled
from backend.model_library import ModelLibrary


class Manager:
    stop = False
    def cancelled(self, job): return self.stop
    def progress(self, job, **values): self.values = values


class ModelLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.store = Store(self.base / 'db')
        self.library = ModelLibrary(self.store)
        self.models = self.base / 'models'; self.models.mkdir()
        self.manager = Manager()
    def tearDown(self): self.temp.cleanup()
    def scan(self):
        root = self.library.add_root(self.models)
        self.library.scan(self.manager, 'test', {'root_id':root['id']})
        return root
    def image(self, metadata):
        path = self.base / ('image'+str(len(list(self.base.glob('image*'))))+'.png')
        path.write_bytes(b'synthetic')
        return self.store.upsert_many([(str(path),None,path.stat(),metadata)])[0]
    def test_scan_is_stat_only_incremental_overlap_and_removal(self):
        sub=self.models/'nested'; sub.mkdir(); file=sub/'a.safetensors'; file.write_bytes(b'synthetic')
        (sub/'ignore.png').write_bytes(b'no')
        with patch('builtins.open',side_effect=AssertionError('no weight reads')):
            root=self.scan(); nested=self.library.add_root(sub)
            self.library.scan(self.manager,'test',{'root_id':nested['id']})
        items=self.library.list_models()['items']; self.assertEqual(len(items),1)
        self.assertEqual(items[0]['sha256'],'')
        self.library.remove_root(root['id'])
        self.assertEqual(self.library.list_models()['total'],1)
        self.library.remove_root(nested['id']); self.assertEqual(file.read_bytes(),b'synthetic')
    def test_hash_sync_preserves_valid_metadata_after_network_error(self):
        file=self.models/'renamed.safetensors'; file.write_bytes(b'model')
        self.scan(); item=self.library.list_models()['items'][0]; digest=hashlib.sha256(b'model').hexdigest()
        class Client:
            fail=False
            def version_by_hash(self, value, cancelled):
                if self.fail: raise RuntimeError('private error')
                return {'model_id':1,'version_id':2,'model_name':'Published','version_name':'v1','type':'Checkpoint','family':'FLUX','base_model':'Flux.1 D','files':[{'name':'original','sha256':digest,'autov2':digest[:10]}],'matched_sha256':digest,'matched_hash_kind':'sha256','source_url':'https://civitai.com/models/1'}
        client=Client(); self.library.client=client
        self.library.sync(self.manager,'test',{'ids':[item['id']],'download_covers':False})
        self.assertEqual(self.library.detail(item['id'])['model_name'],'Published')
        client.fail=True; self.library.sync(self.manager,'test',{'ids':[item['id']],'download_covers':False})
        self.assertEqual(self.library.detail(item['id'])['model_name'],'Published')
        self.assertNotIn('private',self.library.detail(item['id'])['status'])
    def test_exact_hash_name_candidate_collision_and_neighbors(self):
        for name in ('same.safetensors','other.safetensors'): (self.models/name).write_bytes(name.encode())
        self.scan(); items=self.library.list_models()['items']
        with self.store.connect() as db:
            for item in items: db.execute('UPDATE luma_model_files SET sha256=? WHERE id=?',('a'*64 if item['name'].startswith('same') else 'a'*10+'b'*54,item['id']))
        same=next(x for x in items if x['name'].startswith('same'))
        exact=self.image({'model':'same','parameters':{'model_sha256':'a'*64}})
        candidate=self.image({'model':'same'})
        ambiguous=self.image({'model':'same','parameters':{'model_hash':'a'*10}})
        verified=self.store.list_images(**self.library.image_filters(same['id']))
        self.assertEqual([x['id'] for x in verified['items']],[exact])
        self.assertEqual(self.store.list_images(**self.library.image_filters(same['id'],'name'))['total'],3)
        self.assertEqual(self.store.browse_neighbors(exact,self.library.image_filters(same['id']))['total'],1)
    def test_cancel_does_not_hash_or_mutate_source(self):
        file=self.models/'a.ckpt'; file.write_bytes(b'x'*20)
        root=self.library.add_root(self.models); self.manager.stop=True
        with self.assertRaises(Cancelled): self.library.scan(self.manager,'test',{'root_id':root['id']})
        self.assertEqual(file.read_bytes(),b'x'*20)
    def test_mismatch_cannot_create_catalog_identity(self):
        self.library.remember_version({'model_id':1,'version_id':2,'type':'Checkpoint','family':'FLUX','matched_sha256':'a'*64,'files':[{'sha256':'b'*64}]})
        self.assertEqual(self.library.catalog_entries(),[])

    def test_detail_legacy_trigger_words_uses_validated_ids_for_source_link(self):
        (self.models/'synthetic.ckpt').write_bytes(b'synthetic');self.scan()
        ident=self.library.list_models()['items'][0]['id']
        legacy={'model_id':17,'version_id':31,'matched_sha256':'a'*64,'files':[{'sha256':'a'*64}],
            'description':'Model introduction','version_description':'Version notes','creator':'Synthetic author',
            'trained_words':['legacy phrase'],'source_url':'https://private.example/untrusted'}
        # Seed the old JSON shape directly: no trigger_words alias existed yet.
        with self.store.connect() as db:
            db.execute('INSERT INTO luma_model_versions(version_id,record,updated_at) VALUES(?,?,0)',(31,json.dumps(legacy)))
            db.execute('UPDATE luma_model_files SET sha256=?,version_id=? WHERE id=?',('a'*64,31,ident))
        reopened=ModelLibrary(Store(self.store.directory))
        with patch('builtins.open',side_effect=AssertionError('detail must not read weights')):
            detail=reopened.detail(ident)
        self.assertEqual(detail.get('trigger_words'),['legacy phrase'])
        self.assertEqual(detail['description'],'Model introduction')
        self.assertEqual(detail['source_url'],'https://civitai.com/models/17?modelVersionId=31')

    def test_detail_cannot_navigate_unverified_cached_ids_or_arbitrary_urls(self):
        (self.models/'synthetic.ckpt').write_bytes(b'synthetic');self.scan()
        ident=self.library.list_models()['items'][0]['id']
        with self.store.connect() as db:
            db.execute('INSERT INTO luma_model_versions(version_id,record,updated_at) VALUES(?,?,0)',(31,'{}'))
            db.execute('UPDATE luma_model_files SET version_id=31 WHERE id=?',(ident,))
        for model_id in (None,True,0,-1,2**63,'17','https://private.example'):
            with self.subTest(model_id=model_id),self.store.connect() as db:
                db.execute('UPDATE luma_model_versions SET record=? WHERE version_id=31',(json.dumps({'model_id':model_id,'version_id':31,'source_url':'https://private.example'}),))
            self.assertEqual(self.library.detail(ident)['source_url'],'')

    def test_trigger_words_native_field_survives_cache_and_empty_refresh(self):
        (self.models/'synthetic.ckpt').write_bytes(b'synthetic');self.scan()
        ident=self.library.list_models()['items'][0]['id']
        record={'model_id':17,'version_id':31,'matched_sha256':'a'*64,'files':[{'sha256':'a'*64}],
            'trigger_words':['explicit phrase']}
        self.assertTrue(self.library.remember_version(record))
        with self.store.connect() as db: db.execute('UPDATE luma_model_files SET sha256=?,version_id=? WHERE id=?',('a'*64,31,ident))
        detail=ModelLibrary(Store(self.store.directory)).detail(ident)
        self.assertEqual(detail.get('trigger_words'),['explicit phrase'])
        self.assertEqual(detail['trained_words'],['explicit phrase'])
        record['trigger_words']=[]
        self.assertTrue(self.library.remember_version(record))
        self.assertEqual(self.library.detail(ident).get('trigger_words'),[])

    def test_hash_is_persisted_when_remote_offline_and_source_change_invalidates(self):
        path=self.models/'a.ckpt'; path.write_bytes(b'abc'); self.scan()
        item=self.library.list_models()['items'][0]
        class Offline:
            def version_by_hash(self,*args,**kwargs): raise RuntimeError('offline')
        self.library.client=Offline()
        self.library.sync(self.manager,'t',{'ids':[item['id']]})
        self.assertEqual(self.library.detail(item['id'])['sha256'],hashlib.sha256(b'abc').hexdigest())
        old=path.stat(); path.write_bytes(b'xyz')
        import os
        os.utime(path,ns=(old.st_atime_ns,old.st_mtime_ns))
        self.scan(); self.assertEqual(self.library.detail(item['id'])['sha256'],'')

    def test_reference_projection_malformed_arrays_and_typed_lora(self):
        first=self.image({'model':'base','parameters':{'model_candidates':'private malformed','checkpoint_hashes':{'not':'array'},'civitai_resources':'bad'}})
        second=self.image({'parameters':{'lora_hashes':{'adapter':'a'*10},'civitai_resources':[{'type':'LORA','name':'adapter2','hash':'b'*64},{'type':'unknown','name':'ignored','hash':'c'*64}]}})
        with self.store.connect() as db:
            refs=[tuple(r) for r in db.execute('SELECT role,kind,value FROM luma_image_model_refs WHERE image_id=?',(second,))]
        self.assertIn(('lora','hash','a'*10),refs)
        self.assertIn(('lora','hash','b'*64),refs)
        self.assertNotIn(('unknown','hash','c'*64),refs)

    def test_image_link_rebuild_is_cached_and_preserves_model_cursor(self):
        for n in range(4): (self.models/f'{n}.ckpt').write_bytes(bytes([n]))
        self.scan(); first=self.library.list_models(limit=2)
        self.assertEqual(len(self.library.list_models(cursor=first['next_cursor'],limit=2)['items']),2)
        image_id=self.image({'parameters':{'checkpoint_hashes':['a'*64,'b'*64]}})
        with self.store.connect() as db: db.execute('DELETE FROM luma_image_model_refs')
        with patch('builtins.open',side_effect=AssertionError('cached only')):
            self.library.rebuild_links(self.manager,'test',{})
        with self.store.connect() as db: self.assertEqual(db.execute('SELECT count(*) FROM luma_image_model_refs WHERE image_id=?',(image_id,)).fetchone()[0],2)
        self.assertEqual(len(self.library.list_models(cursor=first['next_cursor'],limit=2)['items']),2)

    def test_direct_hash_lookup_has_no_name_or_auxiliary_projection(self):
        image_id=self.image({'model':'private-name','parameters':{'model_sha256':'a'*64,'lora_hashes':{'x':'b'*10}}})
        with patch('backend.model_library._projection',side_effect=AssertionError('full reference projection forbidden')):
            self.assertEqual(self.library.image_lookup_hashes([image_id]),[{'hash':'a'*64,'hash_kind':'sha256'}])

    def test_auxiliary_top_level_hash_does_not_become_checkpoint_lookup(self):
        image_id=self.image({'model':'adapter','parameters':{'type':'LORA','model_sha256':'b'*64}})
        self.assertEqual(self.library.image_lookup_hashes([image_id]),[])
        with self.store.connect() as db:
            refs=[tuple(r) for r in db.execute('SELECT role,kind,value FROM luma_image_model_refs WHERE image_id=?',(image_id,))]
        self.assertIn(('lora','hash','b'*64),refs)

    def test_projection_returns_only_scalar_leaves_and_valid_hashes(self):
        image_id=self.image({'parameters':{'model_hash':{'prompt':'PRIVATE'},'checkpoint_sha256':'private-scalar'}})
        from backend.model_references import hash_projection, _projection
        with self.store.connect() as db:
            values=[tuple(row) for row in hash_projection(db,image_id)]
            refs=[tuple(row) for row in _projection(db,image_id)]
        self.assertFalse(any('PRIVATE' in str(value) or 'private-scalar' in str(value) for value in values+refs))

    def test_catalog_does_not_promote_unqueried_sibling_files(self):
        self.library.remember_version({'model_id':1,'version_id':2,'type':'Checkpoint','family':'FLUX','matched_sha256':'a'*64,'files':[{'sha256':'a'*64},{'sha256':'b'*64}]})
        self.assertEqual([x['match'] for x in self.library.catalog_entries()],['a'*64])

    def test_sync_cancel_at_remote_return_discards_late_snapshot(self):
        (self.models/'a.ckpt').write_bytes(b'abc');self.scan(); item=self.library.list_models()['items'][0]
        manager=self.manager
        class Late:
            def version_by_hash(self,digest,cancelled):
                manager.stop=True
                return {'version_id':2,'matched_sha256':digest,'files':[{'sha256':digest}]}
        self.library.client=Late()
        with self.assertRaises(Cancelled):self.library.sync(manager,'t',{'ids':[item['id']]})
        self.assertIsNone(self.library.detail(item['id'])['version_id'])

    def test_rebuild_highwater_selection_and_cancel_transaction(self):
        ids=[self.image({'model':f'model-{n}'}) for n in range(3)]
        with self.store.connect() as db: db.execute('DELETE FROM luma_image_model_refs')
        self.library.rebuild_links(self.manager,'t',{'ids':ids,'highwater':ids[1]})
        with self.store.connect() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT DISTINCT image_id FROM luma_image_model_refs ORDER BY image_id')],ids[:2])
        self.manager.stop=True
        with self.assertRaises(Cancelled): self.library.rebuild_links(self.manager,'t',{})

    def test_schema_creation_does_not_backfill_existing_images(self):
        self.image({'model':'before'})
        with self.store.connect() as db: db.execute('DELETE FROM luma_image_model_refs')
        with patch('backend.model_references.update_image_references',side_effect=AssertionError('no implicit backfill')):
            Store(self.store.directory); ModelLibrary(self.store)
        with self.store.connect() as db: self.assertEqual(db.execute('SELECT count(*) FROM luma_image_model_refs').fetchone()[0],0)

    def test_directory_guard_rejects_replacement_while_streaming(self):
        (self.models/'a.ckpt').write_bytes(b'abc')
        from backend.model_library import _directory_guard
        with _directory_guard(self.models):
            if __import__('os').name=='nt':
                with self.assertRaises(OSError): self.models.rename(self.base/'replaced')
        self.assertTrue(self.models.is_dir())

    def test_remote_registry_provenance_not_described_as_user_mapping(self):
        from backend.model_registry import ModelRegistry
        self.library.remember_version({'version_id':2,'model_id':1,'type':'Checkpoint','family':'FLUX','matched_sha256':'a'*64,'files':[{'sha256':'a'*64}],'source_url':'https://civitai.com/models/1'})
        registry=ModelRegistry(self.store); snapshot=registry.combined_catalog_snapshot()
        result=registry.resolve({'parameters':{'model_sha256':'a'*64}}, {'family':'未知'},[],catalog=snapshot['entries'])
        self.assertEqual(result['reason_code'],'catalog')
        self.assertIn('Civitai',result['reason'])

    def test_sync_known_error_category_is_safe_and_visible(self):
        from backend.civitai import CivitaiError
        (self.models/'a.ckpt').write_bytes(b'abc');self.scan();item=self.library.list_models()['items'][0]
        class Limited:
            def version_by_hash(self,*args,**kwargs): raise CivitaiError('rate_limited','PRIVATE TOKEN')
        self.library.client=Limited();self.library.sync(self.manager,'t',{'ids':[item['id']]})
        self.assertEqual(self.library.detail(item['id'])['status'],'sync_error_rate_limited')
        self.assertIn('频繁',self.manager.values['message'])
        self.assertNotIn('PRIVATE',str(self.manager.values))

    def test_hash_source_replacement_during_open_never_persists_identity(self):
        path=self.models/'a.ckpt';path.write_bytes(b'abc');self.scan();item=self.library.list_models()['items'][0]
        original=__import__('backend.model_library',fromlist=['VerifiedFile']).VerifiedFile
        library=self.library
        class Changed(original):
            def digest(self,cancelled):
                result=super().digest(cancelled)
                raise ValueError('synthetic identity changed')
        class NoNetwork:
            def version_by_hash(self,*args,**kwargs): raise AssertionError('must not request')
        library.client=NoNetwork()
        with patch('backend.model_library.VerifiedFile',Changed):library.sync(self.manager,'t',{'ids':[item['id']]})
        self.assertEqual(library.detail(item['id'])['sha256'],'')

    def test_duplicate_sql_restore_rebuilds_references_without_original_reads(self):
        from backend.duplicates import DuplicateManager
        first=self.image({'model':'main','parameters':{'model_sha256':'a'*64}})
        second=self.image({'model':'main','parameters':{'model_sha256':'a'*64,'lora_hashes':{'adapter':'b'*10}}})
        duplicates=DuplicateManager(self.store)
        scan=duplicates.create_scan();duplicates.scan(self.manager,'scan',{'scan_id':scan['id']})
        duplicates.select_all(scan['id']);preview=duplicates.preview_cleanup(scan['id'])
        duplicates.claim_plan(preview['id'],'cleanup');duplicates.execute_cleanup(self.manager,'clean',{'plan_id':preview['id']})
        with self.store.connect() as db:
            row=dict(db.execute("SELECT * FROM duplicate_journal WHERE job_id='clean'").fetchone())
            self.assertEqual(db.execute('SELECT count(*) FROM luma_image_model_refs WHERE image_id=?',(row['image_id'],)).fetchone()[0],0)
        with patch('builtins.open',side_effect=AssertionError('SQL restore hook no image read')):
            duplicates._restore_index(row)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM luma_image_model_refs WHERE image_id=?',(row['image_id'],)).fetchone()[0],3)

    def test_reparse_entries_skipped_and_scan_cancel_keeps_previous_snapshot(self):
        path=self.models/'a.ckpt';path.write_bytes(b'abc');root=self.scan()
        original=self.library.list_models()['items'][0]['id']
        self.manager.stop=True
        with self.assertRaises(Cancelled):self.library.scan(self.manager,'t',{'root_id':root['id']})
        self.assertEqual(self.library.list_models()['items'][0]['id'],original)
        self.manager.stop=False
        with patch('backend.model_library.reparse_point',side_effect=lambda p,info=None:Path(p)==path):
            self.library.scan(self.manager,'t',{'root_id':root['id']})
        self.assertEqual(self.library.list_models()['total'],0)

    def test_sync_rejects_stale_source_identity_and_clears_stale_hash(self):
        path=self.models/'a.ckpt';path.write_bytes(b'abc');self.scan();item=self.library.list_models()['items'][0]
        with self.store.connect() as db:db.execute("UPDATE luma_model_files SET sha256=? WHERE id=?",('a'*64,item['id']))
        path.write_bytes(b'changed')
        class NoNetwork:
            def version_by_hash(self,*args,**kwargs):raise AssertionError('stale source must not upload')
        self.library.client=NoNetwork();self.library.sync(self.manager,'t',{'ids':[item['id']]})
        self.assertEqual(self.library.detail(item['id'])['sha256'],'')
        self.assertEqual(self.library.detail(item['id'])['status'],'changed')

    def test_explicit_typed_air_preserves_checkpoint_and_lora_roles(self):
        image_id=self.image({'model':'urn:air:sdxl:checkpoint:civitai:1@2','parameters':{'civitai_resources':[{'type':'LORA','air':'urn:air:sdxl:lora:civitai:3@4'}]}})
        with self.store.connect() as db:
            refs=[tuple(r) for r in db.execute('SELECT role,kind,value FROM luma_image_model_refs WHERE image_id=?',(image_id,))]
        self.assertIn(('checkpoint','version','2'),refs)
        self.assertIn(('lora','version','4'),refs)

    def test_scan_file_replaced_after_stat_cannot_commit_old_identity(self):
        path=self.models/'a.ckpt';path.write_bytes(b'abc')
        import backend.model_library as module
        original=module._identity;calls=[]
        def race(p,info):
            value=original(p,info);calls.append(1)
            if len(calls)==2:path.write_bytes(b'replaced-after-stat')
            return value
        with patch('backend.model_library._identity',side_effect=race):self.scan()
        self.assertEqual(self.library.list_models()['total'],0)

if __name__ == '__main__': unittest.main()
