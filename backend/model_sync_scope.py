"""SQL snapshots for whole-filter sync; bounded batches and fixed issue codes."""
import uuid

ISSUE_CODES=frozenset(('unauthorized','forbidden','not_found','rate_limited','unavailable','network','redirect','response_too_large','invalid_response','invalid_hash','hash_mismatch','invalid_id','invalid_cover','credentials','source_changed','file_missing','file_error','sync_error','sidecar_write','sidecar_busy','sidecar_permission','sidecar_format','sidecar_size','sidecar_conflict','sidecar_space','cover_not_found','profile_not_found','version_not_found'))

def prepare(library,filters):
    token=uuid.uuid4().hex
    with library.lock,library.store.connect() as db:
        _,_,source,args,_,_=library._scope_query(db,**filters)
        db.execute('BEGIN IMMEDIATE')
        # Service admits this only when there is no active inventory/sync job.
        db.execute('DELETE FROM luma_model_sync_selection')
        db.execute('INSERT INTO luma_model_sync_selection(scope_id,file_id) SELECT ?,f.id'+source,[token,*args])
        count=db.execute('SELECT count(*) FROM luma_model_sync_selection WHERE scope_id=?',(token,)).fetchone()[0]
    return token,count

def selected(library,token):
    anchor=0
    while True:
        with library.store.connect() as db:
            rows=db.execute('SELECT file_id FROM luma_model_sync_selection WHERE scope_id=? AND file_id>? ORDER BY file_id LIMIT 120',(token,anchor)).fetchall()
        if not rows:return
        for row in rows:anchor=row[0];yield anchor

def discard(library,token):
    with library.store.connect() as db:db.execute('DELETE FROM luma_model_sync_selection WHERE scope_id=?',(token,))

def prune(library):
    with library.store.connect() as db:
        db.execute("DELETE FROM luma_model_sync_selection WHERE scope_id NOT IN (SELECT json_extract(payload,'$.scope_id') FROM jobs WHERE kind='model_sync' AND status IN ('queued','running') AND json_type(payload,'$.scope_id')='text')")
        db.execute('DELETE FROM luma_model_sync_failures WHERE job_id NOT IN (SELECT id FROM jobs)')

def record_issue(library,job_id,code,file_id=None):
    assert code in ISSUE_CODES
    with library.store.connect() as db:
        db.execute('INSERT INTO luma_model_sync_issues(job_id,code,count) VALUES(?,?,1) ON CONFLICT(job_id,code) DO UPDATE SET count=count+1',(job_id,code))
        if file_id is not None:db.execute('INSERT OR REPLACE INTO luma_model_sync_failures(job_id,file_id,code) SELECT ?,id,? FROM luma_model_files WHERE id=?',(job_id,code,file_id))


def prepare_retry(library,job_id):
    token=uuid.uuid4().hex
    with library.lock,library.store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM luma_model_sync_selection')
        db.execute('INSERT INTO luma_model_sync_selection(scope_id,file_id) SELECT ?,e.file_id FROM luma_model_sync_failures e JOIN luma_model_files f ON f.id=e.file_id WHERE e.job_id=?',(token,job_id))
        count=db.execute('SELECT count(*) FROM luma_model_sync_selection WHERE scope_id=?',(token,)).fetchone()[0]
    return token,count

def attach_issues(library,jobs):
    ids=[j['id'] for j in jobs if j.get('kind')=='model_sync'];issues={i:[] for i in ids};retry={i:0 for i in ids}
    if ids:
        with library.store.connect() as db:
            for row in db.execute('SELECT job_id,code,count FROM luma_model_sync_issues WHERE job_id IN ('+','.join('?' for _ in ids)+') ORDER BY code',ids):
                if row['code'] in ISSUE_CODES and type(row['count']) is int and row['count']>0:issues[row['job_id']].append({'code':row['code'],'count':row['count']})
            for row in db.execute('SELECT e.job_id,count(*) total FROM luma_model_sync_failures e JOIN luma_model_files f ON f.id=e.file_id WHERE e.job_id IN ('+','.join('?' for _ in ids)+') GROUP BY e.job_id',ids):retry[row['job_id']]=row['total']
    return [{**j,'issues':issues[j['id']],'retryable_count':retry[j['id']]} if j['id'] in issues else j for j in jobs]
