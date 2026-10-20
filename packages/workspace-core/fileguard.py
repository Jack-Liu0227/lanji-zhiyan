"""文件安全三件套：环境变量洗白 · 写前快照+审计 · 打开文档的扩展名闸门。

照 Hermes 的 SECURITY.md §2.2 口径写的：**这一层是启发式，不是沙箱**。
真正的边界只有操作系统 —— 同一个 Windows 用户下的进程之间没有隔离，这里做的是
「别让手滑和顺手就把东西送出去/盖掉」，不是「挡住有心的本地程序」。

    clean_env()        给 xelatex 这类外部子进程用的干净环境（剥掉 key/token/secret）
    before_write()     覆盖文件前先在 data/.rwbak/<日期>/ 留一份，可按路径回滚
    rollback()         把某文件恢复到最近一次快照
    open_document()    只放行文档类扩展名，其余一律拒（不拿 os.startfile 当万能打开器）
    audit()            所有写/删/打开/回滚各记一行 data/audit.jsonl
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

import paths            # 唯一路径来源

HERE = Path(__file__).resolve().parent
ROOT = paths.APP          # 程序根（只读资源）
DATA = paths.DATA         # 运行数据（HOME/data）
SNAP = paths.SNAP         # data/_backup/.rwbak —— 写前快照
AUDIT = paths.AUDIT       # data/_backup/audit.jsonl
KEEP_PER_FILE = 10                       # 每个文件留最近几份快照（够回滚，不至于把盘塞满）

# 只有这些扩展名能交给系统默认程序打开。代码/脚本/快捷方式一律不碰：
# 白名单目录里放个 .exe 或 .lnk，os.startfile 就等于「点一下就执行」。
DOC_SUFFIXES = frozenset((
    ".pdf", ".md", ".markdown", ".txt", ".tex", ".bib", ".csv", ".json",
    ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".html", ".htm",
))

# 子进程不该看见的环境变量（key / token / 凭据 / 本应用的私有变量）
_SECRET_ENV = re.compile(
    r"(?i)(api[_-]?key|apikey|token|secret|passw|credential|auth|cookie"
    r"|openai|anthropic|deepseek|dashscope|zhipu|moonshot|siliconflow|hf_|huggingface|github|gitlab"
    r"|research_workbench|rw_trans|rw_debug)")


# ---------------------------------------------------------------- 子进程环境
_READ_ONLY_SOURCES: set[Path] = set()


def protect_source(path: str | Path) -> None:
    _READ_ONLY_SOURCES.add(Path(path).resolve())


def assert_writable(path: str | Path) -> None:
    target = Path(path).resolve()
    paths.assert_not_zotero_write(target)
    if target in _READ_ONLY_SOURCES:
        raise ValueError("Zotero 原附件只读，请修改项目副本")
    if target.is_file() and target.stat().st_nlink > 1:
        for source in tuple(_READ_ONLY_SOURCES):
            if source.is_file() and target.samefile(source):
                raise ValueError("不能通过硬链接修改 Zotero 原附件，请使用普通文件副本")


def clean_env(extra_path: str = "") -> dict[str, str]:
    """给外部工具（xelatex 等）用的环境：保留 PATH/HOME/TMP/代理，剥掉一切凭据类变量。"""
    env = {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}
    if extra_path:                                    # 让 xelatex 能被找到
        env["PATH"] = extra_path + os.pathsep + env.get("PATH", "")
    return env


# ---------------------------------------------------------------- 审计
def audit(action: str, path: object = "", **meta: object) -> None:
    """记一行审计。**永不抛**：审计失败不能把用户的写操作带崩。"""
    try:
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        if AUDIT.is_file() and AUDIT.stat().st_size > 4 * 1024 * 1024:      # 4MB 转一份
            AUDIT.replace(AUDIT.with_suffix(".1.jsonl"))
        line = {"ts": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "action": action, "path": str(path or ""), **meta}
        with AUDIT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- 快照 / 回滚
def _slot(path: Path) -> Path:
    """某个文件的快照目录：SNAP/<日期>/<路径hash>_<文件名>。"""
    h = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]
    return SNAP / _dt.date.today().isoformat() / f"{h}_{path.name}"


def before_write(path: object, note: str = "") -> Path | None:
    """覆盖写之前留一份旧内容。文件不存在/空 → 不存（没什么可丢的）。返回快照路径。"""
    p = Path(path)
    assert_writable(p)
    try:
        if not p.is_file() or p.stat().st_size == 0:
            return None
        slot = _slot(p)
        slot.mkdir(parents=True, exist_ok=True)
        dest = slot / _dt.datetime.now().strftime("%H%M%S-%f")
        shutil.copy2(p, dest)
        for old in sorted(slot.iterdir())[:-KEEP_PER_FILE]:      # 只留最近 N 份
            old.unlink(missing_ok=True)
        audit("snapshot", p, backup=str(dest), note=note)
        return dest
    except OSError:
        return None                     # 快照失败不该挡住正常写入；审计里没有记录也算线索


def snapshots(path: object) -> list[Path]:
    """某个文件的历史快照（新 → 旧）。"""
    p = Path(path)
    slot = _slot(p)
    return sorted(slot.iterdir(), reverse=True) if slot.is_dir() else []


def rollback(path: object) -> dict:
    """把文件恢复到最近一次快照。没有快照就拒绝（快照本身就是「曾经允许写」的凭证）。"""
    p = Path(path)
    assert_writable(p)
    snaps = snapshots(p)
    if not snaps:
        raise ValueError(f"这个文件没有历史版本可回滚：{p}")
    latest = snaps[0]
    p.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(latest, p)
    audit("rollback", p, backup=str(latest))
    return {"ok": True, "path": str(p), "restored_from": str(latest),
            "bytes": p.stat().st_size, "others": len(snaps) - 1}


# ---------------------------------------------------------------- 打开文件
def open_document(path: object) -> dict:
    """只把文档类文件交给系统默认程序；可执行/脚本/快捷方式一律拒。"""
    p = Path(path)
    if not p.is_file():
        raise ValueError("文件不存在")
    suffix = p.suffix.lower()
    if suffix not in DOC_SUFFIXES:
        raise ValueError(f"出于安全只打开文档类文件（{suffix or '无扩展名'} 不在白名单里）；"
                         f"代码/脚本类请在编辑器里查看：{p.name}")
    if not hasattr(os, "startfile") and not paths.GRANT:
        raise RuntimeError("这个系统没有 startfile")
    audit("open", p)
    if not paths.host_open(p):                # Low 里 ShellExecute 给文档返回 5 → 请宿主代开
        raise ValueError(f"系统拒绝把这个文件交给默认程序（沙箱里只能由窗口宿主打开）：{p.name}")
    return {"opened": str(p)}
