"""Frozen/source verification of selected metadata rereads using generated files."""
from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
import struct
import tempfile
import time
from unittest.mock import patch

from PIL import Image


def run(report_path):
    from desktop.service import LibraryService
    report = {
        "verification": "failed", "fixture_count": 3,
        "fixtures": "three generated JPEG geometry files in an independent temporary catalog",
        "default_catalog_accessed": False, "user_images_accessed": False,
        "network_used": False, "production_capacity_validated": False,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="luma-metadata-smoke-") as temporary:
            root = Path(temporary)
            source = root / "synthetic"
            source.mkdir()
            graph = {
                "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "synthetic-unknown.safetensors"}},
                "2": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "synthetic geometry prompt"}},
                "3": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "second fixture line"}},
                "4": {"class_type": "StringConcatenate", "inputs": {"string_a": ["2", 0], "string_b": ["3", 0], "delimiter": "\n"}},
                "5": {"class_type": "CLIPTextEncode", "inputs": {"text": ["4", 0]}},
                "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "synthetic negative"}},
                "7": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["5", 0], "negative": ["6", 0], "seed": 42, "steps": 4}},
                "8": {"class_type": "CLIPTextEncode", "inputs": {"text": "disconnected fixture note"}},
            }
            comment = ("prompt=" + json.dumps(graph, ensure_ascii=False)).encode("utf-8")
            paths = []
            for index, color in enumerate(("teal", "coral", "navy")):
                buffer = BytesIO()
                Image.new("RGB", (48, 32), color).save(buffer, "JPEG")
                data = buffer.getvalue()
                path = source / f"generated-{index}.jpg"
                path.write_bytes(data[:2] + b"\xff\xfe" + struct.pack(">H", len(comment) + 2) + comment + data[2:])
                paths.append(path)
            hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
            service = LibraryService(root / "isolated-data")
            try:
                root_id = service.store.add_root(source)
                ids = [service.store.upsert(path, root_id, path.stat(), {"format": "JPEG", "width": 48, "height": 32}) for path in paths]
                service.store.patch_image(ids[0], {"favorite": True, "rating": 4})
                tag_id = service.content_tags()[0]["id"]
                state = service.content_label_state(ids[:1])
                service.apply_content_label_overrides(ids[:1], [{"tag_id": tag_id, "action": "include"}], state["snapshot_token"])
                untouched = service.detail(ids[2])
                with patch("PIL.Image.Image.load", side_effect=AssertionError("metadata reread must not decode pixels")):
                    job = service.reread_metadata(ids[:2])
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        result = service.job(job["id"])
                        if result["status"] not in ("queued", "running"):
                            break
                        time.sleep(.01)
                    else:
                        raise RuntimeError("Synthetic metadata task timed out")
                assert result["status"] == "completed" and result["errors"] == 0
                first, second = (service.detail(image_id) for image_id in ids[:2])
                expected = "synthetic geometry prompt\nsecond fixture line"
                assert first["prompt"] == second["prompt"] == expected
                assert first["negative_prompt"] == "synthetic negative"
                assert "disconnected fixture note" not in first["prompt"]
                assert first["favorite"] and first["rating"] == 4 and first["root_id"] == root_id
                assert any(tag["id"] == tag_id and tag["source"] == "manual" for tag in first["content_tags"])
                assert service.detail(ids[2]) == untouched
                assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths} == hashes
                report.update(verification="passed", task_status=result["status"], selected_image_count=2,
                    jpeg_comment_prompt_restored=True, string_chain_resolved=True,
                    unselected_image_unchanged=True, annotations_preserved=True,
                    files_unchanged=True, no_disconnected_text=True, pixels_decoded_during_reread=False)
            finally:
                service.close()
    except Exception as error:
        # This diagnostic uses synthetic values only. Never reuse it with private input.
        report["error"] = str(error)
    output = Path(report_path).absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["verification"] == "passed" else 1
