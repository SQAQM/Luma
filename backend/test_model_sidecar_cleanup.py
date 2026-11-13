"""Recoverable cleanup, using only repository-local synthetic sidecars."""
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from backend.store import Store
from backend.model_library import ModelLibrary
from backend.model_sidecars import _hash
from backend import model_sidecar_cleanup as cleanup


class Manager:
    stop = False
    def cancelled(self, job): return self.stop
    def progress(self, job, **values): self.values = values


class ModelSidecarCleanupTests(unittest.TestCase):
    def setUp(self):
        build = Path(__file__).resolve().parents[1] / 'build'
        build.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='test-info-cleanup-', dir=build)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.models = self.base / 'models'; self.models.mkdir()
        self.store = Store(self.base / 'Data')
        self.library = ModelLibrary(self.store)
        self.root = self.library.add_root(self.models)['id']
        self.manager = Manager()

    def register(self, name='fixture.safetensors', root=None):
        path = self.models / name
        path.write_bytes(b'INERT FIXTURE; NEVER READ')
        with self.store.connect() as db:
            cur = db.execute('INSERT INTO luma_model_files(root_id,path,name,normalized_name,size,mtime_ns,ctime_ns,device,inode,change_ns) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (root or self.root, str(path), name, name, path.stat().st_size, path.stat().st_mtime_ns,path.stat().st_ctime_ns,str(path.stat().st_dev),str(path.stat().st_ino),0))
            db.execute('INSERT INTO luma_model_memberships(root_id,file_id) VALUES(?,?)', (root or self.root, cur.lastrowid))
        return path

    def sidecar(self, model, *, rg=False, value=None):
        value = value or {'description':'fixture', 'raw':{'type':'LORA'}}
        def leaves(data, prefix=''):
            result = {}
            for key, item in data.items():
                if isinstance(item, dict): result.update(leaves(item, prefix+key+'/'))
                else: result[prefix+key] = _hash(item)
            return result
        content = {**value, '_luma':{'schema':1, 'file':model.name, 'managed':leaves(value)}}
        path = model.with_name(model.name+'.rgthree-info.json' if rg else model.stem+'.json')
        path.write_text(json.dumps(content), encoding='utf-8')
        return path

    def execute(self, plan):
        cleanup.apply(self.library, self.manager, 'cleanup-fixture', {'plan_id':plan['id'], 'confirmed':True})

    def restore(self, plan):
        cleanup.undo(self.library, self.manager, 'restore-fixture', {'plan_id':plan['id'], 'confirmed':True})

    @contextmanager
    def failed_state_commit(self, plan, state):
        connect = sqlite3.connect; failures = []
        class CommitFailure(sqlite3.Connection):
            def commit(connection):
                if connection.in_transaction and not failures and connection.execute(
                        'SELECT 1 FROM items WHERE plan_id=? AND status=?',(plan['id'],state)).fetchone():
                    failures.append(state)
                    raise sqlite3.OperationalError('database is locked')
                return super().commit()
        def open_database(path,*args,**kwargs):
            if Path(path).name=='plans.sqlite': kwargs['factory'] = CommitFailure
            return connect(path,*args,**kwargs)
        with patch.object(cleanup.sqlite3,'connect',open_database): yield failures

    def test_only_fully_owned_unchanged_compatibility_metadata_is_reviewed(self):
        good = self.register(); one = self.sidecar(good); two = self.sidecar(good, rg=True)
        preserved = [good.with_suffix('.civitai.info'), good.with_suffix('.png')]
        for path in preserved: path.write_bytes(b'untouched')
        for suffix, change in [('extra', lambda v:v.update(notes='mine')), ('edited',lambda v:v.update(description='mine')),
                ('deleted',lambda v:v.pop('description')), ('marker',lambda v:v.pop('_luma')),
                ('owner',lambda v:v['_luma'].update(file='other.safetensors'))]:
            path = self.sidecar(self.register(suffix+'.safetensors'))
            content = json.loads(path.read_text()); change(content); path.write_text(json.dumps(content))
        self.sidecar(self.register('invalid.safetensors')).write_bytes(b'{malformed')
        original_open = Path.open
        def no_weights(path, *args, **kwargs):
            if path.suffix == '.safetensors': raise AssertionError('weight was read')
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', no_weights): plan = cleanup.preview(self.library)
        self.assertEqual(plan['count'], 2)
        self.assertEqual({item['name'] for item in plan['items']}, {one.name, two.name})
        self.assertEqual(plan['bytes'], one.stat().st_size+two.stat().st_size)
        self.assertTrue(one.exists() and two.exists(), 'preview must not move anything')
        self.assertTrue((self.store.directory/'ModelInfoRecovery'/'plans.sqlite').exists())

    def test_paginated_inventory_and_bounded_preview_include_beyond_first_page(self):
        for i in range(135): self.sidecar(self.register(f'fixture-{i}.safetensors'))
        plan = cleanup.preview(self.library, self.root)
        self.assertEqual((plan['count'], plan['total'], len(plan['items'])), (135,135,100))
        self.assertTrue(plan['items_truncated'])
        with self.assertRaises(ValueError): cleanup.preview(self.library, 999999)

    def test_hardlinks_and_duplicate_json_keys_are_skipped(self):
        path = self.sidecar(self.register())
        os.link(path, self.models/'other.json')
        other = self.sidecar(self.register('bad.safetensors'))
        other.write_text(other.read_text().replace('"description": "fixture"','"description": "mine", "description": "fixture"'))
        self.assertEqual(cleanup.preview(self.library)['count'], 0)

    def test_explicit_confirmation_is_required(self):
        path = self.sidecar(self.register()); plan = cleanup.preview(self.library)
        with self.assertRaises(ValueError): cleanup.apply(self.library, self.manager, 'job', {'plan_id':plan['id']})
        self.assertTrue(path.exists())

    @unittest.skipUnless(os.name=='nt', 'Windows safe handle rename')
    def test_apply_then_restart_undo_does_not_overwrite_arriving_file(self):
        model = self.register(); path = self.sidecar(model); raw = path.read_bytes()
        keep = model.with_suffix('.civitai.info'); keep.write_bytes(b'canonical')
        cover = model.with_suffix('.png'); cover.write_bytes(b'cover')
        plan = cleanup.preview(self.library); self.execute(plan)
        self.assertFalse(path.exists()); self.assertEqual(keep.read_bytes(),b'canonical'); self.assertEqual(cover.read_bytes(),b'cover')
        self.library = ModelLibrary(self.store)
        self.assertEqual(cleanup.preview(self.library)['last_plan']['id'],plan['id'])
        path.write_bytes(b'user arrived'); self.restore(plan)
        self.assertEqual(path.read_bytes(),b'user arrived')
        path.unlink(); self.restore(plan)
        self.assertEqual(path.read_bytes(),raw)
        self.assertEqual(cleanup.preview(self.library)['last_plan'],None)

    @unittest.skipUnless(os.name=='nt', 'Windows safe handle rename')
    def test_changed_file_after_preview_is_preserved(self):
        path = self.sidecar(self.register()); plan = cleanup.preview(self.library)
        path.write_bytes(b'user changed'); self.execute(plan)
        self.assertEqual(path.read_bytes(),b'user changed'); self.assertEqual(self.manager.values['errors'],1)

    @unittest.skipUnless(os.name=='nt', 'Windows safe handle rename')
    def test_interruption_after_rename_can_be_restored_from_prepared_journal(self):
        path = self.sidecar(self.register()); raw = path.read_bytes(); plan = cleanup.preview(self.library)
        update = cleanup._state
        def interrupted(db, item_id, state, **kwargs):
            if state=='moved': raise OSError('synthetic interruption after rename')
            return update(db,item_id,state,**kwargs)
        with patch.object(cleanup,'_state',interrupted): self.execute(plan)
        self.assertFalse(path.exists())
        self.restore(plan); self.assertEqual(path.read_bytes(),raw)

    @unittest.skipUnless(os.name=='nt', 'Windows safe handle rename')
    def test_cross_volume_refusal_keeps_source(self):
        path = self.sidecar(self.register()); raw = path.read_bytes(); plan = cleanup.preview(self.library)
        with patch.object(cleanup.VerifiedFile,'rename',side_effect=ValueError('隔离和恢复仅允许同卷操作')): self.execute(plan)
        self.assertEqual(path.read_bytes(),raw); self.assertEqual(self.manager.values['errors'],1)

    @unittest.skipUnless(os.name=='nt', 'Windows safe handle rename')
    def test_interruption_after_restore_rename_can_be_reconciled(self):
        path = self.sidecar(self.register()); raw = path.read_bytes(); plan = cleanup.preview(self.library)
        self.execute(plan); update = cleanup._state
        def interrupted(db, item_id, state, **kwargs):
            if state=='restored' and kwargs.get('file') is not None: raise OSError('synthetic interruption after restore rename')
            return update(db,item_id,state,**kwargs)
        with patch.object(cleanup,'_state',interrupted): self.restore(plan)
        self.assertEqual(path.read_bytes(),raw)
        self.restore(plan)
        self.assertEqual(cleanup.preview(self.library)['last_plan'],None)

    @unittest.skipUnless(os.name=='nt', 'Windows safe handle rename')
    def test_failed_moved_commit_keeps_persisted_recovery_visible_and_undoable(self):
        path = self.sidecar(self.register()); raw = path.read_bytes(); plan = cleanup.preview(self.library)
        with self.failed_state_commit(plan,'moved') as failures: self.execute(plan)
        self.assertEqual(failures,['moved']); self.assertFalse(path.exists())
        previous = cleanup.preview(self.library)['last_plan']
        self.assertIsNotNone(previous,'a moved file must remain reachable after its state commit fails')
        self.assertEqual(previous['id'],plan['id'])
        self.restore(plan); self.assertEqual(path.read_bytes(),raw)

    @unittest.skipUnless(os.name=='nt', 'Windows safe handle rename')
    def test_failed_restored_commit_keeps_intent_visible_until_reconciled(self):
        path = self.sidecar(self.register()); raw = path.read_bytes(); plan = cleanup.preview(self.library)
        self.execute(plan)
        with self.failed_state_commit(plan,'restored') as failures: self.restore(plan)
        self.assertEqual(failures,['restored']); self.assertEqual(path.read_bytes(),raw)
        previous = cleanup.preview(self.library)['last_plan']
        self.assertIsNotNone(previous,'a failed restore commit must preserve its durable recovery intent')
        self.restore(plan)
        self.assertEqual(cleanup.preview(self.library)['last_plan'],None)


if __name__=='__main__': unittest.main()
