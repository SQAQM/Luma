"""Preview-bound file operations. No original access on construction or ordinary reads."""
import hashlib,json,os,sqlite3,stat,threading,time,uuid
from contextlib import contextmanager
from pathlib import Path
from backend.organizer import Cancelled,absolute_directory,PENDING_MOVE_BUNDLES_SQL,PENDING_UNDO_SIDES_SQL
from backend.duplicates import VerifiedFile
from desktop.service import INDEX_TASKS
from worker.ownership import no_reparse
from worker.management import ManagementError,fields,integer,text,ids,boolean,job

KINDS=('organize','organize_undo','root_remove','duplicate_cleanup','duplicate_undo','duplicate_purge','recover_moves','recover_duplicates')
MOVE_STATES="('prepared','copying','verified','published','needs_review')"
SIDE_PENDING=PENDING_MOVE_BUNDLES_SQL
def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()
def token(file):
    info=file.info
    return [info.st_size,info.st_mtime_ns,info.st_ctime_ns,info.st_dev,info.st_ino,info.st_nlink,file.change_ns]

class FileOperations:
    def __init__(self,service):
        self.service=service;self.path=service.store.directory/'file-reviews.sqlite3'
        self._db_lock=threading.RLock()
        service.manager.handlers.update(file_preview=self.build,organize=self.organize,undo=self.undo,file_recovery=self.recover)
    @contextmanager
    def db(self):
        # Keep alias validation, open and last-connection WAL cleanup in one owner scope.
        with self._db_lock:
            with self._connection() as db:yield db
    @contextmanager
    def _connection(self):
        for suffix in ('','-wal','-shm','-journal'):
            path=Path(str(self.path)+suffix);no_reparse(path)
            try:info=path.lstat()
            except FileNotFoundError:continue # SQLite removes WAL/SHM on last close.
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ManagementError('conflict')
        db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
            db.executescript('''CREATE TABLE IF NOT EXISTS reviews(
             id TEXT PRIMARY KEY,kind TEXT NOT NULL,options TEXT NOT NULL,state TEXT NOT NULL,
             plan_id TEXT,job_id TEXT,execution_job_id TEXT,fingerprint TEXT,backend_hash TEXT,
             revision INTEGER,rules_hash TEXT,total INTEGER DEFAULT 0,actionable INTEGER DEFAULT 0,
             skipped INTEGER DEFAULT 0,conflicts INTEGER DEFAULT 0,created_at REAL NOT NULL,details TEXT NOT NULL DEFAULT '{}');
             CREATE TABLE IF NOT EXISTS review_items(review_id TEXT,seq INTEGER,source TEXT,target TEXT,
             keep_path TEXT,status TEXT,detail TEXT,image_id INTEGER,fs TEXT,PRIMARY KEY(review_id,seq));
             CREATE INDEX IF NOT EXISTS review_source ON review_items(review_id,source);
             CREATE TABLE IF NOT EXISTS execution_files(job_id TEXT,path TEXT,fs TEXT NOT NULL,PRIMARY KEY(job_id,path));''')
            if 'details' not in {r[1] for r in db.execute('PRAGMA table_info(reviews)')}:db.execute("ALTER TABLE reviews ADD COLUMN details TEXT NOT NULL DEFAULT '{}'")
            with db:yield db
        finally:db.close()
    def revision(self):
        with self.service.store.connect() as db:return db.execute('SELECT revision FROM query_state WHERE id=1').fetchone()[0]
    def rules_hash(self):return digest(self.service.rules())
    def pending(self):
        with self.service.store.connect() as db:
            moves=db.execute(f'SELECT COUNT(*) FROM journal WHERE state IN {MOVE_STATES}').fetchone()[0]
            side=db.execute('SELECT COUNT(*) FROM ('+SIDE_PENDING+')').fetchone()[0]
            undo=db.execute('SELECT COUNT(*) FROM ('+PENDING_UNDO_SIDES_SQL+')').fetchone()[0]
        return moves+side+undo
    def ensure(self,recovery=False):
        self.service._ensure_open()
        if not recovery:
            self.service._ensure_ready()
            if self.pending():raise ManagementError('recovery_required')
        self.service._ensure_no_active(INDEX_TASKS,allow_duplicate_recovery=recovery)
    def row(self,ident):
        with self.db() as db:row=db.execute('SELECT * FROM reviews WHERE id=?',(ident,)).fetchone()
        if row is None:raise ManagementError('not_found')
        return dict(row)
    def plan_ready(self,row):
        table={'organize':'plans','duplicate_cleanup':'duplicate_plans','duplicate_purge':'duplicate_plans','root_remove':'removal_plans'}.get(row['kind'])
        if table is None:return True
        with self.service.store.connect() as db:plan=db.execute('SELECT status FROM '+table+' WHERE id=?',(row['plan_id'],)).fetchone()
        return plan is not None and plan['status']=='ready'
    def header(self,row):
        result={k:row[k] for k in ('id','kind','state','job_id','execution_job_id','fingerprint','total','actionable','skipped','conflicts','created_at')}
        result['options']=json.loads(row['options']);result['details']=json.loads(row['details']);result['releasesSpace']=row['kind']=='duplicate_purge'
        if row['state']=='ready' and (row['revision']!=self.revision() or row['rules_hash']!=self.rules_hash() or not self.plan_ready(row)):result['state']='stale'
        if row['state']=='building' and row['job_id']:
            current=self.service.manager.get(row['job_id'])
            if current and current['status'] not in ('queued','running'):result['state']='interrupted'
        return result
    def state(self):
        with self.db() as db:reviews=[self.header(dict(r)) for r in db.execute('SELECT * FROM reviews ORDER BY created_at DESC LIMIT 30')]
        with self.service.store.connect() as db:
            moves=[dict(r) for r in db.execute("SELECT id,kind,status,processed,total,errors FROM jobs WHERE kind='organize' ORDER BY created_at DESC LIMIT 30")]
            needs=db.execute("SELECT COUNT(*) FROM journal WHERE state='needs_review'").fetchone()[0]
        roots=[{k:r[k] for k in ('id','name','count') if k in r} for r in self.service.roots()[:200]]
        rules=self.service.rules()
        return dict(roots=roots,rules=rules[:25],rulesTotal=len(rules),rulesHash=self.rules_hash(),reviews=reviews,
                    history=moves,quarantine=self.service.duplicate_cleanup_history(),scan=self.service.duplicate_latest_scan(),
                    recovery={'moves':self.pending(),'needsReview':needs,'duplicates':self.service.duplicate_recovery_status()['total']})
    def items(self,ident,cursor):
        self.row(ident);last=0 if cursor is None else integer(cursor)
        rows=[];budget=0;next_cursor=None
        with self.db() as db:
            for row in db.execute('SELECT seq,source,target,keep_path,status,detail,image_id FROM review_items WHERE review_id=? AND seq>? ORDER BY seq LIMIT 61',(ident,last)):
                item=dict(row);size=len(json.dumps(item,ensure_ascii=False).encode())
                if rows and (len(rows)==60 or budget+size>1024*1024):next_cursor=rows[-1]['seq'];break
                rows.append(item);budget+=size
        return {'items':rows,'next_cursor':next_cursor,'total':self.row(ident)['total']}
    def options(self,kind,value):
        if kind not in KINDS:raise ManagementError()
        if kind=='organize':
            fields(value,('mode','destination','scope','ids','rootId'),('mode','destination','scope'))
            if value['mode'] not in ('model','tag','content_tag','artist','format'):raise ManagementError()
            path=absolute_directory(text(value['destination']))
            if path==self.service.store.directory or self.service.store.directory in path.parents:raise ManagementError()
            scope=value['scope']
            if scope not in ('all','selected','root'):raise ManagementError()
            if scope=='selected':ids(value.get('ids'))
            elif 'ids' in value:raise ManagementError()
            if scope=='root':integer(value.get('rootId'))
            elif 'rootId' in value:raise ManagementError()
        elif kind=='root_remove':fields(value,('rootId',),('rootId',));integer(value['rootId'])
        elif kind in ('organize_undo','duplicate_undo','duplicate_purge'):
            fields(value,('jobId',),('jobId',));text(value['jobId'],100)
            task=self.service.job(value['jobId']);expected='organize' if kind=='organize_undo' else 'duplicate_cleanup'
            if task['kind']!=expected or task['status'] in ('queued','running'):raise ManagementError()
        elif kind=='duplicate_cleanup':fields(value,('scanId',),('scanId',));text(value['scanId'],100)
        else:fields(value,())
        return value
    def preview(self,kind,options):
        with self.service._operation(),self.service._submission_lock:
            self.options(kind,options);self.ensure(kind.startswith('recover_'))
            ident=uuid.uuid4().hex
            with self.db() as db:db.execute('INSERT INTO reviews(id,kind,options,state,created_at) VALUES(?,?,?,?,?)',(ident,kind,json.dumps(options,ensure_ascii=False),'building',time.time()))
            task=self.service.manager.submit('file_preview',{'reviewId':ident})
            with self.db() as db:db.execute('UPDATE reviews SET job_id=? WHERE id=?',(task['id'],ident))
            return {**job(task),'reviewId':ident}
    def _backend_rows(self,kind,options,plan_id):
        with self.service.store.connect() as db:
            if kind=='organize':
                cursor=db.execute('SELECT * FROM plan_items WHERE plan_id=? ORDER BY id',(plan_id,))
            elif kind.startswith('duplicate_') and kind!='duplicate_undo':
                cursor=db.execute('SELECT * FROM duplicate_plan_items WHERE plan_id=? ORDER BY id',(plan_id,))
            elif kind=='organize_undo':
                cursor=db.execute("SELECT * FROM journal WHERE job_id=? AND state='committed' ORDER BY id",(options['jobId'],))
            elif kind=='duplicate_undo':
                cursor=db.execute("SELECT * FROM duplicate_journal WHERE job_id=? AND state='committed' ORDER BY id",(options['jobId'],))
            elif kind=='root_remove':
                cursor=db.execute('SELECT i.id,i.path AS source,(SELECT COUNT(*) FROM image_roots x WHERE x.image_id=i.id) AS owners FROM images i JOIN image_roots r ON r.image_id=i.id WHERE r.root_id=? ORDER BY i.id',(options['rootId'],))
            elif kind=='recover_moves':
                cursor=db.execute(f'SELECT * FROM journal WHERE state IN {MOVE_STATES} ORDER BY id')
            else:
                cursor=db.execute("SELECT * FROM duplicate_journal WHERE state IN ('prepared','moved','restore_prepared','purge_prepared','needs_review') ORDER BY id")
            while batch:=cursor.fetchmany(100):
                for row in batch:yield dict(row)
            if kind=='recover_moves':
                cursor=db.execute(SIDE_PENDING+' ORDER BY p.id')
                while batch:=cursor.fetchmany(100):
                    for row in batch:yield {'pending_companions':dict(row)}
                cursor=db.execute(PENDING_UNDO_SIDES_SQL+' ORDER BY s.id')
                while batch:=cursor.fetchmany(100):
                    for row in batch:yield {'pending_undo_side':dict(row)}
    def snapshot(self,kind,options,plan_id):
        hashed=hashlib.sha256()
        for row in self._backend_rows(kind,options,plan_id):hashed.update(json.dumps(row,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode())
        return hashed.hexdigest()
    def _records(self,kind,row):
        if 'pending_undo_side' in row:
            side=row['pending_undo_side']
            yield dict(source=side['destination'],target=side['source'],status='ready',detail='恢复中断撤销的伴随文件',image_id=side['image_id'])
            yield dict(source=side['restored_image'],target=side['restored_image'],status='reference',detail='已还原图片，复核文件束')
        elif 'pending_companions' in row:
            plan=row['pending_companions']
            yield dict(source=plan['destination'],target=plan['destination'],status='reference',detail='已移动图片，恢复时复核')
            for side in json.loads(plan['companions']):
                yield dict(source=side['source'],target=side['destination'],status='ready',detail='中断侧车恢复')
                yield dict(source=side['destination'],target=side['destination'],status='reference',detail='已存在侧车目标，复核日志与字节')
        elif kind=='organize':
            yield dict(source=row['source'],target=row['destination'],status=row['status'],detail=row['category'],image_id=row['image_id'])
            for side in json.loads(row['companions']):yield dict(source=side['source'],target=side['destination'],status=row['status'],detail='伴随文件',image_id=row['image_id'])
        elif kind=='root_remove':
            yield dict(source=row['source'],target='',status='shared' if row['owners']>1 else 'ready',detail='重叠目录保留索引' if row['owners']>1 else '仅移除索引，不删文件',image_id=row['id'])
        elif kind in ('organize_undo','duplicate_undo'):
            yield dict(source=row['destination'],target=row['source'],keep_path=row.get('keep_path',''),status='ready',detail='还原到原路径，不覆盖',image_id=row['image_id'])
        elif kind.startswith('recover_'):
            yield dict(source=row['source'],target=row['destination'],status='ready',detail=row['state'],image_id=row['image_id'])
            if row.get('temp_path'):yield dict(source=row['temp_path'],target='',status='reference',detail='中断临时副本')
            if row['destination']:yield dict(source=row['destination'],target=row['destination'],status='reference',detail='恢复时复核目标')
        else:
            yield dict(source=row['source'],target=row['destination'],keep_path=row['keep_path'],status=row['status'],detail='永久清空不可撤销' if kind=='duplicate_purge' else '移入可恢复区，不释放空间',image_id=row['image_id'])
    def build(self,manager,job_id,payload):
        ident=payload['reviewId'];review=self.row(ident);kind=review['kind'];options=json.loads(review['options']);plan_id=None
        cancelled=lambda:manager.cancelled(job_id) or self.service._closing.is_set()
        try:
            if kind=='organize':
                plan=self.service.organizer.preview(options['mode'],options['destination'],options.get('ids'),cancelled,root_id=options.get('rootId'));plan_id=plan['id']
            elif kind=='root_remove':plan=self.service.store.preview_remove_root(options['rootId'],job_id=job_id);plan_id=plan['id']
            elif kind=='duplicate_cleanup':plan_id=self.service.duplicates.preview_cleanup(options['scanId'],cancelled=cancelled)['id']
            elif kind=='duplicate_purge':plan_id=self.service.duplicates.preview_purge(options['jobId'],cancelled=cancelled)['id']
            rows=[];seq=0;actionable=skipped=conflicts=0
            for raw in self._backend_rows(kind,options,plan_id):
                for item in self._records(kind,raw):
                    if cancelled():raise Cancelled()
                    fs=None
                    if kind in ('organize','organize_undo','recover_moves'):
                        try:
                            path=Path(item['source'])
                            if path==self.service.store.directory or self.service.store.directory in path.parents:raise ValueError('internal source')
                            with VerifiedFile(path) as file:fs=token(file)
                        except (OSError,ValueError):
                            if kind=='organize':item['status']='skipped';item['detail']='文件身份/安全句柄不可用，保留'
                    seq+=1;state=item['status'];actionable+=state=='ready';skipped+=state in ('skipped','shared');conflicts+=state=='conflict'
                    rows.append((ident,seq,item['source'],item['target'],item.get('keep_path',''),state,item.get('detail',''),item.get('image_id'),json.dumps(fs) if fs else None))
                    if len(rows)>=100:
                        with self.db() as db:db.executemany('INSERT INTO review_items VALUES(?,?,?,?,?,?,?,?,?)',rows)
                        rows=[];manager.progress(job_id,processed=seq)
            if rows:
                with self.db() as db:db.executemany('INSERT INTO review_items VALUES(?,?,?,?,?,?,?,?,?)',rows)
            if cancelled():raise Cancelled()
            details={}
            if kind=='root_remove':
                actionable=0 if plan['blockers'] else 1
                details={'rootPath':plan['root']['path'],'removed':plan['remove'],'retained':plan['shared'],'blockers':plan['blockers']}
            if kind.startswith('recover_'):actionable=seq
            backend_hash=self.snapshot(kind,options,plan_id);revision=self.revision();rules_hash=self.rules_hash()
            fingerprint=digest([ident,kind,options,plan_id,backend_hash,revision,rules_hash,seq])
            with self.db() as db:db.execute("UPDATE reviews SET state='ready',plan_id=?,backend_hash=?,revision=?,rules_hash=?,fingerprint=?,total=?,actionable=?,skipped=?,conflicts=?,details=? WHERE id=?",(plan_id,backend_hash,revision,rules_hash,fingerprint,seq,actionable,skipped,conflicts,json.dumps(details,ensure_ascii=False),ident))
            manager.progress(job_id,processed=seq,total=seq,message='具体文件预览已保存；等待用户确认')
        except BaseException:
            with self.db() as db:db.execute("UPDATE reviews SET state='interrupted' WHERE id=?",(ident,))
            raise
    def identity_guard(self,review_id,job_id):
        def check(file):
            with self.db() as db:rows=db.execute('SELECT fs FROM review_items WHERE review_id=? AND source=?',(review_id,str(file.path))).fetchall()
            if any(row['fs'] and json.loads(row['fs'])==token(file) for row in rows):return
            # Compensation only accepts the exact identity published by this execution.
            with self.db() as db:row=db.execute('SELECT fs FROM execution_files WHERE job_id=? AND path=?',(job_id,str(file.path))).fetchone()
            if row and json.loads(row['fs'])==token(file):return
            raise ValueError('预览后文件身份改变，已保留')
        def remember(file):
            with self.db() as db:db.execute('INSERT OR REPLACE INTO execution_files VALUES(?,?,?)',(job_id,str(file.path),json.dumps(token(file))))
        check.remember=remember
        return check
    def organize(self,manager,job_id,payload):
        with self.service.organizer.identity_check(self.identity_guard(payload['reviewId'],job_id)):
            self.service.organizer.execute(manager,job_id,payload['plan_id'])
    def undo(self,manager,job_id,payload):
        with self.service.organizer.identity_check(self.identity_guard(payload['reviewId'],job_id)):
            self.service.organizer.undo(manager,job_id,payload['job_id'])
    def recover(self,manager,job_id,payload):
        try:
            with self.service.organizer.lock,self.service.organizer.identity_check(self.identity_guard(payload['reviewId'],job_id)):
                self.service.organizer.recover(lambda:manager.cancelled(job_id) or self.service._closing.is_set())
            remaining=self.pending();manager.progress(job_id,processed=manager.get(job_id)['total'],errors=remaining,message='恢复检查完成；仍待复核项不会自动重试')
        finally:self.service.manager.recovering=False
    def execute(self,ident,fingerprint,confirmed,confirmation):
        if not confirmed:raise ManagementError('confirmation_required')
        with self.service._operation(),self.service._submission_lock:
            row=self.row(ident);kind=row['kind'];options=json.loads(row['options'])
            self.ensure(kind.startswith('recover_'))
            if kind=='duplicate_purge' and confirmation!='永久清空':raise ManagementError('confirmation_required')
            if not self.plan_ready(row) or row['state']!='ready' or row['fingerprint']!=fingerprint or row['revision']!=self.revision() or row['rules_hash']!=self.rules_hash() or row['backend_hash']!=self.snapshot(kind,options,row['plan_id']):raise ManagementError('stale_review')
            if not row['actionable']:raise ManagementError('stale_review')
            with self.db() as db:
                if not db.execute("UPDATE reviews SET state='claimed' WHERE id=? AND state='ready'",(ident,)).rowcount:raise ManagementError('stale_review')
            manager=self.service.manager
            if kind=='organize':
                total=self.service.organizer.claim_plan(row['plan_id']);task=manager.submit('organize',{'plan_id':row['plan_id'],'reviewId':ident},total)
            elif kind=='organize_undo':task=manager.submit('undo',{'job_id':options['jobId'],'reviewId':ident},row['actionable'])
            elif kind=='root_remove':task=self.service.remove_root(options['rootId'],row['plan_id'])
            elif kind=='duplicate_cleanup':task=self.service.execute_duplicate_cleanup(row['plan_id'])
            elif kind=='duplicate_undo':task=self.service.undo_duplicate_cleanup(options['jobId'])
            elif kind=='duplicate_purge':task=self.service.execute_duplicate_purge(row['plan_id'])
            elif kind=='recover_duplicates':task=self.service.recover_duplicates()
            else:
                manager.recovering=True;task=manager.submit('file_recovery',{'reviewId':ident},row['actionable'])
            with self.db() as db:db.execute("UPDATE reviews SET state='submitted',execution_job_id=? WHERE id=?",(task['id'],ident))
            return {**job(task),'reviewId':ident}

_init_lock=threading.Lock()
def get(service):
    with _init_lock:
        if not hasattr(service,'file_operations'):service.file_operations=FileOperations(service)
        return service.file_operations
def read(service,section,a):
    ops=get(service)
    if section=='files.state':fields(a,());return ops.state()
    if section=='files.rules':
        fields(a,('cursor',));offset=integer(a['cursor']) if a.get('cursor') is not None else 0;rules=service.rules()
        return {'items':rules[offset:offset+25],'total':len(rules),'next_cursor':offset+25 if offset+25<len(rules) else None,'hash':ops.rules_hash()}
    if section=='files.root':
        fields(a,('rootId',),('rootId',));ident=integer(a['rootId'])
        with service.store.connect() as db:row=db.execute('SELECT id,path,name FROM roots WHERE id=?',(ident,)).fetchone()
        if row is None:raise ManagementError('not_found')
        return dict(row)
    if section=='files.review':fields(a,('reviewId',),('reviewId',));return ops.header(ops.row(text(a['reviewId'],100)))
    if section=='files.items':fields(a,('reviewId','cursor'),('reviewId',));return ops.items(text(a['reviewId'],100),a.get('cursor'))
    if section=='files.duplicate.groups':fields(a,('scanId','cursor'),('scanId',));return service.duplicate_groups(text(a['scanId'],100),a.get('cursor'),60)
    if section=='files.duplicate.members':fields(a,('groupId','cursor'),('groupId',));return service.duplicate_members(integer(a['groupId']),a.get('cursor'),60)
    raise ManagementError('method_not_allowed')
def prepare(service,action,a):
    ops=get(service)
    if action=='files.preview':
        fields(a,('kind','options'),('kind','options'));ops.options(a['kind'],a['options']);return lambda:ops.preview(a['kind'],a['options'])
    if action=='files.execute':
        fields(a,('reviewId','fingerprint','confirmed','confirmation'),('reviewId','fingerprint','confirmed'));ident=text(a['reviewId'],100);fingerprint=text(a['fingerprint'],64);confirmed=boolean(a['confirmed']);confirmation=text(a.get('confirmation',''),20,empty=True)
        return lambda:ops.execute(ident,fingerprint,confirmed,confirmation)
    if action=='files.scan':fields(a,('rootId',));root=a.get('rootId');integer(root) if root is not None else None;return lambda:job(service.scan_duplicates(root))
    if action=='files.duplicate.select':fields(a,('groupId','selected'),('groupId','selected'));ident=integer(a['groupId']);selected=boolean(a['selected']);return lambda:service.select_duplicate_group(ident,selected)
    if action=='files.duplicate.all':fields(a,('scanId','selected'),('scanId','selected'));ident=text(a['scanId'],100);selected=boolean(a['selected']);return lambda:service.select_duplicate_all(ident,selected)
    if action=='files.duplicate.keep':fields(a,('groupId','imageId'),('groupId','imageId'));group=integer(a['groupId']);image=integer(a['imageId']);return lambda:service.keep_duplicate(group,image)
    if action=='files.rules.save':
        fields(a,('rules','expectedHash'),('rules','expectedHash'));expected=text(a['expectedHash'],64)
        def save():
            with service._submission_lock:
                ops.ensure()
                if ops.rules_hash()!=expected:raise ManagementError('stale_review')
                return service.put_rules(a['rules'])
        return save
    if action=='files.rules.change':
        fields(a,('rule','removeId','expectedHash'),('expectedHash',));expected=text(a['expectedHash'],64)
        if ('rule' in a)==('removeId' in a):raise ManagementError()
        def change():
            with service._submission_lock:
                ops.ensure()
                if ops.rules_hash()!=expected:raise ManagementError('stale_review')
                values=service.rules();ident=text(a['removeId'],100) if 'removeId' in a else text(a['rule'].get('id'),100)
                if 'rule' in a:
                    found=any(r['id']==ident for r in values)
                    values=[a['rule'] if r['id']==ident else r for r in values]
                    if not found:values.append(a['rule'])
                else:values=[r for r in values if r['id']!=ident]
                service.put_rules(values);return {'saved':True,'hash':ops.rules_hash()}
        return change
    if action=='files.discard':
        fields(a,('reviewId',),('reviewId',));ident=text(a['reviewId'],100)
        def discard_review():
            with service._submission_lock:
                ops.ensure();row=ops.row(ident)
                if row['state']!='ready':raise ManagementError('stale_review')
                with service.store.connect() as db:
                    if row['kind']=='organize':db.execute("UPDATE plans SET status='cancelled' WHERE id=? AND status='ready'",(row['plan_id'],))
                    elif row['kind'] in ('duplicate_cleanup','duplicate_purge'):db.execute("UPDATE duplicate_plans SET status='cancelled' WHERE id=? AND status='ready'",(row['plan_id'],))
                with ops.db() as db:db.execute("UPDATE reviews SET state='cancelled' WHERE id=?",(ident,))
                return {'discarded':True}
        return discard_review
    if action=='files.root.discard':
        fields(a,('rootId','confirmed'),('rootId','confirmed'));root=integer(a['rootId']);confirmed=boolean(a['confirmed'])
        def discard():
            if not confirmed:raise ManagementError('confirmation_required')
            return {'discarded':service.discard_root_previews(root)}
        return discard
    raise ManagementError('method_not_allowed')
