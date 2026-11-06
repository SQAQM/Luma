"""Explicit bounded Civitai lookups; never scan a file or fetch images implicitly.

The only identifier sent is the hash passed by the caller. API records are
source claims, not proof of training ancestry. Auxiliary resource types stay
intact so callers cannot mistake a LoRA base model for an image checkpoint.
"""
from __future__ import annotations

from html.parser import HTMLParser
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings

from PIL import Image

from .credentials import validate_token


API_ORIGIN = "https://civitai.com"
API_BASE = API_ORIGIN + "/api/v1"
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_COVER_BYTES = 8 * 1024 * 1024
_TIMEOUT = 10
_TOTAL_TIMEOUT = 30
_MESSAGES = {
    "unauthorized": "Civitai 密钥无效或已过期，请重新设置。",
    "forbidden": "Civitai 拒绝访问此资源。",
    "not_found": "Civitai 未找到公开的模型记录。",
    "rate_limited": "Civitai 请求过于频繁，请稍后手动重试。",
    "unavailable": "Civitai 暂时不可用，请稍后重试。",
    "network": "无法连接 Civitai，请检查网络后重试。",
    "redirect": "为保护密钥，已拒绝 Civitai 重定向。",
    "response_too_large": "Civitai 响应超过安全大小限制。",
    "invalid_response": "Civitai 返回了无法验证的数据。",
    "invalid_hash": "仅支持完整 SHA256 或明确的10位 AutoV2 哈希。",
    "hash_mismatch": "Civitai 文件哈希与请求不符或存在冲突，未确认模型身份。",
    "invalid_id": "Civitai 模型编号无效。",
    "invalid_cover": "封面来源、格式或尺寸不符合安全限制。",
    "credentials": "无法安全读取 Civitai 密钥，请重新设置。",
    "cancelled": "Civitai 操作已取消。",
}


class CivitaiError(RuntimeError):
    def __init__(self, code, message=None):
        # Never propagate body, URL, hash, token or arbitrary upstream text.
        self.code = code if code in _MESSAGES else "invalid_response"
        self.message = _MESSAGES[self.code]
        super().__init__(self.message)


def sync_feedback(message, status):
    """Export only our fixed sync vocabulary; never arbitrary job error text."""
    fallback = {'completed': '模型资料已更新。', 'completed_with_errors': '部分模型资料未获取，请检查连接后重试。',
                'failed': '模型资料获取失败，请检查连接和目录。', 'cancelled': '同步已取消，已保存的资料保留。'}
    default = fallback.get(status, '正在同步模型资料。')
    if not isinstance(message, str) or len(message) > 512:
        return default
    if message in ('正在流式计算所选模型SHA256；可请求取消', '正在获取模型封面'):
        return message
    match = re.fullmatch(r'(?:模型资料同步完成|正在计算哈希并同步模型资料)'
                         r'(?:；封面已保存 ([0-9]{1,10}) 个)?'
                         r'(?:；([0-9]{1,10}) 个模型未提供静态封面)?(?:；(.+))?', message)
    if not match:
        return default
    error = match.group(3)
    allowed = set(_MESSAGES.values()) | {'部分文件同步失败；请重新索引或检查网络后重试'}
    return message if error is None or error in allowed else default


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _text(value, maximum=500):
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value[:maximum] if ord(c) >= 32 or c in "\n\t").strip()


def normalize_trigger_words(value):
    """Bound explicit upstream trainedWords; never infer words from other text."""
    if not isinstance(value, list):
        return []
    return [word for word in (_text(item) for item in value[:128]) if word]


class _PlainText(HTMLParser):
    _HIDDEN = frozenset(("script", "style", "iframe", "object", "svg", "template"))
    _BREAK = frozenset(("p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "blockquote"))

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], []

    def handle_starttag(self, tag, attrs):
        if tag in self._HIDDEN:
            self.hidden.append(tag)
        elif not self.hidden and tag in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if not self.hidden and tag in self._BREAK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _plain_text(value):
    parser = _PlainText()
    parser.feed(_text(value, 200000))
    parser.close()
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)[:20000]


