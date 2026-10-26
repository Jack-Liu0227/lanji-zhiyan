"""知识星图的数据层：项目当星核，已有数据摊成五簇 —— 文献 / 笔记 / 知识点 / 检索候选 / 工作流。

读库内已有数据即可出图，不联网。唯一的网络调用是 brief()：给某个术语写 2~3 行简介（调手上配的模型，结果落库）。
边全部由数据推出来：

    引用 / 共享参考文献   papergraph.graph()（正文参考文献里点到了库内某篇）
    笔记 → 文献           notes.paper_id / md_notes.paper_id
    候选 → 文献           search_cache.doi 命中 paper_meta.doi
    提及 → 知识点          正文或标题里出现该术语
    双向共振             两条内容共同提及 ≥2 个知识点

知识点（concept）是唯一需要推导的东西：英文二元词组 + 大写缩写，只保留「≥2 条内容共同提及」的，
按提及条数取前 40 —— 抽不出来就不出节点，宁可少画不造假。
"""
from __future__ import annotations

import re
import time
from collections import Counter
from typing import Any

MODULES = {
    "works": "文献", "methods": "笔记", "concepts": "知识点", "ideas": "检索候选", "drafts": "工作流",
}
KIND_OF_MODULE = {"works": "post", "methods": "note", "concepts": "concept", "ideas": "idea", "drafts": "draft"}
CONCEPT_CAP = 40
CANDIDATE_CAP = 40          # 检索候选只画最近的这些，总数记在簇摘要里

_WORD = r"[A-Za-z][A-Za-z\-]{3,}"
# 二元词组必须**原文相邻**（中间只有一个空白）：先断句再拼，会拼出「available possible」这种假术语
_BIGRAM = re.compile(rf"\b({_WORD})[ \t]+({_WORD})\b")
_ACRONYM = re.compile(r"\b[A-Z][A-Za-z]{1,5}[A-Z]\b|\b[A-Z]{2,6}\b")
# 通用词不配当知识点：它们在任何一篇里都出现，做节点等于没信息
_STOP = set("""
about above after again against also among another approach approaches based because been before being
between both case cases could data design designs different during each early effect effects either
else even every from further given great group groups have having here high higher however into
itself just large larger last least less like made make makes making many more most much must
need needs never next none only other others over paper papers present provides rather results
same several show shown shows since some such than that their them then there these they thing
this those three through under until using very were what when where which while will with within
without would your study studies method methods model models analysis based research review
toward towards future current recent new novel first second third two one use used uses
material materials experimental experiment simulation simulations prediction predictions
efficiency performance property properties available possible considering complete best found
pdf html doi abstract introduction conclusion section figure table references supplementary
by cc page fig et al vs no id vol pp issn journal nature science acta advanced letters
society physics chemistry biology medicine engineering materials today reviews progress
""".split())


def _terms_of(text: str) -> tuple[set[str], set[str]]:
    """一篇内容里的候选术语：原文相邻的二元词组 + 缩写。裸单词不收（material/experimental 这类全是噪音）。

    返回 (术语, 其中「原样小写出现过」的那些) —— 两个词都大写开头的词组多是作者名 / 刊名
    （Shaohan Tian、Nature Machine…），只有在小写形态里真的出现过，才算真术语。
    """
    out: set[str] = set()
    low_seen: set[str] = set()
    for a, b in _BIGRAM.findall(text or ""):
        key = a.lower() + " " + b.lower()
        if a.lower() in _STOP or b.lower() in _STOP or len(a) < 4 or len(b) < 4:
            continue
        out.add(key)
        if a.islower() and b.islower():
            low_seen.add(key)
    for ac in _ACRONYM.findall(text or ""):
        if len(ac) >= 2 and ac.lower() not in _STOP:
            out.add(ac)
    return out, low_seen


def concepts(docs: list[tuple[str, str]], cap: int = CONCEPT_CAP) -> list[tuple[str, list[str]]]:
    """docs = [(id, text)] → [(术语, 提及它的 id 列表)]：只留 ≥2 条内容共同提及的。"""
    where: dict[str, set[str]] = {}
    lower: set[str] = set()
    for did, text in docs:
        terms, low_seen = _terms_of(text)
        lower |= low_seen
        for t in terms:
            where.setdefault(t, set()).add(did)
    keep = [(t, sorted(ids)) for t, ids in where.items()
            if len(ids) >= 2 and (" " not in t or t in lower)]
    # 提及条数多的在前；同票时二元词组优先（长的那条信息量大）
    keep.sort(key=lambda kv: (-len(kv[1]), -len(kv[0].split()), kv[0]))
    big = {t for t, _ in keep if " " in t}
    return [(t, ids) for t, ids in keep if " " in t or not any(t in b.split() for b in big)][:cap]


