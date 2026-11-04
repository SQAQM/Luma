"""V2 bounded lossless pixels; cache resources, never original paths/bytes in RPC."""
import hashlib,json,os,secrets
from PIL import Image,ImageOps,UnidentifiedImageError
from .image_read import ReadImage
from .read_catalog import CatalogError,detail
from .ownership import no_reparse

MAX_SOURCE_PIXELS=50_000_000
MAX_ORIGINAL_PIXELS=24_000_000
MAX_CONTAINER_BYTES=128*1024*1024
MAX_ASSET_BYTES=32*1024*1024

def valid_metadata(value):
    if not isinstance(value,dict):return False
    for name,maximum in [('width',16384),('height',16384),('sourceWidth',MAX_SOURCE_PIXELS),('sourceHeight',MAX_SOURCE_PIXELS),('bytes',MAX_ASSET_BYTES)]:
        if type(value.get(name)) is not int or not 1<=value[name]<=maximum:return False
    checksum=value.get('sha256')
    return (type(value.get('version')) is int and value['version']==2 and type(value.get('frame')) is int and value['frame']==0
        and type(value.get('animated')) is bool and value.get('kind') in ('original','standard','compatibility')
        and value['width']*value['height']<=MAX_ORIGINAL_PIXELS and value['sourceWidth']*value['sourceHeight']<=MAX_SOURCE_PIXELS
        and isinstance(checksum,str) and len(checksum)==64 and all(c in '0123456789abcdef' for c in checksum))

def preview(service,image_id,expected,mode,cancelled):
    def checkpoint():
        if cancelled.is_set():raise CatalogError('cancelled')
        service._ensure_open()
    checkpoint();row=detail(service,image_id,expected)
    try:
        with ReadImage(row['path']) as source:
            if (source.info.st_size,source.info.st_mtime_ns)!=(row['size'],row['mtime_ns']):raise CatalogError('stale_identity')
            if source.info.st_size>MAX_CONTAINER_BYTES:raise CatalogError('unsupported')
            key=hashlib.sha256(f"pixels-v2|{row['identity']}|{source.info.st_dev}|{source.info.st_ino}|{source.change_ns}|{mode}".encode()).hexdigest()
            cache=service.store.directory/'thumbnails'/'viewer-v2'/key[:2]/(key+'.png');seal=cache.with_suffix('.json')
            no_reparse(cache);no_reparse(seal);metadata=None
            if cache.exists() and seal.exists() and seal.stat().st_size<=2048:
                try:
                    with ReadImage(seal) as saved:metadata=json.loads(saved.file.read(2049));saved.verify()
                    if not valid_metadata(metadata):raise ValueError('cache_metadata')
                    with ReadImage(cache) as saved:
                        if saved.info.st_size!=metadata['bytes'] or saved.info.st_size>MAX_ASSET_BYTES or saved.digest(cancelled.is_set)!=metadata['sha256']:raise ValueError('cache_bytes')
                        saved.file.seek(0)
                        if saved.file.read(8)!=b'\x89PNG\r\n\x1a\n':raise ValueError('cache_format')
                        saved.file.seek(0)
                        with Image.open(saved.file) as pixels:
                            if pixels.format!='PNG' or pixels.size!=(metadata['width'],metadata['height']):raise ValueError('cache_dimensions')
                        saved.verify()
                except (OSError,ValueError,KeyError):metadata=None
            if metadata is None:
                checkpoint();source.file.seek(0)
                with Image.open(source.file) as image:
                    w,h=image.size
                    if min(w,h)<1 or w*h>MAX_SOURCE_PIXELS:raise CatalogError('unsupported')
                    orientation=image.getexif().get(274,1);sw,sh=(h,w) if orientation in (5,6,7,8) else (w,h)
                    full=mode=='original' and w*h<=MAX_ORIGINAL_PIXELS and max(w,h)<=16384 and image.format not in ('TIFF',)
                    kind='original' if full else 'compatibility' if mode=='original' or w*h>MAX_ORIGINAL_PIXELS or image.format=='TIFF' else 'standard'
                    edge=max(w,h) if full else 1200 if kind=='compatibility' else 2400
                    image.draft('RGB',(edge,edge));image.seek(0)
                    image.thumbnail((edge,edge),Image.Resampling.LANCZOS,reducing_gap=3.0);checkpoint()
                    rendered=ImageOps.exif_transpose(image).convert('RGBA');checkpoint()
                    metadata={'version':2,'width':rendered.width,'height':rendered.height,'sourceWidth':sw,'sourceHeight':sh,'kind':kind,'frame':0,'animated':bool(getattr(image,'is_animated',False))}
                    cache.parent.mkdir(parents=True,exist_ok=True);no_reparse(cache)
                    temp=cache.with_name(cache.name+'.'+secrets.token_hex(8)+'.tmp')
                    try:
                        # Fresh image drops prompt/EXIF/ICC dictionaries from encoded asset.
                        clean=Image.new('RGBA',rendered.size);clean.paste(rendered);clean.save(temp,format='PNG')
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
    except FileNotFoundError:raise CatalogError('not_found') from None
    except CatalogError:raise
    except (OSError,ValueError,Image.DecompressionBombError,UnidentifiedImageError):raise CatalogError('unsupported') from None
