import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from worker.protocol import encode, read_frame
from worker.ownership import EngineStartError, Ownership, prepare_preview

ROOT = Path(__file__).resolve().parents[1]


def request(method, payload=None, rid='r1', generation=0, identity=''):
    return dict(version=1, requestId=rid, generation=generation, identity=identity, method=method, payload=payload or {})


class EngineTests(unittest.TestCase):
    def test_database_hardlink_rejected_before_any_open(self):
        original=Path(self.temp.name)/'original.db'
        db=sqlite3.connect(original);db.execute('CREATE TABLE synthetic(value TEXT)');db.commit();db.close()
        before=original.read_bytes()
        os.link(original,self.data/'library.sqlite3')
        with Ownership(self.data) as owner:
            with self.assertRaises(EngineStartError):owner.preflight()
        self.assertEqual(original.read_bytes(),before)
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='luma-stage3-')
        self.data = Path(self.temp.name) / 'catalog'
        prepare_preview(self.data)
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.stdin.close()
                child.wait(timeout=10)
            if not child.stdin.closed: child.stdin.close()
            child.stdout.close(); child.stderr.close()
        self.temp.cleanup()

    def start(self):
        child = subprocess.Popen([sys.executable, '-m', 'worker.main', '--data-dir', str(self.data)], cwd=ROOT,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.children.append(child)
        return child

    def send(self, child, value):
        child.stdin.write(encode(value)); child.stdin.flush()

    def hello(self, child):
        self.send(child, request('engine.hello', {'protocol': 1}))
        result = read_frame(child.stdout)
        self.assertEqual(result['kind'], 'result')
        self.assertFalse(result['payload']['writesEnabled'])
        return result

    def test_marker_required_before_catalog(self):
        other = Path(self.temp.name) / 'unmarked'; other.mkdir()
        with self.assertRaises(EngineStartError): Ownership(other)
        self.assertFalse((other/'library.sqlite3').exists())

    def test_old_launcher_mutex_both_directions(self):
        from launcher import InstanceLock
        old = InstanceLock(self.data); self.assertTrue(old.acquire())
        try:
            with self.assertRaises(EngineStartError): Ownership(self.data)
            self.assertFalse((self.data/'library.sqlite3').exists())
        finally: old.close()
        with Ownership(self.data):
            old = InstanceLock(self.data)
            try: self.assertFalse(old.acquire())
            finally: old.close()

    def test_recovery_visible_without_automatic_file_mutation(self):
        from backend.store import Store
        store = Store(self.data)
        with store.connect() as db:
            db.execute("INSERT INTO jobs(id,kind,status,created_at,updated_at) VALUES('test','organize','interrupted',0,0)")
            db.execute("INSERT INTO journal(id,job_id,image_id,source,destination,temp_path,size,mtime_ns,state,created_at,updated_at) VALUES(1,'test',1,'synthetic-source','synthetic-destination','synthetic-temp',4,0,'prepared',0,0)")
        child = self.start(); self.hello(child)
        self.send(child, request('manage.read',{'section':'files.state','args':{}}))
        reply=read_frame(child.stdout);self.assertEqual(reply['kind'],'result');self.assertGreater(reply['payload']['recovery']['moves'],0)
        child.stdin.close();self.assertEqual(child.wait(timeout=10),0)
        with store.connect() as db: self.assertEqual(db.execute('SELECT state FROM journal WHERE id=1').fetchone()[0], 'prepared')

    def test_handshake_read_allowlist_and_caps(self):
        child = self.start(); self.hello(child)
        for method, payload, expected in [('catalog.query', {'limit': 120}, 'result'), ('catalog.query', {'limit': 121}, 'error'), ('catalog.neighbors', {'imageId': 1, 'before': 5}, 'error'), ('organize.execute', {}, 'error'), ('model.sync', {}, 'error')]:
            self.send(child, request(method, payload)); reply = read_frame(child.stdout); self.assertEqual(reply['kind'], expected)
        child.stdin.close(); self.assertEqual(child.wait(timeout=10), 0)

    def test_invalid_catalog_rejected_and_released(self):
        database = self.data/'library.sqlite3'; database.write_bytes(b'synthetic-invalid-database')
        child = self.start(); self.assertEqual(child.wait(timeout=10), 74)
        self.assertEqual(child.stderr.read().strip(), b'catalog_unavailable')
        self.assertEqual(database.read_bytes(), b'synthetic-invalid-database')
        with Ownership(self.data): pass

    def test_unicode_detail_neighbors_and_controlled_thumbnail(self):
        from backend.store import Store
        from PIL import Image
        image = Path(self.temp.name)/'synthetic.png'; Image.new('RGB',(16,16),(30,60,90)).save(image)
        before = image.read_bytes(); store=Store(self.data); root=store.add_root(image.parent)
        image_id=store.upsert(image,root,image.stat(),{'width':16,'height':16,'format':'PNG','family':'Unknown','model':'fixture','prompt':'雪😀'*3000,'negative_prompt':'合成负面','tags':['fixture']})
        child=self.start();self.hello(child)
        self.send(child,request('catalog.detail',{'imageId':image_id})); detail=read_frame(child.stdout)
        self.assertEqual(detail['payload']['prompt'],'雪😀'*3000)
        self.send(child,request('catalog.neighbors',{'imageId':image_id}));self.assertLessEqual(len(read_frame(child.stdout)['payload']['items']),9)
        self.send(child,request('catalog.thumbnail',{'imageId':image_id}));asset=read_frame(child.stdout)['payload']
        self.assertTrue(asset['relativePath'].startswith('thumbnails/'));self.assertTrue((self.data/asset['relativePath']).exists());self.assertEqual(image.read_bytes(),before)

    def test_protocol_version_mismatch_is_fixed_error(self):
        child=self.start();self.send(child,{**request('engine.hello',{'protocol':1}),'version':2});child.stdin.close()
        self.assertEqual(child.wait(timeout=10),65);self.assertEqual(child.stderr.read().strip(),b'protocol_mismatch')

    def test_hello_requires_integer_protocol(self):
        child=self.start();self.send(child,request('engine.hello',{'protocol':True}));child.stdin.close()
        self.assertEqual(child.wait(timeout=10),65);self.assertEqual(child.stderr.read().strip(),b'protocol_mismatch')

    def test_out_of_order_and_cancel(self):
        child = self.start(); self.hello(child)
        self.send(child, request('diagnostics.wait', {'milliseconds': 600}, 'slow', 1, 'old'))
        self.send(child, request('diagnostics.wait', {'milliseconds': 10}, 'fast', 2, 'new'))
        self.assertEqual(read_frame(child.stdout)['requestId'], 'fast')
        self.send(child, request('request.cancel', {'requestId': 'slow'}, 'cancel'))
        replies = [read_frame(child.stdout), read_frame(child.stdout)]
        slow = next(r for r in replies if r['requestId'] == 'slow')
        self.assertEqual(slow['payload']['code'], 'cancelled'); self.assertEqual(slow['identity'], 'old')

    def test_eof_waits_checkpoint_and_releases_lock(self):
        child = self.start(); self.hello(child)
        self.send(child, request('diagnostics.wait', {'milliseconds': 800, 'safeMilliseconds': 250}, 'safe'))
        time.sleep(.08)
        start = time.monotonic(); child.stdin.close(); child.wait(timeout=10)
        self.assertGreater(time.monotonic()-start, .10)
        with Ownership(self.data): pass

    def test_malformed_input_only_fixed_diagnostic(self):
        child = self.start(); child.stdin.write(b'\x01\x00\x00\x00\xff'); child.stdin.flush(); child.stdin.close()
        self.assertEqual(child.wait(timeout=10), 65)
        self.assertEqual(child.stderr.read().strip(), b'protocol_error')

    def test_stdout_disconnect_alone_releases_ownership(self):
        child=self.start();self.hello(child);child.stdout.close()
        self.send(child,request('diagnostics.echo',{'synthetic':True},'broken-output'))
        self.assertEqual(child.wait(timeout=2),0)
        self.assertFalse(child.stdin.closed)
        with Ownership(self.data): pass


if __name__ == '__main__': unittest.main()
