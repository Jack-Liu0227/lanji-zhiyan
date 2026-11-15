"""情报日报：RSS 抓取与解析（纯标准库，RSS 2.0 / RSS 1.0(RDF) / Atom 都吃）。

管理全局订阅配置，抓取与解析来源并调度入库。落库在 workflow.WorkflowDB，
路由在 server.py。文章按 link 全局去重，删除来源仍保留已入库内容。
"""
from __future__ import annotations

import email.utils
import html
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree as ET

UA = "Mozilla/5.0 (compatible; research-workbench/1.0; +local)"
TIMEOUT = 12
MAX_ITEMS = 60                 # 单个源一次最多收 60 条（够看；多的下次刷新还会来）
SUMMARY_MAX = 400
_sources_lock = threading.RLock()

# 内置来源；用户可在设置中添加、编辑或停用。
DEFAULT_SOURCES = [
    {"url": "https://www.nature.com/nenergy.rss", "title": "Nature Energy", "category": "学术论文"},
    {"url": "https://www.nature.com/nmat.rss", "title": "Nature Materials", "category": "学术论文"},
    {"url": "https://www.nature.com/npjcompumats.rss", "title": "npj Computational Materials", "category": "学术论文"},
    {"url": "https://rss.arxiv.org/rss/cond-mat.mtrl-sci", "title": "arXiv cond-mat.mtrl-sci", "category": "学术论文"},
    {"url": "https://hnrss.org/frontpage", "title": "Hacker News", "category": "技术新闻"},
]

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _clean(s: str) -> str:
    s = html.unescape(_TAG.sub(" ", s or ""))
    s = _WS.sub(" ", s).strip()
    return s[:SUMMARY_MAX]


def _iso(raw: str) -> str:
    """RFC822（RSS）或 ISO（Atom）→ 'YYYY-MM-DD HH:MM'，认不出就给空串。"""
    raw = (raw or "").strip()
    if not raw:
        return ""
    dt = None
    try:
        dt = email.utils.parsedate_to_datetime(raw)
    except Exception:
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)          # 统一 UTC，前端按本地时间显示
    return dt.strftime("%Y-%m-%d %H:%M")


def _pick(node: ET.Element, *names: str) -> str:
    """按局部名找文本（命名空间无关）：先直接子元素，再往下一层（Atom 的 author/name）。"""
    want = {n.lower() for n in names}
    for kid in node:
        if _local(kid.tag) in want and (kid.text or "").strip():
            return kid.text.strip()
    for kid in node.iter():
        if kid is node:
            continue
        if _local(kid.tag) in want and (kid.text or "").strip():
            return kid.text.strip()
    return ""


def parse(raw: bytes | str, charset: str = "utf-8") -> list[dict[str, str]]:
    """把一份 feed 解析成 [{title, link, authors, summary, published_at}]。"""
    if isinstance(raw, bytes):
        raw = raw.decode(charset or "utf-8", "replace")
    body = raw.strip().lstrip("\ufeff")
    head = body[:200].lstrip().lower()
    if head.startswith("<!doctype") or head.startswith("<html"):
        raise ValueError("这不是 RSS：拿到的是网页（地址失效或被反爬拦了）")
    root = ET.fromstring(body)
    items = [el for el in root.iter() if _local(el.tag) in ("item", "entry")]
    out: list[dict[str, str]] = []
    for it in items[:MAX_ITEMS]:
        # 链接：RSS 是 <link>文本</link>；Atom 是 <link href=.../>（挑 alternate 或第一个有 href 的）
        link = _pick(it, "link") if _local(it.tag) == "item" else ""
        if not link:
            for kid in it:
                if _local(kid.tag) == "link" and kid.get("href"):
                    if kid.get("rel") in (None, "alternate"):
                        link = kid.get("href")
                        break
                    link = link or kid.get("href")
        if not link and _local(it.tag) == "item":
            link = _pick(it, "guid")
        link = (link or "").strip()
        if not link:
            continue
        title = _clean(_pick(it, "title")) or link
        authors = _clean(_pick(it, "author", "creator", "name"))
        summary = _clean(_pick(it, "description", "summary", "content", "encoded"))
        published = _iso(_pick(it, "pubdate", "published", "updated", "date"))
        out.append({"title": title, "link": link, "authors": authors,
                    "summary": summary, "published_at": published})
    return out


