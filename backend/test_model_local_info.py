import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.store import Store
from backend.model_library import ModelLibrary


class Progress:
    def cancelled(self, _): return False
    def progress(self, _, **values): pass


class LocalModelInfoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / 'models'; self.folder.mkdir()
        self.weight = self.folder / '合成模型.safetensors'; self.weight.write_bytes(b'inert')
        self.store = Store(Path(self.temp.name) / 'Data'); self.lib = ModelLibrary(self.store)
        self.root = self.lib.add_root(self.folder)['id']

    def info(self, value, suffix='.cm-info.json'):
        path = self.weight.with_name(self.weight.stem + suffix)
        path.write_text(json.dumps(value), encoding='utf-8'); return path

    def scan(self):
        self.lib.scan(Progress(), 'fixture', {'root_id': self.root})
        return self.lib.detail(self.lib.list_models()['items'][0]['id'])

    def test_stability_matrix_info_is_usable_without_reading_or_hashing_weight(self):
        self.info({'ModelId': 17, 'VersionId': 31, 'ModelName': 'Synthetic model', 'VersionName': 'Version 2',
                   'ModelType': 'LORA', 'BaseModel': 'Krea 2', 'ModelDescription': '<p>Local intro</p>',
                   'TrainedWords': ['synthetic'], 'Hashes': {'SHA256': 'a'*64}})
        from backend.duplicates import VerifiedFile
        with patch.object(VerifiedFile, 'digest', side_effect=AssertionError('no weight hashing')):
            item = self.scan()
        self.assertEqual(item['model_name'], 'Synthetic model')
        self.assertEqual(item['type'], 'LORA'); self.assertEqual(item['family'], 'Krea 2')
        self.assertEqual(item['description'], 'Local intro'); self.assertEqual(item['trigger_words'], ['synthetic'])
        self.assertEqual(item['metadata_source'], 'local'); self.assertEqual(item['sha256'], '')
        self.assertIsNone(item['version_id'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM luma_model_verified_hashes').fetchone()[0], 0)

    def test_civitai_version_sidecar_and_type_family_filters(self):
        self.info({'id': 31, 'modelId': 17, 'name': 'Version 2', 'baseModel': 'SDXL 1.0',
                   'model': {'name': 'Synthetic XL', 'type': 'Checkpoint', 'description': 'Intro'},
                   'trainedWords': ['fixture']}, '.civitai.info')
        self.scan()
        page = self.lib.list_models(types=['Checkpoint'], families=['Stable Diffusion'])
        self.assertEqual(page['total'], 1); self.assertEqual(page['items'][0]['model_name'], 'Synthetic XL')
        self.assertEqual(self.lib.list_models(types=['LORA'])['total'], 0)
        self.assertEqual(self.lib.list_models(q='Synthetic XL')['total'], 1)

    def test_rescan_updates_or_removes_old_local_info(self):
        path = self.info({'ModelName': 'First', 'BaseModel': 'Krea2'})
        self.assertEqual(self.scan()['model_name'], 'First')
        self.info({'ModelName': 'Second', 'BaseModel': 'Anima'})
        self.assertEqual(self.scan()['family'], 'Anima')
        path.unlink()
        self.assertEqual(self.scan()['metadata_source'], '')

    def test_generic_neighbor_and_invalid_or_oversized_json_are_ignored(self):
        (self.folder/'model.cm-info.json').write_text('{"ModelName":"Other"}')
        path = self.weight.with_name(self.weight.stem+'.cm-info.json')
        for raw in ('[1,2]', '{bad', ' '*1048577):
            path.write_text(raw)
            self.assertEqual(self.scan().get('metadata_source', ''), '')

    def test_file_name_with_extension_is_also_supported(self):
        self.weight.with_name(self.weight.name+'.civitai.info').write_text(json.dumps({'modelName':'Local', 'modelType':'LORA', 'baseModel':'Flux.1 D'}))
        item = self.scan()
        self.assertEqual(item['model_name'], 'Local'); self.assertEqual(item['family'], 'FLUX')

    def test_descriptions_are_text_and_external_links_are_not_imported(self):
        self.info({'ModelName':'Local', 'ModelId':17, 'VersionId':31, 'Source':'HuggingFace',
                   'ModelDescription':'<script>hidden()</script><b>Visible</b>', 'source_url':'https://example.invalid'})
        item = self.scan()
        self.assertEqual(item['description'], 'Visible'); self.assertEqual(item['source_url'], '')

    def test_local_claim_does_not_populate_remote_versions_or_image_reference_tables(self):
        self.info({'ModelName':'Local', 'ModelId':17, 'VersionId':31, 'Hashes':{'SHA256':'a'*64}})
        self.scan()
        with self.store.connect() as db:
            for table in ('luma_model_versions', 'luma_model_verified_hashes', 'luma_image_model_refs'):
                self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0], 0)

    def test_verified_network_record_has_priority_in_details_and_filters(self):
        self.info({'ModelName':'Local', 'BaseModel':'Krea 2', 'ModelType':'LORA'})
        item = self.scan(); digest = 'a'*64
        self.lib.remember_version({'model_id':17,'version_id':31,'model_name':'Verified','family':'FLUX',
                                  'type':'Checkpoint','files':[{'sha256':digest}], 'matched_sha256':digest})
        with self.store.connect() as db:
            db.execute("UPDATE luma_model_files SET sha256=?,version_id=31,status='synced' WHERE id=?",(digest,item['id']))
        item = self.lib.detail(item['id'])
        self.assertEqual(item['metadata_source'],'civitai'); self.assertEqual(item['model_name'],'Verified')
        self.assertEqual(item['type'],'Checkpoint')
        self.assertEqual(self.lib.list_models(families=['Krea 2'])['total'],0)
        self.assertEqual(self.lib.list_models(families=['FLUX'])['total'],1)

    def test_local_metadata_pagination_and_revision_remain_bounded(self):
        for index in range(5):
            path=self.folder/f'fixture-{index}.gguf'; path.write_bytes(b'inert')
            path.with_suffix('.cm-info.json').write_text(json.dumps({'ModelName':f'Local {index}','ModelType':'LORA','BaseModel':'Anima'}))
        self.scan()
        first=self.lib.list_models(limit=2,types=['LORA'],families=['Anima'])
        self.assertEqual(first['total'],5); self.assertEqual(len(first['items']),2)
        second=self.lib.list_models(limit=2,types=['LORA'],families=['Anima'],cursor=first['next_cursor'])
        self.assertTrue(set(i['id'] for i in first['items']).isdisjoint(i['id'] for i in second['items']))
        self.scan()
        with self.assertRaises(ValueError): self.lib.list_models(limit=2,types=['LORA'],families=['Anima'],cursor=first['next_cursor'])

    def test_matrix_numeric_source_civitai_and_bom(self):
        path=self.info({'ModelName':'Local','ModelId':17,'VersionId':31,'Source':0})
        path.write_text('\ufeff'+path.read_text(),encoding='utf-8')
        self.assertEqual(self.scan()['source_url'],'https://civitai.com/models/17?modelVersionId=31')


if __name__ == '__main__': unittest.main()
