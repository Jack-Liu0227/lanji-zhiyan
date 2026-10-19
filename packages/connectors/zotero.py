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
  · 本模块**只读**。写回 Zotero 仍走既有 preview/confirm 流程（integrations.zotero_preview）。

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
                for candidate_root in [s_dir.parent, s_dir, Path.home()]:
                    cand = candidate_root / rel
                    if cand.is_file():
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


if __name__ == "__main__":                                        # 手跑自检：python zotero.py [base]
    import sys
    print(json.dumps(status(sys.argv[1] if len(sys.argv) > 1 else None), ensure_ascii=False, indent=1))
