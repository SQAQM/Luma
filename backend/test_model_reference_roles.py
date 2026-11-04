"""Auxiliary model leaves must never be sent as checkpoint identities."""
import tempfile
import unittest
from pathlib import Path
from backend.store import Store
from backend.model_library import ModelLibrary


class ReferenceRoleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.base=Path(self.temp.name)
        self.store=Store(self.base/'isolated');self.library=ModelLibrary(self.store)
    def tearDown(self):self.temp.cleanup()
    def image(self,params):
        path=self.base/'synthetic.png';path.write_bytes(b'not decoded')
        return self.store.upsert(path,None,path.stat(),{'model':'synthetic','parameters':params})
    def test_auxiliary_and_unknown_types_do_not_enter_network_projection(self):
        for kind in ('DoRA','Hypernetwork','Upscaler','TextualInversion','CLIP','IPAdapter','T2IAdapter','Text Encoder','unknown resource'):
            with self.subTest(kind=kind):
                ident=self.image({'type':kind,'model_sha256':'a'*64})
                self.assertEqual(self.library.image_lookup_hashes([ident]),[])
    def test_known_architecture_is_still_checkpoint(self):
        ident=self.image({'model_type':'SDXL','model_sha256':'a'*64})
        self.assertEqual(len(self.library.image_lookup_hashes([ident])),1)
    def test_checkpoint_version_does_not_prove_attached_vae_was_used(self):
        model='a'*64;vae='b'*64
        self.library.remember_version({'model_id':1,'version_id':31,'type':'Checkpoint','model_type':'Checkpoint',
            'family':'Stable Diffusion','matched_sha256':model,'files':[
                {'sha256':model,'type':'Model','resource_type':'Checkpoint'},
                {'sha256':vae,'type':'VAE','resource_type':'VAE'}]})
        folder=self.base/'models';folder.mkdir();self.library.add_root(folder)
        with self.store.connect() as db:
            root=db.execute('SELECT id FROM luma_model_roots').fetchone()[0]
            for name,digest in (('model',model),('vae',vae)):
                db.execute('INSERT INTO luma_model_files(root_id,path,name,normalized_name,size,mtime_ns,ctime_ns,device,inode,change_ns,sha256,version_id) VALUES(?,?,?,?,1,1,1,1,1,1,?,31)',(root,str(folder/name),name,name,digest))
            ids={r['name']:r['id'] for r in db.execute('SELECT id,name FROM luma_model_files')}
        ident=self.image({'civitai_resources':[{'type':'checkpoint','modelVersionId':31}]})
        self.assertEqual(self.store.list_images(**self.library.image_filters(ids['model']))['total'],1)
        self.assertEqual(self.store.list_images(**self.library.image_filters(ids['vae']))['total'],0)
