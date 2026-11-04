"""Exact checkpoint identity mappings; no image reads or online model lookup."""
import json
import re
from pathlib import PureWindowsPath
from .model_catalog import catalog_entries, catalog_status, normalize_repository_id

FAMILIES=("Anima","Stable Diffusion","Krea 2","FLUX","NovelAI")


def model_name(value):
    name=PureWindowsPath(str(value).replace("/","\\")).name.casefold().strip()
    for suffix in (".safetensors",".ckpt",".gguf",".pt",".bin"):
        if name.endswith(suffix): return name[:-len(suffix)]
    return name


def checkpoint_identity(value):
    """Repository namespaces are identities; weight paths use their basename."""
    value=str(value).strip()
    repository=normalize_repository_id(value)
    if repository and not value.casefold().endswith((".safetensors",".ckpt",".gguf",".pt",".bin")):
        return ("repo",repository)
    return ("name",model_name(value))


def model_hashes(parameters):
    """Only checkpoint hash fields; image/LoRA hashes provide no model evidence."""
    normalized={str(key).casefold().replace(" ","_"):value for key,value in parameters.items()}
    values=[normalized[key] for key in ("model_hash","checkpoint_hash","model_sha256","checkpoint_sha256") if key in normalized]
    if isinstance(normalized.get("checkpoint_hashes"),list):
        values.extend(normalized["checkpoint_hashes"][:64])
    hashes=normalized.get("hashes")
    if isinstance(hashes,str):
        try: hashes=json.loads(hashes)
        except (ValueError,TypeError): hashes=None
    if isinstance(hashes,dict):
        values.extend(value for key,value in hashes.items() if str(key).casefold() in ("model","checkpoint"))
    return list(dict.fromkeys(str(value).casefold() for value in values if re.fullmatch(r"[0-9a-fA-F]{10,64}",str(value))))


