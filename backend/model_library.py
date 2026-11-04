"""Read-only local model inventory and explicit verified Civitai snapshots."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .duplicates import VerifiedFile, _change_stamp
from .organizer import Cancelled, signature, check_directory_chain, reparse_point
from .model_registry import model_name, FAMILIES
from .model_references import initialize, update_image_references, _projection, hash_projection
from .civitai import API_ORIGIN, normalize_trigger_words
from . import model_covers

EXTENSIONS=frozenset(('.safetensors','.ckpt','.gguf','.pt','.pth','.bin'))
_SHA=re.compile('[0-9a-fA-F]{64}\\Z')
_UNSET=object()


def model_selections(value,dimension):
    """Canonical bounded selections; resource types remain extensible."""
    if value is _UNSET: return []
    if not isinstance(value,list) or len(value)>32: raise ValueError('模型筛选条件无效')
    if any(not isinstance(v,str) or not v.strip() or len(v)>100 or '\0' in v for v in value):
        raise ValueError('模型筛选条件无效')
    result=sorted({v.strip() for v in value})
    if dimension=='families' and any(v not in (*FAMILIES,'Unknown') for v in result):
        raise ValueError('模型系筛选无效')
    return result


def _cached_record(encoded):
    if not isinstance(encoded,str) or len(encoded)>1024*1024: return {}
    try: value=json.loads(encoded)
    except (ValueError,TypeError): return {}
    return value if isinstance(value,dict) else {}


def _resource_type(record,digest):
    # Match only this exact weight SHA, including version-attached VAE files.
    if not isinstance(digest,str) or not _SHA.fullmatch(digest): return 'Unknown'
    files=record.get('files',[])
    if not isinstance(files,list): return 'Unknown'
    roles=[]
    for file in files[:200]:
        if not isinstance(file,dict) or not isinstance(file.get('sha256'),str) or file['sha256'].lower()!=digest.lower(): continue
        role=file.get('resource_type') if 'resource_type' in file else record.get('model_type',record.get('type','')) if str(file.get('type','Model')).casefold()=='model' else file.get('type','Unknown')
        role=role.strip() if isinstance(role,str) and 0<len(role.strip())<=100 and '\0' not in role else 'Unknown'
        roles.append('Unknown' if role.casefold()=='unknown' else role)
    return roles[0] if roles and len({role.casefold() for role in roles})==1 else 'Unknown'


def _cached_family(record):
    family=record.get('family')
    return family if isinstance(family,str) and family in FAMILIES else 'Unknown'


class _SourceChanged(ValueError):
    pass


def _verify_source(file):
    try: file.verify()
    except (OSError,ValueError): raise _SourceChanged('模型文件身份改变，请重新索引') from None


def _id(value):
    if type(value) is not int or value<1: raise ValueError('模型 ID 无效')
    return value


def _source_url(record):
    # Public navigation is derived from identities, including older snapshots;
    # upstream/cached free-form URLs never become application navigation.
    model_id,version_id=record.get('model_id'),record.get('version_id')
    if any(type(value) is not int or not 0<value<=2**63-1 for value in (model_id,version_id)):
        return ''
    return f'{API_ORIGIN}/models/{model_id}?modelVersionId={version_id}'


def _identity(path,info):
    return (info.st_size,info.st_mtime_ns,info.st_ctime_ns,str(info.st_dev),str(info.st_ino),_change_stamp(path,info))


@contextmanager
def _directory_guard(path,ancestors=True):
    """Directory handles; holds ancestors against replacement on Windows."""
    handles=[]
    try:
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            api=ctypes.WinDLL('kernel32',use_last_error=True)
            api.CreateFileW.argtypes=(wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE)
            api.CreateFileW.restype=wintypes.HANDLE
            api.CloseHandle.argtypes=(wintypes.HANDLE,)
            current=Path(path)
            while current!=current.parent:
                handle=api.CreateFileW(str(current),0x80000000,1,None,3,0x02200000,None)
                if handle==ctypes.c_void_p(-1).value: raise ValueError('模型目录正被修改，已跳过')
                handles.append((api,handle))
                if not ancestors: break
                current=current.parent
        check_directory_chain(path)
        yield
    finally:
        for api,handle in reversed(handles): api.CloseHandle(handle)


class ModelLibrary:
    def __init__(self,store,organizer_lock=None,client=None,cache_dir=None):
        self.store=store; self.lock=organizer_lock or threading.RLock(); self.client=client
        self.cache_dir=Path(cache_dir or store.directory/'model-covers')
        with store.connect() as db: initialize(db)

    def add_root(self,path):
        path=Path(os.path.abspath(os.fspath(path)))
        check_directory_chain(path); info=path.lstat()
        if not stat.S_ISDIR(info.st_mode) or reparse_point(path,info): raise ValueError('请选择普通模型目录')
        with self.lock, self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO luma_model_roots(path,name) VALUES(?,?)',(str(path),path.name or str(path)))
            row=db.execute('SELECT id,path,name FROM luma_model_roots WHERE path=?',(str(path),)).fetchone()
        return next(root for root in self.roots() if root['id']==row['id'])

    def roots(self):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute('SELECT r.id,r.path,r.name,(SELECT count(*) FROM luma_model_memberships m WHERE m.root_id=r.id) count FROM luma_model_roots r ORDER BY r.id')]

    def remove_root(self,root_id):
        with self.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE'); _id(root_id)
            if not db.execute('SELECT 1 FROM luma_model_roots WHERE id=?',(root_id,)).fetchone(): raise ValueError('模型目录不存在')
            db.execute('DELETE FROM luma_model_memberships WHERE root_id=?',(root_id,))
            db.execute('DELETE FROM luma_model_files WHERE NOT EXISTS(SELECT 1 FROM luma_model_memberships m WHERE m.file_id=luma_model_files.id)')
            db.execute('UPDATE luma_model_files SET root_id=(SELECT MIN(root_id) FROM luma_model_memberships m WHERE m.file_id=luma_model_files.id) WHERE root_id=?',(root_id,))
            db.execute('DELETE FROM luma_model_roots WHERE id=?',(root_id,))
        return {'removed':True,'root_id':root_id}

    def _item(self,row,detail=False):
        item=dict(row); record=_cached_record(item.pop('record',None))
        for key in ('model_name','version_name','type','base_model','family','model_id','version_id'):
            item[key]=record.get(key,item.get(key) or (None if key.endswith('_id') else ''))
        item['type']=_resource_type(record,item.get('sha256'))
        item['family']=_cached_family(record)
        item['cover_available']=model_covers.available(item['path']) or bool(self._cover_for_hash(item.get('sha256')))
        if detail:
            for key in ('description','version_description','creator','updated_at'):
                item[key]=record.get(key,'')
            words=normalize_trigger_words(record.get('trigger_words',record.get('trained_words',[])))
            item['trigger_words']=words; item['trained_words']=list(words)
            item['source_url']=_source_url(record)
        return item

    def list_models(self,root_id=None,q='',cursor=None,limit=60,types=_UNSET,families=_UNSET):
        if type(limit) is not int or not 1<=limit<=120: raise ValueError('模型分页数量无效')
        if not isinstance(q,str) or len(q)>1024: raise ValueError('模型搜索无效')
        types=model_selections(types,'types'); families=model_selections(families,'families')
        clauses=[]; args=[]
        if root_id is not None:
            clauses.append('f.id IN (SELECT file_id FROM luma_model_memberships WHERE root_id=?)');args.append(_id(root_id))
        if q.strip():
            clauses.append("(instr(lower(f.name),lower(?))>0 OR instr(lower(COALESCE(json_extract(v.record,'$.model_name'),'')),lower(?))>0)");args.extend([q.strip(),q.strip()])
        where=' AND '.join(clauses) or '1'
        with self.store.connect() as db:
            # SQLite evaluates these bounded cached-JSON projections per row;
            # neither all model rows nor weight bytes enter Python collections.
            db.create_function('luma_resource_type',2,lambda record,digest:_resource_type(_cached_record(record),digest),deterministic=True)
            db.create_function('luma_cached_family',1,lambda record:_cached_family(_cached_record(record)),deterministic=True)
            db.execute('BEGIN'); revision=db.execute('SELECT revision FROM query_state WHERE id=1').fetchone()[0]
            fingerprint=hashlib.sha256(json.dumps([root_id,q,types,families]).encode()).hexdigest()
            anchor=0
            if cursor:
                try:
                    if not isinstance(cursor,str) or len(cursor)>2048: raise ValueError()
                    data=json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))
                    if data['revision']!=revision or data['filter']!=fingerprint: raise ValueError()
                    anchor=_id(data['id'])
                except (ValueError,TypeError,KeyError,UnicodeError): raise ValueError('模型分页已过期，请刷新') from None
            source=' FROM luma_model_files f LEFT JOIN luma_model_versions v ON v.version_id=f.version_id WHERE '+where
            facets={}
            for key,expression in (('types','luma_resource_type(v.record,f.sha256)'),('families','luma_cached_family(v.record)')):
                # Scope facets precede both dimension filters. Unknown is first
                # in SQL so it cannot be lost to the bounded type facet limit.
                facets[key]=sorted(r[0] for r in db.execute('SELECT DISTINCT '+expression+' AS facet'+source+" ORDER BY (facet='Unknown') DESC,facet LIMIT 128",args))
            filtered=list(args)
            for selected,expression in ((types,'luma_resource_type(v.record,f.sha256)'),(families,'luma_cached_family(v.record)')):
                if selected:
                    source+=' AND '+expression+' IN ('+','.join('?' for _ in selected)+')';filtered.extend(selected)
            total=db.execute('SELECT count(*)'+source,filtered).fetchone()[0]
            rows=list(db.execute('SELECT f.id,f.root_id,f.path,f.name,f.size,f.mtime_ns,f.sha256,f.status,f.version_id,v.record'+source+' AND f.id>? ORDER BY f.id LIMIT ?',[*filtered,anchor,limit+1]))
        more=len(rows)>limit; rows=rows[:limit]
        token=base64.urlsafe_b64encode(json.dumps({'revision':revision,'filter':fingerprint,'id':rows[-1]['id']}).encode()).decode().rstrip('=') if more else None
        return {'items':[self._item(row) for row in rows],'total':total,'next_cursor':token,'facets':facets}

    def detail(self,model_id):
        with self.store.connect() as db:
            row=db.execute('SELECT f.id,f.root_id,f.path,f.name,f.size,f.mtime_ns,f.sha256,f.status,f.version_id,v.record FROM luma_model_files f LEFT JOIN luma_model_versions v ON v.version_id=f.version_id WHERE f.id=?',(_id(model_id),)).fetchone()
        if row is None: raise ValueError('模型文件不存在')
        return self._item(row,True)

    def scan(self,manager,job_id,payload):
        cancelled=lambda:manager.cancelled(job_id)
        count=errors=0; token=uuid.uuid4().hex
        with self.lock:
            with self.store.connect() as db: root=db.execute('SELECT path FROM luma_model_roots WHERE id=?',(_id(payload.get('root_id')),)).fetchone()
            if not root: raise ValueError('模型目录不存在')
            root_id=payload['root_id']; root_path=Path(root[0]); check_directory_chain(root_path)
            # Every stack frame retains its identity. No recursive file list.
            stack=[]
            def enter(path):
                guard=_directory_guard(path,ancestors=not stack); guard.__enter__()
                try:
                    info=path.lstat()
                    if reparse_point(path,info) or not stat.S_ISDIR(info.st_mode): raise ValueError('模型目录身份改变')
                    stack.append((path,info.st_dev,info.st_ino,os.scandir(path),guard))
                except BaseException:
                    guard.__exit__(None,None,None); raise
            try:
                enter(root_path)
                while stack:
                    if cancelled(): raise Cancelled()
                    directory,device,inode,iterator,guard=stack[-1]
                    now=directory.lstat(); check_directory_chain(directory)
                    if (now.st_dev,now.st_ino)!=(device,inode): raise ValueError('模型目录身份改变')
                    try: entry=next(iterator)
                    except StopIteration:
                        stack.pop()[3].close();guard.__exit__(None,None,None);continue
                    try:
                        info=entry.stat(follow_symlinks=False)
                        if entry.is_symlink() or reparse_point(entry.path,info): continue
                        if stat.S_ISDIR(info.st_mode):
                            if len(stack)<256: enter(Path(entry.path))
                            continue
                        if not stat.S_ISREG(info.st_mode) or Path(entry.name).suffix.casefold() not in EXTENSIONS: continue
                        path=Path(entry.path)
                        # Windows DirEntry.stat can omit device/inode. Use the
                        # non-following path stat for durable file identity.
                        info=signature(path); identity=_identity(path,info)
                        # Recheck path and parent immediately before committing.
                        current=signature(path)
                        if _identity(path,current)!=identity: raise ValueError('模型文件身份改变')
                        if cancelled(): raise Cancelled()
                        # Hold a read-only identity handle through SQLite's
                        # commit. No file bytes are read during indexing.
                        with VerifiedFile(path) as held, self.store.connect() as db:
                            if _identity(path,held.info)!=identity: raise ValueError('模型文件身份改变')
                            held.verify()
                            old=db.execute('SELECT id,size,mtime_ns,ctime_ns,device,inode,change_ns FROM luma_model_files WHERE path=?',(str(path),)).fetchone()
                            changed=old is not None and tuple(old)[1:]!=identity
                            db.execute('''INSERT INTO luma_model_files(root_id,path,name,normalized_name,size,mtime_ns,ctime_ns,device,inode,change_ns,seen) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                              ON CONFLICT(path) DO UPDATE SET size=excluded.size,mtime_ns=excluded.mtime_ns,ctime_ns=excluded.ctime_ns,device=excluded.device,inode=excluded.inode,change_ns=excluded.change_ns,seen=excluded.seen''',(root_id,str(path),entry.name,model_name(entry.name),*identity,token))
                            file_id=db.execute('SELECT id FROM luma_model_files WHERE path=?',(str(path),)).fetchone()[0]
                            if changed: db.execute("UPDATE luma_model_files SET sha256='',version_id=NULL,status='changed' WHERE id=?",(file_id,))
                            db.execute('INSERT OR IGNORE INTO luma_model_memberships(file_id,root_id) VALUES(?,?)',(file_id,root_id))
                        count+=1
                    except (OSError,ValueError): errors+=1
                    if (count+errors)%100==0: manager.progress(job_id,processed=count,total=count,errors=errors,message='正在索引模型文件属性')
            finally:
                for frame in stack:
                    frame[3].close();frame[4].__exit__(None,None,None)
            if cancelled(): raise Cancelled()
            # A partially inaccessible traversal cannot prove files missing.
            if errors==0:
                with self.store.connect() as db:
                    db.execute('DELETE FROM luma_model_memberships WHERE root_id=? AND file_id IN (SELECT id FROM luma_model_files WHERE seen<>?)',(root_id,token))
                    db.execute('DELETE FROM luma_model_files WHERE NOT EXISTS(SELECT 1 FROM luma_model_memberships m WHERE m.file_id=luma_model_files.id)')
                    db.execute('UPDATE luma_model_files SET root_id=(SELECT MIN(root_id) FROM luma_model_memberships m WHERE m.file_id=luma_model_files.id)')
        manager.progress(job_id,processed=count,total=count,errors=errors,message='模型索引完成；未读取模型权重')

    def remember_version(self,record,*,preserve_profile=True):
        if not isinstance(record,dict): return False
        digest=record.get('matched_sha256','')
        if not isinstance(digest,str) or not _SHA.fullmatch(digest): return False
        files=record.get('files',[])
        hashes={f.get('sha256','').lower() for f in files[:200] if isinstance(f,dict) and isinstance(f.get('sha256'),str) and _SHA.fullmatch(f['sha256'])} if isinstance(files,list) else set()
        if digest.lower() not in hashes or type(record.get('version_id')) is not int or record['version_id']<1: return False
        fields=('model_id','version_id','model_name','version_name','type','model_type','base_model','family','model_variant','description','version_description','creator','files','images','matched_sha256','matched_hash_kind','updated_at')
        safe={key:record[key] for key in fields if key in record}
        words=normalize_trigger_words(record.get('trigger_words',record.get('trained_words',[])))
        safe.update(trigger_words=words,trained_words=list(words),source_url=_source_url(record))
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT record FROM luma_model_versions WHERE version_id=?',(record['version_id'],)).fetchone()
            cached=json.loads(previous[0]) if previous else {}
            # Image hash lookups receive a minimal parent object, not a profile.
            # Merge only same-parent cached profile leaves; an explicit successful
            # profile refresh can still clear fields that the server removed.
            if preserve_profile and type(safe.get('model_id')) is int and safe['model_id']>0 and cached.get('model_id')==safe['model_id']:
                for key in ('description','creator'):
                    if not safe.get(key) and cached.get(key): safe[key]=cached[key]
            encoded=json.dumps(safe,ensure_ascii=False)
            if len(encoded.encode())>1024*1024: return False
            db.execute('INSERT INTO luma_model_versions(version_id,record,updated_at) VALUES(?,?,?) ON CONFLICT(version_id) DO UPDATE SET record=excluded.record,updated_at=excluded.updated_at',(record['version_id'],encoded,time.time()))
            db.execute('DELETE FROM luma_model_version_files WHERE version_id=?',(record['version_id'],))
            db.executemany('INSERT INTO luma_model_version_files(version_id,sha256) VALUES(?,?)',[(record['version_id'],h) for h in sorted(hashes)])
            db.execute('INSERT OR IGNORE INTO luma_model_verified_hashes(version_id,sha256) VALUES(?,?)',(record['version_id'],digest.lower()))
        return True

    def sync(self,manager,job_id,payload):
        ids=payload.get('ids'); covers=payload.get('download_covers',False)
        if not isinstance(ids,list) or not 1<=len(ids)<=60 or any(type(x) is not int or x<1 for x in ids) or type(covers) is not bool: raise ValueError('请选择1至60个模型文件')
        if self.client is None: raise ValueError('Civitai 连接不可用')
        cancelled=lambda:manager.cancelled(job_id); processed=errors=0; error_message=''
        covers_saved=covers_missing=0
        versions={}; profiles={}; stop_requests=False
        with self.lock:
            for file_id in dict.fromkeys(ids):
                if cancelled(): raise Cancelled()
                try:
                    with self.store.connect() as db: row=db.execute('SELECT * FROM luma_model_files WHERE id=?',(file_id,)).fetchone()
                    if row is None: raise ValueError('模型文件不存在')
                    with _directory_guard(Path(row['path']).parent), VerifiedFile(row['path']) as file:
                        identity=_identity(file.path,file.info)
                        if identity!=tuple(row[key] for key in ('size','mtime_ns','ctime_ns','device','inode','change_ns')): raise _SourceChanged('模型文件已改变，请重新索引')
                        manager.progress(job_id,processed=processed,total=len(set(ids)),errors=errors,message='正在流式计算所选模型SHA256；可请求取消')
                        try:
                            # The full stat/change identity has just been checked
                            # against a held, read-only source handle. Reuse only
                            # a SHA computed for that unchanged indexed identity.
                            digest=row['sha256'] if isinstance(row['sha256'],str) and _SHA.fullmatch(row['sha256']) else file.digest(cancelled)
                        except (OSError,ValueError): raise _SourceChanged('模型散列期间身份改变') from None
                        if cancelled(): raise Cancelled()
                        # A completed local hash is useful even if the public
                        # service is offline; it still describes held bytes.
                        _verify_source(file)
                        with self.store.connect() as db: db.execute('UPDATE luma_model_files SET sha256=? WHERE id=?',(digest,file_id))
                        if digest not in versions:
                            versions[digest]=self.client.version_by_hash(digest,cancelled=cancelled)
                        record=versions[digest]
                        if cancelled(): raise Cancelled()
                        _verify_source(file)
                        if not isinstance(record,dict) or record.get('matched_sha256','').lower()!=digest: raise ValueError('Civitai 文件哈希不匹配')
                        profile_error=None; profile_updated=False
                        # The by-hash endpoint only embeds a small parent model
                        # object. Fetch its separate profile for author/intro.
                        if callable(getattr(self.client,'model',None)):
                            try:
                                if record['model_id'] not in profiles:
                                    profiles[record['model_id']]=self.client.model(record['model_id'],cancelled=cancelled)
                                profile=profiles[record['model_id']]
                                if not isinstance(profile,dict) or profile.get('model_id')!=record['model_id']: raise ValueError('invalid_profile')
                                record={**record,'model_name':profile.get('model_name') or record.get('model_name',''),
                                        'description':profile.get('description',''),'creator':profile.get('creator','')}
                                # Hash lookup can omit previews. Use only the exact
                                # matched version in the complete parent profile.
                                matching=[v for v in profile.get('versions',[]) if isinstance(v,dict) and v.get('version_id')==record['version_id']]
                                images=list(record.get('images') or [])
                                seen={image.get('url') for image in images if isinstance(image,dict)}
                                for version in matching[:1]:
                                    for image in version.get('images',[]):
                                        if isinstance(image,dict) and image.get('url') not in seen:
                                            images.append(image);seen.add(image.get('url'))
                                record={**record,'images':images[:32]}
                                profile_updated=True
                            except Exception as error:
                                if cancelled(): raise Cancelled() from None
                                profile_error=error
                        if cancelled(): raise Cancelled()
                        _verify_source(file)
                        if not self.remember_version(record,preserve_profile=not profile_updated): raise ValueError('Civitai 文件哈希不匹配')
                        _verify_source(file)
                        with self.store.connect() as db: db.execute("UPDATE luma_model_files SET sha256=?,version_id=?,status='synced' WHERE id=?",(digest,record['version_id'],file_id))
                    existing_cover=self.cover_path(file_id) if covers else None
                    if covers and existing_cover:
                        covers_saved+=1
                    if covers and not existing_cover:
                        from .civitai import CivitaiError
                        if not any(i.get('type')!='video' for i in record.get('images',[]) if isinstance(i,dict)) and callable(getattr(self.client,'version_by_id',None)):
                            complete=self.client.version_by_id(record['version_id'],digest,cancelled=cancelled)
                            if complete.get('model_id')!=record['model_id'] or complete.get('version_id')!=record['version_id'] or complete.get('matched_sha256')!=digest:
                                raise CivitaiError('hash_mismatch')
                            record={**record,'images':complete.get('images',[])}
                        # The checkbox authorizes a public model cover. Prefer a
                        # safe preview without silently excluding every other
                        # author-provided image; videos are not still covers.
                        images=[item for item in (record.get('images') or []) if isinstance(item,dict)
                                and item.get('url') and item.get('type')!='video']
                        images.sort(key=lambda item:bool(item.get('nsfw')))
                        data=None; last_error=None
                        for image in images[:4]:
                            if cancelled(): raise Cancelled()
                            manager.progress(job_id,processed=processed,total=len(set(ids)),errors=errors,message='正在获取模型封面')
                            try:
                                data=self.client.download_cover(image['url'],cancelled=cancelled)
                                break
                            except CivitaiError as error:
                                if error.code=='cancelled': raise Cancelled() from None
                                if error.code in ('unauthorized','credentials','rate_limited'): raise
                                last_error=error
                        if data is None:
                            if last_error is not None: raise last_error
                            covers_missing+=1
                        else:
                            if cancelled(): raise Cancelled()
                            if not isinstance(data,bytes) or len(data)>8*1024*1024: raise ValueError('封面无效')
                            self.cache_dir.mkdir(parents=True,exist_ok=True)
                            target=self.cache_dir/(digest+'.cover'); temp=self.cache_dir/(uuid.uuid4().hex+'.tmp')
                            try:
                                temp.write_bytes(data)
                                if cancelled(): raise Cancelled()
                                os.replace(temp,target)
                                covers_saved+=1
                            finally:
                                if temp.exists(): temp.unlink()
                    if profile_error is not None: raise profile_error
                except Cancelled: raise
                except Exception as error:
                    errors+=1
                    from .civitai import CivitaiError
                    if isinstance(error,CivitaiError) and error.code=='cancelled': raise Cancelled() from None
                    code=error.code if isinstance(error,CivitaiError) else ''
                    stop_requests=code in ('unauthorized','credentials','rate_limited')
                    error_message=str(CivitaiError(code)) if code else '部分文件同步失败；请重新索引或检查网络后重试'
                    # Fixed status; preserve any last valid snapshot and cover.
                    with self.store.connect() as db:
                        if isinstance(error,_SourceChanged): db.execute("UPDATE luma_model_files SET sha256='',version_id=NULL,status='changed' WHERE id=?",(file_id,))
                        else: db.execute('UPDATE luma_model_files SET status=? WHERE id=?',('sync_error_'+code if code else 'sync_error',file_id))
                processed+=1
                message='模型资料同步完成' if processed==len(set(ids)) or stop_requests else '正在计算哈希并同步模型资料'
                if covers:
                    message+=f'；封面已保存 {covers_saved} 个'
                    if covers_missing: message+=f'；{covers_missing} 个模型未提供静态封面'
                manager.progress(job_id,processed=processed,total=len(set(ids)),errors=errors,message=message+('；'+error_message if error_message else ''))
                if stop_requests: break

    def cover_path(self,model_id):
        with self.store.connect() as db: row=db.execute('SELECT path,sha256 FROM luma_model_files WHERE id=?',(_id(model_id),)).fetchone()
        return (model_covers.thumbnail(row['path'],self.cache_dir) or self._cover_for_hash(row['sha256'])) if row else None

    def _cover_for_hash(self,digest):
        if not isinstance(digest,str) or not _SHA.fullmatch(digest): return None
        path=self.cache_dir/(digest+'.cover')
        return path if path.is_file() and not path.is_symlink() else None

    def image_filters(self,model_id,match='verified'):
        if match not in ('verified','name'): raise ValueError('模型匹配类型无效')
        self.detail(_id(model_id))
        return {'model_file_id':model_id,'model_match':match}

    def rebuild_links(self,manager,job_id,payload):
        cancelled=lambda:manager.cancelled(job_id)
        ids_filter=payload.get('ids')
        if ids_filter is not None and (not isinstance(ids_filter,list) or len(ids_filter)>120 or any(type(i) is not int or i<1 for i in ids_filter)): raise ValueError('模型关联图片范围无效')
        with self.store.connect() as db: high=db.execute('SELECT COALESCE(MAX(id),0) FROM images').fetchone()[0]
        if 'highwater' in payload:
            if type(payload['highwater']) is not int or payload['highwater']<0: raise ValueError('模型关联范围无效')
            high=min(high,payload['highwater'])
        last=count=0
        while last<high:
            if cancelled(): raise Cancelled()
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                scope=' AND id IN ('+','.join('?' for _ in ids_filter)+')' if ids_filter else ''
                if ids_filter==[]: break
                ids=[r[0] for r in db.execute('SELECT id FROM images WHERE id>? AND id<=?'+scope+' ORDER BY id LIMIT 100',[last,high,*(ids_filter or [])])]
                if not ids: break
                for image_id in ids:
                    if cancelled(): raise Cancelled()
                    update_image_references(db,image_id)
                last=ids[-1]; count+=len(ids)
            manager.progress(job_id,processed=count,total=count,errors=0,message='正在更新缓存模型关联')
        manager.progress(job_id,processed=count,total=count,errors=0,message='缓存模型关联已更新；未读取原图')

    def image_lookup_hashes(self,ids):
        if not isinstance(ids,list) or not 1<=len(ids)<=120 or any(type(x) is not int or x<1 for x in ids): raise ValueError('请选择1至120张图片')
        result=set()
        with self.store.connect() as db:
            for image_id in dict.fromkeys(ids):
                for row in hash_projection(db,image_id):
                    if isinstance(row['value'],str) and re.fullmatch('[0-9a-fA-F]{64}|[0-9a-fA-F]{10}',row['value']):
                        result.add((row['value'].lower(),'sha256' if len(row['value'])==64 else 'autov2'))
        if len(result)>60: raise ValueError('本次模型哈希超过60个，请缩小选择范围')
        return [{'hash':h,'hash_kind':kind} for h,kind in sorted(result)]

    def catalog_entries(self):
        result=[]; known_roles={}
        with self.store.connect() as db:
            rows=db.execute('SELECT v.record,f.sha256 FROM luma_model_versions v JOIN luma_model_verified_hashes f ON f.version_id=v.version_id JOIN luma_model_version_files p ON p.version_id=f.version_id AND p.sha256=f.sha256 ORDER BY v.version_id,f.sha256 LIMIT 20001')
            for index,row in enumerate(rows):
                if index==20000: return []
                record=json.loads(row['record'])
                parent_type=record.get('model_type',record.get('type',''))
                roles=set()
                for file in record.get('files',[])[:200]:
                    if not isinstance(file,dict) or str(file.get('sha256','')).lower()!=row['sha256']:continue
                    if 'resource_type' in file: role=file['resource_type']
                    elif 'type' in file: role=parent_type if str(file['type']).casefold()=='model' else file['type']
                    else: role=record.get('type','')  # Legacy normalized cache records.
                    roles.add(str(role).casefold())
                known_roles.setdefault(row['sha256'],set()).update(roles or {'unknown'})
                if roles!={'checkpoint'} or record.get('family') not in FAMILIES: continue
                result.append({'kind':'hash','match':row['sha256'],'family':record['family'],'model_variant':record.get('model_variant',''),'origin':'civitai','display_name':record.get('model_name',''),'catalog_version':'civitai-cache','source_urls':[_source_url(record)]})
        return [entry for entry in result if known_roles.get(entry['match'])=={'checkpoint'}]