def fetch(url: str, timeout: int = TIMEOUT) -> list[dict[str, str]]:
    """抓一个 feed 并解析；网络/格式问题直接抛，由调用方记进失败清单。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        cs = resp.headers.get_content_charset()
    return parse(raw, cs or "utf-8")


def refresh(db: Any) -> dict[str, Any]:
    """抓取已启用来源；网络并发，SQLite 落库在主线程串行完成。"""
    from concurrent.futures import ThreadPoolExecutor
    import service_settings

    with _sources_lock:
        if not db.rss_sources() and not service_settings.read(db,'rss').get('configured'):
            seed(db)
        srcs = [source for source in db.rss_sources() if source['enabled']]
    if not srcs:
        return {"sources": 0, "fetched": 0, "added": 0, "failures": []}

    def one(s: dict) -> tuple[dict, list, str]:
        err = ""
        for attempt in (1, 2):
            try:
                return s, fetch(s["url"]), ""
            except Exception as exc:                   # 超时/403/不是 XML…都算这一条失败，其它源照抓
                err = f"{type(exc).__name__}: {exc}"[:200]
                if attempt == 1:
                    time.sleep(1.5)                    # Nature 家并发连发会被限流（拿到网页），隔一下重试一次
        return s, [], err

    ok = added = 0
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=min(6, len(srcs))) as pool:
        for s, items, err in pool.map(one, srcs):
            if err:
                db.mark_rss_fetch(s["id"], err)
                failures.append({"title": s["title"], "url": s["url"], "error": err})
            else:
                added += db.save_rss_items(s, items)
                db.mark_rss_fetch(s["id"], "")
                ok += 1
    return {"sources": len(srcs), "fetched": ok, "added": added, "failures": failures}


def seed(db: Any) -> int:
    """补上内置来源（URL 已存在的跳过），保留已有配置。"""
    have = {s["url"] for s in db.rss_sources()}
    n = 0
    for d in DEFAULT_SOURCES:
        if d["url"] not in have:
            db.add_rss_source(d["url"], d["title"], d["category"])
            n += 1
    return n


def _source(db: Any, sid: str) -> dict[str, Any]:
    try:return db.rss_source(sid)
    except KeyError:raise ValueError('RSS 订阅源不存在') from None


def _configured(db: Any) -> None:
    import service_settings
    service_settings.write(db,'rss',{**service_settings.read(db,'rss'),'configured':True})


def save_source(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    with _sources_lock:
        sid=str(body.get('id') or '')
        old=_source(db,sid) if sid else {}
        url=str(body.get('url',old.get('url','')) or '').strip()
        try:
            parts=urllib.parse.urlsplit(url)
            valid=parts.scheme in ('http','https') and bool(parts.hostname) and not any(c.isspace() for c in url)
            parts.port
        except ValueError:valid=False
        if not valid or len(url)>8192:raise ValueError('请输入有效的 http:// 或 https:// RSS 地址')
        url=parts.geturl()
        title=str(body.get('title',old.get('title','')) or '').strip() or url
        category=str(body.get('category',old.get('category','学术论文')) or '').strip() or '学术论文'
        if len(title)>200:raise ValueError('订阅源名称不能超过 200 字符')
        if len(category)>60:raise ValueError('分类不能超过 60 字符')
        enabled=body.get('enabled',old.get('enabled',True))
        if not isinstance(enabled,(bool,int)) or enabled not in (0,1):raise ValueError('启用状态无效')
        duplicate=db.db.execute('SELECT id FROM rss_sources WHERE url=? AND id<>?',(url,sid)).fetchone()
        if duplicate and (not old or url!=old['url']):raise ValueError('该 RSS 地址已存在，请编辑已有订阅源')
        if not sid:sid=db.add_rss_source(url,title,category)['id']
        db.db.execute('UPDATE rss_sources SET url=?,title=?,category=?,enabled=? WHERE id=?',(url,title,category,int(enabled),sid))
        db.db.commit();_configured(db)
        return {'source':db.rss_source(sid)}


def delete_source(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    with _sources_lock:
        source=_source(db,str(body.get('id') or ''))
        db.db.execute('DELETE FROM rss_sources WHERE id=?',(source['id'],));db.db.commit();_configured(db)
        return {'deleted':source['id'],'retained_items':db.rss_count(source_id=source['id'])}


def add_defaults(db: Any) -> dict[str, Any]:
    with _sources_lock:
        added=seed(db);_configured(db)
        return {'added':added,'sources':db.rss_sources()}


if __name__ == "__main__":                     # 自带小样，不联网也能验解析
    rss2 = """<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
      <item><title>钠电正极 &amp; 掺杂</title><link>https://a.example/1</link>
        <description>&lt;p&gt;摘要&lt;/p&gt;</description><pubDate>Mon, 06 Oct 2026 08:30:00 +0800</pubDate>
        <dc:creator xmlns:dc="http://purl.org/dc/elements/1.1/">Zhang, Li</dc:creator></item></channel></rss>"""
    atom = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Atom 也有</title><link rel="alternate" href="https://b.example/2"/>
        <summary>summary text</summary><published>2026-10-05T21:00:00Z</published>
        <author><name>Wang</name></author></entry></feed>"""
    a, b = parse(rss2), parse(atom)
    assert len(a) == 1 and a[0]["link"] == "https://a.example/1", a
    assert a[0]["title"] == "钠电正极 & 掺杂" and a[0]["authors"] == "Zhang, Li", a
    assert a[0]["summary"] == "摘要" and a[0]["published_at"] == "2026-10-06 00:30", a   # +0800 → UTC
    assert len(b) == 1 and b[0]["link"] == "https://b.example/2" and b[0]["authors"] == "Wang", b
    assert b[0]["published_at"] == "2026-10-05 21:00", b
    try:                                  # 拿到网页（死链/反爬）要当场说清楚，别丢个 XML 语法错
        parse("<!DOCTYPE html><html><body>nope</body></html>")
        raise AssertionError("HTML 应该报错")
    except ValueError as e:
        assert "不是 RSS" in str(e), e
    assert len(DEFAULT_SOURCES) == 5, "来源固定 5 个"
    print("rss.py 自检通过", a[0]["published_at"], b[0]["published_at"], "| 固定来源:", len(DEFAULT_SOURCES))