# Exact upstream baseModel labels only. No substring/name/tag inference.
_BASES = {
    "SD 1.4": ("Stable Diffusion", "SD 1.4"),
    "SD 1.5": ("Stable Diffusion", "SD 1.5"),
    "SD 1.5 LCM": ("Stable Diffusion", "SD 1.5 LCM"),
    "SD 2.0": ("Stable Diffusion", "SD 2.0"),
    "SD 2.0 768": ("Stable Diffusion", "SD 2.0 768"),
    "SD 2.1": ("Stable Diffusion", "SD 2.1"),
    "SD 2.1 768": ("Stable Diffusion", "SD 2.1 768"),
    "SDXL 0.9": ("Stable Diffusion", "SDXL 0.9"),
    "SDXL 1.0": ("Stable Diffusion", "SDXL 1.0"),
    "SDXL 1.0 LCM": ("Stable Diffusion", "SDXL 1.0 LCM"),
    "SDXL Turbo": ("Stable Diffusion", "SDXL Turbo"),
    "SDXL Lightning": ("Stable Diffusion", "SDXL Lightning"),
    "Pony": ("Stable Diffusion", "SDXL / Pony"),
    "Illustrious": ("Stable Diffusion", "SDXL / Illustrious"),
    "SD 3": ("Stable Diffusion", "SD 3"),
    "SD 3.5": ("Stable Diffusion", "SD 3.5"),
    "SD 3.5 Large": ("Stable Diffusion", "SD 3.5 Large"),
    "SD 3.5 Large Turbo": ("Stable Diffusion", "SD 3.5 Large Turbo"),
    "SD 3.5 Medium": ("Stable Diffusion", "SD 3.5 Medium"),
    "Flux.1 D": ("FLUX", "FLUX.1 dev"),
    "Flux.1 S": ("FLUX", "FLUX.1 schnell"),
    "Flux.1 Krea": ("FLUX", "FLUX.1 Krea"),
    "Anima": ("Anima", "Anima"),
    "Krea 2": ("Krea 2", "Krea 2"),
    "Krea2": ("Krea 2", "Krea 2"),
    "NovelAI": ("NovelAI", "NovelAI"),
}


def _identifier(value):
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise CivitaiError("invalid_response")
    return value


def _hash(value, length):
    return value.lower() if isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{%d}" % length, value) else ""


