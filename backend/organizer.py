"""Preview-first organization, verified copies, durable journals, and safe undo."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import stat
import threading
import time
import uuid
from contextlib import contextmanager, ExitStack
from pathlib import Path


class Cancelled(Exception):
    pass


PENDING_MOVE_BUNDLES_SQL="SELECT p.*,j.id job_id FROM plan_items p JOIN jobs j ON j.kind='organize' AND json_extract(j.payload,'$.plan_id')=p.plan_id WHERE p.status='ready' AND EXISTS(SELECT 1 FROM journal x WHERE x.job_id=j.id AND x.image_id=p.image_id AND x.asset_kind='image' AND x.state='committed')"
PENDING_UNDO_SIDES_SQL="SELECT s.*,u.job_id undo_job_id,u.destination restored_image,u.digest restored_digest FROM journal s JOIN journal i ON i.job_id=s.job_id AND i.image_id=s.image_id AND i.asset_kind='image' AND i.state='undone' JOIN journal u ON u.undo_of=i.id AND u.asset_kind='image' AND u.state='committed' JOIN jobs j ON j.id=u.job_id AND j.kind='undo' WHERE s.asset_kind='sidecar' AND s.state='committed'"


@contextmanager
def locked_directory(path):
    """Pin the destination ancestry before creating a temporary copy."""
    if os.name!='nt':raise ValueError('安全目录句柄不可用，文件保留')
    import ctypes
    from ctypes import wintypes
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.CreateFileW.argtypes=(wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE)
    api.CreateFileW.restype=wintypes.HANDLE;api.CloseHandle.argtypes=(wintypes.HANDLE,)
    handles=[];current=Path(path)
    try:
        while current!=current.parent:
            handle=api.CreateFileW(str(current),0x80,1,None,3,0x02200000,None)
            if handle==ctypes.c_void_p(-1).value:raise OSError(ctypes.get_last_error(),'无法锁定目标目录')
            handles.append(handle);current=current.parent
        check_directory_chain(path)
        yield
    finally:
        for handle in reversed(handles):api.CloseHandle(handle)


def reparse_point(path,info=None):
    try:
        info = info or Path(path).lstat()
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info,"st_file_attributes",0) & getattr(stat,"FILE_ATTRIBUTE_REPARSE_POINT",1024))
    except FileNotFoundError:
        return False


def check_directory_chain(path):
    current = Path(path)
    while True:
        if reparse_point(current):
            raise ValueError("路径包含符号链接或 NTFS 重解析点，无法安全操作")
        if current == current.parent:
            break
        current = current.parent


def shared_companion(source,sidecar):
    from .jobs import IMAGE_EXTENSIONS
    source,sidecar = Path(source),Path(sidecar)
    if sidecar != source.with_suffix(sidecar.suffix):
        return False
    return any(source.with_suffix(extension) != source and source.with_suffix(extension).is_file() for extension in IMAGE_EXTENSIONS)


def absolute_directory(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("请选择绝对路径，不能使用相对目录")
    resolved = path.resolve()
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("目标路径必须是文件夹")
    return resolved


def category_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .")
    if not value:
        value = "未分类"
    if value.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        value = "_" + value
    if len(value) > 100:
        value = value[:85] + "_" + hashlib.sha256(value.encode()).hexdigest()[:10]
    return value


def safe_target(root, category, filename):
    root = absolute_directory(root)
    target = root / category_name(category) / Path(filename).name
    resolved = target.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("目标文件夹符号链接越过了所选目录")
    check_directory_chain(target.parent)
    return target


def digest_file(path, cancelled=None):
    signature(path)
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while block := source.read(1024 * 1024):
            if cancelled and cancelled():
                raise Cancelled()
            digest.update(block)
    return digest.hexdigest()


def signature(path):
    check_directory_chain(Path(path).parent)
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode) or reparse_point(path,info):
        raise ValueError("源路径不再是普通文件")
    return info


class Organizer:
    def __init__(self, store, *, secure_moves=False):
        self.store = store
        self.lock = threading.Lock()
        self.secure_moves = secure_moves
        self._identity = threading.local()

    @contextmanager
    def identity_check(self, callback):
        previous=getattr(self._identity,'check',None)
        self._identity.check=callback
        try:yield
        finally:self._identity.check=previous

    def _check_identity(self, file):
        check=getattr(self._identity,'check',None)
        if check is not None:check(file)

    def preview(self, kind, destination, ids=None, cancelled=lambda:False, *, root_id=None):
        plan_id = uuid.uuid4().hex
        try:
            return self._preview(kind,destination,ids,cancelled,plan_id,root_id)
        except BaseException as exc:
            stopped = cancelled() or isinstance(exc,Cancelled)
            with self.store.connect() as db:
                db.execute("UPDATE plans SET status=? WHERE id=?",("cancelled" if stopped else "failed",plan_id))
            if stopped and not isinstance(exc,Cancelled):
                raise Cancelled() from exc
            raise

    def _preview(self, kind, destination, ids, cancelled, plan_id, root_id=None):
        if kind not in ("model","tag","content_tag","artist","format"):
            raise ValueError("分类方式无效")
        destination = absolute_directory(destination)
        rules = [rule for rule in self.store.rules() if rule["enabled"] and rule["kind"] == kind]
        with self.store.connect() as db:
            db.execute("INSERT INTO plans(id,kind,destination,created_at) VALUES(?,?,?,?)", (plan_id, kind, str(destination), time.time()))
        where, arguments = "", []
        if root_id is not None:
            if ids is not None or isinstance(root_id,bool) or not isinstance(root_id,int) or root_id<1:
                raise ValueError("分类目录范围无效")
            with self.store.connect() as db:
                if not db.execute('SELECT 1 FROM roots WHERE id=?',(root_id,)).fetchone():raise ValueError("目录不存在")
            where=" WHERE EXISTS(SELECT 1 FROM image_roots WHERE image_roots.image_id=images.id AND root_id=?)"
            arguments=[root_id]
        if ids is not None:
            unique_ids = list(dict.fromkeys(ids))
            if not unique_ids:
                return self.plan(plan_id)
            where = " WHERE id IN (" + ",".join("?" for _ in unique_ids) + ")"
            arguments = unique_ids
        counts = {"total": 0, "movable": 0, "skipped": 0, "conflicts": 0}
        # A read connection streams batches; no list of the entire library is built.
        with self.store.connect() as read:
            tag_projection = "tags" if kind=="tag" else "'[]' AS tags"
            rows = read.execute("SELECT id,path,name,size,mtime_ns,model,family,"+tag_projection+",artist,format,companion_signature FROM images" + where + " ORDER BY id", arguments)
            while batch := rows.fetchmany(250):
                inserts = []
                content_matches = self.store._content_matches(read,[row["id"] for row in batch]) if kind=="content_tag" else {}
                for row in batch:
                    if cancelled():
                        raise Cancelled()
                    tags = json.loads(row["tags"])
                    content = [match for match in content_matches.get(row["id"],[]) if match["enabled"] and not match["stale"] and (match["source"]=="manual" or match["score"]>=match["threshold"])]
                    content_names = list(dict.fromkeys(match["name"] for match in content))
                    category = {"model": row["family"] if row["family"] not in ("", "Unknown", "未知") else "未识别模型", "tag": tags[0] if tags else "无 TAG", "content_tag":content_names[0] if content_names else "未识别内容", "artist": row["artist"] or "未知画师", "format": row["format"] or "未知格式"}[kind]
                    root,matched = destination,False
                    for rule in rules:
                        candidates = {"model": [row["model"], row["family"]], "tag": tags,"content_tag":content_names, "artist": [row["artist"]], "format": [row["format"]]}[kind]
                        if rule["match"].casefold() and any(rule["match"].casefold() in item.casefold() for item in candidates):
                            category, root,matched = rule["name"], absolute_directory(rule["destination"]) if rule["destination"].strip() else destination,True
                            break
                    source = Path(row["path"])
                    target = safe_target(root, category, row["name"])
                    status, reason = "ready", ""
                    if kind=="content_tag" and len(content_names)>1:
                        choice = "按人工确认优先与类别名称稳定排序选择“"+category+"”" if content[0]["source"]=="manual" else "按最高相似度与稳定排序选择“"+category+"”"
                        reason = "同时匹配多个内容类别；"+("按优先规则使用“"+category+"”" if matched else choice)
                    if not matched:
                        if kind == "model" and row["family"] in ("", "Unknown", "未知", "非 AI"):
                            status,reason = "skipped","没有可信模型归属或匹配规则；保留原图"
                        elif kind == "tag":
                            status,reason = "skipped","TAG 分类仅执行已启用的匹配规则；保留原图"
                        elif kind == "content_tag" and not content_names:
                            status,reason = "skipped","没有有效图像内容分类；生成提示词不作为内容类别，保留原图"
                        elif kind == "artist" and not row["artist"].strip():
                            status,reason = "skipped","未读取到画师信息；保留原图"
                        elif kind == "format" and not row["format"].strip():
                            status,reason = "skipped","未识别图片格式；保留原图"
                    try:
                        source_stat = signature(source)
                        if (source_stat.st_size, source_stat.st_mtime_ns) != (row["size"], row["mtime_ns"]):
                            status, reason = "skipped", "源文件已改变，请重新扫描"
                        elif os.path.normcase(str(source.resolve())) == os.path.normcase(str(target.resolve())):
                            status, reason = "skipped", "已经位于目标位置"
                        elif target.exists() and status == "ready":
                            status, reason = "conflict", "目标已存在同名文件；不会覆盖"
                    except (OSError, ValueError) as exc:
                        status, reason = "skipped", str(exc)
                    counts["total"] += 1
                    counts[{"ready": "movable", "skipped": "skipped", "conflict": "conflicts"}[status]] += 1
                    companions = []
                    saved_companions = json.loads(row["companion_signature"])
                    if saved_companions != self.store.sidecar_signatures(source) and status == "ready":
                        status,reason = "skipped","TXT 伴随文件已变更；请重新扫描后分类"
                    for companion in saved_companions:
                        sidecar_name = companion["name"]
                        if Path(sidecar_name).name != sidecar_name or companion.get("symlink"):
                            status,reason = "skipped","TXT 伴随文件不是安全的普通文件；保留原图"
                            continue
                        sidecar = source.parent / sidecar_name
                        extension = sidecar.suffix.lower()
                        companion_target = target.with_suffix(extension) if sidecar == source.with_suffix(extension) else Path(str(target)+extension)
                        try:
                            companion_info = signature(sidecar)
                            companions.append({"source":str(sidecar),"destination":str(companion_target),"size":companion_info.st_size,"mtime_ns":companion_info.st_mtime_ns})
                            if companion_target.exists() and status == "ready":
                                status,reason = "conflict","TXT 伴随文件目标已存在；图片和 TXT 均保留"
                            # A stem sidecar shared by another image must not be removed implicitly.
                            if shared_companion(source,sidecar):
                                status,reason = "skipped","TXT/JSON 伴随文件与同名其他图片共享；需先拆分伴随文件"
                        except (OSError,ValueError):
                            if status == "ready":
                                status,reason = "skipped","TXT 伴随文件已变更或丢失；请重新扫描"
                    # Counts are rebuilt below; count only tracks bounded insertion totals here.
                    inserts.append((plan_id, row["id"], row["name"], str(source), str(target), category_name(category), status, reason, row["size"], row["mtime_ns"],json.dumps(companions)))
                with self.store.connect() as write:
                    write.executemany("INSERT INTO plan_items(plan_id,image_id,name,source,destination,category,status,reason,size,mtime_ns,companions) VALUES(?,?,?,?,?,?,?,?,?,?,?)", inserts)
        with self.store.connect() as db:
            if cancelled():
                raise Cancelled()
            db.set_progress_handler(lambda:1 if cancelled() else 0,10000)
            # Same names in different source folders can collide even before files exist.
            db.execute("UPDATE plan_items SET status='conflict',reason='多个源文件具有相同目标名称；不会覆盖' WHERE plan_id=? AND status='ready' AND id NOT IN (SELECT MIN(id) FROM plan_items WHERE plan_id=? AND status='ready' GROUP BY destination)", (plan_id,plan_id))
            actual = {row["status"]:row["amount"] for row in db.execute("SELECT status,COUNT(*) amount FROM plan_items WHERE plan_id=? GROUP BY status", (plan_id,))}
            db.execute("UPDATE plans SET total=?,movable=?,skipped=?,conflicts=? WHERE id=?", (counts["total"],actual.get("ready",0),actual.get("skipped",0),actual.get("conflict",0),plan_id))
        return self.plan(plan_id)

    def plan(self, plan_id):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
            if not row:
                raise ValueError("整理预览不存在")
            result = dict(row)
            result["items"] = [{"id": item["image_id"], "companions":json.loads(item["companions"]), **{key: item[key] for key in ("name", "source", "destination", "category", "status", "reason")}} for item in db.execute("SELECT * FROM plan_items WHERE plan_id=? ORDER BY id LIMIT 200", (plan_id,))]
            result["counts"] = [dict(item) for item in db.execute("SELECT category name,COUNT(*) count FROM plan_items WHERE plan_id=? GROUP BY category ORDER BY count DESC LIMIT 200", (plan_id,))]
            result["items_truncated"] = result["total"] > 200
            return result

    def claim_plan(self, plan_id):
        with self.store.connect() as db:
            cursor = db.execute("UPDATE plans SET status='queued' WHERE id=? AND status='ready'", (plan_id,))
            if not cursor.rowcount:
                raise ValueError("预览不存在或已执行；请重新生成预览")
            return db.execute("SELECT movable FROM plans WHERE id=?", (plan_id,)).fetchone()[0]

    def _journal(self, job_id, image_id, source, target, info, source_root_id=None,asset_kind="image",restore_root=None,undo_of=None):
        temp = target.parent / (".luma-" + uuid.uuid4().hex + ".tmp")
        with self.store.connect() as db:
            cursor = db.execute("INSERT INTO journal(job_id,image_id,source,destination,temp_path,source_root_id,size,mtime_ns,created_at,updated_at,asset_kind,restore_root_id,undo_of) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id,image_id,str(source),str(target),str(temp),source_root_id,info.st_size,info.st_mtime_ns,time.time(),time.time(),asset_kind,restore_root,undo_of))
        return cursor.lastrowid, temp

    def _state(self, journal_id, state, digest=None, error=""):
        with self.store.connect() as db:
            if digest is None:
                db.execute("UPDATE journal SET state=?,error=?,updated_at=? WHERE id=?", (state,error,time.time(),journal_id))
            else:
                db.execute("UPDATE journal SET state=?,digest=?,error=?,updated_at=? WHERE id=?", (state,digest,error,time.time(),journal_id))

    def _catalog_move(self, image_id, destination, root_id=None):
        destination = Path(destination)
        if root_id is None:
            root_id = self.store.add_root(destination.parent.parent)
        with self.store.connect() as db:
            db.execute("UPDATE images SET path=?,name=?,root_id=?,size=?,mtime_ns=? WHERE id=?", (str(destination),destination.name,root_id,destination.stat().st_size,destination.stat().st_mtime_ns,image_id))
            self.store.reassociate_image(db,image_id,destination,root_id)

    def move(self, job_id, image_id, source, target, size, mtime_ns, cancelled=lambda: False, restore_root=None,asset_kind="image",undo_of=None):
        if self.secure_moves:
            return self._secure_move(job_id,image_id,source,target,size,mtime_ns,cancelled,restore_root,asset_kind,undo_of)
        source, target = Path(source), Path(target)
        info = signature(source)
        if (info.st_size, info.st_mtime_ns) != (size, mtime_ns):
            raise ValueError("源文件已改变；已跳过")
        if target.exists():
            raise FileExistsError("目标已存在；已跳过，未覆盖任何文件")
        if asset_kind == "image":
            with self.store.connect() as db:
                collision = db.execute("SELECT id FROM images WHERE path=? AND id<>?", (str(target),image_id)).fetchone()
            if collision:
                raise FileExistsError("目标路径已有索引记录；请重新扫描目标目录")
        # Check the parent chain before touching the filesystem, including existing symlinks.
        check_directory_chain(target.parent)
        image = self.store.get_image(image_id)
        journal_id, temp = self._journal(job_id,image_id,source,target,info,image.get("root_id") if image else None,asset_kind,restore_root,undo_of)
        published = False
        try:
            if cancelled():
                raise Cancelled()
            target.parent.mkdir(parents=True,exist_ok=True)
            check_directory_chain(target.parent)
            self._state(journal_id, "copying")
            digest = hashlib.sha256()
            with open(source,"rb") as incoming, open(temp,"xb") as outgoing:
                while block := incoming.read(1024 * 1024):
                    if cancelled():
                        raise Cancelled()
                    outgoing.write(block)
                    digest.update(block)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            after = signature(source)
            if (after.st_size,after.st_mtime_ns,after.st_ino,after.st_dev) != (size,mtime_ns,info.st_ino,info.st_dev):
                raise ValueError("复制期间源文件发生变化，原文件保留")
            checksum = digest.hexdigest()
            if digest_file(temp, cancelled) != checksum:
                raise IOError("复制校验失败，原文件保留")
            shutil.copystat(source,temp,follow_symlinks=False)
            self._state(journal_id,"verified",checksum)
            if cancelled():
                raise Cancelled()
            check_directory_chain(target.parent)
            try:
                # link is an atomic publication that fails if the target exists.
                os.link(temp,target)
            except OSError as exc:
                if exc.errno == errno.EEXIST or target.exists():
                    raise FileExistsError("目标已存在；未覆盖") from exc
                # FAT / removable drives may lack hardlinks; exclusive create still never overwrites.
                with open(temp,"rb") as incoming, open(target,"xb") as outgoing:
                    while block := incoming.read(1024 * 1024):
                        if cancelled():
                            raise Cancelled()
                        outgoing.write(block)
                    outgoing.flush(); os.fsync(outgoing.fileno())
                shutil.copystat(temp,target,follow_symlinks=False)
            published = True
            if digest_file(target) != checksum:
                raise IOError("目标校验失败，原文件保留")
            # Persistence precedes source deletion. Recovery only resumes rows with this state.
            self._state(journal_id,"published",checksum)
            after = signature(source)
            if (after.st_size,after.st_mtime_ns) != (size,mtime_ns) or digest_file(source) != checksum:
                raise ValueError("源文件在校验后发生变化；两个文件都已保留")
            final_info = signature(source)
            if (final_info.st_size,final_info.st_mtime_ns,final_info.st_ino,final_info.st_dev) != (size,mtime_ns,info.st_ino,info.st_dev):
                raise ValueError("源文件在最终校验后发生变化；两个文件都已保留")
            check_directory_chain(target.parent)
            signature(target)
            source.unlink()
            if asset_kind == "image":
                self._catalog_move(image_id,target,restore_root)
            self._state(journal_id,"committed")
            if undo_of is not None:
                self._state(undo_of,"undone")
            return journal_id
        except BaseException as exc:
            # Once the source is gone, leave publication recoverable if catalog commit failed.
            state = "published" if published and not source.exists() else "needs_review" if published or target.exists() else "failed"
            self._state(journal_id,state,error=str(exc) or "任务已取消")
            raise
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass

    def _secure_move(self, job_id, image_id, source, target, size, mtime_ns, cancelled, restore_root, asset_kind, undo_of):
        """Same verified-copy journal protocol, pinned Windows handles through publication/deletion."""
        from .duplicates import VerifiedFile
        source,target=Path(source),Path(target)
        journal_id=None;temp=None;published=False;temp_identity=None
        try:
            with VerifiedFile(source,mutable=True) as incoming,ExitStack() as holds:
                self._check_identity(incoming)
                info=incoming.info
                if (info.st_size,info.st_mtime_ns)!=(size,mtime_ns):raise ValueError("源文件改变，已保留")
                if target.exists() or target.is_symlink():raise FileExistsError("目标已存在，不覆盖")
                if asset_kind=='image':
                    with self.store.connect() as db:
                        if db.execute('SELECT 1 FROM images WHERE path=? AND id<>?',(str(target),image_id)).fetchone():raise FileExistsError("目标已有索引，不覆盖")
                check_directory_chain(target.parent)
                image=self.store.get_image(image_id)
                journal_id,temp=self._journal(job_id,image_id,source,target,info,image.get('root_id') if image else None,asset_kind,restore_root,undo_of)
                if cancelled():raise Cancelled()
                target.parent.mkdir(parents=True,exist_ok=True);check_directory_chain(target.parent)
                holds.enter_context(locked_directory(target.parent))
                self._state(journal_id,'copying')
                digest=hashlib.sha256()
                with open(temp,'xb') as output:
                    try:
                        while block:=incoming.file.read(1024*1024):
                            if cancelled():raise Cancelled()
                            output.write(block);digest.update(block)
                        output.flush();os.fsync(output.fileno())
                    finally:
                        output.flush();created=os.fstat(output.fileno());temp_identity=(created.st_dev,created.st_ino,created.st_ctime_ns)

                incoming.verify();checksum=digest.hexdigest()
                os.utime(temp,ns=(info.st_atime_ns,info.st_mtime_ns))
                with VerifiedFile(temp,mutable=True) as copied:
                    if copied.digest(cancelled)!=checksum:raise IOError("副本校验失败，源文件保留")
                    self._state(journal_id,'verified',checksum)
                    if cancelled():raise Cancelled()
                    # Temp and destination share a volume; rename never replaces a target.
                    copied.rename(target);published=True
                    if copied.digest(cancelled)!=checksum:raise IOError("目标校验失败，源文件保留")
                    self._state(journal_id,'published',checksum)
                    if incoming.digest(cancelled)!=checksum:raise ValueError("源文件校验改变，两个文件保留")
                    incoming.verify();copied.verify()
                    if cancelled():raise Cancelled()
                    incoming.delete()
                    if asset_kind=='image':self._catalog_move(image_id,target,restore_root)
                    self._state(journal_id,'committed')
                    callback=getattr(getattr(self._identity,'check',None),'remember',None)
                    if callback:callback(copied)
                    if undo_of is not None:self._state(undo_of,'undone')
                return journal_id
        except BaseException as error:
            if journal_id is not None:
                self._state(journal_id,'published' if published else 'failed',error=type(error).__name__)
            raise
        finally:
            if temp is not None and temp.exists():
                try:
                    with VerifiedFile(temp,mutable=True) as leftover:
                        if temp_identity==(leftover.info.st_dev,leftover.info.st_ino,leftover.info.st_ctime_ns):leftover.delete()
                except (OSError,ValueError):pass

    def move_bundle(self, job_id, row, cancelled):
        companions = json.loads(row["companions"])
        assets = [{"source":row["source"],"destination":row["destination"],"size":row["size"],"mtime_ns":row["mtime_ns"],"asset_kind":"image"}, *[{**item,"asset_kind":"sidecar"} for item in companions]]
        for asset in assets:
            info = signature(asset["source"])
            if (info.st_size,info.st_mtime_ns) != (asset["size"],asset["mtime_ns"]):
                raise ValueError("图片或 TXT 伴随文件已改变；请重新扫描")
            if Path(asset["destination"]).exists():
                raise FileExistsError("图片或 TXT 的目标已存在；不会覆盖")
            if asset["asset_kind"] == "sidecar" and shared_companion(row["source"],asset["source"]):
                raise ValueError("伴随文件在预览后与新图片共享；保留全部源文件")
        moved = []
        try:
            # Sidecar follows the image, with its own journal and exact checks.
            for asset in assets:
                journal_id = self.move(job_id,row["image_id"],asset["source"],asset["destination"],asset["size"],asset["mtime_ns"],cancelled,asset_kind=asset["asset_kind"])
                moved.append((journal_id,asset))
        except BaseException:
            # Best-effort compensation is journaled too; an external conflict preserves both copies.
            for journal_id,asset in reversed(moved):
                try:
                    info = signature(asset["destination"])
                    with self.store.connect() as db:
                        original_root = db.execute("SELECT source_root_id FROM journal WHERE id=?",(journal_id,)).fetchone()[0]
                    rollback_id = self.move(job_id,row["image_id"],asset["destination"],asset["source"],info.st_size,info.st_mtime_ns,restore_root=original_root,asset_kind=asset["asset_kind"])
                    self._state(journal_id,"rolled_back")
                    self._state(rollback_id,"rolled_back")
                except Exception as exc:
                    self._state(journal_id,"needs_review",error="伴随文件移动未完成，回滚受阻："+str(exc))
            raise

    def execute(self, manager, job_id, plan_id):
        with self.lock:
            with self.store.connect() as db:
                db.execute("UPDATE plans SET status='running' WHERE id=?", (plan_id,))
            processed, errors, last_id = 0, 0, 0
            try:
                while True:
                    with self.store.connect() as db:
                        batch = list(db.execute("SELECT * FROM plan_items WHERE plan_id=? AND status='ready' AND id>? ORDER BY id LIMIT 100", (plan_id,last_id)))
                    if not batch:
                        break
                    for row in batch:
                        if manager.cancelled(job_id):
                            raise Cancelled()
                        last_id = row["id"]
                        try:
                            self.move_bundle(job_id,row,lambda: manager.cancelled(job_id))
                            item_status, reason = "moved", ""
                        except Cancelled:
                            raise
                        except Exception as exc:
                            errors += 1
                            item_status, reason = "error", str(exc)
                        with self.store.connect() as db:
                            db.execute("UPDATE plan_items SET status=?,reason=? WHERE id=?", (item_status,reason,row["id"]))
                        processed += 1
                        manager.progress(job_id,processed=processed,errors=errors,message=f"已处理 {processed} 张，失败 {errors} 张")
            finally:
                with self.store.connect() as db:
                    db.execute("UPDATE plans SET status=? WHERE id=?", ("cancelled" if manager.cancelled(job_id) else "finished",plan_id))

    def undo(self, manager, job_id, original_job):
        with self.lock:
            processed, errors, last_id = 0, 0, 9223372036854775807
            while True:
                with self.store.connect() as db:
                    batch = list(db.execute("SELECT * FROM journal WHERE job_id=? AND asset_kind='image' AND state='committed' AND id<? ORDER BY id DESC LIMIT 100", (original_job,last_id)))
                if not batch:
                    break
                for row in batch:
                    last_id = row["id"]
                    if manager.cancelled(job_id):
                        raise Cancelled()
                    moved = []
                    try:
                        with self.store.connect() as db:
                            sidecars = list(db.execute("SELECT * FROM journal WHERE job_id=? AND image_id=? AND asset_kind='sidecar' AND state='committed' ORDER BY id",(original_job,row["image_id"])))
                            pending = db.execute("SELECT 1 FROM journal WHERE job_id=? AND image_id=? AND state IN ('needs_review','published')",(original_job,row["image_id"])).fetchone()
                        if pending:
                            raise ValueError("图片或 TXT 有未解决的移动日志；请先检查恢复记录")
                        assets = [row,*sidecars]
                        for asset in assets:
                            signature(asset["destination"])
                            if Path(asset["source"]).exists():
                                raise FileExistsError("原路径已有文件；图片和 TXT 均保留，不会覆盖")
                            if digest_file(asset["destination"],lambda: manager.cancelled(job_id)) != asset["digest"]:
                                raise ValueError("整理后的图片或 TXT 已改变；无法安全撤销")
                        for asset in assets:
                            current = signature(asset["destination"])
                            new_id = self.move(job_id,asset["image_id"],asset["destination"],asset["source"],current.st_size,current.st_mtime_ns,lambda: manager.cancelled(job_id),asset["source_root_id"],asset["asset_kind"],asset["id"])
                            moved.append((new_id,asset))
                    except BaseException as exc:
                        for new_id,asset in reversed(moved):
                            try:
                                current = signature(asset["source"])
                                with self.store.connect() as db:
                                    undo_root = db.execute("SELECT source_root_id FROM journal WHERE id=?",(new_id,)).fetchone()[0]
                                rollback_id = self.move(job_id,asset["image_id"],asset["source"],asset["destination"],current.st_size,current.st_mtime_ns,restore_root=undo_root,asset_kind=asset["asset_kind"])
                                self._state(new_id,"rolled_back")
                                self._state(rollback_id,"rolled_back")
                                self._state(asset["id"],"committed")
                            except Exception as rollback_error:
                                self._state(new_id,"needs_review",error="撤销回滚受阻："+str(rollback_error))
                        if isinstance(exc,Cancelled):
                            raise
                        if not isinstance(exc,Exception):
                            raise
                        errors += 1
                        manager.progress(job_id,message=str(exc))
                    processed += 1
                    manager.progress(job_id,processed=processed,errors=errors)

    def recover(self,cancelled=lambda:False):
        """Conservative crash recovery: never delete a source without a persisted publication."""
        if self.secure_moves:return self._secure_recover(cancelled)
        with self.store.connect() as db:
            unfinished = list(db.execute("SELECT * FROM journal WHERE state IN ('prepared','copying','verified','published')"))
        for row in unfinished:
            if cancelled():
                raise Cancelled()
            source,target,temp = Path(row["source"]),Path(row["destination"]),Path(row["temp_path"])
            try:
                if row["state"] == "published":
                    if not target.is_file() or digest_file(target,cancelled) != row["digest"]:
                        raise ValueError("中断后的目标文件校验失败；需要人工检查")
                    if source.exists():
                        info = signature(source)
                        if (info.st_size,info.st_mtime_ns) != (row["size"],row["mtime_ns"]) or digest_file(source,cancelled) != row["digest"]:
                            raise ValueError("中断后源文件改变；源和目标均保留")
                        source.unlink()
                    if row["asset_kind"] == "image":
                        self._catalog_move(row["image_id"],target,row["restore_root_id"])
                    self._state(row["id"],"committed")
                    if row["undo_of"] is not None:
                        self._state(row["undo_of"],"undone")
                else:
                    self._state(row["id"],"needs_review" if target.exists() else "failed",error="任务中断；源文件保留，请重新预览。可能存在的目标副本需检查。")
            except Cancelled:
                raise
            except Exception as exc:
                self._state(row["id"],"needs_review",error=str(exc))
            if temp.name.startswith(".luma-") and temp.name.endswith(".tmp") and temp.parent == target.parent:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
        # A crash between the image and its TXT companion is completed from the approved plan.
        with self.store.connect() as read:
            rows = read.execute("SELECT p.*,j.id job_id FROM plan_items p JOIN jobs j ON j.kind='organize' AND json_extract(j.payload,'$.plan_id')=p.plan_id WHERE p.status='ready' AND EXISTS(SELECT 1 FROM journal x WHERE x.job_id=j.id AND x.image_id=p.image_id AND x.asset_kind='image' AND x.state='committed')")
            while batch := rows.fetchmany(100):
                for item in batch:
                    if cancelled():
                        raise Cancelled()
                    try:
                        if Path(item["source"]).exists() or not Path(item["destination"]).is_file():
                            continue
                        with self.store.connect() as verify:
                            image_journal = verify.execute("SELECT id,digest FROM journal WHERE job_id=? AND image_id=? AND asset_kind='image' AND state='committed' ORDER BY id DESC LIMIT 1",(item["job_id"],item["image_id"])).fetchone()
                        if not image_journal or digest_file(item["destination"],cancelled) != image_journal["digest"]:
                            if image_journal:
                                self._state(image_journal["id"],"needs_review",error="图片在中断后发生变化；伴随文件保留原位置")
                            raise ValueError("图片在中断后发生变化；伴随文件保留原位置")
                        for companion in json.loads(item["companions"]):
                            source,target = Path(companion["source"]),Path(companion["destination"])
                            if source.exists() and not target.exists():
                                self.move(item["job_id"],item["image_id"],source,target,companion["size"],companion["mtime_ns"],cancelled,asset_kind="sidecar")
                            elif source.exists() or not target.exists():
                                raise ValueError("TXT 伴随文件恢复存在冲突；请检查移动日志")
                        with self.store.connect() as write:
                            write.execute("UPDATE plan_items SET status='moved',reason='中断后已从日志恢复' WHERE id=?",(item["id"],))
                    except Cancelled:
                        raise
                    except Exception as exc:
                        with self.store.connect() as write:
                            write.execute("UPDATE plan_items SET status='error',reason=? WHERE id=?",(str(exc),item["id"]))

    def _secure_recover(self,cancelled):
        from .duplicates import VerifiedFile
        last=0
        while True:
            with self.store.connect() as db:
                batch=list(db.execute("SELECT * FROM journal WHERE state IN ('prepared','copying','verified','published','needs_review') AND id>? ORDER BY id LIMIT 100",(last,)))
            if not batch:break
            for row in batch:
                if cancelled():raise Cancelled()
                last=row['id'];source,target=Path(row['source']),Path(row['destination'])
                try:
                    if row['state']=='published' or (row['state']=='needs_review' and row['digest']):
                        with VerifiedFile(target) as destination:
                            self._check_identity(destination)
                            if destination.digest(cancelled)!=row['digest']:raise ValueError('目标改变，保留现场')
                            if source.exists():
                                with VerifiedFile(source,mutable=True) as original:
                                    self._check_identity(original)
                                    if (original.info.st_size,original.info.st_mtime_ns)!=(row['size'],row['mtime_ns']) or original.digest(cancelled)!=row['digest']:raise ValueError('源改变，两个文件保留')
                                    destination.verify();original.delete()
                            if row['asset_kind']=='image':self._catalog_move(row['image_id'],target,row['restore_root_id'])
                            self._state(row['id'],'committed')
                            if row['undo_of'] is not None:self._state(row['undo_of'],'undone')
                    else:self._state(row['id'],'needs_review' if target.exists() else 'failed',error='源保留，重新预览')
                except Cancelled:raise
                except Exception as error:self._state(row['id'],'needs_review',error=type(error).__name__)
                temp=Path(row['temp_path'])
                if temp.name.startswith('.luma-') and temp.name.endswith('.tmp') and temp.parent==target.parent and temp.exists():
                    try:
                        with VerifiedFile(temp,mutable=True) as leftover:self._check_identity(leftover);leftover.delete()
                    except (OSError,ValueError):pass
        with self.store.connect() as read:
            cursor=read.execute(PENDING_MOVE_BUNDLES_SQL+' ORDER BY p.id')
            while batch:=cursor.fetchmany(100):
                for item in batch:
                    if cancelled():raise Cancelled()
                    try:
                        with self.store.connect() as db:proof=db.execute("SELECT digest FROM journal WHERE job_id=? AND image_id=? AND asset_kind='image' AND state='committed' ORDER BY id DESC LIMIT 1",(item['job_id'],item['image_id'])).fetchone()
                        with VerifiedFile(item['destination']) as image:
                            self._check_identity(image)
                            if not proof or image.digest(cancelled)!=proof['digest']:raise ValueError('已移动图片改变，侧车保留')
                            for side in json.loads(item['companions']):
                                source,target=Path(side['source']),Path(side['destination'])
                                if source.exists() and not target.exists():self.move(item['job_id'],item['image_id'],source,target,side['size'],side['mtime_ns'],cancelled,asset_kind='sidecar')
                                elif not source.exists() and target.exists():
                                    with self.store.connect() as db:side_proof=db.execute("SELECT digest FROM journal WHERE job_id=? AND image_id=? AND asset_kind='sidecar' AND destination=? AND state='committed' ORDER BY id DESC LIMIT 1",(item['job_id'],item['image_id'],str(target))).fetchone()
                                    with VerifiedFile(target) as existing:
                                        self._check_identity(existing)
                                        if not side_proof or existing.digest(cancelled)!=side_proof['digest']:raise ValueError('侧车目标改变或缺少日志，保留现场')
                                else:raise ValueError('侧车位置冲突，保留现场')
                        state,reason='moved','恢复检查完成'
                    except Cancelled:raise
                    except Exception as error:state,reason='ready',type(error).__name__
                    with self.store.connect() as db:db.execute('UPDATE plan_items SET status=?,reason=? WHERE id=?',(state,reason,item['id']))

        # An undo can commit its image before its companion assets start.
        with self.store.connect() as read:
            cursor=read.execute(PENDING_UNDO_SIDES_SQL+' ORDER BY s.id')
            while batch:=cursor.fetchmany(100):
                for side in batch:
                    if cancelled():raise Cancelled()
                    try:
                        with VerifiedFile(side['restored_image']) as image:
                            self._check_identity(image)
                            if image.digest(cancelled)!=side['restored_digest']:raise ValueError('已还原图片改变，保留侧车')
                            with VerifiedFile(side['destination']) as original:
                                self._check_identity(original)
                                if original.digest(cancelled)!=side['digest']:raise ValueError('侧车改变，保留现场')
                                info=original.info
                            self.move(side['undo_job_id'],side['image_id'],Path(side['destination']),Path(side['source']),info.st_size,info.st_mtime_ns,cancelled,side['source_root_id'],'sidecar',side['id'])
                    except Cancelled:raise
                    except Exception as error:
                        with self.store.connect() as db:db.execute('UPDATE journal SET error=?,updated_at=? WHERE id=?',(type(error).__name__,time.time(),side['id']))
