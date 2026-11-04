"""Real client + library using official-shaped fake responses; no network."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from backend.civitai import CivitaiClient
from backend.model_library import ModelLibrary
from backend.store import Store
from backend.test_civitai import version, document, Reply, Transport
from backend.test_model_library import Manager


class CivitaiPipelineTests(unittest.TestCase):
    def test_hash_version_then_model_profile_populates_plain_introduction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); folder=root/'models';folder.mkdir()
            path=folder/'synthetic.safetensors';path.write_bytes(b'inert synthetic weights')
            sha=hashlib.sha256(path.read_bytes()).hexdigest()
            value=version();value['files'][0]['hashes']={'SHA256':sha,'AutoV2':sha[:10]}
            profile={'id':17,'type':'Checkpoint','name':'Synthetic checkpoint','description':'<p>Public introduction</p><script>bad()</script>','creator':{'username':'Synthetic creator'}}
            transport=Transport(document(value),document(profile))
            library=ModelLibrary(Store(root/'isolated'),client=CivitaiClient(transport=transport))
            manager=Manager();modelroot=library.add_root(folder);library.scan(manager,'test',{'root_id':modelroot['id']})
            ident=library.list_models()['items'][0]['id'];library.sync(manager,'test',{'ids':[ident],'download_covers':False})
            detail=library.detail(ident)
            self.assertEqual(detail['description'],'Public introduction')
            self.assertEqual(detail['version_description'],'Version notes')
            self.assertEqual(detail['creator'],'Synthetic creator')
            self.assertEqual(detail.get('trigger_words'),['synthetic word'])
            self.assertEqual(detail['trained_words'],['synthetic word'])
            self.assertEqual([request.full_url.rsplit('/',1)[-1] for request,_ in transport.requests],[sha,'17'])
            self.assertEqual(manager.values['errors'],0)
            # A later image hash lookup has only the minimal version response;
            # it must not erase the separately fetched model profile.
            transport.replies.append(document(value))
            self.assertTrue(library.remember_version(library.client.version_by_hash(sha)))
            self.assertEqual(library.detail(ident)['creator'],'Synthetic creator')
            self.assertIn('Public introduction',library.detail(ident)['description'])
            # A profile failure on a later refresh must not erase good identity
            # or already-cached introduction/creator.
            transport.replies.extend([document(value),Reply(b'',status=503)])
            library.sync(manager,'test',{'ids':[ident],'download_covers':False})
            detail=library.detail(ident)
            self.assertEqual(detail['description'],'Public introduction')
            self.assertEqual(detail['version_description'],'Version notes')
            self.assertEqual(detail.get('trigger_words'),['synthetic word'])
            self.assertEqual(detail['creator'],'Synthetic creator')
            self.assertEqual(detail['version_id'],31)
            self.assertEqual(detail['status'],'sync_error_unavailable')
            self.assertEqual(manager.values['errors'],1)
            # A successful, explicitly requested empty profile is authoritative,
            # unlike a minimal hash response or a failed profile request.
            profile['description']='';profile['creator']={}
            transport.replies.extend([document(value),document(profile)])
            library.sync(manager,'test',{'ids':[ident],'download_covers':False})
            self.assertEqual(library.detail(ident)['creator'],'')
            self.assertEqual(library.detail(ident)['description'],'')
            self.assertEqual(library.detail(ident)['version_description'],'Version notes')
            self.assertEqual(manager.values['errors'],0)
