"""Explicit manual content edits: synthetic files and temporary catalogs only."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from .duplicates import DuplicateManager
from .organizer import Organizer
from .store import Store


class Worker:
    def cancelled(self, unused): return False
    def progress(self, unused, **values): pass


class ContentLabelTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="luma-manual-content-")
        self.directory=Path(self.temp.name)
        self.source=self.directory/"synthetic";self.source.mkdir()
        self.store=Store(self.directory/"isolated-data")
        self.root=self.store.add_root(self.source)
        self.tags=self.store.content_tag_definitions()
        self.a,self.b=(tag["id"] for tag in self.tags)
        self.ids=[self.add(f"generated-{i}.png") for i in range(3)]

    def tearDown(self): self.temp.cleanup()

    def add(self,name,data=b"generated synthetic bytes"):
        path=self.source/name;path.write_bytes(data)
        return self.store.upsert(path,self.root,path.stat(),{"format":"PNG","prompt":"private fixture prompt",
            "negative_prompt":"fixture negative","tags":["prompt-only"],"parameters":{"secret":"fixture"}})

    def apply(self,ids,tag=None,action="include",snapshot=None):
        state=self.store.content_label_state(ids)
        return self.store.apply_content_label_overrides(ids,[{"tag_id":tag or self.a,"action":action}],snapshot or state["snapshot_token"])

    def match(self,image_id,tag=None,score=.8,source="vision"):
        self.store.set_content_matches(image_id,[{"tag_id":tag or self.a,"score":score}],source=source)

    def tag(self,ids,tag=None):
        return next(item for item in self.store.content_label_state(ids)["tags"] if item["id"]==(tag or self.a))

    def test_include_requires_no_model_and_has_no_fake_similarity(self):
        self.apply(self.ids[:1])
        label=self.store.get_image(self.ids[0])["content_tags"][0]
        self.assertEqual(label["source"],"manual");self.assertIsNone(label["score"])
        self.assertEqual(label["model_id"],"");self.assertFalse(label["stale"])
        self.assertEqual(self.tag(self.ids[:1])["manual_count"],1)
        self.assertEqual(self.tag(self.ids[:1])["machine_count"],0)
        self.assertEqual(self.store.list_images(content_tag=self.a)["total"],1)

    def test_exclude_suppresses_existing_and_future_machine_auto_reveals(self):
        image_id=self.ids[0];self.match(image_id)
        self.apply([image_id],action="exclude")
        self.assertEqual(self.store.get_image(image_id)["content_tags"],[])
        self.assertEqual(self.tag([image_id])["machine_count"],1)
        self.assertEqual(self.tag([image_id])["effective_count"],0)
        self.match(image_id,score=.9)
        self.assertEqual(self.store.list_images(content_tag=self.a)["total"],0)
        self.apply([image_id],action="auto")
        self.assertEqual(self.store.get_image(image_id)["content_tags"][0]["source"],"vision")
        self.assertEqual(self.tag([image_id])["effective_count"],1)

    def test_mixed_selection_preserves_untouched_images_and_categories(self):
        self.apply([self.ids[0]],tag=self.b)
        self.apply([self.ids[1]],action="exclude")
        self.apply([self.ids[2]],action="include")
        before=self.store.get_image(self.ids[2])["content_tags"]
        summary=self.apply(self.ids[:2])
        self.assertEqual(summary["changed_image_count"],2)
        self.assertEqual(summary["changed_override_count"],2)
        self.assertEqual(self.tag(self.ids,self.b)["manual_count"],1)
        self.assertEqual(self.store.get_image(self.ids[2])["content_tags"],before)

    def test_legacy_manual_override_auto_and_definition_revision(self):
        image_id=self.ids[0];self.match(image_id,source="manual")
        self.match(image_id)
        self.assertEqual(self.tag([image_id])["manual_count"],1)
        definitions=self.store.content_tag_definitions();definitions[0]["description"]="a newly revised landscape description"
        self.store.replace_content_tags(definitions)
        self.assertEqual(self.tag([image_id])["effective_count"],1)
        self.assertIsNone(self.store.get_image(image_id)["content_tags"][0]["score"])
        self.match(image_id)
        self.apply([image_id],action="auto")
        self.assertEqual(self.tag([image_id])["manual_count"],0)
        self.assertEqual(self.tag([image_id])["machine_count"],1)

    def test_rescan_preserves_overrides_and_invalidates_machine_counts(self):
        image_id=self.ids[0];self.match(image_id)
        self.apply([image_id],tag=self.b)
        with self.store.connect() as db:
            db.execute("UPDATE images SET mtime_ns=mtime_ns+1 WHERE id=?",(image_id,))
        self.assertEqual(self.tag([image_id])["machine_count"],0)
        self.assertEqual(self.store.list_images(content_tag=self.a)["total"],0)
        self.assertEqual(self.store.list_images(content_tag=self.b)["total"],1)
        self.assertTrue(next(t for t in self.store.get_image(image_id)["content_tags"] if t["id"]==self.a)["stale"])

    def test_snapshot_rejects_index_change_missing_rows_and_wrong_selection(self):
        state=self.store.content_label_state(self.ids[:1])
        with self.store.connect() as db: db.execute("UPDATE images SET indexed_at=indexed_at+1 WHERE id=?",(self.ids[0],))
        with self.assertRaisesRegex(ValueError,"已改变"): self.apply(self.ids[:1],snapshot=state["snapshot_token"])
        with self.assertRaisesRegex(ValueError,"已改变"): self.apply(self.ids[1:2],snapshot=state["snapshot_token"])
        with self.assertRaisesRegex(ValueError,"不在索引"): self.apply([999999])
        self.assertEqual(self.tag(self.ids)["manual_count"],0)

    def test_snapshot_rejects_path_id_and_override_aba(self):
        image_id=self.ids[0];state=self.store.content_label_state([image_id])
        self.apply([image_id]);self.apply([image_id],action="auto")
        with self.assertRaisesRegex(ValueError,"已改变"): self.apply([image_id],snapshot=state["snapshot_token"])
        state=self.store.content_label_state([image_id])
        with self.store.connect() as db:
            db.execute("UPDATE images SET path=path||'.changed' WHERE id=?",(image_id,))
            db.execute("UPDATE images SET path=SUBSTR(path,1,LENGTH(path)-8) WHERE id=?",(image_id,))
        with self.assertRaisesRegex(ValueError,"已改变"): self.apply([image_id],snapshot=state["snapshot_token"])
        state=self.store.content_label_state([image_id])
        with self.store.connect() as db:
            columns=[row["name"] for row in db.execute("PRAGMA table_info(images)")]
            row=db.execute("SELECT * FROM images WHERE id=?",(image_id,)).fetchone()
            db.execute("DELETE FROM images WHERE id=?",(image_id,))
            db.execute("INSERT INTO images("+",".join(columns)+") VALUES("+",".join("?" for _ in columns)+")",tuple(row))
        with self.assertRaisesRegex(ValueError,"已改变"): self.apply([image_id],snapshot=state["snapshot_token"])

    def test_invalid_inputs_conflicts_and_disabled_positive_rollback(self):
        state=self.store.content_label_state(self.ids)
        invalid_ids=([],[True],["1"],[0],[2**63],self.ids*41)
        for ids in invalid_ids:
            with self.subTest(ids=repr(ids)[:30]),self.assertRaises(ValueError): self.store.content_label_state(ids)
        invalid_changes=([],[{"tag_id":True,"action":"include"}],[{"tag_id":self.a,"action":"bogus"}],
            [{"tag_id":self.a,"action":"include"},{"tag_id":self.a,"action":"exclude"}],
            [{"tag_id":self.a,"action":"include","extra":1}],[{"tag_id":self.a,"action":"include"}]*129)
        for changes in invalid_changes:
            with self.subTest(changes=repr(changes)[:50]),self.assertRaises(ValueError):
                self.store.apply_content_label_overrides(self.ids,changes,state["snapshot_token"])
        for token in (None,"x"*64,"中"*64,"a"*63):
            with self.assertRaises(ValueError): self.apply(self.ids,snapshot=token or "bad")
        definitions=self.store.content_tag_definitions();definitions[1]["enabled"]=False;self.store.replace_content_tags(definitions)
        state=self.store.content_label_state(self.ids)
        with self.assertRaisesRegex(ValueError,"停用"):
            self.store.apply_content_label_overrides(self.ids,[{"tag_id":self.a,"action":"include"},{"tag_id":self.b,"action":"include"}],state["snapshot_token"])
        self.assertEqual(self.tag(self.ids)["manual_count"],0)
        self.apply(self.ids,tag=self.b,action="exclude");self.apply(self.ids,tag=self.b,action="auto")

    def test_duplicate_input_merges_and_noop_preserves_snapshot(self):
        state=self.store.content_label_state([self.ids[0],self.ids[0]])
        changes=[{"tag_id":self.a,"action":"include"}]*2
        summary=self.store.apply_content_label_overrides([self.ids[0]]*2,changes,state["snapshot_token"])
        self.assertEqual(summary["image_count"],1);self.assertEqual(summary["changed_override_count"],1)
        again=self.apply([self.ids[0]])
        self.assertEqual(again["changed_override_count"],0)
        self.assertEqual(again["snapshot_token"],summary["snapshot_token"])

    def test_sql_failure_rolls_back_all_images_counts_and_identity_revisions(self):
        state=self.store.content_label_state(self.ids)
        with self.store.connect() as db:
            db.execute(f"CREATE TRIGGER synthetic_reject_override BEFORE INSERT ON image_content_overrides WHEN new.image_id={self.ids[1]} BEGIN SELECT RAISE(ABORT,'synthetic rollback'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError,"synthetic rollback"):
            self.store.apply_content_label_overrides(self.ids,[{"tag_id":self.a,"action":"include"}],state["snapshot_token"])
        after=self.store.content_label_state(self.ids)
        self.assertEqual(after["snapshot_token"],state["snapshot_token"])
        self.assertEqual(self.tag(self.ids)["manual_count"],0)
        self.assertEqual(self.store.list_images(content_tag=self.a)["total"],0)

    def test_disabled_existing_manual_is_visible_ineffective_and_removed_tag_cascades(self):
        self.apply(self.ids[:1]);definitions=self.store.content_tag_definitions()
        definitions[0]["enabled"]=False;self.store.replace_content_tags(definitions)
        tag=self.tag(self.ids[:1])
        self.assertEqual((tag["manual_count"],tag["effective_count"]),(1,0))
        label=self.store.get_image(self.ids[0])["content_tags"][0]
        self.assertFalse(label["enabled"]);self.assertFalse(label["stale"])
        state=self.store.content_label_state(self.ids[:1])
        self.store.replace_content_tags(definitions[1:])
        with self.assertRaisesRegex(ValueError,"已改变"):
            self.store.apply_content_label_overrides(self.ids[:1],[{"tag_id":self.a,"action":"auto"}],state["snapshot_token"])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM image_content_overrides WHERE tag_id=?",(self.a,)).fetchone()[0],0)

    def test_file_bytes_prompt_rating_and_roots_unchanged(self):
        image_id=self.ids[0]
        self.store.patch_image(image_id,{"rating":4,"favorite":True})
        before=self.store.get_image(image_id)
        paths=list(self.source.iterdir());hashes={path:hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        with patch("builtins.open",side_effect=AssertionError("pixel access")),patch("pathlib.Path.stat",side_effect=AssertionError("file stat")),patch.object(self.store,"get_image",side_effect=AssertionError("full metadata returned")):
            state=self.store.content_label_state([image_id])
            self.store.apply_content_label_overrides([image_id],[{"tag_id":self.a,"action":"include"}],state["snapshot_token"])
        after=self.store.get_image(image_id)
        for key in ("path","root_id","prompt","negative_prompt","tags","parameters","raw_metadata","rating","favorite"):
            self.assertEqual(after[key],before[key],key)
        self.assertEqual({path:hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},hashes)

    def test_state_and_apply_do_not_project_sensitive_image_columns(self):
        original=self.store.connect
        @contextmanager
        def restricted():
            with original() as db:
                def authorize(op,table,column,unused,trigger):
                    if op==sqlite3.SQLITE_READ and table=="images" and column not in {"","id","size","mtime_ns"}:
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                db.set_authorizer(authorize)
                yield db
        with patch.object(self.store,"connect",restricted):
            state=self.store.content_label_state(self.ids)
            # UPDATE is restricted to overrides; trigger programs may reference
            # image UPDATE triggers without reading their image columns.
            self.store.apply_content_label_overrides(self.ids,[{"tag_id":self.a,"action":"include"}],state["snapshot_token"])
        encoded=json.dumps(state)
        self.assertNotIn("prompt",encoded);self.assertNotIn("generated-",encoded)

    def test_query_search_cache_cursor_and_stats_use_effective_labels(self):
        for image_id in self.ids: self.match(image_id)
        page=self.store.list_images(content_tag=self.a,limit=1)
        self.assertEqual(page["total"],3)
        self.apply(self.ids[:1],action="exclude")
        with self.assertRaisesRegex(ValueError,"过期"): self.store.list_images(content_tag=self.a,limit=1,cursor=page["next_cursor"])
        self.assertEqual(self.store.list_images(content_tag=self.a)["total"],2)
        self.assertEqual(self.store.list_images(q="风景")["total"],2)
        counts={row["id"]:row["count"] for row in self.store.stats()["content_tags"]}
        self.assertEqual(counts[self.a],2)
        self.assertEqual(self.store.list_images(tag="prompt-only")["total"],3)

    def test_plans_become_stale_and_manual_sort_is_explicit(self):
        image_id=self.ids[0];self.match(image_id,tag=self.b,score=.99);self.apply([image_id])
        organizer=Organizer(self.store)
        plan=organizer.preview("content_tag",self.directory/"out",[image_id])
        self.assertEqual(plan["items"][0]["category"],"风景")
        self.assertIn("人工确认",plan["items"][0]["reason"])
        self.apply([image_id],action="exclude")
        with self.assertRaises(ValueError): organizer.claim_plan(plan["id"])
        plan=organizer.preview("content_tag",self.directory/"out",[image_id]);self.match(image_id,tag=self.b)
        with self.assertRaises(ValueError): organizer.claim_plan(plan["id"])

    def test_definition_change_and_rescan_invalidate_ready_plans(self):
        image_id=self.ids[0];self.apply([image_id]);organizer=Organizer(self.store)
        plan=organizer.preview("content_tag",self.directory/"out",[image_id])
        definitions=self.store.content_tag_definitions();definitions[0]["name"]="校正风景";self.store.replace_content_tags(definitions)
        with self.assertRaises(ValueError): organizer.claim_plan(plan["id"])
        plan=organizer.preview("content_tag",self.directory/"out",[image_id])
        with self.store.connect() as db: db.execute("UPDATE images SET indexed_at=indexed_at+1 WHERE id=?",(image_id,))
        with self.assertRaises(ValueError): organizer.claim_plan(plan["id"])

    def test_removal_cascades_overrides_and_counts_without_changing_files(self):
        self.apply(self.ids);paths=list(self.source.iterdir())
        plan=self.store.preview_remove_root(self.root)
        self.store.remove_root(self.root,plan_id=plan["id"])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM image_content_overrides").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM image_effective_content_tags").fetchone()[0],0)
        self.assertTrue(all(path.exists() for path in paths))
        self.assertEqual(self.store.stats()["total"],0)

    def test_persisted_overrides_and_legacy_migration_counts(self):
        self.match(self.ids[0],source="manual");self.apply(self.ids[1:2],action="exclude")
        with self.store.connect() as db:
            db.execute("DELETE FROM schema_migrations WHERE name='content-overrides-v1'")
            db.execute("DELETE FROM image_effective_content_tags")
        reopened=Store(self.store.directory)
        state=reopened.content_label_state(self.ids)
        tag=next(t for t in state["tags"] if t["id"]==self.a)
        self.assertEqual((tag["manual_count"],tag["excluded_count"],tag["effective_count"]),(1,1,1))
        self.assertEqual(reopened.list_images(content_tag=self.a)["total"],1)

    def test_duplicate_restore_maps_overrides_and_purge_forgets_archive(self):
        self.apply(self.ids[1:2]);self.apply(self.ids[1:2],tag=self.b,action="exclude")
        duplicates=DuplicateManager(self.store,Organizer(self.store).lock);worker=Worker()
        scan=duplicates.create_scan();duplicates.scan(worker,"scan",{"scan_id":scan["id"]})
        duplicates.select_all(scan["id"]);plan=duplicates.preview_cleanup(scan["id"])
        duplicates.claim_plan(plan["id"],"cleanup");duplicates.execute_cleanup(worker,"cleanup",{"plan_id":plan["id"]})
        item=next(item for item in plan["items"] if item["image_id"]==self.ids[1])
        # Reuse removed image IDs deliberately; restoration must remap relations.
        replacement=self.add("replacement.png",b"unrelated replacement bytes")
        self.assertEqual(replacement,self.ids[1])
        duplicates.undo_cleanup(worker,"restore",{"job_id":"cleanup"})
        with self.store.connect() as db:
            restored=db.execute("SELECT id FROM images WHERE path=?",(item["source"],)).fetchone()[0]
            self.assertNotEqual(restored,self.ids[1])
            self.assertEqual([tuple(row) for row in db.execute("SELECT tag_id,action FROM image_content_overrides WHERE image_id=? ORDER BY tag_id",(restored,))],[(self.a,"include"),(self.b,"exclude")])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM duplicate_archived_image_content_overrides").fetchone()[0],0)
        scan=duplicates.create_scan();duplicates.scan(worker,"scan2",{"scan_id":scan["id"]})
        duplicates.select_all(scan["id"]);plan=duplicates.preview_cleanup(scan["id"])
        duplicates.claim_plan(plan["id"],"cleanup");duplicates.execute_cleanup(worker,"cleanup2",{"plan_id":plan["id"]})
        purge=duplicates.preview_purge("cleanup2");duplicates.claim_plan(purge["id"],"purge")
        duplicates.execute_purge(worker,"purge",{"plan_id":purge["id"]})
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM duplicate_archived_image_content_overrides").fetchone()[0],0)


if __name__=="__main__": unittest.main()
