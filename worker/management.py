"""Stage6 facade: explicit index/model actions, never destructive file commands."""
import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import closing
from pathlib import Path

from worker.ownership import no_reparse
from worker.read_catalog import detail, query

_lock = threading.RLock()
JOB_FIELDS = ('id','kind','status','processed','total','errors','cancel_requested','created_at','updated_at')
TASKS = ('import','metadata_refresh','install_model','classify_contents','reclassify_models','model_scan','model_sync','model_links','civitai_image_lookup','file_preview','organize','undo','remove_root','duplicate_scan','duplicate_cleanup','duplicate_undo','duplicate_purge','duplicate_recovery','file_recovery')

class ManagementError(ValueError):
    def __init__(self, code='invalid_request'):
        self.code=code
        super().__init__(code)

def fields(value, allowed, required=()):
    if not isinstance(value,dict) or set(value)-set(allowed) or set(required)-set(value):raise ManagementError()

def integer(value, high=2**63-1):
    if type(value) is not int or not 1<=value<=high:raise ManagementError()
    return value

def text(value, high=32767, empty=False):
    if not isinstance(value,str) or len(value)>high or '\0' in value or (not empty and not value):raise ManagementError()
    return value

def ids(value, high=120):
    if not isinstance(value,list) or not 1<=len(value)<=high:raise ManagementError()
    return list(dict.fromkeys(integer(v) for v in value))

def boolean(value):
    if type(value) is not bool:raise ManagementError()
    return value

def job(value):
    result = {k:value[k] for k in JOB_FIELDS if k in value}
    if value.get('kind') == 'model_sync':
        from backend.civitai import sync_feedback
        from backend.model_sync_scope import ISSUE_CODES
        result['feedback'] = sync_feedback(value.get('message'), value.get('status'))
        result['issues']=[{'code':v['code'],'count':v['count']} for v in value.get('issues',[])[:32] if isinstance(v,dict) and v.get('code') in ISSUE_CODES and type(v.get('count')) is int and 0<v['count']<=2**63-1]
    return result

