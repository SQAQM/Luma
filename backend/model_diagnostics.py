"""Read-only diagnostics over a bounded, model-only SQLite leaf projection.

This module never creates a Store, opens a picture/sidecar, reparses raw metadata,
or returns an image identifier. JSON objects stay inside SQLite. Only approved,
sanitized model leaves reach Python; free text sources/reasons are not selected.
The result compares the saved family with today's pure classifier and supplied
mapping snapshot. It does not update the index or prove absent image metadata.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from contextlib import closing
from functools import lru_cache
from pathlib import Path, PureWindowsPath
import re
import sqlite3
from typing import Callable, Iterable

from .metadata import classify_model
from .model_catalog import catalog_entries, normalize_repository_id
from .model_registry import FAMILIES, ModelRegistry

BATCH_SIZE = 100
MODEL_LIMIT = 100
EVIDENCE_LIMIT = 64
PARAMETER_BYTE_LIMIT = 1024 * 1024
MODEL_REDACTED = "[模型字段已省略]"
NO_MODEL = "未记录模型"
DIAGNOSES = ("no_identity", "unregistered_identity", "stale_cache",
             "evidence_conflict", "identified", "non_ai", "incomplete_evidence")
_FAMILIES = frozenset((*FAMILIES, "未知", "非 AI"))
_CODES = frozenset(("explicit", "registered", "catalog", "missing", "unregistered",
                    "conflict", "non_ai"))
_HASH = re.compile(r"[0-9a-fA-F]{10,64}\Z")
_MODEL = re.compile(r"[\w .+()@-]*\Z", re.UNICODE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f<>;,]")
_SUFFIXES = (".safetensors", ".ckpt", ".gguf", ".sft", ".pt", ".pth", ".bin")
_ARRAYS = ("model_candidates", "checkpoint_hashes", "model_evidence",
           "checkpoint_ancestry", "invokeai_model_hashes", "swarm_model_tensor_hashes")
_FUNCTIONS = frozenset(("count", "max", "length", "typeof", "json_valid", "json_type",
                       "json_extract", "json_array_length", "diag_model", "diag_loss",
                       "diag_bound_loss", "diag_family", "diag_code", "diag_hash",
                       "diag_ancestry", "diag_kind", "diag_resource", "diag_algorithm"))


def _path(key: str) -> str:
    # Keys below are module constants, never user-provided SQL/path fragments.
    return '$."' + key + '"'


_HASH_PATHS = tuple(_path(key) for key in (
    "model_hash", "checkpoint_hash", "model_sha256", "checkpoint_sha256",
    "Model hash", "Model Hash", "model hash", "Checkpoint hash", "Checkpoint Hash",
    "modelHash", "checkpointHash", "modelSha256", "checkpointSha256")) + (
        '$.hashes.model', '$.hashes.checkpoint', '$.hashes.Model', '$.hashes.Checkpoint')
_ANCESTRY_PATHS = tuple(_path(key) for key in (
    "base_model", "base_family", "model_family", "base_model_type", "base_model_version",
    "architecture", "model_architecture", "model_type", "model_version", "prediction_model", "class_name",
    "Base model", "Base Model", "baseModel", "baseFamily", "modelFamily", "baseModelType",
    "baseModelVersion", "Architecture", "Model architecture", "modelArchitecture",
    "Model type", "modelType", "modelVersion", "predictionModel", "className"))
_RESOURCE_PATHS = tuple(_path(key) for key in ("type", "resource_type", "model_type", "resourceType", "modelType"))
_PARAMS = ("CASE WHEN typeof(parameters)='text' AND length(CAST(parameters AS BLOB))<=1048576 "
           "AND json_valid(parameters) THEN parameters ELSE '{}' END")


def _valid_model_piece(value: str, public_repositories: frozenset[str]) -> tuple[str, bool]:
    value = value.strip()
    if not value:
        return "", False
    if len(value) > 1024 or _CONTROL.search(value) or "://" in value:
        return MODEL_REDACTED, True
    repository = normalize_repository_id(value)
    if repository in public_repositories:
        return repository, False
    is_repository = bool(repository and not value.casefold().endswith(_SUFFIXES))
    if "\\" in value or "/" in value or re.match(r"^[A-Za-z]:", value):
        value = PureWindowsPath(value.replace("/", "\\")).name
    if len(value) > 120 or len(value.split()) > 5 or not _MODEL.fullmatch(value):
        return MODEL_REDACTED, True
    return value, is_repository


def _model_parts(value, public_repositories: frozenset[str]) -> tuple[list[str], bool]:
    if not isinstance(value, str):
        return ([], value is not None)
    if len(value) > 8192 or value.count("|") >= EVIDENCE_LIMIT:
        return [MODEL_REDACTED], True
    parts, loss = [], False
    for part in value.split("|"):
        clean, omitted = _valid_model_piece(part, public_repositories)
        if clean:
            parts.append(clean)
        loss = loss or omitted
    return parts, loss


def _safe_hash(value):
    return value.casefold() if isinstance(value, str) and _HASH.fullmatch(value) else ""


@lru_cache(maxsize=256)
def _safe_ancestry(value):
    """Return a recognized enum, never the free text supplied in its place."""
    if not isinstance(value, str) or len(value) > 120 or _CONTROL.search(value):
        return ""
    output = classify_model(metadata={"base_model": value})
    if output.get("reason_code") == "conflict":
        return "__conflict__"
    if output.get("reason_code") != "explicit" or output.get("family") not in FAMILIES:
        return ""
    return output.get("model_variant") or output["family"]


def _safe_kind(value):
    return value if value in ("checkpoint", "base", "architecture") else ""


def _safe_resource(value):
    if not isinstance(value, str) or len(value) > 80:
        return ""
    flat = re.sub(r"[^a-z0-9]", "", value.casefold())
    for token in ("lora", "locon", "dora", "lycoris", "hypernet", "controlnet", "upscaler",
                  "vae", "embedding", "textualinversion", "clip", "ipadapter", "t2iadapter", "encoder"):
        if token in flat:
            return token
    return ""


def _safe_algorithm(value):
    if not isinstance(value, str):
        return "unspecified"
    return {"sha256": "sha256", "sha-256": "sha256", "blake3": "blake3",
            "sha256-tensor": "sha256-tensor"}.get(value.casefold(), "unspecified")


def _authorizer(action, table, column, database, trigger):
    """Deny schema, other tables, arbitrary functions, pragmas and writes."""
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        return (sqlite3.SQLITE_OK if (database == "main" or (database is None and column == "")) and table == "images"
                and column in ("id", "model", "family", "parameters", "")
                and trigger is None else sqlite3.SQLITE_DENY)
    if action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_OK if column in _FUNCTIONS else sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_DENY


def _connection(database: Path, public_repositories: frozenset[str]):
    # Setup precedes the authorizer; its only transaction is a read snapshot.
    db = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA temp_store=MEMORY")
        db.execute("BEGIN")
        model = lambda value: " | ".join(_model_parts(value, public_repositories)[0])
        loss = lambda value: int(_model_parts(value, public_repositories)[1])
        bound_loss = lambda value: int(bool(isinstance(value, str) and ("\\" in value or "/" in value)
                                            and normalize_repository_id(value) not in public_repositories) or loss(value))
        functions = {"diag_model": model, "diag_loss": loss, "diag_bound_loss": bound_loss,
                     "diag_family": lambda value: value if value in _FAMILIES else "未知",
                     "diag_code": lambda value: value if value in _CODES else "not_cached",
                     "diag_hash": _safe_hash, "diag_ancestry": _safe_ancestry,
                     "diag_kind": _safe_kind, "diag_resource": _safe_resource,
                     "diag_algorithm": _safe_algorithm}
        for name, function in functions.items():
            db.create_function(name, 1, function, deterministic=True)
        db.set_authorizer(_authorizer)
        return db
    except BaseException:
        db.close()
        raise


def _text_leaf(path: str, sanitizer: str, *, root=_PARAMS):
    return (f"CASE WHEN json_type({root},'{path}')='text' "
            f"THEN {sanitizer}(json_extract({root},'{path}')) ELSE '' END")


def _array_length(name):
    path = _path(name)
    return (f"CASE WHEN json_type({_PARAMS},'{path}')='array' "
            f"THEN json_array_length({_PARAMS},'{path}') ELSE 0 END")


_BASE_SELECT = ",".join((
    "id", "diag_model(model)", "diag_family(family)",
    _text_leaf(_path("model_reason_code"), "diag_code"), "diag_loss(model)",
    (f"CASE WHEN typeof(parameters)!='text' OR length(CAST(parameters AS BLOB))>1048576 OR NOT json_valid(parameters) "
     f"OR json_type({_PARAMS},'$.model_provenance_incomplete')='true' THEN 1 ELSE 0 END"),
    *(_text_leaf(path, "diag_hash") for path in _HASH_PATHS),
    *(_text_leaf(path, "diag_ancestry") for path in _ANCESTRY_PATHS),
    *(_text_leaf(path, "diag_resource") for path in _RESOURCE_PATHS),
    *(_array_length(name) for name in _ARRAYS),
    _text_leaf('$.invokeai_model_hash', 'diag_hash'),
    _text_leaf('$.invokeai_hash_algorithm', 'diag_algorithm'),
    f"CASE WHEN json_type({_PARAMS},'$.hashes')='text' THEN 1 ELSE 0 END",
))
_SLOTS = " UNION ALL ".join(f"SELECT {i} AS slot" for i in range(EVIDENCE_LIMIT))


def _array_sql(name):
    # A static array root + bounded slot number + static leaf suffix; no object
    # or array is returned. Every value is sanitized before leaving SQLite.
    path = '$."' + name + '"['
    def leaf(suffix, sanitizer):
        target = f"'{path}' || slots.slot || ']{suffix}'"
        return (f"CASE WHEN json_type({_PARAMS},{target})='text' "
                f"THEN {sanitizer}(json_extract({_PARAMS},{target})) ELSE '' END")
    if name == "model_candidates":
        target = f"'{path}' || slots.slot || ']'"
        invalid = (f"CASE WHEN json_type({_PARAMS},{target})='text' THEN {leaf('', 'diag_bound_loss')} "
                   "ELSE 1 END")
        columns = (leaf("", "diag_model"), invalid)
    elif name == "checkpoint_hashes":
        columns = (leaf("", "diag_hash"),)
    elif name == "model_evidence":
        kind = leaf(".kind", "diag_kind")
        value = (f"CASE WHEN {kind}='checkpoint' THEN {leaf('.value','diag_model')} "
                 f"WHEN {kind} IN ('base','architecture') THEN {leaf('.value','diag_ancestry')} ELSE '' END")
        target = f"'{path}' || slots.slot || '].value'"
        invalid = (f"CASE WHEN {kind} IN ('checkpoint','base','architecture') THEN "
                   f"CASE WHEN json_type({_PARAMS},{target})='text' THEN {leaf('.value', 'diag_bound_loss')} "
                   "ELSE 1 END ELSE 0 END")
        columns = (kind, value, invalid)
    elif name == "checkpoint_ancestry":
        kind = leaf(".kind", "diag_kind")
        guard = lambda expression: f"CASE WHEN {kind} IN ('base','architecture') THEN {expression} ELSE '' END"
        target = f"'{path}' || slots.slot || '].checkpoint'"
        invalid = (f"CASE WHEN {kind} IN ('base','architecture') THEN "
                   f"CASE WHEN json_type({_PARAMS},{target})='text' THEN {leaf('.checkpoint', 'diag_bound_loss')} "
                   "ELSE 1 END ELSE 0 END")
        columns = (guard(leaf(".checkpoint", "diag_model")), kind,
                   guard(leaf(".value", "diag_ancestry")), invalid)
    elif name == "invokeai_model_hashes":
        columns = (leaf(".hash", "diag_hash"), leaf(".algorithm", "diag_algorithm"))
    else:  # Swarm tensor hashes are independent from whole-file SHA256.
        digest = f"'{path}' || slots.slot || '].hash'"
        columns = (f"CASE WHEN json_type({_PARAMS},{digest})='text' "
                   f"AND substr(json_extract({_PARAMS},{digest}),1,2)='0x' "
                   f"AND {leaf('.algorithm','diag_algorithm')}='sha256-tensor' THEN "
                   f"diag_hash(substr(json_extract({_PARAMS},{digest}),3)) ELSE '' END",)
    return (f"SELECT images.id,{','.join(columns)} FROM images CROSS JOIN ({_SLOTS}) AS slots "
            f"WHERE images.id>? AND images.id<=? AND slots.slot<{_array_length(name)} "
            f"ORDER BY images.id,slots.slot")


_ARRAY_SQL = {name: _array_sql(name) for name in _ARRAYS}
# substr is used only to remove SwarmUI's fixed 0x tensor-hash prefix.
_FUNCTIONS = _FUNCTIONS | {"substr"}


def _unpack(row):
    image_id, model, family, code, loss, incomplete = row[:6]
    offset = 6
    hashes = set(value for value in row[offset:offset + len(_HASH_PATHS)] if value)
    offset += len(_HASH_PATHS)
    ancestry = [value for value in row[offset:offset + len(_ANCESTRY_PATHS)] if value]
    offset += len(_ANCESTRY_PATHS)
    resources = [value for value in row[offset:offset + len(_RESOURCE_PATHS)] if value]
    offset += len(_RESOURCE_PATHS)
    lengths = dict(zip(_ARRAYS, row[offset:offset + len(_ARRAYS)]))
    offset += len(_ARRAYS)
    invoke_hash, invoke_algorithm, encoded_hashes = row[offset:offset + 3]
    candidates = [piece for piece in model.split(" | ") if piece and piece != MODEL_REDACTED]
    return {"id": image_id, "model": model, "family": family, "code": code or "not_cached",
            "hashes": hashes, "ancestry": ancestry, "resources": resources,
            "lengths": lengths, "candidates": candidates, "evidence": [], "bound": [],
            "independent_hashes": {invoke_hash} if invoke_hash and invoke_algorithm != "sha256" else set(),
            "incomplete": bool(loss or incomplete or encoded_hashes or any(value > EVIDENCE_LIMIT for value in lengths.values()))}


def _apply_leaf(record, name, leaf):
    if name == "model_candidates":
        model, loss = leaf
        if model and model != MODEL_REDACTED:
            record["candidates"].extend(piece for piece in model.split(" | ") if piece != MODEL_REDACTED)
        record["incomplete"] = record["incomplete"] or bool(loss) or model == MODEL_REDACTED
    elif name == "checkpoint_hashes":
        if leaf[0]:
            record["hashes"].add(leaf[0])
    elif name == "model_evidence":
        kind, value, loss = leaf
        if kind:
            record["incomplete"] = record["incomplete"] or bool(loss)
        if kind and value:
            record["evidence"].append({"kind": kind, "value": value, "source": "索引模型叶字段"})
            if kind == "checkpoint":
                record["candidates"].extend(piece for piece in value.split(" | ") if piece != MODEL_REDACTED)
                record["incomplete"] = record["incomplete"] or bool(loss) or value == MODEL_REDACTED
            else:
                record["ancestry"].append(value)
    elif name == "checkpoint_ancestry":
        checkpoint, kind, value, loss = leaf
        if kind in ("base", "architecture"):
            record["incomplete"] = record["incomplete"] or bool(loss)
        if checkpoint and kind in ("base", "architecture") and value:
            record["bound"].append({"checkpoint": checkpoint, "kind": kind, "value": value,
                                    "source": "索引模型叶字段"})
            record["incomplete"] = record["incomplete"] or bool(loss) or checkpoint == MODEL_REDACTED
    elif name == "invokeai_model_hashes":
        digest, algorithm = leaf
        if digest and algorithm != "sha256":
            record["independent_hashes"].add(digest)
    elif leaf[0]:
        record["independent_hashes"].add(leaf[0])


def _classify(record, entries, catalog):
    hashes = record["hashes"] - record["independent_hashes"]
    candidates = list(dict.fromkeys(record["candidates"]))
    ancestry = [value for value in record["ancestry"] if value != "__conflict__"]
    # Only scalar-enum ancestry is synthesized; no private source is retained.
    params = {"model_candidates": candidates, "checkpoint_hashes": sorted(hashes),
              "checkpoint_ancestry": record["bound"], "model_evidence": record["evidence"]}
    if ancestry:
        params["model_evidence"] = params["model_evidence"] + [
            {"kind": "base", "value": value, "source": "索引模型叶字段"} for value in ancestry]
    if record["resources"]:
        params["resource_type"] = record["resources"][0]
    if record["family"] == "非 AI":
        params["ai"] = False
    if record["incomplete"]:
        params["model_provenance_incomplete"] = True
    primary = candidates[0] if candidates else ""
    baseline = classify_model(primary, metadata=params)
    if "__conflict__" in record["ancestry"] or any(item["value"] == "__conflict__" for item in record["bound"]):
        baseline = {"family": "未知", "reason_code": "conflict"}
    current = ModelRegistry.resolve({"model": primary, "parameters": params}, baseline, entries, catalog=catalog)
    raw_family = current.get("family", "未知")
    family = raw_family if raw_family in (*FAMILIES, "非 AI", "未知") else "未知"
    if record["incomplete"]:
        diagnosis = "incomplete_evidence"
    elif current.get("reason_code") == "conflict":
        diagnosis = "evidence_conflict"
    elif family == "非 AI":
        diagnosis = "non_ai"
    elif family in FAMILIES:
        diagnosis = "identified" if family == record["family"] else "stale_cache"
    elif candidates or hashes:
        diagnosis = "unregistered_identity"
    else:
        diagnosis = "no_identity"
    return family, diagnosis, bool(hashes), bool(record["ancestry"] or record["bound"])


def _empty_result(status="completed"):
    summary = {key: 0 for key in ("total", "scanned", *DIAGNOSES,
                                 "rows_with_valid_hash", "rows_with_ancestry")}
    return {"status": status, "summary": summary, "models": [], "truncated_groups": False,
            "limit": MODEL_LIMIT, "cancelled": False, "cached_reason_codes": [],
            "privacy": {"read_only": True, "image_access": False, "prompt_access": False,
                        "path_or_filename_export": False, "whole_parameters_selected": False,
                        "raw_metadata_selected": False}}


def diagnose_models(database: Path, *, entries: Iterable[dict] = (), catalog=None,
                    cancelled: Callable[[], bool] | None = None) -> dict:
    """Return aggregate diagnostics; never write or create the supplied DB.

    ``entries`` is an already captured user mapping snapshot, not a table read.
    Cancellation is checked between batches and by SQLite's VM progress hook.
    Up to 100 model/cache-family groups are returned; their per-row comparison
    may be ``mixed``. Counts on cancellation describe the rows actually scanned.
    """
    result = _empty_result()
    database = Path(database)
    if not database.is_file():
        result["status"] = "database_not_found"
        return result
    is_cancelled = cancelled or (lambda: False)
    catalog = tuple(entry for entry in (catalog_entries() if catalog is None else catalog)
                    if isinstance(entry, Mapping) and entry.get("family") in FAMILIES
                    and entry.get("kind") in ("name", "hash", "repo"))
    entries = tuple(dict(entry) for entry in entries if isinstance(entry, Mapping)
                    and entry.get("family") in FAMILIES and entry.get("kind") in ("name", "hash", "repo"))
    public_repositories = frozenset(entry["match"] for entry in catalog
                                    if entry.get("kind") == "repo" and entry.get("origin") == "builtin")
    selected, codes = {}, Counter()
    try:
        with closing(_connection(database, public_repositories)) as db:
            db.set_progress_handler(lambda: int(bool(is_cancelled())), 1000)
            highwater, total = db.execute("SELECT MAX(id),COUNT(*) FROM images").fetchone()
            result["summary"]["total"] = total
            if is_cancelled():
                result.update(status="cancelled", cancelled=True)
                return result
            groups = db.execute(
                "SELECT diag_model(model),diag_family(family),COUNT(*) FROM images "
                "GROUP BY diag_model(model),diag_family(family) ORDER BY COUNT(*) DESC,1,2 LIMIT 101"
            ).fetchall()
            result["truncated_groups"] = len(groups) > MODEL_LIMIT
            for model, family, count in groups[:MODEL_LIMIT]:
                selected[(model, family)] = {"model": model or NO_MODEL, "cached_family": family,
                                           "current": set(), "diagnoses": set(), "count": 0}
            cursor = 0
            while highwater is not None and cursor < highwater:
                if is_cancelled():
                    result.update(status="cancelled", cancelled=True)
                    break
                rows = db.execute(f"SELECT {_BASE_SELECT} FROM images WHERE id>? AND id<=? ORDER BY id LIMIT 100",
                                  (cursor, highwater)).fetchall()
                if not rows:
                    break
                records = {row[0]: _unpack(row) for row in rows}
                end = rows[-1][0]
                for name in _ARRAYS:
                    if not any(record["lengths"][name] for record in records.values()):
                        continue
                    with closing(db.execute(_ARRAY_SQL[name], (cursor, end))) as leaves:
                        while chunk := leaves.fetchmany(BATCH_SIZE):
                            for leaf in chunk:
                                _apply_leaf(records[leaf[0]], name, leaf[1:])
                            if is_cancelled():
                                result.update(status="cancelled", cancelled=True)
                                break
                    if result["cancelled"]:
                        break
                if result["cancelled"]:
                    break
                for record in records.values():
                    family, diagnosis, has_hash, has_ancestry = _classify(record, entries, catalog)
                    summary = result["summary"]
                    summary["scanned"] += 1
                    summary[diagnosis] += 1
                    summary["rows_with_valid_hash"] += int(has_hash)
                    summary["rows_with_ancestry"] += int(has_ancestry)
                    codes[record["code"]] += 1
                    group = selected.get((record["model"], record["family"]))
                    if group is not None:
                        group["count"] += 1
                        group["current"].add(family)
                        group["diagnoses"].add(diagnosis)
                cursor = end
    except (sqlite3.Error, OSError):
        result.update(status="cancelled" if is_cancelled() else "read_failed", cancelled=bool(is_cancelled()))
        # Database errors never echo a query, path, private value or exception.
    result["models"] = [{"model": group["model"], "cached_family": group["cached_family"],
                         "current_family": next(iter(group["current"])) if len(group["current"]) == 1 else "混合",
                         "diagnosis": next(iter(group["diagnoses"])) if len(group["diagnoses"]) == 1 else "mixed",
                         "count": group["count"]} for group in selected.values() if group["count"]]
    result["cached_reason_codes"] = [{"reason_code": code, "count": count} for code, count in sorted(codes.items())]
    return result
