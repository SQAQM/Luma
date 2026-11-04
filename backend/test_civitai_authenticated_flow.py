"""Credential-backed service and incomplete API records; no external network."""
import hashlib
import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import uuid
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from backend.test_civitai import Reply, document
from desktop.service import LibraryService
from worker.management import read, write

TOKEN = 'synthetic-authenticated-flow-not-a-real-key'
COVER = 'https://image.civitai.com/fixture/width=640/cover.png'
OTHER_COVER = 'https://image.civitai.com/fixture/width=640/wrong-version.png'


class RouteTransport:
    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def open(self, request, timeout):
        self.requests.append(request)
        value = self.routes[request.full_url]
        if isinstance(value, BaseException):
            raise value
        return Reply(value) if isinstance(value, bytes) else document(value)


class AuthenticatedCivitaiFlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.models = self.base / 'inert-models'; self.models.mkdir()
        self.weight = self.models / 'synthetic.safetensors'
        self.weight.write_bytes(b'inert fixture, never deserialize')
        self.sha = hashlib.sha256(self.weight.read_bytes()).hexdigest()
        self.version = {'id': 31, 'modelId': 17, 'name': 'v1', 'baseModel': 'SDXL 1.0',
                        'model': {'type': 'Checkpoint'}, 'trainedWords': [],
                        'files': [{'type': 'Model', 'hashes': {'SHA256': self.sha}}], 'images': []}
        self.profile = {'id': 17, 'name': 'Published fixture', 'description': '<p>Fixture introduction</p>',
                        'modelVersions': [{'id': 31, 'images': [{'url': COVER, 'nsfwLevel': 1}]}]}
        buffer = io.BytesIO(); Image.new('RGB', (12, 8), (24, 48, 72)).save(buffer, 'PNG')
        self.cover = buffer.getvalue()
        self.routes = {'https://civitai.com/api/v1/me': {'id': 7, 'username': 'must not return account identity'},
                       'https://civitai.com/api/v1/model-versions/by-hash/' + self.sha: self.version,
                       'https://civitai.com/api/v1/model-versions/31': self.version,
                       'https://civitai.com/api/v1/models/17': self.profile, COVER: self.cover}
        self.transport = RouteTransport(self.routes)
        self.event = threading.Event()
        self.opener = patch('urllib.request.build_opener', return_value=self.transport)
        self.opener.start(); self.addCleanup(self.opener.stop)
        self.service = self.open_service()
        self.addCleanup(lambda: self.service.close())

    def open_service(self):
        return LibraryService(self.base / 'isolated-data', automatic_recovery=False)

    def action(self, name, args):
        return write(self.service, name, args, uuid.uuid4().hex, self.event)

    def settled(self, task):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            item = self.service.job(task['id'])
            if item['status'] not in ('queued', 'running'):
                return item
            time.sleep(.01)
        self.fail('Own model operation did not settle')

    def index(self):
        root = self.action('models.add', {'path': str(self.models)})
        self.assertEqual('completed', self.settled(self.action('models.scan', {'rootId': root['id']}))['status'])
        return read(self.service, 'models', {}, self.event)['items'][0]['id']

    def sync(self, ident):
        return self.settled(self.action('models.sync', {'ids': [ident], 'downloadCovers': True}))

    def test_saved_key_survives_restart_authenticates_api_and_exact_profile_version_supplies_cover(self):
        result = self.action('credential.set', {'token': TOKEN, 'remember': True})
        self.assertTrue(result['remembered'])
        self.assertNotIn(TOKEN, json.dumps(result))
        encrypted = self.base / 'isolated-data' / 'credentials' / 'civitai.dpapi'
        self.assertNotIn(TOKEN.encode(), encrypted.read_bytes())
        self.service.close(); self.service = self.open_service()
        checked = self.action('credential.test', {})
        self.assertTrue(checked['ok']); self.assertTrue(checked['authenticated'])
        self.assertNotIn('username', checked)
        ident = self.index()
        self.assertEqual('completed', self.sync(ident)['status'])
        asset = read(self.service, 'modelCover', {'modelId': ident}, self.event)
        self.assertIsNotNone(asset, 'Hash lookup may omit previews: use the exact model-profile version')
        self.assertEqual(self.cover, (self.service.store.directory / asset['relativePath']).read_bytes())
        for request in self.transport.requests:
            self.assertEqual(None if request.full_url == COVER else 'Bearer ' + TOKEN,
                             request.get_header('Authorization'))
        self.assertEqual(self.sha, hashlib.sha256(self.weight.read_bytes()).hexdigest())

    def test_version_id_endpoint_recovers_cover_when_model_profile_has_no_versions(self):
        self.profile['modelVersions'] = []
        self.routes['https://civitai.com/api/v1/model-versions/31'] = {**self.version, 'images': [{'url': COVER}]}
        ident = self.index(); self.sync(ident)
        self.assertTrue(read(self.service, 'modelDetail', {'modelId': ident}, self.event)['cover_available'],
                        'Exact version lookup must recover an omitted hash preview')

    def test_different_profile_version_cannot_supply_a_cover_for_the_matched_model_file(self):
        self.profile['modelVersions'][0]['id'] = 999
        self.profile['modelVersions'][0]['images'][0]['url'] = OTHER_COVER
        ident = self.index(); self.sync(ident)
        self.assertFalse(read(self.service, 'modelDetail', {'modelId': ident}, self.event)['cover_available'])
        self.assertNotIn(OTHER_COVER, [r.full_url for r in self.transport.requests])

    def test_connect_current_draft_saves_and_validates_the_same_key_without_returning_it(self):
        self.action('credential.set', {'token': 'previous-synthetic-key', 'remember': False})
        result = self.action('credential.connect', {'token': TOKEN, 'remember': False})
        self.assertTrue(result['ok']); self.assertTrue(result['authenticated'])
        self.assertEqual('Bearer ' + TOKEN, self.transport.requests[-1].get_header('Authorization'))
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_authentication_failure_is_a_failed_validation_not_a_successful_connection(self):
        self.routes['https://civitai.com/api/v1/me'] = urllib.error.HTTPError('https://civitai.com/api/v1/me', 401, 'private response', {}, None)
        result = self.action('credential.connect', {'token': TOKEN, 'remember': False})
        self.assertFalse(result['ok']); self.assertFalse(result['authenticated'])
        self.assertEqual('unauthorized', result['code'])
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_version_fallback_cannot_replace_identity_and_keeps_valid_introduction_on_failure(self):
        self.profile['modelVersions'] = []
        self.routes['https://civitai.com/api/v1/model-versions/31'] = {**self.version, 'id': 999, 'images': [{'url': COVER}]}
        ident = self.index(); task = self.sync(ident)
        self.assertGreater(task['errors'], 0)
        detail = read(self.service, 'modelDetail', {'modelId': ident}, self.event)
        self.assertEqual(31, detail['version_id'])
        self.assertEqual('Fixture introduction', detail['description'])
        self.assertFalse(detail['cover_available'])
        self.assertNotIn(COVER, [r.full_url for r in self.transport.requests])


if __name__ == '__main__':
    unittest.main()
