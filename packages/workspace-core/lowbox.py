"""低完整性（Low IL）子进程：Windows 内核强制的「只能写授权目录」边界。

为什么不是 AppContainer / 受限令牌（见 doc/plan/2026-10-20_真边界spike.md）：
  · AppContainer 从 Python 起子进程，SECURITY_CAPABILITIES 属性会被静默忽略 —— 出来的是**假沙箱**；
  · 受限令牌（S-1-5-12）连 python 都起不来（0xC0000142），System32 不认这个 SID 且改它要管理员。
Low IL 不用管理员就能拿到真边界：Low 进程**写不了** Medium/High 的对象（内核拒绝），读不受限。

用法（接口照着 subprocess 写，替换时只改一行）：
    lowbox.run([exe, ...], cwd=..., env=..., timeout=180)   # ≈ subprocess.run(capture_output=True, text=True)
    with lowbox.popen([exe, ...], env=...) as p:            # ≈ Popen(stdout=PIPE, stderr=STDOUT, text=True)
        for line in p.stdout: ...
    lowbox.mark_low(path, recursive=True)                   # 目录标 Low，Low 进程才写得进去

**边界口径**：这一层只管「写哪儿」。读仍然靠 paths.resolve_allowed() 的应用层白名单。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from typing import Any

LOW_SID = "S-1-16-4096"          # 低完整性级别的 SID
_TOKEN_ALL = 0xF01FF
_TOKEN_INTEGRITY_LEVEL = 25
_SECURITY_IMPERSONATION, _TOKEN_PRIMARY = 2, 1
_STARTF_USESTDHANDLES, _STARTF_USESHOWWINDOW = 0x100, 0x1
_CREATE_NO_WINDOW, _CREATE_UNICODE_ENV = 0x08000000, 0x400

if os.name == "nt":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _adv = ctypes.WinDLL("advapi32", use_last_error=True)
else:                                            # 非 Windows：全部退回普通 spawn
    _k32 = _adv = None


def available() -> bool:
    return os.name == "nt" and _k32 is not None


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [("Label", SID_AND_ATTRIBUTES)]


_HANDLE, _DWORD = wintypes.HANDLE, wintypes.DWORD
if available():
    _k32.GetCurrentProcess.restype = _HANDLE
    _k32.CloseHandle.argtypes = [_HANDLE]
    _k32.TerminateProcess.argtypes = [_HANDLE, ctypes.c_uint]
    _k32.WaitForSingleObject.argtypes = [_HANDLE, _DWORD]
    _k32.GetExitCodeProcess.argtypes = [_HANDLE, ctypes.POINTER(_DWORD)]
    _adv.OpenProcessToken.argtypes = [_HANDLE, _DWORD, ctypes.POINTER(_HANDLE)]
    _adv.OpenProcessToken.restype = wintypes.BOOL
    _adv.DuplicateTokenEx.argtypes = [_HANDLE, _DWORD, ctypes.c_void_p, _DWORD, _DWORD, ctypes.POINTER(_HANDLE)]
    _adv.DuplicateTokenEx.restype = wintypes.BOOL
    _adv.SetTokenInformation.argtypes = [_HANDLE, _DWORD, ctypes.c_void_p, _DWORD]
    _adv.SetTokenInformation.restype = wintypes.BOOL
    _adv.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    _adv.ConvertStringSidToSidW.restype = wintypes.BOOL
    _adv.CreateProcessAsUserW.restype = wintypes.BOOL


def low_token():
    """当前进程令牌的副本，完整性级别降到 Low。"""
    tok = _HANDLE()
    if not _adv.OpenProcessToken(_k32.GetCurrentProcess(), _TOKEN_ALL, ctypes.byref(tok)):
        raise OSError(ctypes.get_last_error(), "OpenProcessToken")
    dup = _HANDLE()
    if not _adv.DuplicateTokenEx(tok, _TOKEN_ALL, None, _SECURITY_IMPERSONATION, _TOKEN_PRIMARY, ctypes.byref(dup)):
        raise OSError(ctypes.get_last_error(), "DuplicateTokenEx")
    sid = ctypes.c_void_p()
    if not _adv.ConvertStringSidToSidW(LOW_SID, ctypes.byref(sid)):
        raise OSError(ctypes.get_last_error(), "ConvertStringSidToSid")
    label = TOKEN_MANDATORY_LABEL(SID_AND_ATTRIBUTES(sid, 0x20))       # SE_GROUP_INTEGRITY
    if not _adv.SetTokenInformation(dup, _TOKEN_INTEGRITY_LEVEL, ctypes.byref(label),
                                    ctypes.sizeof(TOKEN_MANDATORY_LABEL) + 8):
        raise OSError(ctypes.get_last_error(), "SetTokenInformation")
    _k32.CloseHandle(tok)
    return dup


def mark_low(path: str | os.PathLike[str], recursive: bool = True) -> bool:
    """把目录标成 Low（含子项，用 /T）。Low 进程只有在这里才写得进去；Medium 进程照样能写。"""
    try:
        cmd = ["icacls", str(path), "/setintegritylevel", "(OI)(CI)L"]
        if recursive:
            cmd.append("/T")
        r = subprocess.run(cmd, capture_output=True, timeout=120,
                           creationflags=0x08000000 if os.name == "nt" else 0)   # 别闪黑框
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class LowProc:
    """≈ subprocess.Popen 的最小面：stdout（文本、已合并 stderr）/ poll / wait / kill / returncode。"""

    def __init__(self, cmd: list[str], cwd: str | None, env: dict[str, str] | None, merge_stderr: bool = True):
        self._h = None
        self._closed = False
        if not available():
            self._fallback = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                              stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
                                              stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
            self.stdout = self._fallback.stdout
            self.pid = self._fallback.pid
            self.returncode = None
            return
        self._fallback = None
        r, w = os.pipe()
        os.set_inheritable(w, True)
        import msvcrt
        wh = msvcrt.get_osfhandle(w)
        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(STARTUPINFOW)
        si.dwFlags = _STARTF_USESHOWWINDOW | _STARTF_USESTDHANDLES
        si.wShowWindow = 0
        si.hStdInput = None
        si.hStdOutput = wh
        si.hStdError = wh if merge_stderr else None
        pi = PROCESS_INFORMATION()
        block = "\0".join(f"{k}={v}" for k, v in (env if env is not None else os.environ).items()) + "\0\0"
        cmdline = subprocess.list2cmdline(cmd)
        tok = low_token()
        try:
            ok = _adv.CreateProcessAsUserW(tok, None, ctypes.create_unicode_buffer(cmdline), None, None, True,
                                           _CREATE_NO_WINDOW | _CREATE_UNICODE_ENV,
                                           ctypes.create_unicode_buffer(block),
                                           str(cwd or os.getcwd()), ctypes.byref(si), ctypes.byref(pi))
        finally:
            _k32.CloseHandle(tok)
        os.close(w)
        if not ok:
            os.close(r)
            raise OSError(ctypes.get_last_error(), "CreateProcessAsUser(lowbox)")
        _k32.CloseHandle(pi.hThread)
        self._h = pi.hProcess
        self.pid = pi.dwProcessId
        self.stdout = os.fdopen(r, "r", encoding="utf-8", errors="replace")
        self.returncode = None

    # ---- subprocess 兼容面 ----
    def poll(self) -> int | None:
        if self._fallback is not None:
            self.returncode = self._fallback.poll()
            return self.returncode
        if self.returncode is not None:
            return self.returncode
        rc = _k32.WaitForSingleObject(self._h, 0)
        if rc == 0:                                  # WAIT_OBJECT_0 → 已退出
            code = _DWORD()
            _k32.GetExitCodeProcess(self._h, ctypes.byref(code))
            self.returncode = int(code.value)
            return self.returncode
        return None

    def wait(self, timeout: float | None = None) -> int:
        if self._fallback is not None:
            self.returncode = self._fallback.wait(timeout)
            return self.returncode
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            rc = self.poll()
            if rc is not None:
                return rc
            if end and time.monotonic() > end:
                raise subprocess.TimeoutExpired("lowbox", timeout)
            time.sleep(0.03)

    def kill(self) -> None:
        if self._fallback is not None:
            self._fallback.kill()
            return
        if self._h and self.poll() is None:
            _k32.TerminateProcess(self._h, 1)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.stdout:
                self.stdout.close()
        except OSError:
            pass
        if self._h:
            _k32.CloseHandle(self._h)

    def __enter__(self) -> "LowProc":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def sandbox_env(env: dict[str, str], tmpdir: str | os.PathLike[str]) -> dict[str, str]:
    """给 Low 子进程的环境：把 TEMP/TMP/TMPDIR 指到「已标 Low 的目录」。
    实测（doc/plan/2026-10-20_真边界spike.md）：xelatex 的 PDF 后端要往 TEMP 写，
    TEMP 还在 Medium 时整份编译会失败；指进来就正常。顺带别让它写 __pycache__。"""
    out = dict(env)
    tmp = str(tmpdir)
    os.makedirs(tmp, exist_ok=True)
    for k in ("TEMP", "TMP", "TMPDIR"):
        out[k] = tmp
    out.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return out


def popen(cmd: list[str], cwd: str | None = None, env: dict[str, str] | None = None,
          merge_stderr: bool = True) -> LowProc:
    return LowProc(cmd, cwd, env, merge_stderr)


def run(cmd: list[str], cwd: str | None = None, env: dict[str, str] | None = None,
        timeout: float | None = None) -> "subprocess.CompletedProcess[str]":
    """≈ subprocess.run(capture_output=True, text=True)：stdout/stderr 合并进 stdout。"""
    p = popen(cmd, cwd, env)
    try:
        try:
            out = p.stdout.read()
        finally:
            p.stdout.close()
        code = p.wait(timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        raise
    finally:
        p.close()
    return subprocess.CompletedProcess(cmd, code, out, "")


def demo() -> None:
    """自检：本进程是 Medium；lowbox 子进程写授权目录 OK、写桌面被拒。"""
    if not available():
        print("非 Windows，跳过"); return
    base = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Temp", "lowbox_demo")
    os.makedirs(base, exist_ok=True)
    assert mark_low(base), "mark_low 失败"
    probe = ("import os,sys\n"
             "a=os.path.join(sys.argv[1],'ok.txt')\n"
             "open(a,'w').write('x')\n"
             "print('write_granted=ok')\n"
             "try:\n"
             "    open(os.path.join(os.path.expanduser('~'),'Desktop','lowbox_demo.txt'),'w').write('x')\n"
             "    print('write_desktop=WROTE')\n"
             "except PermissionError:\n"
             "    print('write_desktop=denied')\n")
    res = run([sys.executable, "-c", probe, base], timeout=60)
    print(res.stdout.strip())
    assert "write_granted=ok" in res.stdout
    assert "write_desktop=denied" in res.stdout, "桌面居然写得进去 → 边界没生效"


if __name__ == "__main__":
    demo()