class ModelRegistry:
    def __init__(self,store):
        self.store=store
        # Load the tiny shipped identity directory once, before a cached-only
        # task starts. No original-image I/O is introduced by classification.
        catalog_entries()
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS model_mappings(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,match TEXT NOT NULL,family TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',enabled INTEGER NOT NULL DEFAULT 1,UNIQUE(kind,match))")

    def list(self):
        with self.store.connect() as db:
            return [{**dict(row),"enabled":bool(row["enabled"])} for row in db.execute("SELECT * FROM model_mappings ORDER BY id")]

    @staticmethod
    def catalog_snapshot():
        return {
            "version": catalog_status().get("version", "unavailable"),
            "entries": [dict(entry) for entry in catalog_entries()],
        }

    def combined_catalog_snapshot(self):
        """Explicit caller snapshot; only reads verified cache, never weights."""
        from .model_library import ModelLibrary
        builtin=self.catalog_snapshot()
        remote=ModelLibrary(self.store).catalog_entries()
        return {'version':builtin['version']+('+civitai-cache' if remote else ''),'entries':builtin['entries']+remote}

    def replace(self,entries):
        if not isinstance(entries,list) or len(entries)>1000: raise ValueError("模型映射最多1000条")
        result=[];seen=set()
        for item in entries:
            if not isinstance(item,dict): raise ValueError("模型映射格式无效")
            kind,match,family=item.get("kind"),item.get("match"),item.get("family")
            if kind not in ("name","hash") or not isinstance(match,str) or not match.strip() or len(match)>1024 or family not in FAMILIES:
                raise ValueError("请填写精确模型名称或有效模型哈希，并选择已支持的家族")
            match=model_name(match) if kind=="name" else match.strip().casefold()
            if not match or (kind=="hash" and not re.fullmatch(r"[0-9a-f]{10,64}",match)): raise ValueError("模型哈希应为10至64位十六进制；名称须为精确checkpoint名称")
            note=item.get("note","")
            if not isinstance(note,str) or len(note)>500 or not isinstance(item.get("enabled",True),bool): raise ValueError("模型映射备注或启用状态无效")
            if (kind,match) in seen: raise ValueError("同一模型名称或哈希不能重复登记")
            seen.add((kind,match))
            result.append((kind,match,family,note.strip(),int(item.get("enabled",True))))
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM model_mappings")
            db.executemany("INSERT INTO model_mappings(kind,match,family,note,enabled) VALUES(?,?,?,?,?)",result)
        return self.list()

    @staticmethod
    def resolve(image,baseline,entries,*,catalog=None):
        params=image.get("parameters") or {}
        if params.get("ai") is False or params.get("is_ai") is False or baseline.get("family")=="非 AI": return baseline
        unresolved = baseline.get("conflict_kind") == "unresolved_checkpoints"
        if baseline.get("reason_code")=="conflict" and not unresolved: return baseline
        normalized={str(key).casefold().replace(" ","_"):value for key,value in params.items()}
        resource_types=" ".join(str(normalized.get(key,"")) for key in ("type","resource_type","model_type")).casefold()
        if any(word in resource_types for word in ("lora","lycoris","hypernet","controlnet","upscal","vae")):
            return {"family":"未知","confidence":0.0,"reason":"元数据明确描述辅助模型，不能将其名称或哈希登记结果作为图片生成 checkpoint 的证据。","reason_code":"conflict"}
        user_entries=[entry for entry in entries if entry.get("enabled",True)]
        builtin_entries=list(catalog_entries() if catalog is None else catalog)
        hashes=model_hashes(params)
        candidates=params.get("model_candidates") or [image.get("model","")]
        if not isinstance(candidates,list): candidates=[image.get("model","")]
        candidates=list(dict.fromkeys(str(name).strip() for name in candidates[:64] if name))
        names=list(dict.fromkeys(checkpoint_identity(name) for name in candidates))
        # A published filename is weaker than a digest. When metadata provides
        # a different digest, that published name cannot identify this file.
        def eligible(entry):
            expected=entry.get("checkpoint_sha256", ())
            return entry.get("kind") == "hash" or not hashes or not expected or all(
                any(value.startswith(digest) or digest.startswith(value) for digest in expected)
                for value in hashes
            )
        builtin_entries=[entry for entry in builtin_entries if eligible(entry)]
        entries=user_entries+builtin_entries
        matched_entries=[]
        mapped=[]
        for identity_kind,name in names:
            identity=(identity_kind,name)
            raw_candidates=[value for value in candidates if checkpoint_identity(value)==identity]
            hits=[entry for entry in entries if
                  (entry["kind"]==identity_kind and entry["match"]==name)]
            # Explicitly identified candidates can accompany a registered fine
            # tune. This never consults prompts, filenames of images or pixels.
            if unresolved:
                from .metadata import classify_model
                ancestry=params.get("checkpoint_ancestry",[])
                evidence=[item for item in ancestry[:64] if isinstance(item,dict)
                          and item.get("kind") in ("base","architecture")
                          and checkpoint_identity(item.get("checkpoint",""))==identity] if isinstance(ancestry,list) else []
                explicit=classify_model(raw_candidates[0],metadata={"model_evidence":evidence})
                if explicit.get("reason_code")=="conflict":
                    return explicit
                if explicit.get("reason_code")=="explicit" and explicit.get("family") in FAMILIES:
                    hits.append({"kind":"explicit", "family":explicit["family"],"origin":"explicit", "model_variant":explicit.get("model_variant","")})
            matches={entry["family"] for entry in hits}
            if len(matches)>1: return {"family":"未知","confidence":0.0,"reason":"精确模型名称的官方目录或用户登记存在冲突，请核对映射。","reason_code":"conflict"}
            mapped.append(matches)
            matched_entries.extend(hits)
        # A short hash and its full digest are one piece of evidence. Distinct
        # incompatible digests must each be accounted for; a mapping for only
        # one checkpoint never resolves the other checkpoint(s) implicitly.
        hash_groups=[value for value in hashes if not any(other!=value and other.startswith(value) for other in hashes)]
        hash_entries=[entry for entry in entries if entry["kind"]=="hash"]
        compatible=lambda value,entry:value.startswith(entry["match"]) or entry["match"].startswith(value)
        ambiguous_entries=[entry for entry in hash_entries if sum(compatible(value,entry) for value in hash_groups)>1]
        hash_entries=[entry for entry in hash_entries if entry not in ambiguous_entries]
        # Even identical family labels cannot establish which full checkpoint
        # digest a colliding prefix denotes. Keep compatible prefix/full chains.
        for value in hash_groups:
            identities=[entry["match"] for entry in hash_entries if compatible(value,entry)]
            leaves={digest for digest in identities if not any(other!=digest and other.startswith(digest) for other in identities)}
            if len(leaves)>1:
                return {"family":"未知","confidence":0.0,"reason":"checkpoint 短哈希同时匹配多个不同完整哈希，身份存在歧义，请核对完整哈希。","reason_code":"conflict"}
        hash_group_matches=[{entry["family"] for entry in hash_entries if compatible(value,entry)} for value in hash_groups]
        matched_entries.extend(entry for entry in hash_entries if any(compatible(value,entry) for value in hash_groups))
        hash_matches=set().union(*hash_group_matches) if hash_group_matches else set()
        families=set().union(*mapped,hash_matches) if mapped or hash_matches else set()
        if len(hash_groups)>1 and (hash_matches or ambiguous_entries or any(mapped)) and any(not matches for matches in hash_group_matches):
            # An existing explicit architecture/name decision can stand on its
            # own evidence, but incomplete registrations cannot upgrade it.
            if baseline.get("reason_code")=="explicit" and baseline.get("family") in FAMILIES and families.issubset({baseline["family"]}):
                return baseline
            return {"family":"未知","confidence":0.0,"reason":"出现多个不兼容的 checkpoint 哈希，部分哈希未登记，不能忽略其中的模型证据。","reason_code":"conflict"}
        if not families: return baseline
        known=baseline.get("family")
        if known in FAMILIES: families.add(known)
        if len(families)!=1:
            return {"family":"未知","confidence":0.0,"reason":"已读取模型证据与用户登记映射冲突，未自动确定家族。","reason_code":"conflict"}
        # A single checkpoint hash cannot resolve a graph containing several
        # different checkpoints with unknown names.
        if len(names)>1 and any(not match for match in mapped): return baseline
        if not hash_matches and any(not match for match in mapped): return baseline
        variants={entry.get("model_variant", "") for entry in matched_entries if entry.get("model_variant")}
        if baseline.get("model_variant"):
            variants.add(baseline["model_variant"])
        variant_groups={value[:3] if value.startswith(("SD1","SD2","SD3")) else value for value in variants}
        if len(variant_groups)>1:
            return {"family":"未知","confidence":0.0,"reason":"模型身份目录/用户登记与明确架构的 SD 大版本不同，不能自动确定归属。","reason_code":"conflict"}
        builtin_hits=[entry for entry in matched_entries if entry.get("origin") in ("builtin","civitai")]
        user_hits=[entry for entry in matched_entries if entry.get("origin") not in ("builtin","civitai","explicit")]
        result={"family":next(iter(families)),"confidence":1.0,"reason":"依据用户登记的精确模型名称/模型哈希映射；由用户确认归属，未根据画面或提示词猜测。","reason_code":"registered"}
        if builtin_hits:
            builtin_hash_hits=any(entry["kind"]=="hash" for entry in builtin_hits)
            labels=list(dict.fromkeys(entry.get("display_name") or entry.get("catalog_id", "") for entry in builtin_hits))
            version=builtin_hits[0].get("catalog_version", "")
            source_urls=list(dict.fromkeys(url for entry in builtin_hits for url in entry.get("source_urls", ())))[:12]
            source_label="本机 Civitai 资料缓存 " if any(entry.get('origin')=='civitai' for entry in builtin_hits) else "离线模型身份目录 "
            result.update(reason_code="catalog" if not user_hits else "registered", confidence=.98 if builtin_hash_hits else .9,
                          reason=source_label+version+" 精确匹配 "+"、".join(labels[:4])+"（"+("checkpoint 哈希" if builtin_hash_hits else "已发布模型名称/完整标识")+"）；来源可核对，未根据提示词或画面猜测。"+("同时核对用户登记。" if user_hits else ""),
                          model_catalog_version=version,model_catalog_sources=source_urls)
        if variants:
            result["model_variant"]=max(variants,key=len)
        return result
