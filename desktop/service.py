"""Thread-safe local facade for Qt workers; no HTTP server or browser imports.

The UI calls short catalog reads and long previews/thumbnail work from workers.
Import, organize, undo, and recovery run in the existing durable job manager.
``close`` cancels tasks and waits for their safe stopping points and active calls;
run it in a shutdown worker while the native window displays closing progress.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from backend.jobs import JobManager, apply_model_mappings
from backend.duplicates import DuplicateManager
from backend.model_registry import ModelRegistry
from backend.organizer import Cancelled, Organizer, absolute_directory, check_directory_chain, signature
from backend.store import Store, default_data_dir, SORTS
from .model_service import ModelService, MODEL_TASKS


KINDS = {"model", "tag", "content_tag", "artist", "format"}
PREFERENCES = {"theme", "density", "sort", "showTags", "thumbnailSize", "language", "lastImportPath", "lastDestination", "sidebarCollapsed", "windowGeometry", "windowMaximized", "reduceMotion", "thumbnailCacheMiB"}
MAX_THUMBNAIL_PIXELS = 50_000_000
DUPLICATE_TASKS = ("duplicate_scan", "duplicate_cleanup", "duplicate_undo", "duplicate_purge", "duplicate_recovery")
INDEX_TASKS = ("import", "organize", "undo", "remove_root", "classify_contents", "reclassify_models", "metadata_refresh", "recovery", "file_preview", "file_recovery", *DUPLICATE_TASKS, *MODEL_TASKS)


class ServiceError(ValueError):
    """A user-facing error, with an optional stable machine-readable code."""

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


def _text(value: Any, label: str, maximum: int, *, empty=False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ServiceError(f"{label}不能为空或超过 {maximum} 个字符")
    return value


def _id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ServiceError("图片 ID 无效")
    return value


class LibraryService(ModelService):
    def __init__(self, data_dir: str | Path | None = None, thumbnail_cache_limit_bytes: int | None = None, *, civitai_client=None, automatic_recovery=True):
        selected = Path(data_dir or default_data_dir()).expanduser()
        if not selected.is_absolute():
            raise ServiceError("用户数据目录必须是绝对路径")
        self.store = Store(selected.resolve())
        check_directory_chain(self.store.directory)
        self.automatic_recovery = automatic_recovery
        self.organizer = Organizer(self.store, secure_moves=not automatic_recovery)
        self.duplicates = DuplicateManager(self.store, self.organizer.lock)
        self.model_registry = ModelRegistry(self.store)
        self._initialize_models(civitai_client)
        self._condition = threading.Condition(threading.RLock())
        self._closing = threading.Event()
        self._closed = False
        self._active = 0
        self._submission_lock = threading.RLock()
        self._thumbnail_slots = threading.BoundedSemaphore(2)
        self._thumbnail_queue = threading.BoundedSemaphore(10)
        self._thumbnail_lock = threading.Lock()
        self._thumbnail_locks: dict[str, dict[str, Any]] = {}
        self._prune_lock = threading.Lock()
        self._vision_lock = threading.RLock()
        self._vision = None
        saved_quota = self.store.settings().get("thumbnailCacheMiB",512)
        if type(saved_quota) is not int or not 64<=saved_quota<=8192:saved_quota=512
        configured = thumbnail_cache_limit_bytes if thumbnail_cache_limit_bytes is not None else int(os.environ.get("LUMA_THUMB_CACHE_BYTES", str(saved_quota * 1024**2)))
        self.thumbnail_cache_limit_bytes = max(64 * 1024**2, int(configured))
        self.manager = JobManager(self.store, self.organizer,{
            "install_model":self._install_model_job,
            "classify_contents":self._classify_contents_job,
            "remove_root":self._remove_root_job,
            "reclassify_models":self._reclassify_models_job,
            "metadata_refresh":self._metadata_refresh_job,
            "model_scan": self._model_scan_job,
            "model_sync": self._model_sync_job,
            "model_links": self._model_links_job,
            "civitai_image_lookup": self._image_model_lookup_job,
            "duplicate_scan": self.duplicates.scan,
            "duplicate_cleanup": self.duplicates.execute_cleanup,
            "duplicate_undo": self.duplicates.undo_cleanup,
            "duplicate_purge": self.duplicates.execute_purge,
            "duplicate_recovery": self.duplicates.recovery_job,
        }, automatic_recovery=automatic_recovery)

    @property
    def recovering(self) -> bool:
        return self.manager.recovering

    @property
    def closed(self) -> bool:
        return self._closed

    @contextmanager
    def _operation(self):
        with self._condition:
            self._ensure_open()
            self._active += 1
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def _ensure_open(self):
        if self._closing.is_set() or self._closed:
            raise ServiceError("软件正在关闭，无法开始新的操作", "closed")

    def _ensure_ready(self):
        self._ensure_open()
        if not self.automatic_recovery:
            with self.store.connect() as db:
                pending=db.execute("SELECT 1 FROM journal WHERE state IN ('prepared','copying','verified','published','needs_review') LIMIT 1").fetchone()
                from backend.organizer import PENDING_MOVE_BUNDLES_SQL,PENDING_UNDO_SIDES_SQL
                for query in (PENDING_MOVE_BUNDLES_SQL,PENDING_UNDO_SIDES_SQL):
                    if not pending:pending=db.execute('SELECT 1 FROM ('+query+') LIMIT 1').fetchone()
            if pending:
                raise ServiceError("有中断移动待复核，请先生成恢复预览并明确启动检查", "recovery_required")
        if self.recovering:
            raise ServiceError("正在恢复上次中断的移动，请等待恢复任务完成", "recovering")

    def query(self, filters: dict | None = None, cursor: str | None = None, limit: int = 120) -> dict:
        with self._operation():
            filters = dict(filters or {})
            allowed = {"q", "model", "artist", "tag", "content_tag", "favorite", "format", "sort", "root_id", "model_file_id", "model_match"}
            if any(key not in allowed for key in filters):
                raise ServiceError("筛选条件无效")
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
                raise ServiceError("每页数量应在 1 至 500 之间")
            if cursor is not None:
                _text(cursor, "分页游标", 2048)
            for key, length in (("q", 2000), ("model", 1024), ("artist",1000), ("tag", 2048), ("format", 30)):
                if key in filters:
                    _text(filters[key], "筛选文字", length, empty=True)
            if filters.get("sort", "newest") not in SORTS:
                raise ServiceError("排序方式无效")
            if filters.get("favorite") is not None and not isinstance(filters["favorite"], bool):
                raise ServiceError("收藏筛选值无效")
            if filters.get("root_id") is not None:
                _id(filters["root_id"])
            if filters.get("model_file_id") is not None:
                _id(filters["model_file_id"])
            if "model_match" in filters and (filters["model_match"] not in ("verified", "name") or filters.get("model_file_id") is None):
                raise ServiceError("模型关联筛选无效")
            if filters.get("content_tag") is not None:
                value=filters["content_tag"]
                if isinstance(value,int):
                    _id(value)
                else:
                    _text(value,"内容 TAG",100,empty=True)
            return self.store.list_images(cursor=cursor, limit=limit, **filters)

    def stats(self) -> dict:
        with self._operation():
            return self.store.stats()

    def browse_neighbors(self,image_id,filters=None,before=4,after=4):
        with self._operation():
            _id(image_id)
            filters=dict(filters or {})
            # The same validation is used for gallery and viewer scopes.
            self.query(filters,None,1)
            for value in (before,after):
                if isinstance(value,bool) or not isinstance(value,int) or not 0<=value<=4:
                    raise ServiceError("相邻预览范围应在0至4之间")
            return self.store.browse_neighbors(image_id,filters,before,after)

    def roots(self) -> list[dict]:
        with self._operation():
            return self.store.roots()

    def _image(self, image_id: int) -> dict:
        image = self.store.get_image(_id(image_id))
        if image is None:
            raise ServiceError("图片不在索引中", "not_found")
        return image

    def detail(self, image_id: int) -> dict:
        with self._operation():
            return self._image(image_id)

    def patch(self, image_id: int, changes: dict) -> dict:
        with self._operation(), self._submission_lock:
            _id(image_id)
            self._ensure_no_active(DUPLICATE_TASKS)
            if not isinstance(changes, dict) or any(key not in ("favorite", "rating", "tags") for key in changes):
                raise ServiceError("仅可修改收藏、评分和 TAG")
            values = {key: value for key, value in changes.items() if value is not None}
            if "favorite" in values and not isinstance(values["favorite"], bool):
                raise ServiceError("收藏值无效")
            if "rating" in values and (isinstance(values["rating"], bool) or not isinstance(values["rating"], int) or not 0 <= values["rating"] <= 5):
                raise ServiceError("评分应在 0 至 5 之间")
            if "tags" in values:
                tags = values["tags"]
                if not isinstance(tags, list) or len(tags) > 5000 or any(not isinstance(tag, str) or len(tag) > 2048 for tag in tags):
                    raise ServiceError("TAG 数量或长度超过限制")
            result = self.store.patch_image(image_id, values)
            if result is None:
                raise ServiceError("图片不在索引中", "not_found")
            return result

    patch_image = patch

    def import_directory(self, path: str, recursive: bool = True) -> dict:
        with self._operation(), self._submission_lock:
            self._ensure_ready()
            if not self.automatic_recovery:self._ensure_no_active(INDEX_TASKS)
            self._ensure_no_active(("remove_root","reclassify_models"))
            _text(path, "图片目录", 32767)
            if not isinstance(recursive, bool):
                raise ServiceError("递归扫描选项无效")
            directory = absolute_directory(path)
            if not directory.is_dir():
                raise ServiceError("所选图片目录不存在", "not_found")
            return self.manager.submit("import", {"path": str(directory), "recursive": recursive,"model_mappings":self.model_registry.list(),"model_catalog":self.model_registry.combined_catalog_snapshot()})

    def _ensure_no_active(self,kinds, *, allow_duplicate_recovery=False):
        self._ensure_open()
        kinds = tuple(dict.fromkeys((*kinds, "metadata_refresh", "file_preview", "file_recovery", *DUPLICATE_TASKS, *MODEL_TASKS)))
        if not allow_duplicate_recovery and self.duplicates.recovery_status()["total"]:
            raise ServiceError("重复清理有中断记录。请在重复清理的可恢复隔离页明确启动检查，再开始新的图库操作。", "duplicate_recovery")
        with self.store.connect() as db:
            marks=",".join("?" for _ in kinds)
            row=db.execute(f"SELECT kind FROM jobs WHERE kind IN ({marks}) AND status IN ('queued','running') LIMIT 1",tuple(kinds)).fetchone()
        if row:
            raise ServiceError("相关后台任务正在运行，请先取消或等待任务完成","busy")

    def reread_metadata(self, image_ids):
        """Only an explicit native confirmation submits this original read."""
        from backend.metadata_refresh import snapshot_selection
        with self._operation(), self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(INDEX_TASKS)
            images = snapshot_selection(self.store, image_ids)
            payload = {"images":images,"model_mappings":self.model_registry.list(),"model_catalog":self.model_registry.combined_catalog_snapshot()}
            self._ensure_open()
            return self.manager.submit("metadata_refresh",payload,len(images))

    def _metadata_refresh_job(self, manager, job_id, payload):
        from backend.metadata_refresh import run_refresh
        cancelled = lambda: manager.cancelled(job_id) or self._closing.is_set()
        with self.organizer.lock:
            try:
                run_refresh(self.store,manager,job_id,payload,cancelled)
            except Cancelled:
                raise
            except Exception:
                # The durable manager otherwise serializes exception text.
                raise ServiceError("生成信息重新读取失败；未保存的条目保留旧信息", "metadata_refresh_failed") from None

    def scan_duplicates(self, root_id=None):
        """Explicit exact-byte scan; opening its dialog never calls this method."""
        with self._operation(), self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(INDEX_TASKS)
            if root_id is not None:
                _id(root_id)
            try:
                scan = self.duplicates.create_scan(root_id, cancelled=self._closing.is_set)
            except Cancelled as exc:
                raise ServiceError("软件关闭，重复扫描创建已取消；原图未修改", "closed") from exc
            return self.manager.submit("duplicate_scan", {"scan_id": scan["id"]}, scan.get("total", 0))

    def duplicate_scan(self, scan_id):
        with self._operation():
            return self.duplicates.get_scan(_text(scan_id, "重复扫描 ID", 100))

    def duplicate_latest_scan(self):
        with self._operation():
            return self.duplicates.latest_scan()

    def duplicate_recovery_status(self):
        """DB-only journal summary; opening the dialog performs no file recovery."""
        with self._operation():
            return self.duplicates.recovery_status()

    def recover_duplicates(self):
        with self._operation(), self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(INDEX_TASKS, allow_duplicate_recovery=True)
            total = self.duplicates.recovery_status()["total"]
            if not total:
                raise ServiceError("没有需要检查的重复清理中断记录")
            return self.manager.submit("duplicate_recovery", {}, total)

    def duplicate_plan_items(self, plan_id, cursor=None, limit=60):
        with self._operation():
            self._duplicate_page(cursor, limit)
            return self.duplicates.plan_items(_text(plan_id, "重复清理预览 ID", 100), cursor, limit)

    @staticmethod
    def _duplicate_page(cursor, limit):
        if cursor is not None:
            _text(cursor, "重复结果游标", 2048)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 60:
            raise ServiceError("重复结果每页数量应在 1 至 60 之间")

    def duplicate_groups(self, scan_id, cursor=None, limit=60):
        with self._operation():
            self._duplicate_page(cursor, limit)
            return self.duplicates.groups(_text(scan_id, "重复扫描 ID", 100), cursor, limit)

    def duplicate_members(self, group_id, cursor=None, limit=60):
        with self._operation():
            self._duplicate_page(cursor, limit)
            return self.duplicates.members(_id(group_id), cursor, limit)

    def select_duplicate_group(self, group_id, selected):
        with self._operation(), self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(INDEX_TASKS)
            if not isinstance(selected, bool):
                raise ServiceError("重复组选择应为布尔值")
            return self.duplicates.select_group(_id(group_id), selected)

    def select_duplicate_all(self, scan_id, selected=True):
        with self._operation(), self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(INDEX_TASKS)
            if not isinstance(selected, bool):
                raise ServiceError("重复组选择应为布尔值")
            return self.duplicates.select_all(_text(scan_id, "重复扫描 ID", 100), selected)

    def keep_duplicate(self, group_id, image_id):
        with self._operation(), self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(INDEX_TASKS)
            return self.duplicates.save_keep(_id(group_id), _id(image_id))

    def preview_duplicate_cleanup(self, scan_id):
        with self._operation(), self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(INDEX_TASKS)
            try:
                return self.duplicates.preview_cleanup(_text(scan_id, "重复扫描 ID", 100), cancelled=self._closing.is_set)
            except Cancelled as exc:
                raise ServiceError("软件关闭，重复清理预览已取消；原图未修改", "closed") from exc

    def execute_duplicate_cleanup(self, plan_id):
        return self._execute_duplicate_plan(plan_id, "cleanup", "duplicate_cleanup")

    def _execute_duplicate_plan(self, plan_id, kind, job_kind):
        with self._operation(), self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(INDEX_TASKS)
            total = self.duplicates.claim_plan(_text(plan_id, "重复清理预览 ID", 100), kind)
            return self.manager.submit(job_kind, {"plan_id": plan_id}, total)

    def duplicate_cleanup_history(self, limit=30):
        with self._operation():
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 30:
                raise ServiceError("隔离历史数量应在 1 至 30 之间")
            return self.duplicates.history(limit)

    def undo_duplicate_cleanup(self, job_id):
        with self._operation(), self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(INDEX_TASKS)
            identity = _text(job_id, "隔离任务 ID", 100)
            job = self.manager.get(identity)
            if not job or job["kind"] != "duplicate_cleanup" or job["status"] in ("queued", "running"):
                raise ServiceError("请选择已结束的重复隔离任务进行恢复")
            return self.manager.submit("duplicate_undo", {"job_id": identity})

    def preview_duplicate_purge(self, job_id):
        with self._operation(), self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(INDEX_TASKS)
            try:
                return self.duplicates.preview_purge(_text(job_id, "隔离任务 ID", 100), cancelled=self._closing.is_set)
            except Cancelled as exc:
                raise ServiceError("软件关闭，永久清空预览已取消；原图未修改", "closed") from exc

    def execute_duplicate_purge(self, plan_id):
        return self._execute_duplicate_plan(plan_id, "purge", "duplicate_purge")

    def model_mappings(self):
        with self._operation():
            return self.model_registry.list()

    def diagnose_models(self, *, cancelled=None):
        """Explicit read-only model leaves; never consults image/prompt columns."""
        from backend.model_diagnostics import diagnose_models as diagnose_model_index
        with self._operation():
            with self.store.connect() as db:
                entries = [{**dict(row), "enabled": bool(row["enabled"])} for row in
                           db.execute("SELECT kind,match,family,enabled FROM model_mappings ORDER BY id LIMIT 1000")]
            return diagnose_model_index(self.store.database, entries=entries,
                cancelled=lambda: self._closing.is_set() or bool(cancelled and cancelled()))

    def put_model_mappings(self,entries):
        """Saving definitions never scans files or reclassifies cached records."""
        with self._operation(),self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(("import","organize","undo","remove_root","classify_contents","reclassify_models","recovery"))
            try:
                return self.model_registry.replace(entries)
            except ValueError as exc:
                raise ServiceError(str(exc)) from exc

    def reclassify_models(self,ids=None,root_id=None,scope=None):
        """Explicit cached-only task, bounded to the initial indexed high-water ID."""
        with self._operation(),self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(("import","organize","undo","remove_root","classify_contents","reclassify_models","recovery"))
            if sum((ids is not None,root_id is not None,scope is not None))!=1 or (scope is not None and scope!="all"):
                raise ServiceError("请选择当前图片、导入目录或全部图库中的一个范围")
            if ids is not None:
                if not isinstance(ids,list) or not 1<=len(ids)<=10000:
                    raise ServiceError("请选择1至10000张图片；整个图库请使用全部范围")
                ids=list(dict.fromkeys(_id(value) for value in ids))
            if root_id is not None:
                _id(root_id)
            with self.store.connect() as db:
                if root_id is not None and not db.execute("SELECT 1 FROM roots WHERE id=?",(root_id,)).fetchone():
                    raise ServiceError("导入目录不存在","not_found")
                highwater=db.execute("SELECT COALESCE(MAX(id),0) FROM images").fetchone()[0]
                where,args=self._model_scope({"ids":ids,"root_id":root_id,"highwater":highwater},0)
                total=db.execute("SELECT COUNT(*) FROM images i WHERE "+where,args).fetchone()[0]
            if not total:
                raise ServiceError("所选范围内没有已索引图片","not_found")
            return self.manager.submit("reclassify_models",{"ids":ids,"root_id":root_id,"highwater":highwater,"model_mappings":self.model_registry.list(),"model_catalog":self.model_registry.combined_catalog_snapshot()},total)

    @staticmethod
    def _model_scope(payload,last_id):
        where="i.id>? AND i.id<=?"
        args=[last_id,payload["highwater"]]
        if payload.get("ids") is not None:
            where+=" AND i.id IN ("+",".join("?" for _ in payload["ids"])+")"
            args.extend(payload["ids"])
        if payload.get("root_id") is not None:
            where+=" AND EXISTS(SELECT 1 FROM image_roots r WHERE r.image_id=i.id AND r.root_id=?)"
            args.append(payload["root_id"])
        return where,args

    def _reclassify_models_job(self,manager,job_id,payload):
        from backend.metadata import reclassify_indexed
        processed=changed=skipped=conflicts=0
        last_id=0
        cancelled=lambda:manager.cancelled(job_id) or self._closing.is_set()
        while True:
            if cancelled():
                raise Cancelled()
            where,args=self._model_scope(payload,last_id)
            with self.store.connect() as db:
                rows=[dict(row) for row in db.execute("SELECT i.id,i.path,i.size,i.mtime_ns,i.indexed_at,i.model,i.family,i.confidence,i.reason,i.parameters,i.raw_metadata FROM images i WHERE "+where+" ORDER BY i.id LIMIT 100",args)]
            if not rows:
                break
            last_id=rows[-1]["id"]
            updates=[]
            for row in rows:
                if cancelled():
                    raise Cancelled()
                # Preserve an existing non-AI decision, including legacy or
                # manually maintained catalogs lacking serialized ai=False.
                if row["family"]=="非 AI":
                    continue
                cached={**row,"parameters":json.loads(row["parameters"]),"raw_metadata":json.loads(row["raw_metadata"])}
                catalog=payload.get("model_catalog")
                result=apply_model_mappings(reclassify_indexed(cached),payload["model_mappings"],catalog["entries"] if isinstance(catalog,dict) else None)
                values=(result["model"],result["family"],result["confidence"],result["reason"],json.dumps(result["parameters"],ensure_ascii=False,allow_nan=False))
                old=(row["model"],row["family"],row["confidence"],row["reason"],row["parameters"])
                conflicts+=result.get("reason_code")=="conflict"
                if values!=old:
                    updates.append((row,values))
            # Optimistic predicates reject re-scan/model-update races, while a
            # concurrent favorite/rating edit remains independent and preserved.
            batch_changed=batch_skipped=0
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                for row,values in updates:
                    if cancelled():
                        raise Cancelled()
                    guard=(row["id"],row["path"],row["size"],row["mtime_ns"],row["indexed_at"],row["model"],row["family"],row["confidence"],row["reason"],row["parameters"],row["raw_metadata"])
                    fields="model=?,family=?,confidence=?,reason=?,parameters=?" if values[0]!=row["model"] else "family=?,confidence=?,reason=?,parameters=?"
                    writes=values if values[0]!=row["model"] else values[1:]
                    update=db.execute("UPDATE images SET "+fields+" WHERE id=? AND path=? AND size=? AND mtime_ns=? AND indexed_at=? AND model=? AND family=? AND confidence=? AND reason=? AND parameters=? AND raw_metadata=?",(*writes,*guard))
                    if update.rowcount:
                        batch_changed+=1
                        from backend.model_references import update_image_references
                        update_image_references(db, row["id"])
                        db.execute("UPDATE plans SET status='cancelled' WHERE status='ready' AND id IN(SELECT plan_id FROM plan_items WHERE image_id=?)",(row["id"],))
                    else:
                        batch_skipped+=1
                if cancelled():
                    raise Cancelled()
            changed+=batch_changed
            skipped+=batch_skipped
            processed+=len(rows)
            manager.progress(job_id,processed=processed,message=f"已重新判定 {processed:,} 张，更新 {changed:,}，冲突 {conflicts:,}，索引变化跳过 {skipped:,}；仅读取缓存")
        manager.progress(job_id,processed=processed,message=f"模型重新判定完成：{processed:,} 张，更新 {changed:,}，冲突 {conflicts:,}，索引变化跳过 {skipped:,}；原图未读取或修改")

    def content_tags(self):
        with self._operation():
            return self.store.content_tag_definitions()

    def content_label_state(self, image_ids):
        with self._operation(),self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(INDEX_TASKS)
            return self.store.content_label_state(image_ids)

    def apply_content_label_overrides(self, image_ids, changes, expected_snapshot):
        with self._operation(),self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(INDEX_TASKS)
            return self.store.apply_content_label_overrides(image_ids,changes,expected_snapshot)

    def put_content_tags(self,definitions):
        import math
        with self._operation(),self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(INDEX_TASKS)
            if not isinstance(definitions,list) or len(definitions)>128:
                raise ServiceError("内容 TAG 应为列表，最多 128 个")
            normalized=[]
            names=set()
            for item in definitions:
                if not isinstance(item,dict):
                    raise ServiceError("内容 TAG 格式无效")
                identifier=item.get("id")
                if identifier is not None:
                    _id(identifier)
                name=_text(item.get("name"),"TAG 名称",100).strip()
                description=_text(item.get("description"),"判定描述",500).strip()
                if len(description)<5 or not description.isascii() or not any(letter.isalpha() for letter in description):
                    raise ServiceError("当前本地模型使用英文判定描述；TAG 名称可以使用中文")
                threshold=item.get("threshold",0.28)
                if isinstance(threshold,bool) or not isinstance(threshold,(float,int)) or not math.isfinite(threshold) or not 0.20<=threshold<=0.90:
                    raise ServiceError("相似度阈值应在 0.20 至 0.90 之间")
                enabled=item.get("enabled",True)
                if not isinstance(enabled,bool) or name.casefold() in names:
                    raise ServiceError("TAG 名称不能重复，启用选项应为布尔值")
                names.add(name.casefold())
                normalized.append({"id":identifier,"name":name,"description":description,"threshold":float(threshold),"enabled":enabled})
            return self.store.replace_content_tags(normalized)

    def _classifier(self):
        # Constructing the local engine never downloads a model or opens images.
        with self._vision_lock:
            if self._vision is None:
                from backend.vision import LocalContentClassifier
                self._vision=LocalContentClassifier(self.store.directory/"models"/"clip-vit-base-patch32-int8")
            return self._vision

    def vision_status(self):
        with self._operation():
            return self._classifier().status()

    def install_vision_model(self):
        with self._operation(),self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(("install_model","classify_contents","remove_root"))
            status=self._classifier().status()
            if status.get("ready"):
                raise ServiceError("本地模型已准备好，无需重复下载")
            if status.get("runtime_available") is False:
                raise ServiceError("本地推理运行库不可用，请使用完整的新版本便携包")
            return self.manager.submit("install_model",{},status.get("download_bytes",0))

    def _install_model_job(self,manager,job_id,payload):
        from backend.vision import VisionCancelled
        def progress(value):
            manager.progress(job_id,processed=value.get("downloaded",0),total=value.get("total",0),message=value.get("message","正在下载公开模型；图片不会上传"))
        try:
            self._classifier().install(progress=progress,cancelled=lambda:manager.cancelled(job_id))
        except VisionCancelled as exc:
            raise Cancelled() from exc
        result=manager.get(job_id)
        manager.progress(job_id,processed=result["total"],message="本地模型已准备好。尚未开始读取或识别图库图片。")

    def classify_contents(self,ids=None,root_id=None,scope=None):
        with self._operation(),self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(("classify_contents","install_model","remove_root","organize","undo","recovery","reclassify_models"))
            status=self._classifier().status()
            if not status.get("ready"):
                raise ServiceError(status.get("message") or "请先下载本地识别模型","not_ready")
            definitions=[item for item in self.store.content_tag_definitions() if item["enabled"]]
            if not definitions:
                raise ServiceError("请至少启用一个内容 TAG")
            if ids is not None:
                if not isinstance(ids,list) or not ids or len(ids)>10000:
                    raise ServiceError("请选择 1 至 10000 张图片，更多图片请选择目录范围")
                ids=list(dict.fromkeys(_id(value) for value in ids))
            elif root_id is not None:
                _id(root_id)
                if not any(root["id"]==root_id for root in self.store.roots()):
                    raise ServiceError("目录不在图库中")
            elif scope!="all":
                raise ServiceError("请明确选择图片、目录或全部图库范围")
            with self.store.connect() as db:
                highwater=db.execute("SELECT COALESCE(MAX(id),0) FROM images").fetchone()[0]
            filters={"root_id":root_id} if root_id is not None else {}
            total=len(ids) if ids is not None else self.store.list_images(limit=1,**filters)["total"]
            return self.manager.submit("classify_contents",{"ids":ids,"root_id":root_id,"highwater":highwater,"definitions":definitions},total)

    def _classify_contents_job(self,manager,job_id,payload):
        from backend.vision import VisionCancelled
        definitions=payload["definitions"]
        engine=self._classifier()
        processed=matched=unknown=errors=0
        cursor=0
        requested=payload.get("ids")
        def cancelled():
            return manager.cancelled(job_id)
        while True:
            if cancelled():
                raise Cancelled()
            with self.store.connect() as db:
                clauses=["i.id>?","i.id<=?"]
                arguments=[cursor,payload["highwater"]]
                if requested is not None:
                    clauses.append("i.id IN ("+",".join("?" for _ in requested)+")")
                    arguments.extend(requested)
                if payload.get("root_id") is not None:
                    clauses.append("EXISTS(SELECT 1 FROM image_roots r WHERE r.image_id=i.id AND r.root_id=?)")
                    arguments.append(payload["root_id"])
                rows=[dict(row) for row in db.execute("SELECT i.id,i.path,i.mtime_ns,i.size FROM images i WHERE "+" AND ".join(clauses)+" ORDER BY i.id LIMIT 32",arguments)]
            if not rows:
                break
            for image in rows:
                if cancelled():
                    raise Cancelled()
                cursor=image["id"]
                try:
                    before=signature(image["path"])
                    if (before.st_size,before.st_mtime_ns)!=(image["size"],image["mtime_ns"]):
                        raise ServiceError("图片发生变化，请重新导入后识别")
                    result=engine.classify(image["path"],definitions,cancelled=cancelled)
                    after=signature(image["path"])
                    if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):
                        raise ServiceError("识别期间图片发生变化，未保存结果")
                    if cancelled():
                        raise Cancelled()
                    revisions={d["id"]:d["revision"] for d in definitions}
                    matches=[{**match,"definition_revision":revisions[match["tag_id"]]} for match in result["matches"]]
                    self.store.set_content_matches(image["id"],matches,source="vision",model_id=result.get("model_id",""),image_signature=(image["size"],image["mtime_ns"]))
                    matched+=bool(result["matches"])
                    unknown+=not bool(result["matches"])
                except (Cancelled,VisionCancelled):
                    raise Cancelled()
                except Exception as exc:
                    if getattr(exc,"code",None) in ("integrity","not_ready","runtime_missing","unsafe","model_schema","model_load"):
                        raise ServiceError(str(exc),getattr(exc,"code")) from exc
                    # Do not expose private filenames or image content in job messages.
                    errors+=1
                processed+=1
                if processed%10==0:
                    manager.progress(job_id,processed=processed,errors=errors,message=f"本地识别：{processed:,} 张，匹配 {matched:,}，未确定 {unknown:,}；图片不上传")
        manager.progress(job_id,processed=processed,errors=errors,message=f"本地识别结束：{processed:,} 张，匹配 {matched:,}，未确定 {unknown:,}，失败 {errors:,}；原图未修改")

    def preview_remove_root(self,root_id):
        with self._operation():
            self._ensure_ready()
            self._ensure_no_active(("reclassify_models",))
            return self.store.preview_remove_root(_id(root_id))

    def discard_root_previews(self,root_id):
        with self._operation(),self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(("remove_root","classify_contents","import","organize","undo","recovery","reclassify_models"))
            _id(root_id)
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if not db.execute("SELECT 1 FROM roots WHERE id=?",(root_id,)).fetchone():
                    raise ServiceError("导入目录不存在")
                result=db.execute("UPDATE plans SET status='cancelled' WHERE status='ready' AND id IN (SELECT p.plan_id FROM plan_items p JOIN image_roots r ON r.image_id=p.image_id WHERE r.root_id=?)",(root_id,))
                return result.rowcount

    def remove_root(self,root_id,plan_id):
        with self._operation(),self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(("remove_root","classify_contents","import","organize","undo","recovery","reclassify_models"))
            _id(root_id)
            _text(plan_id,"目录移除预览",100)
            return self.manager.submit("remove_root",{"root_id":root_id,"plan_id":plan_id})

    def _remove_root_job(self,manager,job_id,payload):
        def progress(value,total=0,**kwargs):
            if isinstance(value,dict):
                manager.progress(job_id,processed=value.get("processed",0),total=value.get("total",0),message="正在移除图库索引；原图保持原位置")
            else:
                manager.progress(job_id,processed=int(value),total=int(total),message="正在移除图库索引；原图保持原位置",**kwargs)
        result=self.store.remove_root(payload["root_id"],plan_id=payload["plan_id"],job_id=job_id,cancelled=lambda:manager.cancelled(job_id),progress=progress)
        removed=result.get("removed",result.get("remove",0)) if isinstance(result,dict) else 0
        manager.progress(job_id,processed=result.get("processed",removed),total=result.get("total",removed),message=f"已移除目录及 {removed:,} 条索引；重叠目录的图片保留，原图未删除或移动")

    def jobs(self) -> list[dict]:
        with self._operation():
            return self.manager.list()

    def job(self, job_id: str) -> dict:
        with self._operation():
            _text(job_id, "任务 ID", 100)
            result = self.manager.get(job_id)
            if result is None:
                raise ServiceError("任务不存在", "not_found")
            return result

    def cancel(self, job_id: str) -> dict:
        with self._operation():
            _text(job_id, "任务 ID", 100)
            current = self.manager.get(job_id)
            if current is None:
                raise ServiceError("任务不存在", "not_found")
            if current["kind"] == "recovery" and current["status"] in ("queued", "running"):
                raise ServiceError("恢复检查不能手动取消；关闭软件会安全暂停，下次启动自动继续", "conflict")
            return self.manager.cancel(job_id)

    def rules(self) -> list[dict]:
        with self._operation():
            return self.store.rules()

    def put_rules(self, rules: list[dict]) -> list[dict]:
        with self._operation():
            if not isinstance(rules, list) or len(rules) > 1000:
                raise ServiceError("分类规则应为列表，最多 1000 条")
            normalized, seen = [], set()
            for rule in rules:
                if not isinstance(rule, dict):
                    raise ServiceError("分类规则格式无效")
                row = {key: _text(rule.get(key), "规则" + key, length) for key, length in (("id", 100), ("name", 200), ("match", 1000))}
                if row["id"] in seen:
                    raise ServiceError("规则 ID 不能重复")
                seen.add(row["id"])
                kind = rule.get("kind")
                if kind not in KINDS:
                    raise ServiceError("分类规则类型无效")
                destination = _text(rule.get("destination", ""), "目标目录", 32767, empty=True)
                enabled = rule.get("enabled", True)
                if not isinstance(enabled, bool):
                    raise ServiceError("规则启用选项无效")
                normalized.append({**row, "kind": kind, "destination": str(absolute_directory(destination)) if destination.strip() else "", "enabled": enabled})
            return self.store.replace_rules(normalized)

    def preview(self, kind: str, destination: str, ids: list[int] | None = None, scope: str | None = None) -> dict:
        with self._operation(),self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(("reclassify_models",))
            if kind not in KINDS:
                raise ServiceError("分类方式无效")
            _text(destination, "目标目录", 32767)
            if ids is None and scope != "all":
                raise ServiceError("请选择图片或指定整个图库")
            if ids is not None:
                if not isinstance(ids, list) or len(ids) > 10000:
                    raise ServiceError("最多一次选择 10000 张图片；整个图库请使用全部范围")
                for image_id in ids:
                    _id(image_id)
            try:
                return self.organizer.preview(kind, destination, ids, cancelled=self._closing.is_set)
            except Cancelled as exc:
                raise ServiceError("分类预览已取消，原图保持原位置", "closed") from exc

    def plan(self, plan_id: str) -> dict:
        with self._operation():
            return self.organizer.plan(_text(plan_id, "预览 ID", 100))

    def execute(self, plan_id: str) -> dict:
        with self._operation(), self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(("classify_contents","remove_root","reclassify_models"))
            _text(plan_id, "预览 ID", 100)
            total = self.organizer.claim_plan(plan_id)
            return self.manager.submit("organize", {"plan_id": plan_id}, total)

    def undo(self, job_id: str) -> dict:
        with self._operation(), self._submission_lock:
            self._ensure_ready()
            self._ensure_no_active(("classify_contents","remove_root","reclassify_models"))
            _text(job_id, "任务 ID", 100)
            job = self.manager.get(job_id)
            if job is None or job["kind"] != "organize":
                raise ServiceError("请选择一个整理任务进行撤销")
            if job["status"] in ("queued", "running"):
                raise ServiceError("请等待整理任务结束后撤销", "conflict")
            with self.store.connect() as db:
                total = db.execute("SELECT COUNT(*) FROM journal WHERE job_id=? AND asset_kind='image' AND state='committed'", (job_id,)).fetchone()[0]
                running = db.execute("SELECT id FROM jobs WHERE kind='undo' AND status IN ('queued','running') AND json_extract(payload,'$.job_id')=?", (job_id,)).fetchone()
            if running:
                raise ServiceError("撤销任务已经在运行", "conflict")
            if not total:
                raise ServiceError("这个任务没有可撤销的移动记录")
            return self.manager.submit("undo", {"job_id": job_id}, total)

    def settings(self) -> dict:
        with self._operation():
            return {**self.store.settings(), "data_dir": str(self.store.directory), "thumbnail_cache_limit_bytes": self.thumbnail_cache_limit_bytes}

    def put_settings(self, values: dict) -> dict:
        with self._operation():
            if not isinstance(values, dict) or any(key not in PREFERENCES for key in values):
                raise ServiceError("仅支持界面偏好设置")
            if "reduceMotion" in values and values["reduceMotion"] is not None and not isinstance(values["reduceMotion"],bool):
                raise ServiceError("减少动态效果必须是开关设置")
            if "thumbnailCacheMiB" in values and (type(values["thumbnailCacheMiB"]) is not int or not 64<=values["thumbnailCacheMiB"]<=8192):
                raise ServiceError("缩略缓存上限应为64至8192MiB的整数")
            try:
                if len(json.dumps(values, ensure_ascii=False, allow_nan=False)) > 32000:
                    raise ServiceError("设置内容超过限制")
            except (TypeError, ValueError) as exc:
                raise ServiceError("设置内容无法保存") from exc
            result = self.store.update_settings(values)
            if "thumbnailCacheMiB" in values:self.thumbnail_cache_limit_bytes=values["thumbnailCacheMiB"]*1024**2
            return {**result, "data_dir": str(self.store.directory), "thumbnail_cache_limit_bytes": self.thumbnail_cache_limit_bytes}

    def recovery(self) -> dict:
        with self._operation(), self.store.connect() as db:
            total = db.execute("SELECT COUNT(*) FROM journal WHERE state='needs_review'").fetchone()[0]
            items = [dict(row) for row in db.execute("SELECT id,job_id,image_id,source,destination,asset_kind,state,error,updated_at FROM journal WHERE state='needs_review' ORDER BY id DESC LIMIT 200")]
            return {"total": total, "items": items}

    def original_path(self, image_id: int) -> str:
        with self._operation():
            image = self._image(image_id)
            try:
                signature(image["path"])
            except (OSError, ValueError) as exc:
                raise ServiceError("原文件不存在或路径已变更", "not_found") from exc
            return image["path"]

    def _acquire(self, lock):
        while not lock.acquire(timeout=0.1):
            self._ensure_open()
        if self._closing.is_set():
            lock.release()
            self._ensure_open()

    def thumbnail_path(self, image_id: int, size: int = 400) -> str:
        with self._operation():
            if isinstance(size, bool) or not isinstance(size, int) or not 64 <= size <= 1200:
                raise ServiceError("缩略图尺寸应在 64 至 1200 之间")
            image = self._image(image_id)
            path = Path(image["path"])
            try:
                info = signature(path)
            except (OSError, ValueError) as exc:
                raise ServiceError("原文件不存在或路径已变更", "not_found") from exc
            key = hashlib.sha256(f"v1|{path}|{info.st_size}|{info.st_mtime_ns}|{size}".encode()).hexdigest()
            cache = self.store.directory / "thumbnails" / key[:2] / key[2:4] / (key + ".jpg")
            check_directory_chain(cache.parent)
            if cache.exists():
                signature(cache)
            else:
                if not self._thumbnail_queue.acquire(blocking=False):
                    raise ServiceError("预览队列已满，请稍后重试", "busy")
                with self._thumbnail_lock:
                    entry = self._thumbnail_locks.setdefault(key, {"lock": threading.Lock(), "users": 0})
                    entry["users"] += 1
                acquired = False
                try:
                    self._acquire(entry["lock"])
                    acquired = True
                    if not cache.exists():
                        self._acquire(self._thumbnail_slots)
                        try:
                            self._make_thumbnail(path, info, cache, size)
                        finally:
                            self._thumbnail_slots.release()
                finally:
                    if acquired:
                        entry["lock"].release()
                    with self._thumbnail_lock:
                        entry["users"] -= 1
                        if entry["users"] == 0:
                            self._thumbnail_locks.pop(key, None)
                    self._thumbnail_queue.release()
            self._remember_thumbnail(key, cache)
            return str(cache)

    def _make_thumbnail(self, path: Path, info, cache: Path, size: int):
        temporary = None
        try:
            # The explicit pixel check protects both decoder threads without changing
            # process-wide Pillow limits or warning filters used by the import worker.
            with Image.open(path) as source:
                if source.width * source.height > MAX_THUMBNAIL_PIXELS:
                    raise ServiceError("图片像素过大，兼容预览限制为 5000 万像素", "unsupported")
                source.draft("RGB", (size, size))
                source.thumbnail((size, size), Image.Resampling.LANCZOS, reducing_gap=3.0)
                self._ensure_open()
                rendered = ImageOps.exif_transpose(source)
                if rendered.mode in ("RGBA", "LA") or "transparency" in rendered.info:
                    rgba = rendered.convert("RGBA")
                    canvas = Image.new("RGB", rgba.size, (28, 30, 34))
                    canvas.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    canvas = rendered.convert("RGB")
                after = signature(path)
                if (after.st_size, after.st_mtime_ns, after.st_ino) != (info.st_size, info.st_mtime_ns, info.st_ino):
                    raise ServiceError("原图在预览期间发生变化，请重新加载", "conflict")
                check_directory_chain(cache.parent)
                cache.parent.mkdir(parents=True, exist_ok=True)
                check_directory_chain(cache.parent)
                temporary = cache.with_name(cache.name + "." + secrets.token_hex(6) + ".tmp")
                canvas.save(temporary, format="JPEG", quality=83, optimize=True)
                self._ensure_open()
                check_directory_chain(cache.parent)
                os.replace(temporary, cache)
        except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError) as exc:
            if isinstance(exc, ServiceError):
                raise
            raise ServiceError(f"无法生成图片预览：{str(exc)[:200]}", "unsupported") from exc
        finally:
            if temporary is not None:
                try:
                    check_directory_chain(temporary.parent)
                    temporary.unlink(missing_ok=True)
                except (OSError, ValueError):
                    pass

    def _remember_thumbnail(self, key: str, cache: Path):
        now = time.time()
        info = signature(cache)
        with self.store.connect() as db:
            db.execute("INSERT INTO thumbnail_cache(key,path,bytes,accessed_at) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET path=excluded.path,bytes=excluded.bytes,accessed_at=excluded.accessed_at WHERE thumbnail_cache.path<>excluded.path OR thumbnail_cache.bytes<>excluded.bytes OR thumbnail_cache.accessed_at<?", (key, str(cache), info.st_size, now, now - 3600))
        if not self._prune_lock.acquire(blocking=False):
            return
        try:
            with self.store.connect() as db:
                occupied = db.execute("SELECT bytes FROM cache_metrics WHERE id=1").fetchone()[0]
                if occupied <= self.thumbnail_cache_limit_bytes:
                    return
                rows = list(db.execute("SELECT key,path,bytes FROM thumbnail_cache WHERE key<>? ORDER BY accessed_at LIMIT 1000", (key,)))
                base = self.store.directory / "thumbnails"
                for row in rows:
                    if occupied <= self.thumbnail_cache_limit_bytes * 0.9 or self._closing.is_set():
                        break
                    path = Path(row["path"])
                    # A corrupted old cache record never grants permission to delete elsewhere.
                    if path.is_absolute() and path.is_relative_to(base) and path.resolve().is_relative_to(base.resolve()):
                        try:
                            check_directory_chain(path.parent)
                            if path.exists():
                                signature(path)
                                path.unlink()
                            if path.parent.parent.name in ('viewer-v2','viewer-v3') and path.suffix=='.png' and len(path.stem)==64 and all(c in '0123456789abcdef' for c in path.stem):
                                seal=path.with_suffix('.json')
                                if seal.exists():signature(seal);seal.unlink()
                        except (OSError, ValueError):
                            continue
                    db.execute("DELETE FROM thumbnail_cache WHERE key=?", (row["key"],))
                    occupied -= row["bytes"]
        finally:
            self._prune_lock.release()

    def close(self):
        """Wait for journals to reach safe stop points; this method is idempotent."""
        with self._condition:
            if self._closed:
                return
            if self._closing.is_set():
                while not self._closed:
                    self._condition.wait(timeout=0.1)
                return
            self._closing.set()
        try:
            with self._submission_lock:
                self.manager.close()
            with self._condition:
                while self._active:
                    self._condition.wait(timeout=0.1)
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET status='cancelled',cancel_requested=1,message='软件关闭，排队任务已取消',updated_at=? WHERE status='queued'", (time.time(),))
            with self.store.connect() as db:
                db.execute("PRAGMA wal_checkpoint(PASSIVE)")
        finally:
            cache=getattr(self,'_viewer_source_cache',None)
            if cache is not None:cache.close()
            with self._condition:
                self._closed = True
                self._condition.notify_all()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
