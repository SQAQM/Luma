"""Real SQLite model pagination regressions, using SQL-only synthetic records."""
import json
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path

from backend.model_library import ModelCursorExpired, ModelLibrary
from backend.store import Store


WORK = Path(__file__).resolve().parents[1] / 'build' / 'perf-1.3.4' / 'model-agent'


class TracedStore(Store):
    def __init__(self, directory):
        self.statements = []
        self.on_revision = None
        super().__init__(directory)

    @contextmanager
    def connect(self):
        with super().connect() as db:
            db.set_trace_callback(self.statements.append)
            owner = self

            class Connection:
                def execute(self, sql, *args):
                    result = db.execute(sql, *args)
                    if sql == 'SELECT revision FROM luma_model_query_state WHERE id=1' and owner.on_revision:
                        owner.on_revision()
                    return result

                def __getattr__(self, name):
                    return getattr(db, name)

            yield Connection()

    def aggregate_counts(self):
        return (
            sum(sql.startswith('SELECT DISTINCT ') for sql in self.statements),
            sum(sql.startswith('SELECT count(*) FROM luma_model_files ') for sql in self.statements),
        )


def seed(store, base):
    library = ModelLibrary(store)
    with store.connect() as db:
        db.executemany('INSERT INTO luma_model_roots(id,path,name) VALUES(?,?,?)',
                       [(n, str(base / f'absent-root-{n}'), f'Root {n}') for n in (1, 2, 3)])
        record = {'family': 'FLUX', 'files': [{'sha256': 'a' * 64, 'resource_type': 'VAE'}]}
        db.execute('INSERT INTO luma_model_versions(version_id,record,updated_at) VALUES(4,?,0)', (json.dumps(record),))
        for n, name in enumerate(('Alpha', 'Beta', 'Alpha', 'Gamma'), 1):
            db.execute("INSERT INTO luma_model_files(id,root_id,path,name,normalized_name,size,mtime_ns,ctime_ns,device,inode,change_ns,sha256,version_id) VALUES(?,?,?,?,?,1,1,1,'1','1',1,?,?)",
                       (n, 1 if n in (1, 3) else 2, str(base / f'absent-{n}.ckpt'), name, name.lower(), 'a' * 64 if n == 4 else '', 4 if n == 4 else None))
            db.execute('INSERT INTO luma_model_memberships(file_id,root_id) VALUES(?,?)', (n, 1 if n in (1, 3) else 2))
            if n < 4:
                local = {'type': 'LoRA' if n < 3 else 'Checkpoint', 'family': 'Krea 2' if n == 2 else 'FLUX', 'model_name': name}
                db.execute('INSERT INTO luma_model_local_info(file_id,record) VALUES(?,?)', (n, json.dumps(local)))
    store.statements.clear()
    return library


