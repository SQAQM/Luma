"""Synthetic Civitai responses and inert local files only."""
import hashlib,json,threading,unittest
from backend import test_civitai_sync as helpers
from backend.civitai import CivitaiClient
from backend.test_civitai import Transport,document

class ModelInteropTests(unittest.TestCase):
    setUp=helpers.CivitaiSyncTests.setUp
    def run_sync(self,*args):return helpers.CivitaiSyncTests.run_sync(self,*args,compatibility=True)

    def test_sync_writes_metadata_and_cover_next_to_model(self):
        from backend.test_civitai import Reply
        self.run_sync([{'url':'https://image.civitai.com/fixture/cover.png','type':'image','nsfwLevel':1}],Reply(self.cover))
        stem=self.weight.with_suffix('')
        cv=json.loads(stem.with_suffix('.civitai.info').read_text(encoding='utf-8'))
        self.assertEqual((cv['modelId'],cv['id'],cv['baseModel']),(17,31,'SDXL 1.0'))
        self.assertEqual(cv['model']['name'],'Published model')
        web=json.loads(stem.with_suffix('.json').read_text(encoding='utf-8'));self.assertIn('Actual introduction',web['description'])
        rg=json.loads(self.weight.with_name(self.weight.name+'.rgthree-info.json').read_text(encoding='utf-8'))
        self.assertEqual(rg['type'],'Checkpoint');self.assertEqual(rg['sha256'],self.digest)
        self.assertEqual(stem.with_suffix('.png').read_bytes(),self.cover)

    def test_existing_webui_notes_and_custom_preview_are_preserved(self):
        path=self.weight.with_suffix('.json');path.write_text(json.dumps({'notes':'own note','description':'own description','activation text':'own words'}))
        preview=self.weight.with_suffix('.png');preview.write_bytes(self.cover)
        self.run_sync([])
        result=json.loads(path.read_text());self.assertEqual(result['notes'],'own note');self.assertEqual(result['description'],'own description');self.assertEqual(result['activation text'],'own words')
        self.assertEqual(preview.read_bytes(),self.cover)

    def test_export_updates_its_fields_and_preserves_later_user_edits(self):
        from backend import model_sidecars
        record={'model_id':17,'version_id':31,'model_name':'First','model_type':'LORA','type':'LORA','base_model':'Anima','description':'First info','trained_words':['one'],'files':[]}
        model_sidecars.write(self.weight,record,self.digest,compatibility=True)
        target=self.weight.with_suffix('.json');value=json.loads(target.read_text());value['activation text']='custom';target.write_text(json.dumps(value))
        record.update(model_name='Second',description='Second info',trained_words=['two'])
        model_sidecars.write(self.weight,record,self.digest,compatibility=True)
        value=json.loads(target.read_text());self.assertEqual(value['description'],'Second info');self.assertEqual(value['activation text'],'custom')
        cv=json.loads(self.weight.with_suffix('.civitai.info').read_text());self.assertEqual(cv['model']['name'],'Second')

    def test_export_cancellation_leaves_no_temporary_files(self):
        from backend import model_sidecars
        from backend.organizer import Cancelled
        with self.assertRaises(Cancelled):model_sidecars.write(self.weight,{},self.digest,cancelled=lambda:True,compatibility=True)
        self.assertEqual(list(self.models.glob('*.tmp')),[])

    def test_user_cleared_fields_stay_cleared_on_refresh(self):
        from backend import model_sidecars
        record={'description':'First','trained_words':['one']}
        model_sidecars.write(self.weight,record,self.digest,compatibility=True)
        target=self.weight.with_suffix('.json');value=json.loads(target.read_text())
        value.update(description='',**{'activation text':None});target.write_text(json.dumps(value))
        model_sidecars.write(self.weight,{'description':'Second','trained_words':['two']},self.digest,compatibility=True)
        value=json.loads(target.read_text());self.assertEqual(value['description'],'');self.assertIsNone(value['activation text'])

    def test_identical_existing_user_fields_are_not_claimed(self):
        from backend import model_sidecars
        target=self.weight.with_suffix('.json');target.write_text(json.dumps({'description':'First'}))
        model_sidecars.write(self.weight,{'description':'First'},self.digest,compatibility=True)
        model_sidecars.write(self.weight,{'description':'Second'},self.digest,compatibility=True)
        value=json.loads(target.read_text());self.assertEqual(value['description'],'First')
        self.assertNotIn('description',value['_luma']['managed'])

    def test_deleted_owned_fields_are_not_recreated_on_repeated_sync(self):
        from backend import model_sidecars
        record={'description':'First','model_name':'Name'}
        model_sidecars.write(self.weight,record,self.digest,compatibility=True)
        web=self.weight.with_suffix('.json');value=json.loads(web.read_text());del value['description'];web.write_text(json.dumps(value))
        cv=self.weight.with_suffix('.civitai.info');value=json.loads(cv.read_text());del value['model'];cv.write_text(json.dumps(value))
        for _ in range(2):
            model_sidecars.write(self.weight,{'description':'Second','model_name':'New name'},self.digest,compatibility=True)
            self.assertNotIn('description',json.loads(web.read_text()))
            self.assertNotIn('model',json.loads(cv.read_text()))

    def test_cover_failure_still_exports_the_valid_metadata(self):
        import urllib.error
        url='https://image.civitai.com/fixture/missing.png'
        detail=self.run_sync([{'url':url,'type':'image','nsfwLevel':1}],urllib.error.HTTPError(url,404,'missing',{},None))
        self.assertEqual(detail['status'],'sync_error_cover_not_found')
        self.assertIn('Actual introduction',json.loads(self.weight.with_suffix('.json').read_text())['description'])
        self.assertEqual(json.loads(self.weight.with_suffix('.civitai.info').read_text())['id'],31)

    def test_profile_merge_preserves_classifier_architecture_vocabulary(self):
        from backend.civitai import with_profile
        for base,variant in (('SDXL 1.0','SDXL'),('SD 1.5','SD1.5'),('SD 2.1 768','SD2.1')):
            with self.subTest(base=base):
                value={'id':31,'modelId':17,'baseModel':base,'model':{'type':'Checkpoint'},'files':[{'type':'Model','hashes':{'SHA256':self.digest}}]}
                record=CivitaiClient(transport=Transport(document(value))).version_by_hash(self.digest)
                merged=with_profile(record,{'type':'Checkpoint'})
                self.assertEqual(merged['model_variant'],variant);self.assertEqual(merged['base_model'],base)

    @unittest.skipUnless(__import__('os').name=='nt','Windows file sharing')
    def test_existing_metadata_is_locked_during_read_merge_and_update(self):
        from backend import model_sidecars
        from unittest.mock import patch
        target=self.weight.with_suffix('.json');target.write_text(json.dumps({'notes':'before'}))
        original=model_sidecars._merge;blocked=[]
        def concurrent_edit(*args,**kwargs):
            if not blocked:
                try:target.write_text(json.dumps({'notes':'concurrent'}))
                except OSError:blocked.append(True)
                else:blocked.append(False)
            return original(*args,**kwargs)
        # Exercise the actual OS lock while the existing JSON is being merged.
        with patch.object(model_sidecars,'_merge',side_effect=concurrent_edit):
            model_sidecars._json(target,{'description':'Remote'},self.weight.name,lambda:False)
        self.assertEqual(blocked,[True]);self.assertEqual(json.loads(target.read_text())['notes'],'before')

    def test_exported_info_imports_into_a_fresh_library(self):
        from backend.model_library import ModelLibrary
        from backend.store import Store
        self.run_sync([])
        other=ModelLibrary(Store(self.base/'fresh'));root=other.add_root(self.models)
        other.scan(self.progress,'scan',{'root_id':root['id']});item=other.list_models()['items'][0]
        self.assertEqual((item['model_name'],item['family'],item['type']),('Published model','Stable Diffusion','Checkpoint'))
        self.assertEqual(item['metadata_source'],'local')

    def test_parent_profile_recovers_missing_type_from_hash_reply(self):
        version={'id':31,'modelId':17,'baseModel':'Krea 2','model':{'name':'Fixture'},'files':[{'type':'Model','hashes':{'SHA256':self.digest}}],'images':[]}
        profile={'id':17,'name':'Fixture','type':'LORA','description':'Info'}
        self.library.client=CivitaiClient(transport=Transport(document(version),document(profile)))
        self.library.sync(self.progress,'sync',{'ids':[self.ident],'download_covers':False})
        self.assertEqual(self.library.detail(self.ident)['type'],'LORA')
        self.assertEqual(self.library.list_models(types=['LORA'])['total'],1)

    def test_profile_base_is_recovered_from_exact_version_only(self):
        version={'id':31,'modelId':17,'model':{},'files':[{'type':'Model','hashes':{'SHA256':self.digest}}]}
        profile={'id':17,'name':'Fixture','type':'LORA','modelVersions':[{'id':99,'baseModel':'SDXL 1.0'},{'id':31,'baseModel':'Anima'}]}
        self.library.client=CivitaiClient(transport=Transport(document(version),document(profile)))
        self.library.sync(self.progress,'sync',{'ids':[self.ident],'download_covers':False})
        self.assertEqual(self.library.detail(self.ident)['family'],'Anima')

    def test_exported_renamed_vae_keeps_its_file_role(self):
        from backend import model_sidecars,model_local_info
        record={'model_id':17,'version_id':31,'model_name':'Fixture','model_type':'Checkpoint','type':'VAE','base_model':'SDXL 1.0','files':[{'name':'official-vae.safetensors','type':'VAE','sha256':self.digest}]}
        model_sidecars.write(self.weight,record,self.digest,compatibility=True)
        self.assertEqual(model_local_info.read(self.weight)['type'],'VAE')

    def test_component_type_is_retained_from_civitai(self):
        version={'id':31,'modelId':17,'baseModel':'SDXL 1.0','model':{'type':'Checkpoint'},'files':[{'type':'Text Encoder','hashes':{'SHA256':self.digest}}]}
        record=CivitaiClient(transport=Transport(document(version))).version_by_hash(self.digest)
        self.assertEqual(record['type'],'Text Encoder')

    def test_new_upstream_base_label_is_visible_and_filterable(self):
        self.library.remember_version({'model_id':17,'version_id':31,'base_model':'Future Diffusion 7','family':'Unknown','type':'Checkpoint','matched_sha256':self.digest,'files':[{'sha256':self.digest}]})
        with self.store.connect() as db:db.execute('UPDATE luma_model_files SET sha256=?,version_id=31 WHERE id=?',(self.digest,self.ident))
        self.assertEqual(self.library.detail(self.ident)['family'],'Future Diffusion 7')
        self.assertEqual(self.library.list_models(families=['Future Diffusion 7'])['total'],1)

    def test_model_cursor_survives_image_only_revision(self):
        from worker.management import read
        for i in range(66):(self.models/f'other-{i:03}.safetensors').write_bytes(b'inert')
        self.library.scan(self.progress,'scan',{'root_id':self.library.roots()[0]['id']})
        first=self.library.list_models()
        with self.store.connect() as db:db.execute('UPDATE query_state SET revision=revision+1 WHERE id=1')
        second=self.library.list_models(cursor=first['next_cursor'])
        self.assertEqual((first['total'],len(first['items']),len(second['items'])),(67,60,7))

    def test_model_cursor_expiry_has_explicit_error_code(self):
        for i in range(66):(self.models/f'other-{i:03}.safetensors').write_bytes(b'inert')
        self.library.scan(self.progress,'scan',{'root_id':self.library.roots()[0]['id']});first=self.library.list_models()
        (self.models/'new.safetensors').write_bytes(b'inert');self.library.scan(self.progress,'scan',{'root_id':self.library.roots()[0]['id']})
        with self.assertRaises(ValueError) as error:self.library.list_models(cursor=first['next_cursor'])
        self.assertEqual(getattr(error.exception,'code',''),'stale_cursor')

    def test_malformed_cursor_is_not_treated_as_a_refreshable_snapshot(self):
        import base64
        for value in ({'revision':-1},{'revision':True,'filter':'bad','id':1},{'revision':-1,'filter':'bad','id':1}):
            token=base64.urlsafe_b64encode(json.dumps(value).encode()).decode()
            with self.assertRaises(ValueError) as error:self.library.list_models(cursor=token)
            self.assertNotEqual(getattr(error.exception,'code',''),'stale_cursor')

if __name__=='__main__':unittest.main()
