"""Durable, cursor-paginated local image catalog."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path

SORTS = {
    "newest": {"label":"最新添加","column":"i.created_at","direction":"DESC","index":"ix_images_newest","type":"number"},
    "oldest": {"label":"最早添加","column":"i.created_at","direction":"ASC","index":"ix_images_newest","type":"number"},
    "name": {"label":"名称 A–Z","column":"i.name COLLATE NOCASE","direction":"ASC","index":"ix_images_name","type":"text"},
    "name_desc": {"label":"名称 Z–A","column":"i.name COLLATE NOCASE","direction":"DESC","index":"ix_images_name","type":"text"},
    "modified_desc": {"label":"最近修改","column":"i.mtime_ns","direction":"DESC","index":"ix_images_modified","type":"number"},
    "modified_asc": {"label":"最早修改","column":"i.mtime_ns","direction":"ASC","index":"ix_images_modified","type":"number"},
    "size_desc": {"label":"大小从大到小","column":"i.size","direction":"DESC","index":"ix_images_size","type":"number"},
    "size_asc": {"label":"大小从小到大","column":"i.size","direction":"ASC","index":"ix_images_size","type":"number"},
    "resolution_desc": {"label":"分辨率从高到低","column":"(i.width * i.height)","direction":"DESC","index":"ix_images_resolution","type":"number"},
    "resolution_asc": {"label":"分辨率从低到高","column":"(i.width * i.height)","direction":"ASC","index":"ix_images_resolution","type":"number"},
    "rating_desc": {"label":"评分从高到低","column":"i.rating","direction":"DESC","index":"ix_images_rating","type":"number"},
    "rating_asc": {"label":"评分从低到高","column":"i.rating","direction":"ASC","index":"ix_images_rating","type":"number"},
}

SUMMARY_PROJECTION = "i.id,i.path,i.root_id,i.name,i.width,i.height,i.size,i.mtime_ns,i.format,i.model,i.family,(SELECT json_group_array(value) FROM (SELECT value FROM json_each(i.tags) LIMIT 100)) AS tags,SUBSTR(i.prompt,1,2048) AS prompt,SUBSTR(i.negative_prompt,1,1024) AS negative_prompt,'{}' AS parameters,i.artist,i.confidence,i.reason,i.favorite,i.rating,i.created_at,i.indexed_at"


def default_data_dir():
    application = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    return Path(os.environ.get("LUMA_DATA_DIR") or application / "Data" / "Qt")


class Store:
    def __init__(self, directory=None):
        self.directory = Path(directory or default_data_dir())
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory / "library.sqlite3"
        self._count_cache = OrderedDict()
        self._count_lock = threading.Lock()
        self._content_snapshot_key = os.urandom(32)
        self.initialize()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS roots(id INTEGER PRIMARY KEY,path TEXT UNIQUE NOT NULL,name TEXT NOT NULL,created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS images(
              id INTEGER PRIMARY KEY,path TEXT UNIQUE NOT NULL,root_id INTEGER REFERENCES roots(id),name TEXT NOT NULL,
              width INTEGER NOT NULL DEFAULT 0,height INTEGER NOT NULL DEFAULT 0,size INTEGER NOT NULL,mtime_ns INTEGER NOT NULL,
              format TEXT NOT NULL DEFAULT '',model TEXT NOT NULL DEFAULT '',family TEXT NOT NULL DEFAULT 'Unknown',
              tags TEXT NOT NULL DEFAULT '[]',prompt TEXT NOT NULL DEFAULT '',negative_prompt TEXT NOT NULL DEFAULT '',
              parameters TEXT NOT NULL DEFAULT '{}',raw_metadata TEXT NOT NULL DEFAULT '{}',artist TEXT NOT NULL DEFAULT '',
              confidence REAL NOT NULL DEFAULT 0,reason TEXT NOT NULL DEFAULT '',favorite INTEGER NOT NULL DEFAULT 0,
              rating INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,indexed_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS ix_images_newest ON images(created_at,id);
            CREATE INDEX IF NOT EXISTS ix_images_name ON images(name COLLATE NOCASE,id);
            CREATE INDEX IF NOT EXISTS ix_images_model ON images(family,model);
            CREATE INDEX IF NOT EXISTS ix_images_checkpoint ON images(model);
            CREATE INDEX IF NOT EXISTS ix_images_format ON images(format);
            CREATE INDEX IF NOT EXISTS ix_images_root ON images(root_id);
            CREATE INDEX IF NOT EXISTS ix_images_favorite ON images(favorite);
            CREATE TABLE IF NOT EXISTS image_tags(image_id INTEGER REFERENCES images(id) ON DELETE CASCADE,tag TEXT NOT NULL,PRIMARY KEY(image_id,tag));
            CREATE INDEX IF NOT EXISTS ix_tag_lookup ON image_tags(tag,image_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS images_fts USING fts5(name,prompt,tags,artist,model,tokenize='unicode61');
            CREATE TRIGGER IF NOT EXISTS images_fts_insert AFTER INSERT ON images BEGIN
              INSERT INTO images_fts(rowid,name,prompt,tags,artist,model) VALUES(new.id,new.name,new.prompt,new.tags,new.artist,new.model); END;
            CREATE TRIGGER IF NOT EXISTS images_fts_delete AFTER DELETE ON images BEGIN DELETE FROM images_fts WHERE rowid=old.id; END;
            DROP TRIGGER IF EXISTS images_fts_update;
            CREATE TRIGGER images_fts_update AFTER UPDATE OF name,prompt,tags,artist,model ON images BEGIN
              DELETE FROM images_fts WHERE rowid=old.id;
              INSERT INTO images_fts(rowid,name,prompt,tags,artist,model) VALUES(new.id,new.name,new.prompt,new.tags,new.artist,new.model); END;
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,status TEXT NOT NULL,processed INTEGER NOT NULL DEFAULT 0,
              total INTEGER NOT NULL DEFAULT 0,errors INTEGER NOT NULL DEFAULT 0,message TEXT NOT NULL DEFAULT '',payload TEXT NOT NULL DEFAULT '{}',
              cancel_requested INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS rules(id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,match TEXT NOT NULL,destination TEXT NOT NULL,enabled INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS catalog_metrics(id INTEGER PRIMARY KEY CHECK(id=1),total INTEGER NOT NULL,bytes INTEGER NOT NULL,ai INTEGER NOT NULL,unclassified INTEGER NOT NULL,favorites INTEGER NOT NULL,revision INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS catalog_counts(kind TEXT NOT NULL,name TEXT NOT NULL,count INTEGER NOT NULL,PRIMARY KEY(kind,name));
            CREATE INDEX IF NOT EXISTS ix_catalog_count_rank ON catalog_counts(kind,count DESC);
            CREATE TABLE IF NOT EXISTS schema_migrations(name TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,kind TEXT NOT NULL,destination TEXT NOT NULL,total INTEGER NOT NULL DEFAULT 0,
              movable INTEGER NOT NULL DEFAULT 0,skipped INTEGER NOT NULL DEFAULT 0,conflicts INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'ready',created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS plan_items(id INTEGER PRIMARY KEY,plan_id TEXT NOT NULL REFERENCES plans(id),image_id INTEGER NOT NULL,
              name TEXT NOT NULL,source TEXT NOT NULL,destination TEXT NOT NULL,category TEXT NOT NULL,status TEXT NOT NULL,reason TEXT NOT NULL DEFAULT '',
              size INTEGER NOT NULL,mtime_ns INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS ix_plan_items ON plan_items(plan_id,id);
            CREATE INDEX IF NOT EXISTS ix_plan_destination ON plan_items(plan_id,destination);
            CREATE TABLE IF NOT EXISTS thumbnail_cache(key TEXT PRIMARY KEY,path TEXT NOT NULL,bytes INTEGER NOT NULL,accessed_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS ix_thumbnail_lru ON thumbnail_cache(accessed_at);
            CREATE TABLE IF NOT EXISTS cache_metrics(id INTEGER PRIMARY KEY CHECK(id=1),bytes INTEGER NOT NULL);
            INSERT OR IGNORE INTO cache_metrics(id,bytes) SELECT 1,COALESCE(SUM(bytes),0) FROM thumbnail_cache;
            CREATE TRIGGER IF NOT EXISTS thumbnail_count_insert AFTER INSERT ON thumbnail_cache BEGIN UPDATE cache_metrics SET bytes=bytes+new.bytes WHERE id=1; END;
            CREATE TRIGGER IF NOT EXISTS thumbnail_count_delete AFTER DELETE ON thumbnail_cache BEGIN UPDATE cache_metrics SET bytes=MAX(0,bytes-old.bytes) WHERE id=1; END;
            CREATE TRIGGER IF NOT EXISTS thumbnail_count_update AFTER UPDATE OF bytes ON thumbnail_cache BEGIN UPDATE cache_metrics SET bytes=MAX(0,bytes-old.bytes+new.bytes) WHERE id=1; END;
            CREATE TABLE IF NOT EXISTS journal(id INTEGER PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(id),image_id INTEGER NOT NULL,
              source TEXT NOT NULL,destination TEXT NOT NULL,temp_path TEXT NOT NULL,source_root_id INTEGER,
              size INTEGER NOT NULL,mtime_ns INTEGER NOT NULL,state TEXT NOT NULL DEFAULT 'prepared',digest TEXT NOT NULL DEFAULT '',
              error TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,updated_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS ix_journal_job ON journal(job_id,id);
            """)
            # Migrate existing catalogs once; subsequent starts never aggregate the image table.
            if not db.execute("SELECT 1 FROM catalog_metrics WHERE id=1").fetchone():
                db.execute("INSERT INTO catalog_metrics SELECT 1,COUNT(*),COALESCE(SUM(size),0),COALESCE(SUM(prompt<>'' OR model<>''),0),COALESCE(SUM(family IN ('Unknown','','未知')),0),COALESCE(SUM(favorite),0),0 FROM images")
                db.execute("INSERT OR IGNORE INTO catalog_counts SELECT 'model',family,COUNT(*) FROM images GROUP BY family")
                db.execute("INSERT OR IGNORE INTO catalog_counts SELECT 'format',format,COUNT(*) FROM images GROUP BY format")
                db.execute("INSERT OR IGNORE INTO catalog_counts SELECT 'tag',tag,COUNT(*) FROM image_tags GROUP BY tag")
                db.execute("INSERT OR IGNORE INTO catalog_counts SELECT 'root',CAST(COALESCE(root_id,0) AS TEXT),COUNT(*) FROM images GROUP BY root_id")
            if not db.execute("SELECT 1 FROM schema_migrations WHERE name='root-counts-v1'").fetchone():
                db.execute("INSERT OR IGNORE INTO catalog_counts SELECT 'root',CAST(COALESCE(root_id,0) AS TEXT),COUNT(*) FROM images GROUP BY root_id")
                db.execute("INSERT INTO schema_migrations VALUES('root-counts-v1')")
            db.executescript("""
            DROP TRIGGER IF EXISTS catalog_insert;
            DROP TRIGGER IF EXISTS catalog_delete;
            DROP TRIGGER IF EXISTS catalog_update;
            DROP TRIGGER IF EXISTS catalog_tag_insert;
            DROP TRIGGER IF EXISTS catalog_tag_delete;
            CREATE TRIGGER IF NOT EXISTS catalog_insert AFTER INSERT ON images BEGIN
              UPDATE catalog_metrics SET total=total+1,bytes=bytes+new.size,ai=ai+(new.prompt<>'' OR new.model<>''),unclassified=unclassified+(new.family IN ('Unknown','','未知')),favorites=favorites+new.favorite,revision=revision+1 WHERE id=1;
              INSERT INTO catalog_counts VALUES('model',new.family,1) ON CONFLICT(kind,name) DO UPDATE SET count=count+1;
              INSERT INTO catalog_counts VALUES('format',new.format,1) ON CONFLICT(kind,name) DO UPDATE SET count=count+1;
            END;
            CREATE TRIGGER IF NOT EXISTS catalog_delete AFTER DELETE ON images BEGIN
              UPDATE catalog_metrics SET total=total-1,bytes=bytes-old.size,ai=ai-(old.prompt<>'' OR old.model<>''),unclassified=unclassified-(old.family IN ('Unknown','','未知')),favorites=favorites-old.favorite,revision=revision+1 WHERE id=1;
              UPDATE catalog_counts SET count=count-1 WHERE kind='model' AND name=old.family;
              UPDATE catalog_counts SET count=count-1 WHERE kind='format' AND name=old.format;
            END;
            CREATE TRIGGER IF NOT EXISTS catalog_update AFTER UPDATE OF size,prompt,model,family,format,favorite,root_id ON images BEGIN
              UPDATE catalog_metrics SET bytes=bytes+new.size-old.size,ai=ai+(new.prompt<>'' OR new.model<>'')-(old.prompt<>'' OR old.model<>''),unclassified=unclassified+(new.family IN ('Unknown','','未知'))-(old.family IN ('Unknown','','未知')),favorites=favorites+new.favorite-old.favorite,revision=revision+1 WHERE id=1;
              UPDATE catalog_counts SET count=count-1 WHERE kind='model' AND name=old.family;
              INSERT INTO catalog_counts VALUES('model',new.family,1) ON CONFLICT(kind,name) DO UPDATE SET count=count+1;
              UPDATE catalog_counts SET count=count-1 WHERE kind='format' AND name=old.format;
              INSERT INTO catalog_counts VALUES('format',new.format,1) ON CONFLICT(kind,name) DO UPDATE SET count=count+1;
            END;
            CREATE TRIGGER IF NOT EXISTS catalog_tag_insert AFTER INSERT ON image_tags BEGIN
              INSERT INTO catalog_counts VALUES('tag',new.tag,1) ON CONFLICT(kind,name) DO UPDATE SET count=count+1;
              UPDATE catalog_metrics SET revision=revision+1 WHERE id=1;
            END;
            CREATE TRIGGER IF NOT EXISTS catalog_tag_delete AFTER DELETE ON image_tags BEGIN
              UPDATE catalog_counts SET count=count-1 WHERE kind='tag' AND name=old.tag;
              UPDATE catalog_metrics SET revision=revision+1 WHERE id=1;
            END;
            """)
            migrations = {"images":{"companion_signature":"TEXT NOT NULL DEFAULT '[]'"},"plan_items":{"companions":"TEXT NOT NULL DEFAULT '[]'"}, "journal":{"asset_kind":"TEXT NOT NULL DEFAULT 'image'","restore_root_id":"INTEGER","undo_of":"INTEGER"}}
            for table, columns in migrations.items():
                existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
                for column, definition in columns.items():
                    if column not in existing:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            self._initialize_v3(db)
            from .model_references import initialize as initialize_model_references
            initialize_model_references(db)

    def _initialize_v3(self, db):
        db.executescript("""
        CREATE TABLE IF NOT EXISTS content_tags(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE NOT NULL,
          description TEXT NOT NULL,threshold REAL NOT NULL DEFAULT 0.28,enabled INTEGER NOT NULL DEFAULT 1,revision INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS image_content_tags(image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
          tag_id INTEGER NOT NULL REFERENCES content_tags(id) ON DELETE CASCADE,score REAL NOT NULL,source TEXT NOT NULL,
          model_id TEXT NOT NULL DEFAULT '',classified_at REAL NOT NULL,definition_revision INTEGER NOT NULL,
          image_size INTEGER NOT NULL,image_mtime_ns INTEGER NOT NULL,PRIMARY KEY(image_id,tag_id,source));
        CREATE INDEX IF NOT EXISTS ix_content_tag_lookup ON image_content_tags(tag_id,image_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS content_tags_fts USING fts5(name,description,tokenize='unicode61');
        CREATE TRIGGER IF NOT EXISTS content_fts_insert AFTER INSERT ON content_tags BEGIN
          INSERT INTO content_tags_fts(rowid,name,description) VALUES(new.id,new.name,new.description); END;
        CREATE TRIGGER IF NOT EXISTS content_fts_delete AFTER DELETE ON content_tags BEGIN
          DELETE FROM content_tags_fts WHERE rowid=old.id; END;
        CREATE TRIGGER IF NOT EXISTS content_fts_update AFTER UPDATE OF name,description ON content_tags BEGIN
          DELETE FROM content_tags_fts WHERE rowid=old.id;
          INSERT INTO content_tags_fts(rowid,name,description) VALUES(new.id,new.name,new.description); END;
        CREATE TRIGGER IF NOT EXISTS content_count_insert AFTER INSERT ON image_content_tags BEGIN
          INSERT INTO catalog_counts(kind,name,count) SELECT 'content',CAST(new.tag_id AS TEXT),1
            WHERE (SELECT COUNT(*) FROM image_content_tags WHERE image_id=new.image_id AND tag_id=new.tag_id)=1
            ON CONFLICT(kind,name) DO UPDATE SET count=count+1;
          UPDATE catalog_metrics SET revision=revision+1 WHERE id=1; END;
        CREATE TRIGGER IF NOT EXISTS content_count_delete AFTER DELETE ON image_content_tags BEGIN
          UPDATE catalog_counts SET count=MAX(0,count-1) WHERE kind='content' AND name=CAST(old.tag_id AS TEXT)
            AND NOT EXISTS(SELECT 1 FROM image_content_tags WHERE image_id=old.image_id AND tag_id=old.tag_id);
          UPDATE catalog_metrics SET revision=revision+1 WHERE id=1; END;
        CREATE TABLE IF NOT EXISTS image_roots(image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
          root_id INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,provenance TEXT NOT NULL DEFAULT 'observed',PRIMARY KEY(image_id,root_id));
        CREATE INDEX IF NOT EXISTS ix_image_roots_root ON image_roots(root_id,image_id);
        CREATE INDEX IF NOT EXISTS ix_images_path_nocase ON images(path COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS ix_images_modified ON images(mtime_ns,id);
        CREATE INDEX IF NOT EXISTS ix_images_size ON images(size,id);
        CREATE INDEX IF NOT EXISTS ix_images_resolution ON images((width*height),id);
        CREATE INDEX IF NOT EXISTS ix_images_rating ON images(rating,id);
        CREATE TABLE IF NOT EXISTS query_state(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL);
        INSERT OR IGNORE INTO query_state(id,revision) VALUES(1,0);
        CREATE INDEX IF NOT EXISTS ix_journal_image ON journal(image_id,state);
        CREATE INDEX IF NOT EXISTS ix_journal_source_root ON journal(source_root_id,state);
        CREATE INDEX IF NOT EXISTS ix_journal_restore_root ON journal(restore_root_id,state);
        CREATE INDEX IF NOT EXISTS ix_plan_items_image ON plan_items(image_id,plan_id);
        CREATE TABLE IF NOT EXISTS removal_plans(id TEXT PRIMARY KEY,root_id INTEGER NOT NULL,root_path TEXT NOT NULL,
          revision INTEGER NOT NULL,total INTEGER NOT NULL,remove_count INTEGER NOT NULL,shared INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'ready',created_at REAL NOT NULL);
        CREATE TRIGGER IF NOT EXISTS root_membership_insert AFTER INSERT ON image_roots BEGIN
          INSERT INTO catalog_counts VALUES('root',CAST(new.root_id AS TEXT),1) ON CONFLICT(kind,name) DO UPDATE SET count=count+1;
          UPDATE catalog_metrics SET revision=revision+1 WHERE id=1; END;
        CREATE TRIGGER IF NOT EXISTS root_membership_delete AFTER DELETE ON image_roots BEGIN
          UPDATE catalog_counts SET count=MAX(0,count-1) WHERE kind='root' AND name=CAST(old.root_id AS TEXT);
          UPDATE catalog_metrics SET revision=revision+1 WHERE id=1; END;
        CREATE TRIGGER IF NOT EXISTS image_primary_root_insert AFTER INSERT ON images WHEN new.root_id IS NOT NULL BEGIN
          INSERT OR IGNORE INTO image_roots(image_id,root_id) VALUES(new.id,new.root_id); END;
        """)
        # Migration uses only indexed paths; it neither lists nor opens filesystem images.
        if not db.execute("SELECT 1 FROM schema_migrations WHERE name='root-memberships-v3'").fetchone():
            db.execute("DELETE FROM catalog_counts WHERE kind='root'")
            db.execute("INSERT OR IGNORE INTO image_roots(image_id,root_id) SELECT id,root_id FROM images WHERE root_id IS NOT NULL")
            for root in db.execute("SELECT id,path FROM roots").fetchall():
                prefix = root["path"].rstrip("/\\") + os.sep
                pattern = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                db.execute("INSERT OR IGNORE INTO image_roots(image_id,root_id,provenance) SELECT id,?,'inferred' FROM images WHERE path LIKE ? ESCAPE '\\'", (root["id"],pattern))
            db.execute("DELETE FROM catalog_counts WHERE kind='root'")
            db.execute("INSERT INTO catalog_counts SELECT 'root',CAST(root_id AS TEXT),COUNT(*) FROM image_roots GROUP BY root_id")
            db.execute("INSERT INTO schema_migrations VALUES('root-memberships-v3')")
        if not db.execute("SELECT 1 FROM schema_migrations WHERE name='content-tags-v3'").fetchone():
            db.executemany("INSERT INTO content_tags(name,description,threshold) VALUES(?,?,?)", [
                ("风景", "a scenic landscape with mountains, ocean, forest, sky and natural scenery",0.28),
                ("人像", "a portrait of a person with a visible face, upper body or full body",0.28),
            ])
            db.execute("INSERT INTO schema_migrations VALUES('content-tags-v3')")
        for table in ("roots","plans","journal","content_tags"):
            for operation in ("INSERT","UPDATE","DELETE"):
                db.execute(f"CREATE TRIGGER IF NOT EXISTS catalog_revision_{table}_{operation.lower()} AFTER {operation} ON {table} BEGIN UPDATE catalog_metrics SET revision=revision+1 WHERE id=1; END")
        for table in ("images","image_tags","image_roots","image_content_tags","content_tags"):
            for operation in ("INSERT","UPDATE","DELETE"):
                db.execute(f"CREATE TRIGGER IF NOT EXISTS query_revision_{table}_{operation.lower()} AFTER {operation} ON {table} BEGIN UPDATE query_state SET revision=revision+1 WHERE id=1; END")
        self._initialize_content_overrides(db)

    def _initialize_content_overrides(self, db):
        """Keep effective labels indexed; overrides never discard machine evidence."""
        db.executescript("""
        CREATE TABLE IF NOT EXISTS image_content_overrides(
          image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
          tag_id INTEGER NOT NULL REFERENCES content_tags(id) ON DELETE CASCADE,
          action TEXT NOT NULL CHECK(action IN ('include','exclude')),updated_at REAL NOT NULL,
          PRIMARY KEY(image_id,tag_id));
        CREATE INDEX IF NOT EXISTS ix_content_override_tag ON image_content_overrides(tag_id,image_id);
        CREATE TABLE IF NOT EXISTS image_content_identity(
          image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
          token TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1);
        CREATE TRIGGER IF NOT EXISTS content_identity_insert AFTER INSERT ON images BEGIN
          INSERT INTO image_content_identity(image_id,token) VALUES(new.id,lower(hex(randomblob(16)))); END;
        CREATE TRIGGER IF NOT EXISTS content_identity_update AFTER UPDATE OF path,root_id,size,mtime_ns,indexed_at ON images BEGIN
          UPDATE image_content_identity SET revision=revision+1 WHERE image_id=new.id; END;
        CREATE TABLE IF NOT EXISTS image_effective_content_tags(
          image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
          tag_id INTEGER NOT NULL REFERENCES content_tags(id) ON DELETE CASCADE,
          PRIMARY KEY(image_id,tag_id));
        CREATE INDEX IF NOT EXISTS ix_effective_content_tag ON image_effective_content_tags(tag_id,image_id);
        DROP TRIGGER IF EXISTS content_count_insert;
        DROP TRIGGER IF EXISTS content_count_delete;
        CREATE VIEW IF NOT EXISTS effective_content_candidates AS
          SELECT m.image_id,m.tag_id FROM image_content_tags m
          JOIN images i ON i.id=m.image_id JOIN content_tags t ON t.id=m.tag_id
          WHERE t.enabled=1 AND (m.source='manual' OR
            (m.definition_revision=t.revision AND m.image_size=i.size AND m.image_mtime_ns=i.mtime_ns AND m.score>=t.threshold))
          AND NOT EXISTS(SELECT 1 FROM image_content_overrides o WHERE o.image_id=m.image_id AND o.tag_id=m.tag_id AND o.action='exclude')
          UNION
          SELECT o.image_id,o.tag_id FROM image_content_overrides o JOIN content_tags t ON t.id=o.tag_id
          WHERE o.action='include' AND t.enabled=1;
        CREATE TRIGGER IF NOT EXISTS effective_content_insert AFTER INSERT ON image_effective_content_tags BEGIN
          INSERT INTO catalog_counts(kind,name,count) VALUES('content',CAST(new.tag_id AS TEXT),1)
            ON CONFLICT(kind,name) DO UPDATE SET count=count+1;
          UPDATE catalog_metrics SET revision=revision+1 WHERE id=1; END;
        CREATE TRIGGER IF NOT EXISTS effective_content_delete AFTER DELETE ON image_effective_content_tags BEGIN
          UPDATE catalog_counts SET count=MAX(0,count-1) WHERE kind='content' AND name=CAST(old.tag_id AS TEXT);
          UPDATE catalog_metrics SET revision=revision+1 WHERE id=1; END;
        """)
        # The one-time migration reads SQL identities/associations only. Later starts
        # do not enumerate the catalog or recompute its effective-label counts.
        if not db.execute("SELECT 1 FROM schema_migrations WHERE name='content-overrides-v1'").fetchone():
            db.execute("INSERT OR IGNORE INTO image_content_identity(image_id,token) SELECT id,lower(hex(randomblob(16))) FROM images")
            db.execute("DELETE FROM catalog_counts WHERE kind='content'")
            db.execute("INSERT OR IGNORE INTO image_effective_content_tags SELECT image_id,tag_id FROM effective_content_candidates")
            db.execute("INSERT INTO schema_migrations VALUES('content-overrides-v1')")
        for table in ("image_content_tags", "image_content_overrides"):
            for operation in ("INSERT", "UPDATE", "DELETE"):
                refs = ("old", "new") if operation=="UPDATE" else ("old",) if operation=="DELETE" else ("new",)
                refresh = "".join(f"DELETE FROM image_effective_content_tags WHERE image_id={ref}.image_id AND tag_id={ref}.tag_id; INSERT OR IGNORE INTO image_effective_content_tags SELECT image_id,tag_id FROM effective_content_candidates WHERE image_id={ref}.image_id AND tag_id={ref}.tag_id;" for ref in refs)
                db.execute(f"CREATE TRIGGER IF NOT EXISTS effective_refresh_{table}_{operation.lower()} AFTER {operation} ON {table} BEGIN {refresh} END")
                image_scope = ",".join(f"{ref}.image_id" for ref in refs)
                db.execute(f"CREATE TRIGGER IF NOT EXISTS content_plan_stale_{table}_{operation.lower()} AFTER {operation} ON {table} BEGIN UPDATE plans SET status='stale' WHERE kind='content_tag' AND status='ready' AND id IN (SELECT plan_id FROM plan_items WHERE image_id IN ({image_scope})); END")
                db.execute(f"CREATE TRIGGER IF NOT EXISTS content_identity_{table}_{operation.lower()} AFTER {operation} ON {table} BEGIN UPDATE image_content_identity SET revision=revision+1 WHERE image_id IN ({image_scope}); END")
        db.executescript("""
        CREATE TRIGGER IF NOT EXISTS effective_refresh_image AFTER UPDATE OF size,mtime_ns ON images BEGIN
          DELETE FROM image_effective_content_tags WHERE image_id=new.id;
          INSERT OR IGNORE INTO image_effective_content_tags SELECT image_id,tag_id FROM effective_content_candidates WHERE image_id=new.id; END;
        CREATE TRIGGER IF NOT EXISTS effective_refresh_definition AFTER UPDATE OF enabled,revision,threshold ON content_tags BEGIN
          DELETE FROM image_effective_content_tags WHERE tag_id=new.id;
          INSERT OR IGNORE INTO image_effective_content_tags SELECT image_id,tag_id FROM effective_content_candidates WHERE tag_id=new.id; END;
        CREATE TRIGGER IF NOT EXISTS content_plan_stale_image AFTER UPDATE OF size,mtime_ns,indexed_at ON images BEGIN
          UPDATE plans SET status='stale' WHERE kind='content_tag' AND status='ready' AND id IN
            (SELECT plan_id FROM plan_items WHERE image_id=new.id); END;
        CREATE TRIGGER IF NOT EXISTS content_plan_stale_definition_update AFTER UPDATE ON content_tags BEGIN
          UPDATE plans SET status='stale' WHERE kind='content_tag' AND status='ready'; END;
        CREATE TRIGGER IF NOT EXISTS content_plan_stale_definition_delete AFTER DELETE ON content_tags BEGIN
          UPDATE plans SET status='stale' WHERE kind='content_tag' AND status='ready'; END;
        """)
        for operation in ("INSERT", "UPDATE", "DELETE"):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS query_revision_content_override_{operation.lower()} AFTER {operation} ON image_content_overrides BEGIN UPDATE query_state SET revision=revision+1 WHERE id=1; UPDATE catalog_metrics SET revision=revision+1 WHERE id=1; END")

    @staticmethod
    def image(row):
        result = dict(row)
        for key, fallback in (("tags", []), ("parameters", {}), ("raw_metadata", {})):
            if key not in result:
                continue
            try:
                result[key] = json.loads(result[key])
            except (TypeError, ValueError):
                result[key] = fallback
        result["favorite"] = bool(result["favorite"])
        result["prompt_tags"] = list(result.get("tags", []))
        return result

    def get_image(self, image_id):
        with self.connect() as db:
            row = db.execute("SELECT i.*,x.token AS image_identity FROM images i LEFT JOIN image_content_identity x ON x.image_id=i.id WHERE i.id=?", (image_id,)).fetchone()
            if not row:
                return None
            result = self.image(row)
            result["content_tags"] = self._content_matches(db,[image_id]).get(image_id,[])
            return result

    def _content_matches(self, db, image_ids):
        result = {}
        if not image_ids:
            return result
        marks = ",".join("?" for _ in image_ids)
        rows = db.execute("""SELECT * FROM (SELECT m.image_id,t.id,t.name,CASE WHEN m.source='manual' THEN NULL ELSE m.score END AS score,m.source,
          CASE WHEN m.source='manual' THEN '' ELSE m.model_id END AS model_id,m.classified_at,
          m.definition_revision,t.revision,t.enabled,t.threshold,
          (m.source<>'manual' AND (m.definition_revision<>t.revision OR m.image_size<>i.size OR m.image_mtime_ns<>i.mtime_ns)) AS stale
          FROM image_content_tags m JOIN content_tags t ON t.id=m.tag_id JOIN images i ON i.id=m.image_id
          WHERE m.image_id IN (""" + marks + """) AND NOT EXISTS(
            SELECT 1 FROM image_content_overrides o WHERE o.image_id=m.image_id AND o.tag_id=m.tag_id)
          UNION ALL SELECT o.image_id,t.id,t.name,NULL,'manual','',o.updated_at,t.revision,t.revision,t.enabled,t.threshold,0
          FROM image_content_overrides o JOIN content_tags t ON t.id=o.tag_id
          WHERE o.action='include' AND o.image_id IN (""" + marks + ")) ORDER BY image_id,source='manual' DESC,score DESC,name COLLATE NOCASE,id,source",[*image_ids,*image_ids])
        for row in rows:
            item = dict(row); image_id = item.pop("image_id")
            item["stale"],item["enabled"] = bool(item["stale"]),bool(item["enabled"])
            result.setdefault(image_id,[]).append(item)
        return result

    @staticmethod
    def _content_label_ids(image_ids):
        if not isinstance(image_ids,list) or not 1<=len(image_ids)<=120:
            raise ValueError("请选择当前页或查看器中的 1 至 120 张图片")
        if any(isinstance(value,bool) or not isinstance(value,int) or not 1<=value<=9223372036854775807 for value in image_ids):
            raise ValueError("图片 ID 无效")
        return sorted(set(image_ids))

    def _content_label_state(self, db, image_ids):
        marks = ",".join("?" for _ in image_ids)
        # Deliberate projections: no image paths, prompts, raw JSON or pixels.
        identities = [tuple(row) for row in db.execute("SELECT i.id,x.token,x.revision FROM images i JOIN image_content_identity x ON x.image_id=i.id WHERE i.id IN ("+marks+") ORDER BY i.id",image_ids)]
        if len(identities)!=len(image_ids):
            raise ValueError("部分图片已不在索引中；请刷新后重新选择")
        definitions = [dict(row) for row in db.execute("SELECT id,name,enabled,revision,threshold FROM content_tags ORDER BY id LIMIT 129")]
        if len(definitions)>128:
            raise ValueError("内容分类最多允许 128 项，请先调整类别定义")
        results = [tuple(row) for row in db.execute("SELECT image_id,tag_id,source,score,classified_at,definition_revision,image_size,image_mtime_ns FROM image_content_tags WHERE image_id IN ("+marks+") ORDER BY image_id,tag_id,source LIMIT 30721",image_ids)]
        if len(results)>30720:
            raise ValueError("内容分类记录超过安全范围；请检查索引")
        overrides = [tuple(row) for row in db.execute("SELECT image_id,tag_id,action,updated_at FROM image_content_overrides WHERE image_id IN ("+marks+") ORDER BY image_id,tag_id",image_ids)]
        encoded = json.dumps([identities,definitions,results,overrides],ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()
        token = hmac.new(self._content_snapshot_key,encoded,hashlib.sha256).hexdigest()
        rows = db.execute("""SELECT t.id,
          SUM(EXISTS(SELECT 1 FROM image_content_overrides o WHERE o.image_id=i.id AND o.tag_id=t.id AND o.action='include') OR
            (NOT EXISTS(SELECT 1 FROM image_content_overrides o WHERE o.image_id=i.id AND o.tag_id=t.id) AND
             EXISTS(SELECT 1 FROM image_content_tags m WHERE m.image_id=i.id AND m.tag_id=t.id AND m.source='manual'))) AS manual_count,
          SUM(EXISTS(SELECT 1 FROM image_content_overrides o WHERE o.image_id=i.id AND o.tag_id=t.id AND o.action='exclude')) AS excluded_count,
          SUM(EXISTS(SELECT 1 FROM image_content_tags m WHERE m.image_id=i.id AND m.tag_id=t.id AND m.source<>'manual'
            AND t.enabled=1 AND m.definition_revision=t.revision AND m.image_size=i.size AND m.image_mtime_ns=i.mtime_ns AND m.score>=t.threshold)) AS machine_count,
          SUM(EXISTS(SELECT 1 FROM image_effective_content_tags e WHERE e.image_id=i.id AND e.tag_id=t.id)) AS effective_count
          FROM content_tags t CROSS JOIN images i WHERE i.id IN ("""+marks+") GROUP BY t.id ORDER BY t.id",image_ids)
        counts = {row["id"]:dict(row) for row in rows}
        tags=[]
        for definition in definitions:
            item={key:definition[key] for key in ("id","name","enabled","revision")}
            item["enabled"]=bool(item["enabled"])
            item.update({key:counts[item["id"]][key] for key in ("manual_count","excluded_count","machine_count","effective_count")})
            item["auto_count"]=len(image_ids)-item["manual_count"]-item["excluded_count"]
            tags.append(item)
        return {"image_count":len(image_ids),"image_ids":list(image_ids),"snapshot_token":token,"tags":tags}

    def content_label_state(self, image_ids):
        image_ids=self._content_label_ids(image_ids)
        with self.connect() as db:
            db.execute("BEGIN")
            return self._content_label_state(db,image_ids)

    def apply_content_label_overrides(self, image_ids, changes, expected_snapshot):
        """Atomic explicit edits over a bounded index selection; auto preserves evidence."""
        image_ids=self._content_label_ids(image_ids)
        if not isinstance(changes,list) or not 1<=len(changes)<=128:
            raise ValueError("请指定 1 至 128 项内容 TAG 校正")
        normalized={}
        for change in changes:
            if not isinstance(change,dict) or set(change)!={"tag_id","action"}:
                raise ValueError("内容 TAG 校正格式无效")
            tag_id,action=change["tag_id"],change["action"]
            if isinstance(tag_id,bool) or not isinstance(tag_id,int) or not 1<=tag_id<=9223372036854775807 or action not in ("include","exclude","auto"):
                raise ValueError("内容 TAG 校正选项无效")
            if tag_id in normalized and normalized[tag_id]!=action:
                raise ValueError("同一内容 TAG 的校正操作冲突")
            normalized[tag_id]=action
        if not isinstance(expected_snapshot,str) or len(expected_snapshot)!=64 or any(c not in "0123456789abcdef" for c in expected_snapshot):
            raise ValueError("内容 TAG 校正预览无效；请重新加载")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state=self._content_label_state(db,image_ids)
            if not hmac.compare_digest(state["snapshot_token"],expected_snapshot):
                raise ValueError("图片或内容 TAG 已改变；请重新加载后校正")
            definitions={tag["id"]:tag for tag in state["tags"]}
            for tag_id,action in normalized.items():
                if tag_id not in definitions: raise ValueError("内容分类已移除；请重新加载")
                if action=="include" and not definitions[tag_id]["enabled"]:
                    raise ValueError("已停用的内容分类不能新增人工确认；请先启用类别")
            changed_images=set();changed_overrides=0;now=time.time()
            for image_id in image_ids:
                for tag_id,action in sorted(normalized.items()):
                    override=db.execute("SELECT action FROM image_content_overrides WHERE image_id=? AND tag_id=?",(image_id,tag_id)).fetchone()
                    legacy=db.execute("SELECT 1 FROM image_content_tags WHERE image_id=? AND tag_id=? AND source='manual'",(image_id,tag_id)).fetchone()
                    if action=="auto":
                        changed=bool(override or legacy)
                        db.execute("DELETE FROM image_content_overrides WHERE image_id=? AND tag_id=?",(image_id,tag_id))
                    else:
                        changed=not override or override["action"]!=action or bool(legacy)
                        if changed:
                            db.execute("INSERT INTO image_content_overrides(image_id,tag_id,action,updated_at) VALUES(?,?,?,?) ON CONFLICT(image_id,tag_id) DO UPDATE SET action=excluded.action,updated_at=excluded.updated_at",(image_id,tag_id,action,now))
                    if legacy:
                        db.execute("DELETE FROM image_content_tags WHERE image_id=? AND tag_id=? AND source='manual'",(image_id,tag_id))
                    if changed:
                        changed_images.add(image_id);changed_overrides+=1
            if changed_images:
                marks=",".join("?" for _ in changed_images)
                db.execute("UPDATE plans SET status='stale' WHERE kind='content_tag' AND status='ready' AND id IN (SELECT plan_id FROM plan_items WHERE image_id IN ("+marks+"))",sorted(changed_images))
            current=self._content_label_state(db,image_ids)
            return {"image_count":len(image_ids),"changed_image_count":len(changed_images),"changed_override_count":changed_overrides,
                    "snapshot_token":current["snapshot_token"],"message":f"已保存 {len(changed_images)} 张图片的内容 TAG 校正"}

    def content_tag_definitions(self):
        with self.connect() as db:
            return [{**dict(row),"enabled":bool(row["enabled"])} for row in db.execute("SELECT * FROM content_tags ORDER BY id")]

    def replace_content_tags(self, definitions):
        if not isinstance(definitions,list) or len(definitions)>128:
            raise ValueError("内容分类最多允许 128 项")
        validated,names,ids = [],set(),set()
        for item in definitions:
            if not isinstance(item,dict):
                raise ValueError("内容分类定义无效")
            name,description = item.get("name",""),item.get("description","")
            if not isinstance(name,str) or not name.strip() or len(name)>100:
                raise ValueError("内容分类名称应为 1 至 100 字符")
            if not isinstance(description,str) or len(description.strip())<5 or len(description)>500:
                raise ValueError("请填写内容分类描述，5 至 500 字符")
            name,description = name.strip(),description.strip()
            if name.casefold() in names:
                raise ValueError("内容分类名称不能重复")
            names.add(name.casefold())
            tag_id = item.get("id")
            if tag_id is not None and (isinstance(tag_id,bool) or not isinstance(tag_id,int) or tag_id<1 or tag_id in ids):
                raise ValueError("内容分类 ID 无效或重复")
            if tag_id is not None: ids.add(tag_id)
            threshold = item.get("threshold",0.28)
            if isinstance(threshold,bool) or not isinstance(threshold,(float,int)) or not math.isfinite(threshold) or not 0.20<=threshold<=0.90:
                raise ValueError("相似度阈值应在 0.20 至 0.90 之间")
            enabled = item.get("enabled",True)
            if not isinstance(enabled,bool): raise ValueError("内容分类启用状态无效")
            validated.append((tag_id,name,description,float(threshold),int(enabled)))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            retained = []
            for tag_id,name,description,threshold,enabled in validated:
                existing = db.execute("SELECT * FROM content_tags WHERE id=?",(tag_id,)).fetchone() if tag_id else db.execute("SELECT * FROM content_tags WHERE name=?",(name,)).fetchone()
                if tag_id and not existing: raise ValueError("内容分类 ID 不存在")
                if existing:
                    tag_id = existing["id"]
                    changed = (existing["name"],existing["description"],existing["threshold"],existing["enabled"]) != (name,description,threshold,enabled)
                    if changed:
                        db.execute("DELETE FROM image_content_tags WHERE tag_id=? AND source<>'manual'",(tag_id,))
                        db.execute("UPDATE content_tags SET name=?,description=?,threshold=?,enabled=?,revision=revision+1 WHERE id=?",(name,description,threshold,enabled,tag_id))
                else:
                    tag_id = db.execute("INSERT INTO content_tags(name,description,threshold,enabled) VALUES(?,?,?,?)",(name,description,threshold,enabled)).lastrowid
                retained.append(tag_id)
            if retained:
                db.execute("DELETE FROM content_tags WHERE id NOT IN ("+",".join("?" for _ in retained)+")",retained)
            else: db.execute("DELETE FROM content_tags")
        return self.content_tag_definitions()

    def set_content_matches(self, image_id, matches, *, source="vision", model_id="", image_signature=None):
        if not isinstance(matches,list) or len(matches)>200 or not isinstance(source,str) or not source or len(source)>128:
            raise ValueError("内容识别结果无效")
        if not isinstance(model_id,str) or len(model_id)>1024: raise ValueError("内容识别模型标识无效")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            image = db.execute("SELECT id,size,mtime_ns FROM images WHERE id=?",(image_id,)).fetchone()
            if not image: raise ValueError("图片不在索引中")
            if image_signature is not None and tuple(image_signature)!=(image["size"],image["mtime_ns"]):
                raise ValueError("识别期间图片已重新索引；结果未写入")
            rows,seen = [],set()
            for match in matches:
                if not isinstance(match,dict): raise ValueError("内容识别结果无效")
                tag_id,score = match.get("tag_id",match.get("id")),match.get("score")
                if isinstance(tag_id,bool) or not isinstance(tag_id,int) or tag_id<1 or tag_id in seen: raise ValueError("内容分类 ID 无效或重复")
                seen.add(tag_id)
                if isinstance(score,bool) or not isinstance(score,(float,int)) or not math.isfinite(score) or not -1<=score<=1: raise ValueError("内容相似度无效")
                definition = db.execute("SELECT * FROM content_tags WHERE id=?",(tag_id,)).fetchone()
                if not definition: raise ValueError("内容分类定义不存在")
                if match.get("definition_revision",definition["revision"]) != definition["revision"]: raise ValueError("内容分类描述已改变；请重新识别")
                evidence_source,evidence_model = match.get("source",source),match.get("model_id",model_id)
                if not isinstance(evidence_source,str) or not evidence_source or len(evidence_source)>128 or (evidence_source=="manual")!=(source=="manual"):
                    raise ValueError("人工标签与模型识别结果应分别保存")
                if not isinstance(evidence_model,str) or len(evidence_model)>1024: raise ValueError("识别模型标识无效")
                if definition["enabled"] and (source=="manual" or score>=definition["threshold"]):
                    rows.append((image_id,tag_id,float(score),evidence_source,evidence_model,time.time(),definition["revision"],image["size"],image["mtime_ns"]))
            condition = "source='manual'" if source=="manual" else "source<>'manual'"
            db.execute("DELETE FROM image_content_tags WHERE image_id=? AND "+condition,(image_id,))
            db.executemany("INSERT INTO image_content_tags(image_id,tag_id,score,source,model_id,classified_at,definition_revision,image_size,image_mtime_ns) VALUES(?,?,?,?,?,?,?,?,?)",rows)
        return self.get_image(image_id)

    def add_root(self, path):
        path = str(Path(path).resolve())
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO roots(path,name,created_at) VALUES(?,?,?)", (path, Path(path).name or path, time.time()))
            return db.execute("SELECT id FROM roots WHERE path=?", (path,)).fetchone()[0]

    def attach_root_images(self, root_id, paths):
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM roots WHERE id=?",(root_id,)).fetchone(): raise ValueError("导入目录不存在")
            attached = 0
            for offset in range(0,len(paths),250):
                batch = [str(path) for path in paths[offset:offset+250]]
                if batch:
                    cursor = db.execute("INSERT OR IGNORE INTO image_roots(image_id,root_id) SELECT id,? FROM images WHERE path IN ("+",".join("?" for _ in batch)+")",[root_id,*batch])
                    attached += cursor.rowcount
            return attached

    attach_root = attach_root_images

    @staticmethod
    def _contains(root, path):
        root,path = os.path.normcase(os.path.normpath(str(root))),os.path.normcase(os.path.normpath(str(path)))
        return path.startswith(root.rstrip("/\\")+os.sep)

    def reassociate_image(self, db, image_id, path, primary_root_id):
        db.execute("DELETE FROM image_roots WHERE image_id=?",(image_id,))
        ids = {row["id"] for row in db.execute("SELECT id,path FROM roots") if self._contains(row["path"],path)}
        if primary_root_id is not None: ids.add(primary_root_id)
        db.executemany("INSERT OR IGNORE INTO image_roots(image_id,root_id) VALUES(?,?)",((image_id,root_id) for root_id in ids))

    def roots(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT r.*,COALESCE(c.count,0) AS count FROM roots r LEFT JOIN catalog_counts c ON c.kind='root' AND c.name=CAST(r.id AS TEXT) ORDER BY r.id")]

    def unchanged(self, path, stat):
        with self.connect() as db:
            row = db.execute("SELECT size,mtime_ns,companion_signature FROM images WHERE path=?", (str(path),)).fetchone()
            return self.same_signature(path,stat,dict(row) if row else None)

    @staticmethod
    def sidecar_signatures(path):
        path = Path(path)
        companions = []
        candidates = [path.with_suffix(extension) for extension in (".txt",".json")] + [Path(str(path)+extension) for extension in (".txt",".json")]
        for sidecar in dict.fromkeys(candidates):
            try:
                info = sidecar.lstat()
                if sidecar.is_file() or sidecar.is_symlink():
                    companions.append({"name":sidecar.name,"size":info.st_size,"mtime_ns":info.st_mtime_ns,"symlink":sidecar.is_symlink()})
            except OSError:
                continue
        return companions

    def same_signature(self,path,info,saved):
        return bool(saved and (saved["size"],saved["mtime_ns"]) == (info.st_size,info.st_mtime_ns) and json.loads(saved["companion_signature"]) == self.sidecar_signatures(path))

    def upsert(self, path, root_id, stat, metadata):
        with self.connect() as db:
            return self._upsert(db, path, root_id, stat, metadata)

    def signatures(self, paths):
        if not paths:
            return {}
        with self.connect() as db:
            rows = db.execute("SELECT path,size,mtime_ns,companion_signature FROM images WHERE path IN (" + ",".join("?" for _ in paths) + ")", paths)
            return {row["path"]: dict(row) for row in rows}

    def upsert_many(self, entries):
        with self.connect() as db:
            return [self._upsert(db, *entry) for entry in entries]

    def _upsert(self, db, path, root_id, stat, metadata, *, read_sidecars=True):
        now = time.time()
        tags = list(dict.fromkeys(str(tag).strip() for tag in metadata.get("tags", []) if str(tag).strip()))[:5000]
        values = {
            "path": str(path), "root_id": root_id, "name": Path(path).name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "width": metadata.get("width", 0), "height": metadata.get("height", 0), "format": str(metadata.get("format", Path(path).suffix[1:])).upper(),
            "model": metadata.get("model", ""), "family": metadata.get("family") or "未知", "tags": json.dumps(tags, ensure_ascii=False),
            "prompt": metadata.get("prompt", ""), "negative_prompt": metadata.get("negative_prompt", ""),
            "parameters": json.dumps(metadata.get("parameters", {}), ensure_ascii=False, default=str),
            "raw_metadata": json.dumps(metadata.get("raw_metadata", {}), ensure_ascii=False, default=str), "artist": metadata.get("artist", ""),
            "confidence": float(metadata.get("confidence", 0)), "reason": metadata.get("reason", ""), "created_at": now, "indexed_at": now,
            "companion_signature":json.dumps(self.sidecar_signatures(path) if read_sidecars else {}),
        }
        columns = list(values)
        updates = ",".join(f"{column}=excluded.{column}" for column in columns if column not in ("path", "created_at"))
        db.execute(f"INSERT INTO images({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) ON CONFLICT(path) DO UPDATE SET {updates}", list(values.values()))
        image_id = db.execute("SELECT id FROM images WHERE path=?", (str(path),)).fetchone()[0]
        if root_id is not None:
            db.execute("INSERT OR IGNORE INTO image_roots(image_id,root_id) VALUES(?,?)",(image_id,root_id))
        db.execute("DELETE FROM image_tags WHERE image_id=?", (image_id,))
        db.executemany("INSERT OR IGNORE INTO image_tags(image_id,tag) VALUES(?,?)", ((image_id, tag) for tag in tags))
        from .model_references import update_image_references
        update_image_references(db,image_id,metadata)
        return image_id

    def patch_image(self, image_id, data):
        allowed = {key: value for key, value in data.items() if key in ("favorite", "rating", "tags")}
        with self.connect() as db:
            if not db.execute("SELECT id FROM images WHERE id=?", (image_id,)).fetchone():
                return None
            if "tags" in allowed:
                tags = list(dict.fromkeys(tag.strip() for tag in allowed["tags"] if tag.strip()))
                allowed["tags"] = json.dumps(tags, ensure_ascii=False)
                db.execute("DELETE FROM image_tags WHERE image_id=?", (image_id,))
                db.executemany("INSERT INTO image_tags(image_id,tag) VALUES(?,?)", ((image_id, tag) for tag in tags))
            if allowed:
                db.execute("UPDATE images SET " + ",".join(f"{key}=?" for key in allowed) + " WHERE id=?", [*allowed.values(), image_id])
        return self.get_image(image_id)

    def _query_info(self, db, filters, limit=120):
        allowed = {"q","model","artist","tag","content_tag","favorite","format","sort","root_id","model_file_id","model_match"}
        if any(key not in allowed for key in filters):
            raise ValueError("图片筛选项无效")
        sort = filters.get("sort","newest")
        if not isinstance(sort,str) or sort not in SORTS:
            raise ValueError("图片排序方式无效")
        spec = SORTS[sort]
        q,model,tag = (filters.get(key,"") for key in ("q","model","tag"))
        artist=filters.get('artist','')
        if not isinstance(artist,str) or len(artist)>1000:raise ValueError('画师筛选无效')
        content_tag,favorite,format,root_id = (filters.get(key) for key in ("content_tag","favorite","format","root_id"))
        if any(not isinstance(value,str) for value in (q,model,tag)) or (format is not None and not isinstance(format,str)):
            raise ValueError("图片文本筛选无效")
        if favorite is not None and not isinstance(favorite,bool):
            raise ValueError("收藏筛选无效")
        if root_id is not None and (isinstance(root_id,bool) or not isinstance(root_id,int) or root_id<1):
            raise ValueError("目录筛选无效")
        if content_tag is not None and (isinstance(content_tag,bool) or not isinstance(content_tag,(str,int))):
            raise ValueError("内容分类筛选无效")
        clauses,args = [],[]
        model_file_id=filters.get('model_file_id')
        model_match=filters.get('model_match','verified')
        if model_match not in ('verified','name') or (model_file_id is not None and (type(model_file_id) is not int or model_file_id<1)):
            raise ValueError('模型关联筛选无效')
        if model_file_id is not None:
            from .model_references import match_clause
            clauses.append(match_clause(model_match)); args.append(model_file_id)
        if q.strip():
            terms = re.findall(r"[^\W_]+",q,re.UNICODE)[:30]
            if terms:
                clauses.append("i.id IN (SELECT rowid FROM images_fts WHERE images_fts MATCH ? UNION SELECT m.image_id FROM image_effective_content_tags m WHERE m.tag_id IN (SELECT rowid FROM content_tags_fts WHERE content_tags_fts MATCH ?))")
                expression = " AND ".join('"'+word.replace('"','""')+'"*' for word in terms)
                args.extend([expression,expression])
        if model:
            clauses.append("(i.model=? OR i.family=?)");args.extend([model,model])
        if artist:
            clauses.append('i.artist=?');args.append(artist)
        if tag:
            clauses.append("i.id IN (SELECT image_id FROM image_tags WHERE tag=?)");args.append(tag)
        content_id = None
        if content_tag is not None and content_tag!="":
            column = "id" if isinstance(content_tag,int) and not isinstance(content_tag,bool) else "name COLLATE NOCASE"
            definition = db.execute(f"SELECT id FROM content_tags WHERE enabled=1 AND {column}=?",(content_tag,)).fetchone()
            content_id = definition[0] if definition else -1
            clauses.append("i.id IN (SELECT image_id FROM image_effective_content_tags WHERE tag_id=?)");args.append(content_id)
        if favorite is not None:
            clauses.append("i.favorite=?");args.append(int(favorite))
        if format:
            clauses.append("i.format=?");args.append(format.upper())
        if root_id is not None:
            clauses.append("i.id IN (SELECT image_id FROM image_roots WHERE root_id=?)");args.append(root_id)
        fingerprint = hashlib.sha256(json.dumps([clauses,args,sort],ensure_ascii=False).encode()).hexdigest()[:16]
        count_key = hashlib.sha256(json.dumps([clauses,args],ensure_ascii=False).encode()).hexdigest()[:16]
        metrics = db.execute("SELECT m.total,m.favorites,q.revision FROM catalog_metrics m JOIN query_state q ON q.id=m.id WHERE m.id=1").fetchone()
        with self._count_lock:
            saved_count = self._count_cache.get(count_key)
        if not clauses:
            total = metrics["total"]
        elif saved_count and saved_count[0]==metrics["revision"]:
            total = saved_count[1]
        elif len(clauses)==1 and (tag or content_id is not None or root_id is not None or format):
            kind,name = ("tag",tag) if tag else ("content",str(content_id)) if content_id is not None else ("root",str(root_id)) if root_id is not None else ("format",format.upper())
            row = db.execute("SELECT count FROM catalog_counts WHERE kind=? AND name=?",(kind,name)).fetchone()
            total = row[0] if row else 0
        elif len(clauses)==1 and favorite is not None:
            total = metrics["favorites"] if favorite else metrics["total"]-metrics["favorites"]
        else:
            total = db.execute("SELECT COUNT(*) FROM images i WHERE "+" AND ".join(clauses),args).fetchone()[0]
        with self._count_lock:
            self._count_cache[count_key] = (metrics["revision"],total)
            self._count_cache.move_to_end(count_key)
            while len(self._count_cache)>128:self._count_cache.popitem(last=False)
        use_ordered = not clauses or total>=max(limit*4,metrics["total"]//100)
        if use_ordered:
            replacements = {
                "i.id IN (SELECT image_id FROM image_tags WHERE tag=?)":"EXISTS(SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND t.tag=?)",
                "i.id IN (SELECT image_id FROM image_effective_content_tags WHERE tag_id=?)":"EXISTS(SELECT 1 FROM image_effective_content_tags t WHERE t.image_id=i.id AND t.tag_id=?)",
                "i.id IN (SELECT image_id FROM image_roots WHERE root_id=?)":"EXISTS(SELECT 1 FROM image_roots r WHERE r.image_id=i.id AND r.root_id=?)",
            }
            clauses = [replacements.get(clause,clause) for clause in clauses]
        return {"spec":spec,"clauses":clauses,"args":args,"fingerprint":fingerprint,
                "total":total,"revision":metrics["revision"],"index":spec["index"] if use_ordered else None}

    def _query_rows(self, db, info, *, limit, anchor=None, before=False):
        spec = info["spec"]
        direction = spec["direction"]
        clauses,args = list(info["clauses"]),list(info["args"])
        if anchor is not None:
            # Previous rows are selected in reverse order, then reversed into display order.
            descending = direction=="DESC"
            op = ">" if descending==before else "<"
            clauses.append(f"({spec['column']},i.id) {op} (?,?)")
            args.extend(anchor)
        if before:direction = "ASC" if direction=="DESC" else "DESC"
        where = " WHERE "+" AND ".join(clauses) if clauses else ""
        hint = " INDEXED BY "+info["index"] if info["index"] else ""
        sql = f"SELECT {SUMMARY_PROJECTION},{spec['column']} AS _sort_key FROM images i{hint}{where} ORDER BY {spec['column']} {direction},i.id {direction} LIMIT ?"
        return list(db.execute(sql,[*args,limit]))

    def _query_summaries(self, db, rows):
        content = self._content_matches(db,[row["id"] for row in rows])
        results=[]
        for row in rows:
            result = self.image(row)
            result.pop("_sort_key",None)
            result["content_tags"] = content.get(row["id"],[])
            results.append(result)
        return results

    def list_images(self, *, cursor=None, limit=120, q="", model="", tag="", content_tag=None, favorite=None, format="", sort="newest", root_id=None, model_file_id=None, model_match='verified', artist=""):
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=500:
            raise ValueError("分页数量应在 1 至 500 之间")
        filters = dict(q=q,model=model,artist=artist,tag=tag,content_tag=content_tag,favorite=favorite,format=format,sort=sort,root_id=root_id,model_file_id=model_file_id,model_match=model_match)
        with self.connect() as db:
            db.execute("BEGIN")
            info = self._query_info(db,filters,limit)
            anchor = None
            if cursor:
                try:
                    if not isinstance(cursor,str) or len(cursor)>8192:raise ValueError("Invalid cursor encoding")
                    decoded = json.loads(base64.urlsafe_b64decode(cursor+"="*(-len(cursor)%4)))
                    if not isinstance(decoded,dict):raise ValueError("Invalid cursor payload")
                    if decoded.get("v")!=2 or decoded.get("revision")!=info["revision"]:
                        raise ValueError("pagination cursor 已过期，请刷新")
                    if decoded["f"]!=info["fingerprint"]:raise ValueError("Cursor does not match current filters")
                    if isinstance(decoded["id"],bool) or not isinstance(decoded["id"],int) or decoded["id"]<1:raise ValueError("Invalid cursor row ID")
                    key = decoded["key"]
                    if info["spec"]["type"]=="text":
                        if not isinstance(key,str):raise ValueError("Invalid cursor name")
                    elif isinstance(key,bool) or not isinstance(key,(int,float)) or not math.isfinite(key):
                        raise ValueError("Invalid cursor sort value")
                    anchor = (key,decoded["id"])
                except (KeyError,ValueError,TypeError,UnicodeError) as exc:
                    raise ValueError("Invalid pagination cursor: "+str(exc)) from exc
            rows = self._query_rows(db,info,limit=limit+1,anchor=anchor)
            more = len(rows)>limit;rows = rows[:limit]
            items = self._query_summaries(db,rows)
            next_cursor = None
            if more and rows:
                last = rows[-1]
                payload = {"v":2,"revision":info["revision"],"key":last["_sort_key"],"id":last["id"],"f":info["fingerprint"]}
                next_cursor = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
            return {"items":items,"next_cursor":next_cursor,"total":info["total"]}

    def browse_neighbors(self, image_id, filters=None, before=4, after=4):
        """Return a bounded window in the current filter/order, without computing a rank."""
        if image_id is not None and (isinstance(image_id,bool) or not isinstance(image_id,int) or image_id<1):
            raise ValueError("图片 ID 无效")
        if any(isinstance(value,bool) or not isinstance(value,int) or not 0<=value<=4 for value in (before,after)):
            raise ValueError("前后邻居数量应在 0 至 4 之间")
        if filters is not None and not isinstance(filters,dict):raise ValueError("图片筛选条件无效")
        with self.connect() as db:
            db.execute("BEGIN")
            info = self._query_info(db,filters or {},max(1,before+after+1))
            spec = info["spec"]
            clauses = [*info["clauses"],"i.id=?"]
            row = db.execute(f"SELECT {SUMMARY_PROJECTION},{spec['column']} AS _sort_key FROM images i WHERE "+" AND ".join(clauses)+" LIMIT 1",[*info["args"],image_id]).fetchone() if image_id is not None else None
            if row is None:
                first = self._query_rows(db,info,limit=1)
                row = first[0] if first else None
            if row is None:
                return {"items":[],"current_id":None,"previous_id":None,"next_id":None,"total":info["total"]}
            anchor = (row["_sort_key"],row["id"])
            previous = self._query_rows(db,info,limit=max(before,1),anchor=anchor,before=True)
            following = self._query_rows(db,info,limit=max(after,1),anchor=anchor)
            window = [*reversed(previous[:before]),row,*following[:after]]
            return {"items":self._query_summaries(db,window),"current_id":row["id"],
                    "previous_id":previous[0]["id"] if previous else None,
                    "next_id":following[0]["id"] if following else None,"total":info["total"]}

    def stats(self):
        with self.connect() as db:
            row = db.execute("SELECT total,bytes,ai,unclassified,favorites FROM catalog_metrics WHERE id=1").fetchone()
            result = {key: row[key] or 0 for key in row.keys()}
            result["models"] = [dict(r) for r in db.execute("SELECT name,count FROM catalog_counts WHERE kind='model' AND count>0 ORDER BY count DESC LIMIT 100")]
            result["formats"] = [dict(r) for r in db.execute("SELECT name,count FROM catalog_counts WHERE kind='format' AND count>0 ORDER BY count DESC")]
            result["tags"] = [dict(r) for r in db.execute("SELECT name,count FROM catalog_counts WHERE kind='tag' AND count>0 ORDER BY count DESC LIMIT 100")]
            result["prompt_tags"] = result["tags"]
            result["content_tags"] = [dict(r) for r in db.execute("SELECT t.id,t.name,COALESCE(c.count,0) count FROM content_tags t LEFT JOIN catalog_counts c ON c.kind='content' AND c.name=CAST(t.id AS TEXT) WHERE t.enabled=1 ORDER BY count DESC,t.id LIMIT 200")]
            return result

    def _root_removal_state(self, db, root_id, job_id=None):
        root = db.execute("SELECT * FROM roots WHERE id=?",(root_id,)).fetchone()
        if not root: raise ValueError("导入目录不存在")
        counts = db.execute("""SELECT COUNT(*) total,COALESCE(SUM(EXISTS(
            SELECT 1 FROM image_roots other WHERE other.image_id=r.image_id AND other.root_id<>r.root_id)),0) shared
            FROM image_roots r WHERE r.root_id=?""",(root_id,)).fetchone()
        blockers = []
        active = list(db.execute("SELECT kind FROM jobs WHERE status IN ('queued','running') AND id<>? LIMIT 8",(job_id or "",)))
        if active: blockers.append("仍有运行或排队任务："+"、".join(dict.fromkeys(row["kind"] for row in active))+"；请等待结束或取消")
        plans = db.execute("""SELECT 1 FROM plan_items p JOIN plans plan ON plan.id=p.plan_id
          WHERE plan.status IN ('ready','queued','running') AND p.image_id IN
          (SELECT image_id FROM image_roots WHERE root_id=?) LIMIT 1""",(root_id,)).fetchone()
        if plans: blockers.append("相关图片仍有未完成整理预览；请取消或完成预览后移除")
        journals = db.execute("""SELECT 1 FROM journal WHERE (state IN
          ('prepared','copying','verified','published','needs_review') OR (state='committed' AND NOT(undo_of IS NOT NULL AND EXISTS(SELECT 1 FROM journal original WHERE original.id=journal.undo_of AND original.state='undone')))) AND
          (source_root_id=? OR restore_root_id=? OR image_id IN
          (SELECT image_id FROM image_roots WHERE root_id=?)) LIMIT 1""",(root_id,root_id,root_id)).fetchone()
        if journals: blockers.append("目录关联未处理或可撤销移动日志；请先完成恢复或撤销，保留索引保障数据安全")
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='duplicate_journal'").fetchone():
            duplicate_logs = db.execute("""SELECT 1 FROM duplicate_journal j JOIN duplicate_plan_items p ON p.id=j.item_id
              WHERE j.state IN ('prepared','moved','committed','restore_prepared','purge_prepared','needs_review') AND
              (EXISTS(SELECT 1 FROM duplicate_archived_image_roots a WHERE a.duplicate_journal_id=j.id AND a.root_id=?) OR
               EXISTS(SELECT 1 FROM image_roots r WHERE r.root_id=? AND r.image_id IN (j.image_id,p.keep_id))) LIMIT 1""", (root_id,root_id)).fetchone()
            if duplicate_logs:
                blockers.append("目录关联可恢复隔离副本或其保留项；请先恢复或明确永久清空后移除")
            duplicate_plans = db.execute("""SELECT 1 FROM duplicate_plan_items p JOIN duplicate_plans plan ON plan.id=p.plan_id
              WHERE plan.status IN ('ready','queued','running') AND EXISTS(SELECT 1 FROM image_roots r
              WHERE r.root_id=? AND r.image_id IN (p.image_id,p.keep_id)) LIMIT 1""", (root_id,)).fetchone()
            if duplicate_plans:
                blockers.append("相关图片仍有重复清理预览；请完成或重新检查后移除")
        missing = db.execute("SELECT 1 FROM images i WHERE root_id=? AND NOT EXISTS(SELECT 1 FROM image_roots r WHERE r.image_id=i.id AND r.root_id=?) LIMIT 1",(root_id,root_id)).fetchone()
        if missing: blockers.append("目录归属记录不完整，请重新扫描后移除")
        revision = db.execute("SELECT revision FROM catalog_metrics WHERE id=1").fetchone()[0]
        total,shared = counts["total"],counts["shared"]
        return {"root":dict(root),"root_id":root_id,"count":total,"total":total,"shared":shared,"retained":shared,
                "remove":total-shared,"revision":revision,"can_remove":not blockers,"blockers":blockers,
                "files_affected":0,"membership_note":"共享图片保留；旧版本重叠目录依据已登记路径保守重建归属，未读取原图"}

    def preview_remove_root(self, root_id, *, job_id=None):
        if isinstance(root_id,bool) or not isinstance(root_id,int) or root_id<1: raise ValueError("目录 ID 无效")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            result = self._root_removal_state(db,root_id,job_id)
            plan_id = uuid.uuid4().hex
            db.execute("INSERT INTO removal_plans(id,root_id,root_path,revision,total,remove_count,shared,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (plan_id,root_id,result["root"]["path"],result["revision"],result["total"],result["remove"],result["shared"],time.time()))
            return {"id":plan_id,"plan_id":plan_id,**result}

    def remove_root(self, root_id, *, plan_id=None, expected_revision=None, job_id=None, cancelled=lambda:False, progress=None):
        """One cancellable transaction removes index relations only, never filesystem data."""
        from .organizer import Cancelled
        if cancelled(): raise Cancelled()
        if progress: progress(0,0)
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.set_progress_handler(lambda:1 if cancelled() else 0,10000)
                if plan_id:
                    preview = db.execute("SELECT * FROM removal_plans WHERE id=? AND root_id=? AND status='ready'",(plan_id,root_id)).fetchone()
                elif expected_revision is not None:
                    preview = db.execute("SELECT * FROM removal_plans WHERE root_id=? AND revision=? AND status='ready' ORDER BY created_at DESC LIMIT 1",(root_id,expected_revision)).fetchone()
                else:
                    raise ValueError("移除目录必须先生成并确认预览")
                if not preview: raise ValueError("目录移除预览不存在或已执行；请重新预览")
                current = self._root_removal_state(db,root_id,job_id)
                if current["blockers"]: raise ValueError("；".join(current["blockers"]))
                if current["revision"] != preview["revision"] or (current["total"],current["remove"],current["shared"],current["root"]["path"]) != (preview["total"],preview["remove_count"],preview["shared"],preview["root_path"]):
                    raise ValueError("图库或移动记录已改变；请重新预览移除范围")
                if expected_revision is not None and expected_revision!=preview["revision"]: raise ValueError("目录移除预览已过期")
                total,processed = current["total"],0
                last_id = 0
                while True:
                    batch = list(db.execute("SELECT image_id FROM image_roots WHERE root_id=? AND image_id>? ORDER BY image_id LIMIT 250",(root_id,last_id)))
                    if not batch: break
                    for row in batch:
                        if cancelled(): raise Cancelled()
                        image_id = row["image_id"]
                        other = db.execute("SELECT root_id FROM image_roots WHERE image_id=? AND root_id<>? ORDER BY root_id LIMIT 1",(image_id,root_id)).fetchone()
                        if other:
                            db.execute("UPDATE images SET root_id=? WHERE id=? AND root_id=?",(other[0],image_id,root_id))
                            db.execute("DELETE FROM image_roots WHERE image_id=? AND root_id=?",(image_id,root_id))
                        else:
                            db.execute("DELETE FROM images WHERE id=?",(image_id,))
                        last_id = image_id; processed += 1
                    if job_id:
                        # Use the same transaction, avoiding another writer deadlocking progress.
                        db.execute("UPDATE jobs SET processed=?,total=?,message=?,updated_at=? WHERE id=?",(processed,total,"移除图库索引中，原文件保留",time.time(),job_id))
                if cancelled(): raise Cancelled()
                db.execute("DELETE FROM roots WHERE id=?",(root_id,))
                db.execute("UPDATE removal_plans SET status='finished' WHERE id=?",(preview["id"],))
                result = {**current,"removed":current["remove"],"processed":processed,"status":"finished","id":preview["id"]}
        except sqlite3.OperationalError as exc:
            if cancelled(): raise Cancelled() from exc
            raise
        if progress: progress(result["processed"],result["total"])
        return result

    def rules(self):
        with self.connect() as db:
            return [{**dict(row), "enabled": bool(row["enabled"])} for row in db.execute("SELECT * FROM rules ORDER BY rowid")]

    def replace_rules(self, rules):
        with self.connect() as db:
            db.execute("DELETE FROM rules")
            db.executemany("INSERT INTO rules(id,name,kind,match,destination,enabled) VALUES(:id,:name,:kind,:match,:destination,:enabled)", rules)
        return self.rules()

    def settings(self):
        with self.connect() as db:
            return {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT * FROM settings")}

    def update_settings(self, settings):
        with self.connect() as db:
            db.executemany("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", [(key,json.dumps(value)) for key,value in settings.items()])
        return self.settings()
