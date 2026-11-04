"""Source/frozen exact-duplicate lifecycle check using temporary synthetic files."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import time
from unittest.mock import patch

from PIL import Image


def run(report_path):
    from desktop.service import LibraryService
    report = {
        "verification": "failed",
        "fixtures": "four generated geometry files only; no default catalog or user images",
        "user_images_accessed": False,
        "user_database_accessed": False,
        "production_capacity_validated": False,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="luma-duplicates-smoke-") as temporary:
            root = Path(temporary)
            source = root / "synthetic"
            source.mkdir()
            Image.new("RGB", (48, 32), "teal").save(source / "a.png")
            for name in ("b.png", "c.png"):
                shutil.copy2(source / "a.png", source / name)
            Image.new("RGB", (48, 32), "coral").save(source / "different.png")
            sidecar = source / "a.txt"
            sidecar.write_bytes(b"synthetic sidecar sentinel; keep this file")
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
            service = LibraryService(root / "data")

            def wait(job):
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    result = service.job(job["id"])
                    if result["status"] not in ("queued", "running"):
                        if result["status"] != "completed" or result["errors"]:
                            raise RuntimeError("Synthetic duplicate task failed: " + result["message"])
                        return result
                    time.sleep(.01)
                raise RuntimeError("Synthetic duplicate task timed out")

            def checked_scan():
                job = wait(service.scan_duplicates())
                scan_id = job["payload"]["scan_id"]
                scan = service.duplicate_scan(scan_id)
                assert (scan["groups"], scan["duplicates"]) == (1, 2), scan
                group = service.duplicate_groups(scan_id)["items"][0]
                members = service.duplicate_members(group["id"])["items"]
                assert len(members) == 3 and sum(x["is_keep"] for x in members) == 1
                service.select_duplicate_all(scan_id, True)
                keep = next(x for x in members if x["is_keep"])
                return scan_id, Path(keep["path"])

            try:
                wait(service.import_directory(str(source)))
                # Decoder and metadata parser are forbidden throughout duplicate work.
                with patch("PIL.Image.open", side_effect=AssertionError("duplicate flow must not decode")), \
                     patch("backend.metadata.extract_metadata", side_effect=AssertionError("duplicate flow must not parse")):
                    scan_id, kept = checked_scan()
                    plan = service.preview_duplicate_cleanup(scan_id)
                    assert plan["total"] == 2
                    cleanup = wait(service.execute_duplicate_cleanup(plan["id"]))
                    assert kept.exists() and hashlib.sha256(kept.read_bytes()).hexdigest() == before[kept.name]
                    assert len(list(source.glob("*.png"))) == 2
                    history = service.duplicate_cleanup_history()
                    assert history[0]["restorable"] == 2
                    restored = wait(service.undo_duplicate_cleanup(cleanup["id"]))
                    assert len(list(source.glob("*.png"))) == 4
                    assert all(hashlib.sha256((source / name).read_bytes()).hexdigest() == digest for name, digest in before.items())
                    scan_id, kept = checked_scan()
                    plan = service.preview_duplicate_cleanup(scan_id)
                    cleanup = wait(service.execute_duplicate_cleanup(plan["id"]))
                    purge = service.preview_duplicate_purge(cleanup["id"])
                    assert purge["total"] == 2
                    cleared = wait(service.execute_duplicate_purge(purge["id"]))
                    assert not any(source.rglob("*.lumaq"))
                    assert kept.exists() and hashlib.sha256(kept.read_bytes()).hexdigest() == before[kept.name]
                    assert hashlib.sha256(sidecar.read_bytes()).hexdigest() == before[sidecar.name]
                    assert service.query()["total"] == 2
                    report.update(
                        verification="passed", exact_group_count=1, duplicate_count=2,
                        scan_job_status="completed", cleanup_job_status=cleanup["status"],
                        undo_job_status=restored["status"], purge_job_status=cleared["status"],
                        keep_file_unchanged=True, restored_files_byte_identical=True,
                        sidecar_unchanged=True, quarantine_empty_after_explicit_purge=True,
                        decoder_or_metadata_parser_used=False,
                    )
            finally:
                service.close()
    except Exception as exc:
        report["error"] = str(exc)
    output = Path(report_path).absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["verification"] == "passed" else 1
