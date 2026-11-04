"""Only synthetic directories; never open the default library."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.store import default_data_dir
from desktop.service import LibraryService, ServiceError


class LocalStorageTests(unittest.TestCase):
    def test_source_launcher_uses_same_default(self):
        import launcher
        with patch.dict(os.environ, {'LOCALAPPDATA':'C:/Synthetic/AppData'}, clear=True), patch('sys.frozen', True, create=True), patch('sys.executable', 'E:/Synthetic/Luma.exe'):
            self.assertEqual(launcher.data_directory(),Path('E:/Synthetic/Data/Qt'))
    def test_default_is_application_local(self):
        with patch.dict(os.environ, {}, clear=True), patch('sys.frozen', True, create=True), patch('sys.executable', str(Path('E:/Synthetic/Luma.exe'))):
            self.assertEqual(default_data_dir(), Path('E:/Synthetic/Data/Qt'))

    def test_cache_quota_persists_and_rejects_invalid(self):
        with tempfile.TemporaryDirectory() as folder:
            service=LibraryService(folder,automatic_recovery=False)
            try:
                self.assertEqual(service.settings()['thumbnail_cache_limit_bytes'],512*1024**2)
                result=service.put_settings({'thumbnailCacheMiB':128})
                self.assertEqual(result['thumbnail_cache_limit_bytes'],128*1024**2)
                for value in (True,63,8193,128.5,'128',None):
                    with self.subTest(value=value), self.assertRaises(ServiceError):
                        service.put_settings({'thumbnailCacheMiB':value})
                self.assertEqual(service.settings()['thumbnailCacheMiB'],128)
            finally:service.close()
            with LibraryService(folder,automatic_recovery=False) as service:
                self.assertEqual(service.settings()['thumbnail_cache_limit_bytes'],128*1024**2)

    def test_migrated_cache_record_rebinds_and_accounts_new_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            with LibraryService(folder,automatic_recovery=False) as service:
                key='a'*64
                cache=Path(folder)/'thumbnails'/'aa'/(key+'.jpg');cache.parent.mkdir(parents=True)
                cache.write_bytes(b'new cache bytes')
                with service.store.connect() as db:
                    db.execute('INSERT INTO thumbnail_cache(key,path,bytes,accessed_at) VALUES(?,?,?,?)',(key,'C:/old-synthetic/thumbnails/old.jpg',300,0))
                service._remember_thumbnail(key,cache)
                with service.store.connect() as db:
                    row=db.execute('SELECT path,bytes FROM thumbnail_cache WHERE key=?',(key,)).fetchone()
                    self.assertEqual(row['path'],str(cache))
                    self.assertEqual(row['bytes'],len(b'new cache bytes'))
                    self.assertEqual(db.execute('SELECT bytes FROM cache_metrics WHERE id=1').fetchone()[0],len(b'new cache bytes'))


if __name__=='__main__':unittest.main()
