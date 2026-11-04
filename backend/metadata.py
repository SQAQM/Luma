"""Bounded, provenance-based image metadata extraction.

No pixel raster is decoded. PNG/WebP use container readers because some Pillow
plugins otherwise buffer the entire file; other codecs get an 8 MiB read budget.
PNG text after IDAT is supported by seeking over pixel chunks. Oversized metadata
is omitted, never silently promoted to classification evidence. Corrupt/unsupported
headers raise ValueError; pixel corruption is intentionally not validated here.

Checkpoint names are evidence, not a registry of fine-tune ancestry. Unknown names
and missing provenance stay unknown. Prompt words, visual style, artist names,
LoRAs and hashes cannot establish a base family. AVIF support depends on Pillow's
installed codecs and container-read budget. Proprietary/stealth pixel metadata and
opaque custom ComfyUI widget orders are outside this reader's current scope.
Graph inputs with explicit checkpoint/architecture semantics are supported; node
titles, text encoders, LoRA resources and arbitrary JSON captions are not ancestry.
"""

from __future__ import annotations

import io
import json
import math
import re
import struct
import zlib
from pathlib import Path, PureWindowsPath
from typing import Any

from PIL import Image, UnidentifiedImageError

MAX_TEXT_BYTES = 512 * 1024
MAX_RAW_BYTES = 1024 * 1024
MAX_HEADER_BYTES = 8 * 1024 * 1024
MAX_METADATA_ENTRIES = 128
MAX_NODES = 2000
MAX_TAGS = 2048
MAX_EVIDENCE = 64
MAX_JSON_VISITS = 256
SUPPORTED_FORMATS = ("PNG", "JPEG", "WEBP", "TIFF", "GIF", "BMP", "AVIF", "ICO")


class _BudgetReader:
    """Seekable wrapper limiting actual I/O, including unbounded plugin reads."""

    def __init__(self, stream: Any):
        self.stream = stream
        self.remaining = MAX_HEADER_BYTES

    def read(self, size: int = -1) -> bytes:
        if size < 0 or size > self.remaining:
            data = self.stream.read(self.remaining + 1)
        else:
            data = self.stream.read(size)
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ValueError("图片容器头部超过安全读取预算（8 MiB）")
        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        return self.stream.seek(offset, whence)

    def tell(self) -> int:
        return self.stream.tell()


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value[:MAX_TEXT_BYTES].replace("\x00", "").strip()
    if isinstance(value, bytes):
        return value[:MAX_TEXT_BYTES].decode("utf-8", "replace").replace("\x00", "").strip()
    return ""


def _key(value: Any) -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(value))
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


_MODEL_FIELDS = ("model", "model_name", "checkpoint", "checkpoint_name", "ckpt_name", "ckpt",
                 "sd_model", "sd_model_checkpoint", "unet_name", "diffusion_model",
                 "diffusion_model_name", "model_path", "checkpoint_path")
_BASE_FIELDS = ("base_model", "base_family", "model_family", "base_model_type", "base_model_version")
_ARCH_FIELDS = ("architecture", "model_architecture", "model_type", "model_version", "prediction_model", "class_name")
_HASH_FIELDS = ("model_hash", "checkpoint_hash", "model_sha256", "checkpoint_sha256")
_WRAPPERS = ("metadata", "generation", "generation_data", "generation_info", "image_metadata", "sd_metadata",
             "settings", "parameters", "extra", "extra_pnginfo", "pnginfo", "data", "prompt", "workflow",
             "extra_metadata", "invokeai_metadata")


def _evidence(value: Any, source: str, kind: str) -> dict[str, str] | None:
    value = _text(value)
    if not value or len(value) > 1024:
        return None
    return {"value": value, "source": source[:160], "kind": kind}

def _auxiliary_record(fields: dict[str, Any]) -> bool:
    """Explicit resource types supersede family words in adapter metadata."""
    return any(
        any(word in re.sub(r"[^a-z0-9]", "", _text(fields.get(key)).casefold())
            for word in ("lora", "locon", "dora", "lycoris", "hypernet", "controlnet", "upscal", "vae", "embedding", "textualinversion", "clip", "ipadapter", "t2iadapter", "encoder"))
        for key in ("type", "resource_type", "model_type")
    )


def _safe_json(value: Any) -> Any:
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_TEXT_BYTES:
        return None
    try:
        def reject_constant(_: str) -> None:
            raise ValueError("非有限 JSON 数值")
        def finite_float(text: str) -> float:
            number = float(text)
            if not math.isfinite(number):
                raise ValueError("非有限 JSON 数值")
            return number
        return json.loads(value, parse_constant=reject_constant, parse_float=finite_float)
    except (ValueError, RecursionError):
        return None


def _checkpoint_identity(value: Any) -> str:
    """Compare generator aliases without reading paths or inventing ancestry."""
    # Preserve the whole path/repository namespace. A basename-only comparison
    # would attach another publisher's explicit ancestry to an unknown model.
    name = str(PureWindowsPath(_text(value).replace("/", "\\"))).casefold()
    return re.sub(r"\.(?:safetensors|ckpt|gguf|sft|pt|pth|bin)$", "", name)


def _checkpoint_air(value: Any, source: str) -> dict[str, Any] | None:
    """Strict typed AIR provenance, not family words in a resource filename.

    Official grammar: civitai/media-metadata src/civitai/air.ts. Require the
    complete prefix, ecosystem, checkpoint type, source and resource identity;
    shortened or auxiliary AIRs cannot establish checkpoint ancestry.
    """
    text = _text(value)
    match = re.fullmatch(r"urn:air:([a-zA-Z0-9_\-/]+):checkpoint:([a-zA-Z0-9_\-/]+):([a-zA-Z0-9_\-/.]+)(?:@([a-zA-Z0-9_\-/.]+))?(?:\+\d+)?", text, re.I)
    if not match:
        return None
    ecosystem = match.group(1)
    return {"name": text, "base_model": ecosystem, "source": source}


def _small_value(value: Any, depth: int = 0, omitted: list[bool] | None = None) -> Any:
    def mark() -> None:
        if omitted is not None:
            omitted[0] = True
    if depth > 6:
        mark()
        return "[嵌套过深]"
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (bytes, str)):
        if len(value) > MAX_TEXT_BYTES:
            mark()
        return _text(value)
    if isinstance(value, dict):
        if len(value) > MAX_METADATA_ENTRIES or any(len(str(k)) > 128 for k in value):
            mark()
        return {str(k)[:128]: _small_value(v, depth + 1, omitted)
                for k, v in list(value.items())[:MAX_METADATA_ENTRIES]}
    if isinstance(value, (list, tuple)):
        if len(value) > 64:
            mark()
        return [_small_value(item, depth + 1, omitted) for item in value[:64]]
    return str(value)[:256]


