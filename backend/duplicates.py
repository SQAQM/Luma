"""Explicit, bounded exact-duplicate checks and recoverable cleanup.

No image decoder, metadata parser, sidecar reader, or network client is used here.
Catalog metadata is preserved with SQL-to-SQL archives, never projected into Python.
Windows mutation uses the same deny-write/delete handles that verified the bytes.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import sqlite3
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .organizer import Cancelled, check_directory_chain, reparse_point, signature

BATCH = 100
CHUNK = 1024 * 1024
ACTIVE = ("import", "organize", "undo", "remove_root", "reclassify_models",
          "classify_contents", "recovery", "duplicate_scan", "duplicate_cleanup",
          "duplicate_undo", "duplicate_purge", "duplicate_recovery")
UNSETTLED = ("prepared", "moved", "restore_prepared", "purge_prepared", "needs_review")


def _fingerprint(info):
    return (info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_dev, info.st_ino, info.st_nlink)


def _windows_change(handle):
    """NT change-time, unlike Python 3.11 Windows st_ctime (creation time)."""
    from ctypes import wintypes
    class BasicInfo(ctypes.Structure):
        _fields_=[("CreationTime",ctypes.c_int64),("LastAccessTime",ctypes.c_int64),
                  ("LastWriteTime",ctypes.c_int64),("ChangeTime",ctypes.c_int64),
                  ("FileAttributes",wintypes.DWORD)]
    api=ctypes.WinDLL("kernel32",use_last_error=True)
    api.GetFileInformationByHandleEx.argtypes=(wintypes.HANDLE,ctypes.c_int,wintypes.LPVOID,wintypes.DWORD)
    api.GetFileInformationByHandleEx.restype=wintypes.BOOL
    value=BasicInfo()
    if not api.GetFileInformationByHandleEx(handle,0,ctypes.byref(value),ctypes.sizeof(value)):
        raise OSError(ctypes.get_last_error(),"无法读取可靠变更标识，已跳过")
    return value.ChangeTime


def _change_stamp(path,info):
    if os.name!="nt": return info.st_ctime_ns
    from ctypes import wintypes
    api=ctypes.WinDLL("kernel32",use_last_error=True)
    api.CreateFileW.argtypes=(wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE)
    api.CreateFileW.restype=wintypes.HANDLE
    api.CloseHandle.argtypes=(wintypes.HANDLE,)
    handle=api.CreateFileW(str(path),0x80,7,None,3,0x00200000,None)
    if handle==ctypes.c_void_p(-1).value: raise OSError(ctypes.get_last_error(),"文件变更标识不可用")
    try: return _windows_change(handle)
    finally: api.CloseHandle(handle)


def _id(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("重复图片 ID 无效")
    return value


def _token(value):
    if not isinstance(value,str) or len(value)!=32 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("持久清理标识无效，文件保留")
    return value


def _limit(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= BATCH:
        raise ValueError("重复图片每页数量应在 1 至 100 之间")
    return value


class VerifiedFile:
    """Local regular file held against write/delete while checked or mutated.

