"""Cross-layer Civitai file roles and classifier variant compatibility."""
import tempfile
import unittest
from pathlib import Path

from backend.civitai import CivitaiClient
from backend.metadata import classify_model
from backend.model_library import ModelLibrary
from backend.model_registry import ModelRegistry
from backend.store import Store
from backend.test_civitai import version, document, Transport, SHA


class CivitaiRoleTests(unittest.TestCase):
    def lookup(self, value, digest=SHA):
        return CivitaiClient(transport=Transport(document(value))).version_by_hash(digest)

    def test_attached_vae_is_not_parent_checkpoint(self):
        value=version(); value['files'][0]['type']='VAE'
        record=self.lookup(value)
        self.assertEqual(record['type'], 'VAE')
        with tempfile.TemporaryDirectory() as temporary:
            library=ModelLibrary(Store(Path(temporary)/'isolated'))
            self.assertTrue(library.remember_version(record))
            self.assertEqual(library.catalog_entries(), [])

    def test_same_version_query_order_never_promotes_vae_digest(self):
        vae_hash='c'*64
        value=version(); value['files'].append({'name':'attached.bin','type':'VAE','hashes':{'SHA256':vae_hash,'AutoV2':vae_hash[:10]}})
        with tempfile.TemporaryDirectory() as temporary:
            library=ModelLibrary(Store(Path(temporary)/'isolated'))
            for digest in (vae_hash,SHA,vae_hash):
                self.assertTrue(library.remember_version(self.lookup(value,digest)))
                entries=library.catalog_entries()
                self.assertFalse(any(e['match']==vae_hash for e in entries))
            self.assertEqual([e['match'] for e in entries], [SHA])

    def test_civitai_sdxl_subtypes_agree_with_explicit_sdxl(self):
        for base in ('SDXL 1.0','Illustrious','Pony','SDXL Turbo'):
            with self.subTest(base=base), tempfile.TemporaryDirectory() as temporary:
                value=version();value['baseModel']=base
                library=ModelLibrary(Store(Path(temporary)/'isolated'))
                library.remember_version(self.lookup(value))
                baseline=classify_model('synthetic',metadata={'base_model':'SDXL'})
                result=ModelRegistry.resolve({'model':'synthetic','parameters':{'model_hash':SHA}},baseline,[],catalog=library.catalog_entries())
                self.assertEqual(result['family'],'Stable Diffusion',result)
                self.assertNotEqual(result.get('reason_code'),'conflict')

    def test_missing_or_conflicting_file_role_cannot_establish_checkpoint(self):
        for role in (None,'Training Data','Unknown'):
            value=version()
            if role is None: value['files'][0].pop('type')
            else:value['files'][0]['type']=role
            record=self.lookup(value)
            self.assertNotEqual(record['type'],'Checkpoint')

    def test_conflicting_verified_file_roles_across_versions_abstain(self):
        with tempfile.TemporaryDirectory() as temporary:
            library=ModelLibrary(Store(Path(temporary)/'isolated'))
            value=version();library.remember_version(self.lookup(value))
            value['id']=32;value['files'][0]['type']='VAE'
            library.remember_version(self.lookup(value))
            self.assertEqual(library.catalog_entries(),[])
