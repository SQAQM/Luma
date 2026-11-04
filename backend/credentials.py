"""Civitai session credentials and user-bound Windows DPAPI persistence.

No database, environment key, plaintext fallback or default library is used.
The caller supplies the independent application credentials directory.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import tempfile
import threading


_MAGIC = b"LUMA-CIVITAI-DPAPI-1\n"
_MAX_FILE = 65536


class CredentialsError(RuntimeError):
    """Safe error suitable for display, never containing the secret/path."""
    def __init__(self, code="credentials"):
        self.code = code
        super().__init__("无法安全读取或保存 Civitai 密钥，请重新设置或清除密钥。")


def validate_token(token):
    if not isinstance(token, str) or not re.fullmatch(r"[!-~]{1,4096}", token):
        raise CredentialsError("invalid_token")
    return token


def _crypt(blob, *, decrypt=False):
    if os.name != "nt":
        raise CredentialsError("protection_unavailable")

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(blob)
    source = DataBlob(len(blob), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        call = crypt32.CryptUnprotectData
        call.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob)]
    else:
        call = crypt32.CryptProtectData
        call.argtypes = [ctypes.POINTER(DataBlob), wintypes.LPCWSTR, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob)]
    call.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN, current user scope (never LOCAL_MACHINE).
    if not call(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise CredentialsError("protection_failed")
    try:
        if not output.pbData or output.cbData > _MAX_FILE:
            raise CredentialsError("protection_failed")
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))
        ctypes.memset(buffer, 0, len(blob))


class CivitaiCredentials:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "civitai.dpapi"
        self._session = ""
        self._lock = threading.RLock()

    def configured(self):
        with self._lock:
            return bool(self._session) or self.path.is_file()

    def status(self):
        """UI-safe configuration state; no decryption or secret readback."""
        with self._lock:
            remembered = self.path.is_file()
            return {"configured": bool(self._session) or remembered, "remembered": remembered}

    def get(self):
        with self._lock:
            if self._session:
                return self._session
            try:
                with self.path.open("rb") as stream:
                    blob = stream.read(_MAX_FILE + 1)
                if len(blob) > _MAX_FILE or not blob.startswith(_MAGIC):
                    raise CredentialsError()
                return validate_token(_crypt(blob[len(_MAGIC):], decrypt=True).decode("ascii"))
            except FileNotFoundError:
                return ""
            except Exception:
                raise CredentialsError() from None

    def set(self, token, remember=True):
        token = validate_token(token)
        with self._lock:
            if not remember:
                try:
                    self.path.unlink(missing_ok=True)
                except OSError:
                    raise CredentialsError() from None
                self._session = token
                return
            temporary = None
            try:
                # Protect before making directories/writing; a failure cannot
                # leave plaintext or silently downgrade to session persistence.
                encrypted = _MAGIC + _crypt(token.encode("ascii"))
                self.directory.mkdir(parents=True, exist_ok=True)
                descriptor, temporary = tempfile.mkstemp(prefix=".civitai-", suffix=".tmp", dir=self.directory)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encrypted)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                temporary = None
                self._session = token
            except Exception:
                raise CredentialsError() from None
            finally:
                if temporary is not None:
                    try:
                        Path(temporary).unlink(missing_ok=True)
                    except OSError:
                        pass

    def clear(self):
        with self._lock:
            self._session = ""
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                raise CredentialsError() from None
