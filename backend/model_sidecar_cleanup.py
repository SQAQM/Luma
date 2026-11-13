"""Explicit, persisted review of unchanged legacy Luma compatibility sidecars.

Only database-registered model names are consulted; weights and covers are never
opened. Files are kept in the application's recovery directory, never deleted.
Windows mutations use the same VerifiedFile handle that verified the manifest.
Cross-volume moves fail closed. Interrupted renames remain available to undo.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .duplicates import VerifiedFile, _fingerprint
from .model_sidecars import MAX_JSON, _hash, _pinned_parent
from .organizer import Cancelled, check_directory_chain, reparse_point

BATCH = 100


def _token(value):
    if not isinstance(value,str) or len(value)!=32 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('资料精简预览标识无效')
    return value


def _directory(library):
    return Path(os.path.abspath(library.store.directory)) / 'ModelInfoRecovery'


@contextmanager
def _manifest(library):
    directory = _directory(library)
    check_directory_chain(directory)
    directory.mkdir(exist_ok=True)
    with _pinned_parent(directory):
        path = directory/'plans.sqlite'
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or reparse_point(path,info) or info.st_nlink!=1:
                raise ValueError('资料精简恢复清单不可安全访问')
        db = sqlite3.connect(path,timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,root_id INTEGER,status TEXT NOT NULL,
                    total INTEGER NOT NULL DEFAULT 0,bytes INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL,job_id TEXT);
                CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY AUTOINCREMENT,plan_id TEXT NOT NULL,
                    model_path TEXT NOT NULL,source TEXT NOT NULL,destination TEXT NOT NULL DEFAULT '',
                    size INTEGER NOT NULL,identity TEXT NOT NULL,change_ns INTEGER NOT NULL,digest TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ready',q_identity TEXT,q_change INTEGER,error TEXT NOT NULL DEFAULT '',
                    UNIQUE(plan_id,source));
                CREATE INDEX IF NOT EXISTS ix_info_items ON items(plan_id,status,id);
            ''')
            yield db
            db.commit()
        except BaseException:
            db.rollback(); raise
        finally: db.close()


def _unique(pairs):
    result = {}
    for key,value in pairs:
        if key in result: raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _owned(raw, filename):
    value = json.loads(raw.decode('utf-8-sig'),object_pairs_hook=_unique,
                       parse_constant=lambda value: (_ for _ in ()).throw(ValueError('invalid JSON number')))
    if not isinstance(value,dict): return False
    marker = value.get('_luma')
    if not isinstance(marker,dict) or set(marker)!={'schema','file','managed'}: return False
    if type(marker['schema']) is not int or marker['schema']!=1 or marker['file']!=filename: return False
    managed = marker['managed']
    if not isinstance(managed,dict) or not managed: return False
    leaves = {}
    def visit(data, prefix=''):
        for key,item in data.items():
            # Slash is the ownership path separator, never a generated key.
            if '/' in key: raise ValueError('ambiguous ownership key')
            name = prefix+key
            if isinstance(item,dict) and item: visit(item,name+'/')
            else: leaves[name] = _hash(item)
    visit({key:item for key,item in value.items() if key!='_luma'})
    return leaves==managed


def _companions(model):
    return (model.with_name(model.stem+'.json'),model.with_name(model.name+'.rgthree-info.json'))


def _identity(file):
    return json.dumps(_fingerprint(file.info),separators=(',',':'))


def preview(library, root_id=None):
    if root_id is not None and (type(root_id) is not int or root_id<1): raise ValueError('模型目录无效')
    with library.lock, _manifest(library) as manifest:
        with library.store.connect() as db:
            if root_id is not None and not db.execute('SELECT 1 FROM luma_model_roots WHERE id=?',(root_id,)).fetchone():
                raise ValueError('模型目录不存在')
            highwater = db.execute('SELECT COALESCE(MAX(id),0) FROM luma_model_files').fetchone()[0]
        plan_id = uuid.uuid4().hex
        manifest.execute("INSERT INTO plans(id,root_id,status,created) VALUES(?,?,'preparing',?)",(plan_id,root_id,time.time()))
        last = 0
        while True:
            with library.store.connect() as db:
                scope = ' AND EXISTS(SELECT 1 FROM luma_model_memberships m WHERE m.file_id=f.id AND m.root_id=?)' if root_id is not None else ''
                args = (last,highwater,root_id) if root_id is not None else (last,highwater)
                rows = list(db.execute('SELECT f.id,f.path FROM luma_model_files f WHERE f.id>? AND f.id<=?'+scope+' ORDER BY f.id LIMIT 100',args))
            if not rows: break
            for row in rows:
                last = row['id']; model = Path(row['path'])
                if not model.is_absolute(): continue
                for path in _companions(model):
                    try:
                        with VerifiedFile(path) as file:
                            if file.info.st_size>MAX_JSON: continue
                            raw = file.file.read(MAX_JSON+1); file.verify()
                            if not _owned(raw,model.name): continue
                            cursor = manifest.execute('INSERT OR IGNORE INTO items(plan_id,model_path,source,size,identity,change_ns,digest) VALUES(?,?,?,?,?,?,?)',
                                (plan_id,str(model),str(path),file.info.st_size,_identity(file),file.change_ns,hashlib.sha256(raw).hexdigest()))
                            if cursor.rowcount:
                                item_id = cursor.lastrowid
                                destination = _directory(library)/plan_id/(str(item_id)+'.json')
                                manifest.execute('UPDATE items SET destination=? WHERE id=?',(str(destination),item_id))
                    except (OSError,ValueError,UnicodeError,RecursionError): continue
        count,size = manifest.execute('SELECT COUNT(*),COALESCE(SUM(size),0) FROM items WHERE plan_id=?',(plan_id,)).fetchone()
        manifest.execute("UPDATE plans SET status='ready',total=?,bytes=? WHERE id=?",(count,size,plan_id))
        items = [{'name':Path(row['source']).name,'path':row['source'],'bytes':row['size']} for row in
                 manifest.execute('SELECT source,size FROM items WHERE plan_id=? ORDER BY id LIMIT 100',(plan_id,))]
        prior = manifest.execute("SELECT p.id,COUNT(*) count,SUM(i.size) bytes FROM plans p JOIN items i ON i.plan_id=p.id WHERE i.status IN ('moved','prepared','restore_prepared','needs_review') GROUP BY p.id ORDER BY p.created DESC LIMIT 1").fetchone()
        previous = dict(prior) if prior else None
        if previous:
            previous['items'] = [{'name':Path(row['source']).name,'path':row['source'],'bytes':row['size']} for row in
                manifest.execute("SELECT source,size FROM items WHERE plan_id=? AND status IN ('moved','prepared','restore_prepared','needs_review') ORDER BY id LIMIT 100",(prior['id'],))]
            previous['items_truncated'] = previous['count']>BATCH
        return {'id':plan_id,'count':count,'total':count,'bytes':size,'items':items,'items_truncated':count>BATCH,
                'last_plan':previous}


def _state(db, item_id, state, *, file=None, error=''):
    if file is not None:
        db.execute('UPDATE items SET status=?,q_identity=?,q_change=?,error=? WHERE id=?',(state,_identity(file),file.change_ns,error,item_id))
    else: db.execute('UPDATE items SET status=?,error=? WHERE id=?',(state,error,item_id))
    db.commit()  # Durable before and after every handle mutation.


def _paths(library, row):
    model,source,destination = (Path(row[key]) for key in ('model_path','source','destination'))
    if not model.is_absolute() or source not in _companions(model): raise ValueError('资料精简来源与模型不符')
    expected = _directory(library)/_token(row['plan_id'])/(str(row['id'])+'.json')
    if destination!=expected or source==destination: raise ValueError('资料精简恢复路径无效')
    return source,destination


def _matches(file, row, *, recovered=False, after_rename=False):
    if recovered and row['q_identity']:
        identity,change = row['q_identity'],row['q_change']
    elif recovered:
        # A crash after rename may precede storing the new change timestamp.
        # Device/inode, size, link count and mtime still identify the same file.
        original = json.loads(row['identity']); current = list(_fingerprint(file.info))
        if current[:2]+current[3:]!=original[:2]+original[3:]: raise ValueError('中断恢复文件身份已改变')
        return
    else: identity,change = row['identity'],row['change_ns']
    if after_rename:
        original = json.loads(identity); current = list(_fingerprint(file.info))
        if current[:2]+current[3:]!=original[:2]+original[3:]: raise ValueError('中断恢复文件身份已改变')
        return
    if _identity(file)!=identity or file.change_ns!=change: raise ValueError('资料文件在预览后已改变，已保留')


def _execute(library, manager, job_id, payload, *, restoring):
    if payload.get('confirmed') is not True: raise ValueError('请先确认具体资料精简预览')
    plan_id = _token(payload.get('plan_id')); cancelled = lambda:manager.cancelled(job_id)
    processed = errors = last = 0
    with library.lock, _manifest(library) as db:
        plan = db.execute('SELECT * FROM plans WHERE id=?',(plan_id,)).fetchone()
        if not plan or (not restoring and plan['status']!='ready'): raise ValueError('资料精简预览已失效或已执行，请重新预览')
        states = "('moved','prepared','restore_prepared','needs_review')" if restoring else "('ready')"
        total = db.execute('SELECT COUNT(*) FROM items WHERE plan_id=? AND status IN '+states,(plan_id,)).fetchone()[0]
        db.execute('UPDATE plans SET status=?,job_id=? WHERE id=?',('restoring' if restoring else 'running',job_id,plan_id)); db.commit()
        manager.progress(job_id,total=total,processed=0,errors=0,message='正在核验资料恢复记录' if restoring else '正在核验已确认的旧版资料')
        try:
            while True:
                rows = list(db.execute('SELECT * FROM items WHERE plan_id=? AND id>? AND status IN '+states+' ORDER BY id LIMIT 100',(plan_id,last)))
                if not rows: break
                for row in rows:
                    if cancelled(): raise Cancelled()
                    last = row['id']
                    try:
                        if restoring: _restore_one(library,db,row,cancelled)
                        else: _move_one(library,db,row,cancelled)
                    except Cancelled: raise
                    except Exception:
                        errors += 1
                        # A failed COMMIT can leave an uncommitted "moved" or
                        # "restored" visible on this connection. Discard that
                        # transaction, then read the durable intent separately;
                        # never turn a possibly completed rename into "error".
                        db.rollback()
                        with _manifest(library) as durable:
                            state = durable.execute('SELECT status FROM items WHERE id=?',(row['id'],)).fetchone()[0]
                            uncertain = state in ('prepared','moved','restore_prepared','restored','needs_review')
                            _state(durable,row['id'],'needs_review' if uncertain else state if restoring else 'error',
                                   error='恢复路径冲突或文件已改变；资料仍保留' if restoring else '资料已改变、被占用或无法同盘安全移动；请保留并检查')
                    processed += 1
                    manager.progress(job_id,processed=processed,errors=errors,message=f"{'恢复资料' if restoring else '精简资料'} {processed}/{total}；失败 {errors}")
        finally:
            db.execute('UPDATE plans SET status=? WHERE id=?',('interrupted' if cancelled() else 'restored' if restoring else 'finished',plan_id)); db.commit()
        manager.progress(job_id,processed=processed,errors=errors,message='资料恢复完成；冲突文件保留，不覆盖' if restoring else '旧版兼容资料已移入本软件恢复区；可在“精简资料”中撤销')


def _move_one(library, db, row, cancelled):
    source,destination = _paths(library,row)
    with library.store.connect() as index:
        if not index.execute('SELECT 1 FROM luma_model_files WHERE path=?',(row['model_path'],)).fetchone():
            raise ValueError('模型已移出登记目录')
    check_directory_chain(destination.parent)
    destination.parent.mkdir(exist_ok=True)
    with VerifiedFile(source,mutable=True) as file:
        _matches(file,row)
        if file.digest(cancelled)!=row['digest']: raise ValueError('资料内容在预览后已改变')
        if destination.parent.stat().st_dev!=file.info.st_dev: raise ValueError('跨分区资料暂保留，无法使用安全句柄移动')
        if cancelled(): raise Cancelled()
        _state(db,row['id'],'prepared')
        file.rename(destination)
        _state(db,row['id'],'moved',file=file)


def _restore_one(library, db, row, cancelled):
    target,source = _paths(library,row)
    check_directory_chain(target.parent)
    # An interrupted apply may have recorded its intent before moving at all.
    # An interrupted undo may have moved back before committing its final state.
    if not source.exists() and not source.is_symlink():
        with VerifiedFile(target) as file:
            _matches(file,row,recovered=True,after_rename=True)
            if file.digest(cancelled)!=row['digest']: raise ValueError('原路径内容不符，恢复记录保留')
        _state(db,row['id'],'restored'); return
    if target.exists() or target.is_symlink(): raise FileExistsError('原路径已存在，不覆盖')
    with VerifiedFile(source,mutable=True) as file:
        _matches(file,row,recovered=True)
        if file.digest(cancelled)!=row['digest']: raise ValueError('恢复区资料已改变')
        if cancelled(): raise Cancelled()
        _state(db,row['id'],'restore_prepared')
        file.rename(target)
        _state(db,row['id'],'restored',file=file)


def apply(library, manager, job_id, payload):
    _execute(library,manager,job_id,payload,restoring=False)


def undo(library, manager, job_id, payload):
    _execute(library,manager,job_id,payload,restoring=True)
