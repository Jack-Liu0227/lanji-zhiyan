"""本机凭据存储：Windows DPAPI（绑当前用户），非 Windows 退回 base64 并如实标注。

格式与 capabilities.py 里 MCP 配置那套完全一致，方便互相搬：

    dpapi:<base64>   CryptProtectData(当前用户) —— 换 Windows 用户、换机器都解不开
    local:<base64>   非 Windows 的降级（等同不加密，只挡一眼）；security() 会如实说
    裸文本          旧库/旧 mail.json 遗留的明文 —— 照常读，下次写自动升格成 dpapi:

用法：
    from securestore import protect, unprotect, is_protected
    db 里存 protect(key)，读出来 unprotect(key)，空串保持空串。

为什么要这一层：SQLite / mail.json 都是「整份拷走就等于拿走 key」的明文文件，
DPAPI 把它们绑到当前 Windows 用户，拷到别的机器/别的账号上解不开。
"""
from __future__ import annotations

import base64
import ctypes
import os

PREFIX_DPAPI = "dpapi:"
PREFIX_LOCAL = "local:"


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, decrypt: bool = False) -> bytes:
    """CryptProtectData / CryptUnprotectData（当前用户作用域，不落任何密钥文件）。"""
    source_buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = _Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError(ctypes.get_last_error(), "Windows credential protection failed")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def security() -> str:
    """当前平台的保护等级（如实回给界面，别让用户以为非 Windows 也加密了）。"""
    return "windows-dpapi-current-user" if os.name == "nt" else "local-database-file-permissions-only"


def is_protected(value: object) -> bool:
    return isinstance(value, str) and value.startswith((PREFIX_DPAPI, PREFIX_LOCAL))


def protect(text: object) -> str:
    """明文 → 密文。空值、已加密的值原样返回（幂等，方便到处调用）。"""
    if text is None:
        return ""
    text = str(text)
    if not text or is_protected(text):
        return text
    raw = text.encode("utf-8")
    if os.name == "nt":
        return PREFIX_DPAPI + base64.b64encode(_dpapi(raw)).decode("ascii")
    # 非 Windows 明确降级：前缀写 local，读的时候能看出来「这其实没加密」
    return PREFIX_LOCAL + base64.b64encode(raw).decode("ascii")


def unprotect(value: object) -> str:
    """密文 → 明文。裸文本（历史遗留明文）原样返回 —— 兼容老库，不需要一次性迁移。"""
    if value is None:
        return ""
    value = str(value)
    if not value:
        return ""
    if value.startswith(PREFIX_DPAPI):
        if os.name != "nt":
            raise ValueError("这份凭据属于保存它的 Windows 用户，换系统解不开")
        return _dpapi(base64.b64decode(value[len(PREFIX_DPAPI):]), decrypt=True).decode("utf-8")
    if value.startswith(PREFIX_LOCAL):
        return base64.b64decode(value[len(PREFIX_LOCAL):]).decode("utf-8")
    return value
