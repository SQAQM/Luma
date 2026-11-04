"""Regression coverage uses generated images in temporary directories only."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from .jobs import JobManager,scan_files
from .organizer import Organizer, category_name, safe_target
from .store import Store


class ContentCatalogTests(unittest.TestCase):
    """All 0.3 fixtures are generated files and explicit temporary databases."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="luma-v3-tests-")
        self.directory = Path(self.temp.name)
        self.source = self.directory/"source"
        self.source.mkdir()
        self.store = Store(self.directory/"data")
        self.root = self.store.add_root(self.source)
        self.organizer = Organizer(self.store)
        self.landscape,self.portrait = self.store.content_tag_definitions()

    def tearDown(self):
        self.temp.cleanup()

    def add(self,name="generated.png",folder="",root=None,tags=None):
        path = self.source/folder/name
        path.parent.mkdir(parents=True,exist_ok=True)
        Image.new("RGB",(24,16),(50,100,180)).save(path)
        image_id = self.store.upsert(path,root or self.root,path.stat(),{"format":"PNG","tags":tags or ["landscape","portrait"],"prompt":"synthetic prompt only"})
        return image_id,path

    def match(self,image_id,score=.8,tag=None,source="vision"):
        return self.store.set_content_matches(image_id,[{"tag_id":(tag or self.landscape)["id"],"score":score,"model_id":"synthetic-local-model"}],source=source)

    def test_legacy_schema_migration_keeps_prompt_tags_and_overlapping_roots(self):
        class LegacyStore(Store):
            def _initialize_v3(self,db): pass
        legacy = LegacyStore(self.directory/"legacy")
        parent = legacy.add_root(self.source)
        child_dir = self.source/"nested";child_dir.mkdir()
        child = legacy.add_root(child_dir)
        path = child_dir/"legacy.png"
        Image.new("RGB",(10,10)).save(path)
        with legacy.connect() as db:
            db.execute("INSERT INTO images(path,root_id,name,size,mtime_ns,created_at,indexed_at,tags,prompt) VALUES(?,?,?,?,?,?,?,?,?)",(str(path),child,path.name,path.stat().st_size,path.stat().st_mtime_ns,time.time(),time.time(),'["landscape"]',"landscape"))
        migrated = Store(legacy.directory)
        image = migrated.list_images()["items"][0]
        self.assertEqual(image["prompt_tags"],["landscape"])
        self.assertEqual(image["content_tags"],[])
        self.assertEqual(migrated.list_images(content_tag="风景")["total"],0)
        self.assertEqual(migrated.list_images(root_id=parent)["total"],1)
        self.assertEqual(migrated.list_images(root_id=child)["total"],1)
        migrated.replace_content_tags([])
        reopened = Store(legacy.directory)
        self.assertEqual(reopened.content_tag_definitions(),[])
        self.assertEqual(reopened.stats()["total"],1)
        self.assertEqual([r["count"] for r in reopened.roots()],[1,1])

    def test_content_filter_cursor_and_fts_are_independent_of_prompt_tags(self):
        ids=[]
        for index in range(9):
            image_id,_ = self.add(f"{index}.png")
            if index%2==0:
                ids.append(image_id);self.match(image_id)
        self.assertEqual(self.store.list_images(tag="landscape")["total"],9)
        self.assertEqual(self.store.list_images(content_tag=self.landscape["id"])["total"],5)
        self.assertEqual(self.store.list_images(q="风景")["total"],5)
        for sort in ("newest","oldest","name"):
            seen,cursor=[],None
            while True:
                page=self.store.list_images(content_tag="风景",limit=2,cursor=cursor,sort=sort)
                seen.extend(i["id"] for i in page["items"])
                cursor=page["next_cursor"]
                if not cursor:break
            self.assertEqual(set(seen),set(ids));self.assertEqual(len(seen),len(ids))
        cursor=self.store.list_images(content_tag="风景",limit=2)["next_cursor"]
        with self.assertRaises(ValueError):self.store.list_images(content_tag="人像",cursor=cursor)
        self.assertEqual(self.store.stats()["content_tags"][0]["count"],5)

    def test_rescan_preserves_content_matches_and_marks_changed_file_stale(self):
        image_id,path=self.add();self.match(image_id)
        self.store.upsert(path,self.root,path.stat(),{"tags":["new-prompt-word"]})
        result=self.store.get_image(image_id)
        self.assertEqual(result["prompt_tags"],["new-prompt-word"])
        self.assertEqual(result["content_tags"][0]["name"],"风景")
        self.assertFalse(result["content_tags"][0]["stale"])
        Image.new("RGB",(50,60)).save(path)
        self.store.upsert(path,self.root,path.stat(),{"tags":["landscape"]})
        self.assertTrue(self.store.get_image(image_id)["content_tags"][0]["stale"])
        plan=self.organizer.preview("content_tag",self.directory/"out",[image_id])
        self.assertEqual(plan["movable"],0)

    def test_definition_edits_invalidate_machine_and_preserve_manual_evidence(self):
        image_id,_=self.add();self.match(image_id)
        self.match(image_id,.9,source="manual")
        self.assertEqual(self.store.stats()["content_tags"][0]["count"],1)
        definitions=self.store.content_tag_definitions();definitions[0]["description"]+=" edited"
        self.store.replace_content_tags(definitions)
        tags=self.store.get_image(image_id)["content_tags"]
        self.assertEqual([t["source"] for t in tags],["manual"])
        self.assertEqual(self.store.stats()["content_tags"][0]["count"],1)
        with self.assertRaises(ValueError):
            self.store.set_content_matches(image_id,[{"tag_id":self.landscape["id"],"score":.8,"definition_revision":1}])
        self.store.set_content_matches(image_id,[])
        self.assertEqual(self.store.get_image(image_id)["content_tags"][0]["source"],"manual")

    def test_invalid_or_below_threshold_scores_never_create_categories(self):
        image_id,_=self.add()
        self.match(image_id,.1)
        self.assertEqual(self.store.get_image(image_id)["content_tags"],[])
        for score in (float("nan"),float("inf"),2,True):
            with self.assertRaises(ValueError):self.match(image_id,score)
        self.assertEqual(self.store.get_image(image_id)["content_tags"],[])

    def test_content_organization_uses_evidence_and_highest_score_and_can_undo(self):
        image_id,path=self.add(); original=path.read_bytes()
        prompt_only,prompt_path=self.add("prompt-only.png")
        self.store.set_content_matches(image_id,[{"tag_id":self.landscape["id"],"score":.7},{"tag_id":self.portrait["id"],"score":.9}])
        out=self.directory/"out"
        plan=self.organizer.preview("content_tag",out,[image_id,prompt_only])
        self.assertEqual(plan["movable"],1)
        self.assertEqual(plan["items"][0]["category"],"人像")
        self.assertIn("最高相似度",plan["items"][0]["reason"])
        self.assertTrue(path.exists());self.assertFalse(out.exists())
        manager=JobManager(self.store,self.organizer)
        try:
            count=self.organizer.claim_plan(plan["id"])
            job=manager.submit("organize",{"plan_id":plan["id"]},count)
            self.wait_job(manager,job)
            target=out/"人像"/path.name
            self.assertEqual(target.read_bytes(),original);self.assertTrue(prompt_path.exists())
            self.assertEqual(self.store.list_images(root_id=self.root)["total"],1)
            undo=manager.submit("undo",{"job_id":job["id"]},1)
            self.wait_job(manager,undo)
            self.assertEqual(path.read_bytes(),original)
            self.assertEqual(self.store.get_image(image_id)["content_tags"][0]["name"],"人像")
            self.assertEqual(self.store.list_images(root_id=self.root)["total"],2)
        finally:manager.close()

    def wait_job(self,manager,job):
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            value=manager.get(job["id"])
            if value["status"] not in ("queued","running"):
                self.assertEqual(value["status"],"completed",value);return value
            time.sleep(.01)
        self.fail("Synthetic background job timed out")

    def test_content_rule_takes_priority_over_default_category(self):
        image_id,_=self.add();self.match(image_id)
        self.store.replace_rules([{"id":"content","name":"自定义风景文件夹","kind":"content_tag","match":"风景","destination":"","enabled":True}])
        plan=self.organizer.preview("content_tag",self.directory/"out",[image_id])
        self.assertEqual(plan["items"][0]["category"],"自定义风景文件夹")

    def test_remove_root_keeps_shared_images_and_never_changes_any_file(self):
        shared,path=self.add("shared.png","nested")
        exclusive,exclusive_path=self.add("exclusive.png")
        other=self.store.add_root(self.source/"nested")
        self.store.attach_root_images(other,[str(path)])
        before={p:p.read_bytes() for p in (path,exclusive_path)}
        self.match(shared);self.match(exclusive)
        preview=self.store.preview_remove_root(self.root)
        self.assertEqual((preview["count"],preview["remove"],preview["shared"]),(2,1,1))
        result=self.store.remove_root(self.root,plan_id=preview["id"])
        self.assertEqual(result["removed"],1)
        self.assertIsNone(self.store.get_image(exclusive))
        self.assertEqual(self.store.get_image(shared)["root_id"],other)
        self.assertEqual(self.store.stats()["total"],1)
        self.assertEqual(self.store.list_images(q="synthetic")["total"],1)
        self.assertEqual(self.store.stats()["content_tags"][0]["count"],1)
        for p,data in before.items():self.assertEqual(p.read_bytes(),data)

    def test_root_removal_refuses_changed_catalog_missing_preview_or_active_work(self):
        self.add()
        with self.assertRaises(ValueError):self.store.remove_root(self.root)
        preview=self.store.preview_remove_root(self.root);self.add("new.png")
        with self.assertRaisesRegex(ValueError,"改变"):self.store.remove_root(self.root,plan_id=preview["id"])
        with self.store.connect() as db:
            db.execute("INSERT INTO jobs(id,kind,status,created_at,updated_at) VALUES('busy','classify','running',?,?)",(time.time(),time.time()))
        self.assertFalse(self.store.preview_remove_root(self.root)["can_remove"])
        with self.store.connect() as db:db.execute("UPDATE jobs SET status='finished' WHERE id='busy'")
        preview=self.store.preview_remove_root(self.root)
        self.store.remove_root(self.root,expected_revision=preview["revision"])
        self.assertEqual(self.store.stats()["total"],0)

    def test_root_removal_blocks_live_plans_and_undo_journals(self):
        image_id,path=self.add();self.match(image_id)
        plan=self.organizer.preview("content_tag",self.directory/"out",[image_id])
        self.assertFalse(self.store.preview_remove_root(self.root)["can_remove"])
        with self.store.connect() as db:
            db.execute("UPDATE plans SET status='cancelled' WHERE id=?",(plan["id"],))
            db.execute("INSERT INTO jobs(id,kind,status,created_at,updated_at) VALUES('old','organize','finished',?,?)",(time.time(),time.time()))
        journal_id,_=self.organizer._journal("old",image_id,path,self.directory/"target.png",path.stat(),self.root)
        self.organizer._state(journal_id,"committed")
        self.assertFalse(self.store.preview_remove_root(self.root)["can_remove"])
        self.organizer._state(journal_id,"undone")
        self.assertTrue(self.store.preview_remove_root(self.root)["can_remove"])

    def test_cancelled_root_removal_rolls_back_memberships_fts_and_counts(self):
        paths=[]
        for index in range(15):
            _,path=self.add(f"{index}.png");paths.append(path)
        preview=self.store.preview_remove_root(self.root)
        calls=0
        def cancelled():
            nonlocal calls
            calls+=1
            return calls>=6
        from .organizer import Cancelled
        with self.assertRaises(Cancelled):self.store.remove_root(self.root,plan_id=preview["id"],cancelled=cancelled)
        self.assertEqual(self.store.list_images(q="synthetic")["total"],15)
        self.assertEqual(self.store.roots()[0]["count"],15)
        self.assertEqual(self.store.stats()["total"],15)
        self.assertTrue(all(p.is_file() for p in paths))
        self.store.remove_root(self.root,plan_id=preview["id"])
        self.assertEqual(self.store.stats()["total"],0)


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="luma-tests-")
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "source"
        self.source.mkdir()
        self.destination = self.directory / "organized"
        self.store = Store(self.directory / "data")
        self.organizer = Organizer(self.store)
        self.manager = JobManager(self.store,self.organizer)
        self.root_id = self.store.add_root(self.source)

    def tearDown(self):
        self.manager.close()
        self.temporary.cleanup()

    def add(self, name="image.png", subfolder="", tags=None, model="Anima"):
        path = self.source / subfolder / name
        path.parent.mkdir(parents=True,exist_ok=True)
        info = PngInfo()
        info.add_text("parameters",f"1girl, landscape\nNegative prompt: blurry\nSteps: 20, Model: {model}, Seed: 42")
        Image.new("RGB",(20,12),(90,120,160)).save(path,pnginfo=info)
        image_id = self.store.upsert(path,self.root_id,path.stat(),{"width":20,"height":12,"format":"PNG","model":model,"family":model,"tags":tags or ["landscape"],"prompt":"blue landscape","confidence":0.8})
        return image_id,path

    def wait_job(self, job):
        deadline = time.monotonic()+20
        while time.monotonic()<deadline:
            current = self.manager.get(job["id"])
            if current["status"] not in ("queued","running"):
                return current
            time.sleep(0.02)
        self.fail("Background job did not finish")

    def execute(self, plan):
        total = self.organizer.claim_plan(plan["id"])
        return self.wait_job(self.manager.submit("organize",{"plan_id":plan["id"]},total))

    def test_cursor_pagination_handles_ties_and_filter_mismatch(self):
        ids = []
        for index in range(13):
            image_id,path = self.add("same.png",str(index))
            ids.append(image_id)
        with self.store.connect() as db:
            db.execute("UPDATE images SET created_at=100")
        for sort in ("newest","oldest","name"):
            received,cursor = [],None
            while True:
                page = self.store.list_images(limit=3,cursor=cursor,sort=sort)
                received.extend(item["id"] for item in page["items"])
                cursor = page["next_cursor"]
                if not cursor:
                    break
            self.assertCountEqual(received,ids)
            self.assertEqual(len(set(received)),len(ids))
        cursor = self.store.list_images(limit=3)["next_cursor"]
        with self.assertRaises(ValueError):
            self.store.list_images(cursor=cursor,model="FLUX")
        with self.assertRaises(ValueError):
            self.store.list_images(cursor="garbage")

    def test_fts_and_tag_updates_preserve_user_state(self):
        image_id,path = self.add(tags=["1girl","中文 标签"])
        self.store.patch_image(image_id,{"tags":["sunset","sunset"],"favorite":True,"rating":5})
        self.assertEqual(self.store.list_images(q="sunset")["total"],1)
        self.assertEqual(self.store.list_images(tag="1girl")["total"],0)
        self.assertEqual(self.store.list_images(tag="sunset",favorite=True)["total"],1)
        self.assertEqual(self.store.list_images(q='landscape " OR *')["total"],0)
        self.store.upsert(path,self.root_id,path.stat(),{"family":"Anima","tags":["changed"]})
        self.assertTrue(self.store.get_image(image_id)["favorite"])
        self.assertEqual(self.store.get_image(image_id)["rating"],5)

    def test_preview_is_read_only_and_reports_same_name_collisions(self):
        first,path = self.add("same.png","a")
        second,other = self.add("same.png","b")
        before = path.read_bytes()
        plan = self.organizer.preview("model",self.destination)
        self.assertFalse(self.destination.exists())
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual((plan["total"],plan["movable"],plan["conflicts"]),(2,1,1))
        self.assertEqual(len(plan["items"]),2)

    def test_move_and_undo_verify_bytes_and_keep_metadata(self):
        image_id,path = self.add()
        original = path.read_bytes()
        plan = self.organizer.preview("model",self.destination,[image_id])
        job = self.execute(plan)
        self.assertEqual(job["status"],"completed",job)
        target = Path(plan["items"][0]["destination"])
        self.assertFalse(path.exists())
        self.assertEqual(target.read_bytes(),original)
        self.assertEqual(self.store.get_image(image_id)["path"],str(target))
        with self.store.connect() as db:
            journal = db.execute("SELECT * FROM journal WHERE job_id=?",(job["id"],)).fetchone()
        self.assertEqual(journal["state"],"committed")
        self.assertEqual(journal["digest"],hashlib.sha256(original).hexdigest())
        undo = self.wait_job(self.manager.submit("undo",{"job_id":job["id"]},1))
        self.assertEqual(undo["status"],"completed",undo)
        self.assertTrue(path.exists())
        self.assertFalse(target.exists())
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(self.store.get_image(image_id)["root_id"],self.root_id)

    def test_destination_created_after_preview_is_never_overwritten(self):
        image_id,path = self.add()
        plan = self.organizer.preview("model",self.destination,[image_id])
        target = Path(plan["items"][0]["destination"])
        target.parent.mkdir(parents=True)
        target.write_bytes(b"preexisting unrelated file")
        job = self.execute(plan)
        self.assertEqual(job["errors"],1)
        self.assertEqual(target.read_bytes(),b"preexisting unrelated file")
        self.assertTrue(path.exists())

    def test_source_changed_after_preview_is_preserved(self):
        image_id,path = self.add()
        plan = self.organizer.preview("model",self.destination,[image_id])
        path.write_bytes(b"changed after preview")
        job = self.execute(plan)
        self.assertEqual(job["errors"],1)
        self.assertEqual(path.read_bytes(),b"changed after preview")
        self.assertFalse(Path(plan["items"][0]["destination"]).exists())

    def test_undo_refuses_source_conflict_and_modified_destination(self):
        image_id,path = self.add()
        plan = self.organizer.preview("model",self.destination,[image_id])
        job = self.execute(plan)
        target = Path(plan["items"][0]["destination"])
        path.write_bytes(b"new user file")
        undo = self.wait_job(self.manager.submit("undo",{"job_id":job["id"]},1))
        self.assertEqual(undo["errors"],1)
        self.assertEqual(path.read_bytes(),b"new user file")
        path.unlink()
        target.write_bytes(b"edited moved file")
        undo = self.wait_job(self.manager.submit("undo",{"job_id":job["id"]},1))
        self.assertEqual(undo["errors"],1)
        self.assertEqual(target.read_bytes(),b"edited moved file")
        self.assertFalse(path.exists())

    def journal_job(self):
        job_id = "simulated-crash"
        with self.store.connect() as db:
            db.execute("INSERT INTO jobs(id,kind,status,created_at,updated_at) VALUES(?,?,?,?,?)",(job_id,"organize","running",time.time(),time.time()))
        return job_id

    def test_crash_after_publication_recovers_catalog_without_data_loss(self):
        image_id,path = self.add()
        target = self.destination / "Anima" / path.name
        job_id = self.journal_job()
        journal_id,temp = self.organizer._journal(job_id,image_id,path,target,path.stat(),self.root_id)
        target.parent.mkdir(parents=True)
        shutil.copy2(path,target)
        self.organizer._state(journal_id,"published",hashlib.sha256(path.read_bytes()).hexdigest())
        self.organizer.recover()
        self.assertFalse(path.exists())
        self.assertTrue(target.exists())
        self.assertEqual(self.store.get_image(image_id)["path"],str(target))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM journal WHERE id=?",(journal_id,)).fetchone()[0],"committed")

    def test_crash_before_publication_keeps_both_copies(self):
        image_id,path = self.add()
        target = self.destination / "Anima" / path.name
        journal_id,temp = self.organizer._journal(self.journal_job(),image_id,path,target,path.stat(),self.root_id)
        target.parent.mkdir(parents=True)
        shutil.copy2(path,target)
        self.organizer._state(journal_id,"verified",hashlib.sha256(path.read_bytes()).hexdigest())
        self.organizer.recover()
        self.assertTrue(path.exists())
        self.assertTrue(target.exists())
        self.assertEqual(self.store.get_image(image_id)["path"],str(path))

    def test_copy_fallback_on_filesystem_without_hardlinks(self):
        image_id,path = self.add()
        plan = self.organizer.preview("model",self.destination,[image_id])
        with patch("backend.organizer.os.link",side_effect=OSError(1,"unsupported")):
            job = self.execute(plan)
        self.assertEqual(job["status"],"completed",job)
        self.assertFalse(path.exists())
        self.assertTrue(Path(plan["items"][0]["destination"]).exists())

    def test_import_is_incremental_and_corrupt_image_does_not_abort(self):
        self.add()
        (self.source/"corrupt.png").write_bytes(b"bad header")
        job = self.wait_job(self.manager.submit("import",{"path":str(self.source),"recursive":True}))
        self.assertEqual(job["processed"],2)
        self.assertEqual(job["errors"],1)
        self.assertEqual(self.store.stats()["total"],1)
        with patch("backend.metadata.extract_metadata",side_effect=AssertionError("unchanged file was decoded")):
            (self.source/"corrupt.png").unlink()
            job = self.wait_job(self.manager.submit("import",{"path":str(self.source),"recursive":True}))
        self.assertEqual(job["errors"],0)
        self.assertEqual(job["processed"],1)

    def test_safe_categories_cannot_escape_destination(self):
        for category in ("../../outside","CON","AUX.txt","", "a"*200):
            target = safe_target(self.destination,category,"image.png")
            self.assertTrue(target.resolve().is_relative_to(self.destination.resolve()))
        self.assertEqual(category_name("CON"),"_CON")
        with self.assertRaises(ValueError):
            safe_target("relative", "Anima", "image.png")

    def test_unknown_model_and_unmatched_tags_are_skipped(self):
        image_id,path = self.add(model="未知")
        self.assertEqual(self.organizer.preview("model",self.destination)["movable"],0)
        self.assertEqual(self.organizer.preview("tag",self.destination)["movable"],0)
        self.store.replace_rules([{"id":"custom","name":"自定义底模","kind":"model","match":"未知","destination":"","enabled":True}])
        plan = self.organizer.preview("model",self.destination)
        self.assertEqual(plan["movable"],1)
        self.assertEqual(Path(plan["items"][0]["destination"]).parent,self.destination/"自定义底模")

    def test_txt_and_json_companions_move_and_undo_together(self):
        image_id,path = self.add()
        txt = path.with_suffix(".txt")
        metadata_json = Path(str(path)+".json")
        txt.write_text("1girl\nSteps: 20, Model: Anima",encoding="utf-8")
        metadata_json.write_text('{"custom":"preserve me"}',encoding="utf-8")
        self.store.upsert(path,self.root_id,path.stat(),{"family":"Anima","model":"Anima","raw_metadata":{"sidecar_name":txt.name}})
        plan = self.organizer.preview("model",self.destination,[image_id])
        self.assertEqual(len(plan["items"][0]["companions"]),2)
        self.assertFalse(self.destination.exists())
        job = self.execute(plan)
        self.assertEqual(job["status"],"completed",job)
        self.assertFalse(txt.exists())
        self.assertFalse(metadata_json.exists())
        target = Path(plan["items"][0]["destination"])
        self.assertTrue(target.with_suffix(".txt").exists())
        self.assertTrue(Path(str(target)+".json").exists())
        undo = self.wait_job(self.manager.submit("undo",{"job_id":job["id"]},1))
        self.assertEqual(undo["status"],"completed",undo)
        self.assertEqual(undo["processed"],1)
        self.assertTrue(path.exists())
        self.assertIn("Anima",txt.read_text(encoding="utf-8"))
        self.assertEqual(metadata_json.read_text(encoding="utf-8"),'{"custom":"preserve me"}')

    def test_sidecar_changes_require_reindex_even_if_image_unchanged(self):
        image_id,path = self.add()
        txt = path.with_suffix(".txt")
        txt.write_text("one",encoding="utf-8")
        self.assertFalse(self.store.unchanged(path,path.stat()))
        self.store.upsert(path,self.root_id,path.stat(),{"family":"Anima"})
        self.assertTrue(self.store.unchanged(path,path.stat()))
        txt.write_text("changed sidecar",encoding="utf-8")
        self.assertFalse(self.store.unchanged(path,path.stat()))
        self.assertEqual(self.organizer.preview("model",self.destination)["movable"],0)

    def test_bundle_failure_compensates_image_move(self):
        image_id,path = self.add()
        txt = path.with_suffix(".txt")
        txt.write_text("original metadata",encoding="utf-8")
        self.store.upsert(path,self.root_id,path.stat(),{"family":"Anima"})
        plan = self.organizer.preview("model",self.destination,[image_id])
        real_move = self.organizer.move
        def fail_sidecar(*args,**kwargs):
            if kwargs.get("asset_kind")=="sidecar":
                raise OSError("simulated TXT failure")
            return real_move(*args,**kwargs)
        with patch.object(self.organizer,"move",side_effect=fail_sidecar):
            job = self.execute(plan)
        self.assertEqual(job["errors"],1)
        self.assertTrue(path.exists())
        self.assertTrue(txt.exists())
        self.assertEqual(self.store.get_image(image_id)["path"],str(path))
        self.assertFalse(Path(plan["items"][0]["destination"]).exists())

    def test_crash_between_image_and_txt_completes_approved_bundle(self):
        image_id,path = self.add()
        txt = path.with_suffix(".txt")
        txt.write_text("original metadata",encoding="utf-8")
        self.store.upsert(path,self.root_id,path.stat(),{"family":"Anima"})
        plan = self.organizer.preview("model",self.destination,[image_id])
        job_id = self.journal_job()
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET payload=? WHERE id=?",(json.dumps({"plan_id":plan["id"]}),job_id))
        target = Path(plan["items"][0]["destination"])
        self.organizer.move(job_id,image_id,path,target,path.stat().st_size,path.stat().st_mtime_ns)
        self.assertTrue(txt.exists())
        self.organizer.recover()
        self.assertFalse(txt.exists())
        self.assertTrue(target.with_suffix(".txt").exists())
        self.assertEqual(self.organizer.plan(plan["id"])["items"][0]["status"],"moved")

    def test_shared_stem_metadata_is_not_removed(self):
        first,first_path = self.add("shared.png")
        second,second_path = self.add("shared.jpg")
        txt = first_path.with_suffix(".txt")
        txt.write_text("shared metadata",encoding="utf-8")
        self.store.upsert(first_path,self.root_id,first_path.stat(),{"family":"Anima"})
        self.store.upsert(second_path,self.root_id,second_path.stat(),{"family":"Anima"})
        plan = self.organizer.preview("model",self.destination)
        self.assertEqual(plan["movable"],0)
        self.assertEqual(plan["skipped"],2)
        self.assertTrue(txt.exists())

    def test_persisted_aggregate_counts_follow_moves_and_tag_edits(self):
        image_id,path = self.add(tags=["one","two"])
        self.store.patch_image(image_id,{"favorite":True,"tags":["two","three"]})
        stats = self.store.stats()
        self.assertEqual((stats["total"],stats["favorites"]),(1,1))
        self.assertEqual({item["name"] for item in stats["tags"]},{"two","three"})
        plan = self.organizer.preview("model",self.destination,[image_id])
        job = self.execute(plan)
        roots = self.store.roots()
        self.assertEqual(sum(root["count"] for root in roots),1)
        self.assertEqual(next(root["count"] for root in roots if root["id"]==self.root_id),0)

    def test_recovery_refuses_changed_image_before_moving_txt(self):
        image_id,path = self.add()
        txt = path.with_suffix(".txt")
        txt.write_text("original metadata",encoding="utf-8")
        self.store.upsert(path,self.root_id,path.stat(),{"family":"Anima"})
        plan = self.organizer.preview("model",self.destination,[image_id])
        job_id = self.journal_job()
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET payload=? WHERE id=?",(json.dumps({"plan_id":plan["id"]}),job_id))
        target = Path(plan["items"][0]["destination"])
        self.organizer.move(job_id,image_id,path,target,path.stat().st_size,path.stat().st_mtime_ns)
        target.write_bytes(b"external change after crash")
        self.organizer.recover()
        self.assertTrue(txt.exists())
        self.assertFalse(target.with_suffix(".txt").exists())
        self.assertEqual(target.read_bytes(),b"external change after crash")
        self.assertEqual(self.organizer.plan(plan["id"])["items"][0]["status"],"error")

    def test_shared_ico_sidecar_and_new_shared_image_after_preview_are_skipped(self):
        image_id,path = self.add("shared.png")
        txt = path.with_suffix(".txt")
        txt.write_text("original metadata",encoding="utf-8")
        self.store.upsert(path,self.root_id,path.stat(),{"family":"Anima"})
        plan = self.organizer.preview("model",self.destination,[image_id])
        self.add("shared.ico")
        self.assertEqual(self.organizer.preview("model",self.destination,[image_id])["movable"],0)
        job = self.execute(plan)
        self.assertEqual(job["errors"],1)
        self.assertTrue(path.exists())
        self.assertTrue(txt.exists())

    @unittest.skipUnless(os.name=="nt","NTFS junction regression is Windows-specific")
    def test_junction_after_preview_cannot_escape_approved_root(self):
        image_id,path = self.add()
        txt = path.with_suffix(".txt")
        txt.write_text("original metadata",encoding="utf-8")
        self.store.upsert(path,self.root_id,path.stat(),{"family":"Anima"})
        plan = self.organizer.preview("model",self.destination,[image_id])
        outside = self.directory/"unapproved"
        outside.mkdir()
        junction = self.destination/"Anima"
        self.destination.mkdir()
        subprocess.run(["cmd","/c","mklink","/J",str(junction),str(outside)],check=True,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertFalse(junction.is_symlink())
        job = self.execute(plan)
        self.assertEqual(job["errors"],1)
        self.assertTrue(path.exists())
        self.assertTrue(txt.exists())
        self.assertEqual(list(outside.iterdir()),[])
        junction.rmdir()

    @unittest.skipUnless(os.name=="nt","NTFS junction regression is Windows-specific")
    def test_scanner_never_follows_ntfs_junctions(self):
        self.add("inside.png")
        outside = self.directory/"outside-source"
        outside.mkdir()
        Image.new("RGB",(2,2)).save(outside/"not-imported.png")
        junction = self.source/"junction"
        subprocess.run(["cmd","/c","mklink","/J",str(junction),str(outside)],check=True,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
        rows = list(scan_files(self.source))
        self.assertEqual([Path(path).name for path,info,error in rows],["inside.png"])
        junction.rmdir()


class APITests(unittest.TestCase):
    def test_session_host_origin_validation_and_real_thumbnail(self):
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory(prefix="luma-api-") as directory:
            with patch.dict(os.environ,{"LUMA_DATA_DIR":directory,"LUMA_TOKEN":"test-secret-token"}):
                from . import app as app_module
                previous = (app_module.store,app_module.organizer,app_module.manager,app_module.TOKEN)
                app_module.store = Store(directory)
                app_module.organizer = Organizer(app_module.store)
                app_module.manager = JobManager(app_module.store,app_module.organizer)
                app_module.TOKEN = "test-secret-token"
                try:
                    path = Path(directory)/"thumb.png"
                    Image.new("RGBA",(50,30),(80,140,170,120)).save(path)
                    root_id = app_module.store.add_root(directory)
                    image_id = app_module.store.upsert(path,root_id,path.stat(),{"width":50,"height":30,"format":"PNG"})
                    with TestClient(app_module.app) as client:
                        self.assertEqual(client.get("/api/health").json()["service"],"luma")
                        self.assertEqual(client.get("/api/images").status_code,401)
                        self.assertEqual(client.post("/api/session",json={"token":"wrong"}).status_code,401)
                        self.assertEqual(client.post("/api/session",json={"token":"test-secret-token"}).status_code,200)
                        self.assertEqual(client.get("/api/images").json()["total"],1)
                        self.assertEqual(client.get("/api/images",headers={"Origin":"https://evil.example"}).status_code,403)
                        self.assertEqual(client.get("/api/health",headers={"Host":"evil.example"}).status_code,403)
                        self.assertEqual(client.get("/api/images?limit=1000000").status_code,422)
                        self.assertEqual(client.get("/api/images?cursor=bad").status_code,400)
                        thumb = client.get(f"/api/images/{image_id}/thumbnail")
                        self.assertEqual(thumb.status_code,200)
                        self.assertEqual(thumb.headers["content-type"],"image/jpeg")
                        self.assertEqual(client.patch(f"/api/images/{image_id}",json={"rating":7}).status_code,422)
                        self.assertEqual(client.get("/api/images/999999/original").status_code,404)
                        self.assertEqual(client.put("/api/settings",json={"unsafePath":"anything"}).status_code,400)
                finally:
                    app_module.manager.close()
                    app_module.store,app_module.organizer,app_module.manager,app_module.TOKEN = previous


if __name__ == "__main__":
    unittest.main()
