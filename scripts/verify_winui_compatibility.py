"""Offline compatibility rehearsal; creates its own catalog, never accepts one.

This is a development lab, not a user-library migration/backup command.
All destinations must be fresh children of build/winui-stage8.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from worker.ownership import no_reparse


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def backup(source, target):
    # SQLite backup includes committed WAL pages; copying only the main DB does not.
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
            assert dst.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


LEGACY_SEED = r'''
import hashlib,json,sqlite3,sys,time
from pathlib import Path
from PIL import Image
from desktop.service import LibraryService
from backend.credentials import CivitaiCredentials
data=Path(sys.argv[1]);images=data/'originals';images.mkdir(parents=True)
service=LibraryService(data)
try:
 root=service.store.add_root(images)
 for i in range(4):
  path=images/f'owned-{i}.png';Image.new('RGB',(64+i,48),(40+i*20,80,120)).save(path)
  if i==0:path.with_suffix('.txt').write_text('synthetic sidecar',encoding='utf-8')
  service.store.upsert(path,root,path.stat(),dict(width=64+i,height=48,format='PNG',family='Stable Diffusion',model='synthetic-checkpoint',prompt='synthetic positive',negative_prompt='synthetic negative' if i==0 else '',tags=['synthetic_generation_term']))
 service.put_settings(dict(theme='dark',sort='name_desc',reduceMotion=True,density='compact'))
 state=service.content_label_state([1]);tag=service.content_tags()[0]['id']
 service.apply_content_label_overrides([1],[dict(tag_id=tag,action='include')],state['snapshot_token'])
 modeldir=data/'models';modeldir.mkdir();(modeldir/'owned.safetensors').write_bytes(b'inert synthetic weight')
 service.add_model_root(str(modeldir))
 service.civitai_credentials.set('synthetic-stage8-credential-012345',remember=True)
finally:service.close()
source=images/'owned-0.png';info=source.stat();target=data/'never-published'/'owned-0.png';now=time.time()
with service.store.connect() as db:
 db.execute("INSERT INTO jobs(id,kind,status,payload,created_at,updated_at) VALUES('interrupted-fixture','organize','running','{}',?,?)",(now,now))
 db.execute("INSERT INTO journal(job_id,image_id,source,destination,temp_path,source_root_id,size,mtime_ns,state,digest,created_at,updated_at) VALUES('interrupted-fixture',1,?,?,?,?,?,?,'prepared',?,?,?)",(str(source),str(target),str(target)+'.partial',root,info.st_size,info.st_mtime_ns,hashlib.sha256(source.read_bytes()).hexdigest(),now,now))
 side=source.with_suffix('.txt');sideinfo=side.stat();sidetarget=target.with_suffix('.txt')
 db.execute("INSERT INTO journal(job_id,image_id,source,destination,temp_path,source_root_id,size,mtime_ns,state,digest,created_at,updated_at,asset_kind) VALUES('interrupted-fixture',1,?,?,?,?,?,?,'prepared',?,?,?,'sidecar')",(str(side),str(sidetarget),str(sidetarget)+'.partial',root,sideinfo.st_size,sideinfo.st_mtime_ns,hashlib.sha256(side.read_bytes()).hexdigest(),now,now))
 db.execute('PRAGMA wal_checkpoint(TRUNCATE)') if not db.in_transaction else None
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    allowed = ROOT / 'build' / 'winui-stage8'
    if not output.is_relative_to(allowed) or output == allowed or output.exists():
        raise ValueError('Fresh build/winui-stage8 child required')
    no_reparse(output)
    output.mkdir(parents=True)
    checks = []

    def check(value, name):
        if not value:
            raise AssertionError(name)
        checks.append(name)

    legacy = ROOT / 'build/winui-stage1/legacy-source-baseline'
    manifest = json.loads((legacy / 'baseline.json').read_text(encoding='utf-8-sig'))
    for relative, digest in manifest.items():
        check(sha(legacy / relative) == digest, 'legacy input intact: ' + relative)
    source = output / 'legacy-owned'
    # The child imports the retained Qt0.7.2 backend, not today's Store.
    result = subprocess.run([sys.executable, '-c', LEGACY_SEED, str(source)],
                            cwd=legacy, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError('Synthetic legacy creation failed: ' + result.stderr.decode('utf-8', 'replace'))
    database = source / 'library.sqlite3'
    source_files = {p.relative_to(source).as_posix(): sha(p) for p in source.rglob('*') if p.is_file()}
    original_hashes = {p: sha(p) for p in (source / 'originals').iterdir()}

    from desktop.service import LibraryService
    from backend.credentials import CivitaiCredentials
    from worker.dispatch import dispatch
    from worker.file_ops import FileOperations
    from worker.management import publish
    event = threading.Event()
    snapshot = output / 'legacy-snapshot.sqlite3'
    backup(database, snapshot)
    check(sha(database) == source_files['library.sqlite3'], 'backup does not mutate source main DB')
    # Keep a writer open so the backup must include a committed, uncheckpointed WAL page.
    with closing(sqlite3.connect(output / 'wal-fixture.sqlite3')) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('CREATE TABLE own(value TEXT)')
        writer.commit()
        writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        writer.execute("INSERT INTO own VALUES('committed-in-wal')")
        writer.commit()
        check((output / 'wal-fixture.sqlite3-wal').stat().st_size > 0, 'committed WAL exists during backup')
        backup(output / 'wal-fixture.sqlite3', output / 'wal-backup.sqlite3')
        with closing(sqlite3.connect(output / 'wal-backup.sqlite3')) as restored_wal:
            check(restored_wal.execute('SELECT value FROM own').fetchone()[0] == 'committed-in-wal', 'SQLite backup retains uncheckpointed committed WAL')
    candidate = output / 'stage8-clone'
    candidate.mkdir()
    backup(snapshot, candidate / 'library.sqlite3')
    (candidate / 'credentials').mkdir()
    shutil.copyfile(source / 'credentials/civitai.dpapi', candidate / 'credentials/civitai.dpapi')
    check(sha(candidate / 'credentials/civitai.dpapi') == source_files['credentials/civitai.dpapi'], 'DPAPI copied byte-exact')
    check(CivitaiCredentials(candidate / 'credentials').get() == 'synthetic-stage8-credential-012345', 'same Windows user decrypts legacy DPAPI')

    service = LibraryService(candidate, automatic_recovery=False)
    try:
        def read(section, **args):
            return dispatch(service, 'manage.read', dict(section=section, args=args), event)
        check(service.store.stats()['total'] == 4, 'legacy images retained')
        check(read('settings')['theme'] == 'dark' and read('settings')['sort'] == 'name_desc', 'shared preferences retained')
        detail = service.detail(1)
        check(detail['prompt'] == 'synthetic positive' and detail['negative_prompt'] == 'synthetic negative', 'positive and negative remain distinct')
        check(service.detail(2)['negative_prompt'] == '', 'empty negative stays empty')
        check(service.store.content_label_state([1])['tags'][0]['manual_count'] == 1, 'semantic manual override retained')
        try:
            service.content_label_state([1])
            raise AssertionError('pending recovery guard bypassed')
        except Exception as error:
            check(getattr(error, 'code', '') == 'recovery_required', 'pending journal blocks edits until explicit recovery review')
        check(len(service.model_roots()) == 1, 'registered model root retained without scanning')
        check(not service.manager.recovering, 'no automatic physical recovery')
        with service.store.connect() as db:
            check(db.execute("SELECT status FROM jobs WHERE id='interrupted-fixture'").fetchone()[0] == 'interrupted', 'active legacy task interrupted')
            check(db.execute('SELECT state FROM journal').fetchone()[0] == 'prepared', 'prepared journal preserved for explicit review')
            check(db.execute("SELECT COUNT(*) FROM journal WHERE asset_kind='sidecar' AND state='prepared'").fetchone()[0] == 1, 'incomplete sidecar bundle retained for explicit review')
        check(not (source / 'never-published').exists(), 'startup does not publish synthetic interrupted files')
        # A stage7-compatible separate receipt/review DB survives SQLite backup too.
        operation = 'a' * 32
        publish(candidate / 'management-operations.sqlite3', operation,
                dict(state='completed', fingerprint='synthetic-fingerprint', action='settings.save', result={'sort': 'name_desc'}))
        files = FileOperations(service)
        with files.db() as db:
            db.execute("INSERT INTO reviews(id,kind,options,state,created_at) VALUES('owned-review','organize','{}','ready',1)")
    finally:
        service.close()
    rollback = output / 'rollback-copy'
    rollback.mkdir()
    for name in ('library.sqlite3', 'management-operations.sqlite3', 'file-reviews.sqlite3'):
        backup(candidate / name, rollback / name)
    restored = LibraryService(rollback, automatic_recovery=False)
    try:
        receipt = dispatch(restored, 'manage.status', {'operationId': operation}, event)
        check(receipt['state'] == 'completed', 'durable receipt restored')
        with FileOperations(restored).db() as db:
            check(db.execute("SELECT state FROM reviews WHERE id='owned-review'").fetchone()[0] == 'ready', 'persisted file review restored without executing')
    finally:
        restored.close()
    # Old Store can read its own backup; never open the running candidate in Qt.
    old_read = subprocess.run([sys.executable, '-c',
        "from backend.store import Store;import sys;assert Store(sys.argv[1]).stats()['total']==4",
        str(rollback)], cwd=legacy, capture_output=True, timeout=30)
    check(old_read.returncode == 0, 'retained old Store reads isolated rollback DB')
    for path, digest in original_hashes.items():
        check(sha(path) == digest, 'synthetic original unchanged')
    # main DB and remembered credential are the irreplaceable source snapshot.
    check(sha(database) == source_files['library.sqlite3'], 'legacy main DB bytes unchanged')
    check(sha(source / 'credentials/civitai.dpapi') == source_files['credentials/civitai.dpapi'], 'legacy credential bytes unchanged')
    check(not (source / 'never-published').exists(), 'no recovery move at end')
    report = dict(result='passed', checks=len(checks), assertions=checks,
                  originals=len(original_hashes), legacyInputs=len(manifest),
                  scope='offline synthetic Qt0.7.2 retained backend -> SQLite backup -> current service -> isolated rollback Store; same Windows DPAPI user only',
                  realLibraryOpened=False, realNetworkUsed=False, productionMigration=False)
    (output / 'compatibility-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('COMPATIBILITY_PASSED', len(checks))


if __name__ == '__main__':
    main()
