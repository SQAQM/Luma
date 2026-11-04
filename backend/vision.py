"""Explicit, offline CLIP content suggestions, independent of catalog metadata.

No constructor/classification call downloads anything. ``install`` is the only
network operation and retrieves fixed public artifacts, never image data.
CLIP cosine similarity is not a probability. The default rejection thresholds
are conservative heuristics, not a production accuracy calibration.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from .organizer import check_directory_chain, reparse_point, signature


MODEL_REPOSITORY = "Xenova/clip-vit-base-patch32"
MODEL_REVISION = "d15189d7028b43f1d3e65039190477f6af591c2a"
MODEL_ID = f"{MODEL_REPOSITORY}@{MODEL_REVISION}:int8"
MAX_DEFINITIONS = 128
MAX_DESCRIPTION_LENGTH = 500
DEFAULT_THRESHOLD = 0.28
MAX_IMAGE_PIXELS = 50_000_000
MAX_IMAGE_BYTES = 1024 * 1024 * 1024
TEXT_BATCH_SIZE = 16
_WORK_SLOTS = threading.BoundedSemaphore(2)


@dataclass(frozen=True)
class ModelAsset:
    remote: str
    local: str
    size: int
    sha256: str


MODEL_ASSETS = (
    ModelAsset("onnx/text_model_quantized.onnx", "text_model.onnx", 64_504_507,
               "73baab855d406190da9faa498cfedf65f15cf309f4cc7385b7b032e6d08e5c3a"),
    ModelAsset("onnx/vision_model_quantized.onnx", "vision_model.onnx", 89_117_001,
               "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299"),
    ModelAsset("tokenizer.json", "tokenizer.json", 2_224_119,
               "f7f3b7af117d467b58374797691a6438d3e6b9e9cef800dfd5dced7f697a90cd"),
)
DOWNLOAD_BYTES = sum(asset.size for asset in MODEL_ASSETS)
# These anchors let the classifier abstain instead of always choosing a label.
# They do not make the model calibrated, and custom taxonomies still need review.
BACKGROUND_DESCRIPTIONS = (
    "an image with unclear or ambiguous content",
    "a blank image with a solid color",
    "an abstract pattern or texture",
    "an image of text, a document or a screenshot",
)
DEFAULT_DEFINITIONS = (
    {"id": "landscape", "name": "风景", "description": "a scenic landscape with mountains, forests, fields or a lake", "threshold": 0.28},
    {"id": "portrait", "name": "人像", "description": "a portrait of a person with a visible face", "threshold": 0.28},
    {"id": "animal", "name": "动物", "description": "an image of an animal, a bird or a pet", "threshold": 0.28},
    {"id": "architecture", "name": "建筑", "description": "an image of buildings, architecture or a city street", "threshold": 0.28},
    {"id": "food", "name": "美食", "description": "an image of food, a meal or a drink", "threshold": 0.28},
    {"id": "illustration", "name": "插画", "description": "a drawing, illustration or anime artwork", "threshold": 0.28},
)


class VisionError(ValueError):
    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


class VisionCancelled(VisionError):
    def __init__(self):
        super().__init__("内容识别或模型下载已取消", "cancelled")


def _cancel(cancelled):
    if cancelled and cancelled():
        raise VisionCancelled()


def normalize_definitions(definitions):
    if not isinstance(definitions, (list, tuple)) or not 1 <= len(definitions) <= MAX_DEFINITIONS:
        raise VisionError(f"请选择 1 到 {MAX_DEFINITIONS} 个内容 TAG")
    output, ids = [], set()
    for item in definitions:
        if not isinstance(item, dict):
            raise VisionError("内容 TAG 定义格式无效")
        if item.get("enabled", True) is False or item.get("enabled") == 0:
            continue
        identity, name, description = item.get("id"), item.get("name"), item.get("description")
        valid_id=(isinstance(identity,int) and not isinstance(identity,bool) and identity>0) or (isinstance(identity,str) and 1<=len(identity.strip())<=100)
        if not valid_id or identity in ids:
            raise VisionError("内容 TAG ID 无效或重复")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise VisionError("内容 TAG 名称需要 1 到 100 个字符")
        if (not isinstance(description, str) or not 5 <= len(description.strip()) <= MAX_DESCRIPTION_LENGTH
                or not re.search(r"[A-Za-z]", description) or re.search(r"[\u3400-\u9fff]", description)):
            raise VisionError(f"请为内容 TAG 提供不超过 {MAX_DESCRIPTION_LENGTH} 字符的英文描述")
        threshold = item.get("threshold", DEFAULT_THRESHOLD)
        if isinstance(threshold, bool) or not isinstance(threshold, (float, int)) or not math.isfinite(threshold) or not 0.20 <= threshold <= 0.90:
            raise VisionError("匹配阈值必须是 0.20 到 0.90 之间的有限相似度数值")
        ids.add(identity)
        output.append({"id": identity, "name": name.strip(), "description": description.strip(), "threshold": float(threshold)})
    if not output:
        raise VisionError("没有启用的内容 TAG")
    return output


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Model URL redirects stay on the provider's public HTTPS CDN."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
                or not any(host == domain or host.endswith("." + domain) for domain in ("huggingface.co", "hf.co", "xethub.hf.co"))):
            raise VisionError("模型下载重定向离开了公开模型服务器", "download_host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class LocalContentClassifier:
    """One pair of CPU sessions, two concurrent workers, bounded text cache.

    The caller supplies an approved image path and streams catalog rows. This
    module never scans a folder or reads the default data directory/database.
    ``status`` is lightweight: full SHA256 verification is mandatory before
    loading ONNX and whenever a verified model file signature changes.
    """
    def __init__(self, modeldir: str | Path):
        chosen = Path(modeldir).expanduser()
        if not chosen.is_absolute():
            raise VisionError("模型缓存目录必须是绝对路径", "unsafe")
        # Keep the literal path until chain checks; resolving first hides links.
        self.directory = Path(os.path.abspath(chosen))
        self._lock = threading.RLock()
        self._install_lock = threading.Lock()
        self._verified_signatures = None
        self._failed_signatures = None
        self._session_signatures = None
        self._text_session = self._vision_session = self._tokenizer = None
        self._text_cache = OrderedDict()

    def _check_chain(self):
        try:
            check_directory_chain(self.directory)
            if self.directory.exists() and not self.directory.is_dir():
                raise ValueError("not a directory")
        except (OSError, ValueError) as exc:
            raise VisionError("模型缓存路径包含链接、重解析点或不可用目录", "unsafe") from exc

    def _signatures(self):
        self._check_chain()
        values = []
        for asset in MODEL_ASSETS:
            path = self.directory / asset.local
            try:
                info = signature(path)
            except FileNotFoundError:
                return None
            except (OSError, ValueError) as exc:
                raise VisionError("模型文件路径不是安全的普通文件", "unsafe") from exc
            if info.st_size != asset.size:
                return None
            values.append((info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_dev, info.st_ino))
        return tuple(values)

    @staticmethod
    def _runtime_available():
        return all(importlib.util.find_spec(name) is not None for name in ("onnxruntime", "tokenizers", "numpy"))

    def status(self):
        status, message, signatures = "missing", "尚未下载本地内容识别模型", None
        try:
            signatures = self._signatures()
            if signatures:
                status, message = "ready", "模型文件已下载；分析前会校验完整 SHA256"
                if signatures == self._failed_signatures:
                    status,message="integrity_failed","模型校验失败，请主动重新下载；未读取任何图片"
            elif self.directory.exists() and any((self.directory / asset.local).exists() for asset in MODEL_ASSETS):
                status, message = "incomplete", "模型文件缺失或不完整，请重新下载"
        except VisionError as exc:
            status, message = "unsafe", str(exc)
        runtime = self._runtime_available()
        if status == "ready" and not runtime:
            status, message = "runtime_missing", "缺少本地 ONNX 推理运行库，请重新安装软件依赖"
        return {"ready": status == "ready", "status": status, "model_id": MODEL_ID,
                "model_revision": MODEL_REVISION, "download_bytes": DOWNLOAD_BYTES,
                "download_mib": round(DOWNLOAD_BYTES / 1048576, 2), "runtime_available": runtime,
                "integrity": "verified" if signatures and signatures == self._verified_signatures else "pending",
                "message": message, "score_kind": "cosine_similarity", "accuracy_calibrated": False}

    @staticmethod
    def _hash(path, cancelled=None):
        before = signature(path)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino, before.st_size) != (opened.st_dev, opened.st_ino, opened.st_size):
                raise VisionError("模型文件在校验时发生变更", "integrity")
            while block := stream.read(1024 * 1024):
                _cancel(cancelled)
                digest.update(block)
        after = signature(path)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise VisionError("模型文件在校验时发生变更", "integrity")
        return digest.hexdigest()

    def _verify(self, cancelled=None):
        signatures = self._signatures()
        if not signatures:
            raise VisionError("请先主动下载完整的本地内容识别模型", "not_ready")
        if signatures != self._verified_signatures:
            for asset in MODEL_ASSETS:
                _cancel(cancelled)
                if self._hash(self.directory / asset.local, cancelled) != asset.sha256:
                    self._verified_signatures = None
                    self._failed_signatures = signatures
                    raise VisionError("模型 SHA256 校验不符，请重新下载，未读取任何图片", "integrity")
            if self._signatures() != signatures:
                raise VisionError("模型文件在校验期间发生变更", "integrity")
            self._verified_signatures = signatures
            self._failed_signatures = None
        return signatures

    def install(self, progress: Callable | None = None, cancelled: Callable | None = None):
        _cancel(cancelled)
        if not self._install_lock.acquire(blocking=False):
            raise VisionError("模型已经在下载，请等待或取消当前任务", "busy")
        try:
            self._check_chain()
            self.directory.mkdir(parents=True, exist_ok=True)
            self._check_chain()
            downloaded = 0
            opener = urllib.request.build_opener(_PublicRedirectHandler())
            for asset in MODEL_ASSETS:
                _cancel(cancelled)
                target = self.directory / asset.local
                try:
                    existing = signature(target)
                except FileNotFoundError:
                    existing = None
                except (OSError, ValueError) as exc:
                    raise VisionError("模型目标路径不是安全的普通文件", "unsafe") from exc
                if existing and existing.st_size == asset.size and self._hash(target, cancelled) == asset.sha256:
                    downloaded += asset.size
                    if progress:
                        progress({"downloaded": downloaded, "total": DOWNLOAD_BYTES, "file": asset.local, "message": "已有模型文件校验通过"})
                    continue
                temporary = None
                try:
                    self._check_chain()
                    descriptor, filename = tempfile.mkstemp(prefix=".luma-download-", suffix=".part", dir=self.directory)
                    temporary = Path(filename)
                    with os.fdopen(descriptor, "wb") as destination:
                        url = f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}/{asset.remote}"
                        request = urllib.request.Request(url, headers={"User-Agent": "Luma-Public-Model-Downloader/0.3", "Accept-Encoding": "identity"})
                        with opener.open(request, timeout=20) as response:
                            digest, received = hashlib.sha256(), 0
                            while True:
                                _cancel(cancelled)
                                block = response.read(1024 * 1024)
                                if not block:
                                    break
                                received += len(block)
                                if received > asset.size:
                                    raise VisionError("公开模型下载大小超过固定清单", "integrity")
                                destination.write(block)
                                digest.update(block)
                                if progress:
                                    progress({"downloaded": downloaded+received, "total": DOWNLOAD_BYTES, "file": asset.local, "message": "正在下载公开模型，图片不会上传"})
                        if received != asset.size or digest.hexdigest() != asset.sha256:
                            raise VisionError("公开模型下载不完整或 SHA256 不符", "integrity")
                        destination.flush()
                        os.fsync(destination.fileno())
                    _cancel(cancelled)
                    self._check_chain()
                    if reparse_point(target):
                        raise VisionError("模型目标路径被替换为链接，停止发布", "unsafe")
                    signature(temporary)
                    os.replace(temporary, target)
                    temporary = None
                    downloaded += asset.size
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    # Do not expose signed CDN URLs, credentials or filesystem
                    # paths in errors/logs. The UI can retry explicitly.
                    raise VisionError("公开模型下载失败，请检查网络或缓存磁盘后重试", "download_failed") from exc
                finally:
                    if temporary is not None:
                        try:
                            self._check_chain()
                            if not reparse_point(temporary):
                                temporary.unlink(missing_ok=True)
                        except (OSError, VisionError):
                            pass
            with self._lock:
                self._verify(cancelled)
            return self.status()
        finally:
            self._install_lock.release()

    @contextmanager
    def _slot(self, cancelled):
        while not _WORK_SLOTS.acquire(timeout=0.1):
            _cancel(cancelled)
        try:
            _cancel(cancelled)
            yield
        finally:
            _WORK_SLOTS.release()

    def _sessions(self, cancelled):
        with self._lock:
            signatures = self._verify(cancelled)
            if self._session_signatures == signatures:
                return
            try:
                import onnxruntime as ort
                ort.disable_telemetry_events()
                from tokenizers import Tokenizer
            except ImportError as exc:
                raise VisionError("缺少本地 ONNX 推理运行库", "runtime_missing") from exc
            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            options.log_severity_level = 3
            options.enable_mem_pattern = False
            try:
                text = ort.InferenceSession(str(self.directory / "text_model.onnx"), options, providers=["CPUExecutionProvider"])
                _cancel(cancelled)
                vision = ort.InferenceSession(str(self.directory / "vision_model.onnx"), options, providers=["CPUExecutionProvider"])
                tokenizer = Tokenizer.from_file(str(self.directory / "tokenizer.json"))
                tokenizer.enable_truncation(max_length=77)
                tokenizer.enable_padding(length=77, pad_id=49407, pad_token="<|endoftext|>")
                if {item.name for item in text.get_inputs()} - {"input_ids", "attention_mask"} or {item.name for item in vision.get_inputs()} != {"pixel_values"}:
                    raise VisionError("固定 ONNX 模型接口与预期不符", "model_schema")
                if "text_embeds" not in {item.name for item in text.get_outputs()} or "image_embeds" not in {item.name for item in vision.get_outputs()}:
                    raise VisionError("固定 ONNX 输出接口与预期不符", "model_schema")
                if self._signatures() != signatures:
                    raise VisionError("模型在加载时发生变更", "integrity")
            except VisionError:
                raise
            except Exception as exc:
                raise VisionError("本地模型加载失败，请检查安装文件与运行库", "model_load") from exc
            self._text_session, self._vision_session, self._tokenizer = text, vision, tokenizer
            self._session_signatures = signatures
            self._text_cache.clear()

    def _text_features(self, definitions, cancelled):
        import numpy as np
        # Display names/thresholds never change or influence model text vectors.
        descriptions = [item["description"] for item in definitions] + list(BACKGROUND_DESCRIPTIONS)
        key = hashlib.sha256(json.dumps(descriptions, ensure_ascii=True).encode()).hexdigest()
        with self._lock:
            if key in self._text_cache:
                self._text_cache.move_to_end(key)
                return self._text_cache[key]
            vectors = []
            for offset in range(0, len(descriptions), TEXT_BATCH_SIZE):
                _cancel(cancelled)
                encoded = self._tokenizer.encode_batch(descriptions[offset:offset+TEXT_BATCH_SIZE])
                values = {"input_ids": np.asarray([item.ids for item in encoded], dtype=np.int64),
                          "attention_mask": np.asarray([item.attention_mask for item in encoded], dtype=np.int64)}
                feed = {item.name: values[item.name] for item in self._text_session.get_inputs()}
                result = self._text_session.run(["text_embeds"], feed)[0]
                if result.shape != (len(encoded), 512) or not np.isfinite(result).all():
                    raise VisionError("文本向量输出无效", "inference")
                vectors.append(result / np.maximum(np.linalg.norm(result, axis=-1, keepdims=True), 1e-12))
            combined = np.concatenate(vectors, axis=0)
            self._text_cache[key] = combined
            while len(self._text_cache) > 8:
                self._text_cache.popitem(last=False)
            return combined

    @staticmethod
    def _pixels(path, cancelled):
        import numpy as np
        _cancel(cancelled)
        selected = Path(path).expanduser()
        if not selected.is_absolute():
            raise VisionError("待识别图片必须使用已批准的绝对路径", "image_path")
        try:
            before = signature(selected)
            if before.st_size > MAX_IMAGE_BYTES:
                raise VisionError("图片文件超过 1 GiB，内容识别暂不支持", "image_limit")
            with selected.open("rb") as stream:
                opened = os.fstat(stream.fileno())
                if (before.st_dev, before.st_ino, before.st_size) != (opened.st_dev, opened.st_ino, opened.st_size):
                    raise VisionError("待识别图片在打开时发生变更", "image_changed")
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(stream) as image:
                        if image.width < 1 or image.height < 1 or image.width * image.height > MAX_IMAGE_PIXELS:
                            raise VisionError("图片超过 5000 万像素，内容识别暂不支持", "image_limit")
                        _cancel(cancelled)
                        image.draft("RGB", (1024,1024))
                        oriented = ImageOps.exif_transpose(image)
                        # Crop before resize keeps extreme aspect ratios bounded.
                        # CLIP uses an aspect-preserving 224px central crop.
                        with ImageOps.fit(oriented, (224,224), method=Image.Resampling.BICUBIC, centering=(0.5,0.5)) as small:
                            rgb = small.convert("RGB")
                            pixels = np.asarray(rgb, dtype=np.float32).copy() / 255.0
                            rgb.close()
                        if oriented is not image:
                            oriented.close()
            after = signature(selected)
            if (before.st_size,before.st_mtime_ns,before.st_ctime_ns) != (after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                raise VisionError("待识别图片在解码时发生变更", "image_changed")
        except VisionError:
            raise
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise VisionError("图片无法安全解码，已跳过", "image_decode") from exc
        _cancel(cancelled)
        mean = np.array([0.48145466,0.4578275,0.40821073], dtype=np.float32)
        std = np.array([0.26862954,0.26130258,0.27577711], dtype=np.float32)
        return np.ascontiguousarray(((pixels-mean)/std).transpose(2,0,1)[None], dtype=np.float32)

    def classify(self, path, definitions, cancelled=None):
        _cancel(cancelled)
        normalized = normalize_definitions(definitions)
        with self._slot(cancelled):
            self._sessions(cancelled)
            import numpy as np
            vectors = self._text_features(normalized, cancelled)
            pixels = self._pixels(path, cancelled)
            _cancel(cancelled)
            try:
                output = self._vision_session.run(["image_embeds"], {"pixel_values": pixels})[0]
            except Exception as exc:
                raise VisionError("本地图片推理失败，已跳过", "inference") from exc
            _cancel(cancelled)
            if output.shape != (1,512) or not np.isfinite(output).all():
                raise VisionError("图片向量输出无效", "inference")
            vector = output[0] / max(float(np.linalg.norm(output[0])),1e-12)
            similarities = np.clip(vectors @ vector,-1.0,1.0)
            scores = [{"tag_id":item["id"], "name":item["name"], "score":float(similarities[index])}
                      for index,item in enumerate(normalized)]
            background = float(max(similarities[len(normalized):]))
            best = max(item["score"] for item in scores)
            matches = [item for item,definition in zip(scores,normalized)
                       if item["score"] >= definition["threshold"] and item["score"] >= background+0.02 and item["score"] >= best-0.08]
            matches.sort(key=lambda item:item["score"], reverse=True)
            return {"model_id":MODEL_ID,"model_revision":MODEL_REVISION,"matches":matches,
                    "scores":scores,"unknown":not matches,"score_kind":"cosine_similarity",
                    "background_score":background,"accuracy_calibrated":False}
