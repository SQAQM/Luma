"""Explicit single-image slot, isolated from the registered library. No folder/sidecar scan."""
import json
from pathlib import Path
from backend.metadata import extract_metadata
from backend.store import Store
from worker.image_read import ReadImage
from worker.ownership import Ownership,EngineStartError,no_reparse


def prepare_single(directory, image):
    directory=Path(directory);image=Path(image)
    if not image.is_absolute() or str(image).startswith('\\\\'):
        raise EngineStartError('invalid_request')
    with Ownership(directory) as owner:
        marker=directory/'.luma-single-file.json';no_reparse(marker)
        if marker.exists():
            if marker.stat().st_size>256 or json.loads(marker.read_text())!={'schema':1,'purpose':'single-file-viewer'}:
                raise EngineStartError()
        elif (directory/'library.sqlite3').exists():
            raise EngineStartError()
        else:
            with marker.open('x') as file:json.dump({'schema':1,'purpose':'single-file-viewer'},file)
        owner.preflight()
        with ReadImage(image) as source:
            if source.info.st_size>128*1024*1024:raise EngineStartError('unsupported')
            metadata=extract_metadata(image,read_sidecars=False)
            if min(metadata['width'],metadata['height'])<1 or metadata['width']*metadata['height']>50_000_000:
                raise EngineStartError('unsupported')
            source.verify()
            store=Store(directory)
            with store.connect() as db:
                if db.execute('SELECT COUNT(*) FROM images').fetchone()[0]>1 or db.execute('SELECT 1 FROM roots LIMIT 1').fetchone():
                    raise EngineStartError()
                db.execute('DELETE FROM images')
                store._upsert(db,image,None,source.info,metadata,read_sidecars=False)
                source.verify()
