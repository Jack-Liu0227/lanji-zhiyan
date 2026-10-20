"""全应用唯一的路径来源 —— 任何模块都不许再自己 parents[2] 去猜 data/ 在哪。

安装/运行后的布局（默认，Windows）：

    %LOCALAPPDATA%\\览籍智研\\
        layout.json          布局版本（迁移用）
        app\\                 程序：exe 与只读资源
        data\\                运行数据：库、文献、产物、备份
        user\\                用户资料与加密凭据（ACL 收紧到当前用户）
        executors\\           模型与用户都能写的 agents / skills / tools

源码运行时 app 目录 = 仓库根，但 data / user / executors 仍然在 HOME 下 ——
代码和数据分开，换个启动方式也不会再多出一份 data。

环境变量（优先级从高到低）：
    LANJI_ZHIYAN_HOME       换整个数据根（测试/多实例用）
    LANJI_ZHIYAN_DATA       只换 data 目录
    LANJI_ZHIYAN_DB         只换主库文件

兼容旧环境变量：RW_HOME / RESEARCH_WORKBENCH_DATA / RESEARCH_WORKBENCH_DB。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import time
from pathlib import Path

APP_NAME = "览籍智研"
LEGACY_APP_NAMES = ("ResearchWorkbench",)
LAYOUT_VERSION = 2

HOME_ENV_VARS = ("LANJI_ZHIYAN_HOME", "RW_HOME")
DATA_ENV_VARS = ("LANJI_ZHIYAN_DATA", "RESEARCH_WORKBENCH_DATA")
DB_ENV_VARS = ("LANJI_ZHIYAN_DB", "RESEARCH_WORKBENCH_DB")

# 从旧布局（仓库内 data/）搬过来时，只搬这些；webview 缓存和截图不搬
LEGACY_ITEMS = ("research.sqlite3", "papers", "notes", "out", "uploads", "ref",
                "demo-vault", "mail.json", "audit.jsonl", ".rwbak")


def _env(*names: str) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def app_dir() -> Path:
    """程序目录：源码运行 = 仓库根；打包后 = exe 所在目录。"""
    root = Path(__file__).resolve().parents[2]
    if (root / "packages").is_dir():
        return root
    return Path(sys.executable).resolve().parent


def _copy_missing(src: Path, dst: Path) -> int:
    """递归复制缺失项；目标已有文件绝不覆盖。"""
    copied = 0
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            copied += _copy_missing(child, dst / child.name)
    elif src.is_file() and not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied = 1
    return copied


def _migrate_legacy_home(home: Path) -> dict:
    """把旧英文数据根里的数据补到新中文数据根，不覆盖现有文件。"""
    marker = home / ".legacy-home-migrated"
    if marker.exists():
        return {}
    base = Path(_env("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local"))
    candidates = [base / name for name in LEGACY_APP_NAMES]
    candidates += [base / "Programs" / name for name in LEGACY_APP_NAMES]
    copied = {}
    errors = []
    found = False
    for old in candidates:
        if not old.is_dir() or old.resolve() == home.resolve():
            continue
        found = True
        for name in ("data", "user", "executors"):
            src = old / name
            if not src.exists():
                continue
            try:
                copied[name] = copied.get(name, 0) + _copy_missing(src, home / name)
            except OSError as exc:
                errors.append("%s: %s" % (src, exc))
    if found and not errors:
        try:
            marker.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
        except OSError:
            pass
    if errors:
        copied["errors"] = errors
    return copied


def home_dir() -> Path:
    """数据根：LANJI_ZHIYAN_HOME > RW_HOME > %LOCALAPPDATA%\\览籍智研。"""
    override = _env(*HOME_ENV_VARS)
    if override:
        return Path(override).expanduser()
    base = _env("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    home = Path(base) / APP_NAME
    _migrate_legacy_home(home)
    return home


APP = app_dir()
HOME = home_dir()

_user_override = _env(*DATA_ENV_VARS)
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

_db_override = _env(*DB_ENV_VARS)
DB_PATH = Path(_db_override).expanduser() if _db_override else DATA / "research.sqlite3"
MAIL_FILE = USER / "mail.json"
LAYOUT_FILE = HOME / "layout.json"

LOWTMP = CACHE / "lowtmp"          # Low 子进程（xelatex/babeldoc）的 TEMP，必须跟着标 Low
_DIRS = (HOME, DATA, USER, EXECUTORS, BACKUP, CACHE, LOWTMP, PAPERS, NOTES, OUT, UPLOADS, REF,
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
    _harden(USER)          # 凭据：只有当前用户可读写
    try:                   # 工具的临时目录必须也是 Low，否则 xelatex 的 PDF 后端写不进 TEMP
        import lowbox
        lowbox.mark_low(LOWTMP, recursive=True)
    except Exception:
        pass
    _harden(EXECUTORS)     # 模型能往这里写可执行文件：至少别让同机其他账号写
    return meta


# 控制台程序（icacls/netstat/tasklist）不加这个就会在启动时闪黑框
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


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
                       capture_output=True, timeout=10, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        pass


# ---------------------------------------------------------------- 文件白名单（唯一闸门）
def project_roots() -> list[Path]:
    """用户自己指定的目录：项目 base_dir + 对话的产出目录（chats.out_dir）。
    这两类都是用户在原生选择器里亲手选的，所以算白名单里的根。"""
    import sqlite3
    out: list[Path] = []
    try:
        conn = sqlite3.connect(str(DB_PATH))
        try:
            for sql in ("SELECT base_dir FROM research_tasks WHERE base_dir<>''",
                        "SELECT out_dir FROM chats WHERE out_dir<>''"):
                try:
                    out += [Path(r[0]) for r in conn.execute(sql)]
                except sqlite3.Error:
                    pass                       # 老库还没这列 → 跳过，别把白名单搞空
        finally:
            conn.close()
    except Exception:
        return out
    return out


def allowed_roots() -> list[Path]:
    """允许碰的全部根：自己的数据根（data/ 里的 uploads、papers、notes、out）+ 用户建过的项目目录。
    白名单只在这里定义一次 —— 别的地方要判，一律调 resolve_allowed()。"""
    roots = [DATA, UPLOADS, PAPERS, NOTES, OUT, *project_roots()]
    return [r.resolve() for r in roots if r and r.exists()]


GRANT = os.environ.get("RW_GRANT", "")          # 窗口宿主（Medium）起的代建/授权通道
GRANT_KEY = os.environ.get("RW_GRANT_KEY", "")


def _grant_post(path: str, payload: dict) -> bool:
    """走宿主（Medium）那条本机随机端口通道；没通道（同进程模式）返回 False 让调用方自己动手。"""
    if not GRANT:
        return False
    body = json.dumps(payload).encode()
    req = urllib.request.Request("http://" + GRANT + path, data=body,
                                 headers={"Content-Type": "application/json", "X-RW-Key": GRANT_KEY})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return bool(json.loads(r.read() or b"{}").get("ok"))
    except (OSError, ValueError):
        return False


def host_pick_folder(initial: str = "", title: str = "选择文件夹") -> dict:
    """Let the window host choose and remember a directory before granting Low access."""
    if not GRANT:
        import folder_picker
        return folder_picker.pick_folder(initial, title)
    body = json.dumps({"initial": initial, "title": title}).encode()
    req = urllib.request.Request("http://" + GRANT + "/pick", data=body,
                                 headers={"Content-Type": "application/json", "X-RW-Key": GRANT_KEY})
    try:
        with urllib.request.urlopen(req, timeout=650) as response:
            return json.loads(response.read() or b"{}")
    except (OSError, ValueError) as exc:
        return {"error": f"文件夹选择失败：{exc}"}


def assert_not_zotero_write(path: str | os.PathLike[str]) -> None:
    """Protect known Zotero storage and linked-attachment roots at write boundaries."""
    target = Path(path).expanduser().resolve()
    if "zotero" in {part.casefold() for part in target.parts} or target.name.casefold().startswith("zotero.sqlite"):
        raise ValueError("Zotero 管理文件只读，请在独立项目中处理副本")
    if any((folder / "zotero.sqlite").exists() for folder in (target, *target.parents)):
        raise ValueError("Zotero 数据目录只读，请在独立项目中处理副本")
    try:
        import zotero
    except ImportError:
        return
    roots = [zotero.find_zotero_data_dir(), *zotero.linked_attachment_roots()]
    if any(target.is_relative_to(root.resolve()) for root in roots):
        raise ValueError("Zotero 数据或链接附件目录只读，请在独立项目中处理副本")


def validate_project_dir(path: str | os.PathLike[str]) -> Path:
    """Reject broad or managed directories before creating or granting a project."""
    target = Path(path).expanduser()
    if not target.is_absolute():
        raise ValueError("项目目录必须是绝对路径")
    target = target.resolve()
    if target == Path(target.anchor):
        raise ValueError("不能把磁盘根目录设为项目目录")
    app_home = HOME.resolve()
    assert_not_zotero_write(target)
    if target.is_relative_to(app_home):
        return target
    user_home = Path.home().resolve()
    code_root = app_dir().resolve()
    system_roots = [Path(os.environ.get("WINDIR", "C:/Windows")).resolve()]
    system_roots += [Path(value).resolve() for name in ("ProgramFiles", "ProgramFiles(x86)")
                     if (value := os.environ.get(name))]
    protected = [user_home, code_root, Path(tempfile.gettempdir()).resolve(), *system_roots]
    if any(target == item or item.is_relative_to(target) for item in protected):
        raise ValueError("项目目录不能覆盖用户根目录、程序或系统目录")
    if target.is_relative_to(code_root) or any(target.is_relative_to(item) for item in system_roots):
        raise ValueError("项目目录不能位于程序或系统目录")
    return target


def grant_dir(path: str | os.PathLike[str]) -> bool:
    """让窗口宿主代建目录并标成 Low，再返回。

    为什么需要：服务端跑在低完整性（Low IL）里，用户新选的目录（比如桌面）是 Medium ——
    内核不允许 Low 在 Medium 目录下 mkdir，报 `[WinError 5] 拒绝访问`。
    自己标 Low 也不行（改 ACL 要对象的 WRITE_DAC，同样是往上写）。所以只能请 Medium 那边动手。
    通道：宿主在 127.0.0.1 的随机端口上，只认带随机 key 的请求；没通道（同进程模式）就直接建。
    """
    target = Path(str(path)).expanduser()
    if not GRANT:
        target.mkdir(parents=True, exist_ok=True)
        return True
    return _grant_post("/grant", {"path": str(target)})


def host_open(target: str | os.PathLike[str]) -> bool:
    """用系统默认程序打开文档/目录 —— **必须由宿主做**。

    Low 进程里 ShellExecute 对文档和目录直接返回 SE_ERR_ACCESSDENIED(5)（实测），
    只有 http/https 这类协议还能交出去。所以沙箱里请宿主（Medium）代开。
    """
    if not GRANT:
        os.startfile(str(target))            # noqa: S606 —— 同进程模式（服务本身就是宿主）
        return True
    return _grant_post("/open", {"target": str(target)})


def host_app(name: str) -> bool:
    """请宿主起一个外部 GUI 程序（只认白名单名字，宿主自己解路径）。

    为什么不能自己 Popen：Low 起的子进程继承 Low 令牌，Zotero 这类程序写自己的数据目录
    会被内核拒（实测子进程 IL = Low）。
    """
    if not GRANT:
        return False
    return _grant_post("/app", {"name": str(name)})


def resolve_allowed(path: str, extra_roots: list[Path] | None = None, *, must_be_file: bool = False,
                    what: str = "文件") -> Path:
    """文件闸门：必须绝对路径 + 必须落在白名单里。越界 → 记审计 + 抛一句人话。"""
    if not str(path or "").strip():
        raise ValueError("没给路径")
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise ValueError(f"{what}必须用绝对路径：{path}")
    try:
        p = p.resolve()
    except OSError as exc:
        raise ValueError(f"路径不可用：{exc}") from exc
    roots = [*(extra_roots or []), *allowed_roots()]
    if not any(p.is_relative_to(r) for r in roots):     # 前缀比较会让 data-evil/ 混进来，按层级判
        _audit_deny(p, what)
        raise ValueError(f"{what}不在允许范围里 —— 只能是程序自己的数据目录，或你建过的项目文件夹：{p}")
    if must_be_file and not p.is_file():
        raise ValueError(f"这不是一个文件：{p}")
    return p


def mark_write_roots_low(verbose: bool = False) -> dict[str, str]:
    """把「允许 Low 子进程写」的目录标成低完整性：data/ 与每个用户项目目录。
    Low 进程（xelatex / babeldoc）只有在这里才写得进去，其它地方内核直接拒。
    幂等；失败只报不抛（不能让权限搞挂启动）。

    **在沙箱服务里得请宿主标**：Low 进程对 Medium 目录跑 icacls 是 rc=5（实测），
    只有宿主（Medium）改得动。所以有通道就逐目录转过去，没有通道（同进程模式）才自己标。
    """
    import lowbox
    if not lowbox.available():
        return {}
    out: dict[str, str] = {}
    for root in [HOME, DATA, *project_roots()]:      # HOME 一起标：user/ executors/ cache/ 都得让沙箱里的服务端写得进
        try:
            if root and Path(root).is_dir():
                ok = _grant_post("/grant", {"path": str(root)}) if GRANT else lowbox.mark_low(root, recursive=True)
                out[str(root)] = "low" if ok else "failed"
                if verbose:
                    print("mark_low", root, out[str(root)])
        except OSError as exc:
            out[str(root)] = f"error: {exc}"
    return out


def _audit_deny(p: Path, what: str) -> None:
    """越界读写记一行（写 fileguard 那份 audit.jsonl；这里不能 import fileguard，会成环）。"""
    try:
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": "deny", "path": str(p), "what": what}
        with AUDIT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
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
    assert HOME.name == APP_NAME or _env(*HOME_ENV_VARS), HOME
    assert DATA.parent == HOME, DATA
    assert all(d.is_dir() for d in _DIRS), [str(d) for d in _DIRS if not d.is_dir()]
    assert LAYOUT_FILE.is_file(), LAYOUT_FILE
    print("[OK] 布局就绪")
