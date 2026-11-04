"""Verify source/frozen manual content editing using an isolated synthetic catalog."""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from PIL import Image


def run(report_path):
    from desktop.service import LibraryService
    report = {
        "verification": "failed",
        "fixtures": "three generated geometry files and an independent temporary index only",
        "user_images_accessed": False,
        "user_database_accessed": False,
        "production_capacity_validated": False,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="luma-content-labels-smoke-") as temporary:
            root = Path(temporary)
            source = root / "synthetic"
            source.mkdir()
            for i, color in enumerate(("teal", "coral", "navy")):
                Image.new("RGB", (48, 32), color).save(source / f"geometry-{i}.png")
            paths = sorted(source.iterdir())
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
            service = LibraryService(root / "isolated-data")
            try:
                root_id = service.store.add_root(source)
                ids = [service.store.upsert(p, root_id, p.stat(), {"format": "PNG"}) for p in paths]
                tag_a, tag_b = [tag["id"] for tag in service.content_tags()]
                # Explicit synthetic machine evidence; no inference/model download.
                service.store.set_content_matches(ids[0], [{"tag_id": tag_a, "score": .8}])
                service.store.set_content_matches(ids[1], [{"tag_id": tag_a, "score": .8}])

                def apply(selected, tag_id, action, token=None):
                    state = service.content_label_state(selected)
                    return service.apply_content_label_overrides(selected,
                        [{"tag_id": tag_id, "action": action}], token or state["snapshot_token"])

                def count(tag_id):
                    return service.query({"content_tag": tag_id})["total"]

                with ExitStack() as stack:
                    stack.enter_context(patch("PIL.Image.open", side_effect=AssertionError("manual edit decoded pixels")))
                    stack.enter_context(patch("backend.metadata.extract_metadata", side_effect=AssertionError("manual edit parsed metadata")))
                    stack.enter_context(patch("pathlib.Path.stat", side_effect=AssertionError("manual edit examined files")))
                    stack.enter_context(patch("builtins.open", side_effect=AssertionError("manual edit opened a file")))
                    stack.enter_context(patch.object(service.store, "get_image", side_effect=AssertionError("manual edit read full metadata")))
                    baseline = service.content_label_state(ids[:2])
                    assert baseline["image_count"] == 2 and count(tag_a) == 2
                    included = apply(ids[:2], tag_b, "include")
                    assert included["changed_image_count"] == 2 and count(tag_b) == 2
                    excluded = apply(ids[:1], tag_a, "exclude")
                    assert excluded["changed_image_count"] == 1 and count(tag_a) == 1
                    state = service.content_label_state(ids[:2])
                    by_id = {tag["id"]: tag for tag in state["tags"]}
                    assert by_id[tag_a]["excluded_count"] == 1 and by_id[tag_a]["machine_count"] == 2
                    assert by_id[tag_b]["manual_count"] == 2
                    assert all(tag["effective_count"] == 0 for tag in service.content_label_state(ids[2:])["tags"])
                    apply(ids[:1], tag_a, "auto")
                    assert count(tag_a) == 2
                    stale_rejected = False
                    try:
                        apply(ids[:2], tag_b, "exclude", baseline["snapshot_token"])
                    except ValueError:
                        stale_rejected = True
                    assert stale_rejected and count(tag_b) == 2
                    apply(ids[:2], tag_b, "auto")
                    assert count(tag_b) == 0 and count(tag_a) == 2
                assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths} == before
                report.update(verification="passed", fixture_count=3,
                    selected_image_count=2, include_exclude_auto_verified=True,
                    unselected_image_unchanged=True, mixed_evidence_preserved=True,
                    stale_snapshot_rejected=True, files_unchanged=True,
                    decoder_or_metadata_parser_used=False, image_file_io_during_edit=False,
                    inference_or_model_download_started=False)
            finally:
                service.close()
    except Exception as exc:
        report["error"] = str(exc)
    output = Path(report_path).absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["verification"] == "passed" else 1
