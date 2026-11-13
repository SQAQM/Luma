"""Native service facade for explicitly started model/network operations."""
from __future__ import annotations

from backend.civitai import CivitaiClient
from backend.credentials import CivitaiCredentials
from backend.model_library import ModelLibrary
from backend.organizer import Cancelled

MODEL_TASKS = ("model_scan", "model_sync", "model_links", "civitai_image_lookup", "model_info_cleanup", "model_info_restore")


class ModelService:
    def _initialize_models(self, client=None):
        self.civitai_credentials = CivitaiCredentials(self.store.directory / "credentials")
        self.civitai_client = client or CivitaiClient(token_provider=self.civitai_credentials.get)
        # ModelLibrary owns a reentrant inventory lock. Organizer's nonreentrant
        # lock is held once, by _run_model_action, across a whole job.
        self.model_library = ModelLibrary(self.store, client=self.civitai_client)

    def model_roots(self):
        with self._operation(): return self.model_library.roots()

    def add_model_root(self, path):
        with self._operation(), self._submission_lock:
            self._ensure_ready(); self._ensure_no_active(MODEL_TASKS)
            if not isinstance(path, str) or not path or len(path) > 32767: raise ValueError("请选择模型目录")
            return self.model_library.add_root(path)

    def remove_model_root(self, root_id):
        with self._operation(), self._submission_lock:
            self._ensure_ready(); self._ensure_no_active(MODEL_TASKS)
            return self.model_library.remove_root(root_id)

    def models(self, filters=None, cursor=None, limit=60):
        with self._operation():
            if not isinstance(filters or {}, dict) or any(k not in ('root_id', 'q','types','families') for k in (filters or {})):
                raise ValueError("模型筛选条件无效")
            return self.model_library.list_models(**(filters or {}), cursor=cursor, limit=limit)

    def model_detail(self, model_id):
        with self._operation(): return self.model_library.detail(model_id)

    def model_cover_path(self, model_id):
        with self._operation(): return self.model_library.cover_path(model_id)

    def model_image_filters(self, model_id, match='verified'):
        with self._operation(): return self.model_library.image_filters(model_id, match)

    def civitai_status(self):
        with self._operation(): return self.civitai_credentials.status()

    def configure_civitai(self, token, remember=True):
        with self._operation(), self._submission_lock:
            self._ensure_ready(); self._ensure_no_active(MODEL_TASKS)
            if type(remember) is not bool: raise ValueError("密钥保存选项无效")
            self.civitai_credentials.set(token, remember)
            return self.civitai_credentials.status()

    def clear_civitai(self):
        with self._operation(), self._submission_lock:
            self._ensure_no_active(MODEL_TASKS)
            self.civitai_credentials.clear()
            return self.civitai_credentials.status()

    def connect_civitai(self, token, remember=True):
        with self._operation(), self._submission_lock:
            self._ensure_ready(); self._ensure_no_active(MODEL_TASKS)
            if type(remember) is not bool: raise ValueError('密钥保存选项无效')
            self.civitai_credentials.set(token, remember)
            return {**self.civitai_client.check_connection(), **self.civitai_credentials.status()}

    def test_civitai_connection(self):
        with self._operation():
            self._ensure_ready()
            return self.civitai_client.check_connection()

    def _submit_model_job(self, kind, payload, total=0):
        from .service import INDEX_TASKS
        self._ensure_ready(); self._ensure_no_active(INDEX_TASKS)
        return self.manager.submit(kind, payload, total)

    def scan_model_directory(self, root_id):
        with self._operation(), self._submission_lock:
            if type(root_id) is not int or not 1 <= root_id <= 2**63-1: raise ValueError("模型目录无效")
            return self._submit_model_job('model_scan', {'root_id': root_id})

    @staticmethod
    def _selected_models(ids):
        if not isinstance(ids, list) or not 1 <= len(ids) <= 60 or any(type(i) is not int or not 1 <= i <= 2**63-1 for i in ids):
            raise ValueError("请明确选择1至60个项目")
        return list(dict.fromkeys(ids))

    def sync_models(self, ids, download_covers=True,force_refresh=False,compatibility=False):
        with self._operation(), self._submission_lock:
            ids = self._selected_models(ids)
            if any(type(v) is not bool for v in (download_covers,force_refresh,compatibility)): raise ValueError("模型资料选项无效")
            return self._submit_model_job('model_sync', {'ids': ids, 'download_covers': download_covers,'force_refresh':force_refresh,'compatibility':compatibility}, len(ids))

    def sync_model_scope(self, filters, download_covers=True,force_refresh=False,compatibility=False):
        from .service import INDEX_TASKS
        from backend import model_sync_scope
        with self._operation(),self._submission_lock:
            self._ensure_ready();self._ensure_no_active(INDEX_TASKS)
            if any(type(v) is not bool for v in (download_covers,force_refresh,compatibility)):raise ValueError('模型资料选项无效')
            token,total=model_sync_scope.prepare(self.model_library,filters)
            try:return self._submit_model_job('model_sync',{'scope_id':token,'download_covers':download_covers,'force_refresh':force_refresh,'compatibility':compatibility},total)
            except BaseException:model_sync_scope.discard(self.model_library,token);raise

    def retry_model_sync(self,job_id):
        from .service import INDEX_TASKS
        from backend import model_sync_scope
        with self._operation(),self._submission_lock:
            self._ensure_ready();self._ensure_no_active(INDEX_TASKS)
            if not isinstance(job_id,str) or len(job_id)!=32 or any(c not in '0123456789abcdef' for c in job_id):raise ValueError('任务无效')
            previous=self.manager.get(job_id)
            if previous is None or previous['kind']!='model_sync' or previous['status'] in ('queued','running'):raise ValueError('任务不能重试')
            token,total=model_sync_scope.prepare_retry(self.model_library,job_id)
            try:
                if not total:raise ValueError('没有失败项目')
                return self._submit_model_job('model_sync',{'scope_id':token,'download_covers':previous['payload'].get('download_covers',False),'force_refresh':previous['payload'].get('force_refresh',False),'compatibility':previous['payload'].get('compatibility',False)},total)
            except BaseException:model_sync_scope.discard(self.model_library,token);raise

    def preview_model_info_cleanup(self,root_id=None):
        from .service import INDEX_TASKS
        from backend.model_sidecar_cleanup import preview
        with self._operation(),self._submission_lock:
            self._ensure_ready();self._ensure_no_active(INDEX_TASKS)
            return preview(self.model_library,root_id)

    def submit_model_info_cleanup(self,plan_id,confirmed,restoring=False):
        if confirmed is not True:raise ValueError('请先确认资料清单')
        if not isinstance(plan_id,str) or len(plan_id)!=32 or any(c not in '0123456789abcdef' for c in plan_id):raise ValueError('资料清单无效')
        with self._operation(),self._submission_lock:
            return self._submit_model_job('model_info_restore' if restoring else 'model_info_cleanup',{'plan_id':plan_id,'confirmed':True})

    def _model_info_cleanup_job(self,manager,job_id,payload):
        from backend.model_sidecar_cleanup import apply
        return self._run_model_action(lambda m,j,p:apply(self.model_library,m,j,p),manager,job_id,payload)

    def _model_info_restore_job(self,manager,job_id,payload):
        from backend.model_sidecar_cleanup import undo
        return self._run_model_action(lambda m,j,p:undo(self.model_library,m,j,p),manager,job_id,payload)

    def _cached_model_scope(self, ids=None):
        with self.store.connect() as db:
            high = db.execute('SELECT COALESCE(MAX(id),0) FROM images').fetchone()[0]
        return {'ids': ids, 'root_id': None, 'highwater': high, 'model_mappings': self.model_registry.list(),
                'model_catalog': self.model_registry.combined_catalog_snapshot()}

    def rebuild_model_links(self):
        with self._operation(), self._submission_lock:
            return self._submit_model_job('model_links', self._cached_model_scope())

    def lookup_image_models(self, image_ids):
        """User-confirmed hashes only, never image bytes or image metadata text."""
        with self._operation(), self._submission_lock:
            ids = self._selected_models(image_ids)
            # Validate presence and typed hashes before a network job is queued.
            if not self.model_library.image_lookup_hashes(ids):
                raise ValueError("所选图片没有可查询的完整SHA256或明确10位AutoV2模型哈希。请先重新读取生成信息；不会按提示词猜测模型。")
            return self._submit_model_job('civitai_image_lookup', {'ids': ids}, len(ids))

    def _run_model_action(self, action, manager, job_id, payload):
        from .service import ServiceError
        try:
            with self.organizer.lock:
                if manager.cancelled(job_id) or self._closing.is_set(): raise Cancelled()
                return action(manager, job_id, payload)
        except Cancelled: raise
        except Exception:
            raise ServiceError("模型任务未完成。已有资料保留，请检查目录或网络后重试。", "model_task_failed") from None

    def _model_scan_job(self, manager, job_id, payload):
        return self._run_model_action(self.model_library.scan, manager, job_id, payload)

    def _model_sync_job(self, manager, job_id, payload):
        return self._run_model_action(self.model_library.sync, manager, job_id, payload)

    def _model_links_job(self, manager, job_id, payload):
        def update(m, ident, scope):
            self._reclassify_models_job(m, ident, scope)
            self.model_library.rebuild_links(m, ident, scope)
        return self._run_model_action(update, manager, job_id, payload)

    def _image_model_lookup_job(self, manager, job_id, payload):
        def lookup(m, ident, values):
            from backend.civitai import CivitaiError
            cancelled = lambda: m.cancelled(ident) or self._closing.is_set()
            hashes = self.model_library.image_lookup_hashes(values['ids'])
            errors = 0
            for number, item in enumerate(hashes, 1):
                if cancelled(): raise Cancelled()
                try:
                    record = self.civitai_client.version_by_hash(item['hash'], cancelled=cancelled)
                    if cancelled(): raise Cancelled()
                    if not self.model_library.remember_version(record): raise ValueError('unverified')
                except Cancelled: raise
                except Exception:
                    if cancelled(): raise Cancelled()
                    errors += 1
                m.progress(ident, processed=number, total=len(hashes), errors=errors, message=f"Civitai模型查询：{number}/{len(hashes)}，未确认 {errors}；原图与提示词未上传")
            scope = self._cached_model_scope(values['ids'])
            self._reclassify_models_job(m, ident, scope)
            self.model_library.rebuild_links(m, ident, scope)
            m.progress(ident, processed=len(hashes), total=len(hashes), errors=errors,
                       message=f"Civitai模型查询完成：已确认 {len(hashes)-errors}，未确认 {errors}；已更新所选缓存，原图未读取")
        return self._run_model_action(lookup, manager, job_id, payload)
