"""Same-directory Civitai/rgthree/WebUI metadata and a shared PNG preview."""
import hashlib,json,os,uuid,stat
import time
from contextlib import contextmanager,ExitStack
from pathlib import Path
from .duplicates import VerifiedFile,_fingerprint
from .organizer import check_directory_chain,signature,Cancelled,reparse_point

MAX_JSON=1024*1024

class SidecarError(OSError):
    def __init__(self,code='sidecar_write'):
        self.code=code if code in ('sidecar_format','sidecar_size','sidecar_conflict','sidecar_permission','sidecar_busy','sidecar_space') else 'sidecar_write'
        super().__init__(self.code)


def error_code(error):
    if isinstance(error,SidecarError):return error.code
    win=getattr(error,'winerror',None)
    if win in (32,33):return 'sidecar_busy'
    if win in (5,19) or getattr(error,'errno',None) in (1,13,30):return 'sidecar_permission'
    if win in (39,112) or getattr(error,'errno',None)==28:return 'sidecar_space'
    return 'sidecar_write'

def _hash(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf-8')).hexdigest()

def _merge(old,new,prior,managed,prefix=''):
    result=dict(old)
    for key,value in new.items():
        field=prefix+key
        if key not in result and any(name==field or name.startswith(field+'/') for name in prior):continue
        if isinstance(value,dict) and (key not in result or isinstance(result.get(key),dict)):
            result[key]=_merge(result.get(key,{}),value,prior,managed,field+'/');continue
        current=result.get(key)
        if key not in result or prior.get(field)==_hash(current):
            result[key]=value;managed[field]=_hash(value)
    return result

def _publish(temporary,target,replace,cancelled,verify=lambda:None):
    for attempt in range(20):
        if cancelled():raise Cancelled()
        verify()
        try:
            if replace:os.replace(temporary,target)
            elif os.name=='nt':os.rename(temporary,target) # Windows rename is atomic and refuses an existing destination.
            else:os.link(temporary,target)
            return
        except OSError as error:
            if getattr(error,'winerror',None) not in (32,33) or attempt==19:raise
            time.sleep(.025)

@contextmanager
def _pinned_parent(path):
    """Pin the ancestry while allowing temporary siblings in the direct parent."""
    handles=[]
    try:
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            api=ctypes.WinDLL('kernel32',use_last_error=True)
            api.CreateFileW.argtypes=(wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE)
            api.CreateFileW.restype=wintypes.HANDLE
            api.CloseHandle.argtypes=(wintypes.HANDLE,)
            current=path
            while current!=current.parent:
                handle=api.CreateFileW(str(current),0x80,3 if current==path else 1,None,3,0x02200000,None)
                if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
                handles.append((api,handle));current=current.parent
        check_directory_chain(path)
        yield
    finally:
        for api,handle in reversed(handles):api.CloseHandle(handle)


@contextmanager
def _exclusive_json(path):
    """The same Windows handle reads and updates an existing ordinary file."""
    import ctypes,msvcrt
    from ctypes import wintypes
    before=signature(path)
    if before.st_nlink!=1:raise SidecarError('sidecar_link')
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.CreateFileW.argtypes=(wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE)
    api.CreateFileW.restype=wintypes.HANDLE;api.CloseHandle.argtypes=(wintypes.HANDLE,)
    handle=api.CreateFileW(str(path),0xc0000000,0,None,3,0x00200000,None)
    if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    try:descriptor=msvcrt.open_osfhandle(handle,os.O_RDWR|os.O_BINARY)
    except BaseException:
        api.CloseHandle(handle);raise
    with os.fdopen(descriptor,'r+b',buffering=0) as stream:
        if _fingerprint(os.fstat(stream.fileno()))!=_fingerprint(before) or _fingerprint(signature(path))!=_fingerprint(before):
            raise SidecarError('sidecar_changed')
        yield stream


def _durable(path,data):
    with path.open('xb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())


def _rewrite(stream,data):
    stream.seek(0);remaining=memoryview(data)
    while remaining:
        written=stream.write(remaining)
        if not written:raise SidecarError('sidecar_short_write')
        remaining=remaining[written:]
    stream.truncate();stream.flush();os.fsync(stream.fileno())


def _update_existing(stream,path,raw,original,cancelled):
    # In-place updates are exclusive, not crash-atomic. Persist exact old bytes
    # first; an interrupted update or a failed rollback retains this recovery copy.
    backup=path.with_name('.luma-model-'+uuid.uuid4().hex+'.bak');keep_backup=False
    try:
        _durable(backup,original)
        if cancelled():raise Cancelled()
        try:_rewrite(stream,raw)
        except BaseException:
            try:_rewrite(stream,original)
            except BaseException:
                keep_backup=True;raise SidecarError('sidecar_recovery') from None
            raise
    finally:
        if not keep_backup:backup.unlink(missing_ok=True)


def _json(path,value,filename,cancelled):
    if cancelled():raise Cancelled()
    with ExitStack() as stack:
        stack.enter_context(_pinned_parent(path.parent))
        old={};stamp=None;stream=None;original=b'';readonly=False
        if path.exists():
            info=path.lstat()
            if not stat.S_ISREG(info.st_mode) or reparse_point(path,info) or info.st_nlink!=1:raise SidecarError('sidecar_conflict')
            readonly=os.name=='nt' and bool(info.st_file_attributes&1)
            if os.name=='nt' and not readonly:
                for attempt in range(20):
                    if cancelled():raise Cancelled()
                    try:stream=stack.enter_context(_exclusive_json(path));break
                    except OSError as error:
                        if getattr(error,'winerror',None) not in (32,33) or attempt==19:raise
                        time.sleep(.025)
                stamp=os.fstat(stream.fileno())
                if stamp.st_size>MAX_JSON:raise SidecarError('sidecar_size')
                original=stream.read(MAX_JSON+1)
            else:
                with VerifiedFile(path,parent_writes=True) as source:
                    if source.info.st_size>MAX_JSON:raise SidecarError('sidecar_size')
                    original=source.file.read(MAX_JSON+1);stamp=source.info;source.verify()
            try:old=json.loads(original.decode('utf-8-sig')) if original.decode('utf-8-sig').strip() else {}
            except (ValueError,UnicodeError):raise SidecarError('sidecar_format') from None
            if not isinstance(old,dict):raise SidecarError('sidecar_format')
        owner=old.get('_luma',{})
        if not isinstance(owner,dict):owner={}
        if owner.get('file') not in (None,filename):raise SidecarError('sidecar_conflict')
        prior=owner.get('managed',{});prior=prior if isinstance(prior,dict) else {}
        managed=dict(prior);value=_merge(old,value,prior,managed);value['_luma']={'schema':1,'file':filename,'managed':managed}
        raw=json.dumps(value,ensure_ascii=False,indent=2).encode('utf-8')
        if len(raw)>MAX_JSON:raise SidecarError('sidecar_size')
        if raw==original:return
        if readonly:raise SidecarError('sidecar_permission')
        temporary=path.with_name('.luma-model-'+uuid.uuid4().hex+'.tmp')
        try:
            _durable(temporary,raw)
            if stream is not None:
                _update_existing(stream,path,raw,original,cancelled)
            else:
                def verify():
                    check_directory_chain(path.parent)
                    if stamp is not None and _fingerprint(signature(path))!=_fingerprint(stamp):raise SidecarError('sidecar_changed')
                _publish(temporary,path,stamp is not None,cancelled,verify)
        finally:temporary.unlink(missing_ok=True)

def write(model_path,record,digest,cover=None,cancelled=lambda:False):
    path=Path(model_path);check_directory_chain(path.parent)
    files=[{'name':f.get('name',''),'type':f.get('type') or 'Model','hashes':{'SHA256':f.get('sha256',''),'AutoV2':f.get('autov2','')}} for f in record.get('files',[]) if isinstance(f,dict)]
    words=record.get('trigger_words',record.get('trained_words',[]))
    model={'id':record.get('model_id'),'name':record.get('model_name',''),'type':record.get('model_type') or record.get('type',''),'description':record.get('description','')}
    info={'id':record.get('version_id'),'modelId':record.get('model_id'),'name':record.get('version_name',''),'modelName':record.get('model_name',''),'modelType':record.get('type',''),'baseModel':record.get('base_model',''),'description':record.get('version_description',''),'trainedWords':words,'model':model,'files':files,'images':record.get('images',[])}
    link=record.get('source_url','')
    rg={'name':record.get('model_name',''),'type':record.get('type',''),'baseModel':record.get('base_model',''),'sha256':digest,'links':[link] if link else [],'trainedWords':[{'word':w,'civitai':True} for w in words],'raw':{'civitai':{**info,'_sha256':digest}}}
    web={'description':'\n\n'.join(v for v in (record.get('description',''),record.get('version_description','')) if v),'activation text':', '.join(words),'baseModel':record.get('base_model',''),'modelVersionId':record.get('version_id'),'modelType':record.get('type','')}
    base=record.get('base_model','')
    if base.startswith('SDXL') or base in ('Pony','Illustrious'):web['sd version']='SDXL'
    elif base.startswith('SD 1'):web['sd version']='SD1'
    elif base.startswith('SD 2'):web['sd version']='SD2'
    failures=[]
    for target,value in ((path.with_name(path.stem+'.civitai.info'),info),(path.with_name(path.stem+'.json'),web),(path.with_name(path.name+'.rgthree-info.json'),rg)):
        if cancelled():raise Cancelled()
        try:_json(target,value,path.name,cancelled)
        except OSError as error:failures.append(error_code(error))
    target=path.with_name(path.stem+'.png')
    if cover is not None and not target.exists():
        if not isinstance(cover,bytes) or len(cover)>8*1024*1024 or not cover.startswith(b'\x89PNG\r\n\x1a\n'):raise SidecarError('sidecar_cover')
        temporary=target.with_name('.luma-model-'+uuid.uuid4().hex+'.tmp')
        try:
            with temporary.open('xb') as file:file.write(cover);file.flush();os.fsync(file.fileno())
            _publish(temporary,target,False,cancelled,lambda:check_directory_chain(path.parent))
        except OSError as error:failures.append(error_code(error))
        finally:temporary.unlink(missing_ok=True)
    if failures:raise SidecarError(failures[0])
