"""Bounded background workers with durable progress and cancellation."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .organizer import Cancelled, reparse_point
from .model_registry import ModelRegistry


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif", ".heic", ".heif", ".ico", ".ppm", ".pgm", ".pbm", ".tga", ".jxl", ".psd"}


def apply_model_mappings(metadata, mappings, catalog=None):
    """Resolve only already parsed checkpoint evidence; never reads an image."""
    resolved = ModelRegistry.resolve(metadata, metadata, mappings, catalog=catalog)
    result = {**metadata, **resolved}
    parameters = dict(result.get("parameters") or {})
    if result.get("reason_code"):
        parameters["model_reason_code"] = result["reason_code"]
    for key in ("model_variant", "model_catalog_version", "model_catalog_sources"):
        if resolved.get(key):
            parameters[key] = resolved[key]
        elif key.startswith("model_catalog_"):
            parameters.pop(key, None)
    result["parameters"] = parameters
    return result


def scan_files(root, recursive=True, cancelled=lambda: False):
    """Depth-first scandir: holds at most one iterator per directory depth."""
    stack = []
    try:
        stack.append(os.scandir(root))
        while stack:
            if cancelled():
                raise Cancelled()
            try:
                entry = next(stack[-1])
            except StopIteration:
                stack.pop().close()
                continue
            except OSError:
                stack.pop().close()
                continue
            try:
                if entry.is_symlink() or reparse_point(entry.path,entry.stat(follow_symlinks=False)):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if recursive and len(stack) < 256:
                        try:
                            stack.append(os.scandir(entry.path))
                        except OSError:
                            yield entry.path, None, "无法访问目录"
                    continue
                if entry.is_file(follow_symlinks=False) and Path(entry.name).suffix.lower() in IMAGE_EXTENSIONS:
                    yield entry.path, entry.stat(follow_symlinks=False), None
            except OSError as exc:
                yield entry.path, None, str(exc)
    finally:
        for iterator in stack:
            iterator.close()


class JobManager:
    def __init__(self, store, organizer, handlers=None, *, automatic_recovery=True):
        self.store = store
        self.organizer = organizer
        self.handlers = dict(handlers or {})
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="luma-job")
        self._events = {}
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self.recovering = False
        with store.connect() as db:
            pending = db.execute("SELECT COUNT(*) FROM journal WHERE state IN ('prepared','copying','verified','published')").fetchone()[0]
            active_moves = db.execute("SELECT COUNT(*) FROM jobs WHERE kind IN ('organize','undo') AND status IN ('queued','running')").fetchone()[0]
            db.execute("UPDATE jobs SET status='interrupted',message=?,updated_at=? WHERE status IN ('queued','running')", ("上次运行被中断；文件未自动恢复，请先生成恢复预览。" if not automatic_recovery else "上次运行被中断，请检查恢复任务。",time.time()))
            db.execute("UPDATE plans SET status='interrupted' WHERE status IN ('queued','running')")
            # Duplicate journals require an explicit native recovery action. Startup
            # repairs database state only and never hashes/moves/purges those files.
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='duplicate_scans'").fetchone():
                db.execute("UPDATE duplicate_scans SET status='interrupted',error='上次检查中断；请重新检查',updated_at=? WHERE status IN ('queued','running')", (time.time(),))
                db.execute("UPDATE duplicate_plans SET status='interrupted' WHERE status IN ('building','queued','running')")
        if automatic_recovery and (pending or active_moves):
            self.recovering = True
            self.submit("recovery",{},pending+active_moves)

    def close(self):
        self._closing.set()
        with self._lock:
            for event in self._events.values():
                event.set()
        self.executor.shutdown(wait=True, cancel_futures=True)

    def get(self, job_id):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        result["cancel_requested"] = bool(result["cancel_requested"])
        return result

    def list(self):
        with self.store.connect() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM jobs ORDER BY created_at DESC LIMIT 100")]
        return [self.get(job_id) for job_id in ids]

    def progress(self, job_id, **values):
        values = {key: value for key, value in values.items() if key in ("status", "processed", "total", "errors", "message")}
        values["updated_at"] = time.time()
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?", [*values.values(),job_id])

    def cancelled(self, job_id):
        with self._lock:
            event = self._events.get(job_id)
        return bool(event and event.is_set())

    def cancel(self, job_id):
        with self._lock:
            event = self._events.get(job_id)
            if event:
                event.set()
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET cancel_requested=1,updated_at=? WHERE id=? AND status IN ('queued','running')", (time.time(),job_id))
        return self.get(job_id)

    def submit(self, kind, payload, total=0):
        job_id = uuid.uuid4().hex
        now = time.time()
        with self.store.connect() as db:
            db.execute("INSERT INTO jobs(id,kind,status,total,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (job_id,kind,"queued",total,json.dumps(payload),now,now))
        with self._lock:
            self._events[job_id] = threading.Event()
        self.executor.submit(self._run,job_id,kind,payload)
        return self.get(job_id)

    def _run(self, job_id, kind, payload):
        try:
            if self.cancelled(job_id):
                raise Cancelled()
            self.progress(job_id,status="running",message="任务正在运行")
            if kind in ("organize","undo") and kind in self.handlers:
                self.handlers[kind](self,job_id,payload)
            elif kind == "import":
                self._import(job_id,payload)
            elif kind == "organize":
                self.organizer.execute(self,job_id,payload["plan_id"])
            elif kind == "undo":
                self.organizer.undo(self,job_id,payload["job_id"])
            elif kind == "recovery":
                try:
                    with self.organizer.lock:
                        self.organizer.recover(lambda:self.cancelled(job_id) or self._closing.is_set())
                    result = self.get(job_id)
                    with self.store.connect() as db:
                        unresolved = db.execute("SELECT COUNT(*) FROM journal WHERE state='needs_review'").fetchone()[0]
                    self.progress(job_id,processed=result["total"],errors=unresolved,message="中断移动的恢复检查已完成")
                finally:
                    self.recovering = False
            elif kind in self.handlers:
                self.handlers[kind](self,job_id,payload)
            else:
                raise ValueError("不支持的后台任务类型")
            result = self.get(job_id)
            message=result["message"] if kind in self.handlers else f"完成：已处理 {result['processed']} 张，错误 {result['errors']} 张"
            self.progress(job_id,status="completed" if not result["errors"] else "completed_with_errors",message=message)
        except Cancelled:
            if kind == "duplicate_purge":
                message="永久清空已取消；已清空副本无法撤销，其余隔离副本保留"
            elif kind in ("duplicate_cleanup","duplicate_undo"):
                message="重复清理任务已取消；已隔离副本可恢复，保留项未修改"
            elif kind in ("duplicate_recovery","file_recovery"):
                message="恢复检查已取消；已完成的恢复保留，剩余项待复核；未自动永久清空"
            else:
                message="任务已取消；已完成的移动可通过撤销恢复" if kind in ("organize","undo","recovery") else "任务已取消；原图未修改，已保存的识别结果保留"
            self.progress(job_id,status="cancelled",message=message)
        except Exception as exc:
            self.progress(job_id,status="failed",message=str(exc))
        finally:
            if kind=="file_recovery":self.recovering=False
            with self._lock:
                self._events.pop(job_id,None)

    def _import(self, job_id, payload):
        from .metadata import extract_metadata
        # A job uses one mapping snapshot throughout its bounded batches. Native
        # submissions capture it before queuing; legacy callers capture at start.
        mappings = payload.get("model_mappings")
        if mappings is None:
            mappings = ModelRegistry(self.store).list()
        catalog=payload.get("model_catalog")
        catalog_entries=catalog["entries"] if isinstance(catalog,dict) else ModelRegistry.catalog_snapshot()["entries"]
        root = Path(payload["path"]).resolve()
        root_id = self.store.add_root(root)
        processed, errors, unchanged = 0, 0, 0
        scanner = iter(scan_files(root,payload.get("recursive",True),lambda: self.cancelled(job_id)))
        while True:
            batch = []
            for _ in range(100):
                try:
                    batch.append(next(scanner))
                except StopIteration:
                    break
            if not batch:
                break
            signatures = self.store.signatures([path for path,info,error in batch if not error])
            writes = []
            for path, info, scan_error in batch:
                if self.cancelled(job_id):
                    raise Cancelled()
                if scan_error:
                    errors += 1
                else:
                    try:
                        if self.store.same_signature(path,info,signatures.get(path)):
                            unchanged += 1
                        else:
                            companion_before = self.store.sidecar_signatures(path)
                            metadata = apply_model_mappings(extract_metadata(path), mappings, catalog_entries)
                            after = Path(path).stat()
                            if (info.st_size,info.st_mtime_ns) != (after.st_size,after.st_mtime_ns) or companion_before != self.store.sidecar_signatures(path):
                                raise ValueError("扫描期间文件发生变化")
                            writes.append((path,root_id,info,metadata))
                    except Exception as exc:
                        errors += 1
                        self.progress(job_id,message=f"跳过 {Path(path).name}: {str(exc)[:180]}")
                processed += 1
            # One durable transaction per bounded batch, rather than per image.
            if writes:
                self.store.upsert_many(writes)
            self.store.attach_root_images(root_id,[path for path,info,error in batch if not error and info is not None])
            self.progress(job_id,processed=processed,total=processed,errors=errors,message=f"已扫描 {processed} 张，复用 {unchanged} 张；扫描完成前总量待定")
        self.progress(job_id,processed=processed,total=processed,errors=errors,message=f"扫描结束，复用 {unchanged} 张")
