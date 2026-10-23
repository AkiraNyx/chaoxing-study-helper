"""Windows DPAPI 加密的登录会话存储。

只保存学习通 Cookie，不保存账号密码；文件按当前 Windows 用户加密，
复制到其他用户/机器上无法解密。
"""

from __future__ import annotations

import ctypes
import json
import os
import time
from ctypes import wintypes


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_crypt32.CryptProtectData.argtypes = [
    ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
]
_crypt32.CryptProtectData.restype = wintypes.BOOL
_crypt32.CryptUnprotectData.argtypes = [
    ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
]
_crypt32.CryptUnprotectData.restype = wintypes.BOOL
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_kernel32.LocalFree.restype = ctypes.c_void_p


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data, len(data))
    value = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    return value, buffer


def _protect(data: bytes) -> bytes:
    source, _buffer = _blob(data)
    target = _DataBlob()
    if not _crypt32.CryptProtectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        _kernel32.LocalFree(target.pbData)


def _unprotect(data: bytes) -> bytes:
    source, _buffer = _blob(data)
    target = _DataBlob()
    if not _crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        _kernel32.LocalFree(target.pbData)


def _path() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    folder = os.path.join(base, "学习通刷课")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "session.bin")


def save(cookie: str) -> bool:
    if not cookie.strip():
        return False
    payload = json.dumps(
        {"cookie": cookie.strip(), "saved_at": int(time.time())},
        ensure_ascii=False,
    ).encode("utf-8")
    encrypted = _protect(payload)
    path = _path()
    temp = path + ".tmp"
    with open(temp, "wb") as handle:
        handle.write(encrypted)
    os.replace(temp, path)
    return True


def load() -> str | None:
    path = _path()
    try:
        with open(path, "rb") as handle:
            encrypted = handle.read()
        payload = json.loads(_unprotect(encrypted).decode("utf-8"))
        cookie = str(payload.get("cookie") or "").strip()
        return cookie or None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def clear() -> None:
    path = _path()
    try:
        os.remove(path)
    except OSError:
        pass


def _selftest() -> None:
    sample = "_uid=1; fid=2; vc3=test"
    encrypted = _protect(sample.encode())
    assert encrypted != sample.encode()
    assert _unprotect(encrypted).decode() == sample
    print("session_store DPAPI OK")


if __name__ == "__main__":
    _selftest()
