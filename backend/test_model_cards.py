"""Synthetic SQL inventory only; never reads model/image bytes or connects online."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop.service import LibraryService
from worker.management import read


class ModelCardsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='luma-model-cards-')
        self.service = LibraryService(Path(self.temp.name) / 'isolated')
        self.library = self.service.model_library
        with self.service.store.connect() as db:
            db.executemany('INSERT INTO luma_model_roots(id,path,name) VALUES(?,?,?)',
                           [(1, str(Path(self.temp.name)/'never-read'), 'Synthetic'),
                            (2, str(Path(self.temp.name)/'other'), 'Other')])
            for i in range(1, 134):
                digest = f'{i:064x}'
                record = {'family': 'Krea 2' if i <= 130 else 'Stable Diffusion',
                          'type': 'Checkpoint', 'model_type': 'Checkpoint',
                          'model_name': 'Synthetic', 'files': [{'sha256':digest, 'type':'Model'}]}
                if i <= 65: record['files'][0]['resource_type'] = 'LoRA'
                if i == 131: record['files'][0]['type'] = 'VAE'
                if i == 132: record['files'] = [{'sha256':'f'*64, 'type':'Model'}]
                if i == 133: record['family'] = 'filename guesses are invalid'
                db.execute('INSERT INTO luma_model_versions(version_id,record,updated_at) VALUES(?,?,0)', (i,json.dumps(record)))
                db.execute("INSERT INTO luma_model_files(id,root_id,path,name,normalized_name,size,mtime_ns,ctime_ns,device,inode,change_ns,sha256,version_id) VALUES(?,?,?,?,?,1,1,1,'1','1',1,?,?)",
                           (i,1,str(Path(self.temp.name)/f'never-read-{i}.ckpt'),f'{i}.ckpt',str(i),digest,i))
                db.execute('INSERT INTO luma_model_memberships(file_id,root_id) VALUES(?,?)', (i,1 if i<=132 else 2))

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_sql_filters_before_page_and_scope_facets_before_dimensions(self):
        with patch('builtins.open', side_effect=AssertionError('no bytes')), patch.object(self.service.civitai_client,'version_by_hash',side_effect=AssertionError('no network')):
            page = read(self.service,'models',{'rootId':1,'types':['Checkpoint','LoRA'],'families':['Krea 2']},threading.Event())
            self.assertEqual(page['total'],130)
            self.assertEqual(len(page['items']),60)
            second = read(self.service,'models',{'rootId':1,'types':['LoRA','Checkpoint','LoRA'],'families':['Krea 2'],'cursor':page['next_cursor']},threading.Event())
            self.assertEqual(len(second['items']),60)
            last = self.library.list_models(root_id=1,types=['Checkpoint','LoRA'],families=['Krea 2'],cursor=second['next_cursor'])
            self.assertEqual(len(last['items']),10)
            self.assertIsNone(last['next_cursor'])
            self.assertEqual(page['facets'],{'types':['Checkpoint','LoRA','Unknown','VAE'],'families':['Krea 2','Stable Diffusion']})
            only = self.library.list_models(root_id=1,types=['Checkpoint'],families=['Krea 2'])
            self.assertEqual(only['total'],65)
            self.assertEqual(only['items'][0]['id'],66)
            self.assertEqual(only['facets'],page['facets'])
            empty = self.library.list_models(root_id=2,q='not present',types=['LoRA'])
            self.assertEqual(empty['facets'],{'types':[],'families':[]})

    def test_unknown_role_family_and_owned_cover_availability(self):
        self.library.cache_dir.mkdir()
        (self.library.cache_dir / (f'{131:064x}'+'.cover')).write_bytes(b'synthetic cover')
        with patch('builtins.open',side_effect=AssertionError('no bytes')):
            vae = self.library.list_models(types=['VAE'])['items']
            self.assertEqual([x['id'] for x in vae],[131])
            self.assertTrue(vae[0]['cover_available'])
            unknown = self.library.list_models(types=['Unknown'])['items']
            self.assertEqual([x['id'] for x in unknown],[132])
            self.assertFalse(unknown[0]['cover_available'])
            family = self.library.list_models(families=['Unknown'])['items']
            self.assertEqual([x['id'] for x in family],[133])
            self.assertEqual(family[0]['family'],'Unknown')
            self.assertEqual(self.library.detail(132)['type'],'Unknown')

    def test_cursor_binds_normalized_dimensions(self):
        page = self.library.list_models(types=['LoRA','Checkpoint'],families=['Krea 2'],limit=2)
        self.assertEqual(self.library.list_models(types=['Checkpoint','LoRA','LoRA'],families=['Krea 2'],cursor=page['next_cursor'],limit=2)['items'][0]['id'],3)
        for changes in ({'types':['LoRA'],'families':['Krea 2']},{'types':['Checkpoint','LoRA'],'families':[]}):
            with self.assertRaises(ValueError): self.library.list_models(cursor=page['next_cursor'],**changes)

    def test_dimension_validation_at_worker_and_service_and_library(self):
        for dimension in ('types','families'):
            for value in ('LoRA',None,{},[True],[''],['  '],['x'*101],['x']*33,['a\0b']):
                with self.subTest(dimension=dimension,value=value):
                    for call in (lambda:self.library.list_models(**{dimension:value}),
                                 lambda:self.service.models({dimension:value}),
                                 lambda:read(self.service,'models',{dimension:value},threading.Event())):
                        with self.assertRaises(ValueError): call()
        for call in (lambda:self.library.list_models(families=['invented']),
                     lambda:read(self.service,'models',{'families':['invented']},threading.Event())):
            with self.assertRaises(ValueError):call()

    def test_missing_cache_and_conflicting_roles_are_unknown(self):
        with self.service.store.connect() as db:
            db.execute('UPDATE luma_model_files SET version_id=NULL WHERE id=1')
            record={'type':'Checkpoint','family':None,'files':[{'sha256':f'{2:064x}','resource_type':'LoRA'},{'sha256':f'{2:064x}','resource_type':'VAE'}]}
            db.execute('UPDATE luma_model_versions SET record=? WHERE version_id=2',(json.dumps(record),))
        page=self.library.list_models(types=['Unknown'],families=['Unknown'])
        self.assertEqual([x['id'] for x in page['items']],[1,2])

    def test_unproven_item_has_explicit_unknown_and_cover_flag(self):
        with self.service.store.connect() as db:
            db.execute('UPDATE luma_model_files SET version_id=NULL WHERE id=1')
        item=self.library.list_models(limit=1)['items'][0]
        self.assertEqual(item['type'],'Unknown')
        self.assertEqual(item['family'],'Unknown')
        self.assertIs(item['cover_available'],False)

    def test_unknown_case_is_canonical_and_facets_are_bounded(self):
        with self.service.store.connect() as db:
            for i in range(1,134):
                role='unknown' if i==133 else f'Extension {i:03d}'
                record={'family':'FLUX','model_name':'Needle' if i==133 else 'Other',
                        'files':[{'sha256':f'{i:064x}','resource_type':role}]}
                db.execute('UPDATE luma_model_versions SET record=? WHERE version_id=?',(json.dumps(record),i))
        page=self.library.list_models()
        self.assertEqual(len(page['facets']['types']),128)
        self.assertIn('Unknown',page['facets']['types'])
        needle=self.library.list_models(q='Needle',types=['Unknown'])
        self.assertEqual(needle['total'],1)
        self.assertEqual(needle['facets'],{'types':['Unknown'],'families':['FLUX']})

    def test_malformed_cached_shapes_produce_unknown(self):
        for encoded in ('{','[]','null',json.dumps({'files':None,'family':{}})):
            with self.subTest(encoded=encoded),self.service.store.connect() as db:
                db.execute('UPDATE luma_model_versions SET record=? WHERE version_id=1',(encoded,))
            page=self.library.list_models(types=['Unknown'],families=['Unknown'])
            self.assertEqual([x['id'] for x in page['items']],[1])

