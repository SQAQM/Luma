import os
import tempfile
import threading
import unittest
import shutil
import sqlite3
from pathlib import Path
from worker.ownership import prepare_preview,EngineStartError
from worker.dispatch import dispatch


class GalleryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base=tempfile.TemporaryDirectory(prefix='luma-stage4-template-');cls.template=Path(cls.base.name)/'catalog';prepare_preview(cls.template)
        from worker.demo_catalog import seed
        seed(cls.template)
    @classmethod
    def tearDownClass(cls):cls.base.cleanup()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='luma-stage4-');self.data=Path(self.temp.name)/'data';shutil.copytree(self.template,self.data)
        with sqlite3.connect(self.data/'library.sqlite3') as db:
            db.execute('UPDATE images SET path=REPLACE(path,?,?)',(str(self.template),str(self.data)))
            db.execute('UPDATE roots SET path=REPLACE(path,?,?)',(str(self.template),str(self.data)))
        db.close()
        from desktop.service import LibraryService
        self.service=LibraryService(self.data);self.event=threading.Event()
    def tearDown(self):
        if hasattr(self,'service'):self.service.close()
        self.temp.cleanup()
    def call(self,method,payload):return dispatch(self.service,method,payload,self.event)
    def test_pages_full_detail_semantics_and_long_id(self):
        first=self.call('catalog.query',{'filters':{'sort':'name'},'limit':120})
        self.assertEqual(len(first['items']),120);self.assertEqual(first['total'],131)
        self.assertNotIn('prompt',first['items'][0]);self.assertEqual(len(first['items'][0]['identity']),64)
        second=self.call('catalog.query',{'filters':{'sort':'name'},'cursor':first['next_cursor'],'limit':120})
        self.assertEqual(len(second['items']),11);self.assertTrue(any(r['id']>1_000_000 for r in second['items']))
        item=first['items'][0];detail=self.call('catalog.detail',{'imageId':item['id'],'identity':item['identity']})
        self.assertGreater(len(detail['prompt']),2048);self.assertEqual(detail['content_tags'][0]['name'],'风景')
        self.assertNotEqual(detail['content_tags'][0]['name'],detail['prompt_tags'][0])
        blank=first['items'][1];self.assertEqual(self.call('catalog.detail',{'imageId':blank['id'],'identity':blank['identity']})['negative_prompt'],'')
    def test_stale_identity_and_cursor_rejected(self):
        page=self.call('catalog.query',{'limit':120});item=page['items'][0]
        with self.service.store.connect() as db:db.execute('UPDATE images SET indexed_at=indexed_at+1 WHERE id=?',(item['id'],))
        with self.assertRaises(ValueError):self.call('catalog.detail',{'imageId':item['id'],'identity':item['identity']})
        with self.assertRaises(ValueError):self.call('catalog.thumbnail',{'imageId':item['id'],'identity':item['identity']})
        with self.assertRaises(ValueError):self.call('catalog.query',{'filters':{'sort':'name'},'cursor':page['next_cursor']})
    def test_thumbnail_aba_and_bytes_unchanged(self):
        item=self.call('catalog.query',{'filters':{'sort':'name'},'limit':1})['items'][0]
        path=Path(item['path']);before=path.read_bytes();first=self.call('catalog.thumbnail',{'imageId':item['id'],'identity':item['identity'],'size':320})
        self.assertEqual(path.read_bytes(),before);self.assertTrue((self.data/first['relativePath']).is_file())
        stat=path.stat();replacement=path.with_suffix('.replacement');replacement.write_bytes(before);os.replace(replacement,path);os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        second=self.call('catalog.thumbnail',{'imageId':item['id'],'identity':item['identity'],'size':320})
        self.assertNotEqual(first['resourceId'],second['resourceId']);self.assertEqual(path.read_bytes(),before)
    def test_empty_filter_and_capabilities(self):
        self.assertEqual(self.call('catalog.query',{'filters':{'q':'no-such-fixture'}})['items'],[])
        for n in [121,True,0]:
            with self.assertRaises(ValueError):self.call('catalog.query',{'limit':n})
        with self.assertRaises(ValueError):self.call('organize.execute',{})
        from worker.demo_catalog import seed
        with self.assertRaises(EngineStartError):seed(self.data)
    def test_cache_replacement_repaired_and_bound(self):
        import hashlib
        page=self.call('catalog.query',{'filters':{'sort':'name'},'limit':2});a,b=page['items']
        asset=self.call('catalog.thumbnail',{'imageId':a['id'],'identity':a['identity'],'size':320})
        other=self.call('catalog.thumbnail',{'imageId':b['id'],'identity':b['identity'],'size':320})
        cache=self.data/asset['relativePath'];cache.write_bytes((self.data/other['relativePath']).read_bytes())
        repaired=self.call('catalog.thumbnail',{'imageId':a['id'],'identity':a['identity'],'size':320})
        self.assertEqual(repaired['sha256'],asset['sha256']);self.assertEqual(hashlib.sha256(cache.read_bytes()).hexdigest(),asset['sha256'])
    def test_exact_artist_filter_and_cursor_scope(self):
        with self.service.store.connect() as db:db.execute("UPDATE images SET artist='Synthetic artist A' WHERE id IN (1,2,3)")
        page=self.call('catalog.query',{'filters':{'artist':'Synthetic artist A'},'limit':2})
        self.assertEqual(page['total'],3);self.assertEqual(len(page['items']),2)
        with self.assertRaises(ValueError):self.call('catalog.query',{'filters':{'artist':'Synthetic artist B'},'cursor':page['next_cursor']})
    def test_facets_offer_concrete_models_and_families(self):
        facets=self.call('catalog.facets',{})
        names={row['name'] for row in facets['model']}
        self.assertIn('合成模型 SDXL',names);self.assertIn('Stable Diffusion',names)
        self.assertIn('合成画师 A',{row['name'] for row in facets['artist']})
        self.assertTrue(all(len(values)<=128 for values in facets.values()))

if __name__=='__main__':unittest.main()
