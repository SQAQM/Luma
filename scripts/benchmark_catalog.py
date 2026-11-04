"""Reproducible *synthetic catalog* benchmark, never an image decode benchmark.

Default: python scripts/benchmark_catalog.py --count 100000
Million rows: python scripts/benchmark_catalog.py --count 1000000

Creates its own disposable data directory, never opens the user's real catalog or
image directories. The SQL insert path measures catalog schema/trigger costs; it
does not measure directory traversal, metadata parsing, thumbnails, network disks,
concurrent indexing, or production workloads. Read timings include Store API JSON
conversion, total-count queries, new SQLite connections and warm OS caches.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import platform
import sqlite3
import statistics
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from backend.store import Store  # noqa: E402

STORE_SOURCE_HASH = hashlib.sha256((PROJECT_ROOT / "backend" / "store.py").read_bytes()).hexdigest()


def process_memory():
    """Peak counters of this benchmark process, never another app or OS cache."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "peak_working_set", "working_set", "peak_paged_pool", "paged_pool",
                    "peak_nonpaged_pool", "nonpaged_pool", "commit", "peak_commit", "private_commit")]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        api = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
        api.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        api.restype = wintypes.BOOL
        value = Counters()
        value.cb = ctypes.sizeof(value)
        if not api(kernel.GetCurrentProcess(), ctypes.byref(value), value.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return dict(method="PSAPI GetProcessMemoryInfo current-process lifetime peaks",
                    peak_working_set_bytes=value.peak_working_set,
                    peak_private_commit_bytes=value.peak_commit,
                    ending_working_set_bytes=value.working_set,
                    ending_private_commit_bytes=value.private_commit,
                    scope="benchmark process only; excludes OS file cache and other processes")
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return dict(method="getrusage RUSAGE_SELF", peak_working_set_bytes=int(peak if sys.platform == "darwin" else peak * 1024),
                scope="benchmark process only; excludes OS file cache and other processes")
INSERT_IMAGES = """INSERT INTO images(
 id,path,root_id,name,width,height,size,mtime_ns,format,model,family,tags,prompt,
 negative_prompt,parameters,raw_metadata,artist,confidence,reason,favorite,rating,
 created_at,indexed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
FAMILIES = ["Anima", "Stable Diffusion", "Krea 2", "FLUX", "NovelAI", "未知"] + [f"Synthetic family {index:02}" for index in range(6, 20)]
FORMATS = ["PNG", "JPEG", "WEBP", "TIFF", "GIF", "BMP", "AVIF", "ICO"]


def populate(store: Store, count: int, batch_size: int) -> dict:
    started = time.perf_counter()
    inserted = 0
    epoch = 1_700_000_000.0
    with store.connect() as db:
        db.execute("INSERT INTO roots(id,path,name,created_at) VALUES(1,?,?,?)", ("synthetic://benchmark", "Synthetic benchmark only", epoch))
    for begin in range(1, count + 1, batch_size):
        image_rows, tag_rows = [], []
        for image_id in range(begin, min(begin + batch_size, count + 1)):
            group = image_id % 20
            tags = ["portrait", f"group{group:02}", f"palette{image_id % 8:02}"]
            fmt = FORMATS[image_id % len(FORMATS)]
            name = f"synthetic_{image_id:09}.{fmt.lower()}"
            image_rows.append((
                image_id, f"synthetic://benchmark/{name}", 1, name,
                1024 + image_id % 4 * 256, 1024, 1_000_000 + image_id % 5000,
                int((epoch + image_id) * 1_000_000_000), fmt,
                f"synthetic_model_{group:02}", FAMILIES[group],
                json.dumps(tags), f"synthetic portrait group{group:02}", "",
                "{}", "{}", f"synthetic_artist_{image_id % 50:02}",
                0.0, "Synthetic fixture, no provenance inference", int(image_id % 100 == 0),
                image_id % 6, epoch + image_id, epoch,
            ))
            tag_rows.extend((image_id, tag) for tag in tags)
        with store.connect() as db:
            # Keep production triggers active, including FTS and aggregate tables.
            db.executemany(INSERT_IMAGES, image_rows)
            db.executemany("INSERT INTO image_tags(image_id,tag) VALUES(?,?)", tag_rows)
        inserted += len(image_rows)
        if inserted % max(batch_size, 100000) == 0 or inserted == count:
            elapsed = time.perf_counter() - started
            print(f"Synthetic catalog: {inserted:,}/{count:,} rows, {elapsed:.1f}s", flush=True)
    with store.connect() as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    elapsed = time.perf_counter() - started
    return {"seconds": round(elapsed, 6), "rows_per_second": round(count / elapsed, 2),
            "batch_size": batch_size, "image_rows": count, "tag_rows": count * 3,
            "insert_method": "batched direct SQL; production FTS/aggregate triggers remain active"}


def timed_query(function, repetitions: int) -> tuple[dict, dict]:
    samples = []
    result = None
    for _ in range(repetitions):
        start = time.perf_counter()
        result = function()
        samples.append((time.perf_counter() - start) * 1000)
    ordered = sorted(samples)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return {"samples_ms": [round(value, 3) for value in samples],
            "median_ms": round(statistics.median(samples), 3), "p95_ms": round(p95, 3),
            "returned_items": len(result["items"]), "matched_total": result["total"]}, result


def benchmark(store: Store, count: int, repetitions: int, page_size: int) -> dict:
    query_trace: dict[str, str] = {}
    phase = ["newest_page"]
    original_connect = store.connect

    def capture(sql: str) -> None:
        normalized = sql.lstrip().upper()
        if normalized.startswith("SELECT ") and " FROM IMAGES I" in normalized and " ORDER BY " in normalized:
            query_trace[phase[0]] = sql

    @contextmanager
    def traced_connect():
        with original_connect() as connection:
            connection.set_trace_callback(capture)
            yield connection

    # Trace the actual generated Store SQL so EXPLAIN reports production queries,
    # including predicates/count ordering, rather than idealized substitutes.
    store.connect = traced_connect
    newest, first_page = timed_query(lambda: store.list_images(limit=page_size), repetitions)
    if first_page["total"] != count or len(first_page["items"]) != min(count, page_size):
        raise RuntimeError("Catalog row count or first page is incorrect")
    if first_page["items"][0]["id"] != count:
        raise RuntimeError("Newest cursor order is incorrect")

    deep_anchor = max(page_size + 1, count // 10)
    deep_anchor = min(count - 1, deep_anchor)
    cursor = first_page.get("next_cursor")
    if cursor and deep_anchor > 0:
        # Synthetic monotonically increasing timestamps let us construct a valid
        # deep keyset cursor without walking thousands of pages or timing OFFSET.
        decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        with store.connect() as db:
            anchor = db.execute("SELECT id,created_at FROM images WHERE id=?", (deep_anchor,)).fetchone()
        decoded.update({"id": anchor["id"], "key": anchor["created_at"]})
        deep_cursor = base64.urlsafe_b64encode(json.dumps(decoded).encode()).decode().rstrip("=")
        phase[0] = "deep_cursor_page"
        deep, deep_page = timed_query(lambda: store.list_images(limit=page_size, cursor=deep_cursor), repetitions)
        if deep_page["items"] and deep_page["items"][0]["id"] != deep_anchor - 1:
            raise RuntimeError("Deep cursor returned an incorrect boundary")
        deep["cursor_anchor_id"] = deep_anchor
        deep["approximate_depth_percent"] = round((1 - deep_anchor / count) * 100, 2)
    else:
        deep = {"skipped": "Not enough rows for a second page"}
    phase[0] = "exact_tag_group17"
    exact_tag, tag_page = timed_query(lambda: store.list_images(limit=page_size, tag="group17"), repetitions)
    if any("group17" not in row["tags"] for row in tag_page["items"]):
        raise RuntimeError("Exact tag query returned another tag")
    phase[0] = "fts_group17"
    full_text, fts_page = timed_query(lambda: store.list_images(limit=page_size, q="group17"), repetitions)
    if full_text["matched_total"] != exact_tag["matched_total"]:
        raise RuntimeError("FTS and exact fixture tag result totals disagree")

    stats_started = time.perf_counter()
    stats = store.stats()
    stats_ms = (time.perf_counter() - stats_started) * 1000
    if stats["total"] != count:
        raise RuntimeError("Aggregate statistics row count is incorrect")
    with store.connect() as db:
        plans = {key: [row[3] for row in db.execute("EXPLAIN QUERY PLAN " + sql)] for key, sql in query_trace.items()}
        pragma = {key: db.execute("PRAGMA " + key).fetchone()[0] for key in ("journal_mode", "synchronous", "page_count", "page_size")}
        counts = {"images": db.execute("SELECT COUNT(*) FROM images").fetchone()[0],
                  "tags": db.execute("SELECT COUNT(*) FROM image_tags").fetchone()[0],
                  "fts": db.execute("SELECT COUNT(*) FROM images_fts").fetchone()[0]}
    if counts != {"images": count, "tags": count * 3, "fts": count}:
        raise RuntimeError("Synthetic image, TAG, or FTS counts are inconsistent")
    store.connect = original_connect
    return {"newest_page": newest, "deep_cursor_page": deep, "exact_tag_group17": exact_tag,
            "fts_group17": full_text,
            "stats_once": {"elapsed_ms": round(stats_ms, 3), "total": stats["total"],
                           "model_groups": len(stats["models"]), "tag_groups": len(stats["tags"])},
            "query_plans": plans, "generated_queries": query_trace, "database_pragmas": pragma, "verified_row_counts": counts,
            "page_size": page_size, "read_repetitions": repetitions}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=100000)
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--page-size", type=int, default=120)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "docs" / "benchmark-result.json")
    args = parser.parse_args()
    if not 1 <= args.count <= 5_000_000 or not 1 <= args.batch_size <= 50000 or not 1 <= args.repetitions <= 20 or not 1 <= args.page_size <= 500:
        parser.error("count 1..5000000; batch-size 1..50000; repetitions 1..20; page-size 1..500")
    result = {
        "benchmark": "Synthetic SQLite catalog only",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "claim_scope": "Tests catalog storage, cursor/TAG/FTS queries and stats. Does not validate one million real images, image decoding, thumbnails, filesystem indexing, concurrency or production readiness.",
        "environment": {"os": platform.platform(), "python": platform.python_version(),
                        "sqlite": sqlite3.sqlite_version, "machine": platform.machine(), "processor": platform.processor()},
        "store_source_sha256": STORE_SOURCE_HASH,
        "synthetic_distribution": {"families": 20, "models": 20, "formats": len(FORMATS), "artists": 50, "tags_per_image": 3,
                                   "description": "Uniform model/family groups and group TAGs, 8 palette TAGs; all prompts contain a portrait token. Paths use synthetic:// and refer to no real image files."},
        "cache_state": "Warm OS file cache after population; no claim of cold disk performance",
    }
    with tempfile.TemporaryDirectory(prefix="luma-catalog-benchmark-") as directory:
        store = Store(directory)
        result["population"] = populate(store, args.count, args.batch_size)
        result["queries"] = benchmark(store, args.count, args.repetitions, args.page_size)
        with store.connect() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        result["database_bytes"] = store.database.stat().st_size
        result["database_mib"] = round(result["database_bytes"] / (1024 * 1024), 3)
        final_hash = hashlib.sha256((PROJECT_ROOT / "backend" / "store.py").read_bytes()).hexdigest()
        result["store_source_changed_during_run"] = final_hash != STORE_SOURCE_HASH
        result["synthetic_database_retained"] = False
        result["process_memory"] = process_memory()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "rows": args.count,
                      "populate_seconds": result["population"]["seconds"],
                      "database_mib": result["database_mib"],
                      "median_ms": {key: value.get("median_ms", value.get("elapsed_ms")) for key, value in result["queries"].items() if isinstance(value, dict) and ("median_ms" in value or "elapsed_ms" in value)}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
