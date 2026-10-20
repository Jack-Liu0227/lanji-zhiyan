# -*- coding: utf-8 -*-
"""论文开放检索、知识图谱与 OA PDF 下载模块。"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


import paths

PAPERS = paths.PAPERS
UA = {"User-Agent": "research-workbench-1.0 (mailto:local@localhost)"}

def _text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value or "")).strip()

def open_url(req: Any, timeout: int = 30) -> Any:
    import workflow
    return workflow.open_url(req, timeout=timeout)

def _get(url: str, timeout: int = 30) -> bytes:
    with open_url(urllib.request.Request(url, headers=UA), timeout=timeout) as res:
        return res.read()


def search_crossref(query: str, rows: int = 10) -> list[dict[str, Any]]:
    url = ("https://api.crossref.org/works?rows=%d&filter=type:journal-article&query=%s"
           % (rows, urllib.parse.quote(query)))
    items = json.loads(_get(url).decode("utf-8", "replace")).get("message", {}).get("items", [])
    out = []
    for it in items:
        authors = "; ".join(filter(None, (f"{a.get('family','')} {a.get('given','')}".strip() for a in it.get("author", [])[:6])))
        year = str((it.get("issued", {}).get("date-parts", [[""]])[0] or [""])[0] or "")
        links = [l.get("URL", "") for l in it.get("link", []) if l.get("content-type") == "application/pdf"]
        out.append({"source": "crossref", "title": _text((it.get("title") or [""])[0]), "authors": authors, "year": year,
                    "doi": it.get("DOI", ""), "url": it.get("URL", ""), "pdf_url": links[0] if links else "",
                    "abstract": _text(it.get("abstract", ""))[:800]})
    return out


def _oa_pdf(w: dict[str, Any]) -> str:
    """从 OpenAlex work 里挖出开放获取 PDF 直链：先 best_oa_location，再所有 locations，最后 oa_url(仅当它是 .pdf)。"""
    locs = [w.get("best_oa_location") or {}] + list(w.get("locations") or [])
    for l in locs:
        u = (l.get("pdf_url") or "").strip()
        if u:
            return u
    oa = ((w.get("open_access") or {}).get("oa_url") or "").strip()
    return oa if oa.lower().endswith(".pdf") else ""


def search_openalex(query: str, rows: int = 10) -> list[dict[str, Any]]:
    url = ("https://api.openalex.org/works?per-page=%d&filter=type:article&search=%s"
           % (rows, urllib.parse.quote(query)))
    items = json.loads(_get(url).decode("utf-8", "replace")).get("results", [])
    out = []
    for it in items:
        authors = "; ".join(a.get("author", {}).get("display_name", "") for a in it.get("authorships", [])[:6])
        pdf = _oa_pdf(it)
        out.append({"source": "openalex", "title": _text(it.get("title", "")), "authors": authors,
                    "year": str(it.get("publication_year") or ""), "doi": (it.get("doi") or "").replace("https://doi.org/", ""),
                    "url": it.get("id", ""), "pdf_url": pdf or "", "abstract": _text(it.get("title", ""))[:800]})
    return out


def search_arxiv(query: str, rows: int = 10) -> list[dict[str, Any]]:
    url = "http://export.arxiv.org/api/query?search_query=all:%s&max_results=%d" % (urllib.parse.quote(query), rows)
    feed = _get(url).decode("utf-8", "replace")
    out = []
    for entry in re.findall(r"<entry>(.*?)</entry>", feed, re.S):
        get = lambda tag: (re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", entry, re.S) or [None, ""])[1]
        aid = get("id")
        pdf = ""
        for tag in re.findall(r"<link[^>]*>", entry):        # 属性顺序不固定（href 常在 title 前），按整条 link 判
            if 'title="pdf"' in tag or "application/pdf" in tag:
                m = re.search(r'href="([^"]+)"', tag)
                if m:
                    pdf = m.group(1)
                    break
        out.append({"source": "arxiv", "title": _text(get("title")), "year": (get("published") or "")[:4],
                    "authors": "; ".join(re.findall(r"<name>(.*?)</name>", entry)[:6]), "doi": "",
                    "url": aid, "pdf_url": pdf, "abstract": _text(get("summary"))[:800],
                    "arxiv_id": (aid or "").rsplit("/", 1)[-1]})
    return out


SOURCES = {"crossref": search_crossref, "openalex": search_openalex, "arxiv": search_arxiv}


def enrich_oa(items: list[dict[str, Any]], chunk: int = 40) -> int:
    """给还没有 pdf_url 的条目补开放获取直链：按 DOI 批量反查 OpenAlex（一次 ≤40 个 DOI）。
    只补链接、不改标题摘要；查到几条算几条。"""
    need = [i for i in items if (i.get("doi") or "").strip() and not (i.get("pdf_url") or "").strip()]
    filled = 0
    for i in range(0, len(need), chunk):
        part = need[i:i + chunk]
        flt = "|".join(sorted({p["doi"].strip().lower() for p in part}))
        try:
            data = json.loads(_get("https://api.openalex.org/works?per-page=50&filter=doi:"
                                   + urllib.parse.quote(flt)).decode("utf-8", "replace"))
        except Exception:                      # 补链接失败不影响检索结果
            return filled
        by = {}
        for w in data.get("results") or []:
            doi = (w.get("doi") or "").replace("https://doi.org/", "").lower()
            pdf = _oa_pdf(w)
            if doi and pdf:
                by[doi] = pdf
        for it in part:
            u = by.get(it["doi"].strip().lower())
            if u:
                it["pdf_url"] = u
                filled += 1
    return filled


# ============================ 文献库星图（知识网络总览） ============================

_TERM_STOP = {
    "the", "and", "for", "with", "from", "into", "using", "based", "via", "under", "their", "this", "that",
    "study", "studies", "analysis", "review", "effect", "effects", "novel", "new", "toward", "towards",
    "approach", "method", "methods", "model", "models", "system", "systems", "high", "low", "between",
    "performance", "application", "applications", "using", "results", "based", "through", "during",
}
_GRAPH_CACHE: dict[str, dict[str, Any]] = {}     # ponytail: 进程内缓存（键含结果条数，变了就重算）；要跨会话缓存再加表


def lit_terms(title: str) -> set[str]:
    """标题里能当主题词的低频词（去掉停用词/太短的词）。"""
    words = re.findall(r"[A-Za-z][A-Za-z\-]{3,}", (title or "").lower())
    return {w for w in words if w not in _TERM_STOP}


def lit_clusters(items: list[dict[str, Any]], top: int = 5) -> list[dict[str, Any]]:
    """按标题高频词分组当「分支卡」：取文档频率最高的词，且与已选分支重叠 ≥80% 的丢掉（免得两张卡是同一批文献）。"""
    docs = {i["id"]: lit_terms(i.get("title") or "") for i in items}
    df: dict[str, int] = {}
    for terms in docs.values():
        for w in terms:
            df[w] = df.get(w, 0) + 1
    picked: list[tuple[str, set[str]]] = []
    for term, n in sorted(df.items(), key=lambda kv: (-kv[1], kv[0])):
        if len(picked) >= top:
            break
        if n < 2:                                # 只出现一次的标题词当不了分支
            break
        hits = {i for i, terms in docs.items() if term in terms}
        if any(len(hits & seen) >= 0.8 * len(hits) for _, seen in picked):
            continue
        picked.append((term, hits))
    return [{"term": t, "ids": sorted(ids), "count": len(ids)} for t, ids in picked]


def _openalex_works(dois: list[str], chunk: int = 40) -> dict[str, dict[str, Any]]:
    """按 DOI 批量反查 OpenAlex：拿每篇的 OpenAlex id + 参考文献表 + 被引数（一次 ≤40 个 DOI）。"""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(dois), chunk):
        part = sorted({d.strip().lower() for d in dois[i:i + chunk] if d.strip()})
        if not part:
            continue
        flt = "|".join(part)
        url = ("https://api.openalex.org/works?per-page=50&select=id,doi,referenced_works,cited_by_count&filter=doi:"
               + urllib.parse.quote(flt))
        try:
            data = json.loads(_get(url).decode("utf-8", "replace"))
        except Exception:                        # 拿不到边就别画边，节点照画
            continue
        for w in data.get("results") or []:
            doi = (w.get("doi") or "").replace("https://doi.org/", "").lower()
            if doi:
                out[doi] = w
    return out


def litgraph(db: Any, task_id: str, top: int = 5) -> dict[str, Any]:
    """文献库星图 = 本项目的检索结果当节点集，OpenAlex 补它们**之间**的引用边。"""
    items = db.results(task_id)
    if not items:
        return {"error": "这个项目还没有检索结果 —— 先在「工作流 · 论文搜索」里搜一次。"}
    key = f"{task_id}:{len(items)}"
    if key in _GRAPH_CACHE:
        return _GRAPH_CACHE[key]

    clusters = lit_clusters(items, top)
    primary = {}
    for ci, c in enumerate(clusters):
        for i in c["ids"]:
            primary.setdefault(i, ci)

    import sys
    wf = sys.modules.get("workflow")
    oa_fn = getattr(wf, "_openalex_works", _openalex_works) if wf else _openalex_works
    works = oa_fn([i.get("doi") or "" for i in items])

    oa_id = {}                                   # OpenAlex id → 我们的节点 id
    for i in items:
        w = works.get((i.get("doi") or "").strip().lower())
        if w:
            oa_id[w.get("id", "")] = i["id"]
    edges = set()
    for i in items:
        w = works.get((i.get("doi") or "").strip().lower())
        if not w:
            continue
        for ref in w.get("referenced_works") or []:
            j = oa_id.get(ref)
            if j and j != i["id"]:
                edges.add(tuple(sorted((i["id"], j))))

    nodes = []
    for i in items:
        w = works.get((i.get("doi") or "").strip().lower()) or {}
        nodes.append({
            "id": i["id"], "title": _text(i.get("title") or "")[:160], "year": i.get("year") or "",
            "authors": _text(i.get("authors") or "")[:120], "doi": i.get("doi") or "",
            "source": i.get("source") or "", "oa": bool(i.get("pdf_url")), "url": i.get("url") or "",
            "pdf": i.get("pdf_url") or "",
            "cited": w.get("cited_by_count") or 0, "cluster": primary.get(i["id"], -1),
        })
    out = {
        "center": {"title": "文献库", "sub": (db.summary(task_id).get("task") or {}).get("title", ""), "branches": len(clusters)},
        "counts": {"papers": len(items), "with_doi": len(works), "edges": len(edges), "clusters": len(clusters)},
        "clusters": clusters, "nodes": nodes, "edges": [list(e) for e in sorted(edges)],
    }
    _GRAPH_CACHE[key] = out
    return out


def search(query: str, sources: list[str] | None = None, rows: int = 10) -> list[dict[str, Any]]:
    sources = sources or list(SOURCES)
    merged: list[dict[str, Any]] = []
    errors: list[str] = []
    for name in sources:
        fn = SOURCES.get(name)
        if not fn:
            continue
        try:
            merged.extend(fn(query, rows))
        except Exception as exc:  # 单源失败不拖垮整体
            errors.append(f"{name}: {exc}")
    if not merged and errors:
        raise RuntimeError("全部检索源失败 → " + "; ".join(errors))
    return merged


# 项目位置里固定生成的六个子目录：名字 + 写进各自 README 的说明
PROJ_DIRS: list[tuple[str, str]] = [
    ("记忆", "项目记忆的落盘副本：长期记忆.md（项目要点与重要结论）、短期记忆.md（对话内容与细节）。由「创新点审查」等步骤自动维护，人工也可直接编辑，下一步再跑时会读回来。"),
    ("文献库", "开放获取(OA) 全文 PDF：文件名 = 论文标题；只放 OA 全文，付费墙文献请走学校图书馆。"),
    ("笔记", "逐篇精读的 Markdown 笔记：一份笔记一个 .md 文件，含证据表与页码。"),
    ("论文正文", "章节草稿 Markdown：draft-*.md，按章节名命名，是「文章撰写」这一步的直接产物。"),
    ("对话", "对话与过程记录：按日期分 YYYY-MM-DD 子目录，当天产生的记录都落在当天那一层。"),
    ("最终产物", "最终交付件：中文 Word(.docx)、LaTeX(.tex)、参考文献(.bib)、成稿 PDF(.pdf)、图片(PNG/SVG) 和画图代码(.py) 都放这里。"),
    ("其余", "工作流产生的其它杂项文件：中间态导出、脚本、临时数据等，不影响主流程。"),
]


def make_project_tree(base: str | Path, task: dict[str, Any] | None = None) -> list[str]:
    """在用户选的项目位置里建好固定子目录 + 每个目录一个 README，再写一个根 README 说明结构。"""
    root = Path(str(base)).expanduser()
    import paths
    paths.validate_project_dir(root)
    root.mkdir(parents=True, exist_ok=True)
    old, new = root / "最终成品", root / "最终产物"
    if old.is_dir() and not new.exists():
        old.rename(new)                                    # 老项目的旧名目录改名，别留两份
    made: list[str] = []
    for name, desc in PROJ_DIRS:
        d = root / name
        if not d.resolve().is_relative_to(root.resolve()):
            raise ValueError("项目子目录越过项目文件夹")
        d.mkdir(parents=True, exist_ok=True)
        readme = d / "README.md"
        if not readme.exists() and not readme.is_symlink():
            readme.write_text(f"# {name}\n\n{desc}\n", encoding="utf-8")
        made.append(name)
    from project_notes import CATEGORIES
    for name, _ in CATEGORIES:
        category = root / "笔记" / name
        if not category.resolve().is_relative_to(root.resolve()):
            raise ValueError("笔记目录越过项目文件夹")
        category.mkdir(exist_ok=True)
    import capabilities
    capabilities.install_steward_skill(root)
    (root / "对话" / datetime.now().strftime("%Y-%m-%d")).mkdir(parents=True, exist_ok=True)
    # 新项目目录标成低完整性：xelatex / babeldoc 这类 Low 子进程只允许往这类目录写
    try:
        import lowbox
        lowbox.mark_low(root, recursive=True)
    except Exception:
        pass
    t = task or {}
    project_readme = root / "README.md"
    if not project_readme.exists() and not project_readme.is_symlink():
        project_readme.write_text("\n".join([
        f"# {t.get('title') or root.name}",
        "",
        f"这个文件夹是「览籍智研」的一个项目位置：程序按用途把文件分到下面 {len(PROJ_DIRS)} 个子目录，",
        "每个子目录里都有一份 README.md 说明该放什么。",
        "",
        "## 项目信息",
        f"- 研究问题：{t.get('question') or '（未填）'}",
        f"- 关键词 / 材料体系：{t.get('material_system') or '（未填）'}",
        f"- 建立时间：{(t.get('created_at') or '')[:19]}",
        "",
        "## 目录结构",
        *[f"- `{name}/` —— {desc.split('：')[0]}" for name, desc in PROJ_DIRS],
        "",
        ]), encoding="utf-8")
    return made


# ============================ 下载（仅开放获取） ============================

def download_pdf(item: dict[str, Any], target_dir: Path | None = None) -> Path:
    url = (item.get("pdf_url") or "").strip()
    if not url:
        raise ValueError("这条记录没有开放获取(OA)PDF 链接；1.0 只下载 OA 全文，不绕过付费墙。")
    target_dir = target_dir or PAPERS
    target_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\-. ]+", "_", item.get("title", "paper"))[:80].strip() or "paper"
    dest = target_dir / f"{safe}.pdf"
    req = urllib.request.Request(url, headers=UA)
    with open_url(req, timeout=120) as res, dest.open("wb") as fh:
        head = res.read(5)
        if head[:4] != b"%PDF":
            raise ValueError(f"目标不是 PDF（返回：{head!r}）")
        fh.write(head)
        while True:
            chunk = res.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
    return dest
