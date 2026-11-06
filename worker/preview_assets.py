"""Bounded lossless frame resources; never original paths/bytes in RPC."""
import hashlib,json,os,secrets
from PIL import Image,ImageOps,UnidentifiedImageError
from .image_read import ReadImage
from .read_catalog import CatalogError,detail
from .ownership import no_reparse
from .preview_source import SourceCache

MAX_SOURCE_PIXELS=50_000_000
MAX_ORIGINAL_PIXELS=24_000_000
MAX_CONTAINER_BYTES=128*1024*1024
MAX_ASSET_BYTES=32*1024*1024
MAX_FRAMES=4096

def valid_metadata(value):
    if not isinstance(value,dict):return False
    for name,low,maximum in [('width',1,16384),('height',1,16384),('sourceWidth',1,MAX_SOURCE_PIXELS),('sourceHeight',1,MAX_SOURCE_PIXELS),('bytes',1,MAX_ASSET_BYTES),('frameCount',1,MAX_FRAMES),('frame',0,MAX_FRAMES-1),('durationMs',0,60000),('repeatLimit',0,65536)]:
        if type(value.get(name)) is not int or not low<=value[name]<=maximum:return False
    checksum=value.get('sha256')
    return (type(value.get('version')) is int and value['version']==2 and value['frame']<value['frameCount']
        and type(value.get('animated')) is bool and value.get('kind') in ('original','standard','compatibility')
        and value['width']*value['height']<=MAX_ORIGINAL_PIXELS and value['sourceWidth']*value['sourceHeight']<=MAX_SOURCE_PIXELS
        and isinstance(checksum,str) and len(checksum)==64 and all(c in '0123456789abcdef' for c in checksum))

def release(service,image_id,identity):
    cache=getattr(service,'_viewer_source_cache',None)
    if cache:cache.release((image_id,identity))
    return {'released':True}

def preview(service,image_id,expected,mode,cancelled,frame=0):
    def checkpoint():
        if cancelled.is_set():raise CatalogError('cancelled')
        service._ensure_open()
    checkpoint();row=detail(service,image_id,expected)
    source_cache=service.__dict__.setdefault('_viewer_source_cache',SourceCache())
    owner=(image_id,expected)
    try:
        with source_cache.lock,ReadImage(row['path']) as source:
            if (source.info.st_size,source.info.st_mtime_ns)!=(row['size'],row['mtime_ns']):raise CatalogError('stale_identity')
            if source.info.st_size>MAX_CONTAINER_BYTES:raise CatalogError('unsupported')
            fingerprint=f"{row['identity']}|{source.info.st_dev}|{source.info.st_ino}|{source.change_ns}"
            image,retained=source_cache.acquire(source,fingerprint,owner,MAX_CONTAINER_BYTES,MAX_FRAMES)
            try:
                if not 0<=frame<source_cache.count:raise CatalogError('invalid_params')
                image.seek(frame+source_cache.offset);w,h=image.size
                if min(w,h)<1 or w*h>MAX_SOURCE_PIXELS:raise CatalogError('unsupported')
                orientation=image.getexif().get(274,1);sw,sh=(h,w) if orientation in (5,6,7,8) else (w,h)
                full=mode=='original' and w*h<=MAX_ORIGINAL_PIXELS and max(w,h)<=16384
                kind='original' if full else 'compatibility' if mode=='original' or w*h>MAX_ORIGINAL_PIXELS or image.format=='TIFF' else 'standard'
                edge=max(w,h) if full else 1200 if kind=='compatibility' else 2400
                if source_cache.animated:image.load()
                duration=image.info.get('duration',100)
                duration=max(20,min(60000,int(duration))) if isinstance(duration,(int,float)) and 0<duration<1e10 else 100
                expected_meta={'version':2,'sourceWidth':sw,'sourceHeight':sh,'kind':kind,'frame':frame,'frameCount':source_cache.count,'animated':source_cache.animated,'durationMs':duration if source_cache.animated else 0,'repeatLimit':source_cache.repeat_limit}
                key=hashlib.sha256(f'pixels-v3|{fingerprint}|{mode}|{frame}'.encode()).hexdigest()
                cache=service.store.directory/'thumbnails'/'viewer-v3'/key[:2]/(key+'.png');seal=cache.with_suffix('.json')
                no_reparse(cache);no_reparse(seal);metadata=None
                if cache.exists() and seal.exists() and seal.stat().st_size<=2048:
                    try:
                        with ReadImage(seal) as saved:metadata=json.loads(saved.file.read(2049));saved.verify()
                        if not valid_metadata(metadata) or any(metadata.get(k)!=v for k,v in expected_meta.items()):raise ValueError('cache_metadata')
                        with ReadImage(cache) as saved:
                            if saved.info.st_size!=metadata['bytes'] or saved.digest(cancelled.is_set)!=metadata['sha256']:raise ValueError('cache_bytes')
                            saved.file.seek(0)
                            with Image.open(saved.file) as pixels:
                                if pixels.format!='PNG' or pixels.size!=(metadata['width'],metadata['height']):raise ValueError('cache_dimensions')
                            saved.verify()
                    except (OSError,ValueError,KeyError):metadata=None
                if metadata is None:
                    checkpoint()
                    if not retained:image.draft('RGB',(edge,edge))
                    rendered=ImageOps.exif_transpose(image.copy())
                    rendered.thumbnail((edge,edge),Image.Resampling.LANCZOS,reducing_gap=3.0);checkpoint()
                    metadata={**expected_meta,'width':rendered.width,'height':rendered.height}
                    cache.parent.mkdir(parents=True,exist_ok=True);no_reparse(cache)
                    temp=cache.with_name(cache.name+'.'+secrets.token_hex(8)+'.tmp')
                    try:
                        clean=Image.new('RGBA',rendered.size);clean.paste(rendered.convert('RGBA'));clean.save(temp,format='PNG',compress_level=1 if retained else 6)
                        checkpoint();source.verify()
                        if temp.stat().st_size>MAX_ASSET_BYTES:raise CatalogError('unsupported')
                        with ReadImage(temp) as file:metadata['sha256']=file.digest(cancelled.is_set);metadata['bytes']=file.info.st_size
                        os.replace(temp,cache)
                    finally:temp.unlink(missing_ok=True)
                    temporary=seal.with_name(seal.name+'.'+secrets.token_hex(8)+'.tmp')
                    try:
                        temporary.write_text(json.dumps(metadata),encoding='ascii');os.replace(temporary,seal)
                    finally:temporary.unlink(missing_ok=True)
                checkpoint();source.verify();detail(service,image_id,expected)
                with ReadImage(cache) as final:
                    if final.info.st_size!=metadata['bytes'] or final.digest(cancelled.is_set)!=metadata['sha256']:raise CatalogError('conflict')
                service._remember_thumbnail(key,cache)
                return {**metadata,'resourceId':key,'relativePath':cache.relative_to(service.store.directory).as_posix(),'identity':row['identity']}
            finally:
                if not retained:image.__exit__(None,None,None)
    except FileNotFoundError:raise CatalogError('not_found') from None
    except CatalogError:
        if cancelled.is_set():source_cache.release(owner)
        raise
    except (OSError,ValueError,EOFError,Image.DecompressionBombError,UnidentifiedImageError):
        source_cache.release(owner);raise CatalogError('unsupported') from None
