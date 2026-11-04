"""Versioned, offline author-evidenced checkpoint identities.

Only this shipped JSON is read. No image, model weight, catalog database or
network resource is opened. Entries are immutable, exact name/hash/repository
mappings; the caller remains responsible for conflicting metadata evidence.
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path, PureWindowsPath
import re
import threading
from types import MappingProxyType
from urllib.parse import urlparse


CATALOG_VERSION = "2026.10.06.1"
CATALOG_PATH = Path(__file__).with_name("model_catalog.json")
FAMILIES = frozenset(("Anima", "Stable Diffusion", "Krea 2", "FLUX", "NovelAI"))
_SOURCE_DESCRIPTION = "随程序发布的离线模型目录；作者模型卡与发布文件元数据核实，不读取图片或联网。"
_MAX_BYTES = 512 * 1024
_lock = threading.RLock()
_snapshot = None
_SUFFIXES = (".safetensors", ".ckpt", ".gguf", ".pt", ".bin")
_GENERIC_NAME = re.compile(
    r"(?:model(?:\.fp16)?|(?:diffusion_pytorch_model|pytorch_model|"
    r"unet|transformer|clip|text_encoder|vae)(?:[._-].*)?|"
    r"weights|checkpoint|sdxl|sd15|sdxl[_-]vae|"
    r"v\d+(?:[._-]\d+)*)\Z", re.IGNORECASE)
_REPOSITORY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def normalize_catalog_name(value: str) -> str:
    """Normalize one explicitly supplied checkpoint name, without inference.

    Windows/Unix directories and a recognized file suffix are discarded, as
    in user checkpoint registrations. Version tokens, punctuation and words
    remain exact. Repository IDs must use ``normalize_repository_id`` instead.
    """
    name = PureWindowsPath(str(value).replace("/", "\\")).name.casefold().strip()
    for suffix in _SUFFIXES:
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name


def normalize_repository_id(value: str) -> str:
    """Keep the whole exact namespace; URLs, paths and guessed IDs are rejected."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value.casefold() if _REPOSITORY_ID.fullmatch(value) else ""


def _strings(value, field, *, maximum=16, allow_empty=False):
    if not isinstance(value, list) or len(value) > maximum or (not value and not allow_empty):
        raise ValueError(f"模型目录字段 {field} 无效")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 2048 for item in value):
        raise ValueError(f"模型目录字段 {field} 包含无效文本")
    return value


