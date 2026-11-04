"""Bounded read adapter. Persistent catalog identity differs from a prompt token."""
import hashlib
import json
from pathlib import Path
from backend.duplicates import VerifiedFile
from backend.organizer import signature
from worker.ownership import no_reparse


class CatalogError(ValueError):
    def __init__(self,code):self.code=code;super().__init__(code)


def stamp(service, rows):
    if not rows:return rows
    ids=[r['id'] for r in rows]
    with service.store.connect() as db:
        current={r['id']:dict(r) for r in db.execute('SELECT i.id,i.path,i.size,i.mtime_ns,i.indexed_at,x.token,x.revision FROM images i JOIN image_content_identity x ON x.image_id=i.id WHERE i.id IN ('+','.join('?' for _ in ids)+')',ids)}
    for row in rows:
        actual=current.get(row['id'])
        if actual is None or any(row[key]!=actual[key] for key in ('path','size','mtime_ns','indexed_at')):raise CatalogError('stale_identity')
        row['identity']=hashlib.sha256(json.dumps(actual,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()
    return rows


def detail(service,image_id,expected=None):
    row=stamp(service,[service.detail(image_id)])[0]
    if expected is not None and row['identity']!=expected:raise CatalogError('stale_identity')
    for key in ('raw_metadata','parameters','reason'):row.pop(key,None)
    return row


def query(service,filters,cursor,limit):
    try:result=service.query(filters,cursor,limit)
    except ValueError as error:
        if cursor is not None:raise CatalogError('stale_cursor') from None
        raise error
    stamp(service,result['items'])
    for row in result['items']:
        for key in ('prompt','negative_prompt','parameters','raw_metadata','tags','prompt_tags','reason'):row.pop(key,None)
    return result


def thumbnail(service,image_id,expected,size):
    row=detail(service,image_id,expected)
    from .image_read import ReadImage
    with ReadImage(Path(row['path'])) as source:
        if (source.info.st_size,source.info.st_mtime_ns)!=(row['size'],row['mtime_ns']):raise CatalogError('stale_identity')
        key=hashlib.sha256(f"v4|{row['identity']}|{source.info.st_dev}|{source.info.st_ino}|{source.change_ns}|{size}".encode()).hexdigest()
        cache=service.store.directory/'thumbnails'/key[:2]/(key+'.jpg');no_reparse(cache)
        seal=cache.with_suffix('.sha256');no_reparse(seal)
        valid=False
        if cache.exists() and seal.exists() and seal.stat().st_size==64:
            with VerifiedFile(cache) as existing:
                if existing.info.st_size<=4*1024*1024:
                    checksum=existing.digest()
                    try:valid=checksum==seal.read_text(encoding='ascii')
                    except (OSError,UnicodeError):valid=False
                    existing.verify()
        if not valid:
            service._make_thumbnail(source.path,source.info,cache,size)
            with VerifiedFile(cache) as fresh:checksum=fresh.digest();fresh.verify()
            # Atomically publish the checksum; a missing/incomplete seal regenerates the asset.
            import secrets
            temporary=seal.with_name(seal.name+'.'+secrets.token_hex(8)+'.tmp')
            with temporary.open('x',encoding='ascii') as file:file.write(checksum)
            temporary.replace(seal)
        with VerifiedFile(cache) as final:
            if final.info.st_size>4*1024*1024 or final.digest()!=checksum:raise CatalogError('asset_changed')
            final.verify();encoded=final.info.st_size
        source.verify();signature(cache)
        detail(service,image_id,row['identity'])
        service._remember_thumbnail(key,cache)
    return {'resourceId':key,'relativePath':cache.relative_to(service.store.directory).as_posix(),'identity':row['identity'],'sha256':checksum,'bytes':encoded}
