"""Local covers and bounded repeated sync, with only synthetic files."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from backend.store import Store
from backend.model_library import ModelLibrary
from backend.duplicates import VerifiedFile


class Progress:
    def cancelled(self, _): return False
    def progress(self, _, **values): self.values = values


class ModelWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name); self.folder = self.base/'models'; self.folder.mkdir()
        self.weight = self.folder/'合成 [model].safetensors'; self.weight.write_bytes(b'inert model')
        self.store = Store(self.base/'Data'); self.library = ModelLibrary(self.store); self.manager = Progress()
        root = self.library.add_root(self.folder)
        self.library.scan(self.manager,'fixture',{'root_id':root['id']})
        self.ident = self.library.list_models()['items'][0]['id']
        self.digest = hashlib.sha256(self.weight.read_bytes()).hexdigest()
        owner = self
        class Client:
            def __init__(self): self.versions = self.profiles = self.downloads = 0
            def version_by_hash(self, digest, cancelled):
                self.versions += 1
                return {'model_id':17,'version_id':31,'model_name':'Fixture','type':'Checkpoint','family':'FLUX',
                        'files':[{'sha256':owner.digest}],'matched_sha256':digest,'images':[]}
            def model(self, ident, cancelled):
                self.profiles += 1
                return {'model_id':ident,'description':'Synthetic introduction','versions':[]}
            def download_cover(self, *args, **kwargs):
                self.downloads += 1; raise AssertionError('existing local cover must not download')
        self.client = Client(); self.library.client = self.client

    def cover(self, suffix='.preview.png', color='navy'):
        path=self.weight.with_name(self.weight.stem+suffix)
        Image.new('RGB',(1800,1200),color).save(path)
        return path

    def sync(self, ids=None, covers=True):
        self.library.sync(self.manager,'fixture',{'ids':ids or [self.ident],'download_covers':covers})

    def test_same_name_local_preview_is_available_without_hash_or_network(self):
        preview=self.cover()
        with patch.object(VerifiedFile,'digest',side_effect=AssertionError('no weight hash')):
            self.assertTrue(self.library.detail(self.ident)['cover_available'])
            asset=self.library.cover_path(self.ident)
        self.assertIsNotNone(asset)
        self.assertTrue(Path(asset).is_relative_to(self.store.directory/'model-covers'))
        with Image.open(asset) as im: self.assertLessEqual(max(im.size),640)
        self.assertTrue(preview.exists()); self.assertEqual(self.client.versions,0)

    def test_generic_neighbor_cover_is_not_assigned_to_model(self):
        Image.new('RGB',(8,8)).save(self.folder/'cover.png')
        self.assertFalse(self.library.detail(self.ident)['cover_available'])

    def test_bad_preview_falls_back_to_next_exact_name_candidate(self):
        self.weight.with_name(self.weight.stem+'.preview.png').write_bytes(b'not an image')
        self.cover('.jpg','green')
        self.assertIsNotNone(self.library.cover_path(self.ident))

    def test_local_preview_change_invalidates_cached_thumbnail(self):
        preview=self.cover(); first=self.library.cover_path(self.ident)
        self.assertIsNotNone(first)
        Image.new('RGB',(20,40),'red').save(preview)
        second=self.library.cover_path(self.ident)
        self.assertIsNotNone(second); self.assertNotEqual(first,second)

    def test_repeated_sync_does_not_rehash_unchanged_weight_or_download_existing_cover(self):
        self.cover(); self.sync()
        self.assertEqual(self.manager.values['errors'],0)
        with patch.object(VerifiedFile,'digest',side_effect=AssertionError('unchanged weight was reread')):
            self.sync()
        self.assertEqual(self.manager.values['errors'],0)
        self.assertEqual(self.client.downloads,0)

    def test_changed_weight_cannot_reuse_old_hash(self):
        self.sync(covers=False)
        self.weight.write_bytes(b'changed model')
        before=self.client.versions
        self.sync(covers=False)
        self.assertEqual(self.client.versions,before)
        self.assertEqual(self.library.detail(self.ident)['status'],'changed')

    def test_same_hash_in_batch_reuses_parent_and_version_requests(self):
        other=self.folder/'second.safetensors';other.write_bytes(self.weight.read_bytes())
        self.library.scan(self.manager,'fixture',{'root_id':self.library.roots()[0]['id']})
        ids=[m['id'] for m in self.library.list_models()['items']]
        self.sync(ids,covers=False)
        self.assertEqual(self.manager.values['errors'],0)
        self.assertEqual(self.client.versions,1);self.assertEqual(self.client.profiles,1)

    def test_worker_receives_only_cached_local_thumbnail_and_removal_keeps_sources(self):
        from types import SimpleNamespace
        from worker.management import cover
        preview=self.cover()
        asset=cover(SimpleNamespace(store=self.store,model_cover_path=self.library.cover_path),self.ident)
        self.assertTrue(asset['relativePath'].startswith('thumbnails/model-covers/'))
        cached=self.store.directory/asset['relativePath']
        self.assertEqual(hashlib.sha256(cached.read_bytes()).hexdigest(),asset['sha256'])
        self.library.remove_root(self.library.roots()[0]['id'])
        self.assertTrue(preview.exists());self.assertEqual(self.weight.read_bytes(),b'inert model')

    def test_generated_thumbnail_does_not_retain_text_metadata(self):
        from PIL.PngImagePlugin import PngInfo
        info=PngInfo();info.add_text('parameters','synthetic private fixture')
        Image.new('RGB',(10,20),'blue').save(self.weight.with_suffix('.png'),pnginfo=info)
        asset=self.library.cover_path(self.ident)
        with Image.open(asset) as image:self.assertNotIn('parameters',image.info)

    def test_invalid_key_stops_batch_without_repeating_authentication_failures(self):
        from backend.civitai import CivitaiError
        for index in range(4):(self.folder/f'other-{index}.ckpt').write_bytes(str(index).encode())
        self.library.scan(self.manager,'fixture',{'root_id':self.library.roots()[0]['id']})
        with patch.object(self.client,'version_by_hash',side_effect=CivitaiError('unauthorized')) as lookup:
            self.sync([model['id'] for model in self.library.list_models()['items']],covers=False)
        self.assertEqual(lookup.call_count,1)
        self.assertEqual(self.manager.values['processed'],1)


if __name__=='__main__':unittest.main()
