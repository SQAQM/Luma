"""Dropped metadata must be explicit, so a reread cannot erase older information."""
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from backend import metadata
from desktop.service import LibraryService


class MetadataLimitTests(unittest.TestCase):
    def fixture(self, root, *, compressed=False):
        path = root / "generated.png"
        info = PngInfo()
        if compressed:
            info.add(b"zTXt", b"parameters\x00\x00not-a-zlib-stream")
        else:
            info.add_text("parameters", "x" * (metadata.MAX_TEXT_BYTES + 1))
        Image.new("RGB", (8, 8), "teal").save(path, pnginfo=info)
        return path

    def test_oversized_png_text_reports_omitted_metadata(self):
        with tempfile.TemporaryDirectory(prefix="luma-png-limit-") as temporary:
            path = self.fixture(Path(temporary))
            self.assertTrue(metadata.extract_metadata(path)["raw_metadata"].get("metadata_truncated"))

    def test_invalid_compressed_text_reports_omitted_metadata(self):
        with tempfile.TemporaryDirectory(prefix="luma-png-compression-") as temporary:
            path = self.fixture(Path(temporary), compressed=True)
            self.assertTrue(metadata.extract_metadata(path)["raw_metadata"].get("metadata_truncated"))

    def test_raw_collection_omission_is_not_silent(self):
        result = metadata._bound_raw({"jpeg_comments": ["synthetic"] * 65})
        self.assertTrue(result.get("metadata_truncated"))

    def test_limited_reread_preserves_existing_prompt_and_source_bytes(self):
        with tempfile.TemporaryDirectory(prefix="luma-reread-limit-") as temporary:
            root = Path(temporary)
            source = root / "synthetic"
            source.mkdir()
            path = self.fixture(source)
            before = path.read_bytes()
            service = LibraryService(root / "isolated-data")
            try:
                image_id = service.store.upsert(path, service.store.add_root(source), path.stat(),
                    {"format": "PNG", "prompt": "previous synthetic prompt", "model": "previous synthetic model"})
                job = service.reread_metadata([image_id])
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    job = service.job(job["id"])
                    if job["status"] not in ("queued", "running"):
                        break
                    time.sleep(.01)
                self.assertEqual(job["status"], "completed_with_errors", job["message"])
                self.assertEqual(job["errors"], 1)
                after = service.detail(image_id)
                self.assertEqual(after["prompt"], "previous synthetic prompt")
                self.assertEqual(after["model"], "previous synthetic model")
                self.assertEqual(path.read_bytes(), before)
            finally:
                service.close()
