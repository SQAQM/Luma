"""All model diagnostics use hand-built SQLite indexes, never real images."""
from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from backend import model_diagnostics as diagnostic
from backend.model_catalog import catalog_entries

PRIVATE = "PRIVATE_CANARY_do_not_read_prompt_source_path_tag"
DIGEST = "1234567890abcdef" * 4
OTHER_DIGEST = "fedcba9876543210" * 4


class ModelDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Warm the bundled public JSON before tests prohibit all image I/O.
        catalog_entries()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="luma-model-diagnostic-")
        self.database = Path(self.temporary.name) / "synthetic.sqlite3"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("""CREATE TABLE images(
                id INTEGER PRIMARY KEY,model TEXT,family TEXT,parameters TEXT,
                path TEXT,name TEXT,prompt TEXT,negative_prompt TEXT,tags TEXT,
                artist TEXT,raw_metadata TEXT,reason TEXT)""")
        self.catalog = [{"kind": "name", "match": "synthetic-checkpoint", "family": "Stable Diffusion",
                         "origin": "builtin", "model_variant": "SDXL", "checkpoint_sha256": [DIGEST]},
                        {"kind": "hash", "match": DIGEST, "family": "Stable Diffusion",
                         "origin": "builtin", "model_variant": "SDXL"},
                        {"kind": "repo", "match": "public/synthetic-checkpoint", "family": "Stable Diffusion",
                         "origin": "builtin", "model_variant": "SDXL"}]

    def tearDown(self):
        self.temporary.cleanup()

    def row(self, model="", family="未知", parameters=None, *, raw_parameters=None):
        with closing(sqlite3.connect(self.database)) as db, db:
            value = json.dumps(parameters or {}, ensure_ascii=False) if raw_parameters is None else raw_parameters
            return db.execute("INSERT INTO images(model,family,parameters,path,name,prompt,negative_prompt,tags,artist,raw_metadata,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                              (model, family, value, *([PRIVATE] * 8))).lastrowid

    def run_diagnostic(self, **kwargs):
        return diagnostic.diagnose_models(self.database, catalog=self.catalog, **kwargs)

    def test_empty_and_missing_index_do_not_create_database(self):
        self.assertEqual(self.run_diagnostic()["summary"]["total"], 0)
        absent = self.database.with_name("absent.sqlite3")
        result = diagnostic.diagnose_models(absent)
        self.assertEqual(result["status"], "database_not_found")
        self.assertFalse(absent.exists())

    def test_model_leaf_projection_never_exports_private_fields_or_objects(self):
        self.row(model="C:\\PRIVATE_PATH\\unknown-finetune.safetensors", parameters={
            "Model hash": OTHER_DIGEST, "prompt": PRIVATE, "tags": [PRIVATE],
            "raw_metadata": {"model": PRIVATE}, "model_reason_code": PRIVATE,
            "model_evidence": [{"kind": "base", "value": "SDXL", "source": PRIVATE, "reason": PRIVATE}],
            "checkpoint_ancestry": [{"checkpoint": "unknown-finetune.safetensors", "kind": "architecture", "value": "SDXL", "source": PRIVATE}],
            "checkpoint_hash": {"prompt": PRIVATE}, "base_model": {"value": PRIVATE},
        })
        result = self.run_diagnostic()
        text = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(PRIVATE, text)
        self.assertNotIn("PRIVATE_PATH", text)
        self.assertNotIn(DIGEST, text)
        self.assertNotIn(OTHER_DIGEST, text)
        self.assertEqual(result["summary"]["rows_with_valid_hash"], 1)
        self.assertEqual(result["summary"]["rows_with_ancestry"], 1)
        self.assertEqual(result["summary"]["stale_cache"], 1)
        self.assertEqual(result["models"][0]["model"], "unknown-finetune.safetensors")
        self.assertEqual(result["cached_reason_codes"], [{"reason_code": "not_cached", "count": 1}])

    def test_authorizer_denies_sensitive_columns_other_tables_schema_functions_and_writes(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE secret(value TEXT)")
        connection = diagnostic._connection(self.database, frozenset())
        try:
            for statement in (
                "SELECT prompt FROM images", "SELECT path FROM images", "SELECT name FROM images",
                "SELECT raw_metadata FROM images", "SELECT reason FROM images", "SELECT artist FROM images",
                "SELECT tags FROM images", "SELECT value FROM secret", "SELECT name FROM sqlite_master",
                "SELECT random()", "PRAGMA query_only=OFF", "COMMIT", "BEGIN IMMEDIATE",
                "INSERT INTO images(id) VALUES(100)", "UPDATE images SET model='x'", "DROP TABLE images",
                "ATTACH DATABASE ':memory:' AS another",
            ):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.DatabaseError):
                    connection.execute(statement).fetchall()
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM images").fetchone()[0], 0)
        finally:
            connection.close()

    def test_unknown_evidence_kinds_never_send_private_values_to_python_udfs(self):
        self.row(parameters={
            "model_evidence": [{"kind": "prompt", "value": PRIVATE},
                               {"kind": "private", "value": PRIVATE, "source": PRIVATE}],
            "checkpoint_ancestry": [{"kind": "prompt", "value": PRIVATE, "checkpoint": PRIVATE},
                                    {"kind": "private", "value": PRIVATE, "checkpoint": PRIVATE}],
        })
        seen = []
        real_parts, real_ancestry = diagnostic._model_parts, diagnostic._safe_ancestry
        def parts(value, repositories):
            seen.append(value)
            return real_parts(value, repositories)
        def ancestry(value):
            seen.append(value)
            return real_ancestry(value)
        with patch.object(diagnostic, "_model_parts", side_effect=parts), \
             patch.object(diagnostic, "_safe_ancestry", side_effect=ancestry):
            result = self.run_diagnostic()
        self.assertEqual(result["status"], "completed")
        self.assertNotIn(PRIVATE, seen)
        self.assertEqual(result["summary"]["no_identity"], 1)

    def test_untrusted_mapping_family_is_filtered_and_resolver_output_is_enum_guarded(self):
        self.row(model="unknown")
        invalid = [{"kind": "name", "match": "unknown", "family": PRIVATE}]
        result = self.run_diagnostic(entries=invalid)
        self.assertEqual(result["summary"]["unregistered_identity"], 1)
        self.assertNotIn(PRIVATE, json.dumps(result))
        with patch.object(diagnostic.ModelRegistry, "resolve", return_value={"family": PRIVATE, "reason_code": "catalog"}):
            result = self.run_diagnostic()
        self.assertEqual(result["models"][0]["current_family"], "未知")
        self.assertNotIn(PRIVATE, json.dumps(result))
        result = diagnostic.diagnose_models(self.database, catalog=invalid)
        self.assertEqual(result["summary"]["unregistered_identity"], 1)
        self.assertNotIn(PRIVATE, json.dumps(result))

    def test_authorizer_trace_only_approved_columns_and_actual_batch_sql(self):
        for index in range(237):
            self.row(model="private-unknown", parameters={"Model hash": OTHER_DIGEST})
        reads, statements = [], []
        real_authorizer = diagnostic._authorizer
        real_connection = diagnostic._connection
        def authorizer(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ:
                reads.append((table, column, database, trigger))
            return real_authorizer(action, table, column, database, trigger)
        def connection(*args):
            db = real_connection(*args)
            db.set_trace_callback(statements.append)
            return db
        with patch.object(diagnostic, "_authorizer", authorizer), patch.object(diagnostic, "_connection", connection):
            result = self.run_diagnostic()
        self.assertEqual(result["summary"]["scanned"], 237)
        self.assertTrue(reads)
        self.assertTrue(all(table == "images" and column in {"", "id", "model", "family", "parameters"}
                            and (database == "main" or (database is None and column == ""))
                            and trigger is None for table, column, database, trigger in reads), reads)
        pages = [sql for sql in statements if "ORDER BY id LIMIT 100" in sql]
        self.assertEqual(len(pages), 3)
        self.assertIn("id>100", pages[1])
        self.assertIn("id>200", pages[2])
        self.assertFalse(any("SELECT parameters" in sql or "SELECT *" in sql for sql in statements))

    def test_hash_aliases_validated_and_current_rules_distinguish_stale_cache(self):
        self.row(model="renamed", parameters={"Model hash": DIGEST[:12]})
        self.row(model="synthetic-checkpoint.safetensors", family="Stable Diffusion", parameters={"checkpoint_sha256": DIGEST})
        self.row(model="unknown-private", parameters={"Model hash": "not-a-hash"})
        self.row(parameters={"model_hash": OTHER_DIGEST})
        self.row(parameters={"lora_hash": DIGEST, "prompt": "SDXL FLUX Anima"})
        result = self.run_diagnostic()
        self.assertEqual(result["summary"]["stale_cache"], 1)
        self.assertEqual(result["summary"]["identified"], 1)
        self.assertEqual(result["summary"]["unregistered_identity"], 2)
        self.assertEqual(result["summary"]["no_identity"], 1)
        self.assertEqual(result["summary"]["rows_with_valid_hash"], 3)

    def test_unknown_nicknames_and_prompts_do_not_establish_family(self):
        for value in ("pretty-animeXL-v9", "LandscapeMix-v3", "unlisted-anime-style"):
            self.row(model=value, parameters={"prompt": "SDXL Stable Diffusion"})
        result = self.run_diagnostic()
        self.assertEqual(result["summary"]["unregistered_identity"], 3)
        self.assertTrue(all(group["current_family"] == "未知" for group in result["models"]))

    def test_explicit_root_and_bound_ancestry_respect_checkpoint_completeness(self):
        self.row(model="primary | secondary", parameters={"model_candidates": ["primary", "secondary"],
                 "checkpoint_ancestry": [{"checkpoint": "primary", "kind": "base", "value": "SDXL", "source": PRIVATE}]})
        self.row(model="primary | secondary", parameters={"model_candidates": ["primary", "secondary"],
                 "checkpoint_ancestry": [{"checkpoint": name, "kind": "base", "value": "SDXL", "source": PRIVATE}
                                         for name in ("primary", "secondary")]})
        self.row(model="generic-finetune", parameters={"base_model": "SD1.5"})
        result = self.run_diagnostic()
        self.assertEqual(result["summary"]["evidence_conflict"], 1)
        self.assertEqual(result["summary"]["stale_cache"], 2)
        self.assertEqual(result["summary"]["rows_with_ancestry"], 3)
        multi = next(group for group in result["models"] if group["model"] == "primary | secondary")
        self.assertEqual(multi["current_family"], "混合")
        self.assertEqual(multi["diagnosis"], "mixed")

    def test_multiple_model_column_names_are_independent_without_candidate_array(self):
        self.row(model="Anima.safetensors | undocumented-finetune.safetensors")
        result = self.run_diagnostic()
        self.assertEqual(result["summary"]["evidence_conflict"], 1)
        self.assertEqual(result["models"][0]["current_family"], "未知")

    def test_unknown_repository_is_redacted_and_not_confused_with_published_filename(self):
        self.row(model="private-owner/synthetic-checkpoint")
        self.row(model="public/synthetic-checkpoint")
        result = self.run_diagnostic()
        self.assertNotIn("private-owner", json.dumps(result))
        self.assertEqual(result["summary"]["incomplete_evidence"], 1)
        self.assertEqual(result["summary"]["stale_cache"], 1)

    def test_conflicting_architecture_auxiliary_and_wrong_hash_abstain(self):
        self.row(model="synthetic-checkpoint", parameters={"architecture": "FLUX"})
        self.row(model="synthetic-checkpoint", parameters={"base_model": "SD1.5"})
        self.row(model="synthetic-checkpoint", parameters={"resource_type": "LoRA", "Model hash": DIGEST})
        self.row(model="synthetic-checkpoint", parameters={"Model hash": OTHER_DIGEST})
        result = self.run_diagnostic()
        self.assertEqual(result["summary"]["evidence_conflict"], 3)
        self.assertEqual(result["summary"]["unregistered_identity"], 1)

    def test_independent_invokeai_and_swarm_hashes_are_not_checkpoint_sha(self):
        self.row(model="unknown", parameters={"Model hash": DIGEST,
            "invokeai_model_hashes": [{"hash": DIGEST, "algorithm": "blake3", "source": PRIVATE}]})
        self.row(model="unknown", parameters={"Model hash": DIGEST,
            "invokeai_model_hash": DIGEST, "invokeai_hash_algorithm": "unspecified"})
        self.row(model="unknown", parameters={"Model hash": DIGEST,
            "swarm_model_tensor_hashes": [{"hash": "0x" + DIGEST, "algorithm": "sha256-tensor"}]})
        self.row(model="unknown", parameters={"model_hash": DIGEST,
            "invokeai_model_hashes": [{"hash": DIGEST, "algorithm": "sha256"}]})
        result = self.run_diagnostic()
        self.assertEqual(result["summary"]["unregistered_identity"], 3)
        self.assertEqual(result["summary"]["stale_cache"], 1)
        self.assertEqual(result["summary"]["rows_with_valid_hash"], 1)

    def test_swarm_tensor_exclusion_requires_prefix_and_fixed_algorithm(self):
        self.row(model="unknown", parameters={"model_hash": DIGEST,
            "swarm_model_tensor_hashes": [{"hash": DIGEST, "algorithm": "sha256-tensor"}]})
        self.row(model="unknown", parameters={"model_hash": DIGEST,
            "swarm_model_tensor_hashes": [{"hash": "0x" + DIGEST, "algorithm": "sha256"}]})
        self.row(model="unknown", parameters={"model_hash": DIGEST,
            "swarm_model_tensor_hashes": [{"hash": "0x" + DIGEST, "algorithm": "sha256-tensor"}]})
        result = self.run_diagnostic()
        self.assertEqual(result["summary"]["stale_cache"], 2)
        self.assertEqual(result["summary"]["unregistered_identity"], 1)
        self.assertEqual(result["summary"]["rows_with_valid_hash"], 2)

    def test_overlong_and_malformed_provenance_abstains_without_private_text(self):
        self.row(model=PRIVATE * 30)
        self.row(model="synthetic-checkpoint", parameters={"model_candidates": ["synthetic-checkpoint"] * 65})
        self.row(model="synthetic-checkpoint", parameters={"model_candidates": [{"value": PRIVATE}]})
        self.row(model="synthetic-checkpoint", raw_parameters="broken " + PRIVATE)
        self.row(model="synthetic-checkpoint", parameters={"hashes": json.dumps({"model": DIGEST, "secret": PRIVATE})})
        result = self.run_diagnostic()
        self.assertEqual(result["summary"]["incomplete_evidence"], 5)
        self.assertNotIn(PRIVATE, json.dumps(result))

    def test_bounded_top_groups_and_no_per_image_identifiers(self):
        for index in range(103):
            self.row(model=f"private-model-{index:03}")
        result = self.run_diagnostic()
        self.assertEqual(result["summary"]["scanned"], 103)
        self.assertEqual(len(result["models"]), 100)
        self.assertTrue(result["truncated_groups"])
        self.assertTrue(all(set(group) == {"model", "cached_family", "current_family", "diagnosis", "count"}
                            for group in result["models"]))

    def test_cancelled_before_scan_has_fixed_safe_status(self):
        self.row(model="unknown")
        result = self.run_diagnostic(cancelled=lambda: True)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["summary"]["scanned"], 0)

    def test_cancellation_interrupts_sql_and_does_not_leave_connection_locked(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.executemany("INSERT INTO images(model,family,parameters) VALUES(?,?,?)",
                           ((f"unknown-{i}", "未知", "{}") for i in range(1000)))
        calls = 0
        def cancelled():
            nonlocal calls
            calls += 1
            return calls > 5
        result = self.run_diagnostic(cancelled=cancelled)
        self.assertTrue(result["cancelled"])
        self.assertLess(result["summary"]["scanned"], 1000)
        with closing(sqlite3.connect(self.database, timeout=1)) as db, db:
            db.execute("INSERT INTO images(model) VALUES('after-cancel')")

    def test_user_mapping_snapshot_is_used_without_registry_database_or_image_io(self):
        self.row(model="renamed-private", parameters={"Model hash": OTHER_DIGEST[:12]})
        before = self.database.read_bytes()
        mapping = [{"kind": "hash", "match": OTHER_DIGEST, "family": "Anima", "enabled": True}]
        with patch("backend.model_registry.ModelRegistry.__init__", side_effect=AssertionError("registry constructor")), \
             patch("backend.store.Store.__init__", side_effect=AssertionError("store constructor")), \
             patch("backend.metadata.extract_metadata", side_effect=AssertionError("original metadata")), \
             patch("backend.metadata.reclassify_indexed", side_effect=AssertionError("raw metadata")), \
             patch("builtins.open", side_effect=AssertionError("opened image")), \
             patch("pathlib.Path.open", side_effect=AssertionError("opened image")), \
             patch("os.scandir", side_effect=AssertionError("scanned files")), \
             patch("PIL.Image.open", side_effect=AssertionError("decoded image")):
            result = self.run_diagnostic(entries=mapping)
        self.assertEqual(result["summary"]["stale_cache"], 1)
        self.assertEqual(result["models"][0]["current_family"], "Anima")
        self.assertEqual(self.database.read_bytes(), before)

    def test_read_snapshot_ignores_concurrent_new_rows_and_never_writes_index(self):
        self.row(model="first")
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
        added = False
        calls = 0
        def cancelled():
            nonlocal added, calls
            calls += 1
            if calls >= 2 and not added:
                added = True
                with closing(sqlite3.connect(self.database)) as db, db:
                    db.execute("INSERT INTO images(model,family,parameters) VALUES('later','未知','{}')")
            return False
        result = self.run_diagnostic(cancelled=cancelled)
        self.assertTrue(added)
        self.assertEqual(result["summary"]["total"], 1)
        self.assertEqual(result["summary"]["scanned"], 1)
        with closing(sqlite3.connect(self.database)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM images").fetchone()[0], 2)

    def test_untrusted_family_reason_and_invalid_schema_return_only_safe_fields(self):
        self.row(model="unknown", family=PRIVATE, parameters={"model_reason_code": PRIVATE})
        result = self.run_diagnostic()
        self.assertNotIn(PRIVATE, json.dumps(result))
        broken = self.database.with_name("broken.sqlite3")
        with closing(sqlite3.connect(broken)) as db, db:
            db.execute("CREATE TABLE unrelated(value TEXT)")
        failed = diagnostic.diagnose_models(broken, catalog=self.catalog)
        self.assertEqual(failed["status"], "read_failed")
        self.assertNotIn(str(broken), json.dumps(failed))

    def test_cached_non_ai_is_preserved_but_generation_conflicts_remain_visible(self):
        self.row(family="非 AI")
        self.row(model="synthetic-checkpoint", family="非 AI")
        result = self.run_diagnostic()
        self.assertEqual(result["summary"]["non_ai"], 1)
        self.assertEqual(result["summary"]["evidence_conflict"], 1)


if __name__ == "__main__":
    unittest.main()
