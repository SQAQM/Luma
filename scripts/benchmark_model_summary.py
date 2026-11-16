"""Bounded synthetic model-page benchmark; no weight files, network, or user Data.

Run before/after against the same catalog:
  .venv-build/Scripts/python.exe -m scripts.benchmark_model_summary --label before
  .venv-build/Scripts/python.exe -m scripts.benchmark_model_summary --label after

Cold means an empty ModelLibrary summary memo, not a cold OS disk cache. Cover
filesystem probes alone are replaced with false/None to isolate SQL and model
record projection. All other pagination, cursors, JSON/UDF, and SQLite work is real.
"""
import argparse
import hashlib
import json
import platform
import statistics
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from backend.model_library import ModelLibrary
from backend.store import Store


WORK = Path(__file__).resolve().parents[1] / 'build' / 'perf-1.3.4' / 'model-agent'


class MeasuredStore(Store):
    def __init__(self, directory):
        self.operations = Counter()
        super().__init__(directory)

    @contextmanager
    def connect(self):
        with super().connect() as db:
            owner = self

            def trace(sql):
                if sql.startswith('SELECT DISTINCT '): owner.operations['facet_sql'] += 1
                elif sql.startswith('SELECT count(*) FROM luma_model_files '): owner.operations['count_sql'] += 1
                elif sql.startswith('SELECT f.id,f.root_id,f.path'): owner.operations['page_sql'] += 1

            db.set_trace_callback(trace)

            class Connection:
                def create_function(self, name, nargs, function, **kwargs):
                    def counted(*args):
                        owner.operations[name + '_udf'] += 1
                        return function(*args)
                    return db.create_function(name, nargs, counted, **kwargs)

                def __getattr__(self, name):
                    return getattr(db, name)

            yield Connection()


def catalog(count):
    directory = WORK / f'catalog-{count}'
    store = MeasuredStore(directory)
    library = ModelLibrary(store)
    manifest = directory / 'synthetic-manifest.json'
    if manifest.exists():
        metadata = json.loads(manifest.read_text(encoding='utf-8'))
        with store.connect() as db:
            actual = db.execute('SELECT count(*) FROM luma_model_files').fetchone()[0]
        if actual != count or metadata['records'] != count:
            raise ValueError('Synthetic catalog count mismatch')
        return store, metadata
    digest = hashlib.sha256()
    with store.connect() as db:
        db.executemany('INSERT INTO luma_model_roots(id,path,name) VALUES(?,?,?)',
                       [(n, str(directory / f'absent-root-{n}'), f'Synthetic {n}') for n in (1, 2)])
        # Only one model record is constructed at a time, then inserted in SQL.
        for n in range(1, count + 1):
            sha = f'{n:064x}'
            role = ('Checkpoint', 'LoRA', 'VAE', 'Embedding')[n % 4]
            family = ('FLUX', 'Krea 2', 'Stable Diffusion')[n % 3]
            record = json.dumps({'model_name': f'Synthetic model {n:06d}', 'family': family,
                                 'model_type': role, 'type': role, 'version_name': 'Synthetic version',
                                 'description': 'Synthetic catalog metadata. ' * 20,
                                 'files': [{'name': f'model-{n}.ckpt', 'sha256': sha, 'resource_type': role}]})
            digest.update(record.encode())
            db.execute('INSERT INTO luma_model_versions(version_id,record,updated_at) VALUES(?,?,0)', (n, record))
            root = 1 if n % 2 else 2
            db.execute("INSERT INTO luma_model_files(id,root_id,path,name,normalized_name,size,mtime_ns,ctime_ns,device,inode,change_ns,sha256,version_id) VALUES(?,?,?,?,?,1,1,1,'1','1',1,?,?)",
                       (n, root, str(directory / f'absent-weight-{n}.ckpt'), f'Synthetic {n:06d}.ckpt', f'synthetic {n:06d}', sha, n))
            db.execute('INSERT INTO luma_model_memberships(file_id,root_id) VALUES(?,?)', (n, root))
    metadata = {'records': count, 'record_sha256': digest.hexdigest(), 'weight_files': 0,
                'created_by': 'scripts.benchmark_model_summary', 'description': 'SQL-only deterministic synthetic records'}
    manifest.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    return store, metadata


def sample(store, library, scope, cursor=None):
    store.operations.clear()
    start = time.perf_counter()
    page = library.list_models(**scope, cursor=cursor, limit=60)
    elapsed = (time.perf_counter() - start) * 1000
    return page, {'ms': elapsed, 'operations': dict(store.operations)}


def summarize(samples):
    return {'median_ms': statistics.median(s['ms'] for s in samples), 'samples': samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', required=True, choices=('before', 'after'))
    parser.add_argument('--records', type=int, default=5000, choices=(5000, 10000))
    parser.add_argument('--repeats', type=int, default=7, choices=range(3, 16))
    options = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    store, metadata = catalog(options.records)
    result = {'label': options.label, 'catalog': metadata, 'python': platform.python_version(),
              'method': {'cold': 'fresh in-memory summary memo; SQLite/OS caches may be warm',
                         'warm': 'next cursor page in unchanged scope/revision', 'page_size': 60,
                         'excluded': 'cover filesystem probes only; mocked available=False and _cover_for_hash=None',
                         'network': False, 'user_data': False, 'repeats': options.repeats}, 'scopes': {}}
    scopes = {'all': {}, 'combined': {'root_id': 1, 'q': 'Synthetic', 'types': ['Checkpoint', 'LoRA'], 'families': ['FLUX', 'Krea 2']}}
    with patch('backend.model_library.model_covers.available', return_value=False), patch.object(ModelLibrary, '_cover_for_hash', return_value=None):
        for name, scope in scopes.items():
            cold, warm = [], []
            for _ in range(options.repeats):
                library = ModelLibrary(store)
                first, measurement = sample(store, library, scope)
                cold.append(measurement)
                if not first['next_cursor']: raise ValueError('Expected multiple pages')
                second, measurement = sample(store, library, scope, first['next_cursor'])
                if first['total'] != second['total'] or first['items'][-1]['id'] >= second['items'][0]['id']:
                    raise ValueError('Pagination mismatch')
                warm.append(measurement)
            result['scopes'][name] = {'scope': scope, 'total': first['total'], 'cold': summarize(cold), 'warm': summarize(warm)}
    result['catalog_database_bytes'] = store.database.stat().st_size
    output = WORK / f'model-summary-{options.label}.json'
    output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'evidence': str(output), 'catalog_sha256': metadata['record_sha256'],
                      'scopes': {k: {'total': v['total'], 'cold_median_ms': v['cold']['median_ms'],
                                     'warm_median_ms': v['warm']['median_ms'],
                                     'cold_ops': v['cold']['samples'][0]['operations'],
                                     'warm_ops': v['warm']['samples'][0]['operations']} for k, v in result['scopes'].items()}}, indent=2))


if __name__ == '__main__':
    main()