class ModelSummaryCacheTests(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='summary-test-', dir=WORK)
        self.base = Path(self.temp.name)
        self.store = TracedStore(self.base / 'db')
        self.library = seed(self.store, self.base)

    def tearDown(self):
        self.temp.cleanup()

    def test_repeated_pages_reuse_aggregates_and_mutation_refreshes_them(self):
        first = self.library.list_models(limit=1)
        second = self.library.list_models(cursor=first['next_cursor'], limit=1)
        self.assertEqual([first['items'][0]['id'], second['items'][0]['id']], [1, 2])
        self.assertEqual(second['total'], 4)
        self.assertEqual(self.store.aggregate_counts(), (2, 1), 'unchanged pages must not rescan facets/count')
        with self.store.connect() as db:
            db.execute('UPDATE luma_model_local_info SET record=? WHERE file_id=1',
                       (json.dumps({'type': 'Embedding', 'family': 'Krea 2'}),))
        with self.assertRaises(ModelCursorExpired):
            self.library.list_models(cursor=second['next_cursor'])
        changed = self.library.list_models()
        self.assertEqual(changed['facets'], {'types': ['Checkpoint', 'Embedding', 'LoRA', 'VAE'], 'families': ['FLUX', 'Krea 2']})
        self.assertEqual(self.store.aggregate_counts(), (4, 2))

    def test_canonical_scope_shares_summary_but_cursor_binding_is_unchanged(self):
        self.library.list_models(q=' Alpha ', types=[' LoRA ', 'Checkpoint', 'LoRA'], families=['FLUX'], limit=1)
        page = self.library.list_models(q='Alpha', types=['Checkpoint', 'LoRA'], families=['FLUX'], limit=1)
        self.assertEqual(page['total'], 2)
        self.assertEqual(self.store.aggregate_counts(), (2, 1))
        with self.assertRaises(ValueError):
            self.library.list_models(q=' Alpha ', types=['Checkpoint', 'LoRA'], families=['FLUX'], cursor=page['next_cursor'])

    def test_distinct_root_search_and_dimensions_do_not_share_totals(self):
        cases = [({}, 4), ({'root_id': 1}, 2), ({'q': 'Alpha'}, 2),
                 ({'types': ['LoRA']}, 2), ({'families': ['Krea 2']}, 1),
                 ({'types': ['LoRA'], 'families': ['FLUX']}, 1), ({'root_id': 2, 'q': 'Alpha'}, 0)]
        for scope, total in cases:
            with self.subTest(scope=scope):
                self.assertEqual(self.library.list_models(**scope)['total'], total)
        self.assertEqual(self.store.aggregate_counts(), (14, 7))
        for scope, total in reversed(cases):
            self.assertEqual(self.library.list_models(**scope)['total'], total)
        self.assertEqual(self.store.aggregate_counts(), (14, 7))

    def test_returned_facets_and_items_are_isolated_from_later_callers(self):
        first = self.library.list_models(limit=1)
        first['facets']['types'].clear()
        first['facets']['families'].append('poison')
        first['facets']['extra'] = ['poison']
        first['items'][0]['name'] = 'poison'
        first['total'] = -1
        page = self.library.list_models(limit=1)
        self.assertEqual(page['facets'], {'types': ['Checkpoint', 'LoRA', 'VAE'], 'families': ['FLUX', 'Krea 2']})
        self.assertEqual((page['items'][0]['name'], page['total']), ('Alpha', 4))
        self.assertEqual(self.store.aggregate_counts(), (2, 1))

    def test_image_revision_and_rolled_back_model_mutation_keep_summary(self):
        first = self.library.list_models(limit=1)
        with self.store.connect() as db:
            db.execute('UPDATE query_state SET revision=revision+1 WHERE id=1')
        with self.assertRaisesRegex(RuntimeError, 'rollback'):
            with self.store.connect() as db:
                db.execute('DELETE FROM luma_model_local_info WHERE file_id=1')
                raise RuntimeError('rollback')
        self.assertEqual(self.library.list_models(cursor=first['next_cursor'])['total'], 4)
        self.assertEqual(self.store.aggregate_counts(), (2, 1))

    def test_all_existing_model_revision_triggers_invalidate_summary(self):
        # Each operation starts from the same fixture. Even changes that do not
        # alter visible facets must invalidate through the persisted revision.
        mutations = [
            ("INSERT INTO luma_model_roots(id,path,name) VALUES(9,'synthetic-only','Nine')", 2),
            ("UPDATE luma_model_roots SET name='Renamed' WHERE id=1", 2),
            ('DELETE FROM luma_model_roots WHERE id=3', 2),
            ("INSERT INTO luma_model_files(id,root_id,path,name,normalized_name,size,mtime_ns,ctime_ns,device,inode,change_ns) VALUES(9,1,'synthetic-only','Delta','delta',1,1,1,'1','9',1)", 2),
            ("UPDATE luma_model_files SET name='Renamed' WHERE id=1", 2),
            ('DELETE FROM luma_model_files WHERE id=1', 1),
            ('INSERT INTO luma_model_memberships(file_id,root_id) VALUES(2,1)', 3),
            ('UPDATE luma_model_memberships SET root_id=2 WHERE file_id=1', 1),
            ('DELETE FROM luma_model_memberships WHERE file_id=1', 1),
            ("INSERT INTO luma_model_versions(version_id,record,updated_at) VALUES(9,'{}',0)", 2),
            ("UPDATE luma_model_versions SET record='{}' WHERE version_id=4", 2),
            ('DELETE FROM luma_model_versions WHERE version_id=4', 2),
            ("INSERT INTO luma_model_local_info(file_id,record) VALUES(4,'{}')", 2),
            ("UPDATE luma_model_local_info SET record='{}' WHERE file_id=1", 2),
            ('DELETE FROM luma_model_local_info WHERE file_id=1', 2),
        ]
        for index, (sql, expected_total) in enumerate(mutations):
            with self.subTest(sql=sql):
                store = TracedStore(self.base / f'trigger-{index}')
                library = seed(store, self.base)
                first = library.list_models(root_id=1, limit=1)
                with store.connect() as db:
                    db.execute(sql)
                with self.assertRaises(ModelCursorExpired):
                    library.list_models(root_id=1, cursor=first['next_cursor'])
                page = library.list_models(root_id=1)
                self.assertEqual(page['total'], expected_total)
                library.list_models(root_id=1)
                self.assertEqual(store.aggregate_counts(), (4, 2))

    def test_old_snapshot_cannot_use_or_replace_newer_revision_summary(self):
        for warm in (False, True):
            with self.subTest(warm=warm):
                store = TracedStore(self.base / f'race-{warm}')
                library = seed(store, self.base)
                if warm:
                    library.list_models()
                entered, release = threading.Event(), threading.Event()
                results, errors = [], []

                def pause_old_snapshot():
                    if threading.current_thread().name == 'old-model-reader':
                        entered.set()
                        if not release.wait(10):
                            raise RuntimeError('old reader timed out')

                def old_reader():
                    try:
                        results.append(library.list_models(limit=1))
                    except BaseException as error:
                        errors.append(error)

                store.on_revision = pause_old_snapshot
                thread = threading.Thread(target=old_reader, name='old-model-reader')
                thread.start()
                try:
                    self.assertTrue(entered.wait(10))
                    with store.connect() as db:
                        db.execute('DELETE FROM luma_model_files WHERE id=1')
                    current = library.list_models(limit=1)
                    self.assertEqual((current['total'], current['items'][0]['id']), (3, 2))
                finally:
                    release.set()
                    thread.join(10)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual((results[0]['total'], results[0]['items'][0]['id']), (4, 1))
                before = store.aggregate_counts()
                self.assertEqual(library.list_models()['total'], 3)
                self.assertEqual(store.aggregate_counts(), before, 'old reader must not displace the newer memo')

    def test_many_search_scopes_evict_old_entries_but_recent_scope_stays_warm(self):
        self.library.list_models(q='Alpha')
        for n in range(160):
            self.library.list_models(q=f'absent-{n}')
        before = self.store.aggregate_counts()
        self.assertEqual(self.library.list_models(q='absent-159')['total'], 0)
        self.assertEqual(self.store.aggregate_counts(), before)
        self.assertEqual(self.library.list_models(q='Alpha')['total'], 2)
        self.assertEqual(self.store.aggregate_counts(), (before[0] + 2, before[1] + 1))


if __name__ == '__main__':
    unittest.main()
