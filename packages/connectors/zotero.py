# -*- coding: utf-8 -*-
"""Zotero 接入（只读）—— 笔记页用。

Zotero 接入约定（
.agents/roles/zotero-local.md）：

  · 只走 Zotero **本机 Local API**（默认 http://127.0.0.1:23119/api/）；
  · **绝不打开/读取/写入 zotero.sqlite，不复制附件，不在后台同步**；
  · 请求头 Zotero-API-Version: 3；分页 limit/start；总数看 `Total-Results` 响应头；
  · 只允许 loopback（非 127.0.0.1/localhost 直接报错）；
  · 能力探测：/connector/ping 看「起没起」，/api/users/0/items 看「读不读得了」，
    没探测成功就不显示「已连接」，中文说清原因（未启动 / 版本太老 / 超时）；
  · 本模块以**只读**为主。Zotero 10+ 另有一条本机写入通道（建分类 / 写条目 / 挂链接文件），
    必须先用 zotero_local_authorize 拿到用户在弹窗里授予的 key（「始终允许」才可复用），
    文件始终留在项目文件夹（linked_file），不上传、不进 Zotero storage。

Zotero 7+ 的接口：
  GET /api/users/0/items?format=json&limit=&start=&q=
  GET /api/users/0/collections?format=json&limit=
  GET /api/users/0/collections/<key>/items?format=json&limit=&start=
"""
from __future__ import annotations

import glob
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import securestore                # 本机写权限 key 加密（DPAPI，见 workspace-core/securestore.py）
import service_settings as store
from pathlib import Path
from typing import Any

DEFAULT_BASE = os.environ.get("ZOTERO_LOCAL_API", "http://127.0.0.1:23119")
API_VERSION = "3"
PING_TIMEOUT = 2
READ_TIMEOUT = 8
MAX_LIMIT = 100
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


class ZoteroError(RuntimeError):
    """带中文人话的失败原因（前端直接显示）。"""


# --------------------------------------------------------------------------- #
# 传输层
# --------------------------------------------------------------------------- #
def _base(value: str | None = None) -> str:
    base = (value or DEFAULT_BASE).rstrip("/")
    parts = urllib.parse.urlparse(base)
    if parts.hostname not in LOOPBACK:
        raise ZoteroError("只允许本机 Zotero（地址必须是 127.0.0.1 或 localhost）。")
    return base


def _api_url(base: str, path: str, params: dict[str, Any] | None = None) -> str:
    url = base + "/api" + path
    query = {"format": "json"}
    for key, value in (params or {}).items():
        if value is None or value == "":
            continue
        query[key] = value
    return url + "?" + urllib.parse.urlencode(query)