def _short(title: str, n: int = 26) -> str:
    t = " ".join((title or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def overview(conn) -> dict[str, Any]:
    """星图第一级：一个项目一个星团，只给计数（纯 SQL，不抽正文，几百毫秒内出图）。"""
    rows = [dict(r) for r in conn.execute(
        "SELECT t.id, t.title, t.stage, t.updated_at,"
        " (SELECT COUNT(*) FROM papers p WHERE p.task_id=t.id) AS papers,"
        " (SELECT COUNT(*) FROM notes n WHERE n.task_id=t.id) AS notes,"
        " (SELECT COUNT(*) FROM md_notes m WHERE m.task_id=t.id) AS md_notes,"
        " (SELECT COUNT(*) FROM search_cache c WHERE c.task_id=t.id) AS candidates,"
        " (SELECT COUNT(*) FROM steps s WHERE s.task_id=t.id) AS steps"
        " FROM research_tasks t ORDER BY t.updated_at DESC").fetchall()]
    for r in rows:
        r["notes"] += r.pop("md_notes")
        r["nodes"] = r["papers"] + r["notes"] + r["candidates"] + r["steps"]
    totals = {k: sum(r[k] for r in rows)
              for k in ("papers", "notes", "candidates", "steps", "nodes")}
    return {"level": "projects", "projects": rows, "totals": totals, "tasks": rows}


def graph(conn, task_id: str = "") -> dict[str, Any]:
    import papergraph

    tasks = [dict(r) for r in conn.execute(
        "SELECT t.id, t.title, t.question, t.material_system, t.stage,"
        " (SELECT COUNT(*) FROM papers p WHERE p.task_id=t.id) AS papers,"
        " (SELECT COUNT(*) FROM notes c WHERE c.task_id=t.id) AS notes"
        " FROM research_tasks t ORDER BY papers DESC, t.updated_at DESC").fetchall()]
    task = next((t for t in tasks if t["id"] == task_id), tasks[0] if tasks else None)
    if not task:
        return {"tasks": [], "task": None, "nodes": [], "edges": [], "stats": {}}
    tid = task["id"]

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    docs: list[tuple[str, str]] = []          # 知识点抽取的语料

    # ① 文献（papergraph 已算好引用 / 共享参考文献）
    pg = papergraph.graph(conn, tid)
    paper_title: dict[str, str] = {}
    for n in pg["nodes"]:
        paper_title[n["id"]] = n["title"]
        nodes.append({
            "id": n["id"], "kind": "paper", "m": "works", "title": n["title"],
            "short": _short(n["title"]), "ref": n["id"],
            "doi": n.get("doi") or "", "year": n.get("year") or "",
            "authors": n.get("authors") or "", "filename": n.get("filename") or "",
            "degree": n.get("degree") or 0,
            "meta": [["年份", n["year"] or "—"], ["度", n["degree"]], ["DOI", "有" if n["doi"] else "无"]],
        })
        docs.append((n["id"], n["title"]))
    for e in pg["edges"]:
        edges.append({"source": e["source"], "target": e["target"],
                      "kind": "cite" if e["kind"] == "cite" else "shared", "weight": e.get("weight", 1)})

    # ② 笔记（精读笔记 + 大笔记）
    import asset_service
    for r in conn.execute(
            "SELECT id, title, markdown, paper_id, created_at FROM notes WHERE task_id=?", (tid,)).fetchall():
        r = dict(r)
        if asset_service.is_instruction_note(conn, tid, r["id"], r["markdown"]):
            continue
        nodes.append({
            "id": r["id"], "kind": "note", "m": "methods", "title": r["title"],
            "short": _short(r["title"]), "ref": r["paper_id"] or "",
            "meta": [["字", len(r["markdown"] or "")], ["挂文献", "有" if r["paper_id"] else "无"]],
        })
        docs.append((r["id"], (r["markdown"] or "")[:6000]))
        if r["paper_id"] in paper_title:
            edges.append({"source": r["id"], "target": r["paper_id"], "kind": "reads", "weight": 1})
    for r in conn.execute(
            "SELECT id, title, content, paper_id FROM md_notes WHERE task_id=?", (tid,)).fetchall():
        r = dict(r)
        if asset_service.is_instruction_note(conn, tid, r["id"], r["content"]):
            continue
        nodes.append({
            "id": r["id"], "kind": "note", "m": "methods", "title": r["title"],
            "short": _short(r["title"]), "ref": r["paper_id"] or "",
            "meta": [["字", len(r["content"] or "")], ["挂文献", "有" if r["paper_id"] else "无"]],
        })
        docs.append((r["id"], (r["content"] or "")[:4000]))
        if r["paper_id"] in paper_title:
            edges.append({"source": r["id"], "target": r["paper_id"], "kind": "reads", "weight": 1})

    # ③ 检索候选（搜到但还没入库的文献）
    total_cand = conn.execute("SELECT COUNT(*) FROM search_cache WHERE task_id=?", (tid,)).fetchone()[0]
    rows = conn.execute(
        "SELECT id, title, year, authors, doi, source, pdf_url, abstract FROM search_cache"
        " WHERE task_id=? ORDER BY created_at DESC LIMIT ?", (tid, CANDIDATE_CAP)).fetchall()
    doi_of_paper = {}
    for r in conn.execute("SELECT p.id, m.doi FROM papers p JOIN paper_meta m ON m.paper_id=p.id"
                          " WHERE p.task_id=? AND m.doi<>''", (tid,)).fetchall():
        doi_of_paper[str(r[1]).strip().lower()] = r[0]
    for r in rows:
        r = dict(r)
        nodes.append({
            "id": r["id"], "kind": "candidate", "m": "ideas", "title": r["title"],
            "short": _short(r["title"]), "ref": "",
            "meta": [["年", r["year"] or "—"], ["源", r["source"] or "—"], ["PDF", "有" if r["pdf_url"] else "无"]],
        })
        docs.append((r["id"], (r["title"] or "") + " " + (r.get("abstract") or "")[:1200]))
        did = doi_of_paper.get(str(r.get("doi") or "").strip().lower())
        if did:
            edges.append({"source": r["id"], "target": did, "kind": "found", "weight": 1})

    # ④ 工作流（项目走过的步骤）
    for r in conn.execute(
            "SELECT id, stage, status, summary, created_at FROM steps WHERE task_id=?"
            " ORDER BY created_at DESC LIMIT 40", (tid,)).fetchall():
        r = dict(r)
        nodes.append({
            "id": r["id"], "kind": "step", "m": "drafts",
            "title": f"{r['stage']} · {r['summary'] or r['status']}",
            "short": _short(f"{r['stage']} · {r['summary'] or r['status']}", 22), "ref": "",
            "meta": [["阶段", r["stage"]], ["状态", r["status"]], ["时间", (r["created_at"] or "")[:10]]],
        })

    # ⑤ 知识点（≥2 条内容共同提及）
    for term, ids in concepts(docs):
        cid = "c:" + term
        nodes.append({
            "id": cid, "kind": "concept", "m": "concepts", "title": term, "short": term, "ref": "",
            "meta": [["提及", len(ids)], ["类型", "二元词组" if " " in term else "缩写"]],
        })
        for did in ids:
            edges.append({"source": did, "target": cid, "kind": "mention", "weight": 1})

    # 双向共振：两条内容共同提及 ≥2 个知识点
    by_node: dict[str, set[str]] = {}
    for e in edges:
        if e["kind"] == "mention":
            by_node.setdefault(e["source"], set()).add(e["target"])
    ids_sorted = sorted(by_node)
    for i, a in enumerate(ids_sorted):
        for b in ids_sorted[i + 1:]:
            shared = by_node[a] & by_node[b]
            if len(shared) >= 2:
                edges.append({"source": a, "target": b, "kind": "resonance", "weight": len(shared)})

    # 出链 / 反链 / 双向 —— 右栏分析栏要
    out_deg: Counter[str] = Counter()
    in_deg: Counter[str] = Counter()
    pair: dict[tuple[str, str], set[str]] = {}
    for e in edges:
        s, t, k = e["source"], e["target"], e["kind"]
        out_deg[s] += 1
        in_deg[t] += 1
        pair.setdefault((s, t), set()).add(k)
    mutual = set()
    for (s, t), ks in pair.items():
        if (t, s) in pair:
            mutual.add(s)
            mutual.add(t)
    for n in nodes:
        n["out"] = out_deg.get(n["id"], 0)
        n["in"] = in_deg.get(n["id"], 0)
        n["both"] = 1 if n["id"] in mutual else 0

    counts: Counter[str] = Counter(n["m"] for n in nodes)
    modules = [{"id": m, "label": MODULES[m], "count": counts.get(m, 0)} for m in MODULES]
    if total_cand > CANDIDATE_CAP:
        for m in modules:
            if m["id"] == "ideas":
                m["total"] = total_cand

    return {
        "tasks": tasks,
        "task": {k: task[k] for k in ("id", "title", "question", "material_system", "stage")},
        "modules": modules,
        "nodes": nodes,
        "edges": edges,
        "stats": {"content": len(nodes), "links": len(edges),
                  "mutual": len(mutual) // 2, "concepts": counts.get("concepts", 0)},
    }


# ── 术语简介：给知识点节点写 2~3 行卡，落在库里，同一个术语只花一次调用 ──
BRIEF_DDL = """
CREATE TABLE IF NOT EXISTS concept_brief (
  term TEXT PRIMARY KEY, text TEXT NOT NULL, model TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
)"""

BRIEF_PROMPT = (
    "你在给科研知识图谱里的术语写卡片。用简体中文写 2~3 行、总共不超过 80 字：先说清它的中英文全称"
    "（缩写才给全称，本身就是全称可省），再说它是什么、主要用在哪里。只输出正文，"
    "不要标题、不要 markdown、不要客套话、不要「以下是」这种开头。"
)


def brief(conn, term: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """一个术语的简介：库里有就回库里的，没有才调一次模型并落库。"""
    term = " ".join((term or "").split())[:80]
    if not term:
        return {"term": "", "text": "", "error": "没有术语"}
    conn.execute(BRIEF_DDL)
    row = conn.execute("SELECT text, model FROM concept_brief WHERE term=?", (term.lower(),)).fetchone()
    if row:
        return {"term": term, "text": row[0], "model": row[1], "cached": True}
    if not profile:
        return {"term": term, "text": "", "error": "还没配置模型：到「模型」页加一个并设为当前模型"}

    import workflow
    try:
        text = (workflow.chat(profile, [{"role": "system", "content": BRIEF_PROMPT},
                                        {"role": "user", "content": term}],
                              temperature=0.2, max_tokens=900) or "").strip()
    except Exception as exc:                      # 模型挂了不要影响星图，只把原因带回去
        return {"term": term, "text": "", "error": str(exc)[:200]}
    text = " ".join(text.split())
    if not text:
        # 这种模型会把预算先花在思考上：上限太低时 completion 拉满、正文为空
        return {"term": term, "text": "", "error": "模型这次没给出正文，再打开一次试试"}
    conn.execute("INSERT OR REPLACE INTO concept_brief(term, text, model, created_at) VALUES(?,?,?,?)",
                 (term.lower(), text, str(profile.get("model") or ""), time.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    return {"term": term, "text": text, "model": profile.get("model") or "", "cached": False}


def _overview_check() -> None:
    """概览层：一个项目一行计数，大笔记和精读笔记要合到手栏，总数要对得上。"""
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
      CREATE TABLE research_tasks(id TEXT PRIMARY KEY, title TEXT, stage TEXT, updated_at TEXT);
      CREATE TABLE papers(id TEXT PRIMARY KEY, task_id TEXT);
      CREATE TABLE notes(id TEXT PRIMARY KEY, task_id TEXT);
      CREATE TABLE md_notes(id TEXT PRIMARY KEY, task_id TEXT);
      CREATE TABLE search_cache(id TEXT PRIMARY KEY, task_id TEXT);
      CREATE TABLE steps(id TEXT PRIMARY KEY, task_id TEXT);
    """)
    c.execute("INSERT INTO research_tasks VALUES('a','项目甲','definition','2026-01-01')")
    c.execute("INSERT INTO research_tasks VALUES('b','项目乙','writing','2026-01-02')")
    c.execute("INSERT INTO papers VALUES('p1','a')")
    c.execute("INSERT INTO notes VALUES('n1','a')")
    c.execute("INSERT INTO md_notes VALUES('m1','a')")
    c.execute("INSERT INTO search_cache VALUES('s1','b')")
    c.execute("INSERT INTO steps VALUES('x1','b')")
    out = overview(c)
    assert out["level"] == "projects" and len(out["projects"]) == 2, out
    a = next(p for p in out["projects"] if p["id"] == "a")
    assert (a["papers"], a["notes"], a["nodes"]) == (1, 2, 3), a
    assert out["totals"]["nodes"] == 5, out["totals"]


def self_check() -> None:
    """最小自检：知识点只留共现 ≥2 的、二元词组优先、边能推出来。"""
    _overview_check()
    docs = [("a", "Machine learning for materials. Machine learning again."),
            ("b", "Deep learning and machine learning in battery design."),
            ("c", "A one-off zebra appears here only once."),
            ("d", "Materials. Machine running experiments.")]
    got = dict(concepts(docs))
    assert "machine learning" in got, got
    assert set(got["machine learning"]) == {"a", "b"}, got["machine learning"]
    assert "zebra" not in got, got                       # 裸单词不收（只出现一次也不收）
    assert "materials machine" not in got, got            # 跨句拼出来的假术语
    assert "one off" not in got and "off zebra" not in got, got
    assert "weiren wang" not in dict(concepts([("a", "Weiren Wang and others"), ("b", "Weiren Wang again")])), "人名不该成节点"
    assert _short("abcdefghijklmnopqrstuvwxyz", 10) == "abcdefghi…"
    print("starmap self_check ok")


if __name__ == "__main__":
    self_check()
