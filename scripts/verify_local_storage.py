"""Real SQLite offline snapshot rehearsal. Own generated catalog only; no default roots."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.store import Store
from worker.ownership import no_reparse


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    parser.add_argument('--driver',default=str(ROOT/'winui/tests/Luma.Core.Tests/bin/Release/net10.0/Luma.Core.Tests.exe'))
    args=parser.parse_args()
    output=Path(args.output).absolute()
    if not output.is_relative_to(ROOT/'build') or output.exists():raise ValueError('fresh own build lab required')
    no_reparse(output);output.mkdir(parents=True)
    profile=output/'source';profile.mkdir()
    library=profile/('engine-probe-'+uuid.uuid4().hex);library.mkdir()
    (profile/'.luma-winui-preview.json').write_text(json.dumps({'purpose':'luma-winui-stage8-preview'}))
    (profile/'winui-library-v1.json').write_text(json.dumps({'directory':library.name}))
    (library/'.luma-engine-preview.json').write_text(json.dumps({'schema':1,'purpose':'luma-stage3-preview'}))
    store=Store(library)
    store.update_settings({'theme':'dark','thumbnailCacheMiB':128})
    # Freeze committed WAL bytes before SQLite checkpoints them on last-close.
    live=output/'live.sqlite3'
    connection=sqlite3.connect(live)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA wal_autocheckpoint=0')
    connection.execute('CREATE TABLE evidence(id INTEGER PRIMARY KEY,value TEXT)')
    connection.execute("INSERT INTO evidence(value) VALUES('synthetic committed WAL')");connection.commit()
    for suffix in ('','-wal'):
        shutil.copyfile(Path(str(live)+suffix),library/('management-operations.sqlite3'+suffix))
    connection.close()
    with closing(sqlite3.connect(library/'file-reviews.sqlite3')) as receipts:
        receipts.execute('CREATE TABLE evidence(id INTEGER PRIMARY KEY,value TEXT)')
        receipts.execute("INSERT INTO evidence(value) VALUES('synthetic review receipt')")
        receipts.commit()
    before={path.relative_to(profile).as_posix():sha(path) for path in profile.rglob('*') if path.is_file()}
    command=[str(Path(args.driver).absolute()),'--storage-fixture',str(profile),str(output/'backup'),str(output/'app'/'Data')]
    result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
    (output/'driver.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    result.check_returncode()
    info=json.loads(result.stdout.strip());restored=Path(info['restored'])
    checks=0
    for relative,checksum in before.items():
        assert sha(profile/relative)==checksum;checks+=1
        assert sha(restored/relative)==checksum;checks+=1
    for name,query,expected in [
        ('library.sqlite3',"SELECT value FROM settings WHERE key='thumbnailCacheMiB'",'128'),
        ('management-operations.sqlite3','SELECT value FROM evidence WHERE id=1','synthetic committed WAL'),
        ('file-reviews.sqlite3','SELECT value FROM evidence WHERE id=1','synthetic review receipt')]:
        with sqlite3.connect(restored/library.name/name) as database:
            assert database.execute('PRAGMA integrity_check').fetchone()[0]=='ok';checks+=1
            assert database.execute(query).fetchone()[0]==expected;checks+=1
    report={'result':'passed_synthetic_offline_only','checks':checks,'files':len(before),'uncheckpointedWalRecovered':True,'receiptDatabases':2,'sourceBytesUnchanged':True,'defaultCatalogAccess':False,'originalImageAccess':False}
    (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('STORAGE_SQLITE_PASSED',checks)


if __name__=='__main__':main()
