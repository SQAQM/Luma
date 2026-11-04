"""Loopback-only API for the Luma desktop image catalog."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, ImageOps, UnidentifiedImageError

from .jobs import JobManager
from .organizer import Organizer, absolute_directory, check_directory_chain, signature
from .store import Store


store = Store()
organizer = Organizer(store)
manager = JobManager(store, organizer)
TOKEN = os.environ.get("LUMA_TOKEN", "")
PORT = int(os.environ.get("LUMA_PORT", "8000"))
ALLOWED_PORTS = {PORT, 8000, 5173, 5174}
thumb_slots = threading.BoundedSemaphore(2)
thumb_queue = threading.BoundedSemaphore(10)
thumb_lock = threading.Lock()
thumb_locks = {}
cache_prune_lock = threading.Lock()
THUMB_CACHE_LIMIT = max(64 * 1024 * 1024,int(os.environ.get("LUMA_THUMB_CACHE_BYTES",str(4 * 1024**3))))
stats_lock = threading.Lock()
stats_cache = {"time": 0.0, "data": None}
Image.MAX_IMAGE_PIXELS = 50_000_000


@asynccontextmanager
async def lifespan(app):
    yield
    manager.close()


app = FastAPI(title="Luma Image Library", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def local_access(request: Request, call_next):
    hostname = request.url.hostname
    if hostname not in ("localhost", "127.0.0.1", "::1", "testserver"):
        return JSONResponse({"detail": "Only local connections are allowed"},status_code=403)
    origin = request.headers.get("origin")
    if origin:
        parsed = urlparse(origin)
        try:
            origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError:
            return JSONResponse({"detail": "Invalid origin"},status_code=403)
        if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1") or origin_port not in ALLOWED_PORTS:
            return JSONResponse({"detail": "Origin is not allowed"},status_code=403)
    if request.method == "OPTIONS" and origin:
        response = Response(status_code=204)
    else:
        if TOKEN and request.url.path.startswith("/api/") and request.url.path not in ("/api/health", "/api/session"):
            cookie = request.cookies.get("luma_session", "")
            bearer = request.headers.get("authorization", "")
            candidate = bearer[7:] if bearer.startswith("Bearer ") else cookie
            if not hmac.compare_digest(candidate,TOKEN):
                return JSONResponse({"detail": "需要本地会话；请使用启动器打开软件"},status_code=401)
        response = await call_next(request)
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
        response.headers["Vary"] = "Origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


class SessionInput(BaseModel):
    token: str = Field(max_length=512)


@app.post("/api/session")
def session(data: SessionInput, response: Response):
    if TOKEN and not hmac.compare_digest(data.token,TOKEN):
        raise HTTPException(401,"无效的本地会话")
    response.set_cookie("luma_session",TOKEN or "local",httponly=True,samesite="strict",max_age=86400,path="/")
    return {"ok":True}


@app.get("/api/health")
def health():
    try:
        from .metadata import supported_formats
        formats = supported_formats()
    except (ImportError, TypeError):
        formats = sorted(Image.registered_extensions())
    return {"ok":True,"service":"luma","version":"0.1.0","database":"sqlite-wal","formats":formats,"authentication":bool(TOKEN),"max_thumbnail_pixels":Image.MAX_IMAGE_PIXELS,"recovering":manager.recovering}


@app.get("/api/stats")
def stats():
    with stats_lock:
        if stats_cache["data"] is None or time.monotonic()-stats_cache["time"] > 3:
            stats_cache.update(data=store.stats(),time=time.monotonic())
        return stats_cache["data"]


@app.get("/api/images")
def images(cursor: str | None = Query(None,max_length=2048),limit: int = Query(120,ge=1,le=500),q: str = Query("",max_length=2000),model: str = Query("",max_length=1024),tag: str = Query("",max_length=2048),favorite: bool | None = None,format: str = Query("",max_length=30),sort: Literal["newest","oldest","name"] = "newest",root_id: int | None = None):
    try:
        return store.list_images(cursor=cursor,limit=limit,q=q,model=model,tag=tag,favorite=favorite,format=format,sort=sort,root_id=root_id)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc


def image_or_404(image_id):
    image = store.get_image(image_id)
    if not image:
        raise HTTPException(404,"图片不在索引中")
    return image


@app.get("/api/images/{image_id}")
def image_detail(image_id: int):
    return image_or_404(image_id)


class ImagePatch(BaseModel):
    favorite: bool | None = None
    rating: int | None = Field(None,ge=0,le=5)
    tags: list[str] | None = Field(None,max_length=5000)


@app.patch("/api/images/{image_id}")
def update_image(image_id: int, data: ImagePatch):
    values = data.model_dump(exclude_none=True)
    if "tags" in values and any(len(tag)>2048 for tag in values["tags"]):
        raise HTTPException(422,"单个 TAG 过长")
    result = store.patch_image(image_id,values)
    if result is None:
        raise HTTPException(404,"图片不在索引中")
    stats_cache["time"] = 0
    return result


def source_file(image):
    path = Path(image["path"])
    try:
        signature(path)
    except (OSError,ValueError):
        raise HTTPException(404,"原文件不存在或已变更")
    return path


def remember_thumbnail(key, cache):
    now = time.time()
    with store.connect() as db:
        db.execute("INSERT INTO thumbnail_cache(key,path,bytes,accessed_at) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET accessed_at=excluded.accessed_at WHERE thumbnail_cache.accessed_at<?", (key,str(cache),cache.stat().st_size,now,now-3600))
    # A small single worker trims old entries. Cache size is durable, not a filesystem-wide scan.
    if cache_prune_lock.acquire(blocking=False):
        try:
            with store.connect() as db:
                occupied = db.execute("SELECT bytes FROM cache_metrics WHERE id=1").fetchone()[0]
                if occupied <= THUMB_CACHE_LIMIT:
                    return
                stale = list(db.execute("SELECT key,path,bytes FROM thumbnail_cache WHERE key<>? ORDER BY accessed_at LIMIT 1000",(key,)))
                for row in stale:
                    if occupied <= THUMB_CACHE_LIMIT * 0.9:
                        break
                    path = Path(row["path"])
                    if path.resolve().is_relative_to((store.directory/"thumbnails").resolve()):
                        try:
                            path.unlink(missing_ok=True)
                        except OSError:
                            continue
                    db.execute("DELETE FROM thumbnail_cache WHERE key=?",(row["key"],))
                    occupied -= row["bytes"]
        finally:
            cache_prune_lock.release()


@app.get("/api/images/{image_id}/original")
def original(image_id: int):
    image = image_or_404(image_id)
    path = source_file(image)
    return FileResponse(path,filename=image["name"],content_disposition_type="inline",headers={"Cache-Control":"private, max-age=60"})


@app.get("/api/images/{image_id}/thumbnail")
def thumbnail(image_id: int, size: int = Query(400,ge=64,le=1200)):
    image = image_or_404(image_id)
    path = source_file(image)
    info = path.stat()
    key = hashlib.sha256(f"v1|{path}|{info.st_size}|{info.st_mtime_ns}|{size}".encode()).hexdigest()
    cache = store.directory / "thumbnails" / key[:2] / key[2:4] / (key+".jpg")
    if not cache.exists():
        if not thumb_queue.acquire(blocking=False):
            raise HTTPException(429,"预览队列已满，请稍后重试",headers={"Retry-After":"1"})
        with thumb_lock:
            entry = thumb_locks.setdefault(key,{"lock":threading.Lock(),"users":0})
            entry["users"] += 1
        try:
            with entry["lock"]:
                if not cache.exists():
                    with thumb_slots:
                        try:
                            with warnings.catch_warnings():
                                warnings.simplefilter("error",Image.DecompressionBombWarning)
                                with Image.open(path) as source:
                                    if source.width*source.height > Image.MAX_IMAGE_PIXELS:
                                        raise ValueError("图片像素过大；请通过原图查看")
                                    source.draft("RGB",(size,size))
                                    source.thumbnail((size,size),Image.Resampling.LANCZOS,reducing_gap=3.0)
                                    rendered = ImageOps.exif_transpose(source)
                                    if rendered.mode in ("RGBA","LA") or "transparency" in rendered.info:
                                        rgba = rendered.convert("RGBA")
                                        canvas = Image.new("RGB",rgba.size,(28,30,34))
                                        canvas.paste(rgba,mask=rgba.getchannel("A"))
                                    else:
                                        canvas = rendered.convert("RGB")
                                    cache.parent.mkdir(parents=True,exist_ok=True)
                                    check_directory_chain(cache.parent)
                                    temporary = cache.with_name(cache.name+"."+secrets.token_hex(6)+".tmp")
                                    try:
                                        canvas.save(temporary,format="JPEG",quality=83,optimize=True)
                                        check_directory_chain(cache.parent)
                                        os.replace(temporary,cache)
                                    finally:
                                        temporary.unlink(missing_ok=True)
                        except (OSError,ValueError,Image.DecompressionBombError,Image.DecompressionBombWarning,UnidentifiedImageError) as exc:
                            raise HTTPException(415,f"无法生成预览：{str(exc)[:200]}") from exc
        finally:
            with thumb_lock:
                entry["users"] -= 1
                if entry["users"] == 0:
                    thumb_locks.pop(key,None)
            thumb_queue.release()
    remember_thumbnail(key,cache)
    return FileResponse(cache,media_type="image/jpeg",headers={"Cache-Control":"private, max-age=86400","ETag":'"'+key+'"'})


@app.get("/api/roots")
def roots():
    return store.roots()


class ImportInput(BaseModel):
    path: str = Field(min_length=1,max_length=32767)
    recursive: bool = True


@app.post("/api/import")
def import_library(data: ImportInput):
    if manager.recovering:
        raise HTTPException(503,"正在恢复上次中断的移动，请等待恢复任务完成后导入")
    try:
        path = absolute_directory(data.path)
        if not path.is_dir():
            raise ValueError("所选目录不存在")
    except (OSError,ValueError) as exc:
        raise HTTPException(400,str(exc)) from exc
    return manager.submit("import",{"path":str(path),"recursive":data.recursive})


@app.get("/api/jobs")
def jobs():
    return manager.list()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    current = manager.get(job_id)
    if current and current["kind"] == "recovery" and current["status"] in ("queued","running"):
        raise HTTPException(409,"恢复检查不能手动取消；关闭软件会安全暂停，下次启动自动继续")
    job = manager.cancel(job_id)
    if job is None:
        raise HTTPException(404,"任务不存在")
    return job


class RuleInput(BaseModel):
    id: str = Field(min_length=1,max_length=100)
    name: str = Field(min_length=1,max_length=200)
    kind: Literal["model","tag","artist","format"]
    match: str = Field(min_length=1,max_length=1000)
    destination: str = Field(default="",max_length=32767)
    enabled: bool = True


class RulesInput(BaseModel):
    rules: list[RuleInput] = Field(max_length=1000)


@app.get("/api/rules")
def rules():
    return store.rules()


@app.put("/api/rules")
def update_rules(data: RulesInput):
    if len({rule.id for rule in data.rules}) != len(data.rules):
        raise HTTPException(400,"规则 ID 不能重复")
    items = []
    for rule in data.rules:
        try:
            items.append({**rule.model_dump(),"destination":str(absolute_directory(rule.destination)) if rule.destination.strip() else ""})
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc
    return store.replace_rules(items)


class PreviewInput(BaseModel):
    ids: list[int] | None = Field(None,max_length=10000)
    scope: Literal["all"] | None = None
    kind: Literal["model","tag","artist","format"]
    destination: str = Field(min_length=1,max_length=32767)


@app.post("/api/organize/preview")
def preview_organize(data: PreviewInput):
    if manager.recovering:
        raise HTTPException(503,"正在恢复上次中断的移动，请等待恢复任务完成")
    if data.ids is None and data.scope != "all":
        raise HTTPException(400,"请选择图片或指定整个图库")
    try:
        return organizer.preview(data.kind,data.destination,data.ids)
    except (OSError,ValueError) as exc:
        raise HTTPException(400,str(exc)) from exc


@app.get("/api/organize/plans/{plan_id}")
def get_plan(plan_id: str):
    try:
        return organizer.plan(plan_id)
    except ValueError as exc:
        raise HTTPException(404,str(exc)) from exc


class ExecuteInput(BaseModel):
    plan_id: str = Field(min_length=1,max_length=100)


@app.post("/api/organize/execute")
def execute_organize(data: ExecuteInput):
    if manager.recovering:
        raise HTTPException(503,"正在恢复上次中断的移动，请稍后重试")
    try:
        total = organizer.claim_plan(data.plan_id)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc
    return manager.submit("organize",{"plan_id":data.plan_id},total)


class UndoInput(BaseModel):
    job_id: str = Field(min_length=1,max_length=100)


@app.post("/api/organize/undo")
def undo_organize(data: UndoInput):
    if manager.recovering:
        raise HTTPException(503,"正在恢复上次中断的移动，请稍后重试")
    job = manager.get(data.job_id)
    if job is None or job["kind"] != "organize":
        raise HTTPException(400,"请选择一个整理任务进行撤销")
    if job["status"] in ("queued","running"):
        raise HTTPException(409,"请等待整理任务结束后撤销")
    with store.connect() as db:
        total = db.execute("SELECT COUNT(*) FROM journal WHERE job_id=? AND asset_kind='image' AND state='committed'", (data.job_id,)).fetchone()[0]
        existing = db.execute("SELECT id FROM jobs WHERE kind='undo' AND status IN ('queued','running') AND json_extract(payload,'$.job_id')=?", (data.job_id,)).fetchone()
    if existing:
        raise HTTPException(409,"撤销任务已经在运行")
    if not total:
        raise HTTPException(400,"这个任务没有可撤销的移动记录")
    return manager.submit("undo",{"job_id":data.job_id},total)


@app.get("/api/recovery")
def recovery_records():
    with store.connect() as db:
        total = db.execute("SELECT COUNT(*) FROM journal WHERE state='needs_review'").fetchone()[0]
        items = [dict(row) for row in db.execute("SELECT id,job_id,image_id,source,destination,asset_kind,state,error,updated_at FROM journal WHERE state='needs_review' ORDER BY id DESC LIMIT 200")]
    return {"total":total,"items":items}


@app.get("/api/settings")
def settings():
    return {**store.settings(),"data_dir":str(store.directory),"thumbnail_cache_limit_bytes":THUMB_CACHE_LIMIT}


@app.put("/api/settings")
def update_settings(data: dict):
    permitted = {"theme","density","sort","showTags","thumbnailSize","language","lastImportPath","lastDestination","sidebarCollapsed"}
    if len(json.dumps(data,ensure_ascii=False)) > 32000 or any(key not in permitted for key in data):
        raise HTTPException(400,"仅支持界面偏好设置")
    store.update_settings(data)
    return settings()


dist_path = Path(os.environ.get("LUMA_DIST_DIR") or (Path(__file__).resolve().parent.parent / "dist"))
if dist_path.is_dir():
    app.mount("/",StaticFiles(directory=dist_path,html=True),name="frontend")
else:
    @app.get("/")
    def welcome():
        return {"service":"luma","message":"前端尚未构建；开发模式请打开 http://127.0.0.1:5173"}
