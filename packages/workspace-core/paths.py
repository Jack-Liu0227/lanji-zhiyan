"""全应用唯一的路径来源 —— 任何模块都不许再自己 parents[2] 去猜 data/ 在哪。

安装/运行后的布局（默认，Windows）：

    %LOCALAPPDATA%\\ResearchWorkbench\\
        layout.json          布局版本（迁移用）
        app\\                 程序：exe 与只读资源
        data\\                运行数据：库、文献、产物、备份
        user\\                用户资料与加密凭据（ACL 收紧到当前用户）
        executors\\           模型与用户都能写的 agents / skills / tools

源码运行时 app 目录 = 仓库根，但 data / user / executors 仍然在 HOME 下 ——
代码和数据分开，换个启动方式也不会再多出一份 data。

环境变量（优先级从高到低）：
    RW_HOME                 换整个数据根（测试/多实例用）
    RESEARCH_WORKBENCH_DATA 只换 data 目录
    RESEARCH_WORKBENCH_DB   只换主库文件
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

APP_NAME = "ResearchWorkbench"
LAYOUT_VERSION = 2

# 从旧布局（仓库内 data/）搬过来时，只搬这些；webview 缓存和截图不搬
LEGACY_ITEMS = ("research.sqlite3", "papers", "notes", "out", "uploads", "ref",
                "demo-vault", "mail.json", "audit.jsonl", ".rwbak")


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def app_dir() -> Path:
    """程序目录：源码运行 = 仓库根；打包后 = exe 所在目录。"""
    root = Path(__file__).resolve().parents[2]
    if (root / "packages").is_dir():
        return root
    return Path(sys.executable).resolve().parent


def home_dir() -> Path:
    """数据根：RW_HOME > %LOCALAPPDATA%\\ResearchWorkbench。"""
    override = _env("RW_HOME")
    if override:
        return Path(override).expanduser()
    base = _env("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME


APP = app_dir()
HOME = home_dir()

_user_override = _env("RESEARCH_WORKBENCH_DATA")
DATA = Path(_user_override).expanduser() if _user_override else HOME / "data"
USER = HOME / "user"
EXECUTORS = HOME / "executors"

BACKUP = DATA / "_backup"
CACHE = DATA / "cache"
PAPERS = DATA / "papers"
NOTES = DATA / "notes"
OUT = DATA / "out"
UPLOADS = DATA / "uploads"
REF = DATA / "ref"
SNAP = BACKUP / ".rwbak"
AUDIT = BACKUP / "audit.jsonl"

_db_override = _env("RESEARCH_WORKBENCH_DB")
DB_PATH = Path(_db_override).expanduser() if _db_override else DATA / "research.sqlite3"
MAIL_FILE = USER / "mail.json"
LAYOUT_FILE = HOME / "layout.json"

_DIRS = (HOME, DATA, USER, EXECUTORS, BACKUP, CACHE, PAPERS, NOTES, OUT, UPLOADS, REF,
         EXECUTORS / "agents", EXECUTORS / "skills", EXECUTORS / "tools")

_EXEC_README = {
    "agents": "# agents\n\n一个 agent 一个目录（或一份 .md）：名字 + 提示词 + 它该用的工具。\n"
              "模型和用户都可以往这里写；应用每次启动重新扫。\n",
    "skills": "# skills\n\n可复用的做法（SKILL.md + 附带脚本）。模型可以直接装进来。\n",
    "tools": "# tools\n\n可执行的小工具（.py / .ps1 / .exe）。\n"
             "**已知边界**：这里不做签名校验，能被写就能被执行 —— 只在这台机器上可信。\n",
}


def ensure_layout() -> dict:
    """建树 + 写 layout.json + 收紧 user 目录权限。幂等；失败只报不抛（不能因为建目录挡住启动）。"""
    for d in _DIRS:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    for name, text in _EXEC_README.items():
        f = EXECUTORS / name / "README.md"
        if not f.exists():
            try:
                f.write_text(text, encoding="utf-8")
            except OSError:
                pass
    meta = {"layout": LAYOUT_VERSION, "app": str(APP), "updated": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        if not LAYOUT_FILE.exists() or json.loads(LAYOUT_FILE.read_text(encoding="utf-8")).get("layout") != LAYOUT_VERSION:
            LAYOUT_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, ValueError):
        pass
    _harden(USER)
    return meta


def _harden(path: Path) -> None:
    """只给当前用户读写（Windows）。失败就算了：不因为权限收紧把应用弄挂。"""
    if os.name != "nt":
        return
    user = os.environ.get("USERNAME")
    if not user:
        return
    try:
        subprocess.run(["icacls", str(path), "/inheritance:r",
                        "/grant:r", f"{user}:(OI)(CI)F"],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def _count_files(p: Path) -> int:
    if p.is_file():
        return 1
    return sum(1 for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def _db_rows(p: Path) -> dict:
    """迁移校验：库里的关键行数（读不出就当 0）。"""
    import sqlite3
    out = {}
    if not p.is_file():
        return out
    c = None
    try:
        c = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
        for t in ("research_tasks", "papers", "md_notes", "chats"):
            try:
                out[t] = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                pass
    except sqlite3.Error:
        pass
    finally:
        if c:
            try:
                c.close()
            except Exception:
                pass
    return out


def migrate_legacy(legacy_data: Path | None = None) -> dict:
    """把旧布局（仓库内 data/）复制到新 HOME 并校验，成功后旧目录改名保留。

    复制而不是移动：半路失败也不会丢库。可以从中断处接着跑（幂等）：
    已经复制过的库/目录不会重复覆盖，只在需要时补齐。
    """
    legacy_data = Path(legacy_data) if legacy_data else (APP / "data")
    if not legacy_data.is_dir() or legacy_data.resolve() == DATA.resolve():
        return {"done": False, "reason": "没有旧布局"}

    copied, skipped, before, after = [], [], {}, {}
    legacy_db = legacy_data / "research.sqlite3"
    if legacy_db.is_file():
        before = _db_rows(legacy_db)

    try:
        for name in LEGACY_ITEMS:
            src = legacy_data / name
            if not src.exists():
                continue
            dst = (USER / "mail.json") if name == "mail.json" else (
                BACKUP / name if name in ("audit.jsonl", ".rwbak") else DATA / name)
            if name == "research.sqlite3" and dst.is_file() and dst.stat().st_size > 0:
                skipped.append(name)          # 上次已拷过，别覆盖正在用的库
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=False)
                elif not dst.exists():
                    shutil.copy2(src, dst)
                copied.append(name)
            except OSError as exc:            # junction/被占用/权限：记下来继续，不让一个条目挡住迁移
                skipped.append("%s(%s)" % (name, exc.__class__.__name__))
    except OSError as exc:
        return {"done": False, "reason": "%s: %s" % (exc.__class__.__name__, exc), "copied": copied}

    # 校验：只校验这一轮真的拷过的。目录只要求「新 >= 旧」——
    # 目标目录里可能已经有应用新写的东西，严格相等会把正常情况判成失败。
    if "research.sqlite3" in copied:
        after = _db_rows(DB_PATH)
        if before and after != before:
            return {"done": False, "reason": "库行数对不上：%s → %s" % (before, after), "copied": copied}
    for name in copied:
        if name == "research.sqlite3" or name.endswith(")") or name == "mail.json":
            continue
        src = legacy_data / name
        dst = BACKUP / name if name in ("audit.jsonl", ".rwbak") else DATA / name
        if src.is_dir():
            a, b = _count_files(src), _count_files(dst)
            if b < a:
                return {"done": False, "reason": "%s 文件数对不上：%s → %s" % (name, a, b), "copied": copied}

    stamp = time.strftime("%Y%m%d-%H%M%S")
    try:
        legacy_data.rename(legacy_data.with_name("data.old-" + stamp))
    except OSError:
        pass
    return {"done": True, "copied": copied, "skipped": skipped, "before": before, "after": after}


def info() -> dict:
    return {"home": str(HOME), "app": str(APP), "data": str(DATA), "user": str(USER),
            "executors": str(EXECUTORS), "db": str(DB_PATH), "layout": LAYOUT_VERSION}


if __name__ == "__main__":                     # 自检：python paths.py
    meta = ensure_layout()
    print(json.dumps({"meta": meta, "paths": info()}, ensure_ascii=False, indent=2))
    assert HOME.name == APP_NAME or _env("RW_HOME"), HOME
    assert DATA.parent == HOME, DATA
    assert all(d.is_dir() for d in _DIRS), [str(d) for d in _DIRS if not d.is_dir()]
    assert LAYOUT_FILE.is_file(), LAYOUT_FILE
    print("[OK] 布局就绪")
