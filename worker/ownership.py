"""Old-launcher-compatible ownership, before any catalog service opens."""
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

MARKER = '.luma-engine-preview.json'


class EngineStartError(RuntimeError):
    def __init__(self, code='unsafe_data_directory'):
        self.code = code
        super().__init__(code)


def no_reparse(path):
    current = Path(path)
    for entry in [current, *current.parents]:
        try: info = entry.lstat()
        except FileNotFoundError: continue
        if entry.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise EngineStartError()


def prepare_preview(path):
    path = Path(path)
    if not path.is_absolute() or path.exists():
        raise EngineStartError()
    no_reparse(path)
    path.mkdir(parents=True, exist_ok=False)
    with (path/MARKER).open('x', encoding='utf-8') as file:
        json.dump({'schema': 1, 'purpose': 'luma-stage3-preview'}, file)


class Ownership:
    def __init__(self, directory):
        self.file = None
        self.owned = False
        path = Path(directory)
        legacy = Path(os.environ.get('LOCALAPPDATA', Path.home()/'.local/share'))/'Luma'
        if not path.is_absolute() or str(path).startswith('\\\\'):
            raise EngineStartError()
        self.directory = Path(os.path.abspath(path))
        legacy = Path(os.path.abspath(legacy))
        if self.directory == legacy or legacy in self.directory.parents:
            raise EngineStartError()
        no_reparse(self.directory)
        marker = self.directory/MARKER
        no_reparse(marker)
        try:
            if marker.stat().st_size > 256: raise EngineStartError()
            value = json.loads(marker.read_text(encoding='utf-8'))
            if value != {'schema': 1, 'purpose': 'luma-stage3-preview'}: raise EngineStartError()
        except (OSError, ValueError):
            raise EngineStartError() from None
        lock = self.directory/'native-instance.lock'
        no_reparse(lock)
        self.file = lock.open('a+b')
        if self.file.tell() == 0:
            self.file.write(b'0'); self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.owned = True
        except OSError:
            self.file.close(); self.file = None
            raise EngineStartError('engine_busy') from None

    def preflight(self, *, allow_recovery=False):
        database = self.directory/'library.sqlite3'
        for path in (database,*(self.directory/('library.sqlite3'+suffix) for suffix in ('-wal','-shm','-journal'))):
            no_reparse(path)
            if path.exists() and (not path.is_file() or path.stat().st_nlink!=1):raise EngineStartError()
        if not database.exists(): return
        try:
            with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('jobs','journal')")}
                if not allow_recovery and 'journal' in tables and db.execute("SELECT 1 FROM journal WHERE state IN ('prepared','copying','verified','published') LIMIT 1").fetchone():
                    raise EngineStartError('recovery_required')
                if not allow_recovery and 'jobs' in tables and db.execute("SELECT 1 FROM jobs WHERE kind IN ('organize','undo','recovery') AND status IN ('queued','running') LIMIT 1").fetchone():
                    raise EngineStartError('recovery_required')
        except sqlite3.Error:
            raise EngineStartError('catalog_unavailable') from None

    def close(self):
        if self.file is None: return
        try:
            if self.owned:
                self.file.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close(); self.file = None; self.owned = False

    def __enter__(self): return self
    def __exit__(self, *_): self.close()
