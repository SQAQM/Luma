"""Offline provenance-directory checks; no image or model weights are used."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import io
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from backend import model_catalog as catalog


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = catalog.CATALOG_PATH.read_bytes()
        cls.document = json.loads(cls.raw.decode("utf-8"))

    def setUp(self):
        catalog._reset_cache_for_tests()

    def tearDown(self):
        catalog._reset_cache_for_tests()

    def test_entire_author_catalog_is_unique_versioned_and_concrete(self):
        entries = catalog.catalog_entries()
        status = catalog.catalog_status()
        self.assertTrue(status["ready"], status)
        self.assertEqual(status["version"], self.document["version"])
        self.assertEqual(status["count"], len(self.document["entries"]))
        self.assertEqual(status["model_count"], status["count"])
        self.assertGreaterEqual(status["count"], 15)
        self.assertLessEqual(status["count"], 30)
        self.assertEqual(status["mapping_count"], len(entries))
        self.assertEqual(status["checkpoint_count"] + status["repository_only_count"], status["count"])
        self.assertEqual(len(entries), len({(row["kind"], row["match"]) for row in entries}))
        self.assertEqual(len(self.document["entries"]), len({row["id"] for row in self.document["entries"]}))
        self.assertTrue({"illustrious-xl-v0.1", "illustrious-xl-v0.1-guided", "noobai-xl-v1.1",
                         "animagine-xl-3.1", "animagine-xl-4.0", "animagine-xl-4.0-zero",
                         "pony-diffusion-v6-xl", "dreamshaper-8-pruned", "juggernaut-xl-v9",
                         "anything-v5.0", "counterfeit-v3.0-fixed-fp16"}.issubset(
                             {row["id"] for row in self.document["entries"]}))
        for row in entries:
            self.assertEqual(row["origin"], "builtin")
            self.assertTrue(row["enabled"])
            self.assertIn(row["family"], catalog.FAMILIES)
            self.assertTrue(row["source_urls"])
            self.assertEqual(row["catalog_version"], status["version"])
            self.assertNotEqual(row["model_variant"], "SD 1.5")

    def test_checkpoint_hashes_belong_to_each_specific_record_payload(self):
        records = {row["id"]: row for row in self.document["entries"]}
        for row in catalog.catalog_entries():
            record = records[row["catalog_id"]]
            self.assertEqual(row["checkpoint_sha256"], tuple(record["sha256"]))
            if row["kind"] == "hash":
                self.assertRegex(row["match"], r"\A[0-9a-f]{64}\Z")
                self.assertEqual(row["match"], record["sha256"][0])
            elif row["kind"] == "name":
                self.assertEqual(len(row["checkpoint_sha256"]), 1)
        expected = {
            "illustrious-xl-v0.1": "3e15ba00387db678ab4a099f75771c4f5ac67fda9e7100a01d263eaf30145aa9",
            "noobai-xl-v1.1": "6681e8e4b134c81f16533acedb0d406d7e5e366e1624b4105178c64d00b05d51",
            "animagine-xl-4.0": "1d5b43ff75b6ab598502d4c779d2fbfa3dceca51c60c3b609640a60772333916",
        }
        for identifier, digest in expected.items():
            self.assertEqual(records[identifier]["sha256"], [digest])
        # The author's current Civitai filename says XL, but that precise
        # version explicitly declares SD1.5. A suffix must not change ancestry.
        anything = records["anything-v5.0"]
        self.assertEqual(anything["checkpoint_names"], ["AnythingXL_v50.safetensors"])
        self.assertEqual(anything["variant"], "SD1.5")

    def test_exact_file_and_repository_boundaries_do_not_guess_aliases(self):
        names = {row["match"] for row in catalog.catalog_entries() if row["kind"] == "name"}
        repos = {row["match"] for row in catalog.catalog_entries() if row["kind"] == "repo"}
        value = catalog.normalize_catalog_name(r"E:\synthetic\ANIMAGINE-XL-4.0.safetensors")
        self.assertIn(value, names)
        self.assertIn(catalog.normalize_catalog_name("/synthetic/animagine-xl-4.0.ckpt"), names)
        for candidate in ("my-animagine-xl-4.0", "animagine-xl-4.0-merge", "animagine-xl-4",
                          "illustrious", "NoobAI", "randomXL", "dreamshaper8", "pony"):
            self.assertNotIn(catalog.normalize_catalog_name(candidate), names)
        self.assertIn(catalog.normalize_repository_id("CAGLIOSTROLAB/animagine-xl-4.0"), repos)
        self.assertNotIn(catalog.normalize_repository_id("someone/animagine-xl-4.0"), repos)
        self.assertNotIn(catalog.normalize_catalog_name("cagliostrolab/animagine-xl-4.0"), repos)
        for invalid in ("https://huggingface.co/cagliostrolab/animagine-xl-4.0", "cagliostrolab/animagine-xl-4.0/file",
                        r"E:\cagliostrolab\animagine-xl-4.0", "animagine-xl-4.0"):
            self.assertEqual(catalog.normalize_repository_id(invalid), "")

    def test_split_pipelines_only_offer_exact_official_repository_evidence(self):
        entries = catalog.catalog_entries()
        for family in ("Anima", "FLUX"):
            rows = [row for row in entries if row["family"] == family]
            self.assertTrue(rows)
            self.assertTrue(all(row["kind"] == "repo" for row in rows))
            self.assertTrue(all(not row["checkpoint_sha256"] for row in rows))
            self.assertTrue(all(row["evidence_type"] == "official_pipeline_repo" for row in rows))
        flux = next(row for row in entries if row["match"] == "black-forest-labs/flux.1-krea-dev")
        self.assertEqual(flux["family"], "FLUX")
        self.assertFalse(any(row["family"] == "Krea 2" for row in entries))
        names = {row["match"] for row in entries if row["kind"] == "name"}
        self.assertFalse({"anima-base-v1.0", "qwen_3_06b_base", "qwen_image_vae", "flux1-krea-dev", "ae", "v6"} & names)

    def test_entries_are_immutable_and_status_is_detached_json_serializable(self):
        entries = catalog.catalog_entries()
        self.assertIsInstance(entries, tuple)
        with self.assertRaises(TypeError):
            entries[0]["family"] = "Krea 2"
        self.assertIsInstance(entries[0]["source_urls"], tuple)
        self.assertIsInstance(entries[0]["checkpoint_sha256"], tuple)
        status = catalog.catalog_status()
        json.dumps(status)
        status["count"] = 0
        status["family_counts"].clear()
        self.assertGreater(catalog.catalog_status()["count"], 0)
        self.assertTrue(catalog.catalog_status()["family_counts"])

    def test_concurrent_first_access_and_repeated_calls_read_asset_once(self):
        reads = []
        class RecordingStream(io.BytesIO):
            def read(self, size=-1):
                reads.append(size)
                return super().read(size)
        def open_asset(path, mode, *args, **kwargs):
            self.assertEqual(path, catalog.CATALOG_PATH)
            self.assertEqual(mode, "rb")
            return RecordingStream(self.raw)
        with patch.object(Path, "open", autospec=True, side_effect=open_asset) as opened:
            with ThreadPoolExecutor(max_workers=8) as pool:
                outcomes = list(pool.map(lambda _: (catalog.catalog_entries(), catalog.catalog_status()), range(32)))
            self.assertEqual(opened.call_count, 1)
            self.assertEqual(reads, [catalog._MAX_BYTES + 1])
            self.assertTrue(all(outcome[0] is outcomes[0][0] for outcome in outcomes))
            self.assertTrue(all(outcome[1]["ready"] for outcome in outcomes))

    def test_missing_asset_failure_is_visible_cached_and_uses_no_network(self):
        with patch.object(Path, "open", side_effect=FileNotFoundError("synthetic missing catalog")) as opened, \
                patch("socket.socket", side_effect=AssertionError("network access forbidden")) as network:
            self.assertEqual(catalog.catalog_entries(), ())
            status = catalog.catalog_status()
            self.assertFalse(status["ready"])
            self.assertEqual(status["status"], "error")
            self.assertEqual(status["model_count"], 0)
            self.assertIn("synthetic missing catalog", status["error"])
            self.assertEqual(catalog.catalog_entries(), ())
            self.assertEqual(opened.call_count, 1)
            network.assert_not_called()

    def test_invalid_schema_hash_source_identity_and_date_fail_closed(self):
        changes = {
            "schema": lambda doc: doc.update(schema_version=2),
            "version": lambda doc: doc.update(version="unverified"),
            "hash": lambda doc: doc["entries"][0].update(sha256=["1234567890"]),
            "family": lambda doc: doc["entries"][0].update(family="guessed XL"),
            "source": lambda doc: doc["entries"][0].update(source_urls=["https://example.com/mirror"]),
            "date": lambda doc: doc["entries"][0].update(verified_date="2026-02-30"),
            "duplicate_id": lambda doc: doc["entries"][1].update(id=doc["entries"][0]["id"]),
            "duplicate_name": lambda doc: doc["entries"][1].update(checkpoint_names=doc["entries"][0]["checkpoint_names"]),
            "duplicate_hash": lambda doc: doc["entries"][1].update(sha256=doc["entries"][0]["sha256"]),
            "duplicate_repo": lambda doc: doc["entries"][1].update(repository_ids=doc["entries"][0]["repository_ids"]),
        }
        for name, mutate in changes.items():
            with self.subTest(name=name):
                document = deepcopy(self.document)
                mutate(document)
                catalog._reset_cache_for_tests()
                raw = json.dumps(document).encode("utf-8")
                with patch.object(Path, "open", return_value=io.BytesIO(raw)):
                    self.assertEqual(catalog.catalog_entries(), ())
                    status = catalog.catalog_status()
                    self.assertFalse(status["ready"])
                    self.assertTrue(status["error"])

    def test_generic_or_auxiliary_component_filenames_cannot_enter_directory(self):
        for name in ("model.safetensors", "diffusion_pytorch_model.fp16.safetensors", "unet.fp16.safetensors",
                     "clip-l.safetensors", "text_encoder_2.safetensors", "vae.safetensors", "sdxl_vae.safetensors",
                     "v6.safetensors", "weights.safetensors", "unet/diffusion_pytorch_model.safetensors"):
            with self.subTest(name=name):
                document = deepcopy(self.document)
                document["entries"][0]["checkpoint_names"] = [name]
                with self.assertRaises(ValueError):
                    catalog._validate_document(document)

    def test_repository_only_record_must_not_promote_a_split_weight(self):
        for field, value in (("checkpoint_names", ["anima-base-v1.0.safetensors"]), ("sha256", ["a" * 64])):
            document = deepcopy(self.document)
            record = next(row for row in document["entries"] if row["family"] == "Anima")
            record[field] = value
            with self.assertRaises(ValueError):
                catalog._validate_document(document)

    def test_complete_checkpoint_record_cannot_group_unrelated_weight_digests(self):
        document = deepcopy(self.document)
        document["entries"][0]["sha256"].append(document["entries"][1]["sha256"][0])
        with self.assertRaisesRegex(ValueError, "唯一发布 SHA256"):
            catalog._validate_document(document)

    def test_oversized_asset_read_is_bounded_and_fails_closed(self):
        sizes = []
        class Oversized(io.BytesIO):
            def read(self, size=-1):
                sizes.append(size)
                return super().read(size)
        with patch.object(Path, "open", return_value=Oversized(b" " * (catalog._MAX_BYTES + 4096))):
            self.assertEqual(catalog.catalog_entries(), ())
            self.assertIn("安全大小限制", catalog.catalog_status()["error"])
        self.assertEqual(sizes, [catalog._MAX_BYTES + 1])


if __name__ == "__main__":
    unittest.main()
