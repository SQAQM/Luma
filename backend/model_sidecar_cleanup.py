"""Persisted review of legacy exports and redundant exact-name companions.

Only database-registered model names are consulted; weights are never opened.
Recognized extra metadata and covers require a verified surviving companion.
Files are kept in the application's recovery directory, never deleted.
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
from contextlib import contextmanager, ExitStack
from pathlib import Path

from .duplicates import VerifiedFile, _fingerprint
from .model_sidecars import MAX_JSON, _hash, _pinned_parent
from .organizer import Cancelled, check_directory_chain, reparse_point
from . import model_covers, model_local_info
from .model_registry import model_name

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
            if 'keep_files' not in {row[1] for row in db.execute('PRAGMA table_info(items)')}:
                db.execute("ALTER TABLE items ADD COLUMN keep_files TEXT NOT NULL DEFAULT '[]'")
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
    metadata = [model.with_name(base+suffix) for base in (model.stem,model.name)
                for suffix in ('.json','.cm-info.json','.rgthree-info.json','.civitai.info')]
    covers = [model.with_name(base+ext) for base in
              (model.stem+'.preview',model.stem,model.name+'.preview',model.name)
              for ext in model_covers.EXTENSIONS]
    canonical = model.with_suffix('.civitai.info')
    return tuple(dict.fromkeys(path for path in metadata+covers if path!=canonical))


def _identity(file):
    return json.dumps(_fingerprint(file.info),separators=(',',':'))


def _snapshot(path, limit):
    with VerifiedFile(path) as file:
        if not 0 < file.info.st_size <= limit: raise ValueError('companion size')
        raw = file.file.read(limit+1); file.verify()
        if len(raw)>limit: raise ValueError('companion size')
        return raw, {'path':str(path),'size':file.info.st_size,'identity':_identity(file),
                     'change_ns':file.change_ns,'digest':hashlib.sha256(raw).hexdigest()}


def _json_value(raw):
    value = json.loads(raw.decode('utf-8-sig'),object_pairs_hook=_unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid number')))
    if not isinstance(value,dict): raise ValueError('companion object')
    return value


def _same_release(value, canonical):
    source = model_local_info._fields(value); target = model_local_info._fields(canonical)
    for names in (('modelid',),('versionid','modelversionid','id')):
        left = next((source[key] for key in names if key in source),None)
        right = next((target[key] for key in names if key in target),None)
        if left is not None and right is not None and left!=right:return False
    return source.get('source','Civitai') in ('Civitai',0)


def _shared_companion(db, model, path):
    """Reverse exact companion suffixes into a small indexed set of owners."""
    from .model_library import EXTENSIONS
    name = path.name
    suffixes = [s for s in ('.cm-info.json','.rgthree-info.json','.civitai.info','.json',*model_covers.EXTENSIONS)
                if name.casefold().endswith(s)]
    if not suffixes:return True
    bases = {name[:-len(suffix)] for suffix in suffixes}
    if path.suffix.casefold() in model_covers.EXTENSIONS:
        bases.update(base[:-8] for base in tuple(bases) if base.casefold().endswith('.preview'))
    names = {model_name(b+ext) for b in bases for ext in EXTENSIONS}
    names.update(model_name(b) for b in bases if Path(b).suffix.casefold() in EXTENSIONS)
    placeholders=','.join('?' for _ in names)
    for row in db.execute('SELECT path FROM luma_model_files WHERE normalized_name IN ('+placeholders+')',tuple(names)):
        owner=Path(row[0])
        if owner!=model and (path==owner.with_suffix('.civitai.info') or path in _companions(owner)):return True
    return False


def _inventory(model):
    """One model at a time, bounded JSON/cover bytes, no directory recursion."""
    canonical = keeper = None
    try:
        raw, keeper = _snapshot(model.with_suffix('.civitai.info'),MAX_JSON)
        canonical = _json_value(raw)
        if not model_local_info.parse(canonical,model,'civitai'):canonical=keeper=None
    except (OSError,ValueError,UnicodeError,RecursionError,TypeError,AttributeError):pass
    cover_keeper = None
    for path,_ in model_covers.candidates(model):
        try:
            from PIL import Image
            import io, warnings
            raw, snap = _snapshot(path,model_covers.MAX_BYTES)
            with warnings.catch_warnings():
                warnings.simplefilter('error',Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format not in ('PNG','JPEG','WEBP','AVIF','GIF') or image.width*image.height>model_covers.MAX_PIXELS:continue
                    image.load()
            cover_keeper = snap;break
        except (OSError,ValueError,Image.DecompressionBombError,Image.DecompressionBombWarning):continue
    for path in _companions(model):
        try:
            is_cover = path.suffix.lower() in model_covers.EXTENSIONS
            if is_cover:
                if cover_keeper is None or str(path)==cover_keeper['path']:continue
                raw,snap = _snapshot(path,model_covers.MAX_BYTES)
                yield snap,[cover_keeper];continue
            raw,snap = _snapshot(path,MAX_JSON)
            legacy = path in (model.with_suffix('.json'),model.with_name(model.name+'.rgthree-info.json')) and _owned(raw,model.name)
            if legacy:yield snap,[keeper] if canonical is not None else [];continue
            if canonical is None:continue
            value = _json_value(raw)
            format_name = 'matrix' if path.name.endswith('.cm-info.json') else 'rgthree' if path.name.endswith('.rgthree-info.json') else 'civitai' if path.name.endswith('.civitai.info') else 'webui'
            related = value.get('raw',{}).get('civitai',value) if format_name=='rgthree' and isinstance(value.get('raw'),dict) else value
            if model_local_info.parse(value,model,format_name) and _same_release(related,canonical):yield snap,[keeper]
        except (OSError,ValueError,UnicodeError,RecursionError,TypeError,AttributeError):continue


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
                for snap,keepers in _inventory(model):
                    path = Path(snap['path'])
                    with library.store.connect() as db:
                        if _shared_companion(db,model,path):continue
                    cursor = manifest.execute('INSERT OR IGNORE INTO items(plan_id,model_path,source,size,identity,change_ns,digest,keep_files) VALUES(?,?,?,?,?,?,?,?)',
                        (plan_id,str(model),str(path),snap['size'],snap['identity'],snap['change_ns'],snap['digest'],json.dumps(keepers)))
                    if cursor.rowcount:
                        item_id = cursor.lastrowid
                        destination = _directory(library)/plan_id/(str(item_id)+path.suffix)
                        manifest.execute('UPDATE items SET destination=? WHERE id=?',(str(destination),item_id))
        count,size = manifest.execute('SELECT COUNT(*),COALESCE(SUM(size),0) FROM items WHERE plan_id=?',(plan_id,)).fetchone()
        manifest.execute("UPDATE plans SET status='ready',total=?,bytes=? WHERE id=?",(count,size,plan_id))
        reviewed = list(manifest.execute('SELECT source,size,keep_files FROM items WHERE plan_id=? ORDER BY id LIMIT 100',(plan_id,)))
        items = [{'name':Path(row['source']).name,'path':row['source'],'bytes':row['size']} for row in reviewed]
        kept = {keeper['path']:keeper for row in reviewed for keeper in json.loads(row['keep_files'])}
        keep_items = [{'name':Path(path).name,'path':path} for path in kept]
        prior = manifest.execute("SELECT p.id,COUNT(*) count,SUM(i.size) bytes FROM plans p JOIN items i ON i.plan_id=p.id WHERE i.status IN ('moved','prepared','restore_prepared','needs_review') GROUP BY p.id ORDER BY p.created DESC LIMIT 1").fetchone()
        previous = dict(prior) if prior else None
        if previous:
            previous['items'] = [{'name':Path(row['source']).name,'path':row['source'],'bytes':row['size']} for row in
                manifest.execute("SELECT source,size FROM items WHERE plan_id=? AND status IN ('moved','prepared','restore_prepared','needs_review') ORDER BY id LIMIT 100",(prior['id'],))]
            previous['items_truncated'] = previous['count']>BATCH
        return {'id':plan_id,'count':count,'total':count,'bytes':size,'items':items,'items_truncated':count>BATCH,
                'keep_items':keep_items,'last_plan':previous}


def _state(db, item_id, state, *, file=None, error=''):
    if file is not None:
        db.execute('UPDATE items SET status=?,q_identity=?,q_change=?,error=? WHERE id=?',(state,_identity(file),file.change_ns,error,item_id))
    else: db.execute('UPDATE items SET status=?,error=? WHERE id=?',(state,error,item_id))
    db.commit()  # Durable before and after every handle mutation.


def _paths(library, row):
    model,source,destination = (Path(row[key]) for key in ('model_path','source','destination'))
    if not model.is_absolute() or source not in _companions(model): raise ValueError('资料精简来源与模型不符')
    parent = _directory(library)/_token(row['plan_id'])
    expected = {parent/(str(row['id'])+'.json'),parent/(str(row['id'])+source.suffix)}
    if destination not in expected or source==destination: raise ValueError('资料精简恢复路径无效')
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
        manager.progress(job_id,total=total,processed=0,errors=0,message='正在恢复资料' if restoring else '正在整理资料')
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
                    manager.progress(job_id,processed=processed,errors=errors,message=f"{'恢复资料' if restoring else '整理资料'} {processed}/{total}；失败 {errors}")
        finally:
            db.execute('UPDATE plans SET status=? WHERE id=?',('interrupted' if cancelled() else 'restored' if restoring else 'finished',plan_id)); db.commit()
        manager.progress(job_id,processed=processed,errors=errors,message='资料已恢复' if restoring else '资料已整理')


def _move_one(library, db, row, cancelled):
    source,destination = _paths(library,row)
    with library.store.connect() as index:
        if not index.execute('SELECT 1 FROM luma_model_files WHERE path=?',(row['model_path'],)).fetchone():
            raise ValueError('模型已移出登记目录')
        if _shared_companion(index,Path(row['model_path']),source):raise ValueError('资料由多个模型共用')
    check_directory_chain(destination.parent)
    destination.parent.mkdir(exist_ok=True)
    with ExitStack() as stack:
        model = Path(row['model_path'])
        for keeper in json.loads(row['keep_files']):
            path = Path(keeper['path'])
            allowed = path==model.with_suffix('.civitai.info') or path in _companions(model) and path.suffix in model_covers.EXTENSIONS
            if not allowed or path==source:raise ValueError('保留资料路径无效')
            held = stack.enter_context(VerifiedFile(path,parent_writes=True))
            _matches(held,keeper)
            if held.digest(cancelled)!=keeper['digest']:raise ValueError('保留资料在预览后已改变')
        file = stack.enter_context(VerifiedFile(source,mutable=True))
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
