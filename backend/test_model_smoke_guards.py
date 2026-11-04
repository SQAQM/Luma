"""The frozen smoke may inspect bundled code, never original data files."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.model_library_smoke import _runtime_read_kind


class FrozenGuardTests(unittest.TestCase):
    def test_only_loader_owned_code_reads_are_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary); bundle=base/'app'/'_internal'; exe=base/'app'/'Luma.exe'
            source={"__name__":"pyimod02_importers","check":_runtime_read_kind}
            archive={"__name__":"pyimod01_archive","check":_runtime_read_kind}
            exec("def get_source(path,mode='rb'):\n return check(path,mode)",source)
            exec("def extract(path,mode='rb'):\n return check(path,mode)",archive)
            with patch.object(sys,'frozen',True,create=True),patch.object(sys,'_MEIPASS',str(bundle),create=True),patch.object(sys,'executable',str(exe)):
                expected=bundle/'desktop'/'service.py'
                self.assertEqual(source['get_source'](expected),'runtime_source')
                self.assertEqual(archive['extract'](exe),'runtime_archive')
                self.assertEqual(_runtime_read_kind(expected,'rb'),'')
                for bad in (base/'original.py',bundle/'private.png',bundle/'weights.ckpt',bundle/'other'/'secret.py',base/'app'/'_internal-other'/'backend'/'private.py'):
                    self.assertEqual(source['get_source'](bad),'')
                self.assertEqual(source['get_source'](expected,'wb'),'')
                self.assertEqual(archive['extract'](base/'original.png'),'')
                self.assertEqual(archive['extract'](exe,'a'),'')