def _get(url: str, timeout: int = READ_TIMEOUT) -> tuple[int, dict[str, str], Any]:
    request = urllib.request.Request(url, headers={
        "Zotero-API-Version": API_VERSION,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            headers = {k.lower(): v for k, v in response.headers.items()}
            body: Any = json.loads(raw.decode("utf-8", "replace")) if raw else None
            return response.status, headers, body
    except urllib.error.HTTPError as exc:                       # 404 = 版本太老（没有本机 API）
        return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise ZoteroError("连不上 Zotero 本机接口（%s）：%s" % (url.split("?")[0], reason)) from exc
    except TimeoutError as exc:
        raise ZoteroError("Zotero 响应超时（%s 秒）。" % timeout) from exc


def _ping(base: str) -> tuple[bool, str]:
    """Zotero 的 connector 服务器：5/6/7 都有，用来区分「没启动」和「版本太老」。"""
    try:
        with urllib.request.urlopen(base + "/connector/ping", timeout=PING_TIMEOUT) as response:
            response.read(64)
        return True, ""
    except Exception as exc:                                     # noqa: BLE001 — 探活失败一律当没启动
        return False, str(exc)


# --------------------------------------------------------------------------- #
# 条目 / 分类
# --------------------------------------------------------------------------- #
def _first(value: Any) -> Any:
    """Zotero 的字段可能是标量，也可能是 {value: ...} 包装。"""
    if isinstance(value, dict):
        return value.get("value", "")
    return value


def creator_name(creator: dict[str, Any]) -> str:
    if creator.get("name"):
        return str(creator["name"]).strip()
    last = str(creator.get("lastName") or "").strip()
    first = str(creator.get("firstName") or "").strip()
    if last and first:
        return "%s %s" % (last, first)
    return last or first


def year_of(date: Any) -> str:
    match = re.search(r"\d{4}", str(_first(date) or ""))
    return match.group(0) if match else ""


def html_to_text(value: Any) -> str:
    """把 Zotero 笔记的 HTML 拧成纯文本（够写进 markdown 就行，不追求完美）。"""
    text = str(value or "")
    if not text:
        return ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = re.sub(r"(?i)<h[1-6][^>]*>", "\n## ", text)
    text = re.sub(r"(?is)<(b|strong)[^>]*>(.*?)</\1>", r"**\2**", text)
    text = re.sub(r"(?is)<(i|em)[^>]*>(.*?)</\1>", r"*\2*", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def normalize(item: dict[str, Any]) -> dict[str, Any]:
    """Zotero item → 本应用用的扁平结构（前端列表/编辑器都用它）。"""
    data = item.get("data") or {}
    meta = item.get("meta") or {}
    creators = [creator_name(c) for c in (data.get("creators") or []) if creator_name(c)]
    tags = [str(_first(t.get("tag") if isinstance(t, dict) else t)).strip()
            for t in (data.get("tags") or [])]
    return {
        "key": str(item.get("key") or data.get("key") or ""),
        "version": item.get("version") or meta.get("version") or 0,
        "type": str(data.get("itemType") or ""),
        "title": str(_first(data.get("title")) or "").strip() or "(无标题)",
        "creators": creators[:8],
        "creators_all": creators,
        "year": year_of(data.get("date")),
        "venue": str(_first(data.get("publicationTitle")) or _first(data.get("proceedingsTitle")) or "").strip(),
        "doi": str(_first(data.get("DOI")) or "").strip(),
        "url": str(_first(data.get("url")) or "").strip(),
        "abstract": html_to_text(data.get("abstractNote")),
        "note": html_to_text(data.get("note")),
        "citation": str(data.get("citationKey") or meta.get("citationKey") or "").strip(),
        "tags": [t for t in tags if t],
        "collections": list(data.get("collections") or []),
    }


def as_note(item: dict[str, Any]) -> dict[str, str]:
    """把条目变成一篇本地 Markdown 笔记的内容（题录 + 摘要 + Zotero 笔记正文）。"""
    it = normalize(item)
    head = [f"# {it['title']}", ""]
    line = " · ".join(x for x in [", ".join(it["creators"]) or "作者未填",
                                 it["year"], it["venue"]] if x)
    head.append("- " + line)
    if it["doi"]:
        head.append(f"- DOI: [{it['doi']}](https://doi.org/{it['doi']})")
    if it["url"]:
        head.append(f"- 链接: {it['url']}")
    head.append(f"- Zotero: `{it['key']}`" + (f" · {it['citation']}" if it["citation"] else ""))
    if it["tags"]:
        head.append("- 标签: " + " ".join("#" + t.replace(" ", "-") for t in it["tags"]))
    body = list(head)
    if it["abstract"]:
        body += ["", "## 摘要", "", it["abstract"]]
    if it["note"]:
        body += ["", "## Zotero 笔记", "", it["note"]]
    body += ["", "## 我的批注", "", "（在这里写）", ""]
    title = it["title"][:160] or "Zotero 条目"
    if it["year"]:
        title = f"{title}（{it['year']}）"
    return {"title": title[:160], "content": "\n".join(body)}


# --------------------------------------------------------------------------- #
# 对外四个动作
# --------------------------------------------------------------------------- #
def find_zotero_executable() -> str | None:
    """自动嗅探本机 Zotero 可执行程序路径（支持 Windows/macOS/Linux）。"""
    if sys.platform == "win32":
        try:
            import winreg
            for root_key in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(root_key, r"Software\Microsoft\Windows\CurrentVersion\App Paths\zotero.exe") as k:
                        val, _ = winreg.QueryValueEx(k, "")
                        if val and os.path.isfile(val):
                            return str(val)
                except Exception:
                    pass
        except Exception:
            pass
        for cand in [
            os.path.expandvars(r"%ProgramFiles%\Zotero\zotero.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Zotero\zotero.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Zotero\zotero.exe"),
            os.path.expandvars(r"%APPDATA%\..\Local\Zotero\zotero.exe"),
        ]:
            if os.path.isfile(cand):
                return str(cand)
    elif sys.platform == "darwin":
        cand = "/Applications/Zotero.app/Contents/MacOS/zotero"
        if os.path.isfile(cand):
            return cand
    else:
        import shutil
        cand = shutil.which("zotero")
        if cand:
            return cand
    return None


def find_zotero_data_dir() -> Path:
    """自适应探测本机 Zotero 真实数据目录（自动解析 profiles/prefs.js 与自定义盘符）。"""
    # 1. 显式环境变量优先
    env_dir = os.environ.get("ZOTERO_DATA")
    if env_dir and os.path.isdir(env_dir):
        return Path(env_dir)

    # 2. 从 Zotero 配置文件 (prefs.js) 精准读取 extensions.zotero.dataDir
    profile_pats: list[str] = []
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            profile_pats.append(os.path.join(appdata, "Zotero", "Zotero", "Profiles", "*", "prefs.js"))
    elif sys.platform == "darwin":
        profile_pats.append(os.path.expanduser("~/Library/Application Support/Zotero/Profiles/*/prefs.js"))
    else:
        profile_pats.append(os.path.expanduser("~/.zotero/zotero/*/prefs.js"))

    for pat in profile_pats:
        for p in glob.glob(pat):
            try:
                with open(p, "rb") as f:
                    content = f.read()
                m = re.search(rb'user_pref\(\s*["\']extensions\.zotero\.dataDir["\']\s*,\s*["\'](.*?)["\']\s*\);', content)
                if m:
                    raw_val = m.group(1).decode("utf-8", errors="replace")
                    clean_path = raw_val.replace("\\\\", "\\")
                    cand = Path(clean_path)
                    if cand.is_dir() and ((cand / "storage").is_dir() or (cand / "zotero.sqlite").is_file()):
                        return cand
            except Exception:
                pass

    # 3. 常见非系统盘直接探查（例如 D:\Zotero, E:\Zotero 等）
    if sys.platform == "win32":
        for drive in ("D", "E", "F", "C"):
            for cand in [Path(f"{drive}:/Zotero"), Path(f"{drive}:/zotero")]:
                if cand.is_dir() and ((cand / "storage").is_dir() or (cand / "zotero.sqlite").is_file()):
                    return cand

    # 4. 默认主目录回退
    return Path.home() / "Zotero"


def _host_launch() -> bool:
    """请窗口宿主（Medium）起 Zotero；没有通道（同进程模式）返回 False 让调用方自己起。"""
    try:
        import paths
        return paths.host_app("zotero")
    except Exception:                             # noqa: BLE001 —— 拿不到通道就走本地起
        return False


def launch_zotero() -> dict[str, Any]:
    """尝试唤起本机 Zotero 客户端并在后台等待 Local API 就绪。"""
    exe = find_zotero_executable()
    if not exe:
        return {"success": False, "message": "未在本机检测到 Zotero 安装路径，请先安装 Zotero 7。"}

    alive, _ = _ping(DEFAULT_BASE)
    if alive:
        return {"success": True, "message": "Zotero 已经在运行中。"}

    try:
        flags = subprocess.DETACHED_PROCESS if os.name == "nt" else 0
        if not _host_launch():            # 沙箱里自己 Popen 出来的是 Low 进程（写不了 ~/Zotero）→ 请宿主起
            subprocess.Popen([exe], creationflags=flags)
        for _ in range(8):
            time.sleep(1)
            if _ping(DEFAULT_BASE)[0]:
                return {"success": True, "message": "Zotero 已成功在后台启动并就绪。"}
        return {"success": True, "message": "已发送启动指令，Zotero 正在初始化中..."}
    except Exception as exc:
        return {"success": False, "message": f"启动 Zotero 失败: {exc}"}


def status(base: str | None = None) -> dict[str, Any]:
    root = _base(base)
    alive, why = _ping(root)
    exe_path = find_zotero_executable()
    data_dir = find_zotero_data_dir()
    storage_exists = (data_dir / "storage").is_dir()
    if not alive:
        return {"connected": False, "readable": False, "base": root,
                "message": "Zotero 没启动（%s 无响应）。" % root.replace("http://", ""),
                "detail": why[:120],
                "executable": exe_path,
                "can_launch": bool(exe_path),
                "data_dir": str(data_dir),
                "storage_exists": storage_exists}
    try:
        _, headers, body = _get(_api_url(root, "/users/0/items", {"limit": 1}))
        if not isinstance(body, list):
            raise ZoteroError("Zotero 返回了非预期的内容。")
        total = headers.get("total-results")
        return {"connected": True, "readable": True, "base": root, "total": int(total) if str(total or "").isdigit() else None,
                "message": "Zotero 已连接（本机自适应）。" + (f"  库内 {total} 条。" if total else ""),
                "executable": exe_path,
                "data_dir": str(data_dir),
                "storage_exists": storage_exists}
    except ZoteroError as exc:
        return {"connected": True, "readable": False, "base": root,
                "message": "Zotero 已启动，但本机 API 读不了：%s（需要 Zotero 7 及以上；"
                           "设置→高级 里本机 API 要打开）。" % exc, "detail": str(exc)[:160],
                "executable": exe_path,
                "data_dir": str(data_dir),
                "storage_exists": storage_exists}


def collections(base: str | None = None, limit: int = MAX_LIMIT) -> dict[str, Any]:
    root = _base(base)
    _, _, body = _get(_api_url(root, "/users/0/collections", {"limit": min(limit, MAX_LIMIT)}))
    rows = body if isinstance(body, list) else []
    out = []
    for row in rows:
        data = row.get("data") or {}
        out.append({"key": str(row.get("key") or ""), "name": str(data.get("name") or "(未命名分类)"),
                    "parent": str(data.get("parentCollection") or "") or None,
                    "count": int((row.get("meta") or {}).get("numItems") or 0)})
    out.sort(key=lambda c: c["name"])
    return {"collections": out, "count": len(out)}


def items(base: str | None = None, collection: str = "", q: str = "",
          start: int = 0, limit: int = 50) -> dict[str, Any]:
    root = _base(base)
    limit = max(1, min(int(limit or 50), MAX_LIMIT))
    start = max(0, int(start or 0))
    path = f"/users/0/collections/{urllib.parse.quote(collection)}/items" if collection else "/users/0/items"
    params: dict[str, Any] = {"limit": limit, "start": start or None, "sort": "dateModified", "direction": "desc"}
    if q:
        params["q"] = q
        params["qmode"] = "everything"
    _, headers, body = _get(_api_url(root, path, params))
    raw = body if isinstance(body, list) else []
    total_raw = headers.get("total-results")
    total = int(total_raw) if str(total_raw or "").isdigit() else None
    rows = [normalize(row) for row in raw]
    return {"items": rows, "start": start, "returned": len(rows), "total": total,
            "hasMore": (start + len(rows)) < total if total is not None else len(rows) >= limit}


def item(key: str, base: str | None = None) -> dict[str, Any]:
    """按 item key 取一条，返回 normalize 后的结构。"""
    root = _base(base)
    key = str(key or "").strip()
    if not key:
        raise ZoteroError("缺少条目 key。")
    _, _, body = _get(_api_url(root, f"/users/0/items/{urllib.parse.quote(key)}"))
    raw = body.get("data") and body or ((body or [{}])[0] if isinstance(body, list) else body)
    if not isinstance(raw, dict) or not raw:
        raise ZoteroError("Zotero 里没找到条目 %s。" % key)
    return normalize(raw)


def storage_dir() -> Path:
    """Zotero 附件目录；自动自适应真实 dataDir。"""
    return find_zotero_data_dir() / "storage"


def linked_attachment_roots() -> list[Path]:
    """Read path preferences only; never execute prefs.js or open Zotero's DB."""
    if sys.platform == "win32":
        profile = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "Zotero/Zotero/Profiles"
    elif sys.platform == "darwin":
        profile = Path.home() / "Library/Application Support/Zotero/Profiles"
    else:
        profile = Path.home() / ".zotero/zotero"
    roots = []
    for prefs in profile.glob("*/prefs.js"):
        try:
            content = prefs.read_text(encoding="utf-8")
            matches = re.findall(r'user_pref\(\s*"extensions\.zotero\.baseAttachmentPath"\s*,\s*("(?:\\.|[^"\\])*")\s*\)', content)
            for match in matches:
                value = json.loads(match)
                if value and Path(value).is_absolute():
                    roots.append(Path(value).resolve())
        except (OSError, UnicodeError, ValueError):
            continue
    return roots


def pdf_path(key: str, base: str | None = None) -> str:
    """条目第一个 PDF 附件在本机的绝对路径。

    支持多种附件存储模式：
      1. 链接文件 (linked_file)：直读 data['path'] 或 attachments: 相对路径；
      2. 独立存储 (imported_file)：按附件 key 读取 storage/<key>/*.pdf；
      3. URI 链接 (enclosure)：解析 file:/// 协议路径。
    """
    root = _base(base)
    key = str(key or "").strip()
    if not key:
        return ""
    try:
        _, _, body = _get(_api_url(root, f"/users/0/items/{urllib.parse.quote(key)}/children"))
    except ZoteroError:
        return ""
    s_dir = storage_dir()
    for row in (body if isinstance(body, list) else []):
        data = row.get("data") or {}
        if data.get("itemType") != "attachment":
            continue
        ct = str(data.get("contentType") or "").lower()
        fn = str(data.get("filename") or "").lower()
        title = str(data.get("title") or "").lower()
        if "pdf" not in ct and not fn.endswith(".pdf") and not title.endswith(".pdf"):
            continue

        # 1. 直接路径 (linked_file / 真实绝对路径)
        p = data.get("path")
        if p:
            if os.path.isfile(p):
                return str(Path(p).resolve())
            if str(p).startswith("attachments:"):
                rel = str(p)[len("attachments:"):].lstrip("/\\")
                for candidate_root in linked_attachment_roots():
                    cand = (candidate_root / rel).resolve()
                    if cand.is_relative_to(candidate_root) and cand.is_file():
                        return str(cand.resolve())

        # 2. enclosure href
        enc = (row.get("links") or {}).get("enclosure") or {}
        href = str(enc.get("href") or "")
        if href.startswith("file:///"):
            cand_p = urllib.parse.unquote(href[8:])
            if os.name == "nt" and cand_p.startswith("/"):
                cand_p = cand_p[1:]
            if os.path.isfile(cand_p):
                return str(Path(cand_p).resolve())

        # 3. 内部 storage 目录 (imported_file)
        folder = s_dir / str(row.get("key") or data.get("key") or "")
        if folder.is_dir():
            hits = sorted(folder.glob("*.pdf"))
            if hits:
                return str(hits[0].resolve())
    return ""


def note_for(base: str | None = None, key: str = "") -> dict[str, str]:
    """按 item key 取一条，产出「可直接存成笔记」的 title/content（不落盘）。"""
    root = _base(base)
    key = str(key or "").strip()
    if not key:
        raise ZoteroError("缺少条目 key。")
    _, _, body = _get(_api_url(root, f"/users/0/items/{urllib.parse.quote(key)}"))
    item = body.get("data") and body or ((body or [{}])[0] if isinstance(body, list) else body)
    if not isinstance(item, dict) or not item:
        raise ZoteroError("Zotero 里没找到条目 %s。" % key)
    return as_note(item)


ITEM_TYPE_LABELS: dict[str, str] = {
    "journalArticle": "期刊文章",
    "preprint": "预印本",
    "conferencePaper": "会议论文",
    "book": "书籍",
    "bookSection": "书籍章节",
    "thesis": "学位论文",
    "report": "报告",
    "webpage": "网页",
    "manuscript": "手稿",
    "patent": "专利",
    "document": "文档",
    "magazineArticle": "杂志文章",
    "newspaperArticle": "报纸文章",
    "blogPost": "博文",
    "presentation": "讲演/汇报",
}

CREATOR_ROLE_LABELS: dict[str, str] = {
    "author": "作者",
    "editor": "编者",
    "contributor": "贡献者",
    "translator": "译者",
    "seriesEditor": "丛书编者",
    "reviewedAuthor": "被评作者",
}


def norm_doi(value: str) -> str:
    """统一形态：小写、去尾随标点。"""
    return (value or "").strip().lower().rstrip(".,;)]}'\"")


def _clean_title_for_query(title: str) -> str:
    """提取核心检索词，去文件名作者年份前缀和标点噪声。"""
    t = (title or "").strip()
    if not t:
        return ""
    if t.lower().endswith(".pdf"):
        t = t[:-4]
    parts = t.split(" - ")
    core = parts[-1] if len(parts) >= 3 else parts[0]
    if ":" in core and len(core.split(":")[0]) >= 15:
        core = core.split(":")[0]
    return re.sub(r"[^\w\s\u4e00-\u9fa5\-]+", " ", core).strip()


def get_item_details(key: str = "", title: str = "", doi: str = "", filename: str = "",
                     base: str | None = None) -> dict[str, Any]:
    """获取完整的 Zotero 条目详情（严格对应 Zotero 右侧面板的所有信息字段）。"""
    root = _base(base)
    item_raw = None

    # 1. 直接按 key 查
    if key:
        try:
            _, _, body = _get(_api_url(root, f"/users/0/items/{urllib.parse.quote(key)}"))
            if isinstance(body, dict) and body:
                item_raw = body
        except Exception:
            item_raw = None

    # 2. 按 DOI 精确查
    if not item_raw and doi:
        clean_doi = norm_doi(doi)
        if len(clean_doi) > 5:
            try:
                _, _, body = _get(_api_url(root, "/users/0/items", {"q": clean_doi, "limit": 6}))
                rows = body if isinstance(body, list) else []
                for r in rows:
                    rd = r.get("data") or {}
                    if rd.get("itemType") not in ("attachment", "note", "annotation"):
                        if norm_doi(rd.get("DOI") or "") == clean_doi:
                            item_raw = r
                            break
            except Exception:
                pass

    # 3. 按标题查
    if not item_raw and title:
        q_title = _clean_title_for_query(title)
        if len(q_title) >= 5:
            try:
                _, _, body = _get(_api_url(root, "/users/0/items", {"q": q_title[:60], "limit": 8}))
                rows = body if isinstance(body, list) else []
                for r in rows:
                    rd = r.get("data") or {}
                    if rd.get("itemType") not in ("attachment", "note", "annotation"):
                        item_raw = r
                        break
                if not item_raw:
                    for r in rows:
                        rd = r.get("data") or {}
                        if rd.get("parentItem"):
                            _, _, pbody = _get(_api_url(root, f"/users/0/items/{rd['parentItem']}"))
                            if isinstance(pbody, dict) and pbody:
                                item_raw = pbody
                                break
            except Exception:
                pass

    # 4. 按文件名查
    if not item_raw and filename:
        q_fn = os.path.splitext(filename)[0][:50]
        if len(q_fn) >= 5:
            try:
                _, _, body = _get(_api_url(root, "/users/0/items", {"q": q_fn, "limit": 6}))
                rows = body if isinstance(body, list) else []
                for r in rows:
                    rd = r.get("data") or {}
                    if rd.get("parentItem"):
                        _, _, pbody = _get(_api_url(root, f"/users/0/items/{rd['parentItem']}"))
                        if isinstance(pbody, dict) and pbody:
                            item_raw = pbody
                            break
            except Exception:
                pass

    if not item_raw:
        return {"zotero_found": False, "error": "Zotero 中未检索到此文献对应条目"}

    data = item_raw.get("data") or {}
    meta = item_raw.get("meta") or {}
    found_key = str(data.get("key") or item_raw.get("key") or "")

    children = []
    if found_key:
        try:
            _, _, cbody = _get(_api_url(root, f"/users/0/items/{found_key}/children"))
            if isinstance(cbody, list):
                children = cbody
        except Exception:
            children = []

    creators = []
    for c in (data.get("creators") or []):
        first = str(c.get("firstName") or "").strip()
        last = str(c.get("lastName") or "").strip()
        name = str(c.get("name") or "").strip()
        full = name or (f"{last}, {first}" if last and first else (last or first))
        role_type = str(c.get("creatorType") or "author")
        role_label = CREATOR_ROLE_LABELS.get(role_type, role_type)
        if full:
            creators.append({
                "name": full,
                "firstName": first,
                "lastName": last,
                "creatorType": role_type,
                "role": role_label,
            })

    attachments = []
    s_dir = storage_dir()
    for ch in children:
        cd = ch.get("data") or {}
        if cd.get("itemType") == "attachment":
            att_key = cd.get("key") or ""
            path = ""
            if cd.get("path") and os.path.isfile(cd.get("path")):
                path = str(Path(cd["path"]).resolve())
            elif att_key:
                folder = s_dir / att_key
                if folder.is_dir():
                    hits = list(folder.glob("*.pdf"))
                    if hits:
                        path = str(hits[0].resolve())
            if not path:
                path = pdf_path(found_key, base)

            attachments.append({
                "key": att_key,
                "title": str(cd.get("title") or cd.get("filename") or "附件"),
                "filename": str(cd.get("filename") or (os.path.basename(path) if path else "")),
                "path": path,
                "contentType": str(cd.get("contentType") or "application/pdf"),
                "url": str(cd.get("url") or ""),
            })

    def _fmt_iso(d: Any) -> str:
        s = str(d or "").strip()
        if not s:
            return ""
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}):(\d{2}))?", s)
        if m:
            y, mo, day, h, mi, sc = m.groups()
            if h is not None:
                return f"{y}/{mo}/{day} {h}:{mi}:{sc}"
            return f"{y}/{mo}/{day}"
        return s

    raw_type = str(data.get("itemType") or "")
    type_label = ITEM_TYPE_LABELS.get(raw_type, raw_type or "文献")

    tags = []
    for t in (data.get("tags") or []):
        tag_val = str(_first(t.get("tag") if isinstance(t, dict) else t)).strip()
        if tag_val:
            tags.append(tag_val)

    return {
        "zotero_found": True,
        "key": found_key,
        "itemType": raw_type,
        "itemTypeLabel": type_label,
        "title": str(_first(data.get("title")) or "").strip(),
        "citationKey": str(data.get("citationKey") or meta.get("citationKey") or "").strip(),
        "creators": creators,
        "publication": str(_first(data.get("publicationTitle")) or _first(data.get("proceedingsTitle")) or _first(data.get("bookTitle")) or _first(data.get("repository")) or "").strip(),
        "journalAbbr": str(_first(data.get("journalAbbreviation")) or "").strip(),
        "publisher": str(_first(data.get("publisher")) or "").strip(),
        "place": str(_first(data.get("place")) or "").strip(),
        "date": str(_first(data.get("date")) or "").strip(),
        "volume": str(_first(data.get("volume")) or "").strip(),
        "issue": str(_first(data.get("issue")) or "").strip(),
        "pages": str(_first(data.get("pages")) or "").strip(),
        "doi": str(_first(data.get("DOI")) or "").strip(),
        "url": str(_first(data.get("url")) or "").strip(),
        "accessDate": _fmt_iso(data.get("accessDate")),
        "issn": str(_first(data.get("ISSN")) or "").strip(),
        "isbn": str(_first(data.get("ISBN")) or "").strip(),
        "language": str(_first(data.get("language")) or "").strip(),
        "libraryCatalog": str(_first(data.get("libraryCatalog")) or "").strip(),
        "extra": str(_first(data.get("extra")) or "").strip(),
        "dateAdded": _fmt_iso(data.get("dateAdded")),
        "dateModified": _fmt_iso(data.get("dateModified")),
        "abstract": html_to_text(data.get("abstractNote")),
        "tags": tags,
        "attachments": attachments,
        "zotero_select_url": f"zotero://select/items/0_{found_key}",
    }


