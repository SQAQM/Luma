"""Explicit bounded original-header re-read. No scans, pixels or original writes."""
from __future__ import annotations

import json
import time
from contextlib import ExitStack
from pathlib import Path

from .jobs import apply_model_mappings
from .duplicates import VerifiedFile
from .metadata import extract_metadata
from .organizer import Cancelled, check_directory_chain, signature


def validate_ids(ids):
    if not isinstance(ids, list) or not 1 <= len(ids) <= 120 or any(type(i) is not int or not 1 <= i <= 2**63-1 for i in ids):
        raise ValueError("请明确选择 1–120 张图片后重新读取生成信息。")
    if len(set(ids)) != len(ids):
        raise ValueError("所选图片范围不能包含重复项。")
    return ids


def snapshot_row(db, image_id):
    row = db.execute("SELECT i.id,i.path,i.root_id,i.size,i.mtime_ns,i.indexed_at,x.token,x.revision FROM images i JOIN image_content_identity x ON x.image_id=i.id WHERE i.id=?", (image_id,)).fetchone()
    if not row:
        return None
    value = dict(row)
    value["roots"] = [dict(r) for r in db.execute("SELECT r.id,r.path,r.created_at FROM image_roots ir JOIN roots r ON r.id=ir.root_id WHERE ir.image_id=? ORDER BY r.id", (image_id,))]
    return value


def snapshot_selection(store, ids):
    validate_ids(ids)
    with store.connect() as db:
        db.execute("BEGIN")
        rows = [snapshot_row(db, i) for i in ids]
    if any(row is None for row in rows):
        raise ValueError("所选图片已不在索引中，请刷新后重新选择。")
    return rows


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def file_state(path):
    """Include missing companions and every ancestor to detect replacement."""
    check_directory_chain(path.parent)
    ancestors = []
    current = path.parent
    while True:
        info = current.lstat()
        ancestors.append((str(current), info.st_dev, info.st_ino))
        if current == current.parent: break
        current = current.parent
    sources = [path, path.with_suffix(".txt"), Path(str(path)+".txt"), path.with_suffix(".json"), Path(str(path)+".json")]
    files = []
    for item in dict.fromkeys(sources):
        try:
            files.append((str(item), _identity(signature(item))))
        except FileNotFoundError:
            if item == path: raise
            files.append((str(item), None))
    return ancestors, files


def _write(store, row, path, state, metadata, cancelled, guards):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if cancelled(): raise Cancelled()
        for guard in guards: guard.verify()
        if snapshot_row(db, row["id"]) != row or file_state(path) != state:
            return False
        info = signature(path)
        # UPDATE in place: never INSERT, reassign roots, reset creation time or
        # touch human content labels. The identity token rejects delete/reuse ABA.
        tags = list(dict.fromkeys(str(t).strip() for t in metadata.get("tags", []) if str(t).strip()))[:5000]
        values = {key: metadata.get(key, default) for key, default in (
            ("width",0),("height",0),("format",""),("model",""),("family","未知"),
            ("prompt",""),("negative_prompt",""),("artist",""),("confidence",0),("reason",""))}
        values.update(size=info.st_size, mtime_ns=info.st_mtime_ns, indexed_at=time.time(),
                      tags=json.dumps(tags,ensure_ascii=False), parameters=json.dumps(metadata.get("parameters",{}),ensure_ascii=False,allow_nan=False),
                      raw_metadata=json.dumps(metadata.get("raw_metadata",{}),ensure_ascii=False,allow_nan=False),
                      companion_signature=json.dumps(store.sidecar_signatures(path)))
        db.execute("UPDATE images SET "+",".join(f"{key}=?" for key in values)+" WHERE id=?",[*values.values(),row["id"]])
        from .model_references import update_image_references
        update_image_references(db, row["id"], metadata)
        db.execute("DELETE FROM image_tags WHERE image_id=?",(row["id"],))
        db.executemany("INSERT INTO image_tags(image_id,tag) VALUES(?,?)",((row["id"],t) for t in tags))
        db.execute("UPDATE plans SET status='stale' WHERE status='ready' AND id IN (SELECT plan_id FROM plan_items WHERE image_id=?)",(row["id"],))
        if cancelled(): raise Cancelled()
        if file_state(path) != state: raise ValueError("file_changed")
        for guard in guards: guard.verify()
    return True


def run_refresh(store, manager, job_id, payload, cancelled):
    processed = changed = errors = 0
    for row in payload["images"]:
        if cancelled(): raise Cancelled()
        try:
            path = Path(row["path"])
            # SQL identity is checked before any original access, then again at
            # commit. Directory ownership must still match its submission state.
            with store.connect() as db:
                if snapshot_row(db,row["id"]) != row: raise ValueError("index_changed")
            if row["roots"] and not any(path.is_relative_to(Path(root["path"])) for root in row["roots"]):
                raise ValueError("root_changed")
            state = file_state(path)
            with ExitStack() as stack:
                guards=[stack.enter_context(VerifiedFile(Path(name))) for name,identity in state[1] if identity is not None]
                if file_state(path) != state: raise ValueError("file_changed")
                metadata = extract_metadata(path)
                raw = metadata.get("raw_metadata",{})
                if any(raw.get(key) for key in ("sidecar_warning","exif_warning","jpeg_comments_truncated","metadata_truncated")):
                    raise ValueError("metadata_incomplete")
                present_sidecars={Path(name).name for name,identity in state[1][1:] if identity is not None}
                if not set(raw.get("sidecar_files",[])).issubset(present_sidecars):
                    raise ValueError("sidecar_changed")
                if cancelled(): raise Cancelled()
                if file_state(path) != state: raise ValueError("file_changed")
                catalog = payload["model_catalog"]
                metadata = apply_model_mappings(metadata,payload["model_mappings"],catalog["entries"])
                if not _write(store,row,path,state,metadata,cancelled,guards): raise ValueError("index_changed")
            changed += 1
        except Cancelled:
            raise
        except Exception:
            # Fixed summary only: parser exceptions can contain entire prompts.
            errors += 1
        processed += 1
        manager.progress(job_id,processed=processed,errors=errors,message=f"生成信息重新读取：{processed}/{len(payload['images'])} 张，已更新 {changed}，跳过或失败 {errors}（读取失败/文件或索引变化）；旧信息保留")
    manager.progress(job_id,message=f"生成信息重新读取完成：已更新 {changed} 张，跳过或失败 {errors} 张；失败项保留旧信息，原图未修改")