def _source_url(value):
    parsed = urlparse(value)
    return (parsed.scheme == "https" and parsed.hostname in ("huggingface.co", "civitai.com")
            and parsed.username is None and parsed.password is None
            and bool(parsed.path.strip("/"))
            and "/images/" not in parsed.path
            and not parsed.path.casefold().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")))


def _validate_document(document):
    """Fail closed for the whole directory rather than retain ambiguous rows."""
    if not isinstance(document, dict) or type(document.get("schema_version")) is not int or document["schema_version"] != 1:
        raise ValueError("模型目录格式版本无效")
    if document.get("version") != CATALOG_VERSION:
        raise ValueError("模型目录版本与程序不一致")
    records = document.get("entries")
    if not isinstance(records, list) or not records or len(records) > 1000:
        raise ValueError("模型目录条目无效")
    result, identities, ids, families, sources = [], set(), set(), {}, []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("模型目录条目格式无效")
        identifier = record.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,120}", identifier) or identifier in ids:
            raise ValueError("模型目录 ID 无效或重复")
        ids.add(identifier)
        display_name, family, variant = (record.get(key) for key in ("display_name", "family", "variant"))
        if (not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 200
                or not isinstance(family, str) or family not in FAMILIES
                or not isinstance(variant, str) or not variant.strip() or len(variant) > 200):
            raise ValueError("模型目录名称、家族或版本无效")
        revision, verified_date = record.get("revision"), record.get("verified_date")
        if not isinstance(revision, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|civitai:model-version:\d+:file:\d+)", revision):
            raise ValueError("模型目录来源版本无效")
        if not isinstance(verified_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", verified_date):
            raise ValueError("模型目录核实日期无效")
        date.fromisoformat(verified_date)
        names = _strings(record.get("checkpoint_names"), "checkpoint_names", allow_empty=True)
        hashes = _strings(record.get("sha256"), "sha256", allow_empty=True)
        repositories = _strings(record.get("repository_ids"), "repository_ids", maximum=4, allow_empty=True)
        source_urls = _strings(record.get("source_urls"), "source_urls", maximum=10)
        if any(not _source_url(url) for url in source_urls):
            raise ValueError("模型目录缺少有效作者来源")
        if any(not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in hashes):
            raise ValueError("模型目录 SHA256 必须为完整64位小写十六进制")
        evidence_type = record.get("evidence_type")
        if evidence_type == "checkpoint":
            # Each record describes one actual weight payload. Multiple names
            # must be author-published aliases for those exact same bytes.
            if not names or len(hashes) != 1:
                raise ValueError("每份完整 checkpoint 必须对应唯一发布 SHA256")
        elif evidence_type == "official_pipeline_repo":
            if names or hashes or not repositories:
                raise ValueError("拆分管线只能用官方仓库 ID，不得伪造完整 checkpoint")
        else:
            raise ValueError("模型目录证据类型无效")
        if any("/" in name or "\\" in name or not name.casefold().endswith((".safetensors", ".ckpt"))
               or _GENERIC_NAME.fullmatch(normalize_catalog_name(name)) for name in names):
            raise ValueError("模型目录不能登记通用文件名或拆分组件路径")
        if any(not normalize_repository_id(repository) for repository in repositories):
            raise ValueError("模型目录仓库 ID 无效")
        note = record.get("note", "")
        if not isinstance(note, str) or len(note) > 1000:
            raise ValueError("模型目录说明无效")
        common = {
            "family": family, "enabled": True, "note": note,
            "origin": "builtin", "catalog_version": CATALOG_VERSION,
            "catalog_id": identifier, "display_name": display_name,
            "model_variant": variant, "source_urls": tuple(source_urls),
            "checkpoint_sha256": tuple(hashes), "revision": revision,
            "verified_date": verified_date, "evidence_type": evidence_type,
        }
        for kind, values in (("name", names), ("hash", hashes), ("repo", repositories)):
            for value in values:
                match = normalize_catalog_name(value) if kind == "name" else (
                    normalize_repository_id(value) if kind == "repo" else value)
                identity = (kind, match)
                if not match or identity in identities:
                    raise ValueError("模型目录精确身份重复；拒绝模糊或冲突归属")
                identities.add(identity)
                result.append(MappingProxyType({**common, "kind": kind, "match": match}))
        families[family] = families.get(family, 0) + 1
        sources.extend(source_urls)
    status = MappingProxyType({
        "status": "ready", "ready": True, "version": CATALOG_VERSION,
        "count": len(records), "model_count": len(records), "mapping_count": len(result),
        "checkpoint_count": sum(item["evidence_type"] == "checkpoint" for item in records),
        "repository_only_count": sum(item["evidence_type"] == "official_pipeline_repo" for item in records),
        "source": _SOURCE_DESCRIPTION, "source_urls": tuple(dict.fromkeys(sources)),
        "family_counts": MappingProxyType(families), "error": "",
    })
    return tuple(result), status


def _load_snapshot():
    global _snapshot
    # Lock covers first-load I/O as well as publication: simultaneous workers
    # cannot reread the asset, and failed reads are cached without retry loops.
    with _lock:
        if _snapshot is None:
            try:
                with CATALOG_PATH.open("rb") as stream:
                    raw = stream.read(_MAX_BYTES + 1)
                if len(raw) > _MAX_BYTES:
                    raise ValueError("模型目录超过安全大小限制")
                _snapshot = _validate_document(json.loads(raw.decode("utf-8")))
            except (OSError, UnicodeError, ValueError, TypeError) as error:
                _snapshot = ((), MappingProxyType({
                    "status": "error", "ready": False, "version": CATALOG_VERSION,
                    "count": 0, "model_count": 0, "mapping_count": 0, "checkpoint_count": 0,
                    "repository_only_count": 0, "source": _SOURCE_DESCRIPTION,
                    "source_urls": (), "family_counts": MappingProxyType({}),
                    "error": "内置模型目录不可用：" + str(error),
                }))
        return _snapshot


def catalog_entries():
    """Return a cached tuple of read-only mappings; kind is name/hash/repo.

    Full SHA256 values belong only to each record's concrete checkpoint bytes.
    ``checkpoint_sha256`` lets resolvers reject a filename hit when metadata
    supplies an incompatible checkpoint hash. Repository-only records have an
    empty digest tuple and must be compared against the complete repository ID.
    """
    return _load_snapshot()[0]


def catalog_status():
    """Return detached, JSON-serializable ready/error and provenance metadata."""
    status = dict(_load_snapshot()[1])
    status["family_counts"] = dict(status["family_counts"])
    return status


def _reset_cache_for_tests():
    global _snapshot
    with _lock:
        _snapshot = None
