"""Real client -> library -> cache with synthetic HTTP and inert local bytes."""
import hashlib
import io
import tempfile
import threading
import time
import unittest
import urllib.error
import uuid
from pathlib import Path

from PIL import Image

from backend.civitai import CivitaiClient
from backend.model_library import ModelLibrary
from backend.store import Store
from backend.test_civitai import Reply, Transport, document


class Progress:
    def __init__(self): self.events = []
    def cancelled(self, job): return False
    def progress(self, job, **values): self.events.append(values)


class CivitaiSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.models = self.base / 'models'; self.models.mkdir()
        self.weight = self.models / 'local-name.safetensors'
        self.weight.write_bytes(b'inert synthetic weight, never deserialize')
        self.digest = hashlib.sha256(self.weight.read_bytes()).hexdigest()
        self.store = Store(self.base / 'index')
        self.progress = Progress()
        self.library = ModelLibrary(self.store)
        root = self.library.add_root(self.models)
        self.library.scan(self.progress, 'scan', {'root_id': root['id']})
        self.ident = self.library.list_models()['items'][0]['id']
        buffer = io.BytesIO(); Image.new('RGB', (8, 6), (31, 69, 101)).save(buffer, 'PNG')
        self.cover = buffer.getvalue()

    def run_sync(self, images, *cover_replies):
        value = {'id': 31, 'modelId': 17, 'name': 'v1', 'baseModel': 'SDXL 1.0',
                 'model': {'type': 'Checkpoint'}, 'description': '<p>Version notes</p>',
                 'trainedWords': [], 'files': [{'type': 'Model', 'hashes': {'SHA256': self.digest}}],
                 'images': images}
        profile = {'id': 17, 'name': 'Published model', 'type': 'Checkpoint',
                   'description': '<p>Actual introduction</p>', 'creator': {'username': 'Fixture author'}}
        # An empty hash response is checked once against the exact version API.
        fallback = [document(value)] if not any(i.get('type') != 'video' for i in images) else []
        self.transport = Transport(document(value), document(profile), *fallback, *cover_replies)
        self.library.client = CivitaiClient(transport=self.transport)
        self.library.sync(self.progress, 'sync', {'ids': [self.ident], 'download_covers': True})
        self.assertEqual(self.digest, hashlib.sha256(self.weight.read_bytes()).hexdigest())
        return self.library.detail(self.ident)

    def test_explicit_cover_request_does_not_silently_drop_flagged_public_cover(self):
        detail = self.run_sync([{'url': 'https://image.civitai.com/fixture/cover.png', 'nsfwLevel': 16}], Reply(self.cover))
        path = self.library.cover_path(self.ident)
        self.assertIsNotNone(path, 'Explicit cover request silently skipped all flagged images')
        self.assertEqual(self.cover, path.read_bytes())
        self.assertEqual('Actual introduction', detail['description'])

    def test_broken_first_image_falls_back_to_next_image_without_losing_intro(self):
        first = 'https://image.civitai.com/fixture/missing.png'
        second = 'https://image.civitai.com/fixture/second.png'
        detail = self.run_sync([{'url': first, 'nsfwLevel': 1}, {'url': second, 'nsfwLevel': 1}],
                               urllib.error.HTTPError(first, 404, 'missing', {}, None), Reply(self.cover))
        path = self.library.cover_path(self.ident)
        self.assertIsNotNone(path, 'A broken first image must not prevent trying the next public image')
        self.assertEqual(self.cover, path.read_bytes())
        self.assertEqual('synced', detail['status'])

    def test_video_preview_is_skipped_for_still_cover(self):
        detail = self.run_sync([{'url': 'https://image.civitai.com/fixture/video.mp4', 'type': 'video', 'nsfwLevel': 1},
                               {'url': 'https://image.civitai.com/fixture/image.png', 'type': 'image', 'nsfwLevel': 1}], Reply(self.cover))
        requested = [request.full_url for request, _ in self.transport.requests]
        self.assertNotIn('https://image.civitai.com/fixture/video.mp4', requested)
        self.assertTrue(self.library.cover_path(self.ident))

    def test_model_profile_name_is_used_when_hash_endpoint_omits_it(self):
        detail = self.run_sync([])
        self.assertEqual('Published model', detail['model_name'])
        self.assertEqual('Actual introduction', detail['description'])

    def test_missing_public_images_has_specific_completion_feedback(self):
        self.run_sync([])
        self.assertIn('封面', self.progress.events[-1]['message'])
        self.assertIn('未提供', self.progress.events[-1]['message'])

    def test_worker_exposes_only_allowlisted_sync_feedback_not_private_error_text(self):
        from worker.management import job
        self.run_sync([])
        result = job({'kind': 'model_sync', 'status': 'completed', **self.progress.events[-1]})
        self.assertIn('封面', result.get('feedback', ''))
        private = job({'kind': 'model_sync', 'status': 'failed', 'message': 'PRIVATE_SECRET path-and-body'})
        self.assertNotIn('PRIVATE_SECRET', str(private))
        other = job({'kind': 'import', 'message': 'PRIVATE_SECRET path-and-body'})
        self.assertNotIn('feedback', other)

    def test_service_task_to_worker_detail_feedback_and_cover_descriptor(self):
        from desktop.service import LibraryService
        from worker.management import read, write
        value = {'id': 31, 'modelId': 17, 'name': 'v1', 'baseModel': 'SDXL 1.0',
                 'model': {'type': 'Checkpoint'}, 'files': [{'type': 'Model', 'hashes': {'SHA256': self.digest}}],
                 'images': [{'url': 'https://image.civitai.com/fixture/cover.png', 'nsfwLevel': 16}]}
        profile = {'id': 17, 'name': 'Published model', 'description': '<p>Actual introduction</p>'}
        transport = Transport(document(value), document(profile), Reply(self.cover))
        service = LibraryService(self.base / 'service', civitai_client=CivitaiClient(transport=transport), automatic_recovery=False)
        self.addCleanup(service.close)
        event = threading.Event()
        def settled(task):
            until = time.monotonic() + 10
            while time.monotonic() < until:
                result = service.job(task['id'])
                if result['status'] not in ('queued', 'running'): return result
                time.sleep(.01)
            self.fail('Own model task did not complete')
        root = write(service, 'models.add', {'path': str(self.models)}, uuid.uuid4().hex, event)
        self.assertEqual('completed', settled(write(service, 'models.scan', {'rootId': root['id']}, uuid.uuid4().hex, event))['status'])
        ident = read(service, 'models', {}, event)['items'][0]['id']
        task = write(service, 'models.sync', {'ids': [ident], 'downloadCovers': True}, uuid.uuid4().hex, event)
        self.assertEqual('completed', settled(task)['status'])
        detail = read(service, 'modelDetail', {'modelId': ident}, event)
        self.assertEqual('Actual introduction', detail['description'])
        self.assertEqual('Published model', detail['model_name'])
        asset = read(service, 'modelCover', {'modelId': ident}, event)
        self.assertEqual(hashlib.sha256(self.cover).hexdigest(), asset['sha256'])
        self.assertEqual(self.cover, (service.store.directory / asset['relativePath']).read_bytes())
        job = next(item for item in read(service, 'jobs', {}, event) if item['id'] == task['id'])
        self.assertIn('封面已保存 1 个', job['feedback'])
        self.assertEqual(3, len(transport.requests))