# --------------------------------------------------------------------------- #
# 写入（Zotero 10+ 本机 API：Zotero-Server-ID + 用户授权 key）
#
# 写权限由用户在 Zotero 弹窗里点按钮授予：点「始终允许」→ key 可复用，加密存库；
# 点「允许」→ key 一次性，第一个写请求成功后即作废。所以本机写失败先看授权。
# --------------------------------------------------------------------------- #
WRITE_TIMEOUT = 180                      # 首次授权要等用户点弹窗，别按读超时算
LOCAL_SERVICE = "zotero_local"


def _server_id(base: str) -> str:
    """Zotero 10+ 每个响应都带 Zotero-Server-ID；照响应头取，不打开 zotero.sqlite。"""
    _, headers, _ = _get(_api_url(base, "/users/0/items", {"limit": 1}))
    sid = headers.get("zotero-server-id", "")
    if not sid:
        raise ZoteroError("这台 Zotero 不带 Zotero-Server-ID：本机写入需要 Zotero 10 及以上。")
    return sid


def local_write_state(db: Any) -> dict:
    data = store.read(db, LOCAL_SERVICE)
    return {"authorized": bool(data.get("key")), "remember": bool(data.get("remember")),
            "server_id": str(data.get("server_id") or ""), "updated_at": int(data.get("updated_at") or 0)}


