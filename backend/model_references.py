"""Bounded, explicit cached model leaves. Never opens image files or raw text."""
from __future__ import annotations

import re
from .model_registry import model_name


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS luma_model_roots(id INTEGER PRIMARY KEY AUTOINCREMENT,path TEXT UNIQUE NOT NULL,name TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS luma_model_files(id INTEGER PRIMARY KEY AUTOINCREMENT,root_id INTEGER REFERENCES luma_model_roots(id),path TEXT UNIQUE NOT NULL,name TEXT NOT NULL,normalized_name TEXT NOT NULL,size INTEGER NOT NULL,mtime_ns INTEGER NOT NULL,ctime_ns INTEGER NOT NULL,device TEXT NOT NULL,inode TEXT NOT NULL,change_ns INTEGER NOT NULL,sha256 TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'indexed',version_id INTEGER,seen TEXT NOT NULL DEFAULT '');
    CREATE INDEX IF NOT EXISTS ix_luma_models_hash ON luma_model_files(sha256);
    CREATE INDEX IF NOT EXISTS ix_luma_models_name ON luma_model_files(normalized_name);
    CREATE TABLE IF NOT EXISTS luma_model_memberships(file_id INTEGER REFERENCES luma_model_files(id) ON DELETE CASCADE,root_id INTEGER REFERENCES luma_model_roots(id) ON DELETE CASCADE,PRIMARY KEY(file_id,root_id));
    CREATE INDEX IF NOT EXISTS ix_luma_model_membership_root ON luma_model_memberships(root_id,file_id);
    CREATE TABLE IF NOT EXISTS luma_model_versions(version_id INTEGER PRIMARY KEY,record TEXT NOT NULL,updated_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS luma_model_local_info(file_id INTEGER PRIMARY KEY REFERENCES luma_model_files(id) ON DELETE CASCADE,record TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS luma_model_sync_selection(scope_id TEXT NOT NULL,file_id INTEGER NOT NULL,PRIMARY KEY(scope_id,file_id));
    CREATE TABLE IF NOT EXISTS luma_model_sync_issues(job_id TEXT NOT NULL,code TEXT NOT NULL,count INTEGER NOT NULL,PRIMARY KEY(job_id,code));
    CREATE TABLE IF NOT EXISTS luma_model_sync_failures(job_id TEXT NOT NULL,file_id INTEGER NOT NULL REFERENCES luma_model_files(id) ON DELETE CASCADE,code TEXT NOT NULL,PRIMARY KEY(job_id,file_id));
    CREATE TABLE IF NOT EXISTS luma_model_query_state(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL);
    INSERT OR IGNORE INTO luma_model_query_state(id,revision) VALUES(1,1);
    CREATE TABLE IF NOT EXISTS luma_model_version_files(version_id INTEGER REFERENCES luma_model_versions(version_id) ON DELETE CASCADE,sha256 TEXT NOT NULL,PRIMARY KEY(version_id,sha256));
    CREATE INDEX IF NOT EXISTS ix_luma_version_hash ON luma_model_version_files(sha256);
    CREATE TABLE IF NOT EXISTS luma_model_verified_hashes(version_id INTEGER REFERENCES luma_model_versions(version_id) ON DELETE CASCADE,sha256 TEXT NOT NULL,PRIMARY KEY(version_id,sha256));
    CREATE TABLE IF NOT EXISTS luma_image_model_refs(image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,role TEXT NOT NULL,kind TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(image_id,role,kind,value));
    CREATE INDEX IF NOT EXISTS ix_luma_image_model_refs_lookup ON luma_image_model_refs(kind,value,image_id);
    ''')
    for table in ('luma_model_files','luma_model_memberships','luma_model_versions','luma_model_local_info','luma_model_version_files','luma_model_verified_hashes','luma_image_model_refs'):
        for op in ('INSERT','UPDATE','DELETE'):
            db.execute(f'CREATE TRIGGER IF NOT EXISTS query_revision_{table}_{op.lower()} AFTER {op} ON {table} BEGIN UPDATE query_state SET revision=revision+1 WHERE id=1; END')
    for table in ('luma_model_roots','luma_model_files','luma_model_memberships','luma_model_versions','luma_model_local_info'):
        for op in ('INSERT','UPDATE','DELETE'):
            db.execute(f'CREATE TRIGGER IF NOT EXISTS model_revision_{table}_{op.lower()} AFTER {op} ON {table} BEGIN UPDATE luma_model_query_state SET revision=revision+1 WHERE id=1; END')


_PARAMS="CASE WHEN typeof(parameters)='text' AND length(CAST(parameters AS BLOB))<=1048576 AND json_valid(parameters) THEN parameters ELSE '{}' END"
_RESOURCE_TYPES=('lora','locon','lycoris','vae','controlnet','embedding')
_ROLE_ALIASES={'lora':'lora','locon':'locon','lycoris':'lycoris','dora':'dora','vae':'vae','controlnet':'controlnet',
    'embedding':'embedding','textualinversion':'embedding','hypernetwork':'hypernetwork','hypernet':'hypernetwork',
    'upscaler':'upscaler','upscale':'upscaler','clip':'clip','ipadapter':'ipadapter','t2iadapter':'t2iadapter',
    'textencoder':'textencoder','encoder':'textencoder'}
_ARCHITECTURES=('checkpoint','model','unet','sd','sd1','sd1.4','sd1.5','sd2','sd2.0','sd2.1','sd3','sd3.5',
    'sdxl','sdxl1.0','stablediffusion','stablediffusionxl','flux','flux.1','flux1','anima','krea2','novelai')
def _type_leaf(key):
    return f"lower(replace(replace(replace(json_extract({_PARAMS},'$.{key}'),' ',''),'-',''),'_',''))"
_AUX_KEYS=','.join(repr(alias) for alias in _ROLE_ALIASES)
_ALIASES_SQL=' '.join(f"WHEN '{alias}' THEN '{role}'" for alias,role in _ROLE_ALIASES.items() if alias!=role)
_ROLE="CASE "+' '.join(f"WHEN {_type_leaf(key)} IN ({_AUX_KEYS}) THEN CASE {_type_leaf(key)} {_ALIASES_SQL} ELSE {_type_leaf(key)} END" for key in ('type','resource_type','model_type'))
_ROLE+=' '+ ' '.join(f"WHEN COALESCE({_type_leaf(key)},'')<>'' AND {_type_leaf(key)} NOT IN ({','.join(repr(v) for v in _ARCHITECTURES)}) THEN 'unknown'" for key in ('type','resource_type','model_type'))+" ELSE 'checkpoint' END"


def _selected_file_role(digest='f.sha256', record='v.record'):
    # Resolve the held file's own role, not the last sibling file queried from
    # this version. Kept inside the single selected-file CTE, never per image.
    role=f"CASE WHEN json_type(j.value,'$.resource_type')='text' THEN lower(json_extract(j.value,'$.resource_type')) WHEN json_type(j.value,'$.type')='text' THEN CASE WHEN lower(json_extract(j.value,'$.type'))='model' THEN lower(COALESCE(json_extract({record},'$.model_type'),json_extract({record},'$.type'))) ELSE lower(json_extract(j.value,'$.type')) END ELSE lower(json_extract({record},'$.type')) END"
    return f"COALESCE((SELECT CASE WHEN count(DISTINCT role)=1 THEN min(role) ELSE 'unknown' END FROM (SELECT {role} role FROM json_each({record},'$.files') j WHERE lower(json_extract(j.value,'$.sha256'))={digest})),'' )"


def _leaf(expression,kind):
    if kind=='hash':
        return f"CASE WHEN typeof({expression})='text' AND length({expression}) BETWEEN 10 AND 64 AND {expression} NOT GLOB '*[^0-9a-fA-F]*' THEN lower({expression}) ELSE '' END"
    if kind=='version':
        return f"CASE WHEN typeof({expression})='integer' AND {expression}>0 THEN {expression} ELSE NULL END"
    return f"CASE WHEN typeof({expression})='text' AND length({expression})<=2048 THEN {expression} ELSE '' END"


def _array(p,key,kind='array'):
    return f"CASE WHEN json_type({p},'$.{key}')='{kind}' THEN json_extract({p},'$.{key}') ELSE '[]' END"


def hash_projection(db,image_id):
    """Network lookup projection: hashes only, even cached model names excluded."""
    p=_PARAMS; parts=[]
    for key in ('model_hash','checkpoint_hash','model_sha256','checkpoint_sha256','Model hash','Checkpoint hash'):
        expression=f"json_extract({p},'$.\"{key}\"')"
        parts.append(f"SELECT {_leaf(expression,'hash')} value FROM images WHERE id=? AND {_ROLE}='checkpoint'")
    for key in ('model','checkpoint','Model','Checkpoint'):
        expression=f"json_extract({p},'$.hashes.\"{key}\"')"
        parts.append(f"SELECT {_leaf(expression,'hash')} value FROM images WHERE id=? AND {_ROLE}='checkpoint'")
    parts.append(f"SELECT {_leaf('j.value','hash')} value FROM images,json_each({_array(p,'checkpoint_hashes')}) j WHERE images.id=? AND {_ROLE}='checkpoint' AND j.type='text' AND CAST(j.key AS INTEGER)<64")
    return db.execute(' UNION ALL '.join(parts),[image_id]*len(parts))


def _projection(db, image_id):
    # Fixed SQL leaf whitelist. No whole objects, prompt/TAG/raw metadata/path
    # values enter Python. json_each only enumerates explicitly named arrays.
    p=_PARAMS
    scalar=[("checkpoint","name","model")]
    for key in ('model_hash','checkpoint_hash','model_sha256','checkpoint_sha256','Model hash','Checkpoint hash'):
        scalar.append(('checkpoint','hash',f"json_extract({p},'$.\"{key}\"')"))
    for key in ('model','checkpoint','Model','Checkpoint'):
        scalar.append(('checkpoint','hash',f"json_extract({p},'$.hashes.\"{key}\"')"))
    sql=' UNION ALL '.join(f"SELECT {_ROLE} role,'{kind}' kind,{_leaf(value,kind)} value FROM images WHERE id=?" for role,kind,value in scalar)
    rows=list(db.execute(sql,[image_id]*len(scalar)))
    for key,kind in (('model_candidates','name'),('checkpoint_hashes','hash')):
        rows.extend(db.execute(f"SELECT {_ROLE} role,? kind,{_leaf('j.value',kind)} value FROM images,json_each({_array(p,key)}) j WHERE images.id=? AND j.type='text' AND CAST(j.key AS INTEGER)<64 LIMIT 64",(kind,image_id)))
    rows.extend(db.execute(f"SELECT 'lora' role,'hash' kind,{_leaf('j.value','hash')} value FROM images,json_each({_array(p,'lora_hashes','object')}) j WHERE images.id=? AND j.type='text' LIMIT 64",(image_id,)))
    # Explicitly typed resources only: auxiliary identities remain auxiliary.
    for key in ('civitai_resources','model_resources'):
        rows.extend(db.execute(f'''SELECT lower(json_extract(j.value,'$.type')) role,'name' kind,{_leaf("json_extract(j.value,'$.name')",'name')} value
          FROM images,json_each({_array(p,key)}) j WHERE images.id=? AND j.type='object'
          AND lower(json_extract(j.value,'$.type')) IN ('checkpoint','lora','locon','lycoris','vae','controlnet','embedding') LIMIT 64''',(image_id,)))
        for leaf,kind in (('hash','hash'),('sha256','hash'),('modelVersionId','version'),('version_id','version'),('air','air')):
            rows.extend(db.execute(f'''SELECT lower(json_extract(j.value,'$.type')) role,? kind,{_leaf(f"json_extract(j.value,'$.{leaf}')",kind)} value
              FROM images,json_each({_array(p,key)}) j WHERE images.id=? AND j.type='object'
              AND lower(json_extract(j.value,'$.type')) IN ('checkpoint','lora','locon','lycoris','vae','controlnet','embedding') LIMIT 64''',(kind,image_id)))
    return rows


def update_image_references(db,image_id,metadata=None):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='luma_image_model_refs'").fetchone(): return
    refs=set()
    for row in _projection(db,image_id):
        role,kind,value=tuple(row)
        if kind in ('air','name') and isinstance(value,str) and value.lower().startswith('urn:air:'):
            air=re.fullmatch(r'urn:air:[a-zA-Z0-9_/-]+:(checkpoint|lora|locon|lycoris|vae|controlnet|embedding):civitai:([1-9][0-9]*)@([1-9][0-9]*)(?:\+[0-9]+)?',value,re.I)
            if air and (kind=='name' or role==air[1].lower()):
                refs.add((image_id,air[1].lower(),'version',air[3]))
            continue
        if kind=='name' and isinstance(value,str) and len(value)<=2048:
            value=model_name(value)
            if value: refs.add((image_id,role,kind,value))
        elif kind=='hash' and isinstance(value,str) and re.fullmatch('[0-9a-fA-F]{10,64}',value):
            refs.add((image_id,role,kind,value.lower()))
        elif kind=='version' and type(value) is int and value>0:
            refs.add((image_id,role,kind,str(value)))
    db.execute('DELETE FROM luma_image_model_refs WHERE image_id=?',(image_id,))
    db.executemany('INSERT INTO luma_image_model_refs(image_id,role,kind,value) VALUES(?,?,?,?)',sorted(refs))


def match_clause(match):
    if match=='name':
        return "i.id IN (SELECT r.image_id FROM luma_image_model_refs r JOIN luma_model_files f ON r.kind='name' AND r.value=f.normalized_name WHERE f.id=?)"
    # A short prefix is evidence only when exactly one full digest is known in
    # both local and verified remote catalogs. Explicit version IDs additionally
    # require the local file's full digest to occur in that verified version.
    lengths=','.join(f'({n})' for n in range(10,64))
    return f'''i.id IN (WITH selected AS (SELECT f.sha256,CASE WHEN v.version_id IS NULL THEN '' ELSE {_selected_file_role()} END role FROM luma_model_files f LEFT JOIN luma_model_versions v ON v.version_id=f.version_id WHERE f.id=? AND length(f.sha256)=64),
      lengths(n) AS (VALUES {lengths}), prefixes(value) AS (
        SELECT substr(s.sha256,1,n) FROM selected s,lengths
        WHERE NOT EXISTS(SELECT 1 FROM luma_model_files o WHERE length(o.sha256)=64 AND o.sha256<>s.sha256 AND o.sha256>=substr(s.sha256,1,n) AND o.sha256<substr(s.sha256,1,n)||'g')
        AND NOT EXISTS(SELECT 1 FROM luma_model_version_files o WHERE o.sha256<>s.sha256 AND o.sha256>=substr(s.sha256,1,n) AND o.sha256<substr(s.sha256,1,n)||'g'))
      SELECT r.image_id FROM selected s JOIN luma_image_model_refs r ON r.kind='hash' AND r.value=s.sha256 AND (s.role='' OR r.role=s.role)
      UNION SELECT r.image_id FROM prefixes p JOIN selected s JOIN luma_image_model_refs r ON r.kind='hash' AND r.value=p.value AND (s.role='' OR r.role=s.role)
      UNION SELECT r.image_id FROM selected s JOIN luma_model_version_files vf ON vf.sha256=s.sha256
        JOIN luma_model_versions cv ON cv.version_id=vf.version_id
        JOIN luma_image_model_refs r ON r.kind='version' AND r.value=CAST(vf.version_id AS TEXT) AND s.role NOT IN ('','unknown') AND r.role=s.role AND {_selected_file_role('s.sha256','cv.record')}=r.role)'''
