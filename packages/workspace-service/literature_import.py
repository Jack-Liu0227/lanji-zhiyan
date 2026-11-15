"""情报条目 / 手动题录 → 项目文献库。

1.0 的「项目文献」就是 `search_cache` 按 `task_id` 分的一堆行，没有 2.0 那套 Zotero
成员关系，所以这里不引 `project_library`：判「已导入」直接按 DOI / 规范化 URL 在
项目的 search_cache 里找。

# ponytail: 不记「移除关联」——删了再导会重新出现（1.0 本来也没有移除语义）；
# 哪天要「导过又被移除」的区分，再加 rss_project_imports 映射表。
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, unquote, urlsplit, urlunsplit

from typing import Any


def web_url(value: str) -> str:
    """只收干净的 http(s) 原文链接；带账号、空格、怪端口的直接拒。"""
    value = str(value or "").strip()
    try:
        parts = urlsplit(value)
        if (parts.scheme.lower() not in ("http", "https") or not parts.hostname
                or parts.username or parts.password or any(c.isspace() for c in value)):
            raise ValueError()
        parts.port
    except ValueError:
        raise ValueError("请提供有效的 http:// 或 https:// 原文链接") from None
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def normalized_url(value: str) -> str:
    """去掉 utm_* / fbclid 这类跟踪参数和末尾斜杠，用来判「同一条」。"""
    try:
        parts = urlsplit(web_url(value))
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")]
        return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), urlencode(sorted(query)), ""))
    except ValueError:
        return ""


def doi(value: str) -> str:
    value = unquote(str(value or "").strip())
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.I)
    return value.casefold() if re.fullmatch(r"10\.\d{4,9}/\S+", value, re.I) else ""


def from_feed(row: dict[str, Any]) -> dict[str, str]:
    """一条 rss_items → search_cache 的形状。原文链接不合法就抛 ValueError，由调用方记一条失败。"""
    link = web_url(row.get("link"))
    match = re.search(r"10\.\d{4,9}/[^?#\s]+", unquote(urlsplit(link).path), re.I)
    year = re.search(r"\b(?:19|20)\d{2}\b", row.get("published_at") or "")
    return {"title": str(row.get("title") or "").strip(), "authors": row.get("authors") or "",
            "year": year.group() if year else "", "doi": doi(match.group()) if match else "",
            "url": link, "abstract": row.get("summary") or "",
            "source": "RSS · " + (row.get("source_title") or "情报日报")}


def _project_exists(db: Any, project_id: str) -> bool:
    return bool(project_id) and bool(
        db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (project_id,)).fetchone())


def _index(db: Any, project_id: str) -> dict[str, str]:
    """项目已有文献的 DOI / URL 索引 → (键 → result_id)，用来判重、回显「已在项目中」。"""
    index: dict[str, str] = {}
    for row in db.db.execute("SELECT id,doi,url FROM search_cache WHERE task_id=?", (project_id,)):
        key = doi(row["doi"])
        if key:
            index.setdefault("doi:" + key, row["id"])
        link = normalized_url(row["url"])
        if link:
            index.setdefault("url:" + link, row["id"])
    return index


def _lookup(index: dict[str, str], item: dict[str, str]) -> str:
    key = doi(item.get("doi"))
    if key and ("doi:" + key) in index:
        return index["doi:" + key]
    link = normalized_url(item.get("url"))
    return index.get("url:" + link, "") if link else ""


def _remember(index: dict[str, str], item: dict[str, str], rid: str) -> None:
    key = doi(item.get("doi"))
    if key:
        index.setdefault("doi:" + key, rid)
    link = normalized_url(item.get("url"))
    if link:
        index.setdefault("url:" + link, rid)


def annotate(db: Any, rows: list[dict[str, Any]], project_id: str) -> list[dict[str, Any]]:
    """给每条情报补 ``imported_result_id``：非空 = 这个项目里已经有了。"""
    if not project_id or not _project_exists(db, project_id):
        return rows
    index = _index(db, project_id)
    out = []
    for row in rows:
        try:
            rid = _lookup(index, from_feed(row))
        except ValueError:                          # 链接不合法的一条不该让整页报错
            rid = ""
        out.append({**row, "imported_result_id": rid})
    return out


def import_rss(db: Any, project_id: str, identifiers: Any) -> dict[str, Any]:
    """把选中的情报条目写进项目文献库。同一条导两次只留一份。"""
    if not _project_exists(db, project_id):
        raise ValueError("请先选择要导入的项目")
    if (not isinstance(identifiers, list) or not identifiers
            or len(identifiers) > 200 or any(not isinstance(i, str) for i in identifiers)):
        raise ValueError("请选择 1 至 200 条情报")
    identifiers = list(dict.fromkeys(identifiers))
    rows = []
    for identifier in identifiers:
        row = db.db.execute("SELECT * FROM rss_items WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise ValueError("部分情报条目已不存在，请刷新后重新选择")
        rows.append(dict(row))

    index = _index(db, project_id)
    results, failures = [], []
    for row in rows:
        try:
            item = from_feed(row)
        except ValueError as exc:
            failures.append({"id": row["id"], "title": row["title"], "error": str(exc)})
            continue
        rid = _lookup(index, item)
        already = bool(rid)
        if not rid:
            rid = db.cache_result(project_id, item)      # 1.0 自带 DOI/标题去重，天然幂等
            _remember(index, item, rid)
        results.append({"id": row["id"], "result_id": rid, "existing": already})
    return {"project_id": project_id, "imported": sum(not r["existing"] for r in results),
            "existing": sum(r["existing"] for r in results), "items": results, "failures": failures}


def import_reference(db: Any, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """手动填题录（DOI / 链接 / 标题）导进项目文献库。"""
    if not _project_exists(db, project_id):
        raise ValueError("请先选择要导入的项目")
    item = {key: str(body.get(key) or "").strip()
            for key in ("title", "authors", "year", "doi", "url", "abstract")}
    if not item["title"] or len(item["title"]) > 2000:
        raise ValueError("请填写文献标题，最多 2000 字")
    if any(len(value) > 50000 for value in item.values()):
        raise ValueError("题录内容过长")
    if item["doi"]:
        item["doi"] = doi(item["doi"])
        if not item["doi"]:
            raise ValueError("DOI 格式应为 10.xxxx/xxxxx")
    item["url"] = web_url(item["url"] or ("https://doi.org/" + item["doi"] if item["doi"] else ""))
    item["source"] = "手动导入"
    rid = _lookup(_index(db, project_id), item)
    existing = bool(rid)
    if not rid:
        rid = db.cache_result(project_id, item)
    return {"project_id": project_id, "result_id": rid, "existing": existing}


def route(method: str, path: str, body: dict[str, Any], query: dict[str, Any], db: Any) -> Any:
    """挂在 server 的 POST 分支上：命中返回结果，没命中返回 None。"""
    if method == "POST" and path == "/api/rss/import":
        return import_rss(db, str(body.get("project_id") or ""), body.get("ids"))
    if method == "POST" and path == "/api/library/import":
        return import_reference(db, str(body.get("project_id") or ""), body)
    return None