def _authorize(db: Any, app_name: str = "览籍智研") -> dict:
    """申请一个本机写 key。用户点了「始终允许」就落库复用；否则拿到一次性 key（约 1.5s，通常不弹窗）。"""
    base = _base()
    sid = _server_id(base)
    request = urllib.request.Request(base + "/api/local/authorize", method="POST",
                                     data=json.dumps({"appName": app_name}).encode(),
                                     headers={"Content-Type": "application/json", "Zotero-Server-ID": sid})
    try:
        with urllib.request.urlopen(request, timeout=WRITE_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise ZoteroError("你在 Zotero 弹窗里点了「拒绝」，没拿到写权限。") from exc
        if exc.code == 429:
            raise ZoteroError("授权请求太频繁（每分钟最多 5 次），等一分钟再试。") from exc
        raise ZoteroError("申请 Zotero 写权限失败（HTTP %s）。" % exc.code) from exc
    except TimeoutError as exc:
        raise ZoteroError("等你点 Zotero 弹窗超时（%s 秒）。" % WRITE_TIMEOUT) from exc
    key = str(body.get("key") or "")
    if not key:
        raise ZoteroError("Zotero 没返回写权限 key。")
    remember = bool(body.get("remember"))
    if remember:
        store.write(db, LOCAL_SERVICE, {"key": securestore.protect(key), "server_id": sid,
                                        "remember": True, "updated_at": int(time.time())})
    return {"key": key, "server_id": sid, "remember": remember}


def authorize_local_write(db: Any, app_name: str = "览籍智研") -> dict:
    """工具入口：申请本机写权限。点「始终允许」才存下可复用 key。"""
    out = _authorize(db, app_name)
    return {"remember": out["remember"], "stored": out["remember"],
            "message": "已存下可复用写权限。" if out["remember"] else
                      "只拿到一次性 key：写入时会自动续（每次约 1.5s）。想一次性搞定就在弹窗里点「始终允许」。"}


def _write(db: Any, path: str, payload: Any = None, method: str = "POST") -> Any:
    raw = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    data = store.read(db, LOCAL_SERVICE)
    for attempt in (0, 1):
        if not data.get("key"):                          # 没授权 / 一次性 key 用掉了 → 现申请一个
            fresh = _authorize(db)
            data = {"key": securestore.protect(fresh["key"]), "server_id": fresh["server_id"]}
        base = _base()
        headers = {"Content-Type": "application/json", "Zotero-API-Version": API_VERSION,
                   "Zotero-Server-ID": str(data.get("server_id") or ""),
                   "Zotero-API-Key": securestore.unprotect(data["key"])}
        if method in {"DELETE", "PATCH", "PUT"}:        # Zotero 要求带版本，否则 428
            _, version_headers, obj = _get(_api_url(base, path))
            version = version_headers.get("last-modified-version") or str((obj or {}).get("version") or "")
            if version:
                headers["If-Unmodified-Since-Version"] = str(version)
        request = urllib.request.Request(base + "/api" + path, data=raw, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=READ_TIMEOUT) as response:
                body = response.read().decode("utf-8", "replace")
                return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt == 0:          # 一次性 key 被上一次写消耗掉了：换 key 重试
                store.write(db, LOCAL_SERVICE, {})
                data = {}
                continue
            hints = {401: "本机写授权失效：Zotero 那边可能在 设置→高级 里清过写入授权",
                     403: "用户拒绝了这次写入",
                     404: "条目/分类不存在（可能已被删）",
                     412: "条目版本已变更，重新读一次再写",
                     428: "Zotero 要版本前置条件（If-Unmodified-Since-Version）",
                     429: "请求过于频繁，稍后重试"}
            raise ZoteroError("Zotero 写入失败 HTTP %s：%s" % (exc.code, hints.get(exc.code, "检查条目字段"))) from exc
    raise ZoteroError("Zotero 写入失败：换 key 后仍然 401。")


def _results(body: Any, hint: str) -> dict:
    successful = (body or {}).get("successful") or {}
    if not successful:
        failed = (body or {}).get("failed") or {}
        raise ZoteroError("%s失败：%s" % (hint, json.dumps(failed, ensure_ascii=False)[:300] or "Zotero 未接受"))
    return list(successful.values())[0]


def create_collection(db: Any, name: str, parent: str = "") -> dict:
    """新建分类（项目分类）。父分类可选。"""
    name = str(name or "").strip()
    if not name:
        raise ValueError("分类名不能为空")
    payload = [{"name": name, "parentCollection": str(parent or "") or False}]
    created = _results(_write(db, "/users/0/collections", payload), "新建分类")
    return {"key": str(created.get("key") or ""), "name": name, "parent": str(parent or "")}


def add_item(db: Any, item: dict) -> dict:
    """把一条题录写进本机库。字段口径同 Zotero Web API 的 item data。"""
    if not isinstance(item, dict) or not item.get("itemType"):
        raise ValueError("条目必须是带 itemType 的对象")
    created = _results(_write(db, "/users/0/items", [item]), "写入条目")
    return {"key": str(created.get("key") or ""), "itemType": item.get("itemType"), "title": str(item.get("title") or "")}


def link_pdf(db: Any, item_key: str, pdf: str, title: str = "PDF") -> dict:
    """把项目文件夹里的 PDF 挂成链接文件附件（文件不搬进 Zotero storage）。"""
    item_key = str(item_key or "").strip()
    path = Path(str(pdf or "")).expanduser()
    if not item_key:
        raise ValueError("缺少条目 key")
    if not path.is_file():
        raise ValueError("PDF 不存在：%s" % path)
    attachment = {"itemType": "attachment", "parentItem": item_key, "linkMode": "linked_file",
                  "title": str(title or "PDF"), "path": str(path.resolve()),
                  "contentType": "application/pdf"}
    created = _results(_write(db, "/users/0/items", [attachment]), "挂链接文件")
    return {"key": str(created.get("key") or ""), "parentItem": item_key, "path": str(path.resolve())}


def bibtex_for(key: str = "", doi: str = "", title: str = "", base: str | None = None) -> dict[str, Any]:
    """按 key / DOI / 标题找到条目，取 **Zotero 自己导出的 BibTeX**（元数据不自己拼）。

    找不到返回 {"found": False}；调用方据此报缺，别用正文里抄的信息凑一条。
    """
    info = get_item_details(key=key, doi=doi, title=title, base=base)
    if not (info or {}).get("zotero_found"):
        return {"found": False, "error": (info or {}).get("error") or "Zotero 里没有这篇"}
    item_key = str(info.get("key") or "")
    root = _base(base)
    request = urllib.request.Request(_api_url(root, f"/users/0/items/{urllib.parse.quote(item_key)}",
                                             {"format": "bibtex"}),
                                     headers={"Zotero-API-Version": API_VERSION})
    try:
        with urllib.request.urlopen(request, timeout=READ_TIMEOUT) as response:
            text = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise ZoteroError("Zotero 导出 BibTeX 失败（HTTP %s）。" % exc.code) from exc
    return {"found": bool(text.strip()), "key": item_key, "title": info.get("title", ""),
            "doi": info.get("doi", ""), "bibtex": text.strip()}


if __name__ == "__main__":                                        # 手跑自检：python zotero.py [base]
    import sys
    print(json.dumps(status(sys.argv[1] if len(sys.argv) > 1 else None), ensure_ascii=False, indent=1))
