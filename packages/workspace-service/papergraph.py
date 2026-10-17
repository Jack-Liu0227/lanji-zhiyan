"""论文星图的数据层：只吃库里已存的 PDF 全文（paper_pages），不联网、不外扩。

产出两样东西：
  1) 每篇论文的元数据 —— 标题 / 作者 / DOI / 年份（「项目空间 → 文献」要显示这三个）
  2) 库内论文之间的关系 —— 直接引用（正文参考文献里点到了库内某篇）、共享参考文献

关系判定不靠 DOI 一处：老式 Elsevier 参考文献常常不带 DOI，所以再用「标题里最长的一段的
规范化子串」在参考文献全文里找。两条都命中就算直接引用。
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

import pdfio

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>,;]+")
# 参考文献小标题：整行只写 References / REFERENCES / Bibliography / 参考文献
REF_HEAD_RE = re.compile(r"(?im)^[ \t]*(?:references|bibliography|参考文献|參考文獻)[ \t]*$")
YEAR_RE = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")

# 首页抬头里必然出现、但绝不是标题的行
_HEADER_NOISE = (
    "available online", "©", "(c) ", "all rights reserved", "issn", "http", "www.",
    "journal homepage", "contents lists", "full length article", "review article",
    "snapshot reviews", "research article", "original article", "open access",
    "received ", "accepted ", "published ", "editor", "corresponding author",
    "department of", "university", "institute", "laboratory", "school of", "college of",
    "e-mail", "@", "orcid", "article history", "keywords", "abstract",
)


def norm_doi(value: str) -> str:
    """统一形态：小写、去尾随标点。DOI 比对唯一入口。"""
    return (value or "").strip().lower().rstrip(".,;)]}'\"")


def _squash(text: str) -> str:
    """参考文献全文 → 只留小写字母数字和单空格，方便做标题子串匹配。"""
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-z]+", " ", (text or "").lower())).strip()


def _clean_title(raw: str) -> str:
    return re.sub(r"\s+", " ", (raw or "").strip(" .-–—"))


def _tidy_title(title: str) -> str:
    """掐掉排版噪声：MDPI 的「Check for updates」水印、标题后面粘上的作者名及其上标数字。"""
    t = re.sub(r"\s*Check for update[s]?\s*", " ", title or "", flags=re.I)
    t = re.split(r"\s+[A-Z][A-Za-z\-'’]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][A-Za-z\-'’]+)?\s*\d+\s*[,.]", t)[0]
    return _clean_title(t)


def _looks_authors(ln: str) -> bool:
    """像作者行的样子：逗号分成 ≥2 段且 ≥2 段是「多个首字母大写的词」，或整行无小写词 + and。"""
    parts = [p.strip() for p in re.split(r",\s*", ln) if p.strip()]
    namey = sum(1 for p in parts
                if re.match(r"^[A-Z][A-Za-z\-'’.]+(?:\s+[A-Z][A-Za-z\-'’]*[.]?)+\b", p))
    if len(parts) >= 2 and namey >= 2:
        return True
    if " and " in ln:                                   # 全大写姓名式："SHUYIN OUYANG and JIE M. ZHANG"
        words = ln.replace(",", " ").split()
        lower = [w for w in words if w[:1].islower() and len(w) > 2]
        return not lower and 2 <= len(words) <= 14
    return False


_INST = ("university", "college", "institute", "laboratory", "department", "centre", "center",
         "school of", "academy", "inc.", "ltd", "research center", "national")


def _names_from(ln: str) -> list[str]:
    parts = re.split(r",\s*|\s+and\s+", re.sub(r"[\d*†‡∗§¶#()]+", " ", ln))
    out = []
    for p in parts:
        p = _clean_title(re.sub(r"\s+[a-z]$", "", _clean_title(p)))   # 去尾部单位标号："Haoyang Xie a"
        low = p.lower()
        if len(p.split()) < 2 or len(p.split()) > 4 or any(k in low for k in _INST):
            continue                                          # 单名（.edu 机构碎片）与单位行都不要
        if re.match(r"^[A-Z]", p):
            out.append(p)
    return out


def parse_meta(pages: list[tuple[int, str]]) -> dict[str, Any]:
    """首页文本 → 标题 / 作者 / DOI / 年份。抽不到就给空串，调用方回退到文件名。"""
    first = (pages[0][1] if pages else "") or ""
    blob = "\n".join(t for _, t in pages[:2])
    tail = "\n".join(t for _, t in pages[:8])           # 有些刊把 DOI 放在脚注/页脚

    dois = [norm_doi(d) for d in DOI_RE.findall(tail)]
    doi = dois[0] if dois else ""

    this_year = time.localtime().tm_year
    years = [int(y) for y in YEAR_RE.findall(blob) if int(y) <= this_year]   # 首页最大年份≈出版年（排除引文里的未来年）
    year = str(max(years)) if years else ""

    lines = [unicodedata.normalize("NFKC", ln).strip() for ln in first.splitlines()]   # NFKC 拆连字：ﬁ→fi
    lines = [ln for ln in lines if ln and len(ln) < 200]
    keep: list[str] = []
    author_line = ""
    last = -1
    for idx, ln in enumerate(lines[:40]):
        low = ln.lower()
        if any(noise in low for noise in _HEADER_NOISE) or re.search(r"\|\s*vol|^nature\b|vol\s*\.?\s*\d+\s*\|", low):
            if keep:                                       # 标题已经开始了 → 抬头噪声结束
                break
            continue
        if re.fullmatch(r"[\d\s.,;:/\-–—()]+", ln):        # 纯数字/页码行
            continue
        if re.search(r"\(\d{4}\)\s*[\dA-Za-z\-–:.,]*\s*$", ln):   # 「期刊名 132 (2023) 101043」式页眉
            if keep:
                break
            continue
        if len(ln.split()) < 3 and not keep:               # 抬头短行（"REVIEW"、"ARTICLE"）
            continue
        if not keep and ln[:1].islower():                  # 双栏 PDF 常见：开头是正文半句
            continue
        if keep and (_looks_authors(ln) or any(m in ln for m in "†‡∗§¶")):
            author_line = ln
            last = idx
            break
        keep.append(ln)
        last = idx
        if len(" ".join(keep)) > 180 or len(keep) >= 4:
            break
    title = _tidy_title(" ".join(keep))

    # 作者行没被判出来（单栏/双栏排版差异）→ 顺延到标题后面 3 行里找最像姓名的一行
    if not author_line:
        for ln in lines[last + 1: last + 4] if last >= 0 else []:
            if _looks_authors(ln) or len(_names_from(ln)) >= 2:
                author_line = ln
                break
    names = _names_from(author_line) if author_line else []
    authors = ("、".join(names[:3]) + ("等" if len(names) > 3 else "")) if names else ""

    return {"title": title, "authors": authors, "doi": doi, "year": year}


def refs_text(full: str) -> str:
    marks = list(REF_HEAD_RE.finditer(full or ""))
    return full[marks[-1].end():] if marks else ""


def parse_refs(full: str) -> dict[str, Any]:
    """参考文献段 → 外部 DOI 集合 + 规范化正文（给标题子串匹配用）。"""
    seg = refs_text(full)[:120000]
    return {"dois": sorted({norm_doi(d) for d in DOI_RE.findall(seg)}), "text": _squash(seg)}


def _weak_title(title: str) -> bool:
    """标题看着不像标题（半句正文/页眉/编号）：交给字号最大的那行或 PDF 元数据。"""
    t = (title or "").strip()
    return (len(t) < 15 or t[:1].islower() or re.match(r"^[(（\d]", t) is not None
            or "vol" in t.lower() and "|" in t)


def _author_from_pdf(value: str) -> str:
    """PDF 元数据里的作者串（"A, B & C" / "CNKI" / 空）→ 最多三位 + 等。"""
    parts = [p.strip(" .;") for p in re.split(r"[,;&]|\s+and\s+", value or "") if p.strip(" .;")]
    parts = [p for p in parts if len(p.split()) >= 2 and p.lower() not in ("cnki", "microsoft", "admin")]
    return ("、".join(parts[:3]) + ("等" if len(parts) > 3 else "")) if parts else ""


def ensure_meta(conn, task_id: str) -> int:
    """缺元数据的论文就地补一遍（已算过的跳过），返回本次新算的篇数。

    兜底顺序：首页文本 → PDF 自带元数据 → 首页字号最大的行（标题）→ 文件名。
    """
    rows = conn.execute(
        "SELECT p.id, p.filename, p.source_path FROM papers p LEFT JOIN paper_meta m ON m.paper_id=p.id"
        " WHERE p.task_id=? AND m.paper_id IS NULL", (task_id,)).fetchall()
    done = 0
    for row in rows:
        pid = row["id"] if not isinstance(row, tuple) else row[0]
        filename = row["filename"] if not isinstance(row, tuple) else row[1]
        src = row["source_path"] if not isinstance(row, tuple) else row[2]
        pages = conn.execute("SELECT page_no, text FROM paper_pages WHERE paper_id=? ORDER BY page_no",
                             (pid,)).fetchall()
        pts = [(r["page_no"], r["text"]) for r in pages] if pages and not isinstance(pages[0], tuple) \
            else [(r[0], r[1]) for r in pages]
        if not pts:
            continue
        meta = parse_meta(pts)
        if not meta["authors"] or _weak_title(meta["title"]):
            doc = pdfio.meta(src) if src and Path(src).is_file() else {}
            if not meta["authors"]:
                meta["authors"] = _author_from_pdf(doc.get("author", ""))
            if _weak_title(meta["title"]):
                meta["title"] = pdfio.biggest_title(src) if src and Path(src).is_file() else ""
        if not meta["title"]:
            meta["title"] = Path(filename).stem.replace("_", " ")
        refs = parse_refs("\n".join(t or "" for _, t in pts))
        conn.execute("INSERT OR REPLACE INTO paper_meta VALUES (?,?,?,?,?,?)",
                     (pid, meta["title"], meta["authors"], meta["doi"], meta["year"],
                      json.dumps(refs, ensure_ascii=False)))
        done += 1
    if done:
        conn.commit()
    return done


def _title_keys(title: str) -> list[str]:
    """一篇论文的「指纹」：标题里最长的几个 ≥4 词的连续片段（规范化后），长度 ≥40 才算数。"""
    words = _squash(title).split()
    if len(words) < 4:
        return []
    keys = []
    for size in (8, 6, 5, 4):
        if len(words) >= size:
            for i in range(0, len(words) - size + 1):
                frag = " ".join(words[i:i + size])
                if len(frag) >= 40:
                    keys.append(frag)
            if keys:
                return keys[:3]
    return []


def graph(conn, task_id: str) -> dict[str, Any]:
    """库内论文星图：节点=论文，边=直接引用（有向）| 共享参考文献（无向）。"""
    ensure_meta(conn, task_id)
    rows = conn.execute(
        "SELECT p.id, p.filename, p.pages, m.title, m.authors, m.doi, m.year, m.refs"
        " FROM papers p LEFT JOIN paper_meta m ON m.paper_id=p.id"
        " WHERE p.task_id=? ORDER BY p.created_at", (task_id,)).fetchall()
    papers = []
    for r in rows:
        d = dict(r) if hasattr(r, "keys") else {"id": r[0]}
        d["refs"] = json.loads(d.get("refs") or "{}")
        d["doi"] = norm_doi(d.get("doi") or "")
        d["key"] = _title_keys(d.get("title") or "")
        papers.append(d)

    edges: dict[tuple, dict] = {}
    for src in papers:
        text = src["refs"].get("text") or ""
        for dst in papers:
            if dst["id"] == src["id"]:
                continue
            hit = bool(dst["doi"] and dst["doi"] in src["refs"].get("dois", []))
            if not hit and text and dst["key"]:
                hit = any(frag in text for frag in dst["key"])
            if hit:
                k = (src["id"], dst["id"])
                edges[k] = {"source": src["id"], "target": dst["id"], "kind": "cite", "weight": 1}

    # 只画库内直接引用：共享参考文献（两条引文 DOI 交集）按需求不展示，也就省掉 O(n²) 的两两比对

    degree: dict[str, int] = {}
    for e in edges.values():
        degree[e["source"]] = degree.get(e["source"], 0) + 1
        degree[e["target"]] = degree.get(e["target"], 0) + 1

    nodes = [{
        "id": p["id"], "title": p["title"] or p["filename"], "authors": p["authors"] or "",
        "doi": p["doi"], "year": p["year"], "filename": p["filename"],
        "degree": degree.get(p["id"], 0), "refs": len(p["refs"].get("dois") or []),
    } for p in papers]
    return {
        "nodes": nodes, "edges": list(edges.values()),
        "stats": {"papers": len(nodes), "edges": len(edges),
                  "cited": sum(1 for e in edges.values() if e["kind"] == "cite"),
                  "withDoi": sum(1 for n in nodes if n["doi"]),
                  "withYear": sum(1 for n in nodes if n["year"])},
    }


def self_check() -> None:
    """最小自检：元数据抽取 + 关系判定的关键路径。"""
    page1 = ("Progress in Materials Science 132 (2023) 101043\n"
             "Available online 11 November 2022\n"
             "Toward autonomous laboratories: Convergence of artificial\n"
             "intelligence and experimental automation\n"
             "Yunchao Xie, Kianoosh Sattari, Chi Zhang, Jian Lin *\n"
             "Department of Mechanical Engineering, University of Missouri\n"
             "https://doi.org/10.1016/j.pmatsci.2022.101043\n")
    m = parse_meta([(1, page1)])
    assert m["doi"] == "10.1016/j.pmatsci.2022.101043", m
    assert "Toward autonomous laboratories" in m["title"], m
    assert m["year"] == "2023", m
    assert "Yunchao Xie" in m["authors"], m

    full = ("body text\nReferences\n[1] X. Jiang, W. Wang, Applications of Natural Language\n"
            "Processing and Large Language Models in Materials Discovery, npj Comput. Mater. 11\n"
            "(2025) 79, https://doi.org/10.1038/s41524-025-01554-0.\n")
    r = parse_refs(full)
    assert "10.1038/s41524-025-01554-0" in r["dois"], r
    keys = _title_keys("Applications of Natural Language Processing and Large Language Models in Materials Discovery")
    assert any(k in r["text"] for k in keys), (keys, r["text"][:200])
    assert norm_doi("10.1038/X.2025).") == "10.1038/x.2025"
    print("papergraph self_check ok")


if __name__ == "__main__":
    self_check()