Windows file-handle mutations avoid a check/path-rename race. On other platforms
read-only scanning is available; destructive actions fail closed.
Microsoft FILE_RENAME_INFO: absolute name, ReplaceIfExists=False.
"""
    def __init__(self, path, *, mutable=False, parent_writes=False):
        if mutable and parent_writes:raise ValueError('mutable_parent_sharing')
        self.path = Path(path)
        self.mutable = mutable
        self.parent_writes=parent_writes
        self.file = None
        self.directories = []

    def __enter__(self):
        if not self.path.is_absolute():raise ValueError("文件路径必须是绝对路径")
        before = signature(self.path)
        if before.st_nlink != 1:
            raise ValueError("硬链接不参与清理；不会把它计算为可释放空间")
        if os.name == "nt":
            import msvcrt
            from ctypes import wintypes
            api = ctypes.WinDLL("kernel32", use_last_error=True)
            api.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                       wintypes.HANDLE)
            api.CreateFileW.restype = wintypes.HANDLE
            api.CloseHandle.argtypes = (wintypes.HANDLE,)
            # Hold every existing ancestor against replacement/reparse changes.
            # Destructive/default reads deny WRITE/DELETE on ancestors. Explicit
            # sidecar export permits sibling writes only in the immediate parent;
            # the source file itself still denies WRITE and DELETE sharing.
            current = self.path.parent
            try:
                while current != current.parent:
                    directory = api.CreateFileW(str(current), 0x80, 3 if self.parent_writes and current==self.path.parent else 1, None, 3,
                                                0x02200000, None)
                    if directory == ctypes.c_void_p(-1).value:
                        raise OSError(ctypes.get_last_error(), "无法锁定文件目录，已跳过")
                    self.directories.append((api,directory))
                    current = current.parent
                check_directory_chain(self.path.parent)
            except BaseException:
                self._close_directories()
                raise
            access = 0x80000000 | (0x00010000 if self.mutable else 0)  # read + DELETE
            # Exclusive mutable handles also prevent a pre-existing reader from
            # keeping a purged file delete-pending after this handle is closed.
            share = 0 if self.mutable else 1
            handle = api.CreateFileW(str(self.path), access, share, None, 3, 0x00200000, None)
            if handle == ctypes.c_void_p(-1).value:
                self._close_directories()
                raise OSError(ctypes.get_last_error(), "文件正被修改或无法锁定，已跳过")
            try:
                descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except BaseException:
                api.CloseHandle.argtypes = (wintypes.HANDLE,)
                api.CloseHandle(handle)
                self._close_directories()
                raise
            self.file = os.fdopen(descriptor, "rb")
            self.handle, self.api = handle, api
        else:
            if self.mutable:
                raise ValueError("此平台未提供已验证的安全文件句柄清理，文件保留")
            self.file = open(self.path, "rb")
        try:
            self.info = os.fstat(self.file.fileno())
            self.change_ns = _windows_change(self.handle) if os.name=="nt" else self.info.st_ctime_ns
            if not stat.S_ISREG(self.info.st_mode) or _fingerprint(before) != _fingerprint(self.info):
                raise ValueError("文件在打开期间改变，已跳过")
            return self
        except BaseException:
            self.file.close()
            self._close_directories()
            raise

    def __exit__(self, *unused):
        self.file.close()
        self._close_directories()

    def _close_directories(self):
        for api,handle in reversed(self.directories):
            api.CloseHandle(handle)
        self.directories.clear()

    def verify(self):
        current = os.fstat(self.file.fileno())
        # On Windows the verified ancestor handles remain open without WRITE or
        # DELETE sharing. They cannot be renamed or turned into reparse points;
        # walking the same locked chain twice per block check adds only I/O.
        on_disk=self.path.lstat() if os.name=="nt" else signature(self.path)
        if not stat.S_ISREG(on_disk.st_mode) or reparse_point(self.path,on_disk):
            raise ValueError("文件不再是普通文件，已跳过")
        if _fingerprint(current) != _fingerprint(self.info) or _fingerprint(on_disk) != _fingerprint(self.info):
            raise ValueError("文件在检查期间改变，已跳过")
        change=_windows_change(self.handle) if os.name=="nt" else current.st_ctime_ns
        if change!=self.change_ns: raise ValueError("文件变更标识改变，已跳过")

    def digest(self, cancelled=lambda: False):
        self.file.seek(0)
        value = hashlib.sha256()
        while block := self.file.read(CHUNK):
            if cancelled():
                raise Cancelled()
            value.update(block)
        self.verify()
        return value.hexdigest()

    def equals(self, other, cancelled=lambda: False):
        if self.info.st_size != other.info.st_size:
            return False
        self.file.seek(0); other.file.seek(0)
        while True:
            if cancelled():
                raise Cancelled()
            left, right = self.file.read(CHUNK), other.file.read(CHUNK)
            if left != right:
                return False
            if not left:
                break
        self.verify(); other.verify()
        return True

    def _set(self, kind, value):
        from ctypes import wintypes
        self.api.SetFileInformationByHandle.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                                        wintypes.LPVOID, wintypes.DWORD)
        self.api.SetFileInformationByHandle.restype = wintypes.BOOL
        if not self.api.SetFileInformationByHandle(self.handle, kind, ctypes.byref(value), ctypes.sizeof(value)):
            raise OSError(ctypes.get_last_error(), "安全文件操作未完成，文件保留")

    def rename(self, target):
        if not self.mutable or os.name != "nt":
            raise ValueError("未持有安全修改句柄")
        self.verify()
        target = Path(target)
        check_directory_chain(target.parent)
        if target.exists() or target.is_symlink():
            raise FileExistsError("目标路径已存在，不会覆盖")
        if target.parent.stat().st_dev != self.info.st_dev:
            raise ValueError("隔离和恢复仅允许同卷操作")
        # Source ancestors are already held. Lock the new target parent too.
        handle = self.api.CreateFileW(str(target.parent),0x80,1,None,3,0x02200000,None)
        if handle == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(),"无法锁定目标目录，文件保留")
        self.directories.append((self.api,handle))
        check_directory_chain(target.parent)
        # The WCHAR field is inline and must preserve native pointer alignment.
        encoded_name = str(target).encode("utf-16-le")
        class RenameInfo(ctypes.Structure):
            _fields_ = [("ReplaceIfExists", ctypes.c_ubyte), ("RootDirectory", ctypes.c_void_p),
                        ("FileNameLength", ctypes.c_uint32), ("FileName", ctypes.c_wchar * (len(encoded_name)//2 + 1))]
        value = RenameInfo(False, None, len(encoded_name), str(target))
        self._set(3, value)
        self.path = target
        # A rename may change creation/change-time on some filesystems.
        self.info = os.fstat(self.file.fileno())
        self.change_ns = _windows_change(self.handle)

    def delete(self):
        if not self.mutable or os.name != "nt":
            raise ValueError("未持有安全修改句柄")
        self.verify()
        class Disposition(ctypes.Structure):
            _fields_ = [("DeleteFile", ctypes.c_ubyte)]
        self._set(4, Disposition(True))  # deletion occurs when the handle closes


class DuplicateManager:
    def __init__(self, store, lock=None):
        self.store = store
        self.lock = lock or threading.Lock()
        self.initialize()

    def initialize(self):
        with self.store.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS duplicate_scans(id TEXT PRIMARY KEY,root_id INTEGER,root_path TEXT NOT NULL DEFAULT '',
              highwater INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'queued',phase TEXT NOT NULL DEFAULT 'stat',
              total INTEGER NOT NULL DEFAULT 0,checked INTEGER NOT NULL DEFAULT 0,hashed INTEGER NOT NULL DEFAULT 0,
              cached INTEGER NOT NULL DEFAULT 0,skipped INTEGER NOT NULL DEFAULT 0,groups INTEGER NOT NULL DEFAULT 0,
              duplicates INTEGER NOT NULL DEFAULT 0,bytes INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',
              created_at REAL NOT NULL,updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS duplicate_files(scan_id TEXT NOT NULL,image_id INTEGER NOT NULL,path TEXT NOT NULL,name TEXT NOT NULL,
              size INTEGER NOT NULL,mtime_ns INTEGER NOT NULL,ctime_ns INTEGER NOT NULL,dev INTEGER NOT NULL,ino INTEGER NOT NULL,nlink INTEGER NOT NULL,
              status TEXT NOT NULL,digest TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '',change_ns INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(scan_id,image_id));
            CREATE INDEX IF NOT EXISTS ix_duplicate_size ON duplicate_files(scan_id,status,size,image_id);
            CREATE INDEX IF NOT EXISTS ix_duplicate_digest ON duplicate_files(scan_id,digest,size,image_id);
            CREATE TABLE IF NOT EXISTS duplicate_hash_cache(image_id INTEGER PRIMARY KEY,path TEXT NOT NULL,size INTEGER NOT NULL,
              mtime_ns INTEGER NOT NULL,ctime_ns INTEGER NOT NULL,dev INTEGER NOT NULL,ino INTEGER NOT NULL,nlink INTEGER NOT NULL,digest TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS duplicate_groups(id INTEGER PRIMARY KEY AUTOINCREMENT,scan_id TEXT NOT NULL,size INTEGER NOT NULL,
              digest TEXT NOT NULL,keep_id INTEGER NOT NULL,selected INTEGER NOT NULL DEFAULT 0,member_count INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS ix_duplicate_groups_scan ON duplicate_groups(scan_id,id);
            CREATE TABLE IF NOT EXISTS duplicate_members(group_id INTEGER NOT NULL,image_id INTEGER NOT NULL,PRIMARY KEY(group_id,image_id));
            CREATE TABLE IF NOT EXISTS duplicate_plans(id TEXT PRIMARY KEY,scan_id TEXT,cleanup_job TEXT NOT NULL DEFAULT '',kind TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'ready',total INTEGER NOT NULL DEFAULT 0,bytes INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS duplicate_plan_items(id INTEGER PRIMARY KEY AUTOINCREMENT,plan_id TEXT NOT NULL,group_id INTEGER,
              image_id INTEGER NOT NULL,keep_id INTEGER NOT NULL,source TEXT NOT NULL,keep_path TEXT NOT NULL,destination TEXT NOT NULL,size INTEGER NOT NULL,
              mtime_ns INTEGER NOT NULL,ctime_ns INTEGER NOT NULL,dev INTEGER NOT NULL,ino INTEGER NOT NULL,nlink INTEGER NOT NULL,
              keep_mtime_ns INTEGER NOT NULL,keep_ctime_ns INTEGER NOT NULL,keep_dev INTEGER NOT NULL,keep_ino INTEGER NOT NULL,
              digest TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'ready',reason TEXT NOT NULL DEFAULT '',original_journal INTEGER);
            CREATE INDEX IF NOT EXISTS ix_duplicate_plan_items ON duplicate_plan_items(plan_id,group_id,id);
            CREATE TABLE IF NOT EXISTS duplicate_journal(id INTEGER PRIMARY KEY AUTOINCREMENT,plan_id TEXT NOT NULL,item_id INTEGER NOT NULL,
              job_id TEXT NOT NULL,image_id INTEGER NOT NULL,source TEXT NOT NULL,destination TEXT NOT NULL,keep_path TEXT NOT NULL,
              size INTEGER NOT NULL,digest TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'prepared',error TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,updated_at REAL NOT NULL,
              q_mtime_ns INTEGER,q_ctime_ns INTEGER,q_dev INTEGER,q_ino INTEGER);
            CREATE INDEX IF NOT EXISTS ix_duplicate_journal_job ON duplicate_journal(job_id,state,id);
            """)
            # Forward-compatible attribute-token migration, never reads image data.
            for table in ("duplicate_files","duplicate_hash_cache"):
                if "change_ns" not in {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN change_ns INTEGER NOT NULL DEFAULT 0")
            for table in ("images", "image_tags", "image_content_tags", "image_content_overrides", "image_roots"):
                db.execute(f"CREATE TABLE IF NOT EXISTS duplicate_archived_{table} AS SELECT CAST(NULL AS INTEGER) AS duplicate_journal_id,t.* FROM {table} t WHERE 0")
                db.execute(f"CREATE INDEX IF NOT EXISTS ix_duplicate_archive_{table} ON duplicate_archived_{table}(duplicate_journal_id)")
            db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('duplicateLibraryId',?)", (json.dumps(uuid.uuid4().hex),))
            self.library_id = json.loads(db.execute("SELECT value FROM settings WHERE key='duplicateLibraryId'").fetchone()[0])
            if not isinstance(self.library_id, str) or len(self.library_id) != 32 or any(c not in "0123456789abcdef" for c in self.library_id):
                raise ValueError("隔离库标识无效；停止清理以保护文件")

    def _guard(self, db, job_id=None):
        marks = ",".join("?" for _ in ACTIVE)
        if db.execute(f"SELECT 1 FROM jobs WHERE kind IN ({marks}) AND status IN ('queued','running') AND id<>? LIMIT 1", (*ACTIVE, job_id or "")).fetchone():
            raise ValueError("相关任务正在运行，请先取消或等待完成")
        if db.execute("SELECT 1 FROM journal WHERE state IN ('prepared','copying','verified','published','needs_review') LIMIT 1").fetchone():
            raise ValueError("存在未处理的移动日志，请先完成恢复检查")
        if db.execute("SELECT 1 FROM duplicate_journal WHERE state IN ('prepared','moved','restore_prepared','purge_prepared','needs_review') LIMIT 1").fetchone():
            raise ValueError("存在中断隔离记录，请先明确启动恢复检查")

    def create_scan(self, root_id=None, *, cancelled=lambda:False):
        if cancelled(): raise Cancelled()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE"); self._guard(db)
            db.execute("UPDATE duplicate_plans SET status='stale' WHERE kind='cleanup' AND status='ready'")
            root_path = ""
            if root_id is not None:
                _id(root_id)
                root = db.execute("SELECT path FROM roots WHERE id=?", (root_id,)).fetchone()
                if not root:
                    raise ValueError("导入目录不存在")
                root_path = root[0]
            scope = " WHERE EXISTS(SELECT 1 FROM image_roots r WHERE r.image_id=i.id AND r.root_id=?)" if root_id else ""
            args = (root_id,) if root_id else ()
            total, highwater = db.execute("SELECT COUNT(*),COALESCE(MAX(i.id),0) FROM images i" + scope, args).fetchone()
            scan_id, now = uuid.uuid4().hex, time.time()
            db.execute("INSERT INTO duplicate_scans(id,root_id,root_path,highwater,total,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                       (scan_id, root_id, root_path, highwater, total, now, now))
        self._prune_old_scans(cancelled)
        return self.get_scan(scan_id)

    def _prune_old_scans(self,cancelled):
        """Keep three recent snapshots and any still-executing approved plan.

Completed journal recovery uses its independently persisted plan/archive, never
requires a whole old-library scan snapshot. Logs/archives are not pruned here.
"""
        while True:
            if cancelled(): raise Cancelled()
            with self._scan_db(cancelled) as db:
                old=db.execute("""SELECT s.id FROM duplicate_scans s WHERE s.id NOT IN
                  (SELECT id FROM duplicate_scans ORDER BY created_at DESC LIMIT 3)
                  AND NOT EXISTS(SELECT 1 FROM duplicate_plans p WHERE p.scan_id=s.id AND p.status IN ('ready','building','queued','running'))
                  AND NOT EXISTS(SELECT 1 FROM duplicate_plans p JOIN duplicate_journal j ON j.plan_id=p.id
                    WHERE p.scan_id=s.id AND j.state IN ('prepared','moved','restore_prepared','purge_prepared','needs_review'))
                  ORDER BY s.created_at LIMIT 1""").fetchone()
                if not old:break
                scan_id=old[0]
                db.execute("UPDATE duplicate_scans SET status='expired' WHERE id=?", (scan_id,))
            while True:
                if cancelled():raise Cancelled()
                with self._scan_db(cancelled) as db:
                    changed=db.execute("DELETE FROM duplicate_files WHERE rowid IN (SELECT rowid FROM duplicate_files WHERE scan_id=? LIMIT 1000)", (scan_id,)).rowcount
                if not changed:break
            with self._scan_db(cancelled) as db:
                db.execute("DELETE FROM duplicate_members WHERE group_id IN (SELECT id FROM duplicate_groups WHERE scan_id=?)", (scan_id,))
                db.execute("DELETE FROM duplicate_groups WHERE scan_id=?", (scan_id,))
                db.execute("DELETE FROM duplicate_scans WHERE id=?", (scan_id,))
        while True:
            if cancelled():raise Cancelled()
            with self._scan_db(cancelled) as db:
                changed=db.execute("DELETE FROM duplicate_hash_cache WHERE image_id IN (SELECT c.image_id FROM duplicate_hash_cache c WHERE NOT EXISTS(SELECT 1 FROM images i WHERE i.id=c.image_id) LIMIT 1000)").rowcount
            if not changed:break
        self._prune_old_plans(cancelled)

    def _prune_old_plans(self,cancelled=lambda:False):
        # Retain three recent obsolete previews for feedback, but never discard
        # an executing plan or any snapshot backing a durable file journal.
        while True:
            if cancelled():raise Cancelled()
            with self._scan_db(cancelled) as db:
                old=db.execute("""SELECT p.id FROM duplicate_plans p WHERE p.status IN ('stale','interrupted')
                  AND p.id NOT IN (SELECT id FROM duplicate_plans WHERE status IN ('stale','interrupted') ORDER BY created_at DESC LIMIT 3)
                  AND NOT EXISTS(SELECT 1 FROM duplicate_journal j WHERE j.plan_id=p.id)
                  AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.status IN ('queued','running') AND json_extract(j.payload,'$.plan_id')=p.id)
                  ORDER BY p.created_at LIMIT 1""").fetchone()
                if not old:break
            while True:
                if cancelled():raise Cancelled()
                with self._scan_db(cancelled) as db:
                    changed=db.execute("DELETE FROM duplicate_plan_items WHERE id IN (SELECT id FROM duplicate_plan_items WHERE plan_id=? LIMIT 1000)", (old[0],)).rowcount
                if not changed:break
            with self._scan_db(cancelled) as db:
                db.execute("DELETE FROM duplicate_plans WHERE id=?", (old[0],))

    def get_scan(self, scan_id):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM duplicate_scans WHERE id=?", (scan_id,)).fetchone()
            if not row:
                raise ValueError("重复图片检查不存在")
            return dict(row)

    def latest_scan(self):
        with self.store.connect() as db:
            row = db.execute("SELECT id FROM duplicate_scans ORDER BY created_at DESC LIMIT 1").fetchone()
        return self.get_scan(row[0]) if row else None

    def _scan_state(self, scan_id, **changes):
        allowed = {"status", "phase", "checked", "hashed", "cached", "skipped", "groups", "duplicates", "bytes", "error"}
        changes = {k:v for k,v in changes.items() if k in allowed}; changes["updated_at"] = time.time()
        with self.store.connect() as db:
            db.execute("UPDATE duplicate_scans SET " + ",".join(f"{key}=?" for key in changes) + " WHERE id=?", (*changes.values(),scan_id))

    @contextmanager
    def _scan_db(self,cancelled):
        """Allow cancellation even inside large SQLite grouping/update work."""
        try:
            with self.store.connect() as db:
                db.set_progress_handler(lambda:1 if cancelled() else 0,10000)
                try:
                    yield db
                finally:
                    db.set_progress_handler(None,0)
        except sqlite3.OperationalError as exc:
            if cancelled(): raise Cancelled() from exc
            raise

    def scan(self, manager, job_id, payload):
        scan_id = payload["scan_id"]
        cancelled = lambda: manager.cancelled(job_id)
        checked = skipped = hashed = cached = 0
        with self.lock:
            with self._scan_db(cancelled) as db:
                self._guard(db, job_id)
            scan = self.get_scan(scan_id)
            self._scan_state(scan_id, status="running")
            try:
                last = 0
                while True:
                    if cancelled(): raise Cancelled()
                    with self._scan_db(cancelled) as db:
                        scope = " AND EXISTS(SELECT 1 FROM image_roots r WHERE r.image_id=i.id AND r.root_id=?)" if scan["root_id"] else ""
                        args = (last,scan["highwater"],scan["root_id"]) if scan["root_id"] else (last,scan["highwater"])
                        rows = list(db.execute("SELECT i.id,i.path,i.name,EXISTS(SELECT 1 FROM journal j WHERE j.image_id=i.id AND j.state IN ('prepared','copying','verified','published','needs_review','committed')) AS journal_blocked FROM images i WHERE i.id>? AND i.id<=?" + scope + " ORDER BY i.id LIMIT 100", args))
                    if not rows: break
                    writes = []
                    for row in rows:
                        if cancelled(): raise Cancelled()
                        last = row["id"]; checked += 1
                        try:
                            if row["journal_blocked"]: raise ValueError("图片有可恢复或可撤销移动日志")
                            info = signature(row["path"])
                            if info.st_nlink != 1: raise ValueError("硬链接跳过")
                            writes.append((scan_id,row["id"],row["path"],row["name"],*_fingerprint(info),"candidate","",_change_stamp(row["path"],info)))
                        except (OSError, ValueError):
                            skipped += 1
                    with self._scan_db(cancelled) as db:
                        db.executemany("INSERT INTO duplicate_files(scan_id,image_id,path,name,size,mtime_ns,ctime_ns,dev,ino,nlink,status,digest,change_ns) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", writes)
                    self._scan_state(scan_id, checked=checked, skipped=skipped)
                    manager.progress(job_id,processed=checked,total=scan["total"],message=f"检查文件大小 {checked} / {scan['total']}；未解码图片")
                self._scan_state(scan_id, phase="hash")
                with self._scan_db(cancelled) as db:
                    db.execute("UPDATE duplicate_files SET status='unique_size' WHERE scan_id=? AND size NOT IN (SELECT size FROM duplicate_files WHERE scan_id=? GROUP BY size HAVING COUNT(*)>1)", (scan_id,scan_id))
                last = 0
                while True:
                    if cancelled(): raise Cancelled()
                    with self._scan_db(cancelled) as db:
                        rows = list(db.execute("""SELECT f.*,c.digest AS cached_digest FROM duplicate_files f
                            LEFT JOIN duplicate_hash_cache c ON c.image_id=f.image_id AND c.path=f.path
                            AND c.size=f.size AND c.mtime_ns=f.mtime_ns AND c.ctime_ns=f.ctime_ns
                            AND c.dev=f.dev AND c.ino=f.ino AND c.nlink=f.nlink AND c.change_ns=f.change_ns
                            WHERE f.scan_id=? AND f.status='candidate' AND f.image_id>? ORDER BY f.image_id LIMIT 100""", (scan_id,last)))
                    if not rows: break
                    file_updates=[]; cache_updates=[]
                    for row in rows:
                        if cancelled(): raise Cancelled()
                        last = row["image_id"]
                        try:
                            with VerifiedFile(row["path"]) as file:
                                if _fingerprint(file.info) != tuple(row[k] for k in ("size","mtime_ns","ctime_ns","dev","ino","nlink")):
                                    raise ValueError("文件在检查期间改变")
                                if file.change_ns!=row["change_ns"]: raise ValueError("检查期间变更标识改变")
                                if row["cached_digest"]:
                                    digest=row["cached_digest"];cached+=1
                                else:
                                    digest=file.digest(cancelled);hashed+=1
                                file.verify()
                            file_updates.append(("hashed",digest,"",scan_id,last))
                            cache_updates.append((last,row["path"],row["size"],row["mtime_ns"],row["ctime_ns"],row["dev"],row["ino"],row["nlink"],digest,row["change_ns"]))
                        except Cancelled: raise
                        except (OSError, ValueError):
                            skipped += 1
                            file_updates.append(("skipped","","文件已改变或无法安全读取",scan_id,last))
                    with self._scan_db(cancelled) as db:
                        db.executemany("UPDATE duplicate_files SET status=?,digest=?,error=? WHERE scan_id=? AND image_id=?",file_updates)
                        db.executemany("INSERT OR REPLACE INTO duplicate_hash_cache(image_id,path,size,mtime_ns,ctime_ns,dev,ino,nlink,digest,change_ns) VALUES(?,?,?,?,?,?,?,?,?,?)",cache_updates)
                    self._scan_state(scan_id, hashed=hashed, cached=cached, skipped=skipped)
                    manager.progress(job_id,message=f"逐字节准备：新算哈希 {hashed}，缓存 {cached}，跳过 {skipped}")
                self._scan_state(scan_id, phase="compare")
                # The aggregate stays in SQLite; fetch bounded candidate-group pages.
                last_digest, last_size = "", -1
                while True:
                    if cancelled(): raise Cancelled()
                    with self._scan_db(cancelled) as db:
                        digests = list(db.execute("SELECT digest,size FROM duplicate_files WHERE scan_id=? AND status='hashed' AND (digest>? OR (digest=? AND size>?)) GROUP BY digest,size HAVING COUNT(*)>1 ORDER BY digest,size LIMIT 100", (scan_id,last_digest,last_digest,last_size)))
                    if not digests: break
                    # Scan results are not file mutations: commit at most 100
                    # hash buckets together instead of fsync per tiny duplicate.
                    with self._scan_db(cancelled) as db:
                        for group in digests:
                            if cancelled(): raise Cancelled()
                            last_digest,last_size = group["digest"],group["size"]
                            while self._confirm_group_in_db(db,scan_id,group["size"],group["digest"],cancelled):
                                pass
                with self._scan_db(cancelled) as db:
                    groups,duplicates,amount = db.execute("SELECT COUNT(*),COALESCE(SUM(member_count-1),0),COALESCE(SUM((member_count-1)*size),0) FROM duplicate_groups WHERE scan_id=?", (scan_id,)).fetchone()
                self._scan_state(scan_id,status="completed",phase="done",groups=groups,duplicates=duplicates,bytes=amount)
                manager.progress(job_id,processed=checked,total=checked,message=f"已确认 {groups} 组完全重复，{duplicates} 份副本；未移动文件")
            except Cancelled:
                self._scan_state(scan_id,status="cancelled",error="检查已取消；不完整结果不能清理")
                raise
            except BaseException:
                self._scan_state(scan_id,status="failed",error="检查失败；文件未修改，请重试")
                raise

    def _confirm_group(self, scan_id, size, digest, cancelled):
        with self._scan_db(cancelled) as db:
            return self._confirm_group_in_db(db,scan_id,size,digest,cancelled)

    def _confirm_group_in_db(self,db,scan_id,size,digest,cancelled):
        first=db.execute("SELECT * FROM duplicate_files WHERE scan_id=? AND size=? AND digest=? AND status='hashed' ORDER BY image_id LIMIT 1",(scan_id,size,digest)).fetchone()
        if not first:return False
        inserted=db.execute("INSERT INTO duplicate_groups(scan_id,size,digest,keep_id,member_count) VALUES(?,?,?,?,0)",(scan_id,size,digest,first["image_id"]))
        group_id=inserted.lastrowid
        count=0
        try:
            with VerifiedFile(first["path"]) as keep:
                self._matches(keep,first)
                last=0
                while True:
                    rows=list(db.execute("SELECT * FROM duplicate_files WHERE scan_id=? AND size=? AND digest=? AND status='hashed' AND image_id>? ORDER BY image_id LIMIT 100",(scan_id,size,digest,last)))
                    if not rows:break
                    matches=[]
                    for row in rows:
                        if cancelled():raise Cancelled()
                        last=row["image_id"]
                        try:
                            if last==first["image_id"]:
                                matches.append((group_id,last));continue
                            with VerifiedFile(row["path"]) as other:
                                self._matches(other,row)
                                if keep.equals(other,cancelled):matches.append((group_id,last))
                        except (OSError,ValueError):pass
                    db.executemany("INSERT INTO duplicate_members VALUES(?,?)",matches)
                    count+=len(matches)
        except (OSError,ValueError):pass
        finally:
            if count<2:
                db.execute("DELETE FROM duplicate_members WHERE group_id=?",(group_id,))
                db.execute("DELETE FROM duplicate_groups WHERE id=?",(group_id,))
                db.execute("UPDATE duplicate_files SET status='unique_hash' WHERE scan_id=? AND image_id=?",(scan_id,first["image_id"]))
            else:
                db.execute("UPDATE duplicate_groups SET member_count=? WHERE id=?",(count,group_id))
                db.execute("UPDATE duplicate_files SET status='confirmed' WHERE scan_id=? AND image_id IN (SELECT image_id FROM duplicate_members WHERE group_id=?)",(scan_id,group_id))
        return True

    @staticmethod
    def _cursor(cursor, scope):
        if cursor is None: return 0
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
            if value[0] != scope: raise ValueError()
            return _id(value[1])
        except Exception as exc:
            raise ValueError("重复图片分页游标无效") from exc

    @staticmethod
    def _next(scope, last):
        return base64.urlsafe_b64encode(json.dumps([scope,last],separators=(",",":")).encode()).decode()

    def groups(self, scan_id, cursor=None, limit=60):
        _limit(limit); last = self._cursor(cursor,"groups:"+scan_id)
        with self.store.connect() as db:
            rows = list(db.execute("SELECT g.*,f.name,f.path FROM duplicate_groups g JOIN duplicate_files f ON f.scan_id=g.scan_id AND f.image_id=g.keep_id WHERE g.scan_id=? AND g.id>? ORDER BY g.id LIMIT ?", (scan_id,last,limit+1)))
            total = db.execute("SELECT COUNT(*) FROM duplicate_groups WHERE scan_id=?", (scan_id,)).fetchone()[0]
        more = len(rows)>limit; rows=rows[:limit]
        return {"items":[{**dict(r),"selected":bool(r["selected"])} for r in rows],"total":total,"next_cursor":self._next("groups:"+scan_id,rows[-1]["id"]) if more else None}

    def members(self, group_id, cursor=None, limit=60):
        _id(group_id); _limit(limit); scope="members:"+str(group_id); last=self._cursor(cursor,scope)
        with self.store.connect() as db:
            group = db.execute("SELECT * FROM duplicate_groups WHERE id=?", (group_id,)).fetchone()
            if not group: raise ValueError("重复图片组不存在")
            rows = list(db.execute("SELECT f.image_id AS id,f.name,f.path,f.size,(f.image_id=g.keep_id) AS is_keep FROM duplicate_members m JOIN duplicate_groups g ON g.id=m.group_id JOIN duplicate_files f ON f.scan_id=g.scan_id AND f.image_id=m.image_id WHERE m.group_id=? AND m.image_id>? ORDER BY m.image_id LIMIT ?", (group_id,last,limit+1)))
        more=len(rows)>limit; rows=rows[:limit]
        return {"items":[{**dict(r),"is_keep":bool(r["is_keep"])} for r in rows],"total":group["member_count"],"next_cursor":self._next(scope,rows[-1]["id"]) if more else None}

    def _editable(self, db, scan_id):
        self._guard(db)
        if not db.execute("SELECT 1 FROM duplicate_scans WHERE id=? AND status='completed'", (scan_id,)).fetchone():
            raise ValueError("请等待一次完整重复检查完成")
        if db.execute("SELECT 1 FROM duplicate_plans WHERE scan_id=? AND kind='cleanup' AND status IN ('queued','running','finished','cancelled')", (scan_id,)).fetchone():
            raise ValueError("该检查已有清理记录，请重新检查后继续")
        db.execute("UPDATE duplicate_plans SET status='stale' WHERE scan_id=? AND status='ready'", (scan_id,))

    def select_group(self, group_id, selected):
        _id(group_id)
        if not isinstance(selected,bool): raise ValueError("组选择必须是开关")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            group=db.execute("SELECT * FROM duplicate_groups WHERE id=?", (group_id,)).fetchone()
            if not group: raise ValueError("重复图片组不存在")
            self._editable(db,group["scan_id"])
            db.execute("UPDATE duplicate_groups SET selected=? WHERE id=?", (selected,group_id))
            return {**dict(group),"selected":selected}

    def select_all(self, scan_id, selected=True):
        if not isinstance(selected,bool): raise ValueError("组选择必须是开关")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE"); self._editable(db,scan_id)
            changed=db.execute("UPDATE duplicate_groups SET selected=? WHERE scan_id=?", (selected,scan_id)).rowcount
        return {"count":changed,"selected":selected}

    def save_keep(self, group_id, image_id):
        _id(group_id); _id(image_id)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            group=db.execute("SELECT * FROM duplicate_groups WHERE id=?", (group_id,)).fetchone()
            if not group: raise ValueError("重复图片组不存在")
            self._editable(db,group["scan_id"])
            if not db.execute("SELECT 1 FROM duplicate_members WHERE group_id=? AND image_id=?", (group_id,image_id)).fetchone():
                raise ValueError("保留项必须属于该重复组")
            db.execute("UPDATE duplicate_groups SET keep_id=? WHERE id=?", (image_id,group_id))
        return {**dict(group),"keep_id":image_id,"selected":bool(group["selected"])}

    def preview_cleanup(self, scan_id, *, cancelled=lambda:False):
        plan_id=uuid.uuid4().hex
        try:
            return self._preview_cleanup(scan_id,plan_id,cancelled)
        except Cancelled:
            with self.store.connect() as db:
                db.execute("UPDATE duplicate_plans SET status='cancelled' WHERE id=? AND status IN ('building','ready')",(plan_id,))
            raise

    def _preview_cleanup(self,scan_id,plan_id,cancelled):
        if cancelled():raise Cancelled()
        with self._scan_db(cancelled) as db:
            db.execute("BEGIN IMMEDIATE"); self._editable(db,scan_id)
            db.execute("INSERT INTO duplicate_plans(id,scan_id,kind,status,created_at) VALUES(?,?,'cleanup','building',?)", (plan_id,scan_id,time.time()))
            # All selection expansion is SQL, not an entire library Python list.
            db.execute("""INSERT INTO duplicate_plan_items(plan_id,group_id,image_id,keep_id,source,keep_path,destination,size,
                mtime_ns,ctime_ns,dev,ino,nlink,keep_mtime_ns,keep_ctime_ns,keep_dev,keep_ino,digest)
                SELECT ?,g.id,f.image_id,g.keep_id,f.path,k.path,'',f.size,f.mtime_ns,f.ctime_ns,f.dev,f.ino,f.nlink,
                  k.mtime_ns,k.ctime_ns,k.dev,k.ino,g.digest
                FROM duplicate_groups g JOIN duplicate_members m ON m.group_id=g.id
                JOIN duplicate_files f ON f.scan_id=g.scan_id AND f.image_id=m.image_id
                JOIN duplicate_files k ON k.scan_id=g.scan_id AND k.image_id=g.keep_id
                WHERE g.scan_id=? AND g.selected=1 AND m.image_id<>g.keep_id""", (plan_id,scan_id))
            count,amount=db.execute("SELECT COUNT(*),COALESCE(SUM(size),0) FROM duplicate_plan_items WHERE plan_id=?", (plan_id,)).fetchone()
            if not count: raise ValueError("请先选择至少一组完全重复图片")
            db.execute("UPDATE duplicate_plans SET total=?,bytes=? WHERE id=?", (count,amount,plan_id))
        # Persist deterministic destinations before this preview can be confirmed.
        last=0
        while True:
            if cancelled():raise Cancelled()
            with self._scan_db(cancelled) as db:
                rows=list(db.execute("SELECT id,source FROM duplicate_plan_items WHERE plan_id=? AND id>? ORDER BY id LIMIT 100", (plan_id,last)))
            if not rows: break
            targets=[]
            for row in rows:
                last=row["id"]
                target=Path(row["source"]).parent/(".luma-duplicate-quarantine-"+self.library_id)/(plan_id+"-"+str(last)+".lumaq")
                targets.append((str(target),last))
            with self._scan_db(cancelled) as db:
                db.executemany("UPDATE duplicate_plan_items SET destination=? WHERE id=?", targets)
        with self._scan_db(cancelled) as db:
            db.execute("UPDATE duplicate_plans SET status='ready' WHERE id=? AND status='building'", (plan_id,))
        self._prune_old_plans(cancelled)
        return self.plan(plan_id)

    def preview_purge(self, cleanup_job_id, *, cancelled=lambda:False):
        plan_id=uuid.uuid4().hex
        try:
            return self._preview_purge(cleanup_job_id,plan_id,cancelled)
        except Cancelled:
            with self.store.connect() as db:
                db.execute("UPDATE duplicate_plans SET status='cancelled' WHERE id=? AND status='ready'",(plan_id,))
            raise

    def _preview_purge(self,cleanup_job_id,plan_id,cancelled):
        if cancelled():raise Cancelled()
        with self._scan_db(cancelled) as db:
            db.execute("BEGIN IMMEDIATE"); self._guard(db)
            if db.execute("SELECT 1 FROM duplicate_journal WHERE job_id=? AND state IN ('prepared','moved','restore_prepared','purge_prepared','needs_review')", (cleanup_job_id,)).fetchone():
                raise ValueError("这个清理任务有待检查日志，请先处理恢复")
            db.execute("UPDATE duplicate_plans SET status='stale' WHERE cleanup_job=? AND kind='purge' AND status='ready'", (cleanup_job_id,))
            db.execute("INSERT INTO duplicate_plans(id,cleanup_job,kind,created_at) VALUES(?,?,'purge',?)", (plan_id,cleanup_job_id,time.time()))
            db.execute("""INSERT INTO duplicate_plan_items(plan_id,group_id,image_id,keep_id,source,keep_path,destination,size,
              mtime_ns,ctime_ns,dev,ino,nlink,keep_mtime_ns,keep_ctime_ns,keep_dev,keep_ino,digest,original_journal)
              SELECT ?,p.group_id,j.image_id,p.keep_id,j.destination,p.keep_path,j.destination,p.size,
                p.mtime_ns,p.ctime_ns,p.dev,p.ino,p.nlink,p.keep_mtime_ns,p.keep_ctime_ns,p.keep_dev,p.keep_ino,j.digest,j.id
              FROM duplicate_journal j JOIN duplicate_plan_items p ON p.id=j.item_id WHERE j.job_id=? AND j.state='committed'""", (plan_id,cleanup_job_id))
            count,amount=db.execute("SELECT COUNT(*),COALESCE(SUM(size),0) FROM duplicate_plan_items WHERE plan_id=?", (plan_id,)).fetchone()
            if not count: raise ValueError("这个任务没有可永久清空的隔离副本")
            db.execute("UPDATE duplicate_plans SET total=?,bytes=? WHERE id=?", (count,amount,plan_id))
        self._prune_old_plans(cancelled)
        return self.plan(plan_id)

    def plan(self, plan_id):
        with self.store.connect() as db:
            row=db.execute("SELECT * FROM duplicate_plans WHERE id=?", (plan_id,)).fetchone()
            if not row: raise ValueError("重复清理预览不存在")
            items=[{**dict(r),"name":Path(r["source"]).name} for r in db.execute("SELECT id,image_id,source,keep_path,destination,size,status,reason FROM duplicate_plan_items WHERE plan_id=? ORDER BY id LIMIT 100", (plan_id,))]
        return {**dict(row),"count":row["total"],"items":items,"items_truncated":row["total"]>BATCH,
                "releases_space":row["kind"]=="purge","sidecars_affected":0}

    def plan_items(self, plan_id, cursor=None, limit=60):
        _limit(limit); scope="plan:"+plan_id; last=self._cursor(cursor,scope)
        with self.store.connect() as db:
            plan=db.execute("SELECT total FROM duplicate_plans WHERE id=?", (plan_id,)).fetchone()
            if not plan: raise ValueError("重复清理预览不存在")
            rows=list(db.execute("SELECT id,image_id,source,keep_path,destination,size,status,reason FROM duplicate_plan_items WHERE plan_id=? AND id>? ORDER BY id LIMIT ?", (plan_id,last,limit+1)))
        more=len(rows)>limit; rows=rows[:limit]
        return {"items":[{**dict(r),"name":Path(r["source"]).name} for r in rows],"total":plan["total"],"next_cursor":self._next(scope,rows[-1]["id"]) if more else None}

    def claim_plan(self, plan_id, kind="cleanup"):
        if kind not in ("cleanup","purge"): raise ValueError("清理预览类型无效")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE"); self._guard(db)
            cursor=db.execute("UPDATE duplicate_plans SET status='queued' WHERE id=? AND kind=? AND status='ready'", (plan_id,kind))
            if not cursor.rowcount: raise ValueError("预览已改变或执行，请重新预览")
            return db.execute("SELECT total FROM duplicate_plans WHERE id=?", (plan_id,)).fetchone()[0]

    @staticmethod
    def _matches(file,row,prefix=""):
        expected=tuple(row[prefix+k] for k in ("mtime_ns","ctime_ns","dev","ino"))
        actual=(file.info.st_mtime_ns,file.info.st_ctime_ns,file.info.st_dev,file.info.st_ino)
        if actual != expected or file.info.st_size != row["size"] or file.info.st_nlink != 1:
            raise ValueError("扫描后文件身份或内容属性已改变，请重新检查")
        if "change_ns" in row.keys() and file.change_ns!=row["change_ns"]:
            raise ValueError("扫描后文件可靠变更标识已改变，请重新检查")

    def _validate_index(self, row, *, purge=False):
        with self.store.connect() as db:
            keep=db.execute("SELECT path FROM images WHERE id=?", (row["keep_id"],)).fetchone()
            source=db.execute("SELECT path FROM images WHERE id=?", (row["image_id"],)).fetchone()
            if not keep or keep[0]!=row["keep_path"]:
                raise ValueError("保留项索引已改变，整组跳过")
            if not purge and (not source or source[0]!=row["source"]):
                raise ValueError("副本索引已改变，已跳过")
            if db.execute("SELECT 1 FROM journal WHERE image_id IN (?,?) AND state IN ('prepared','copying','verified','published','needs_review','committed') LIMIT 1", (row["keep_id"],row["image_id"])).fetchone():
                raise ValueError("图片具有可恢复或可撤销整理日志，已跳过")

    def _new_journal(self, job_id, row):
        with self.store.connect() as db:
            now=time.time()
            result=db.execute("INSERT INTO duplicate_journal(plan_id,item_id,job_id,image_id,source,destination,keep_path,size,digest,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (row["plan_id"],row["id"],job_id,row["image_id"],row["source"],row["destination"],row["keep_path"],row["size"],row["digest"],now,now))
        return result.lastrowid

    def _guard_journal(self,row):
        """Corrupt log paths cannot expand an approved cleanup's filesystem scope."""
        source=Path(row["source"])
        _token(row["plan_id"]);_id(row["item_id"]);_id(row["image_id"])
        if not source.is_absolute():raise ValueError("恢复日志源路径无效，文件保留")
        expected=source.parent/(".luma-duplicate-quarantine-"+self.library_id)/(row["plan_id"]+"-"+str(row["item_id"])+".lumaq")
        if Path(row["destination"])!=expected:raise ValueError("隔离日志目标与原批准预览不符，文件保留")
        with self.store.connect() as db:
            item=db.execute("SELECT p.* FROM duplicate_plan_items p JOIN duplicate_plans plan ON plan.id=p.plan_id WHERE p.id=? AND p.plan_id=? AND plan.kind='cleanup'", (row["item_id"],row["plan_id"])).fetchone()
        if not item or any(row[k]!=item[k] for k in ("image_id","source","destination","keep_path","size","digest")):
            raise ValueError("隔离日志与原批准预览不一致，文件保留")

    def _state(self, journal_id, state, error=""):
        with self.store.connect() as db:
            db.execute("UPDATE duplicate_journal SET state=?,error=?,updated_at=? WHERE id=?", (state,error,time.time(),journal_id))

    def _archive(self, journal_id, row):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current=db.execute("SELECT path FROM images WHERE id=?", (row["image_id"],)).fetchone()
            if not current or current[0]!=row["source"]:
                raise ValueError("移动后索引已改变，文件保留隔离区待检查")
            for table,key in (("images","id"),("image_tags","image_id"),("image_content_tags","image_id"),("image_content_overrides","image_id"),("image_roots","image_id")):
                db.execute(f"INSERT INTO duplicate_archived_{table} SELECT ?,t.* FROM {table} t WHERE {key}=?", (journal_id,row["image_id"]))
            db.execute("DELETE FROM images WHERE id=?", (row["image_id"],))
            db.execute("UPDATE duplicate_journal SET state='committed',updated_at=? WHERE id=?", (time.time(),journal_id))
            db.execute("UPDATE duplicate_plan_items SET status='quarantined',reason='' WHERE id=?", (row["id"],))
            db.execute("UPDATE plans SET status='stale' WHERE status='ready' AND id IN (SELECT plan_id FROM plan_items WHERE image_id=?)", (row["image_id"],))
            db.execute("UPDATE removal_plans SET status='stale' WHERE status='ready'")

    def _restore_ready(self, db, row):
        archived=db.execute("SELECT id,path,root_id FROM duplicate_archived_images WHERE duplicate_journal_id=?", (row["id"],)).fetchone()
        if not archived: raise ValueError("索引归档不存在，隔离文件保留")
        if db.execute("SELECT 1 FROM images WHERE path=? COLLATE NOCASE", (archived["path"],)).fetchone():
            raise ValueError("原路径已有索引，隔离文件保留，不覆盖")
        if db.execute("SELECT 1 FROM duplicate_archived_image_roots a WHERE a.duplicate_journal_id=? AND NOT EXISTS(SELECT 1 FROM roots r WHERE r.id=a.root_id)", (row["id"],)).fetchone():
            raise ValueError("原导入目录已移除，隔离文件保留待检查")
        if db.execute("SELECT 1 FROM duplicate_archived_image_content_tags a WHERE a.duplicate_journal_id=? AND NOT EXISTS(SELECT 1 FROM content_tags t WHERE t.id=a.tag_id)", (row["id"],)).fetchone():
            raise ValueError("原内容类别已移除，隔离文件保留待检查")
        if db.execute("SELECT 1 FROM duplicate_archived_image_content_overrides a WHERE a.duplicate_journal_id=? AND NOT EXISTS(SELECT 1 FROM content_tags t WHERE t.id=a.tag_id)", (row["id"],)).fetchone():
            raise ValueError("原人工内容类别已移除，隔离文件保留待检查")
        return archived

    def _restore_index(self, row):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE"); archived=self._restore_ready(db,row)
            columns=[r["name"] for r in db.execute("PRAGMA table_info(images)")]
            # SQLite may reuse a removed highest ID after the user imports more
            # files. Assign a fresh ID when necessary, never overwrite the new row.
            reuse=not db.execute("SELECT 1 FROM images WHERE id=?", (archived["id"],)).fetchone()
            if not reuse: columns.remove("id")
            quoted=",".join('"'+c+'"' for c in columns)
            inserted=db.execute(f"INSERT INTO images({quoted}) SELECT {quoted} FROM duplicate_archived_images WHERE duplicate_journal_id=?", (row["id"],))
            restored_id=archived["id"] if reuse else inserted.lastrowid
            from .model_references import update_image_references
            update_image_references(db,restored_id)
            for table in ("image_tags","image_content_tags","image_content_overrides","image_roots"):
                columns=[r["name"] for r in db.execute(f"PRAGMA table_info({table})")]
                quoted=",".join('"'+c+'"' for c in columns)
                projection=",".join("?" if c=="image_id" else '"'+c+'"' for c in columns)
                # images' primary-root trigger already recreated one relation.
                command="INSERT OR IGNORE" if table=="image_roots" else "INSERT"
                db.execute(f"{command} INTO {table}({quoted}) SELECT {projection} FROM duplicate_archived_{table} WHERE duplicate_journal_id=?", (restored_id,row["id"]))
            db.execute("UPDATE duplicate_journal SET state='restored',error='',updated_at=? WHERE id=?", (time.time(),row["id"]))
            self._forget_archive(db,row["id"])

    @staticmethod
    def _forget_archive(db,journal_id):
        for table in ("images","image_tags","image_content_tags","image_content_overrides","image_roots"):
            db.execute(f"DELETE FROM duplicate_archived_{table} WHERE duplicate_journal_id=?", (journal_id,))

    def _finish_purge(self,journal_id):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE duplicate_journal SET state='purged',error='',updated_at=? WHERE id=?", (time.time(),journal_id))
            self._forget_archive(db,journal_id)

    def execute_cleanup(self, manager, job_id, payload):
        self._execute_plan(manager,job_id,payload["plan_id"],purge=False)

    def execute_purge(self, manager, job_id, payload):
        self._execute_plan(manager,job_id,payload["plan_id"],purge=True)

    def _execute_plan(self, manager, job_id, plan_id, *, purge):
        cancelled=lambda:manager.cancelled(job_id)
        processed=errors=0
        with self.lock:
            with self.store.connect() as db:
                self._guard(db,job_id)
                plan=db.execute("SELECT * FROM duplicate_plans WHERE id=? AND status='queued' AND kind=?", (plan_id,"purge" if purge else "cleanup")).fetchone()
                if not plan: raise ValueError("清理预览未确认或已经执行")
                db.execute("UPDATE duplicate_plans SET status='running' WHERE id=?", (plan_id,))
            try:
                last_group=0
                while True:
                    with self.store.connect() as db:
                        groups=list(db.execute("SELECT group_id FROM duplicate_plan_items WHERE plan_id=? AND group_id>? GROUP BY group_id ORDER BY group_id LIMIT 100", (plan_id,last_group)))
                    if not groups: break
                    for group in groups:
                        if cancelled(): raise Cancelled()
                        last_group=group[0]
                        with self.store.connect() as db:
                            first=db.execute("SELECT * FROM duplicate_plan_items WHERE plan_id=? AND group_id=? ORDER BY id LIMIT 1", (plan_id,last_group)).fetchone()
                        try:
                            self._validate_index(first,purge=purge)
                            with VerifiedFile(first["keep_path"]) as keep:
                                self._matches(keep,first,"keep_")
                                if keep.digest(cancelled)!=first["digest"]:
                                    raise ValueError("保留项内容已改变，整组跳过")
                                last=0
                                while True:
                                    with self.store.connect() as db:
                                        rows=list(db.execute("SELECT * FROM duplicate_plan_items WHERE plan_id=? AND group_id=? AND id>? AND status='ready' ORDER BY id LIMIT 100", (plan_id,last_group,last)))
                                    if not rows: break
                                    for row in rows:
                                        if cancelled(): raise Cancelled()
                                        last=row["id"]
                                        try:
                                            self._mutate(row,keep,job_id,cancelled,purge=purge)
                                        except Cancelled: raise
                                        except Exception as exc:
                                            errors+=1
                                            with self.store.connect() as db:
                                                db.execute("UPDATE duplicate_plan_items SET status='error',reason=? WHERE id=?", (str(exc)[:300],row["id"]))
                                        processed+=1
                                        manager.progress(job_id,processed=processed,errors=errors,message=f"{'永久清空' if purge else '可恢复清理'} {processed}/{plan['total']}；失败 {errors}")
                        except Cancelled: raise
                        except Exception as exc:
                            with self.store.connect() as db:
                                count=db.execute("UPDATE duplicate_plan_items SET status='error',reason=? WHERE plan_id=? AND group_id=? AND status='ready'", (str(exc)[:300],plan_id,last_group)).rowcount
                            processed+=count; errors+=count
                            manager.progress(job_id,processed=processed,errors=errors,message="保留项不可用，整组跳过")
                manager.progress(job_id,processed=processed,errors=errors,message=("已永久清空已验证隔离副本；原保留项未修改" if purge else "已从图库清理至可恢复隔离区；未释放磁盘空间，TXT/JSON 保留原位置"))
            finally:
                with self.store.connect() as db:
                    db.execute("UPDATE duplicate_plans SET status=? WHERE id=?", ("cancelled" if cancelled() else "finished",plan_id))

    def _mutate(self, row, keep, job_id, cancelled, *, purge):
        _token(row["plan_id"]);_id(row["id"])
        self._validate_index(row,purge=purge)
        if purge:
            with self.store.connect() as db:
                original=db.execute("SELECT * FROM duplicate_journal WHERE id=? AND state='committed'", (row["original_journal"],)).fetchone()
                if not original: raise ValueError("隔离副本已恢复或清空，请重新预览")
                if original["destination"]!=row["source"]: raise ValueError("隔离路径不匹配")
            self._guard_journal(original)
            if any(original[k]!=row[k] for k in ("image_id","keep_path","size","digest")):
                raise ValueError("永久清空预览与原隔离记录不符")
        with VerifiedFile(row["source"],mutable=True) as source:
            if not purge: self._matches(source,row)
            elif tuple(original[k] for k in ("q_mtime_ns","q_ctime_ns","q_dev","q_ino")) != (source.info.st_mtime_ns,source.info.st_ctime_ns,source.info.st_dev,source.info.st_ino):
                raise ValueError("隔离文件身份已改变，请保留并检查")
            # Every action rehashes AND compares bytes: hash collisions never delete.
            if source.digest(cancelled)!=row["digest"] or not keep.equals(source,cancelled):
                raise ValueError("文件内容不同或已改变，副本保留")
            if cancelled(): raise Cancelled()
            keep.verify(); source.verify()
            if purge:
                journal_id=original["id"]
                self._state(journal_id,"purge_prepared")
                try:
                    source.delete()
                except BaseException:
                    self._state(journal_id,"committed")
                    raise
            else:
                target=Path(row["destination"])
                expected=Path(row["source"]).parent/(".luma-duplicate-quarantine-"+self.library_id)/(row["plan_id"]+"-"+str(row["id"])+".lumaq")
                if target!=expected or not Path(row["source"]).is_absolute(): raise ValueError("隔离路径无效")
                check_directory_chain(target.parent)
                target.parent.mkdir(exist_ok=True)
                check_directory_chain(target.parent)
                journal_id=self._new_journal(job_id,row)
                try:
                    source.rename(target)
                    with self.store.connect() as db:
                        db.execute("UPDATE duplicate_journal SET state='moved',q_mtime_ns=?,q_ctime_ns=?,q_dev=?,q_ino=?,updated_at=? WHERE id=?", (source.info.st_mtime_ns,source.info.st_ctime_ns,source.info.st_dev,source.info.st_ino,time.time(),journal_id))
                except BaseException as exc:
                    self._state(journal_id,"needs_review",str(exc)[:300])
                    raise
        if purge:
            # Handle is closed: the actual OS deletion has now happened.
            self._finish_purge(journal_id)
            with self.store.connect() as db:
                db.execute("UPDATE duplicate_plan_items SET status='purged' WHERE id=?", (row["id"],))
        else:
            try:
                self._archive(journal_id,row)
            except BaseException as exc:
                self._state(journal_id,"needs_review",str(exc)[:300]); raise

    def undo_cleanup(self, manager, job_id, payload):
        original=payload["job_id"]; cancelled=lambda:manager.cancelled(job_id)
        processed=errors=last=0
        with self.lock:
            with self.store.connect() as db:
                self._guard(db,job_id)
                total=db.execute("SELECT COUNT(*) FROM duplicate_journal WHERE job_id=? AND state='committed'", (original,)).fetchone()[0]
            manager.progress(job_id,total=total,message="正在核验隔离文件与原路径，随后恢复")
            while True:
                with self.store.connect() as db:
                    rows=list(db.execute("SELECT * FROM duplicate_journal WHERE job_id=? AND state='committed' AND id>? ORDER BY id LIMIT 100", (original,last)))
                if not rows: break
                for row in rows:
                    if cancelled(): raise Cancelled()
                    last=row["id"]
                    try:
                        self._undo_one(row,cancelled)
                    except Cancelled: raise
                    except Exception as exc:
                        errors+=1
                        with self.store.connect() as db:
                            db.execute("UPDATE duplicate_journal SET error=?,updated_at=? WHERE id=? AND state='committed'", (str(exc)[:300],time.time(),row["id"]))
                    processed+=1
                    manager.progress(job_id,processed=processed,errors=errors,message=f"恢复隔离副本 {processed}，失败 {errors}；不覆盖原路径")

    def _undo_one(self,row,cancelled):
        self._guard_journal(row)
        source,target=Path(row["destination"]),Path(row["source"])
        check_directory_chain(target.parent)
        if target.exists() or target.is_symlink(): raise FileExistsError("原路径已存在，隔离副本保留，不覆盖")
        if not target.parent.is_dir(): raise ValueError("原目录不存在，请先恢复目录，隔离副本保留")
        with self.store.connect() as db: self._restore_ready(db,row)
        with VerifiedFile(source,mutable=True) as file:
            if tuple(row[k] for k in ("q_mtime_ns","q_ctime_ns","q_dev","q_ino"))!=(file.info.st_mtime_ns,file.info.st_ctime_ns,file.info.st_dev,file.info.st_ino):
                raise ValueError("隔离文件身份已改变，文件保留待检查")
            if file.digest(cancelled)!=row["digest"]: raise ValueError("隔离副本已改变，无法自动恢复")
            if cancelled(): raise Cancelled()
            self._state(row["id"],"restore_prepared")
            try:
                file.rename(target)
            except BaseException:
                self._state(row["id"],"committed"); raise
        try:
            self._restore_index(row)
        except BaseException as exc:
            self._state(row["id"],"needs_review",str(exc)[:300]); raise

    def history(self, limit=30):
        _limit(limit)
        with self.store.connect() as db:
            rows=list(db.execute("SELECT job_id,COUNT(*) AS count,SUM(size) AS bytes,SUM(state='committed') AS restorable,SUM(state='committed') AS purgeable,SUM(state='needs_review') AS needs_review,MAX(updated_at) AS updated_at FROM duplicate_journal GROUP BY job_id ORDER BY updated_at DESC LIMIT ?", (limit,)))
        return [{**dict(row),"status":"needs_review" if row["needs_review"] else "available" if row["restorable"] else "finished"} for row in rows]

    def recovery_status(self):
        with self.store.connect() as db:
            total=db.execute("SELECT COUNT(*) FROM duplicate_journal WHERE state IN ('prepared','moved','restore_prepared','purge_prepared','needs_review')").fetchone()[0]
            items=[dict(row) for row in db.execute("SELECT id,job_id,image_id,source,destination,state,error FROM duplicate_journal WHERE state IN ('prepared','moved','restore_prepared','purge_prepared','needs_review') ORDER BY id LIMIT 100")]
        return {"total":total,"items":items,"items_truncated":total>BATCH}

    def recovery_job(self, manager, job_id, payload):
        total=self.recovery_status()["total"]
        with self.lock:
            self.recover(lambda:manager.cancelled(job_id))
        remaining=self.recovery_status()["total"]
        manager.progress(job_id,processed=total,total=total,errors=remaining,message=f"中断记录检查完成；仍有 {remaining} 项需手工检查，未自动永久清空")

    def recover(self,cancelled=lambda:False):
        """Reconcile only already-approved journal entries; never purge at startup."""
        last=0
        while True:
            with self.store.connect() as db:
                rows=list(db.execute("SELECT * FROM duplicate_journal WHERE state IN ('prepared','moved','restore_prepared','purge_prepared','needs_review') AND id>? ORDER BY id LIMIT 100", (last,)))
            if not rows: break
            for row in rows:
                if cancelled(): raise Cancelled()
                last=row["id"]; source,target=Path(row["source"]),Path(row["destination"])
                try:
                    self._guard_journal(row)
                    if row["state"]=="needs_review":
                        with self.store.connect() as db:
                            archived=bool(db.execute("SELECT 1 FROM duplicate_archived_images WHERE duplicate_journal_id=?", (row["id"],)).fetchone())
                        if archived and target.exists() and not source.exists():
                            with VerifiedFile(target) as file:
                                if file.digest(cancelled)!=row["digest"]: raise ValueError("隔离文件校验失败")
                            self._state(row["id"],"committed","恢复检查通过；隔离文件仍保留，可撤销或重新预览永久清空")
                            continue
                        if archived and source.exists() and not target.exists():
                            with VerifiedFile(source) as file:
                                if file.digest(cancelled)!=row["digest"]: raise ValueError("已恢复文件校验失败")
                            self._restore_index(row)
                            continue
                    if row["state"]=="purge_prepared":
                        if target.exists():
                            self._state(row["id"],"committed","永久清空中断；隔离副本仍保留，需重新预览")
                        else:
                            self._finish_purge(row["id"])
                        continue
                    if row["state"]=="restore_prepared":
                        if target.exists() and not source.exists():
                            self._state(row["id"],"committed","恢复在移动前中断；可再次恢复")
                        elif source.exists() and not target.exists():
                            with VerifiedFile(source) as file:
                                if file.digest(cancelled)!=row["digest"]: raise ValueError("已恢复文件校验失败")
                            self._restore_index(row)
                        else: raise ValueError("恢复中断后路径冲突，文件均保留")
                        continue
                    if source.exists() and not target.exists():
                        self._state(row["id"],"failed","隔离移动未发生；原文件保留，请重新检查")
                    elif target.exists() and not source.exists():
                        with VerifiedFile(target) as file:
                            if file.digest(cancelled)!=row["digest"]: raise ValueError("隔离文件校验失败")
                            with self.store.connect() as db:
                                db.execute("UPDATE duplicate_journal SET q_mtime_ns=?,q_ctime_ns=?,q_dev=?,q_ino=? WHERE id=?", (file.info.st_mtime_ns,file.info.st_ctime_ns,file.info.st_dev,file.info.st_ino,row["id"]))
                        with self.store.connect() as db:
                            item=db.execute("SELECT * FROM duplicate_plan_items WHERE id=?", (row["item_id"],)).fetchone()
                        self._archive(row["id"],item)
                    else: raise ValueError("隔离中断后路径冲突，文件均保留待检查")
                except Cancelled: raise
                except Exception as exc:
                    self._state(row["id"],"needs_review",str(exc)[:300])
        with self.store.connect() as db:
            db.execute("UPDATE duplicate_scans SET status='interrupted',error='上次检查中断；请重新检查',updated_at=? WHERE status IN ('queued','running')", (time.time(),))
            db.execute("UPDATE duplicate_plans SET status='interrupted' WHERE status IN ('queued','running')")
