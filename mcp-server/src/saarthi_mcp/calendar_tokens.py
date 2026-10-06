"""Local OAuth tokens encrypted for the current Windows login with DPAPI.

This store is for the local Windows connector, not a portable cloud secret store.
No plaintext tokens are written, including during atomic replacement. Deployments
on other operating systems must supply a different encrypted token store.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import tempfile


_HEADER = b"SAARTHI-DPAPI-1\n"
_MAX_JSON_BYTES = 64 * 1024
_MAX_FILE_BYTES = 128 * 1024


class TokenStoreError(RuntimeError):
    """Safe diagnostic that never includes token material or OS exception text."""


def _require_windows() -> None:
    if sys.platform != "win32":
        raise TokenStoreError("Local calendar token storage requires Windows DPAPI.")


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data: bytes, *, decrypt: bool) -> bytes:
    _require_windows()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    transform = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    transform.argtypes = [
        ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    transform.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = _Blob()
    try:
        # UI_FORBIDDEN only: deliberately omit LOCAL_MACHINE so other Windows
        # logins cannot decrypt the stored token using their own credentials.
        if not transform(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(target)):
            raise TokenStoreError("Windows could not protect or unlock calendar credentials.")
        if target.cbData > _MAX_FILE_BYTES:
            raise TokenStoreError("Calendar credentials exceed the local storage limit.")
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.memset(buffer, 0, ctypes.sizeof(buffer))
        if target.pbData:
            ctypes.memset(target.pbData, 0, target.cbData)
            kernel32.LocalFree(target.pbData)


class LocalTokenStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def save(self, payload: dict) -> None:
        _require_windows()
        temporary: Path | None = None
        try:
            if not isinstance(payload, dict):
                raise ValueError("Expected an object")
            encoded = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > _MAX_JSON_BYTES:
                raise ValueError("Payload too large")
            ciphertext = _HEADER + _crypt(encoded, decrypt=False)
            if len(ciphertext) > _MAX_FILE_BYTES:
                raise ValueError("Ciphertext too large")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # mkstemp creates a random exclusive sibling; it contains ciphertext
            # only, encrypted before any file is opened, even if replacement fails.
            descriptor, name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(ciphertext)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except (OSError, ValueError, TypeError, RecursionError):
            raise TokenStoreError("Calendar credentials could not be saved securely.") from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    # An orphan here is encrypted; do not obscure the save error.
                    pass

    def load(self) -> dict | None:
        try:
            with self.path.open("rb") as stream:
                _require_windows()
                ciphertext = stream.read(_MAX_FILE_BYTES + 1)
            if len(ciphertext) > _MAX_FILE_BYTES or not ciphertext.startswith(_HEADER):
                raise ValueError("Invalid token file")
            plaintext = _crypt(ciphertext[len(_HEADER):], decrypt=True)
            if len(plaintext) > _MAX_JSON_BYTES:
                raise ValueError("Payload too large")
            payload = json.loads(plaintext)
            if not isinstance(payload, dict):
                raise ValueError("Expected an object")
            return payload
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, RecursionError):
            raise TokenStoreError("Saved calendar credentials could not be read securely. Reconnect Calendar.") from None
