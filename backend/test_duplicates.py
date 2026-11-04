"""Exact duplicate regression tests: synthetic bytes, isolated SQLite only."""
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from .duplicates import DuplicateManager, VerifiedFile
from .jobs import JobManager
from .organizer import Cancelled, Organizer
from .store import Store


class Worker:
    def __init__(self):
        self.stop=False; self.updates=[]
    def cancelled(self, unused): return self.stop
    def progress(self, unused, **values): self.updates.append(values)


class DuplicateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="luma-duplicate-tests-")
        self.directory=Path(self.temp.name)
        self.source=self.directory/"synthetic"; self.source.mkdir()
        self.store=Store(self.directory/"isolated-data")
        self.root=self.store.add_root(self.source)
        self.organizer=Organizer(self.store)
        self.duplicates=DuplicateManager(self.store,self.organizer.lock)
        self.worker=Worker()

    def tearDown(self): self.temp.cleanup()

    def add(self,name,data=b"generated synthetic bytes",folder=None):
        path=(folder or self.source)/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
        image_id=self.store.upsert(path,self.root,path.stat(),{"format":"PNG","prompt":"synthetic private sentinel","negative_prompt":"synthetic negative","tags":["fixture-tag"],"parameters":{"fixture":True}})
        return image_id,path

    def scan(self,root_id=None):
        scan=self.duplicates.create_scan(root_id)
        self.duplicates.scan(self.worker,"scan-fixture",{"scan_id":scan["id"]})
        return self.duplicates.get_scan(scan["id"])

    def prepare(self,scan):
        self.duplicates.select_all(scan["id"])
        return self.duplicates.preview_cleanup(scan["id"])

    def cleanup(self,plan,job=None):
        job=job or uuid.uuid4().hex
        self.duplicates.claim_plan(plan["id"],"cleanup")
        self.duplicates.execute_cleanup(self.worker,job,{"plan_id":plan["id"]})
        return job

    def two(self): return self.add("keep.png"),self.add("duplicate.png")

    def test_constructor_and_listing_never_open_files(self):
        self.two()
        with patch("builtins.open",side_effect=AssertionError("unexpected byte access")):
            other=DuplicateManager(self.store,self.organizer.lock)
            self.assertIsNone(other.latest_scan())
            self.assertEqual(other.history(),[])
            self.assertEqual(other.recovery_status()["total"],0)

    def test_exact_bytes_only_not_name_extension_or_size(self):
        self.add("first.png",b"AAAA"); self.add("other.jpg",b"AAAA")
        self.add("first-copy.png",b"BBBB"); self.add("single.png",b"other length")
        scan=self.scan()
        self.assertEqual((scan["groups"],scan["duplicates"],scan["bytes"]),(1,1,4))
        group=self.duplicates.groups(scan["id"])["items"][0]
        self.assertFalse(group["selected"])
        self.assertEqual(self.duplicates.members(group["id"])["total"],2)

    def test_size_prefilter_does_not_hash_unique_size_files(self):
        self.add("small.png",b"a");self.add("large.png",b"abcd")
        with patch.object(VerifiedFile,"digest",side_effect=AssertionError("unexpected hash")):
            scan=self.scan()
        self.assertEqual(scan["hashed"],0)

    def test_second_check_reuses_unchanged_cache(self):
        self.two(); first=self.scan();second=self.scan()
        self.assertEqual(first["hashed"],2);self.assertEqual(second["cached"],2)
        self.assertEqual(second["groups"],1)

    def test_same_size_mtime_edit_invalidates_windows_change_token(self):
        (one,a),(two,b)=self.two();self.scan()
        old_a,old_b=a.stat(),b.stat()
        newer=b"x"*old_a.st_size
        a.write_bytes(newer);b.write_bytes(newer)
        os.utime(a,ns=(old_a.st_atime_ns,old_a.st_mtime_ns));os.utime(b,ns=(old_b.st_atime_ns,old_b.st_mtime_ns))
        scan=self.scan()
        self.assertEqual(scan["cached"],0);self.assertEqual(scan["hashed"],2)
        group=self.duplicates.groups(scan["id"])["items"][0]
        self.assertEqual(group["digest"],hashlib.sha256(newer).hexdigest())

    def test_forced_hash_collision_partitions_exact_groups(self):
        self.add("different.png",b"AAAA");self.add("one.png",b"BBBB");self.add("two.png",b"BBBB")
        with patch.object(VerifiedFile,"digest",return_value="f"*64): scan=self.scan()
        group=self.duplicates.groups(scan["id"])["items"][0]
        names={r["name"] for r in self.duplicates.members(group["id"])["items"]}
        self.assertEqual(names,{"one.png","two.png"})

    def test_group_and_member_pages_are_bounded_scope_bound(self):
        for x in range(8): self.add(f"{x}.png",bytes([x//2])*8)
        scan=self.scan(); page=self.duplicates.groups(scan["id"],limit=2)
        self.assertEqual(len(page["items"]),2)
        other=self.duplicates.groups(scan["id"],page["next_cursor"],2)
        self.assertEqual(len(other["items"]),2)
        with self.assertRaises(ValueError): self.duplicates.groups("other",page["next_cursor"],2)
        group=page["items"][0];members=self.duplicates.members(group["id"],limit=1)
        self.assertIsNotNone(members["next_cursor"])
        with self.assertRaises(ValueError):self.duplicates.members(group["id"],limit=101)

    def test_scope_overlap_membership_does_not_duplicate_indexed_file(self):
        child=self.source/"child";child.mkdir();child_root=self.store.add_root(child)
        i,a=self.add("a.png",folder=child);j,b=self.add("b.png",folder=child)
        self.store.attach_root_images(child_root,[a,b])
        self.assertEqual(self.scan(self.root)["duplicates"],1)
        self.assertEqual(self.scan(child_root)["duplicates"],1)

    def test_scan_cancel_is_durable_and_cannot_clean_partial_results(self):
        self.two();scan=self.duplicates.create_scan();self.worker.stop=True
        with self.assertRaises(Cancelled):self.duplicates.scan(self.worker,"cancel",{"scan_id":scan["id"]})
        self.assertEqual(self.duplicates.get_scan(scan["id"])["status"],"cancelled")
        with self.assertRaises(ValueError):self.duplicates.preview_cleanup(scan["id"])

    def test_hardlinks_skipped_not_reclaimed(self):
        one,a=self.add("first.png");b=self.source/"linked.png";os.link(a,b)
        self.store.upsert(b,self.root,b.stat(),{})
        scan=self.scan()
        self.assertEqual(scan["groups"],0);self.assertEqual(scan["skipped"],2)

    def test_organizer_committed_journal_blocks_its_images(self):
        (i,a),(j,b)=self.two()
        with self.store.connect() as db:
            db.execute("INSERT INTO jobs(id,kind,status,created_at,updated_at) VALUES('old','organize','completed',0,0)")
            db.execute("INSERT INTO journal(job_id,image_id,source,destination,temp_path,size,mtime_ns,state,created_at,updated_at) VALUES('old',?,?,?,?,?,?,'committed',0,0)", (i,str(a),str(a),str(a)+'.temp',a.stat().st_size,a.stat().st_mtime_ns))
        scan=self.scan();self.assertEqual(scan["groups"],0);self.assertEqual(scan["skipped"],1)

    def test_active_move_blocks_scan_creation(self):
        with self.store.connect() as db:
            db.execute("INSERT INTO jobs(id,kind,status,created_at,updated_at) VALUES('move','organize','running',0,0)")
        with self.assertRaises(ValueError):self.duplicates.create_scan()

    def test_preview_required_and_selection_defaults_empty(self):
        self.two();scan=self.scan()
        with self.assertRaises(ValueError):self.duplicates.preview_cleanup(scan["id"])
        with self.assertRaises(ValueError):self.duplicates.claim_plan("missing")

    def test_keep_selection_is_durable_and_invalidates_preview(self):
        (i,a),(j,b)=self.two();scan=self.scan();plan=self.prepare(scan)
        group=self.duplicates.groups(scan["id"])["items"][0]
        self.duplicates.save_keep(group["id"],j)
        self.assertEqual(DuplicateManager(self.store).groups(scan["id"])["items"][0]["keep_id"],j)
        with self.assertRaises(ValueError):self.duplicates.claim_plan(plan["id"])
        newer=self.duplicates.preview_cleanup(scan["id"])
        self.assertEqual(newer["items"][0]["source"],str(a))

    def test_keep_must_be_group_member(self):
        self.two();third,_=self.add("different.png",b"different")
        scan=self.scan();group=self.duplicates.groups(scan["id"])["items"][0]
        with self.assertRaises(ValueError):self.duplicates.save_keep(group["id"],third)

    def test_cleanup_moves_only_confirmed_copy_and_preserves_sidecars(self):
        (i,a),(j,b)=self.two();side=b.with_suffix(".txt");side.write_text("synthetic sidecar")
        plan=self.prepare(self.scan());job=self.cleanup(plan)
        target=Path(plan["items"][0]["destination"])
        self.assertTrue(a.exists());self.assertFalse(b.exists());self.assertTrue(target.exists());self.assertTrue(side.exists())
        self.assertEqual(self.store.stats()["total"],1)
        self.assertEqual(self.duplicates.history()[0]["restorable"],1)
        self.assertFalse(plan["releases_space"])

    def test_complete_undo_restores_metadata_tags_roots_and_bytes(self):
        (i,a),(j,b)=self.two();self.store.patch_image(j,{"favorite":True,"rating":5})
        definition=self.store.content_tag_definitions()[0]
        self.store.set_content_matches(j,[{"tag_id":definition["id"],"score":.8,"model_id":"synthetic"}],source="manual")
        before=self.store.get_image(j)
        plan=self.prepare(self.scan());job=self.cleanup(plan)
        self.duplicates.undo_cleanup(self.worker,"restore",{"job_id":job})
        after=self.store.get_image(j)
        for field in ("prompt","negative_prompt","parameters","tags","favorite","rating","root_id","path"):
            self.assertEqual(after[field],before[field],field)
        self.assertEqual(after["content_tags"],before["content_tags"])
        self.assertEqual(self.store.stats()["total"],2);self.assertTrue(b.exists())

    def test_undo_id_reuse_assigns_new_id_without_overwrite(self):
        (i,a),(j,b)=self.two();plan=self.prepare(self.scan());job=self.cleanup(plan)
        new_id,new_path=self.add("new.png",b"new unique bytes")
        self.assertEqual(new_id,j)
        self.duplicates.undo_cleanup(self.worker,"restore",{"job_id":job})
        self.assertEqual(self.store.get_image(j)["path"],str(new_path))
        with self.store.connect() as db: restored=db.execute("SELECT id FROM images WHERE path=?", (str(b),)).fetchone()[0]
        self.assertNotEqual(restored,j);self.assertTrue(b.exists())

    def test_undo_never_overwrites_original_path_conflict(self):
        (i,a),(j,b)=self.two();plan=self.prepare(self.scan());job=self.cleanup(plan)
        b.write_bytes(b"external new file")
        self.duplicates.undo_cleanup(self.worker,"restore",{"job_id":job})
        self.assertEqual(b.read_bytes(),b"external new file")
        self.assertTrue(Path(plan["items"][0]["destination"]).exists())
        self.assertEqual(self.duplicates.history()[0]["restorable"],1)

    def test_missing_keeper_skips_entire_cleanup_group(self):
        (i,a),(j,b)=self.two();self.add("third.png");plan=self.prepare(self.scan());a.unlink()
        self.cleanup(plan)
        self.assertTrue(b.exists());self.assertTrue((self.source/"third.png").exists())
        self.assertEqual(self.duplicates.history(),[])

    def test_same_size_mtime_mutation_after_preview_does_not_move(self):
        (i,a),(j,b)=self.two();plan=self.prepare(self.scan());info=b.stat()
        b.write_bytes(b"x"*info.st_size);os.utime(b,ns=(info.st_atime_ns,info.st_mtime_ns))
        self.cleanup(plan);self.assertTrue(b.exists());self.assertEqual(self.store.stats()["total"],2)

    def test_cleanup_and_purge_do_not_decode_or_parse_metadata(self):
        self.two()
        with patch.object(self.store,"get_image",side_effect=AssertionError("metadata read")),patch("backend.metadata.extract_metadata",side_effect=AssertionError("metadata parse")),patch("PIL.Image.open",side_effect=AssertionError("decoder")):
            plan=self.prepare(self.scan());job=self.cleanup(plan)
            purge=self.duplicates.preview_purge(job);self.duplicates.claim_plan(purge["id"],"purge")
            self.duplicates.execute_purge(self.worker,"purge",{"plan_id":purge["id"]})
        self.assertFalse(Path(plan["items"][0]["destination"]).exists())
        self.assertEqual(self.duplicates.history()[0]["restorable"],0)

    def test_permanent_purge_requires_new_plan_and_keeps_original(self):
        (i,a),(j,b)=self.two();plan=self.prepare(self.scan());job=self.cleanup(plan)
        with self.assertRaises(ValueError):self.duplicates.execute_purge(self.worker,"purge",{"plan_id":plan["id"]})
        purge=self.duplicates.preview_purge(job)
        self.assertTrue(purge["releases_space"]);self.duplicates.claim_plan(purge["id"],"purge")
        self.duplicates.execute_purge(self.worker,"purge",{"plan_id":purge["id"]})
        self.assertTrue(a.exists());self.assertFalse(Path(plan["items"][0]["destination"]).exists())

    def test_purge_missing_keeper_keeps_quarantine(self):
        (i,a),(j,b)=self.two();plan=self.prepare(self.scan());job=self.cleanup(plan)
        purge=self.duplicates.preview_purge(job);a.unlink();self.duplicates.claim_plan(purge["id"],"purge")
        self.duplicates.execute_purge(self.worker,"purge",{"plan_id":purge["id"]})
        self.assertTrue(Path(plan["items"][0]["destination"]).exists())

    def test_quarantine_replaced_with_same_bytes_is_not_purged(self):
        self.two();plan=self.prepare(self.scan());job=self.cleanup(plan)
        target=Path(plan["items"][0]["destination"]);payload=target.read_bytes();target.unlink();target.write_bytes(payload)
        purge=self.duplicates.preview_purge(job);self.duplicates.claim_plan(purge["id"],"purge")
        self.duplicates.execute_purge(self.worker,"purge",{"plan_id":purge["id"]})
        self.assertTrue(target.exists())

    def test_preview_members_are_fully_pageable(self):
        for index in range(104):self.add(f"{index}.png")
        plan=self.prepare(self.scan())
        self.assertEqual(len(plan["items"]),100);self.assertTrue(plan["items_truncated"])
        cursor=None;seen=[]
        while True:
            page=self.duplicates.plan_items(plan["id"],cursor,30)
            seen.extend(r["id"] for r in page["items"]);cursor=page["next_cursor"]
            if not cursor:break
        self.assertEqual(len(seen),103);self.assertEqual(len(set(seen)),103)

    def test_root_removal_blocked_by_restorable_archive(self):
        self.two();plan=self.prepare(self.scan());job=self.cleanup(plan)
        preview=self.store.preview_remove_root(self.root)
        self.assertFalse(preview["can_remove"])
        self.assertTrue(any("隔离" in v for v in preview["blockers"]))
        self.duplicates.undo_cleanup(self.worker,"restore",{"job_id":job})
        self.assertTrue(self.store.preview_remove_root(self.root)["can_remove"])

    def test_file_and_ancestor_replacement_denied_while_comparing(self):
        (i,a),(j,b)=self.two();plan=self.prepare(self.scan());original=VerifiedFile.equals
        attempted=[]
        def guarded(file,other,cancelled=lambda:False):
            for path in (file.path,other.path):
                with self.assertRaises(OSError):path.write_bytes(b"attack")
            with self.assertRaises(OSError):self.source.rename(self.source.with_name("replaced"))
            attempted.append(True)
            return original(file,other,cancelled)
        with patch.object(VerifiedFile,"equals",guarded):self.cleanup(plan)
        self.assertTrue(attempted);self.assertTrue(a.exists());self.assertFalse(b.exists())

    def test_non_bmp_name_cleanup_restore_and_purge(self):
        self.add("保留😀.png");j,b=self.add("副本🖼️.png")
        plan=self.prepare(self.scan());job=self.cleanup(plan)
        self.duplicates.undo_cleanup(self.worker,"restore",{"job_id":job});self.assertTrue(b.exists())
        newer=self.prepare(self.scan());job=self.cleanup(newer)
        purge=self.duplicates.preview_purge(job);self.duplicates.claim_plan(purge["id"],"purge")
        self.duplicates.execute_purge(self.worker,"purge",{"plan_id":purge["id"]})
        self.assertFalse(Path(newer["items"][0]["destination"]).exists())

    def test_startup_marks_pending_database_only_without_file_reads(self):
        self.two();scan=self.duplicates.create_scan()
        with patch("backend.duplicates.VerifiedFile",side_effect=AssertionError("startup file access")),patch("builtins.open",side_effect=AssertionError("startup bytes")):
            jobs=JobManager(self.store,self.organizer)
            try:self.assertFalse(jobs.recovering)
            finally:jobs.close()
        self.assertEqual(self.duplicates.get_scan(scan["id"])["status"],"interrupted")

    def test_move_archive_crash_explicit_recovery_restores_safe_state(self):
        self.two();plan=self.prepare(self.scan());original=self.duplicates._archive
        with patch.object(self.duplicates,"_archive",side_effect=RuntimeError("synthetic archive crash")):
            job=self.cleanup(plan)
        self.assertEqual(self.duplicates.recovery_status()["total"],1)
        with self.assertRaises(ValueError):self.duplicates.create_scan()
        self.duplicates.recover()
        self.assertEqual(self.duplicates.recovery_status()["total"],0)
        self.assertEqual(self.store.stats()["total"],1)
        self.duplicates.undo_cleanup(self.worker,"restore",{"job_id":job})
        self.assertEqual(self.store.stats()["total"],2)

    def test_purge_intent_recovery_never_deletes_remaining_file(self):
        self.two();plan=self.prepare(self.scan());job=self.cleanup(plan)
        with self.store.connect() as db:db.execute("UPDATE duplicate_journal SET state='purge_prepared' WHERE job_id=?", (job,))
        self.duplicates.recover()
        self.assertTrue(Path(plan["items"][0]["destination"]).exists())
        self.assertEqual(self.duplicates.history()[0]["restorable"],1)

    def test_sql_vm_cancellation_during_grouping_rolls_back(self):
        calls=[0]
        def cancelled():
            calls[0]+=1
            return calls[0]>2
        with self.assertRaises(Cancelled):
            with self.duplicates._scan_db(cancelled) as db:
                db.execute("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<1000000) SELECT SUM(x) FROM n").fetchone()
        self.assertGreater(calls[0],2)

    def test_snapshot_retention_is_bounded_without_deleting_execution_logs(self):
        self.two();initial=self.scan();plan=self.prepare(initial);job=self.cleanup(plan)
        for unused in range(5):self.scan()
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM duplicate_scans").fetchone()[0],3)
            self.assertEqual(db.execute("SELECT COUNT(DISTINCT scan_id) FROM duplicate_files").fetchone()[0],3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM duplicate_journal WHERE job_id=?", (job,)).fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM duplicate_archived_images").fetchone()[0],1)
        self.duplicates.undo_cleanup(self.worker,"late-undo",{"job_id":job})
        self.assertEqual(self.store.stats()["total"],2)
        with self.store.connect() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM duplicate_archived_images").fetchone()[0],0)

    def test_rescan_expires_ready_preview_and_prunes_missing_hash_cache(self):
        (i,a),(j,b)=self.two();plan=self.prepare(self.scan())
        with self.store.connect() as db:db.execute("DELETE FROM images WHERE id=?", (j,))
        self.duplicates.create_scan()
        with self.assertRaises(ValueError):self.duplicates.claim_plan(plan["id"])
        with self.store.connect() as db:self.assertIsNone(db.execute("SELECT 1 FROM duplicate_hash_cache WHERE image_id=?", (j,)).fetchone())

    def test_partial_cleanup_cancel_keeps_durable_restore_action(self):
        self.two();self.add("third.png");plan=self.prepare(self.scan())
        original=self.worker.progress
        def progress(unused,**values):
            original(unused,**values)
            if values.get("processed")==1:self.worker.stop=True
        self.worker.progress=progress
        self.duplicates.claim_plan(plan["id"])
        with self.assertRaises(Cancelled):self.duplicates.execute_cleanup(self.worker,"partial",{"plan_id":plan["id"]})
        self.assertEqual(self.duplicates.history()[0]["restorable"],1)
        self.assertEqual(self.store.stats()["total"],2)
        self.worker.stop=False;self.worker.progress=original
        self.duplicates.undo_cleanup(self.worker,"restore",{"job_id":"partial"})
        self.assertEqual(self.store.stats()["total"],3)

    def test_restore_after_move_database_failure_requires_explicit_recovery(self):
        self.two();plan=self.prepare(self.scan());job=self.cleanup(plan)
        with patch.object(self.duplicates,"_restore_index",side_effect=RuntimeError("synthetic SQL crash")):
            self.duplicates.undo_cleanup(self.worker,"restore",{"job_id":job})
        self.assertEqual(self.duplicates.recovery_status()["total"],1)
        self.duplicates.recover()
        self.assertEqual(self.duplicates.recovery_status()["total"],0)
        self.assertEqual(self.store.stats()["total"],2)

    def test_old_unexecuted_preview_snapshots_are_bounded(self):
        self.two();scan=self.scan()
        for unused in range(8):self.prepare(scan)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM duplicate_plans WHERE status='ready'").fetchone()[0],1)
            self.assertLessEqual(db.execute("SELECT COUNT(*) FROM duplicate_plans WHERE status='stale'").fetchone()[0],3)
            self.assertLessEqual(db.execute("SELECT COUNT(*) FROM duplicate_plan_items").fetchone()[0],4)

    def test_corrupt_quarantine_destination_is_never_purged(self):
        self.two();plan=self.prepare(self.scan());job=self.cleanup(plan)
        target=Path(plan["items"][0]["destination"]);outside=self.source/"outside-owned-quarantine.lumaq";target.rename(outside)
        with self.store.connect() as db:db.execute("UPDATE duplicate_journal SET destination=? WHERE job_id=?", (str(outside),job))
        purge=self.duplicates.preview_purge(job);self.duplicates.claim_plan(purge["id"],"purge")
        self.duplicates.execute_purge(self.worker,"purge",{"plan_id":purge["id"]})
        self.assertTrue(outside.exists())

    def test_corrupt_quarantine_destination_is_never_restored(self):
        self.two();plan=self.prepare(self.scan());job=self.cleanup(plan)
        target=Path(plan["items"][0]["destination"]);outside=self.source/"outside-owned-quarantine.lumaq";target.rename(outside)
        with self.store.connect() as db:db.execute("UPDATE duplicate_journal SET destination=? WHERE job_id=?", (str(outside),job))
        self.duplicates.undo_cleanup(self.worker,"restore",{"job_id":job})
        self.assertTrue(outside.exists())
        self.assertFalse((self.source/"duplicate.png").exists())

    def test_existing_reader_prevents_purge_until_it_is_closed(self):
        self.two();plan=self.prepare(self.scan());job=self.cleanup(plan)
        target=Path(plan["items"][0]["destination"])
        with VerifiedFile(target):
            purge=self.duplicates.preview_purge(job);self.duplicates.claim_plan(purge["id"],"purge")
            self.duplicates.execute_purge(self.worker,"purge-busy",{"plan_id":purge["id"]})
        self.assertTrue(target.exists());self.assertEqual(self.duplicates.history()[0]["restorable"],1)
        newer=self.duplicates.preview_purge(job);self.duplicates.claim_plan(newer["id"],"purge")
        self.duplicates.execute_purge(self.worker,"purge-free",{"plan_id":newer["id"]})
        self.assertFalse(target.exists())

    def test_closing_cancels_preview_without_claimable_half_plan(self):
        self.two();scan=self.scan();self.duplicates.select_all(scan["id"])
        with self.assertRaises(Cancelled):self.duplicates.preview_cleanup(scan["id"],cancelled=lambda:True)
        with self.store.connect() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM duplicate_plans WHERE status='ready'").fetchone()[0],0)
        self.assertEqual(self.store.stats()["total"],2)


if __name__=="__main__": unittest.main()
