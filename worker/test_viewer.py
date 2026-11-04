"""Only generated local files; never reads user data or metadata fixtures."""
import hashlib, os, threading
from pathlib import Path
from unittest import mock
from PIL import Image
from worker.test_gallery import GalleryTests


class ViewerTests(GalleryTests):
    def item(self):
        return self.call('catalog.query',{'filters':{'sort':'name'},'limit':1})['items'][0]
    def preview(self,item,mode='standard'):
        return self.call('catalog.preview.v2',{'imageId':item['id'],'identity':item['identity'],'mode':mode})
    def replace_fixture(self,image,fmt='PNG',**options):
        path=self.data/'demo-images'/'viewer-fixture.png'; image.save(path,format=fmt,**options)
        info=path.stat()
        with self.service.store.connect() as db:
            db.execute('UPDATE images SET path=?,size=?,mtime_ns=?,width=?,height=?,format=? WHERE id=1',(str(path),info.st_size,info.st_mtime_ns,image.width,image.height,fmt))
        return self.item()
    def test_preview_v2_preserves_alpha_and_source_bytes(self):
        item=self.replace_fixture(Image.new('RGBA',(100,60),(80,120,180,90)))
        before=Path(item['path']).read_bytes();asset=self.preview(item,'original')
        self.assertEqual(asset['version'],2);self.assertEqual(asset['kind'],'original');self.assertEqual(asset['identity'],item['identity'])
        with Image.open(self.data/asset['relativePath']) as image:
            self.assertEqual(image.size,(100,60));self.assertEqual(image.getpixel((0,0))[3],90)
        self.assertEqual(hashlib.sha256((self.data/asset['relativePath']).read_bytes()).hexdigest(),asset['sha256'])
        self.assertEqual(Path(item['path']).read_bytes(),before)
    def test_synthetic_viewer_fixture_exercises_real_pan(self):
        item=self.item();self.assertGreater(item['width'],1200);self.assertGreater(item['height'],800)
    def test_orientation_and_standard_preview_dimensions(self):
        exif=Image.Exif();exif[274]=6
        item=self.replace_fixture(Image.new('RGB',(80,40),'red'),'JPEG',exif=exif)
        asset=self.preview(item);self.assertEqual((asset['width'],asset['height']),(40,80))
        self.assertEqual((asset['sourceWidth'],asset['sourceHeight']),(40,80))
    def test_hardlinks_are_readable_without_mutation(self):
        item=self.item();path=Path(item['path']);link=path.with_name('viewer-hardlink.png');os.link(path,link)
        before=path.read_bytes();asset=self.preview(item)
        self.assertEqual(asset['version'],2);self.assertEqual(path.read_bytes(),before);self.assertEqual(link.read_bytes(),before)
        self.assertEqual(self.call('catalog.thumbnail',{'imageId':item['id'],'identity':item['identity'],'size':320})['identity'],item['identity'])
    def test_static_multiformat_and_corrupt_input(self):
        for fmt in ('BMP','GIF','TIFF','WEBP'):
            item=self.replace_fixture(Image.new('RGB',(60,40),'blue'),fmt)
            result=self.preview(item)
            self.assertEqual((result['width'],result['height']),(60,40))
            if fmt=='TIFF':self.assertEqual(result['kind'],'compatibility')
        path=Path(item['path']);path.write_bytes(b'synthetic unsupported image');info=path.stat()
        with self.service.store.connect() as db:db.execute('UPDATE images SET size=?,mtime_ns=? WHERE id=1',(info.st_size,info.st_mtime_ns))
        with self.assertRaisesRegex(ValueError,'unsupported'):self.preview(self.item())
    def test_identity_aba_and_cache_replacement(self):
        item=self.item();first=self.preview(item);cache=self.data/first['relativePath'];cache.write_bytes(b'corrupt synthetic cache')
        repaired=self.preview(item);self.assertEqual(first['sha256'],repaired['sha256'])
        path=Path(item['path']);stat=path.stat();temp=path.with_suffix('.replacement');temp.write_bytes(path.read_bytes());os.replace(temp,path);os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        self.assertNotEqual(self.preview(item)['resourceId'],first['resourceId'])
        with self.service.store.connect() as db:db.execute('UPDATE images SET indexed_at=indexed_at+1 WHERE id=?',(item['id'],))
        with self.assertRaisesRegex(ValueError,'stale_identity'):self.preview(item)
    def test_corrupt_cache_metadata_is_repaired(self):
        import json
        item=self.item();asset=self.preview(item);seal=(self.data/asset['relativePath']).with_suffix('.json')
        metadata=json.loads(seal.read_text());metadata['width']=0;seal.write_text(json.dumps(metadata))
        self.assertEqual(self.preview(item)['width'],asset['width'])
    def test_valid_range_inconsistent_cache_metadata_is_repaired(self):
        import json
        item=self.item();asset=self.preview(item);seal=(self.data/asset['relativePath']).with_suffix('.json')
        for field in ('bytes','width','height'):
            metadata=json.loads(seal.read_text());metadata[field]+=1;seal.write_text(json.dumps(metadata))
            self.assertEqual(self.preview(item)[field],asset[field])
    def test_budgets_modes_and_cancel_fail_closed(self):
        item=self.item()
        with self.assertRaises(ValueError):self.preview(item,'unbounded')
        with mock.patch('worker.preview_assets.MAX_SOURCE_PIXELS',100):
            with self.assertRaisesRegex(ValueError,'unsupported'):self.preview(item)
        with mock.patch('worker.preview_assets.MAX_ORIGINAL_PIXELS',100):
            asset=self.preview(item,'original');self.assertEqual(asset['kind'],'compatibility');self.assertLessEqual(max(asset['width'],asset['height']),1200)
        self.event.set()
        with self.assertRaisesRegex(ValueError,'cancelled'):self.preview(item)
    def test_neighbor_window_has_identities_and_is_bounded(self):
        result=self.call('catalog.neighbors',{'imageId':120,'filters':{'sort':'name'},'before':4,'after':4})
        self.assertLessEqual(len(result['items']),9)
        self.assertTrue(all(len(row.get('identity',''))==64 for row in result['items']))

if __name__=='__main__':
    import unittest
    unittest.main()
