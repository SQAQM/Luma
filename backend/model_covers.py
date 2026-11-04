"""Exact-name local model previews; bounded decode only when a card requests it."""
from __future__ import annotations
import hashlib
import io
import os
from pathlib import Path
import stat
import uuid
import warnings
from PIL import Image, ImageOps
from .duplicates import VerifiedFile
from .organizer import check_directory_chain, reparse_point

MAX_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 24_000_000
EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.avif', '.gif')


def candidates(model_path):
    path = Path(model_path)
    # Never assign a generic folder cover to every unrelated model.
    for base in (path.stem + '.preview', path.stem, path.name + '.preview', path.name):
        for extension in EXTENSIONS:
            candidate = path.with_name(base + extension)
            try:
                info = candidate.lstat()
                if stat.S_ISREG(info.st_mode) and not reparse_point(candidate, info) and 0 < info.st_size <= MAX_BYTES:
                    yield candidate, info
            except OSError:
                continue


def available(model_path):
    return next(candidates(model_path), None) is not None


def thumbnail(model_path, cache_dir):
    cache_dir = Path(cache_dir)
    for path, info in candidates(model_path):
        try:
            check_directory_chain(path.parent)
            identity = (str(path), info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_dev, info.st_ino)
            key = hashlib.sha256(repr(identity).encode('utf-8')).hexdigest()
            target = cache_dir / ('local-' + key + '.cover')
            check_directory_chain(cache_dir)
            if target.exists():
                cached = target.lstat()
                if stat.S_ISREG(cached.st_mode) and not reparse_point(target, cached) and 0 < cached.st_size <= MAX_BYTES:
                    return target
                continue
            with VerifiedFile(path) as source:
                if source.info.st_size > MAX_BYTES:
                    continue
                data = source.file.read(MAX_BYTES + 1)
                source.verify()
                if len(data) > MAX_BYTES:
                    continue
                with warnings.catch_warnings():
                    warnings.simplefilter('error', Image.DecompressionBombWarning)
                    with Image.open(io.BytesIO(data)) as image:
                        if image.format not in ('PNG', 'JPEG', 'WEBP', 'AVIF', 'GIF') or image.width * image.height > MAX_PIXELS:
                            continue
                        image.draft('RGB', (640, 640))
                        image = ImageOps.exif_transpose(image)
                        image.thumbnail((640, 640), Image.Resampling.LANCZOS)
                        # Fresh pixels only: no EXIF, prompt, comment or sidecar metadata.
                        output = Image.new('RGBA' if 'A' in image.getbands() else 'RGB', image.size)
                        output.paste(image)
                        buffer = io.BytesIO(); output.save(buffer, format='PNG')
                source.verify()
            cache_dir.mkdir(parents=True, exist_ok=True)
            check_directory_chain(cache_dir)
            temporary = cache_dir / (uuid.uuid4().hex + '.tmp')
            try:
                with temporary.open('xb') as stream:
                    stream.write(buffer.getvalue())
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            return target
        except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            continue
    return None