def _bound_raw(values: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    remaining = MAX_RAW_BYTES
    omitted = [len(values) > MAX_METADATA_ENTRIES]
    for key, value in list(values.items())[:MAX_METADATA_ENTRIES]:
        cleaned = _small_value(value, omitted=omitted)
        size = len(json.dumps(cleaned, ensure_ascii=False).encode("utf-8"))
        if size > remaining:
            output["metadata_truncated"] = True
            continue
        output[str(key)[:128]] = cleaned
        remaining -= size
    if omitted[0]:
        output["metadata_truncated"] = True
    return output


def _inflate(data: bytes) -> bytes | None:
    try:
        decoder = zlib.decompressobj()
        result = decoder.decompress(data, MAX_TEXT_BYTES + 1)
        if len(result) > MAX_TEXT_BYTES or decoder.unconsumed_tail or not decoder.eof:
            return None
        return result
    except zlib.error:
        return None


def _png_header(stream: Any, file_size: int) -> tuple[int, int, dict[str, Any]]:
    stream.seek(8)
    metadata: dict[str, Any] = {}
    width = height = 0
    text_budget = MAX_RAW_BYTES
    chunk_count = 0
    seen_iend = False
    seen_idat = False
    while stream.tell() + 12 <= file_size:
        chunk_count += 1
        if chunk_count > 100000:
            raise ValueError("PNG 块数量超过安全限制")
        header = stream.read(8)
        length, kind = struct.unpack(">I4s", header)
        if stream.tell() + length + 4 > file_size:
            raise ValueError("PNG 块长度损坏")
        checked_payload: bytes | None = None
        if kind == b"IHDR":
            if length != 13 or width or chunk_count != 1:
                raise ValueError("PNG 头部损坏")
            payload = stream.read(length)
            checked_payload = payload
            width, height = struct.unpack(">II", payload[:8])
            if payload[8] not in (1, 2, 4, 8, 16) or payload[9] not in (0, 2, 3, 4, 6) or payload[10:12] != b"\x00\x00" or payload[12] not in (0, 1):
                raise ValueError("PNG 头部编码无效")
        elif kind in (b"tEXt", b"zTXt", b"iTXt") and length <= MAX_TEXT_BYTES and text_budget > 0 and len(metadata) < MAX_METADATA_ENTRIES:
            payload = stream.read(length)
            checked_payload = payload
            key, separator, remainder = payload.partition(b"\x00")
            content: bytes | None = None
            if separator and 0 < len(key) <= 79:
                if kind == b"tEXt":
                    content = remainder
                elif kind == b"zTXt" and remainder[:1] == b"\x00":
                    content = _inflate(remainder[1:])
                elif kind == b"iTXt" and len(remainder) >= 2:
                    compressed, method = remainder[0], remainder[1]
                    rest = remainder[2:].split(b"\x00", 2)
                    if len(rest) == 3 and method == 0:
                        content = _inflate(rest[2]) if compressed == 1 else rest[2] if compressed == 0 else None
                if content is not None and len(content) <= min(MAX_TEXT_BYTES, text_budget):
                    encoding = "utf-8" if kind == b"iTXt" else "latin-1"
                    metadata[key.decode("latin-1")] = content.decode(encoding, "replace")
                    text_budget -= len(content)
                else:
                    metadata["metadata_truncated"] = True
            else:
                metadata["metadata_truncated"] = True
        elif kind == b"eXIf" and length <= min(MAX_TEXT_BYTES, text_budget) and len(metadata) < MAX_METADATA_ENTRIES:
            payload = stream.read(length)
            checked_payload = payload
            metadata.update(_exif_metadata(payload))
            text_budget -= length
        else:
            if kind in (b"tEXt", b"zTXt", b"iTXt", b"eXIf"):
                metadata["metadata_truncated"] = True
            stream.seek(length, io.SEEK_CUR)
        if kind == b"IDAT":
            seen_idat = True
        crc = stream.read(4)
        if checked_payload is not None and int.from_bytes(crc, "big") != zlib.crc32(kind + checked_payload):
            raise ValueError("PNG 头部或文本元数据校验失败")
        if kind == b"IEND":
            if length != 0:
                raise ValueError("PNG 结束块长度无效")
            seen_iend = True
            break
    if not width or not height or not seen_iend or not seen_idat:
        raise ValueError("PNG 头部或结束块缺失")
    return width, height, metadata


def _ico_header(stream: Any, file_size: int) -> tuple[int, int, dict[str, Any]]:
    # Pillow's ICO _open calls load(), so read the directory ourselves.
    stream.seek(4)
    count_data = stream.read(2)
    if len(count_data) != 2:
        raise ValueError("ICO 目录头部损坏")
    count = int.from_bytes(count_data, "little")
    if not count or 6 + 16 * count > file_size:
        raise ValueError("ICO 目录长度损坏")
    sizes: list[tuple[int, int]] = []
    for _ in range(count):
        entry = stream.read(16)
        length, offset = struct.unpack("<II", entry[8:16])
        if not length or offset < 6 + 16 * count or offset + length > file_size:
            raise ValueError("ICO 图片偏移损坏")
        sizes.append((entry[0] or 256, entry[1] or 256))
    width, height = max(sizes, key=lambda size: (size[0] * size[1], size[0]))
    return width, height, {}


def _webp_header(stream: Any, file_size: int) -> tuple[int, int, dict[str, Any]]:
    stream.seek(0)
    header = stream.read(12)
    declared = struct.unpack("<I", header[4:8])[0] + 8
    if declared > file_size or declared < 20:
        raise ValueError("WebP 容器长度损坏")
    width = height = 0
    metadata: dict[str, Any] = {}
    chunk_count = 0
    while stream.tell() + 8 <= declared:
        chunk_count += 1
        if chunk_count > 100000:
            raise ValueError("WebP 块数量超过安全限制")
        kind, length = struct.unpack("<4sI", stream.read(8))
        end = stream.tell() + length + (length & 1)
        if end > declared:
            raise ValueError("WebP 块长度损坏")
        if kind == b"VP8X" and length >= 10:
            payload = stream.read(10)
            width = 1 + int.from_bytes(payload[4:7], "little")
            height = 1 + int.from_bytes(payload[7:10], "little")
        elif kind == b"VP8 " and not width and length >= 10:
            payload = stream.read(10)
            if payload[3:6] == b"\x9d\x01\x2a":
                width = int.from_bytes(payload[6:8], "little") & 0x3FFF
                height = int.from_bytes(payload[8:10], "little") & 0x3FFF
        elif kind == b"VP8L" and not width and length >= 5:
            payload = stream.read(5)
            if payload[0] == 0x2F:
                bits = int.from_bytes(payload[1:5], "little")
                width, height = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        elif kind == b"EXIF" and length <= MAX_TEXT_BYTES:
            metadata.update(_exif_metadata(stream.read(length)))
        elif kind == b"XMP " and length <= MAX_TEXT_BYTES:
            metadata["xmp"] = _text(stream.read(length))
        stream.seek(end)
    if not width or not height:
        raise ValueError("无法识别 WebP 尺寸头部")
    return width, height, metadata


def _decode_comment(value: Any, endian: str = ">") -> str:
    if not isinstance(value, bytes):
        return _text(value)
    data = value[:MAX_TEXT_BYTES]
    prefix, payload = data[:8], data[8:]
    if prefix == b"ASCII\x00\x00\x00":
        return payload.decode("utf-8", "replace").rstrip("\x00").strip()
    if prefix == b"UNICODE\x00":
        if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
            encoding = "utf-16"
        elif len(payload) >= 4 and payload[1::2].count(0) > payload[::2].count(0):
            encoding = "utf-16-le"
        elif len(payload) >= 4 and payload[::2].count(0) > payload[1::2].count(0):
            encoding = "utf-16-be"
        else:
            encoding = "utf-16-le" if endian == "<" else "utf-16-be"
        return payload[:len(payload) // 2 * 2].decode(encoding, "replace").rstrip("\x00").strip()
    if prefix == b"JIS\x00\x00\x00\x00\x00":
        return payload.decode("shift_jis", "replace").rstrip("\x00").strip()
    return _text(data)


def _exif_metadata(source: Any) -> dict[str, Any]:
    try:
        if isinstance(source, bytes):
            exif = Image.Exif()
            exif.load(source)
        else:
            exif = source
        result: dict[str, Any] = {}
        for code, key in ((270, "description"), (315, "artist"), (305, "software")):
            if code in exif:
                result[key] = _text(exif[code])
        comment = exif.get(37510)
        if comment is None and 34665 in exif:
            comment = exif.get_ifd(34665).get(37510)
        if comment is not None:
            result["user_comment"] = _decode_comment(comment, getattr(exif, "endian", ">"))
        if not result.get("artist") and 40093 in exif:
            raw = exif[40093]
            result["artist"] = bytes(raw).decode("utf-16-le", "replace").rstrip("\x00")
        return result
    except (OSError, ValueError, TypeError, KeyError, struct.error, SyntaxError):
        return {"exif_warning": "EXIF 数据损坏，已跳过"}


def _jpeg_comments(stream: Any, file_size: int) -> dict[str, Any]:
    """Read independent UTF-8 COM records before SOS, never image scan bytes.

    Segments are not concatenated: JPEG defines independent comments, not a
    continuation protocol. Invalid/binary comments are omitted. The aggregate
    budget and marker count also cover files with many individually small COMs.
    """
    stream.seek(2)
    comments = []
    remaining = MAX_RAW_BYTES
    skipped = False
    for _ in range(4096):
        if stream.tell() >= file_size:
            break
        if stream.read(1) != b"\xff":
            raise ValueError("JPEG 标记损坏")
        marker = stream.read(1)
        while marker == b"\xff":
            marker = stream.read(1)
        if marker in (b"\xda", b"\xd9"):
            break
        if not marker or marker == b"\x00":
            raise ValueError("JPEG 标记缺失")
        if marker == b"\x01" or b"\xd0" <= marker <= b"\xd8":
            continue
        encoded_length = stream.read(2)
        if len(encoded_length) != 2:
            raise ValueError("JPEG 段长度缺失")
        length = int.from_bytes(encoded_length, "big") - 2
        if length < 0 or stream.tell() + length > file_size:
            raise ValueError("JPEG 段长度损坏")
        if marker == b"\xfe" and length <= min(MAX_TEXT_BYTES, remaining) and len(comments) < MAX_METADATA_ENTRIES:
            data = stream.read(length)
            remaining -= length
            try:
                text = data.decode("utf-8", "strict")
            except UnicodeDecodeError:
                continue
            if not any(ord(char) < 32 and char not in "\t\r\n" for char in text):
                comments.append(text)
        else:
            skipped = skipped or marker == b"\xfe"
            stream.seek(length, io.SEEK_CUR)
    else:
        raise ValueError("JPEG 标记数量超过安全限制")
    result = {"jpeg_comments": comments} if comments else {}
    if skipped:
        result["jpeg_comments_truncated"] = True
    return result


def _read_header(path: Path) -> tuple[int, int, str, dict[str, Any]]:
    try:
        with path.open("rb") as stream:
            header = stream.read(12)
            size = path.stat().st_size
            if header.startswith(b"\x89PNG\r\n\x1a\n"):
                width, height, metadata = _png_header(_BudgetReader(stream), size)
                return width, height, "PNG", metadata
            if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
                width, height, metadata = _webp_header(_BudgetReader(stream), size)
                return width, height, "WEBP", metadata
            if header[:4] == b"\x00\x00\x01\x00":
                width, height, metadata = _ico_header(_BudgetReader(stream), size)
                return width, height, "ICO", metadata
            comments = _jpeg_comments(_BudgetReader(stream), size) if header.startswith(b"\xff\xd8") else {}
            stream.seek(0)
            with Image.open(_BudgetReader(stream), formats=list(SUPPORTED_FORMATS)) as image:
                width, height = image.size
                metadata = {key: value for key, value in list(image.info.items())[:MAX_METADATA_ENTRIES]
                            if isinstance(value, str) and len(value.encode("utf-8")) <= MAX_TEXT_BYTES}
                metadata.update(_exif_metadata(image.getexif()))
                metadata.update(comments)
                return width, height, image.format or path.suffix[1:].upper(), metadata
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError, SyntaxError, struct.error, EOFError, OverflowError, IndexError) as exc:
        raise ValueError(f"无法读取图片头部：{exc}") from exc


def _split_top_level(value: str) -> list[str]:
    output: list[str] = []
    start = 0
    stack: list[str] = []
    escaped = False
    quote = ""
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in '\"\'' and (index == 0 or value[index - 1] in " :,"):
            quote = char
        elif char in "([{<":
            stack.append(char)
        elif char in ")]}>":
            if stack and {"(": ")", "[": "]", "{": "}", "<": ">"}[stack[-1]] == char:
                stack.pop()
        elif char in ",\n" and not stack:
            output.append(value[start:index].strip())
            start = index + 1
    output.append(value[start:].strip())
    return output


def parse_tags(prompt: str) -> list[str]:
    """Comma TAGs, respecting escaped commas and weighted/nested directives."""
    tags: list[str] = []
    seen: set[str] = set()
    for value in _split_top_level(prompt[:MAX_TEXT_BYTES]):
        value = re.sub(r"<\s*(?:lora|lyco|hypernet):[^>]*>", "", value, flags=re.I).strip()
        for _ in range(8):
            if (value.startswith("(") and value.endswith(")")) or (value.startswith("[") and value.endswith("]")):
                value = re.sub(r":\s*[-+]?\d+(?:\.\d+)?\s*$", "", value[1:-1]).strip()
            else:
                break
        value = re.sub(r"\\([,()\[\]<>\\])", r"\1", value)
        value = re.sub(r"\s+", " ", value).strip().strip('\"')
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            tags.append(value[:256])
            if len(tags) >= MAX_TAGS:
                break
    return tags


def _a1111(text: str) -> dict[str, Any] | None:
    # The official A1111 parser accepts reordered settings tails. Require
    # multiple known fields plus numeric data here, rather than prompt wording.
    known = set(_MODEL_FIELDS + _BASE_FIELDS + _ARCH_FIELDS) | {
        "steps", "seed", "cfg_scale", "sampler", "sampler_name", "scheduler",
        "size", "model_hash", "clip_skip", "version", "denoising_strength"}
    offset = 0
    beginning = None
    for line in text.splitlines(keepends=True):
        items = {}
        for item in _split_top_level(line):
            key, separator, value = item.partition(":")
            if separator:
                items[_key(key)] = value.strip()
        recognized = known.intersection(items)
        steps = bool(re.fullmatch(r"\d+", items.get("steps", "")))
        numeric = bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?", items.get("seed", items.get("cfg_scale", ""))))
        sized = bool(re.fullmatch(r"\d+\s*[x×]\s*\d+", items.get("size", ""), re.I))
        if (steps and len(recognized) >= 2) or (len(recognized) >= 3 and (numeric or sized)):
            beginning = offset
            break
        offset += len(line)
    if beginning is None:
        return None
    prompt_block, settings = text[:beginning].strip(), text[beginning:].strip()
    parts = re.split(r"(?im)^\s*Negative\s+prompt\s*:\s*", prompt_block, maxsplit=1)
    params: dict[str, Any] = {}
    for item in _split_top_level(settings):
        key, separator, value = item.partition(":")
        if separator and key.strip() and len(params) < 256:
            value = value.strip()
            decoded = _safe_json(value) if value.startswith('"') else None
            params[key.strip()] = decoded if isinstance(decoded, str) else value.strip('"')
    record = _structured({**params, "prompt": parts[0].strip(), "negative_prompt": parts[1].strip() if len(parts) > 1 else ""}, source="A1111") or {}
    record["parameters"] = {**params, **record.get("parameters", {}), "metadata_source": "A1111"}
    for resource in _civitai_records(params, "A1111"):
        record = _combine_records([record, resource], "A1111")
    record["ai_evidence"] = True
    return record


_TEXT_CLASSES = ("CLIPTextEncode", "CLIPTextEncodeSDXL", "CLIPTextEncodeSDXLRefiner", "CLIPTextEncodeFlux")


def _loader_inputs(cls: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Read semantic diffusion fields, never auxiliary model filenames."""
    category = cls.casefold()
    if any(word in category for word in ("lora", "lycoris", "hypernet", "controlnet", "cliploader", "cliptextencode", "textencode", "vaeloader", "upscale")):
        return {}
    normalized = {_key(key): value for key, value in inputs.items()}
    strong = ("ckpt_name", "checkpoint", "checkpoint_name", "ckpt", "unet_name", "diffusion_model", "diffusion_model_name")
    fields = list(strong)
    if any(word in category for word in ("loader", "checkpoint", "unet", "diffusion")):
        fields += ["model_name", "model_path", "checkpoint_path", "model"]
    result = {key: normalized[key] for key in fields if isinstance(normalized.get(key), (str, dict, list))}
    result.update({key: normalized[key] for key in _BASE_FIELDS + _ARCH_FIELDS + _HASH_FIELDS if isinstance(normalized.get(key), str)})
    return result


def _workflow_graph(workflow: dict[str, Any]) -> dict[str, Any]:
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list) or len(nodes) > MAX_NODES:
        return {}
    link_data = workflow.get("links", [])
    link_data = link_data if isinstance(link_data, list) else []
    links = {str(link[0]): link for link in link_data[:MAX_NODES * 8]
             if isinstance(link, list) and len(link) >= 6}
    graph = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = _text(node.get("type"))
        slots = node.get("inputs", [])
        inputs = dict(slots) if isinstance(slots, dict) else {}
        if isinstance(slots, list):
            for slot in slots[:64]:
                if not isinstance(slot, dict):
                    continue
                link = links.get(str(slot.get("link")))
                if link:
                    inputs[_key(slot.get("name"))] = [str(link[1]), link[2]]
        widgets = node.get("widgets_values", [])
        if isinstance(widgets, dict):
            # Named custom widget values carry actual field semantics.
            inputs.update({_key(key): value for key, value in widgets.items()})
        elif isinstance(widgets, list) and widgets:
            # Only verified core orders are used. Unknown custom widget orders
            # remain opaque rather than treating the first filename as a model.
            if node_type == "CheckpointLoaderSimple":
                inputs["ckpt_name"] = widgets[0]
            elif node_type == "CheckpointLoader" and len(widgets) > 1:
                inputs["ckpt_name"] = widgets[1]
            elif node_type in ("UNETLoader", "UnetLoaderGGUF", "DiffusionModelLoader"):
                inputs["unet_name"] = widgets[0]
            elif node_type == "DiffusersLoader":
                inputs["model_path"] = widgets[0]
            elif node_type in _TEXT_CLASSES:
                inputs.setdefault("text", widgets[0])
            elif node_type in ("PrimitiveString", "PrimitiveStringMultiline"):
                inputs.setdefault("value", widgets[0])
            elif node_type == "StringConcatenate":
                for name, value in zip(("string_a", "string_b", "delimiter"), widgets):
                    inputs.setdefault(name, value)
        graph[str(node.get("id"))] = {"class_type": node_type, "inputs": inputs,
                                      "_metadata_node_mode": node.get("mode", 0)}
    return graph


def _comfy(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    graph = _workflow_graph(data) if "nodes" in data else data
    if not graph or len(graph) > MAX_NODES:
        return None
    nodes = {str(key): node for key, node in graph.items()
             if isinstance(node, dict) and isinstance(node.get("inputs"), dict) and isinstance(node.get("class_type"), str)}
    if not nodes:
        return None
    evidence = []
    model_fields = ("model", "model_1", "model_2", "model1", "model2", "guider", "pipe", "pipeline", "basic_pipe")
    params = {"metadata_source": "ComfyUI"}

    # Independently implemented from documented node semantics. No extension
    # code is imported/executed and no LoRA registry/model file is consulted.
    string_cache: dict[tuple[str, int], tuple[str, bool]] = {}
    string_visits = 0
    string_cache_bytes = 0
    generated_string_bytes = 0

    def unwrap(value):
        return value.get("__value__") if isinstance(value, dict) and "__value__" in value else value

    def string_value(value, active=frozenset()):
        nonlocal string_visits, string_cache_bytes, generated_string_bytes
        if isinstance(value, str):
            return (value, True) if len(value.encode("utf-8")) <= MAX_TEXT_BYTES else ("", False)
        if not isinstance(value, list) or len(value) != 2 or type(value[1]) is not int:
            return "", False
        token = (str(value[0]), value[1])
        if token in active or len(active) >= 128:
            return "", False
        if token in string_cache:
            return string_cache[token]
        string_visits += 1
        if string_visits > MAX_NODES * 4:
            return "", False
        node = nodes.get(token[0], {})
        cls, inputs = node.get("class_type"), node.get("inputs", {})
        branch = active | {token}
        result = ("", False)
        if node.get("_metadata_node_mode", 0) != 0:
            return "", False
        if cls in ("PrimitiveString", "PrimitiveStringMultiline") and token[1] == 0:
            result = string_value(inputs.get("value"), branch)
        elif cls == "StringConcatenate" and token[1] == 0:
            a, a_ok = string_value(inputs.get("string_a"), branch)
            b, b_ok = string_value(inputs.get("string_b"), branch)
            delimiter, delimiter_ok = string_value(inputs.get("delimiter", ""), branch)
            if delimiter_ok:
                joined = a + delimiter + b
                result = (joined, a_ok and b_ok) if len(joined.encode("utf-8")) <= MAX_TEXT_BYTES else ("", False)
        elif cls == "Lora Loader (LoraManager)" and token[1] == 2:
            # text is a search widget and is ignored by the official loader.
            # Active LoRAs obtain words from external local registry state, so
            # an embedded name or strength cannot reproduce their output.
            loras = unwrap(inputs.get("loras"))
            loras = _safe_json(loras) if isinstance(loras, str) else loras
            if isinstance(loras, list) and not inputs.get("lora_stack") and all(
                    isinstance(item, dict) and item.get("active", False) is False for item in loras):
                result = ("", True)
        elif cls == "TriggerWord Toggle (LoraManager)" and token[1] == 0:
            original = unwrap(inputs.get("orinalMessage", ""))
            override = unwrap(inputs.get("trigger_words", ""))
            original, original_ok = string_value(original, branch)
            override, override_ok = string_value(override, branch)
            if original_ok and override_ok:
                words = lambda text: {part.strip() for part in text.split(",") if part.strip()}
                if override and words(override) != words(original):
                    result = (override, True)
                else:
                    toggles = unwrap(inputs.get("toggle_trigger_words"))
                    toggles = _safe_json(toggles) if isinstance(toggles, str) else toggles
                    group_mode = inputs.get("group_mode", True)
                    strength_mode = inputs.get("allow_strength_adjustment", False)
                    if not toggles:
                        result = (original, True)
                    elif isinstance(toggles, list) and len(toggles) <= MAX_TAGS and type(group_mode) is bool and type(strength_mode) is bool:
                        def selected(item, weighted):
                            if not isinstance(item, dict) or not isinstance(item.get("text", ""), str) or type(item.get("active", False)) is not bool:
                                raise ValueError("unsupported toggle state")
                            text = item.get("text", "").strip()
                            strength = item.get("strength")
                            match = re.fullmatch(r"\((.+):([\d.]+)\)", text)
                            if match:
                                text = match[1].strip()
                                if strength is None:
                                    strength = float(match[2])
                            if not item.get("active", False) or not text:
                                return ""
                            if weighted and strength is not None:
                                if type(strength) not in (int, float) or not math.isfinite(strength):
                                    raise ValueError("unsupported toggle strength")
                                return f"({text}:{strength:.2f})"
                            return text
                        try:
                            outputs = []
                            has_children = group_mode and any(isinstance(item, dict) and isinstance(item.get("items"), list) for item in toggles)
                            for item in toggles:
                                if has_children:
                                    text = selected(item, False)
                                    if text and isinstance(item.get("items"), list):
                                        if len(item["items"]) > MAX_TAGS:
                                            raise ValueError("too many toggle children")
                                        text = ", ".join(filter(None, (selected(child, False) for child in item["items"])))
                                    copy = {**item, "text": text}
                                    text = selected(copy, strength_mode)
                                elif group_mode and not strength_mode:
                                    # This branch intentionally preserves inline
                                    # weights, matching the upstream group mode.
                                    selected(item, False)  # validate shape
                                    text = item.get("text", "").strip() if item.get("active", False) else ""
                                else:
                                    text = selected(item, strength_mode)
                                if text:
                                    outputs.append(text)
                            output = ", ".join(outputs)
                            result = (output, True) if len(output.encode("utf-8")) <= MAX_TEXT_BYTES else ("", False)
                        except (ValueError, TypeError, OverflowError):
                            result = ("", False)
                    elif not isinstance(toggles, list):
                        # Unexpected state is intentionally not guessed.
                        result = ("", False)
        result_size = len(result[0].encode("utf-8"))
        if cls not in ("PrimitiveString", "PrimitiveStringMultiline"):
            # Per-output bounds alone permit a tiny DAG to expand into many
            # distinct large buffers. Keep all generated strings within one
            # aggregate budget, even when they are not retained in the cache.
            if generated_string_bytes + result_size > MAX_RAW_BYTES:
                result, result_size = ("", False), 0
            generated_string_bytes += result_size
        if string_cache_bytes + result_size <= MAX_RAW_BYTES:
            string_cache[token] = result
            string_cache_bytes += result_size
        return result

    condition_cache = {}
    walk_visits = 0

    def walk(reference, mode, visited=None):
        nonlocal walk_visits
        walk_visits += 1
        if walk_visits > MAX_NODES * 16:
            params["model_provenance_incomplete" if mode == "model" else "prompt_extraction_incomplete"] = True
            return []
        cache_key = None
        if mode != "model" and isinstance(reference, list) and len(reference) == 2 and type(reference[1]) is int:
            if str(reference[0]) in (visited or set()):
                params["prompt_extraction_incomplete"] = True
                return []
            cache_key = (str(reference[0]), reference[1], mode)
            if cache_key in condition_cache:
                return condition_cache[cache_key]
        output = list(dict.fromkeys(_walk(reference, mode, visited)))
        if cache_key is not None:
            condition_cache[cache_key] = output
        return output

    def _walk(reference, mode, visited=None):
        visited = set() if visited is None else visited
        if isinstance(reference, str):
            return [_text(reference)] if mode != "model" else []
        if isinstance(reference, dict):
            keys = model_fields if mode == "model" else (mode, "conditioning", "text")
            return [item for key in keys for item in walk(reference.get(key), mode, visited)]
        if not isinstance(reference, list) or len(reference) != 2:
            return []
        if mode != "model" and (type(reference[1]) is not int or reference[1] != 0):
            params["prompt_extraction_incomplete"] = True
            return []
        node_id = str(reference[0])
        if node_id in visited or len(visited) >= 128:
            if mode != "model":
                params["prompt_extraction_incomplete"] = True
            return []
        visited = visited | {node_id}
        node = nodes.get(node_id, {})
        cls, inputs = _text(node.get("class_type")), node.get("inputs", {})
        inputs = {_key(key): value for key, value in inputs.items()}
        if node.get("_metadata_node_mode", 0) != 0:
            params["model_provenance_incomplete" if mode == "model" else "prompt_extraction_incomplete"] = True
            return []
        if mode == "model":
            if cls == "CivitaiModelSelector":
                # Official resources_json.bySlot selects the resource used by
                # this exact loader input; the primary AIR may be a different
                # resource. Never follow an auxiliary selector slot.
                selection = _safe_json(inputs.get("resources_json"))
                by_slot = selection.get("bySlot") if isinstance(selection, dict) else None
                air = by_slot.get(str(reference[1])) if isinstance(by_slot, dict) else None
                air = air if isinstance(air, str) else inputs.get("air")
                resource = _checkpoint_air(air, f"ComfyUI.{node_id}.{cls}.slot{reference[1]}")
                if resource:
                    record = _structured({"checkpoint_name": resource["name"], "base_model": resource["base_model"]}, source=resource["source"])
                    evidence.extend(record["parameters"].get("model_evidence", []))
                    _parameter_merge(params, {"checkpoint_ancestry": record["parameters"].get("checkpoint_ancestry", [])})
                    return record["parameters"].get("model_candidates", [])
                return []
            fields = _loader_inputs(cls, inputs)
            record = _structured(fields, source=f"ComfyUI.{node_id}.{cls}") if fields else None
            names = []
            if record:
                evidence.extend(record["parameters"].get("model_evidence", []))
                names.extend(record["parameters"].get("model_candidates", []))
                _parameter_merge(params, {key: record["parameters"][key]
                                         for key in _HASH_FIELDS + ("checkpoint_hashes", "checkpoint_ancestry")
                                         if key in record["parameters"]})
                for key in _BASE_FIELDS + _ARCH_FIELDS + _HASH_FIELDS:
                    if key in record["parameters"]:
                        params[key] = record["parameters"][key]
            for key, value in fields.items():
                if key in _MODEL_FIELDS and isinstance(value, list) and len(value) == 2:
                    names.extend(walk(value, mode, visited))
            # Wrappers/mergers/guiders and custom pipelines retain the actual
            # upstream model. LoRA/control/CLIP filenames are never followed.
            for key in model_fields:
                value = inputs.get(key)
                if isinstance(value, (list, dict)):
                    names.extend(walk(value, mode, visited))
            return names
        if cls == "ConditioningZeroOut" and reference[1] == 0:
            return []
        if cls in _TEXT_CLASSES and reference[1] == 0:
            output = []
            for key in ("text", "text_g", "text_l", "clip_l", "t5xxl"):
                if key in inputs:
                    text, complete = string_value(inputs[key])
                    if not complete:
                        params["prompt_extraction_incomplete"] = True
                    if text:
                        output.append(text)
            return output
        # Only known core conditioning transforms or semantically named
        # pipeline/guider inputs may pass text provenance through. An unknown
        # dynamic conditioning transform could replace its input completely.
        passthrough = ("ConditioningCombine", "ConditioningConcat", "ConditioningAverage",
                       "ConditioningSetArea", "ConditioningSetAreaPercentage", "ConditioningSetAreaStrength",
                       "ConditioningSetMask", "ConditioningSetTimestepRange", "CFGGuider", "BasicGuider", "DualCFGGuider")
        named_pipeline = ("pipe" in cls.casefold() or "guider" in cls.casefold()) and any(
            key in inputs for key in (mode, "pipe", "pipeline", "basic_pipe"))
        if cls not in passthrough and not named_pipeline:
            params["prompt_extraction_incomplete"] = True
            return []
        keys = [key for key in inputs if key == mode or "conditioning" in key]
        keys += ["pipe", "pipeline", "basic_pipe"]
        return [item for key in dict.fromkeys(keys) for item in walk(inputs.get(key), mode, visited)]

    models, positives, negatives = [], [], []
    samplers = []
    for node in nodes.values():
        if node.get("_metadata_node_mode", 0) != 0:
            continue
        cls, inputs = _text(node.get("class_type")).casefold(), {_key(key): value for key, value in node["inputs"].items()}
        model_input = any(key in inputs for key in model_fields)
        structural = any(key in inputs for key in ("positive", "negative", "conditioning")) and any(key in inputs for key in ("steps", "sampler_name", "seed"))
        if model_input and ("sampler" in cls or structural) and not any(word in cls for word in ("lora", "loader", "clip", "controlnet")):
            samplers.append(node)
    for node in samplers:
        inputs = {_key(key): value for key, value in node["inputs"].items()}
        for key in model_fields:
            if isinstance(inputs.get(key), (list, dict)):
                models.extend(walk(inputs[key], "model"))
        positives.extend(walk(inputs.get("positive", inputs.get("conditioning")), "positive"))
        negatives.extend(walk(inputs.get("negative"), "negative"))
        for key in ("guider", "pipe", "pipeline", "basic_pipe"):
            if key in inputs:
                positives.extend(walk(inputs[key], "positive"))
                negatives.extend(walk(inputs[key], "negative"))
        for key in ("seed", "noise_seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"):
            if isinstance(inputs.get(key), (str, int, float)):
                params[key] = inputs[key]
        # Core SamplerCustomAdvanced separates seed/sampler/sigmas into linked
        # nodes. Only generation parameters from those named links are read.
        for key in ("noise", "sampler", "sigmas", "guider"):
            ref = inputs.get(key)
            if isinstance(ref, list) and ref:
                linked = nodes.get(str(ref[0]), {}).get("inputs", {})
                for parameter in ("noise_seed", "seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"):
                    if isinstance(linked.get(parameter), (str, int, float)):
                        params[parameter] = linked[parameter]
    if not samplers:
        for key, node in nodes.items():
            cls = _text(node.get("class_type"))
            if _loader_inputs(cls, node["inputs"]):
                models.extend(walk([key, 0], "model"))
    models = list(dict.fromkeys(filter(None, models)))
    params["model_candidates"] = models[:MAX_EVIDENCE]
    evidence = list({(item["kind"], item["value"], item["source"]): item for item in evidence}.values())
    params["model_evidence"] = evidence[:MAX_EVIDENCE]
    if len(models) > MAX_EVIDENCE or len(evidence) > MAX_EVIDENCE:
        params["model_provenance_incomplete"] = True
    def bounded_prompt(parts):
        output = []
        remaining = MAX_TEXT_BYTES
        for text in dict.fromkeys(filter(None, parts)):
            text = text.strip()
            if not text:
                continue
            size = len(text.encode("utf-8")) + (2 if output else 0)
            if size > remaining:
                params["prompt_extraction_incomplete"] = True
                continue
            output.append(text)
            remaining -= size
        return ", ".join(output)
    positive, negative = bounded_prompt(positives), bounded_prompt(negatives)
    return {"model": " | ".join(models), "prompt": positive,
            "negative_prompt": negative,
            "parameters": params, "ai_evidence": True}


def _combine_records(records: list[dict[str, Any]], source: str) -> dict[str, Any]:
    """Merge provenance without hiding another actual checkpoint or hash."""
    result = {"model": "", "prompt": "", "negative_prompt": "", "tags": [], "artist": "", "parameters": {}, "ai_evidence": False}
    candidates = []
    for record in records:
        for key in ("prompt", "negative_prompt", "artist"):
            if not result[key] and record.get(key):
                result[key] = record[key]
        result["tags"].extend(record.get("tags", []))
        result["ai_evidence"] = result["ai_evidence"] or bool(record.get("ai_evidence"))
        params = record.get("parameters", {})
        _parameter_merge(result["parameters"], params)
        candidates.extend(params.get("model_candidates", []))
    candidates = list(dict.fromkeys(candidates))
    result["model"] = " | ".join(candidates)
    if candidates:
        result["parameters"]["model_candidates"] = candidates[:MAX_EVIDENCE]
    if len(candidates) > MAX_EVIDENCE:
        result["parameters"]["model_provenance_incomplete"] = True
    result["parameters"]["metadata_source"] = source
    return result


def _civitai_records(data: dict[str, Any], source: str, curated=False):
    """Read only Civitai-namespaced or curated, explicitly typed checkpoints.

    Shapes are documented in civitai/media-metadata src/civitai/a1111.ts and
    src/civitai/comfy.ts. An arbitrary JSON resources list stays opaque. Resource
    IDs alone do not prove ancestry and do not cause any network lookup.
    """
    fields = {_key(key): value for key, value in list(data.items())[:MAX_METADATA_ENTRIES]}
    keys = ("civitai_resources", "resources") if curated else ("civitai_resources",)
    for key in keys:
        resources = fields.get(key)
        resources = _safe_json(resources) if isinstance(resources, str) else resources
        if not isinstance(resources, list):
            continue
        if len(resources) > MAX_EVIDENCE:
            yield {"parameters": {"model_provenance_incomplete": True}, "ai_evidence": True}
        for index, resource in enumerate(resources[:MAX_EVIDENCE]):
            if not isinstance(resource, dict):
                continue
            item = {_key(k): value for k, value in list(resource.items())[:MAX_METADATA_ENTRIES]}
            nested = item.get("model")
            nested = {_key(k): v for k, v in nested.items()} if isinstance(nested, dict) else {}
            resource_type = _text(item.get("type", item.get("model_type", nested.get("type")))).casefold()
            location = f"{source}.{key}[{index}]"
            air_value = _text(item.get("air"))
            air = _checkpoint_air(air_value, location + ".air") if air_value else None
            if _auxiliary_record(item) or _auxiliary_record(nested):
                continue
            if air_value and not air:
                # A typed checkpoint record contradicted by a LoRA/invalid AIR
                # is malformed, never upgraded by its name or baseModel text.
                if resource_type == "checkpoint":
                    yield {"parameters": {"model_provenance_incomplete": True}, "ai_evidence": True}
                continue
            if resource_type not in ("", "checkpoint") or (resource_type != "checkpoint" and not air):
                continue
            name = next((_text(item.get(k)) for k in ("model_name", "name", "filename") if _text(item.get(k))), "")
            name = name or _text(nested.get("name")) or (air["name"] if air else "")
            values = {"checkpoint_name": name}
            for field in _BASE_FIELDS + _ARCH_FIELDS + _HASH_FIELDS:
                if isinstance(item.get(field), str):
                    values[field] = item[field]
            hash_value = item.get("hash")
            if isinstance(hash_value, str):
                values["checkpoint_hash"] = hash_value
            if isinstance(item.get("sha256"), str):
                values["checkpoint_sha256"] = item["sha256"]
            records = []
            record = _structured(values, source=location)
            if record:
                records.append(record)
            if air:
                records.append(_structured({"checkpoint_name": name, "base_model": air["base_model"]}, source=air["source"]))
            if records:
                yield _combine_records(records, location)


def _generator_record(data: dict[str, Any], source: str, generator: str) -> dict[str, Any] | None:
    """Generator-specific meanings supersede generic same-named JSON fields."""
    fields = {_key(key): value for key, value in list(data.items())[:MAX_METADATA_ENTRIES]}
    # Fooocus base_model is a checkpoint name, not an ancestry label. The
    # official meta_parser.py writes SHA256 hashes for both base and refiner.
    fooocus = generator == "Fooocus" or (
        isinstance(fields.get("base_model"), str) and isinstance(fields.get("base_model_hash"), str)
        and isinstance(fields.get("full_prompt"), list))
    if fooocus and isinstance(fields.get("base_model"), str):
        values = dict(fields)
        values["checkpoint_name"] = values.pop("base_model")
        if isinstance(values.get("base_model_hash"), str):
            values["checkpoint_sha256"] = values.pop("base_model_hash")
        record = _structured(values, source=source + ".Fooocus")
        records = [record] if record else []
        refiner = _text(fields.get("refiner_model"))
        if refiner and refiner.casefold() not in ("none", "disabled"):
            secondary = _structured({"checkpoint_name": refiner, "checkpoint_sha256": fields.get("refiner_model_hash")}, source=source + ".Fooocus.refiner")
            if secondary:
                records.append(secondary)
        return _combine_records(records, "Fooocus")
    # InvokeAI explicitly records the used SDXL refiner as another main model.
    # Keep each record's ancestry/hash bound to its own checkpoint; treating the
    # main model's base as the refiner's base would hide an unknown/conflict.
    main = fields.get("model")
    invokeai = generator == "InvokeAI" or "invokeai" in source.casefold() or (
        isinstance(main, dict) and all(key in {_key(k) for k in main} for key in ("key", "base", "type")))
    if invokeai:
        values = {key: value for key, value in fields.items() if key != "refiner_model"}
        record = _structured(values, source=source, generator="InvokeAI")
        records = [record] if record else []
        refiner = fields.get("refiner_model")
        if isinstance(refiner, dict):
            secondary = _structured({"model": refiner}, source=source + ".refiner_model", generator="InvokeAI")
            if secondary:
                records.append(secondary)
            refiner_fields = {_key(key): value for key, value in refiner.items()}
            if refiner and _text(refiner_fields.get("type")) != "main" and not _auxiliary_record(refiner_fields):
                records.append({"parameters": {"model_provenance_incomplete": True}, "ai_evidence": True})
        elif refiner is not None and _text(refiner).casefold() not in ("", "none", "disabled"):
            records.append({"parameters": {"model_provenance_incomplete": True}, "ai_evidence": True})
        return _combine_records(records, "InvokeAI") if records else None
    # SwarmUI records SHA256 *tensor* hashes prefixed with 0x. They differ from
    # whole-file SHA256/A1111 hashes; preserve them in a separate display field.
    # Official docs/Image Metadata Format.md specifies sui_image_params/models.
    swarm = fields.get("sui_image_params")
    if isinstance(swarm, dict):
        values = {_key(k): v for k, v in list(swarm.items())[:MAX_METADATA_ENTRIES]}
        values["negative_prompt"] = values.pop("negativeprompt", values.get("negative_prompt", ""))
        if "cfgscale" in values:
            values["cfg_scale"] = values.pop("cfgscale")
        records = []
        main = _structured(values, source=source + ".sui_image_params")
        if main:
            records.append(main)
        tensor_hashes = []
        used = fields.get("sui_models")
        if isinstance(used, list):
            if len(used) > MAX_EVIDENCE:
                records.append({"parameters": {"model_provenance_incomplete": True}, "ai_evidence": True})
            main_identity = _checkpoint_identity(values.get("model"))
            for index, model in enumerate(used[:MAX_EVIDENCE]):
                if not isinstance(model, dict) or _text(model.get("param")) != "model":
                    continue
                name = _text(model.get("name"))
                if name and _checkpoint_identity(name) != main_identity:
                    record = _structured({"checkpoint_name": name}, source=f"{source}.sui_models[{index}].model")
                    if record:
                        records.append(record)
                digest = _text(model.get("hash"))
                if re.fullmatch(r"0x[0-9a-fA-F]{64}", digest):
                    tensor_hashes.append({"name": name, "hash": digest, "algorithm": "sha256-tensor"})
        result = _combine_records(records, "SwarmUI")
        if tensor_hashes:
            result["parameters"]["swarm_model_tensor_hashes"] = tensor_hashes[:MAX_EVIDENCE]
        return result
    return None


def _structured(data: Any, novelai: bool = False, source: str = "JSON", generator: str = "") -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    normalized = {_key(key): value for key, value in list(data.items())[:MAX_METADATA_ENTRIES]}
    if not any(key in normalized for key in _MODEL_FIELDS + _BASE_FIELDS + _ARCH_FIELDS + _HASH_FIELDS + ("hashes", "prompt", "positive_prompt", "negative_prompt", "tags", "artist", "author", "ai", "is_ai", "uc", "generation_type")):
        return None
    params = normalized.get("parameters", {})
    params = _small_value(params) if isinstance(params, dict) else {}
    for key, value in normalized.items():
        if key not in ("prompt", "negative_prompt", "uc", "tags", "artist", "author", "creator", "parameters") and isinstance(value, (str, int, float, bool)):
            params[key] = value
    if novelai:
        params["software"] = "NovelAI"
    tag_data = normalized.get("tags", [])
    tags = parse_tags(tag_data) if isinstance(tag_data, str) else [_text(tag)[:256] for tag in tag_data[:MAX_TAGS] if isinstance(tag, str)] if isinstance(tag_data, list) else []
    evidence = []
    models = []
    ancestry = []
    top_ancestry = []
    adapter = _auxiliary_record(normalized)
    if adapter:
        params = {}
    if not adapter:
        hashes = normalized.get("hashes")
        if isinstance(hashes, (dict, str)):
            # Keep checkpoint-specific hash maps for explicit user registration;
            # no hash is an automatic ancestry lookup.
            params["hashes"] = _small_value(hashes)
        for field in _MODEL_FIELDS:
            value = normalized.get(field)
            if isinstance(value, str):
                record = _evidence(value, f"{source}.{field}", "checkpoint")
                if record:
                    evidence.append(record)
                    models.append(record["value"])
            elif isinstance(value, dict):
                nested = {_key(key): val for key, val in value.items()}
                if _auxiliary_record(nested):
                    continue
                invokeai = generator == "InvokeAI" or ("invokeai" in source.casefold()) or (
                    all(key in nested for key in ("key", "base", "type")) and _text(nested.get("type")) == "main")
                if invokeai and _text(nested.get("type")) != "main":
                    continue
                nested_models = []
                nested_ancestry = []
                for name_field in ("name", "filename", "path", "model_name", "checkpoint_name"):
                    record = _evidence(nested.get(name_field), f"{source}.{field}.{name_field}", "checkpoint")
                    if record:
                        evidence.append(record)
                        models.append(record["value"])
                        nested_models.append(record["value"])
                # ModelIdentifierField.base is explicit. The default hash is
                # BLAKE3, but InvokeAI allows another installation algorithm.
                # A bare hash never proves whole-file SHA256 identity.
                if invokeai and "base" in nested and _text(nested.get("type")) == "main":
                    record = _evidence(nested.get("base"), f"{source}.{field}.base", "base")
                    if record:
                        evidence.append(record)
                        nested_ancestry.append(record)
                for key in _BASE_FIELDS + _ARCH_FIELDS:
                    record = _evidence(nested.get(key), f"{source}.{field}.{key}", "base" if key in _BASE_FIELDS else "architecture")
                    if record:
                        evidence.append(record)
                        params[key] = nested[key]
                        nested_ancestry.append(record)
                ancestry.extend({"checkpoint": checkpoint, **item}
                                for checkpoint in nested_models for item in nested_ancestry)
                for key in ("hash", "model_hash", "sha256"):
                    if isinstance(nested.get(key), str):
                        if invokeai and key == "hash":
                            prior_hashes = _invokeai_hash_records(params)
                            params["invokeai_model_hash"] = nested[key]
                            params["invokeai_hash_algorithm"] = "unspecified"
                            params["invokeai_hash_default_algorithm"] = "blake3"
                            params["invokeai_model_hashes"] = prior_hashes + [{
                                "checkpoint": nested_models[0] if nested_models else "",
                                "hash": nested[key], "algorithm": "unspecified",
                                "default_algorithm": "blake3", "source": f"{source}.{field}.hash"}]
                        else:
                            params["model_sha256" if key == "sha256" else "model_hash"] = nested[key]
        for field in _BASE_FIELDS + _ARCH_FIELDS:
            record = _evidence(normalized.get(field), f"{source}.{field}", "base" if field in _BASE_FIELDS else "architecture")
            if record:
                evidence.append(record)
                top_ancestry.append(record)
    models = list(dict.fromkeys(models))
    if evidence:
        params["model_evidence"] = evidence[:MAX_EVIDENCE]
    if models:
        params["model_candidates"] = models[:MAX_EVIDENCE]
    if len(models) > MAX_EVIDENCE or len(evidence) > MAX_EVIDENCE:
        params["model_provenance_incomplete"] = True
    if len(models) == 1:
        ancestry.extend({"checkpoint": models[0], **item} for item in top_ancestry)
    if ancestry:
        params["checkpoint_ancestry"] = ancestry[:MAX_EVIDENCE]
        if len(ancestry) > MAX_EVIDENCE:
            params["model_provenance_incomplete"] = True
    params["metadata_source"] = source
    ai_evidence = novelai or bool(evidence) or any(key in normalized for key in _HASH_FIELDS + ("generation_type", "uc"))
    def prompt_value(*keys):
        for key in keys:
            value = normalized.get(key)
            if isinstance(value, dict):
                value = value.get("text", value.get("prompt"))
            if isinstance(value, str):
                return _text(value)
        return ""
    return {"model": " | ".join(models), "prompt": prompt_value("prompt", "positive_prompt", "positive"),
            "negative_prompt": prompt_value("negative_prompt", "negative", "uc"),
            "tags": tags, "artist": _text(normalized.get("artist", normalized.get("author", normalized.get("creator", "")))),
            "parameters": params, "ai_evidence": ai_evidence}


def _generation_records(data: Any, source="JSON", novelai=False, generator=""):
    """Walk only provenance wrappers, never TAG/LoRA/style/resource subtrees."""
    pending = [(data, source, 0, generator)]
    visits = 0
    while pending and visits < MAX_JSON_VISITS:
        value, location, depth, inherited_generator = pending.pop(0)
        visits += 1
        if isinstance(value, str):
            value = _safe_json(value)
        if not isinstance(value, dict) or depth > 8:
            continue
        fields = {_key(key): item for key, item in list(value.items())[:MAX_METADATA_ENTRIES]}
        local_generator = inherited_generator
        if _text(fields.get("fooocus_scheme")).casefold() == "fooocus" or re.search(r"\bfooocus\b", _text(fields.get("software")), re.I):
            local_generator = "Fooocus"
        if "invokeai_metadata" in location.casefold():
            local_generator = "InvokeAI"
        special = _generator_record(value, location, local_generator)
        comfy = _comfy(value) if not special else None
        if special:
            yield special
        if comfy:
            yield comfy
        if not special and not comfy:
            record = _structured(value, novelai, location, local_generator)
            if record:
                yield record
        yield from _civitai_records(value, location, curated=".extra_metadata" in location)
        for key, nested in list(value.items())[:MAX_METADATA_ENTRIES]:
            normalized = _key(key)
            if normalized not in _WRAPPERS:
                continue
            if normalized in ("prompt", "workflow"):
                graph = _safe_json(nested) if isinstance(nested, str) else nested
                graph_record = _comfy(graph)
                if graph_record:
                    pending.append((graph, f"{location}.{normalized}", depth + 1, local_generator))
                continue
            if isinstance(nested, (dict, str)):
                # Carry explicit generator identity through its own provenance
                # wrappers; nothing in a TAG/prompt/style subtree is visited.
                pending.append((nested, f"{location}.{normalized}", depth + 1, local_generator))


def _metadata_records(raw: dict[str, Any]):
    values = {_key(key): value for key, value in raw.items()}
    software, source = _text(values.get("software")), _text(values.get("source"))
    novelai = bool(re.search(r"\bnovelai\b", software, re.I)) or _family_from_name(source) == {"NovelAI"}
    generator = "Fooocus" if _text(values.get("fooocus_scheme")).casefold() == "fooocus" or re.search(r"\bfooocus\b", software, re.I) else ""
    comments = values.get("jpeg_comments")
    if isinstance(comments, list):
        for comment in comments[:MAX_METADATA_ENTRIES]:
            if isinstance(comment, str):
                yield from _metadata_records({"comment": comment})
    for key in ("parameters", "user_comment", "prompt", "workflow", "comment", "description", "metadata",
                "generation", "generation_data", "generation_info", "generation_parameters", "sd_metadata",
                "invokeai_metadata", "extra_metadata", "civitai_resources", "extra", "extra_pnginfo", "sidecar_text"):
        value = values.get(key)
        if isinstance(value, str):
            if len(value.encode("utf-8")) > MAX_TEXT_BYTES:
                continue
            label = re.match(r"^\s*(prompt|workflow)\s*=", value)
            if label:
                # A complete labelled graph is provenance. The same label
                # around arbitrary model-looking text is not evidence.
                graph = _safe_json(value[label.end():])
                if _comfy(graph):
                    yield from _generation_records(graph, f"元数据.{key}.{label[1]}", novelai, generator)
                continue
            parsed = _a1111(_text(value))
            if parsed:
                yield parsed
            value = _safe_json(value)
        if key in ("prompt", "workflow"):
            # These fields carry ComfyUI graphs. A JSON-looking caption with
            # {"model": "..."} is still prompt content, not model provenance.
            parsed = _comfy(value)
            if parsed:
                yield from _generation_records(value, f"元数据.{key}", novelai, generator)
            continue
        if key == "civitai_resources":
            yield from _civitai_records({key: value}, "元数据")
            continue
        yield from _generation_records(value, f"元数据.{key}", novelai, "InvokeAI" if key == "invokeai_metadata" else generator)
    direct = {key: value for key, value in values.items() if key in
              _MODEL_FIELDS + _BASE_FIELDS + _ARCH_FIELDS + _HASH_FIELDS + ("hashes", "tags", "artist", "author", "creator", "ai", "is_ai")}
    if direct:
        parsed = _structured(direct, novelai, "图片字段")
        if parsed:
            yield parsed


def _family_from_name(name: str) -> set[str]:
    text = re.sub(r"[_/\\]+", " ", name.casefold())
    # Adapter names are not checkpoint ancestry, even if a family word appears.
    if re.search(r"(?<![a-z0-9])(?:lora|lycoris|lyco|hypernet)(?![a-z0-9])", text):
        return set()
    matches = set()
    for family, pattern in (
        ("Anima", r"(?<![a-z0-9])anima(?![a-z0-9])"),
        ("Stable Diffusion", r"(?<![a-z0-9])(?:stable[ .-]*diffusion(?:[ .-]*xl)?|sdxl|sd[ .-]*(?:v[ .-]*)?(?:xl|1(?:[ .-]?[45])?|2(?:[ .-]?[01])?|3(?:[ .-]?5)?)|sd)(?![a-z0-9])"),
        ("Krea 2", r"(?<![a-z0-9])krea[ .-]*2(?![a-z0-9])"),
        ("FLUX", r"(?<![a-z0-9])flux(?:[ .-]*[12])?(?![a-z0-9])"),
        ("NovelAI", r"(?<![a-z0-9])(?:novel[ .-]*ai|nai[ .-]*diffusion)(?![a-z0-9])"),
    ):
        if re.search(pattern, text):
            matches.add(family)
    return matches


def _architecture_info(value: str, *, ancestry=False) -> tuple[set[str], str]:
    """Explicit generator class/architecture aliases, not arbitrary XL names."""
    flat = re.sub(r"[^a-z0-9]", "", value.casefold())
    variant = ""
    families = _family_from_name(value)
    # Civitai's explicit ecosystem/baseModel enumerations, verified in
    # packages/civitai-shared/src/basemodel.constants.ts: Pony, Illustrious and
    # NoobAI have parentEcosystemId ECO.SDXL. PonyV7 instead has AuraFlow ancestry
    # and is deliberately not included. A checkpoint named 'pony' is not enough.
    if ancestry and flat in ("pony", "illustrious", "noobai", "sdxldistilled"):
        families.add("Stable Diffusion")
        variant = "SDXL"
    if re.fullmatch(r"stablediffusionxl(?:[a-z0-9]*pipeline)?", flat) or flat in ("sdxlbasev10", "sdxlrefinerv10"):
        families.add("Stable Diffusion")
        variant = "SDXL"
    elif flat in ("sd3transformer2dmodel", "stablediffusion3pipeline", "stablediffusion3img2imgpipeline", "stablediffusion3inpaintpipeline"):
        families.add("Stable Diffusion")
        variant = "SD3"
    elif flat in ("stablediffusionpipeline", "stablediffusionimg2imgpipeline", "stablediffusioninpaintpipeline"):
        families.add("Stable Diffusion")
    elif flat in ("fluxtransformer2dmodel", "fluxpipeline", "fluximg2imgpipeline", "fluxinpaintpipeline"):
        families.add("FLUX")
    if not variant and "Stable Diffusion" in families:
        normalized = re.sub(r"[_/\\]+", " ", value.casefold())
        if re.search(r"(?<![a-z0-9])(?:sdxl|stable[ .-]*diffusion[ .-]*xl)(?![a-z0-9])", normalized):
            variant = "SDXL"
        else:
            match = re.search(r"(?<![a-z0-9])(?:sd|stable[ .-]*diffusion)[ .-]*(?:v[ .-]*)?(3[ .-]?5|3|2[ .-]?[01]|2|1[ .-]?[45]|1)(?![a-z0-9])", normalized)
            if match:
                number = re.sub(r"[ .-]", "", match.group(1))
                variant = "SD" + (number[0] + "." + number[1:] if len(number) > 1 else number)
    return families, variant


def _checkpoint_hash_values(values: dict[str, Any]) -> list[str]:
    """Checkpoint file hashes only; BLAKE3/tensor hashes have separate fields."""
    normalized = {_key(key): value for key, value in values.items()}
    hashes = [normalized[key] for key in _HASH_FIELDS if key in normalized]
    nested = normalized.get("hashes")
    nested = _safe_json(nested) if isinstance(nested, str) else nested
    if isinstance(nested, dict):
        hashes.extend(value for key, value in nested.items() if _key(key) in ("model", "checkpoint"))
    checkpoint_hashes = normalized.get("checkpoint_hashes")
    if isinstance(checkpoint_hashes, list):
        hashes.extend(checkpoint_hashes[:MAX_EVIDENCE])
    return list(dict.fromkeys(value.casefold() for value in hashes
                             if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{10,64}", value)))


def _invokeai_hash_records(values: dict[str, Any]) -> list[dict[str, str]]:
    """Detached InvokeAI identities, never checkpoint file-hash evidence."""
    fields = {_key(key): value for key, value in values.items()}
    entries = fields.get("invokeai_model_hashes", [])
    records = []
    for item in entries[:MAX_EVIDENCE] if isinstance(entries, list) else []:
        if not isinstance(item, dict) or not _text(item.get("hash")):
            continue
        records.append({"checkpoint": _text(item.get("checkpoint"))[:1024],
                        "hash": _text(item["hash"]), "algorithm": "unspecified",
                        "default_algorithm": "blake3", "source": _text(item.get("source"))[:160]})
    legacy = _text(fields.get("invokeai_model_hash"))
    if legacy and not any(item["hash"].casefold() == legacy.casefold() for item in records):
        records.append({"checkpoint": "", "hash": legacy, "algorithm": "unspecified",
                        "default_algorithm": "blake3", "source": "已索引 InvokeAI hash"})
    return list({(item["checkpoint"], item["hash"].casefold(), item["source"]): item for item in records}.values())


def _parameter_merge(target: dict[str, Any], values: dict[str, Any]) -> None:
    old_evidence = target.get("model_evidence", [])
    new_evidence = values.get("model_evidence", [])
    old_models = target.get("model_candidates", [])
    new_models = values.get("model_candidates", [])
    old_ancestry = target.get("checkpoint_ancestry", [])
    new_ancestry = values.get("checkpoint_ancestry", [])
    invokeai_hashes = _invokeai_hash_records(target) + _invokeai_hash_records(values)
    first_invokeai_hash = target.get("invokeai_model_hash")
    hashes = list(dict.fromkeys(_checkpoint_hash_values(target) + _checkpoint_hash_values(values)))
    incomplete = target.get("model_provenance_incomplete") is True or values.get("model_provenance_incomplete") is True
    incomplete = incomplete or any(isinstance(record.get(key), list) and len(record[key]) > MAX_EVIDENCE
                                   for record in (target, values)
                                   for key in ("checkpoint_hashes", "checkpoint_ancestry", "model_evidence", "model_candidates"))
    target.update(values)
    if invokeai_hashes:
        invokeai_hashes = list({(item["checkpoint"], item["hash"].casefold(), item["source"]): item
                              for item in invokeai_hashes}.values())
        target["invokeai_model_hashes"] = invokeai_hashes[:MAX_EVIDENCE]
        target["invokeai_hash_algorithm"] = "unspecified"
        target["invokeai_hash_default_algorithm"] = "blake3"
        if first_invokeai_hash:
            target["invokeai_model_hash"] = first_invokeai_hash
        if len(invokeai_hashes) > MAX_EVIDENCE:
            incomplete = True
    if hashes:
        target["checkpoint_hashes"] = hashes[:MAX_EVIDENCE]
    if incomplete or len(hashes) > MAX_EVIDENCE:
        target["model_provenance_incomplete"] = True
    ancestry = []
    for entries in (old_ancestry, new_ancestry):
        if not isinstance(entries, list):
            continue
        for item in entries[:MAX_EVIDENCE]:
            if not isinstance(item, dict) or item.get("kind") not in ("base", "architecture"):
                continue
            checkpoint = _text(item.get("checkpoint"))
            record = _evidence(item.get("value"), _text(item.get("source")), item["kind"])
            if checkpoint and len(checkpoint) <= 1024 and record:
                ancestry.append({"checkpoint": checkpoint, **record})
    if ancestry:
        ancestry = list({(item["checkpoint"], item["kind"], item["value"], item["source"]): item for item in ancestry}.values())
        target["checkpoint_ancestry"] = ancestry[:MAX_EVIDENCE]
        if len(ancestry) > MAX_EVIDENCE:
            target["model_provenance_incomplete"] = True
    evidence = []
    for entries in (old_evidence, new_evidence):
        if not isinstance(entries, list):
            continue
        for item in entries[:MAX_EVIDENCE]:
            if not isinstance(item, dict) or item.get("kind") not in ("checkpoint", "base", "architecture"):
                continue
            record = _evidence(item.get("value"), _text(item.get("source")), item["kind"])
            if record:
                evidence.append(record)
    if evidence:
        evidence = list({(item.get("kind"), item.get("value"), item.get("source")): item for item in evidence}.values())
        target["model_evidence"] = evidence[:MAX_EVIDENCE]
        if len(evidence) > MAX_EVIDENCE:
            target["model_provenance_incomplete"] = True
    models = [_text(item) for entries in (old_models, new_models) if isinstance(entries, list) for item in entries[:MAX_EVIDENCE] if _text(item)]
    if models:
        models = list(dict.fromkeys(models))
        target["model_candidates"] = models[:MAX_EVIDENCE]
        if len(models) > MAX_EVIDENCE:
            target["model_provenance_incomplete"] = True


def classify_model(model: str = "", prompt: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Only checkpoint/base/architecture evidence is eligible; prompt is ignored.

    reason_code is machine-readable: explicit/conflict/unregistered/missing/non_ai.
    Confidence describes provenance specificity, not measured model accuracy.
    """
    fields = {_key(key): value for key, value in (metadata or {}).items()}
    if _auxiliary_record(fields):
        return {"family": "未知", "confidence": 0.0, "reason_code": "conflict",
                "reason": "这份模型记录显式描述辅助模型或 LoRA，不是生成图片的底模；不能将其名称、哈希或适配架构登记成 checkpoint 归属。"}
    evidence = []
    for key in _BASE_FIELDS + _ARCH_FIELDS:
        record = _evidence(fields.get(key), f"字段.{key}", "base" if key in _BASE_FIELDS else "architecture")
        if record:
            evidence.append(record)
    for item in fields.get("model_evidence", [])[:MAX_EVIDENCE] if isinstance(fields.get("model_evidence"), list) else []:
        if isinstance(item, dict) and item.get("kind") in ("checkpoint", "base", "architecture"):
            record = _evidence(item.get("value"), _text(item.get("source")) or "已索引证据", item["kind"])
            if record:
                evidence.append(record)
    clean_model = re.sub(r"<[^>]*>", "", _text(model))
    if clean_model:
        evidence.append({"value": clean_model, "source": "模型名称", "kind": "checkpoint"})
    software = _text(fields.get("software", ""))
    if re.search(r"\bnovelai\b", software, re.I):
        evidence.append({"value": "NovelAI", "source": "生成软件字段", "kind": "base"})

    def result(family, confidence, reason, code, variant=""):
        output = {"family": family, "confidence": confidence, "reason": reason, "reason_code": code}
        if variant:
            output["model_variant"] = variant
        return output

    markers = fields.get("ai_markers", [])
    markers = markers if isinstance(markers, list) else []
    non_ai = fields.get("ai") is False or fields.get("is_ai") is False or False in markers
    if non_ai:
        if evidence or fields.get("ai_evidence") or True in markers:
            return result("未知", 0.0, "非 AI 标记与生成模型证据冲突，需人工确认。", "conflict")
        return result("非 AI", 0.95, "元数据显式标记为非 AI 图片。", "non_ai")
    if fields.get("model_provenance_incomplete") is True:
        return result("未知", 0.0, "模型资源记录超过读取上限或类型/AIR证据损坏，不能用不完整证据自动确定家族。", "conflict")
    candidates = fields.get("model_candidates")
    candidate_families = []
    if isinstance(candidates, list):
        if len(candidates) > MAX_EVIDENCE:
            return result("未知", 0.0, "实际 checkpoint 数量超过读取上限，不能忽略剩余模型。", "conflict")
        for candidate in candidates:
            text = _text(candidate)
            if text:
                evidence.append({"value": text, "source": "实际 checkpoint 候选", "kind": "checkpoint"})
                known = _architecture_info(text)[0]
                ancestry = fields.get("checkpoint_ancestry", [])
                for item in ancestry[:MAX_EVIDENCE] if isinstance(ancestry, list) else []:
                    if not isinstance(item, dict) or item.get("kind") not in ("base", "architecture") or _checkpoint_identity(item.get("checkpoint")) != _checkpoint_identity(text):
                        continue
                    record = _evidence(item.get("value"), _text(item.get("source")), item["kind"])
                    if record:
                        evidence.append(record)
                        known.update(_architecture_info(record["value"], ancestry=True)[0])
                candidate_families.append(known)

    recognized = [(item, *_architecture_info(item["value"], ancestry=item["kind"] in ("base", "architecture"))) for item in evidence]
    families = set().union(*(families for _, families, _ in recognized)) if recognized else set()
    variants = {variant for _, _, variant in recognized if variant}
    variant_groups = {variant[:3] if variant.startswith(("SD1", "SD2", "SD3")) else variant for variant in variants}
    if len(families) > 1 or len(variant_groups) > 1:
        names = "、".join(sorted(families if len(families) > 1 else variants))
        return result("未知", 0.0, f"明确模型/底模/架构字段出现冲突（{names}），不能自动认定归属。", "conflict")
    if len(candidate_families) > 1 and any(len(value) != 1 for value in candidate_families):
        output = result("未知", 0.0, "出现多个实际 checkpoint；部分模型归属未知，不能忽略其中的模型。", "conflict")
        output["conflict_kind"] = "unresolved_checkpoints"
        return output
    if len(families) == 1:
        family = next(iter(families))
        item = next(item for item, matched, _ in recognized if family in matched)
        source_label = "模型名称" if item["kind"] == "checkpoint" else "显式底模/架构字段"
        variant = next(iter(variants)) if len(variants) == 1 else next(iter(variant_groups), "")
        if len(variants) == 2 and variant in variants:
            variant = max(variants, key=len)
        detail = f"（{variant}）" if variant else ""
        return result(family, 0.85 if item["kind"] == "checkpoint" else 0.95,
                      f"{source_label} {item['source']} 提供 {family}{detail} 的明确标识；模型昵称和普通提示词不参与猜测。",
                      "explicit", variant)
    if clean_model or any(item["kind"] == "checkpoint" for item in evidence):
        return result("未知", 0.0, "已读取 checkpoint 名称，但没有明确底模/架构标识；需登记名称或哈希映射，普通 TAG、LoRA 和哈希本身不能证明归属。", "unregistered")
    if fields.get("ai_evidence") or evidence:
        return result("未知", 0.0, "发现生成元数据，但缺少可识别的 checkpoint 或底模/架构标识；自定义节点的未命名 widget 不作猜测。", "missing")
    return result("未知", 0.0, "没有可识别的生成元数据；可能已被导出软件移除，不能据此认定为非 AI。", "missing")


def extract_metadata(path: str | Path, *, read_sidecars: bool = True) -> dict[str, Any]:
    """Extract JSON-compatible metadata without loading image pixels."""
    path = Path(path)
    width, height, image_format, raw = _read_header(path)
    raw = _bound_raw(raw)
    values = {str(key).casefold(): value for key, value in raw.items()}
    software = _text(values.get("software", ""))
    source = _text(values.get("source", ""))
    novelai = bool(re.search(r"\bnovelai\b", software, re.I)) or _family_from_name(source) == {"NovelAI"}
    result: dict[str, Any] = {"width": width, "height": height, "format": image_format,
                              "model": "", "prompt": "", "negative_prompt": "", "tags": [],
                              "parameters": {}, "artist": _text(values.get("artist", values.get("author", "")))}
    ai_evidence = False
    provenance_models: list[str] = []
    ai_markers = []

    def merge(parsed: dict[str, Any] | None) -> None:
        nonlocal ai_evidence
        if not parsed:
            return
        ai_evidence = ai_evidence or parsed.get("ai_evidence", False)
        for key in ("ai", "is_ai"):
            marker = parsed.get("parameters", {}).get(key)
            if isinstance(marker, bool):
                ai_markers.append(marker)
        candidates = parsed.get("parameters", {}).get("model_candidates", [])
        candidates = candidates if isinstance(candidates, list) and candidates else [parsed.get("model", "")]
        provenance_models.extend(_text(candidate) for candidate in candidates if _text(candidate))
        for key in ("model", "prompt", "negative_prompt", "artist"):
            if parsed.get(key) and not result[key]:
                result[key] = parsed[key]
        if parsed.get("tags"):
            result["tags"].extend(parsed["tags"])
        _parameter_merge(result["parameters"], parsed.get("parameters", {}))

    # Parse provenance-bearing fields first. Generic descriptions may be captions.
    for parsed in _metadata_records(raw):
        merge(parsed)
    description = _text(values.get("description", ""))
    plain_prompt = _text(values.get("prompt", values.get("positive_prompt", "")))
    if not result["prompt"] and plain_prompt and plain_prompt[:1] not in ("{", "["):
        result["prompt"] = plain_prompt
        ai_evidence = True
    if not result["negative_prompt"]:
        result["negative_prompt"] = _text(values.get("negative_prompt", ""))
    if novelai:
        ai_evidence = True
        if not result["prompt"]:
            result["prompt"] = description
        result["parameters"]["software"] = "NovelAI"
        if not result["model"]:
            result["model"] = _text(values.get("source", ""))

    # Prefer existing TXT metadata while allowing JSON to supply absent artist,
    # TAG or model fields. Both conventional stem and full-filename forms are
    # bounded. Different explicit model sources remain visible as uncertainty.
    sidecar_paths = (path.with_suffix(".txt"), Path(str(path) + ".txt"),
                     path.with_suffix(".json"), Path(str(path) + ".json"))
    for sidecar in dict.fromkeys(sidecar_paths) if read_sidecars else ():
        try:
            label = sidecar.suffix[1:].upper()
            if sidecar.is_symlink():
                raw["sidecar_warning"] = f"{label} 伴随文件是符号链接，已跳过"
                continue
            with sidecar.open("rb") as stream:
                data = stream.read(MAX_TEXT_BYTES + 1)
            if len(data) > MAX_TEXT_BYTES:
                raw["sidecar_warning"] = f"{label} 伴随文件超过 512 KiB，已跳过"
                continue
            if data.startswith((b"\xff\xfe", b"\xfe\xff")):
                content = data.decode("utf-16", "replace")
            else:
                content = data.decode("utf-8-sig", "replace")
            structured = _safe_json(content)
            if sidecar.suffix.lower() == ".json" and not isinstance(structured, dict):
                raw["sidecar_warning"] = "JSON 伴随文件不是有效对象，已跳过"
                continue
            raw.setdefault("sidecar_name", sidecar.name)
            raw.setdefault("sidecar_text", content)
            raw.setdefault("sidecar_files", []).append(sidecar.name)
            parsed = _a1111(content)
            records = [parsed] if parsed else list(_generation_records(structured, f"伴随文件.{label}"))
            for record in records:
                merge(record)
            if not records and not result["prompt"] and sidecar.suffix.lower() == ".txt":
                result["prompt"] = content.strip()
        except FileNotFoundError:
            continue
        except OSError:
            raw["sidecar_warning"] = f"{label} 伴随文件读取失败"
    pixiv = re.match(r"^(\d{5,})_p(\d+)(?:\D|$)", path.stem)
    if pixiv:
        result["parameters"]["pixiv_id"] = pixiv.group(1)
        result["parameters"]["pixiv_page"] = int(pixiv.group(2))
    result["tags"] = list(dict.fromkeys(result["tags"] + parse_tags(result["prompt"])))[:MAX_TAGS]
    provenance_models = list(dict.fromkeys(provenance_models))
    if len(provenance_models) > 1:
        result["parameters"]["model_candidates"] = provenance_models
    if ai_markers:
        result["parameters"]["ai_markers"] = list(dict.fromkeys(ai_markers))
    result["parameters"] = _bound_raw(result["parameters"])
    classification_data = {**result["parameters"], "ai_evidence": ai_evidence}
    result.update(classify_model(result["model"], result["prompt"], classification_data))
    if result.get("model_variant"):
        result["parameters"]["model_variant"] = result["model_variant"]
    result["raw_metadata"] = _bound_raw(raw)
    return result


def reclassify_indexed(image: dict[str, Any]) -> dict[str, Any]:
    """Reparse saved provenance only. Never access path, pixels or sidecar files.

    Returns model/parameters and classification, deliberately no prompt/TAG keys.
    Old cached PNG strings and sidecar_text use the same bounded parser as import.
    A user registry can inspect reason_code and every preserved model_candidate.
    """
    model = _text(image.get("model", ""))
    existing = image.get("parameters", {})
    params = dict(existing) if isinstance(existing, dict) else {}
    raw = image.get("raw_metadata", {})
    raw = raw if isinstance(raw, dict) else {}
    ai_evidence = bool(model)
    markers = list(params.get("ai_markers", [])) if isinstance(params.get("ai_markers"), list) else []
    records = list(_metadata_records(raw))
    fooocus_models = {_checkpoint_identity(value) for record in records
                     if record.get("parameters", {}).get("metadata_source") == "Fooocus"
                     for value in record.get("parameters", {}).get("model_candidates", [])}
    if fooocus_models:
        for key in list(params):
            if _key(key) == "base_model" and _checkpoint_identity(params[key]) in fooocus_models:
                params.pop(key)
        if isinstance(params.get("model_evidence"), list):
            params["model_evidence"] = [item for item in params["model_evidence"][:MAX_EVIDENCE]
                                        if not isinstance(item, dict) or item.get("kind") != "base"
                                        or _checkpoint_identity(item.get("value")) not in fooocus_models]
    # Older versions interpreted InvokeAI ModelIdentifierField.hash as a
    # generic model_hash. Correct that cached field using retained provenance,
    # without reading a single image/sidecar or mutating the input dictionary.
    independent_hashes = {item["hash"].casefold()
                          for values in [params] + [record.get("parameters", {}) for record in records]
                          for item in _invokeai_hash_records(values)}
    file_hashes = set(value for record in records for value in _checkpoint_hash_values(record.get("parameters", {})))
    excluded = independent_hashes - file_hashes
    for key in list(params):
        if _key(key) in _HASH_FIELDS and _text(params[key]).casefold() in excluded:
            params.pop(key)
        elif _key(key) == "checkpoint_hashes" and isinstance(params[key], list):
            params[key] = [value for value in params[key][:MAX_EVIDENCE]
                           if _text(value).casefold() not in excluded]
        elif _key(key) == "hashes" or str(key).casefold() == "hashes":
            hashes = _safe_json(params[key]) if isinstance(params[key], str) else params[key]
            if isinstance(hashes, dict):
                params[key] = {name: value for name, value in hashes.items()
                               if str(name).casefold() not in ("model", "checkpoint") or _text(value).casefold() not in excluded}
    records.extend(_generation_records(params, "已索引参数"))
    models = [_text(value) for value in params.get("model_candidates", []) if _text(value)] if isinstance(params.get("model_candidates"), list) else []
    if model:
        # A ComfyUI display value joins names; its explicit candidates must be
        # used individually, never interpreted as an extra checkpoint.
        if not models:
            models.append(model)
    for record in records:
        ai_evidence = ai_evidence or bool(record.get("ai_evidence"))
        new_params = record.get("parameters", {})
        _parameter_merge(params, new_params)
        candidates = new_params.get("model_candidates", [])
        if isinstance(candidates, list):
            models.extend(_text(value) for value in candidates if _text(value))
        if not model and record.get("model"):
            model = _text(record["model"])
        for key in ("ai", "is_ai"):
            if isinstance(new_params.get(key), bool):
                markers.append(new_params[key])
    raw_fields = {_key(key): value for key, value in raw.items()}
    if re.search(r"\bnovelai\b", _text(raw_fields.get("software")), re.I):
        params["software"] = "NovelAI"
        ai_evidence = True
        if not model and _family_from_name(_text(raw_fields.get("source"))) == {"NovelAI"}:
            model = _text(raw_fields["source"])
    if models:
        params["model_candidates"] = list(dict.fromkeys(models))[:MAX_EVIDENCE]
    if markers:
        params["ai_markers"] = list(dict.fromkeys(markers))
    params.pop("model_variant", None)
    params = _bound_raw(params)
    classification = classify_model(model, metadata={**params, "ai_evidence": ai_evidence})
    if classification.get("model_variant"):
        params["model_variant"] = classification["model_variant"]
    return {**classification, "model": model, "parameters": params}
