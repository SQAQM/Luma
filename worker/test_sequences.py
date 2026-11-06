"""Generated multiframe files only; no real library or private metadata."""
import json, os, unittest
from pathlib import Path
from unittest import mock
from PIL import Image
from worker import test_gallery as gallery_helpers, test_viewer as viewer_helpers


class SequenceTests(unittest.TestCase):
    setUpClass=classmethod(gallery_helpers.GalleryTests.setUpClass.__func__)
    tearDownClass=classmethod(gallery_helpers.GalleryTests.tearDownClass.__func__)
    setUp=gallery_helpers.GalleryTests.setUp
    tearDown=gallery_helpers.GalleryTests.tearDown
    call=gallery_helpers.GalleryTests.call
    item=viewer_helpers.ViewerTests.item
    replace_fixture=viewer_helpers.ViewerTests.replace_fixture

    def sequence(self,fmt,loop=0):
        frames=[Image.new('RGB',(64,40),color) for color in ('red','green','blue')]
        return self.replace_fixture(frames[0],fmt,save_all=True,append_images=frames[1:],duration=[80,140,200],loop=loop,lossless=True)

    def frame(self,item,index=0,mode='standard'):
        return self.call('catalog.preview.v2',{'imageId':item['id'],'identity':item['identity'],'mode':mode,'frame':index})

    def color(self,asset):
        with Image.open(self.data/asset['relativePath']) as image:return image.convert('RGB').getpixel((0,0))

    def test_gif_frames_timings_and_loop(self):
        item=self.sequence('GIF',1);before=Path(item['path']).read_bytes()
        frames=[self.frame(item,i) for i in range(3)]
        self.assertEqual([a['frame'] for a in frames],[0,1,2])
        self.assertEqual([self.color(a) for a in frames],[(255,0,0),(0,128,0),(0,0,255)])
        self.assertEqual([a['durationMs'] for a in frames],[80,140,200])
        self.assertTrue(all(a['frameCount']==3 and a['animated'] and a['repeatLimit']==2 for a in frames))
        self.assertEqual(len({a['resourceId'] for a in frames}),3)
        self.assertEqual(Path(item['path']).read_bytes(),before)

    def test_webp_duration_is_loaded_for_each_frame(self):
        item=self.sequence('WEBP',1)
        frames=[self.frame(item,i) for i in range(3)]
        self.assertEqual([a['durationMs'] for a in frames],[80,140,200])
        self.assertEqual([self.color(a) for a in frames],[(255,0,0),(0,128,0),(0,0,255)])
        self.assertTrue(all(a['repeatLimit']==1 for a in frames))

    def test_tiff_pages_have_individual_size_and_original_pixels(self):
        item=self.replace_fixture(Image.new('RGB',(60,40),'red'),'TIFF',save_all=True,
                                  append_images=[Image.new('RGB',(30,50),'blue')])
        first=self.frame(item,0);second=self.frame(item,1,'original')
        self.assertEqual((second['sourceWidth'],second['sourceHeight']),(30,50))
        self.assertEqual((second['width'],second['height']),(30,50))
        self.assertEqual(second['kind'],'original')
        self.assertEqual(second['frameCount'],2);self.assertFalse(second['animated'])
        self.assertEqual(self.color(first),(255,0,0));self.assertEqual(self.color(second),(0,0,255))

    def test_single_container_is_reused_and_does_not_lock_original(self):
        item=self.sequence('GIF')
        self.frame(item,0);cache=self.service._viewer_source_cache
        self.frame(item,1);self.frame(item,0)
        self.assertEqual(cache.loads,1)
        path=Path(item['path']);moved=path.with_suffix('.moved');path.rename(moved);moved.rename(path)

    def test_invalid_frame_and_request_types(self):
        item=self.sequence('GIF')
        for index in (-1,3,True,1.5,4096):
            with self.subTest(index=index),self.assertRaises(ValueError):self.frame(item,index)

    def test_release_discards_container_and_other_owner_cannot_release(self):
        item=self.sequence('WEBP');self.frame(item)
        cache=self.service._viewer_source_cache
        self.assertGreater(cache.bytes,0)
        self.call('catalog.preview.release',{'imageId':item['id']+1,'identity':item['identity']})
        self.assertGreater(cache.bytes,0)
        self.call('catalog.preview.release',{'imageId':item['id'],'identity':item['identity']})
        self.assertEqual(cache.bytes,0)

    def test_animated_cache_seal_frame_and_count_are_checked(self):
        item=self.sequence('GIF');asset=self.frame(item,1)
        seal=(self.data/asset['relativePath']).with_suffix('.json')
        value=json.loads(seal.read_text());value['frame']=0;value['frameCount']=99;seal.write_text(json.dumps(value))
        fixed=self.frame(item,1)
        self.assertEqual(fixed['frame'],1);self.assertEqual(fixed['frameCount'],3)

    def test_replaced_source_never_reuses_old_decoder(self):
        item=self.sequence('GIF');before=self.frame(item)
        path=Path(item['path']);info=path.stat();replacement=path.with_suffix('.replacement')
        replacement.write_bytes(path.read_bytes());os.replace(replacement,path);os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns))
        after=self.frame(item)
        self.assertNotEqual(before['resourceId'],after['resourceId'])
        self.assertEqual(self.service._viewer_source_cache.loads,2)

    def test_cancel_and_frame_count_budget(self):
        item=self.sequence('GIF')
        from worker import preview_assets
        with mock.patch.object(preview_assets,'MAX_FRAMES',2):
            with self.assertRaisesRegex(ValueError,'unsupported'):self.frame(item)
        self.event.set()
        with self.assertRaisesRegex(ValueError,'cancelled'):self.frame(item)

    def test_service_close_releases_decoder(self):
        item=self.sequence('GIF');self.frame(item);cache=self.service._viewer_source_cache
        self.service.close();self.assertEqual(cache.bytes,0)

    def test_quota_removes_evicted_frame_descriptor(self):
        item=self.sequence('GIF');first=self.frame(item,0)
        self.service.thumbnail_cache_limit_bytes=1
        self.frame(item,1)
        path=self.data/first['relativePath']
        self.assertFalse(path.exists());self.assertFalse(path.with_suffix('.json').exists())

    def test_apng_default_poster_is_not_a_playback_frame(self):
        item=self.replace_fixture(Image.new('RGBA',(32,24),'white'),'PNG',save_all=True,default_image=True,
            append_images=[Image.new('RGBA',(32,24),'red'),Image.new('RGBA',(32,24),'blue')],duration=[80,140],loop=1)
        a=self.frame(item);b=self.frame(item,1)
        self.assertEqual(a['frameCount'],2);self.assertEqual(a['repeatLimit'],1)
        self.assertEqual(self.color(a),(255,0,0));self.assertEqual(self.color(b),(0,0,255))


if __name__=='__main__':unittest.main()
