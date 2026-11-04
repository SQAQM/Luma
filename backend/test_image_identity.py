"""Opaque pixel-cache identity survives annotations but rejects index ID reuse."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.store import Store


class ImageIdentityTests(unittest.TestCase):
    def test_deleted_id_reused_with_same_path_signature_and_timestamp_changes_identity(self):
        with tempfile.TemporaryDirectory(prefix="luma-image-identity-") as temporary:
            root = Path(temporary)
            path = root / "synthetic.png"
            path.write_bytes(b"synthetic index identity fixture")
            store = Store(root / "isolated-data")
            root_id = store.add_root(root)
            with patch("backend.store.time.time", return_value=12345.0):
                image_id = store.upsert(path, root_id, path.stat(), {"format": "PNG"})
                first = store.get_image(image_id)
                with store.connect() as db:
                    db.execute("DELETE FROM images WHERE id=?", (image_id,))
                replacement = store.upsert(path, root_id, path.stat(), {"format": "PNG"})
            second = store.get_image(replacement)
            self.assertEqual(replacement, image_id)
            for key in ("path", "size", "mtime_ns", "indexed_at"):
                self.assertEqual(first[key], second[key])
            self.assertNotEqual(first.get("image_identity"), second.get("image_identity"))

    def test_annotation_changes_keep_pixel_identity_and_index_timestamp(self):
        with tempfile.TemporaryDirectory(prefix="luma-image-annotations-") as temporary:
            root = Path(temporary)
            path = root / "synthetic.png"
            path.write_bytes(b"synthetic annotation fixture")
            store = Store(root / "isolated-data")
            image_id = store.upsert(path, store.add_root(root), path.stat(), {"format": "PNG"})
            first = store.get_image(image_id)
            self.assertIsInstance(first.get("image_identity"), str)
            store.patch_image(image_id, {"favorite": True, "rating": 4})
            state = store.content_label_state([image_id])
            store.apply_content_label_overrides([image_id], [{"tag_id": state["tags"][0]["id"], "action": "include"}], state["snapshot_token"])
            second = store.get_image(image_id)
            self.assertEqual(second["image_identity"], first["image_identity"])
            self.assertEqual(second["indexed_at"], first["indexed_at"])
            self.assertTrue(second["favorite"])
            self.assertEqual(second["rating"], 4)
