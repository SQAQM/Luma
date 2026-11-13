"""Synthetic complete/missing cover paths and persistent sync reuse."""
import io,json,unittest,urllib.error
from PIL import Image
from backend import test_civitai_sync as helpers
from backend.test_civitai import Reply,Transport,document
from backend.civitai import CivitaiClient

class Offline:
    def version_by_hash(self,*args,**kwargs):raise AssertionError('unnecessary hash lookup')
    def model(self,*args,**kwargs):raise AssertionError('unnecessary model profile')
    def version_by_id(self,*args,**kwargs):raise AssertionError('unnecessary version lookup')
    def download_cover(self,*args,**kwargs):raise AssertionError('unnecessary cover download')

class CompletionTests(unittest.TestCase):
    setUp=helpers.CivitaiSyncTests.setUp
    run_sync=helpers.CivitaiSyncTests.run_sync
    def repeat(self):
        self.library.sync(self.progress,'again',{'ids':[self.ident],'download_covers':True})
        self.assertEqual(self.progress.events[-1]['errors'],0)
        return self.library.detail(self.ident)
    def test_second_sync_reuses_persistent_metadata_and_cover_without_network_or_rewrites(self):
        self.run_sync([{'url':'https://image.civitai.com/fixture/cover.png','type':'image','nsfwLevel':1}],Reply(self.cover))
        info=self.weight.with_suffix('.civitai.info');cover=self.weight.with_suffix('.png')
        before=(info.read_bytes(),info.stat().st_mtime_ns,cover.read_bytes(),cover.stat().st_mtime_ns)
        self.library.client=Offline();self.repeat()
        self.assertEqual(before,(info.read_bytes(),info.stat().st_mtime_ns,cover.read_bytes(),cover.stat().st_mtime_ns))
    def test_default_export_is_one_info_and_one_cover(self):
        self.run_sync([{'url':'https://image.civitai.com/fixture/cover.png','type':'image','nsfwLevel':1}],Reply(self.cover))
        self.assertEqual({p.name for p in self.models.iterdir()},{self.weight.name,'local-name.civitai.info','local-name.png'})
    def test_partial_image_lookup_does_not_replace_complete_profile_or_cover_state(self):
        self.run_sync([])
        with self.store.connect() as db:full=json.loads(db.execute('SELECT record FROM luma_model_versions WHERE version_id=31').fetchone()[0])
        partial={k:v for k,v in full.items() if k not in ('profile_complete','preview_checked')}
        partial.update(model_name='By-hash fragment',description='',creator='',images=[])
        self.library.remember_version(partial)
        self.library.client=Offline();self.repeat()
        self.assertEqual(self.library.detail(self.ident)['model_name'],'Published model')
    def test_thin_hash_record_cannot_erase_profile_recovered_family_or_role(self):
        from backend.civitai import with_profile
        value={'id':31,'modelId':17,'model':{'name':'Thin'},'files':[{'type':'Model','hashes':{'SHA256':self.digest}}],'images':[]}
        thin=CivitaiClient(transport=Transport(document(value))).version_by_hash(self.digest)
        full=with_profile(thin,{'type':'LORA','versions':[{'version_id':31,'base_model':'Anima'}]})
        self.library.remember_version({**full,'profile_complete':True,'preview_checked':True})
        with self.store.connect() as db:db.execute("UPDATE luma_model_files SET sha256=?,version_id=31,status='synced' WHERE id=?",(self.digest,self.ident))
        self.library.remember_version(thin)
        self.library.client=Offline();self.repeat()
        detail=self.library.detail(self.ident);self.assertEqual((detail['type'],detail['family']),('LORA','Anima'))
    def test_existing_jpeg_preview_does_not_generate_a_second_png(self):
        stream=io.BytesIO();Image.new('RGB',(12,10),(30,60,90)).save(stream,'JPEG')
        preview=self.weight.with_name(self.weight.stem+'.preview.jpg');preview.write_bytes(stream.getvalue())
        self.run_sync([])
        self.assertFalse(self.weight.with_suffix('.png').exists());self.assertEqual(preview.read_bytes(),stream.getvalue())
    def test_legacy_jpeg_cache_can_be_reused_and_exported_as_png(self):
        self.run_sync([])
        self.library.cache_dir.mkdir(exist_ok=True,parents=True)
        stream=io.BytesIO();Image.new('RGB',(12,10)).save(stream,'JPEG')
        (self.library.cache_dir/(self.digest+'.cover')).write_bytes(stream.getvalue())
        self.library.client=Offline();self.repeat()
        self.assertTrue(self.weight.with_suffix('.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))
    def test_fifth_static_candidate_is_tried_after_four_broken_links(self):
        urls=[f'https://image.civitai.com/fixture/{i}.png' for i in range(5)]
        self.run_sync([{'url':u,'type':'image','nsfwLevel':1} for u in urls],*[urllib.error.HTTPError(u,404,'missing',{},None) for u in urls[:4]],Reply(self.cover))
        self.assertEqual(self.library.detail(self.ident)['status'],'synced')
        self.assertTrue(self.library.cover_path(self.ident))
    def test_broken_static_list_refetches_the_exact_version_once(self):
        bad='https://image.civitai.com/fixture/old.png';good='https://image.civitai.com/fixture/new.png'
        value={'id':31,'modelId':17,'name':'v1','baseModel':'SDXL 1.0','model':{'type':'Checkpoint'},'files':[{'type':'Model','hashes':{'SHA256':self.digest}}],'images':[{'url':bad,'type':'image','nsfwLevel':1}]}
        profile={'id':17,'name':'Fixture','type':'Checkpoint'}
        full={**value,'images':[{'url':good,'type':'image','nsfwLevel':1}]}
        transport=Transport(document(value),document(profile),urllib.error.HTTPError(bad,404,'missing',{},None),document(full),Reply(self.cover))
        self.library.client=CivitaiClient(transport=transport);self.repeat()
        self.assertEqual(self.library.detail(self.ident)['status'],'synced')
        self.assertEqual(sum('/model-versions/31' in r.full_url for r,_ in transport.requests),1)

if __name__=='__main__':unittest.main()
