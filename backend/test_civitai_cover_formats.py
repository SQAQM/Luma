"""Synthetic media only: cover conversion, metadata removal and URL boundaries."""
import io
import unittest

from PIL import Image, PngImagePlugin

from . import civitai
from .test_civitai import COVER, Reply, Transport, document, version, SHA


class CoverFormatTests(unittest.TestCase):
    def test_download_returns_small_metadata_free_png_for_static_formats(self):
        for format in ('PNG', 'JPEG', 'WEBP', 'AVIF'):
            with self.subTest(format=format):
                source = Image.new('RGB', (1280, 960), (23, 45, 67))
                exif = Image.Exif(); exif[270] = 'synthetic-private-comment'
                options = {'exif': exif}
                if format == 'PNG':
                    info = PngImagePlugin.PngInfo()
                    info.add_text('Comment', 'synthetic-private-comment')
                    options['pnginfo'] = info
                data = io.BytesIO(); source.save(data, format, **options)
                client = civitai.CivitaiClient(
                    lambda: self.fail('CDN covers must not read credentials'),
                    Transport(Reply(data.getvalue())))
                result = client.download_cover(COVER)
                self.assertTrue(result.startswith(b'\x89PNG\r\n\x1a\n'))
                with Image.open(io.BytesIO(result)) as output:
                    output.load()
                    self.assertEqual((640, 480), output.size)
                    self.assertEqual({}, output.info)
                    self.assertEqual(0, len(output.getexif()))
                    self.assertTrue(all(abs(a - b) <= 4 for a, b in zip(output.getpixel((10, 10)), (23, 45, 67))))
                self.assertNotIn(b'synthetic-private-comment', result)

    def test_cached_cover_normalization_preserves_transparency_and_limits(self):
        data = io.BytesIO(); Image.new('RGBA', (32, 24), (23, 45, 67, 89)).save(data, 'PNG')
        with Image.open(io.BytesIO(civitai.normalize_cover(data.getvalue()))) as output:
            self.assertEqual((32, 24), output.size)
            self.assertEqual((23, 45, 67, 89), output.getpixel((0, 0)))
        oversized = io.BytesIO(); Image.new('L', (4001, 4000)).save(oversized, 'PNG')
        for blob, code in ((oversized.getvalue(), 'invalid_cover'),
                           (b'x' * (8 * 1024 * 1024 + 1), 'response_too_large'),
                           (b'\x00\x00\x00\x18ftypmp42synthetic-video', 'invalid_cover')):
            with self.subTest(code=code, size=len(blob)):
                with self.assertRaises(civitai.CivitaiError) as caught:
                    civitai.normalize_cover(blob)
                self.assertEqual(code, caught.exception.code)
        for format in ('PNG', 'WEBP', 'AVIF'):
            with self.subTest(animated=format):
                data = io.BytesIO()
                Image.new('RGB', (16, 12), 'red').save(data, format, save_all=True,
                    append_images=[Image.new('RGB', (16, 12), 'blue')], duration=100, loop=0)
                with self.assertRaises(civitai.CivitaiError) as caught:
                    civitai.normalize_cover(data.getvalue())
                self.assertEqual('invalid_cover', caught.exception.code)

    def test_header_only_png_is_rejected_instead_of_saved_as_a_cover(self):
        data = io.BytesIO(); Image.new('RGB', (32, 24), 'blue').save(data, 'PNG')
        truncated = data.getvalue()[:45]
        client = civitai.CivitaiClient(transport=Transport(Reply(truncated)))
        with self.assertRaises(civitai.CivitaiError) as caught:
            client.download_cover(COVER)
        self.assertEqual('invalid_cover', caught.exception.code)

    def test_preview_normalizes_filename_and_media_labels_without_relaxing_origins(self):
        value = version()
        value['images'] = [
            {'url': 'https://image.civitai.com/route/key/width=450/合成 cover.jpeg', 'type': 'Image', 'nsfwLevel': 1},
            {'url': COVER, 'type': 'Video', 'nsfwLevel': 1},
            {'url': COVER, 'type': 'Audio', 'nsfwLevel': 1},
            {'url': COVER, 'type': 'VIDEO', 'thumbnail': 'https://image.civitai.com/route/key/width=450/still.jpeg', 'nsfwLevel': 1},
            {'url': 'https://private.example/cover.png', 'type': 'image'},
        ]
        record = civitai.CivitaiClient(transport=Transport(document(value))).version_by_hash(SHA)
        self.assertEqual([
            {'url': 'https://image.civitai.com/route/key/width=450/%E5%90%88%E6%88%90%20cover.jpeg', 'type': 'image', 'nsfw': False},
            {'url': COVER, 'type': 'video', 'nsfw': False},
            {'url': 'https://image.civitai.com/route/key/width=450/still.jpeg', 'type': 'image', 'nsfw': False},
        ], record['images'])
        self.assertTrue(record['cover_candidates_invalid'])

    def test_only_video_or_audio_is_absence_but_rejected_static_url_is_invalid(self):
        for images, invalid in (([], False), ([{'url': COVER, 'type': 'video'}], False),
                                ([{'url': COVER, 'type': 'audio'}], False),
                                ([{'url': COVER + '?key=synthetic', 'type': 'image'}], True)):
            with self.subTest(images=images):
                value = version(); value['images'] = images
                record = civitai.CivitaiClient(transport=Transport(document(value))).version_by_hash(SHA)
                self.assertEqual(invalid, record.get('cover_candidates_invalid'))


if __name__ == '__main__':
    unittest.main()
