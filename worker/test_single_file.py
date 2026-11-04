import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import threading
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from worker.ownership import prepare_preview, Ownership, EngineStartError
from worker.single_file import prepare_single
from backend.store import Store
from worker.dispatch import dispatch, DispatchError

class SingleFileTests(unittest.TestCase):
    def test_single_view_rejects_all_management_writes_before_receipt_or_action(self):
        service=SimpleNamespace(single_view=True)
        with patch('worker.management.write',side_effect=AssertionError('write reached')):
            for action in ('settings.save','roots.add','metadata.refresh','labels.apply','organize.execute','duplicates.scan'):
                with self.subTest(action=action),self.assertRaises(DispatchError) as error:
                    dispatch(service,'manage.write',{'action':action,'args':{},'operationId':'0'*32},threading.Event())
                self.assertEqual(error.exception.code,'method_not_allowed')

    def test_invalid_selection_preserves_previous_single_slot(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);data=root/'private-viewer';prepare_preview(data)
            image=root/'valid.png';Image.new('RGB',(16,16)).save(image);prepare_single(data,image)
            before=(data/'library.sqlite3').read_bytes()
            invalid=root/'bad.png';invalid.write_bytes(b'not an image')
            with self.assertRaises((ValueError,EngineStartError)):prepare_single(data,invalid)
            self.assertEqual((data/'library.sqlite3').read_bytes(),before)

    def test_embedded_only_bounded_slot_and_originals_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);data=root/'private-viewer';prepare_preview(data)
            images=[]
            for name in ('one','two'):
                path=root/(name+'.png');meta=PngInfo();meta.add_text('parameters','synthetic portrait\nNegative prompt: synthetic blur\nSteps: 20, Model: synthetic-model')
                Image.new('RGB',(48,32),(10,80,130)).save(path,pnginfo=meta);images.append(path)
                path.with_suffix('.txt').write_text('sidecar must never be opened')
            before=[hashlib.sha256(p.read_bytes()).hexdigest() for p in images]
            original_stat=Path.stat;original_open=Path.open
            def guarded_stat(path,*args,**kwargs):
                if path.parent==root and path.suffix in ('.txt','.json'):raise AssertionError('sidecar stat')
                return original_stat(path,*args,**kwargs)
            def guarded_open(path,*args,**kwargs):
                if path.parent==root and path.suffix in ('.txt','.json'):raise AssertionError('sidecar open')
                return original_open(path,*args,**kwargs)
            with patch.object(Path,'stat',guarded_stat),patch.object(Path,'open',guarded_open):
                for image in images:prepare_single(data,image)
            store=Store(data)
            with store.connect() as db:
                rows=db.execute('SELECT path,prompt,negative_prompt,companion_signature FROM images').fetchall()
                self.assertEqual(len(rows),1);self.assertEqual(rows[0]['path'],str(images[1]));self.assertEqual(rows[0]['negative_prompt'],'synthetic blur');self.assertEqual(json.loads(rows[0]['companion_signature']),{})
                self.assertEqual(db.execute('SELECT COUNT(*) FROM roots').fetchone()[0],0)
            self.assertEqual([hashlib.sha256(p.read_bytes()).hexdigest() for p in images],before)

    def test_existing_non_single_library_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);data=root/'main';prepare_preview(data);store=Store(data);store.update_settings({'theme':'dark'})
            path=root/'one.png';Image.new('RGB',(8,8)).save(path)
            with self.assertRaises((ValueError,EngineStartError)):prepare_single(data,path)
            self.assertEqual(Store(data).settings()['theme'],'dark')