def op(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{32}',value):raise ManagementError()
    return value

def receipt_path(service, ident):
    op(ident)
    path=service.store.directory/'management-operations.sqlite3'
    for suffix in ('','-wal','-shm'):
        candidate=Path(str(path)+suffix);no_reparse(candidate)
        if candidate.exists() and candidate.stat().st_nlink!=1:raise ManagementError('conflict')
    return path

def receipt_db(path):
    db=sqlite3.connect(path,timeout=3)
    db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
    db.execute('CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,state TEXT NOT NULL,fingerprint TEXT NOT NULL,action TEXT NOT NULL,result TEXT)')
    return db

def status(service, ident):
    with _lock:
        p=receipt_path(service,ident)
        if not p.exists():return {'state':'unknown'}
        with closing(receipt_db(p)) as db:
            row=db.execute('SELECT state,fingerprint,action,result FROM operations WHERE id=?',(ident,)).fetchone()
        if not row:return {'state':'unknown'}
        value={'state':row[0],'fingerprint':row[1],'action':row[2]}
        if row[3] is not None:value['result']=json.loads(row[3])
        return value

def publish(path, ident, value):
    with closing(receipt_db(path)) as db, db:
        db.execute('INSERT INTO operations VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,result=excluded.result',
            (ident,value['state'],value['fingerprint'],value['action'],json.dumps(value['result'],ensure_ascii=False,allow_nan=False) if 'result' in value else None))
        db.execute("UPDATE operations SET state='expired',result=NULL WHERE state='completed' AND rowid NOT IN (SELECT rowid FROM operations WHERE state='completed' ORDER BY rowid DESC LIMIT 128)")

def read(service, section, a, cancelled):
    if section.startswith('files.'):
        from .file_ops import read as file_read
        return file_read(service,section,a)
    if section=='runtime':
        fields(a,())
        from .runtime_check import check
        return check()
    if section in ('tags','vision','modelRoots','registry','credential','settings','jobs','diagnosis'):
        fields(a,())
        if section=='tags':return service.content_tags()
        if section=='vision':return service.vision_status()
        if section=='modelRoots':return service.model_roots()[:200]
        if section=='registry':return service.model_mappings()
        if section=='credential':return service.civitai_status()
        if section=='settings':return {k:v for k,v in service.settings().items() if k in ('theme','density','sort','showTags','thumbnailSize','reduceMotion','thumbnailCacheMiB','thumbnail_cache_limit_bytes')}
        if section=='jobs':return [job(j) for j in service.jobs()][:100]
        return service.diagnose_models(cancelled=cancelled.is_set)
    if section=='labels':
        fields(a,('ids',),('ids',));return service.content_label_state(ids(a['ids']))
    if section=='models':
        fields(a,('rootId','query','cursor','types','families'))
        f={'q':text(a.get('query',''),1024,True)}
        from backend.model_library import model_selections
        for dimension in ('types','families'):
            if dimension in a:
                try: f[dimension]=model_selections(a[dimension],dimension)
                except ValueError: raise ManagementError() from None
        if a.get('rootId') is not None:f['root_id']=integer(a['rootId'])
        cursor=a.get('cursor')
        if cursor is not None:text(cursor,2048)
        return service.models(f,cursor,60)
    if section in ('modelDetail','modelImages','modelCover'):
        fields(a,('modelId','match','cursor') if section=='modelImages' else ('modelId',),('modelId',));ident=integer(a['modelId'])
        if section=='modelDetail':return service.model_detail(ident)
        if section=='modelImages':
            match=a.get('match','verified')
            if match not in ('verified','candidate'):raise ManagementError()
            cursor=a.get('cursor')
            if cursor is not None:text(cursor,2048)
            filters=service.model_image_filters(ident,'name' if match=='candidate' else match)
            filters['sort']='name'
            return query(service,filters,cursor,60)
        return cover(service,ident)
    raise ManagementError('method_not_allowed')

def cover(service, ident):
    path=service.model_cover_path(ident)
    if not path:return None
    from worker.image_read import ReadImage
    base=service.store.directory
    p=Path(path)
    if not p.is_relative_to(base/'model-covers'):raise ManagementError('asset_boundary')
    with ReadImage(p) as f:
        if f.info.st_size>16*1024*1024:raise ManagementError('unsupported')
        checksum=f.digest();f.file.seek(0);data=f.file.read();f.verify()
        target=base/'thumbnails'/'model-covers'/(checksum+'.img');no_reparse(target)
        target.parent.mkdir(exist_ok=True,parents=True)
        valid=False
        if target.exists():
            with ReadImage(target) as existing:valid=existing.info.st_size==len(data) and existing.digest()==checksum;existing.verify()
        if not valid:
            temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
            with temporary.open('xb') as output:output.write(data)
            os.replace(temporary,target)
        return {'resourceId':checksum,'relativePath':target.relative_to(base).as_posix(),'sha256':checksum,'bytes':len(data)}

def scope(a):
    fields(a,('ids','rootId','scope'))
    if sum(k in a for k in ('ids','rootId','scope'))!=1:raise ManagementError()
    if 'ids' in a:return {'ids':ids(a['ids'])}
    if 'rootId' in a:return {'root_id':integer(a['rootId'])}
    if a['scope']!='all':raise ManagementError()
    return {'scope':'all'}

def prepare(service, action, a):
    if action.startswith('files.'):
        from .file_ops import prepare as file_prepare
        return file_prepare(service,action,a)
    """Validate the complete shape before a service method can have side effects."""
    if action=='catalog.import':
        fields(a,('path','recursive'),('path','recursive'));path=text(a['path']);recursive=boolean(a['recursive'])
        if not Path(path).is_absolute():raise ManagementError()
        return lambda:job(service.import_directory(path,recursive))
    if action=='metadata.refresh':
        fields(a,('ids',),('ids',));selected=ids(a['ids']);return lambda:job(service.reread_metadata(selected))
    if action=='image.patch':
        fields(a,('imageId','identity','favorite','rating'),('imageId','identity'));ident=integer(a['imageId']);identity=text(a['identity'],64)
        changes={k:a[k] for k in ('favorite','rating') if k in a}
        if not changes:raise ManagementError()
        if 'favorite' in changes:boolean(changes['favorite'])
        if 'rating' in changes and (type(changes['rating']) is not int or not 0<=changes['rating']<=5):raise ManagementError()
        def patch():
            from desktop.service import INDEX_TASKS
            with service._submission_lock:
                service._ensure_no_active(INDEX_TASKS)
                detail(service,ident,identity)
                service.patch(ident,changes)
                return {'imageId':ident,**changes}
        return patch
    if action=='settings.save':
        fields(a,('values',),('values',));v=a['values'];fields(v,('theme','density','sort','showTags','thumbnailSize','reduceMotion','thumbnailCacheMiB'))
        if 'theme' in v and v['theme'] not in ('light','dark','system'):raise ManagementError()
        if 'density' in v and v['density'] not in ('compact','comfortable'):raise ManagementError()
        if 'sort' in v:
            from backend.store import SORTS
            if v['sort'] not in SORTS:raise ManagementError()
        for k in ('showTags','reduceMotion'):
            if k in v:boolean(v[k])
        if 'thumbnailSize' in v and (type(v['thumbnailSize']) is not int or not 64<=v['thumbnailSize']<=1200):raise ManagementError()
        if 'thumbnailCacheMiB' in v and (type(v['thumbnailCacheMiB']) is not int or not 64<=v['thumbnailCacheMiB']<=8192):raise ManagementError()
        def settings():
            result=service.put_settings(v);return {k:result.get(k) for k in v}
        return settings
    if action=='tags.save':
        fields(a,('definitions',),('definitions',));d=a['definitions']
        if not isinstance(d,list) or len(d)>128:raise ManagementError()
        return lambda:service.put_content_tags(d)
    if action=='labels.apply':
        fields(a,('ids','changes','snapshot'),('ids','changes','snapshot'));selected=ids(a['ids']);snapshot=text(a['snapshot'],64)
        if not isinstance(a['changes'],list) or not 1<=len(a['changes'])<=128:raise ManagementError()
        for change in a['changes']:
            fields(change,('tag_id','action'),('tag_id','action'));integer(change['tag_id'])
            if change['action'] not in ('include','exclude','auto'):raise ManagementError()
        if not re.fullmatch('[a-f0-9]{64}',snapshot):raise ManagementError()
        return lambda:service.apply_content_label_overrides(selected,a['changes'],snapshot)
    if action in ('vision.classify','models.reclassify'):
        selected=scope(a)
        return lambda:job((service.classify_contents if action=='vision.classify' else service.reclassify_models)(**selected))
    if action in ('vision.install','models.rebuild','credential.clear','credential.test'):
        fields(a,())
        fn={'vision.install':service.install_vision_model,'models.rebuild':service.rebuild_model_links,'credential.clear':service.clear_civitai,'credential.test':service.test_civitai_connection}[action]
        return lambda:job(fn()) if action in ('vision.install','models.rebuild') else fn()
    if action in ('credential.set','credential.connect'):
        fields(a,('token','remember'),('token','remember'));token=text(a['token'],512);remember=boolean(a['remember'])
        return lambda:service.connect_civitai(token,remember) if action=='credential.connect' else service.configure_civitai(token,remember)
    if action in ('models.add','models.remove','models.scan','models.sync','models.lookup'):
        if action=='models.add':
            fields(a,('path',),('path',));path=text(a['path'])
            if not Path(path).is_absolute():raise ManagementError()
            return lambda:service.add_model_root(path)
        if action in ('models.remove','models.scan'):
            fields(a,('rootId',),('rootId',));ident=integer(a['rootId'])
            return lambda:service.remove_model_root(ident) if action=='models.remove' else job(service.scan_model_directory(ident))
        if action=='models.sync' and 'scope' in a:
            fields(a,('scope','downloadCovers'),('scope',));s=a['scope'];fields(s,('rootId','query','types','families'))
            from backend.model_library import model_selections
            filters={'q':text(s.get('query',''),1024,True)}
            if s.get('rootId') is not None:filters['root_id']=integer(s['rootId'])
            for dimension in ('types','families'):
                if dimension in s:
                    try:filters[dimension]=model_selections(s[dimension],dimension)
                    except ValueError:raise ManagementError() from None
            covers=boolean(a.get('downloadCovers',False))
            return lambda:job(service.sync_model_scope(filters,covers))
        fields(a,('ids','downloadCovers') if action=='models.sync' else ('ids',),('ids',));selected=ids(a['ids'],60)
        if action=='models.lookup' and not service.model_library.image_lookup_hashes(selected):raise ManagementError('model_hash_missing')
        covers=boolean(a.get('downloadCovers',False))
        return lambda:job(service.sync_models(selected,covers) if action=='models.sync' else service.lookup_image_models(selected))
    if action=='registry.save':
        fields(a,('entries',),('entries',));entries=a['entries']
        if not isinstance(entries,list) or len(entries)>1000:raise ManagementError()
        return lambda:service.put_model_mappings(entries)
    if action=='job.cancel':
        fields(a,('jobId',),('jobId',));ident=text(a['jobId'],100)
        def cancel():
            if service.job(ident)['kind'] not in TASKS:raise ManagementError('method_not_allowed')
            return job(service.cancel(ident))
        return cancel
    raise ManagementError('method_not_allowed')

def write(service, action, args, ident, cancelled):
    op(ident)
    with _lock:
        action_fn=prepare(service,action,args)
        if cancelled.is_set():raise ManagementError('cancelled')
        path=receipt_path(service,ident)
        fingerprint=hashlib.sha256(json.dumps([action,args],sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
        current=status(service,ident)
        if current['state']!='unknown':
            if current.get('fingerprint')!=fingerprint:raise ManagementError('conflict')
            if current['state']=='completed':return current['result']
            raise ManagementError('conflict') # pending across crash is uncertain; NEVER resubmit.
        # Full results bounded to128; small durable tombstones prevent old IDs replaying.
        publish(path,ident,{'state':'pending','fingerprint':fingerprint,'action':action})
        try:
            result=action_fn()
            publish(path,ident,{'state':'completed','fingerprint':fingerprint,'action':action,'result':result})
            return result
        except Exception as error:
            publish(path,ident,{'state':'failed','fingerprint':fingerprint,'action':action})
            if type(error) is ValueError:raise ManagementError('stale_identity' if action=='labels.apply' else 'invalid_request') from None
            if getattr(error,'code','')=='invalid':raise ManagementError() from None
            raise
