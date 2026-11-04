"""Ordering and neighbor regressions use only generated images and temporary catalogs."""
import base64
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import time
import unittest

from PIL import Image

from .store import SORTS, Store


class BrowseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="luma-browse-tests-")
        self.directory = Path(self.temp.name)
        self.source = self.directory/"generated"
        self.source.mkdir()
        self.store = Store(self.directory/"data")
        self.root = self.store.add_root(self.source)
        seed = self.source/"seed.png"
        Image.new("RGB",(24,16),(80,120,160)).save(seed)
        data = seed.read_bytes()
        entries,records = [],[]
        for index in range(281):
            path = self.source/str(index%4)/f"generated-{index}.png"
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(data)
            entry = {"width":24,"height":16,"format":"PNG","family":"Anima" if index%2==0 else "SD",
                     "model":"synthetic-model","tags":["fixture"],"prompt":"generated fixture"}
            entries.append((path,self.root,path.stat(),entry))
            records.append(dict(path=str(path),name=("Picture" if index%2 else "picture")+f"-{index%19:02d}.png",
                created_at=float(index//4),mtime_ns=1000000+index%11,size=100+index%7,
                width=100+index%9,height=80+index%5,rating=index%6,favorite=index%5==0,family=entry["family"]))
        ids = self.store.upsert_many(entries)
        self.records = [dict(id=image_id,**record) for image_id,record in zip(ids,records)]
        with self.store.connect() as db:
            db.executemany("UPDATE images SET name=:name,created_at=:created_at,mtime_ns=:mtime_ns,size=:size,width=:width,height=:height,rating=:rating,favorite=:favorite WHERE id=:id",self.records)

    def tearDown(self):
        self.temp.cleanup()

    def ordered(self,sort,records=None):
        keys = {
            "newest":lambda r:r["created_at"],"oldest":lambda r:r["created_at"],
            "name":lambda r:r["name"].lower(),"name_desc":lambda r:r["name"].lower(),
            "modified_desc":lambda r:r["mtime_ns"],"modified_asc":lambda r:r["mtime_ns"],
            "size_desc":lambda r:r["size"],"size_asc":lambda r:r["size"],
            "resolution_desc":lambda r:r["width"]*r["height"],"resolution_asc":lambda r:r["width"]*r["height"],
            "rating_desc":lambda r:r["rating"],"rating_asc":lambda r:r["rating"],
        }
        reverse = SORTS[sort]["direction"]=="DESC"
        return sorted(records if records is not None else self.records,key=lambda r:(keys[sort](r),r["id"]),reverse=reverse)

    def test_every_sort_pages_across_120_boundary_without_duplicates_or_missing_ties(self):
        self.assertEqual(len(SORTS),12)
        for sort in SORTS:
            with self.subTest(sort=sort):
                expected = [r["id"] for r in self.ordered(sort)]
                found,cursor=[],None
                while True:
                    page = self.store.list_images(sort=sort,cursor=cursor,limit=120)
                    self.assertEqual(page["total"],281)
                    self.assertLessEqual(len(page["items"]),120)
                    found.extend(item["id"] for item in page["items"])
                    cursor = page["next_cursor"]
                    if not cursor:break
                self.assertEqual(found,expected)
                self.assertEqual(len(set(found)),281)

    def test_neighbors_follow_every_sort_across_page_boundary_and_library_ends(self):
        for sort in SORTS:
            expected = [r["id"] for r in self.ordered(sort)]
            for position in (0,119,120,121,280):
                with self.subTest(sort=sort,position=position):
                    result = self.store.browse_neighbors(expected[position],{"sort":sort})
                    self.assertEqual(result["current_id"],expected[position])
                    self.assertEqual(result["previous_id"],expected[position-1] if position else None)
                    self.assertEqual(result["next_id"],expected[position+1] if position<280 else None)
                    self.assertEqual([r["id"] for r in result["items"]],expected[max(0,position-4):position+5])
                    self.assertLessEqual(len(result["items"]),9)
                    self.assertEqual(result["total"],281)

    def test_filtered_neighbors_share_root_model_search_favorite_and_content_filter(self):
        child = self.store.add_root(self.source/"0")
        shared = [r for r in self.records if Path(r["path"]).parent==self.source/"0"]
        self.store.attach_root_images(child,[r["path"] for r in shared])
        tag = self.store.content_tag_definitions()[0]
        tagged = [r for index,r in enumerate(self.records) if index%3==0]
        for row in tagged:
            self.store.set_content_matches(row["id"],[{"tag_id":tag["id"],"score":.8}],source="manual")
        allowed = [r for r in shared if r["favorite"] and r["family"]=="Anima" and r in tagged]
        filters = {"sort":"size_desc","q":"generated","model":"Anima","favorite":True,"root_id":child,"format":"PNG","content_tag":tag["id"]}
        expected = [r["id"] for r in self.ordered("size_desc",allowed)]
        anchor = expected[len(expected)//2]
        result = self.store.browse_neighbors(anchor,filters)
        position = expected.index(anchor)
        self.assertEqual(result["total"],len(expected))
        self.assertEqual([r["id"] for r in result["items"]],expected[max(0,position-4):position+5])
        self.assertTrue(all(row["content_tags"][0]["name"]==tag["name"] for row in result["items"]))
        self.assertTrue(all(row["prompt_tags"]==["fixture"] for row in result["items"]))

    def test_deleted_anchor_and_filter_mismatch_choose_first_matching_image(self):
        expected = self.ordered("name")
        anchor = expected[121]["id"]
        with self.store.connect() as db:db.execute("DELETE FROM images WHERE id=?",(anchor,))
        result = self.store.browse_neighbors(anchor,{"sort":"name"})
        self.assertEqual(result["current_id"],expected[0]["id"])
        self.assertIsNone(result["previous_id"])
        nonfavorite = next(r["id"] for r in self.records if not r["favorite"])
        favorite_expected = self.ordered("rating_asc",[r for r in self.records if r["favorite"] and r["id"]!=anchor])
        result = self.store.browse_neighbors(nonfavorite,{"sort":"rating_asc","favorite":True})
        self.assertEqual(result["current_id"],favorite_expected[0]["id"])
        self.assertEqual(result["total"],len(favorite_expected))
        empty = self.store.browse_neighbors(anchor,{"content_tag":"nonexistent synthetic category"})
        self.assertEqual(empty,{"items":[],"current_id":None,"previous_id":None,"next_id":None,"total":0})
        other = Store(self.directory/"empty")
        self.assertIsNone(other.browse_neighbors(None)["current_id"])

    def test_zero_neighbor_window_still_exposes_adjacent_ids_and_bounded_summary(self):
        ordered = self.ordered("oldest")
        anchor = ordered[120]["id"]
        with self.store.connect() as db:
            db.execute("UPDATE images SET prompt=?,negative_prompt=?,raw_metadata=? WHERE id=?",("x"*8000,"n"*8000,'{"large":"workflow"}',anchor))
        result = self.store.browse_neighbors(anchor,{"sort":"oldest"},before=0,after=0)
        self.assertEqual(len(result["items"]),1)
        self.assertEqual(result["previous_id"],ordered[119]["id"])
        self.assertEqual(result["next_id"],ordered[121]["id"])
        self.assertLessEqual(len(result["items"][0]["prompt"]),2048)
        self.assertLessEqual(len(result["items"][0]["negative_prompt"]),1024)
        self.assertNotIn("raw_metadata",result["items"][0])
        self.assertNotIn("_sort_key",result["items"][0])
        with self.assertRaises(ValueError):self.store.browse_neighbors(anchor,before=5)
        with self.assertRaises(ValueError):self.store.list_images(sort="unsupported")

    def test_stale_cursor_fails_after_data_changes_and_legacy_or_nonfinite_cursor_is_rejected(self):
        page = self.store.list_images(sort="rating_desc",limit=120)
        cursor = page["next_cursor"]
        self.store.patch_image(page["items"][0]["id"],{"rating":4})
        with self.assertRaisesRegex(ValueError,"已过期"):
            self.store.list_images(sort="rating_desc",cursor=cursor)
        cursor = self.store.list_images(sort="rating_desc",limit=120)["next_cursor"]
        payload = json.loads(base64.urlsafe_b64decode(cursor+"="*(-len(cursor)%4)))
        for change in ({"v":1},{"key":float("nan")},{"id":True}):
            changed = {**payload,**change}
            bad = base64.urlsafe_b64encode(json.dumps(changed).encode()).decode().rstrip("=")
            with self.assertRaises(ValueError):self.store.list_images(sort="rating_desc",cursor=bad)
        with self.assertRaises(ValueError):self.store.list_images(sort="size_desc",cursor=cursor)

    def test_plan_and_task_progress_does_not_expire_image_cursor(self):
        cursor = self.store.list_images(limit=120)["next_cursor"]
        with self.store.connect() as db:
            db.execute("INSERT INTO plans(id,kind,destination,created_at) VALUES('synthetic-plan','model',?,?)",(str(self.directory/"never-created"),time.time()))
            db.execute("UPDATE plans SET status='cancelled' WHERE id='synthetic-plan'")
        self.assertEqual(len(self.store.list_images(limit=120,cursor=cursor)["items"]),120)

    def test_neighbor_queries_are_bounded_and_use_order_indexes_without_rank_counts(self):
        original = self.store.connect
        statements=[]
        @contextmanager
        def traced():
            with original() as db:
                db.set_trace_callback(statements.append)
                yield db
        self.store.connect = traced
        anchor = self.records[180]["id"]
        for sort in SORTS:self.store.browse_neighbors(anchor,{"sort":sort})
        reads = [sql for sql in statements if sql.startswith("SELECT") and "FROM images i" in sql]
        self.assertTrue(reads)
        self.assertTrue(all(" LIMIT " in sql for sql in reads),reads)
        self.assertFalse(any("COUNT(" in sql.upper() for sql in reads))
        with original() as db:
            for sql in reads:
                if "ORDER BY" not in sql:continue
                plan = [row[3] for row in db.execute("EXPLAIN QUERY PLAN "+sql)]
                self.assertFalse(any("TEMP B-TREE FOR ORDER BY" in row for row in plan),plan)
                self.assertTrue(any("USING INDEX ix_images_" in row for row in plan),plan)


if __name__=="__main__":
    unittest.main()