def _cover_url(url):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 or ord(c) > 126 for c in url):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname
        public_source = host == "image.civitai.com" or (host == "blobs-b2.civitai.com"
                and re.fullmatch(r"/file/blobs-managed-public/[A-Za-z0-9_-]{10,200}(?:\.[A-Za-z0-9]{1,8})?", parsed.path) is not None)
        return (parsed.scheme == "https" and public_source
                and parsed.netloc in (host, host + ":443")
                and parsed.username is None and parsed.password is None
                and parsed.port in (None, 443) and parsed.path.startswith("/")
                and "\\" not in url and not parsed.query and not parsed.fragment)
    except ValueError:
        return False


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _preview_images(raw):
    """Keep only bounded public preview descriptors, never prompt/meta fields."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise CivitaiError("invalid_response")
    result = []
    for item in raw[:32]:
        if not isinstance(item, dict):
            continue
        url, kind = item.get('url'), item.get('type')
        thumbnail = item.get('thumbnail')
        if kind == 'video' and isinstance(thumbnail, dict) and _cover_url(thumbnail.get('url')):
            url, kind = thumbnail['url'], 'image'
        if kind == 'audio' or not _cover_url(url):
            continue
        level, nsfw = item.get('nsfwLevel'), item.get('nsfw')
        safe = nsfw is False or nsfw == 'None' or (type(level) is int and level == 1)
        unsafe = (type(level) is int and level != 1) or (nsfw is not None and nsfw is not False and nsfw != 'None')
        image = {'url': url, 'nsfw': unsafe or not safe}
        if kind in ('image', 'video'):
            image['type'] = kind
        result.append(image)
    return result


class CivitaiClient:
    def __init__(self, token_provider=None, transport=None):
        self._token_provider = token_provider
        self._transport = transport if transport is not None else urllib.request.build_opener(_NoRedirect())

    def _token(self):
        try:
            token = self._token_provider() if self._token_provider else ""
            return "" if token == "" else validate_token(token)
        except Exception:
            raise CivitaiError("credentials") from None

    @staticmethod
    def _cancel(cancelled):
        if cancelled():
            raise CivitaiError("cancelled")

    def _read(self, url, maximum, cancelled, *, token="", cover=False, _redirects=0, _deadline=None):
        self._cancel(cancelled)
        started = time.monotonic()
        deadline = started + _TOTAL_TIMEOUT if _deadline is None else _deadline
        if started >= deadline:
            raise CivitaiError("network")
        def moved(location):
            target = urllib.parse.urljoin(url, location) if isinstance(location, str) else ""
            if not cover or token or _redirects >= 2 or not _cover_url(target):
                raise CivitaiError("redirect")
            self._cancel(cancelled)
            return self._read(target, maximum, cancelled, cover=True, _redirects=_redirects + 1, _deadline=deadline)
        headers = {"User-Agent": "Luma/1.2.1 Civitai metadata client", "Accept-Encoding": "identity",
                   "Accept": "image/png,image/jpeg,image/webp" if cover else "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(url, headers=headers, method="GET")
        response = None
        try:
            response = self._transport.open(request, timeout=min(_TIMEOUT, deadline - started))
            self._cancel(cancelled)
            status = response.status
            if 300 <= status < 400:
                location = response.headers.get("Location")
                response.close(); response = None
                return moved(location)
            if status != 200:
                raise CivitaiError(self._status_code(status))
            final_url = response.geturl()
            if final_url and final_url != url:
                raise CivitaiError("redirect")
            length = response.headers.get("Content-Length")
            if length is not None:
                if not re.fullmatch(r"[0-9]{1,20}", str(length)):
                    raise CivitaiError("invalid_response")
                if int(length) > maximum:
                    raise CivitaiError("response_too_large")
            encoding = response.headers.get("Content-Encoding", "identity")
            if encoding.strip().lower() not in ("", "identity"):
                raise CivitaiError("invalid_response")
            blocks, total = [], 0
            while True:
                self._cancel(cancelled)
                if time.monotonic() >= deadline:
                    raise CivitaiError("network")
                block = response.read(min(65536, maximum + 1 - total))
                self._cancel(cancelled)
                if not block:
                    break
                total += len(block)
                if total > maximum:
                    raise CivitaiError("response_too_large")
                blocks.append(block)
            if length is not None and total != int(length):
                raise CivitaiError("invalid_response")
            return b"".join(blocks)
        except urllib.error.HTTPError as error:
            status = error.code
            location = error.headers.get("Location") if error.headers else None
            try:
                error.close()
            except Exception:
                pass
            if 300 <= status < 400:
                return moved(location)
            raise CivitaiError(self._status_code(status)) from None
        except CivitaiError:
            raise
        except Exception:
            raise CivitaiError("network") from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    @staticmethod
    def _status_code(status):
        if 300 <= status < 400:
            return "redirect"
        return {401: "unauthorized", 403: "forbidden", 404: "not_found", 429: "rate_limited"}.get(status, "unavailable")

    def _json(self, path, cancelled, *, token=None):
        blob = self._read(API_BASE + path, MAX_JSON_BYTES, cancelled, token=self._token() if token is None else token)
        try:
            result = json.loads(blob.decode("utf-8"), object_pairs_hook=_unique_pairs)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (ValueError, UnicodeError, RecursionError):
            raise CivitaiError("invalid_response") from None

    def version_by_hash(self, digest, cancelled=lambda: False):
        if not isinstance(digest, str) or len(digest) not in (10, 64) or not _hash(digest, len(digest)):
            raise CivitaiError("invalid_hash")
        digest = digest.lower()
        self._cancel(cancelled)
        return self._version(self._json("/model-versions/by-hash/" + digest, cancelled), digest, cancelled)

    def version_by_id(self, version_id, digest, cancelled=lambda: False):
        if type(version_id) is not int or not 0 < version_id <= 2**63 - 1:
            raise CivitaiError('invalid_id')
        if not isinstance(digest, str) or len(digest) != 64 or not _hash(digest, 64):
            raise CivitaiError('invalid_hash')
        record = self._version(self._json(f'/model-versions/{version_id}', cancelled), digest.lower(), cancelled)
        if record['version_id'] != version_id:
            raise CivitaiError('invalid_response')
        return record

    def _version(self, value, digest, cancelled):
        model_id, version_id = _identifier(value.get("modelId")), _identifier(value.get("id"))
        model = value.get("model", {})
        if not isinstance(model, dict):
            raise CivitaiError("invalid_response")
        model_type = _text(model.get("type"), 100)
        raw_files = value.get("files")
        if not isinstance(raw_files, list) or len(raw_files) > 512:
            raise CivitaiError("invalid_response")
        files, matching = [], []
        for item in raw_files:
            if not isinstance(item, dict):
                raise CivitaiError("invalid_response")
            hashes = item.get("hashes", {})
            if not isinstance(hashes, dict):
                raise CivitaiError("invalid_response")
            file = {"name": _text(item.get("name")), "sha256": _hash(hashes.get("SHA256"), 64),
                    "autov2": _hash(hashes.get("AutoV2"), 10), "type": _text(item.get("type"), 100)}
            # A checkpoint version may ship VAE/config/training attachments.
            # Its parent's type cannot confer checkpoint identity on those bytes.
            file["resource_type"] = model_type if file["type"].casefold() == "model" else "VAE" if file["type"].casefold() == "vae" else "Unknown"
            files.append(file)
            if file["sha256" if len(digest) == 64 else "autov2"] == digest:
                matching.append(file)
        if not matching or (len(digest) == 10 and (not all(f["sha256"] for f in matching)
                                                  or len({f["sha256"] for f in matching}) != 1)):
            raise CivitaiError("hash_mismatch")
        base = _text(value.get("baseModel"), 200)
        family, variant = _BASES.get(base, ("未知", "未知"))
        if family == "Stable Diffusion":
            # Match the classifier's architecture vocabulary. The original
            # upstream subtype stays intact in base_model for display.
            sd = re.match(r"SD\s*([123](?:\.\d+)?)", variant)
            variant = "SDXL" if variant.startswith("SDXL") else "SD" + sd[1] if sd else variant
        roles = {file["resource_type"] for file in matching}
        matched_type = next(iter(roles)) if len(roles) == 1 else "Unknown"
        images = _preview_images(value.get('images', []))
        trained = value.get("trainedWords", [])
        if not isinstance(trained, list):
            raise CivitaiError("invalid_response")
        trained = normalize_trigger_words(trained)
        self._cancel(cancelled)
        creator = model.get("creator", {})
        return {"model_id": model_id, "version_id": version_id, "model_name": _text(model.get("name")),
                "version_name": _text(value.get("name")), "type": matched_type, "model_type": model_type,
                "base_model": base, "family": family, "model_variant": variant,
                "description": _plain_text(model.get("description")),
                "version_description": _plain_text(value.get("description")),
                "creator": _text(creator.get("username")) if isinstance(creator, dict) else "",
                "trained_words": trained, "trigger_words": list(trained),
                "files": files, "images": images, "matched_sha256": matching[0]["sha256"],
                "matched_hash_kind": "sha256" if len(digest) == 64 else "autov2",
                "source_url": f"{API_ORIGIN}/models/{model_id}?modelVersionId={version_id}",
                "updated_at": _text(value.get("updatedAt"), 100)}

    def model(self, model_id, cancelled=lambda: False):
        if type(model_id) is not int or not 0 < model_id <= 2**63 - 1:
            raise CivitaiError("invalid_id")
        self._cancel(cancelled)
        value = self._json(f"/models/{model_id}", cancelled)
        if _identifier(value.get("id")) != model_id:
            raise CivitaiError("invalid_response")
        creator = value.get("creator", {})
        versions = []
        raw_versions = value.get('modelVersions', [])
        if isinstance(raw_versions, list):
            for version in raw_versions[:128]:
                if not isinstance(version, dict) or type(version.get('id')) is not int or not 0 < version['id'] <= 2**63 - 1:
                    continue
                if version.get('modelId', model_id) != model_id:
                    continue
                versions.append({'version_id': version['id'], 'images': _preview_images(version.get('images', []))})
        self._cancel(cancelled)
        return {"model_id": model_id, "model_name": _text(value.get("name")),
                "type": _text(value.get("type"), 100), "description": _plain_text(value.get("description")),
                "creator": _text(creator.get("username")) if isinstance(creator, dict) else "",
                "source_url": f"{API_ORIGIN}/models/{model_id}", 'versions': versions}

    def download_cover(self, url, cancelled=lambda: False):
        if not _cover_url(url):
            raise CivitaiError("invalid_cover")
        parsed = urllib.parse.urlsplit(url)
        parts = parsed.path.split("/")
        if parsed.hostname == "image.civitai.com" and len(parts) == 5 and parts[3] == "original=true":
            parts[3] = "width=640,height=640,fit=scale-down,quality=85,anim=false"
            url = urllib.parse.urlunsplit(parsed._replace(path="/".join(parts)))
        blob = self._read(url, MAX_COVER_BYTES, cancelled, cover=True)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(blob)) as image:
                    width, height = image.size
                    if (image.format not in ("PNG", "JPEG", "WEBP") or min(width, height) < 1
                            or max(width, height) > 8192 or width * height > 16000000
                            or getattr(image, "n_frames", 1) != 1):
                        raise ValueError()
        except Exception:
            raise CivitaiError("invalid_cover") from None
        self._cancel(cancelled)
        return blob

    def check_connection(self):
        try:
            token = self._token()
            value = self._json("/me" if token else "/models?limit=1", lambda: False, token=token)
            if token:
                _identifier(value.get("id"))
            elif not isinstance(value.get("items"), list):
                raise CivitaiError("invalid_response")
            return {"ok": True, "authenticated": bool(token), "code": "ok"}
        except CivitaiError as error:
            return {"ok": False, "authenticated": False, "code": error.code, "message": error.message}
