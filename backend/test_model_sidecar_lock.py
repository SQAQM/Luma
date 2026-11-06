"""Native sharing and recovery checks using inert, repository-local JSON files."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from backend import model_sidecars
from backend.organizer import Cancelled


class ModelSidecarLockTests(unittest.TestCase):
    def setUp(self):
        build = Path(__file__).resolve().parents[1] / 'build'
        build.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='test-sidecar-lock-', dir=build)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.target = self.directory / 'fixture.json'
        self.original = b'\xef\xbb\xbf{ "notes": "user note", "description": "" }\r\n'
        self.target.write_bytes(self.original)

    def update(self, cancelled=lambda: False):
        model_sidecars._json(self.target, {'baseModel': 'Anima'}, 'fixture.safetensors', cancelled)

    def leftovers(self):
        return sorted(path.name for path in self.directory.glob('.luma-model-*'))

    @unittest.skipUnless(os.name == 'nt', 'Windows mandatory share-mode semantics')
    def test_another_process_cannot_write_or_replace_during_merge(self):
        merge = model_sidecars._merge
        results = []

        def during_merge(*args, **kwargs):
            child = subprocess.run([sys.executable, '-c', '''
import json, os, pathlib, sys
target = pathlib.Path(sys.argv[1]); other = target.with_name('foreign.tmp')
other.write_bytes(b'{"notes":"foreign"}')
results = []
try:
    with target.open('r+b'): pass
    results.append('write_allowed')
except OSError: results.append('write_blocked')
try:
    os.replace(other, target)
    results.append('replace_allowed')
except OSError: results.append('replace_blocked')
finally: other.unlink(missing_ok=True)
print(json.dumps(results))
''', str(self.target)], capture_output=True, text=True, timeout=10, check=True)
            results.extend(json.loads(child.stdout))
            self.assertEqual(results, ['write_blocked', 'replace_blocked'])
            return merge(*args, **kwargs)

        with patch.object(model_sidecars, '_merge', during_merge):
            self.update()
        self.assertEqual(json.loads(self.target.read_text())['notes'], 'user note')
        self.assertEqual(self.leftovers(), [])

    @unittest.skipUnless(os.name == 'nt', 'Windows existing-file update path')
    def test_target_flush_failure_restores_exact_original_bytes(self):
        inode = self.target.stat().st_ino
        fsync = os.fsync
        failed = False

        def fail_once(descriptor):
            nonlocal failed
            if os.fstat(descriptor).st_ino == inode and not failed:
                failed = True
                raise OSError('synthetic target flush failure')
            return fsync(descriptor)

        with patch.object(model_sidecars.os, 'fsync', fail_once):
            with self.assertRaises(OSError):
                self.update()
        self.assertTrue(failed, 'failure must occur on the updated target, not its prepared file')
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.leftovers(), [])

    @unittest.skipUnless(os.name == 'nt', 'Windows existing-file update path')
    def test_failed_restore_keeps_a_complete_original_backup(self):
        inode = self.target.stat().st_ino
        fsync = os.fsync

        def fail_target(descriptor):
            if os.fstat(descriptor).st_ino == inode:
                raise OSError('synthetic target and restore flush failure')
            return fsync(descriptor)

        with patch.object(model_sidecars.os, 'fsync', fail_target):
            with self.assertRaises(model_sidecars.SidecarError):
                self.update()
        backups = list(self.directory.glob('.luma-model-*.bak'))
        self.assertEqual(len(backups), 1, 'the durable original must survive a failed restore')
        self.assertEqual(backups[0].read_bytes(), self.original)
        self.assertEqual(list(self.directory.glob('*.tmp')), [])

    @unittest.skipUnless(os.name == 'nt', 'Windows existing-file update path')
    def test_backup_flush_failure_preserves_target_and_cleans_staging(self):
        fsync = os.fsync

        def fail_backup(descriptor):
            if list(self.directory.glob('.luma-model-*.bak')):
                raise OSError('synthetic backup flush failure')
            return fsync(descriptor)

        with patch.object(model_sidecars.os, 'fsync', fail_backup):
            with self.assertRaises(OSError):
                self.update()
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.leftovers(), [])

    @unittest.skipUnless(os.name == 'nt', 'Windows existing-file update path')
    def test_cancellation_after_backup_preserves_target_and_cleans_staging(self):
        with self.assertRaises(Cancelled):
            self.update(lambda: bool(list(self.directory.glob('.luma-model-*.bak'))))
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.leftovers(), [])

    def test_new_target_race_never_overwrites_the_arriving_file(self):
        self.target.unlink()
        publish = model_sidecars._publish
        arrived = b'{"notes":"created by another process"}'

        def arrival(temporary, target, *args, **kwargs):
            target.write_bytes(arrived)
            return publish(temporary, target, *args, **kwargs)

        with patch.object(model_sidecars, '_publish', arrival):
            with self.assertRaises(FileExistsError):
                self.update()
        self.assertEqual(self.target.read_bytes(), arrived)
        self.assertEqual(self.leftovers(), [])


if __name__ == '__main__':
    unittest.main()
