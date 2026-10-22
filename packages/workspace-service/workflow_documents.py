# -*- coding: utf-8 -*-
"""确认大纲、论文正文、审查文档统一管理与专用 Agent 调度。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fileguard
import paths
import pdfio
from research_core import now
from workflow_prompts import OUTLINE_TEMPLATE, _schema, DELEGATE_TOOL, skill_text

if TYPE_CHECKING:
    from workflow import WorkflowDB

DATA, PAPERS, NOTES, OUT = paths.DATA, paths.PAPERS, paths.NOTES, paths.OUT





# ============================ 确认大纲 / 论文撰写 / 稿件审查（三步共用一套「左边那份文件」）============
DOC_STEPS = {
    "outline": {"file": "框架.md", "dir": "论文正文", "label": "框架",
                "hint": "大标题 `## `、小标题 `### `、每个小标题下一行 `- 说明：…`"},
    "write":   {"file": "正文.md", "dir": "论文正文", "label": "正文",
                "hint": "从框架展开成正文：标题层级跟框架一致，每节写实；数据没有就写「待补」，不要编。"},
    "review":  {"file": "审查意见.md", "dir": "论文正文", "label": "审查意见",
                "hint": "审稿意见本身：三重视角打分 + 决议 + 逐段可操作的修改建议；要动正文回「论文撰写」。"},
}


def doc_path(db: "WorkflowDB", task_id: str, kind: str = "outline") -> Path:
    """每一步左边那份文件：设了项目位置就落「<项目>/论文正文/」，没设位置按项目 id 分名（免得互相覆盖）。"""
    d = DOC_STEPS.get(kind) or DOC_STEPS["outline"]
    base = db.base_dir(task_id)
    if base:
        return base / d["dir"] / d["file"]
    stem = d["file"].replace(".md", "")
    return OUT / f"{stem}_{task_id[:8]}.md"


def doc_read(db: "WorkflowDB", task_id: str, kind: str = "outline") -> dict[str, Any]:
    p = doc_path(db, task_id, kind)
    md = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
    return {"path": str(p), "exists": p.is_file(), "markdown": md, "chars": len(md),
            "sections": len(re.findall(r"(?m)^## ", md)), "subs": len(re.findall(r"(?m)^### ", md))}


# ---- 梳理精读（第 5 步）：大纲每个小标题 → 论文精读结论 / 实验数据 的谱系图 ----
MAP_FILE = "谱系.json"
MAP_TYPES = ("section", "paper", "note", "data")


def map_path(db: "WorkflowDB", task_id: str) -> Path:
    """一份 JSON，落在 <项目>/论文正文/谱系.json；没设项目位置就按项目 id 分名。"""
    base = db.base_dir(task_id)
    return (base / "论文正文" / MAP_FILE) if base else (OUT / f"谱系_{task_id[:8]}.json")


def _note_brief(db: "WorkflowDB", task_id: str, ref: str) -> str:
    """笔记节点「连线后面那行字」：取笔记正文最前面两条实质要点（照抄，不改写、没有就空着）。

    精读笔记本身会被重读 / 补写，所以这行字不存死在谱系.json 里，每次读图都按文件现取。
    """
    stem = Path(str(ref)).stem
    base = db.base_dir(task_id)
    for root in ([base / "笔记"] if base else []) + [NOTES]:
        if not root.is_dir():
            continue
        try:
            f = next((p for p in root.rglob(stem + ".md")), None)
        except OSError:
            f = None
        if not f:
            continue
        lines: list[str] = []
        for raw in f.read_text(encoding="utf-8", errors="replace").splitlines():
            t = raw.strip()
            if not t or t.startswith("#") or t.upper().startswith("**PAGE"):
                continue
            if re.search(r"本文标题|DOI|开放获取|许可|收稿|收到|接受日期|作者包括", t):
                continue                           # 出版信息不是精读结论，跳过
            lines.append(re.sub(r"^[-*+]\s*", "", t))
            if len(lines) == 2:
                break
        return ("；".join(lines))[:200]
    return ""


def map_read(db: "WorkflowDB", task_id: str) -> dict[str, Any]:
    p = map_path(db, task_id)
    raw: dict[str, Any] = {"nodes": [], "edges": []}
    broken = False
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8", errors="replace") or "{}") or {}
        except Exception:
            raw, broken = {"nodes": [], "edges": []}, True
    nodes = [n for n in (raw.get("nodes") or []) if isinstance(n, dict) and n.get("id") and n.get("label")]
    ids = {n["id"] for n in nodes}
    edges = [e for e in (raw.get("edges") or [])
             if isinstance(e, dict) and e.get("from") in ids and e.get("to") in ids]
    linked = [n for n in nodes if n.get("type") == "note" and n.get("ref")]
    if linked:                                     # 笔记改了名 / 重读了 → 图上那行字跟着更新
        titles = {x["id"]: (x.get("title") or "") for x in db.notes(task_id)}
        for n in linked:
            stem = Path(str(n["ref"])).stem
            if titles.get(stem):
                n["label"] = titles[stem]
            brief = _note_brief(db, task_id, n["ref"])
            if brief:
                n["detail"] = brief
    return {"path": str(p), "exists": p.is_file(), "broken": broken, "nodes": nodes, "edges": edges,
            "updated": raw.get("updated") or "",
            "sections": sum(1 for n in nodes if n.get("type") == "section"),
            "refs": sum(1 for n in nodes if n.get("type") != "section")}


def map_write(db: "WorkflowDB", task_id: str, data: Any, keep_positions: bool = True) -> dict[str, Any]:
    """整份覆盖写（和 doc_write 同口径）。不合规的节点/边直接丢掉，不报错 —— 模型给脏数据也要能落盘。

    keep_positions=True（agent 重写时）保留磁盘上已有的手动坐标；前端「全部重排」要清坐标，
    所以它传 False —— 否则用户按了重排，坐标又被服务端补回来，等于没重排。
    """
    if isinstance(data, str):
        data = json.loads(data or "{}")
    if not isinstance(data, dict):
        raise ValueError("谱系要给 {nodes:[…], edges:[…]} 结构的 JSON")
    nodes, seen = [], set()
    _dragged_positions = {} if not keep_positions else {
        n["id"]: {k: n[k] for k in ("x", "y") if k in n}
        for n in map_read(db, task_id)["nodes"] if "x" in n and "y" in n}
    for n in (data.get("nodes") or []):
        if not isinstance(n, dict) or not n.get("id") or not n.get("label"):
            continue
        nid = str(n["id"])
        if nid in seen:
            continue
        seen.add(nid)
        t = str(n.get("type") or "note")
        node = {"id": nid, "type": t if t in MAP_TYPES else "note",
                "label": str(n["label"])[:120], "detail": str(n.get("detail") or "")[:600],
                "ref": str(n.get("ref") or "")[:300]}
        if n.get("mine"):                            # 他自己用 ＋ 加的节点：来源标记，重新整理时不许动它
            node["mine"] = True
        for k in ("x", "y"):                       # 手动拖过的位置
            try:
                node[k] = round(float(n.get(k)), 1)
            except (TypeError, ValueError):
                continue
        if "x" not in node or "y" not in node:
            keep = _dragged_positions.get(nid, {})   # 模型重写整份 JSON 时没带坐标 → 沿用用户拖过的位置
            if keep:
                node.update(keep)
        nodes.append(node)
    edges = [{"from": str(e["from"]), "to": str(e["to"]), "label": str(e.get("label") or "")[:40]}
             for e in (data.get("edges") or [])
             if isinstance(e, dict) and str(e.get("from")) in seen and str(e.get("to")) in seen]
    p = map_path(db, task_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    fileguard.before_write(p, "谱系图覆盖")
    content = json.dumps({"nodes": nodes, "edges": edges, "updated": now()},
                         ensure_ascii=False, indent=1)
    import asset_service
    asset_service.atomic_write_text(p, content)
    asset_service.register_file(db, p, project_id=task_id, kind="document", title=MAP_FILE,
                                source="workflow", body=content, metadata={"document_kind": "map"})
    return map_read(db, task_id)


_NUM_RE = re.compile(r"^[\s　]*(?:[（(]?(\d+(?:[.．]\d+)*)[)）]?[.．、]?|([一二三四五六七八九十]+)[、.．)）])")


def _num_key(s: str) -> str:
    """行首序号本身（1 / 1.1 / 一）。认框架先认序号：他会手改小标题序号，
    而「实验方法」这类同名小标题在不同章里很常见 —— 只按文字认会认错人、把挂好的笔记串到别的节。"""
    m = _NUM_RE.match(str(s or ""))
    if not m:
        return ""
    return (m.group(1) or m.group(2)).replace("．", ".")


def _norm_title(s: str) -> str:
    """去掉行首序号和所有空白，只剩标题文字（序号认不出来时用它兜底）。"""
    t = re.sub(r"^[\s　]*(?:[（(]?\d+(?:[.．]\d+)*[)）]?[.．、]?|[一二三四五六七八九十]+[、.．)）])", "", str(s or ""))
    return re.sub(r"[\s　]+", "", t)


def map_tidy(db: "WorkflowDB", task_id: str) -> dict[str, Any]:
    """「重新整理」= 按大纲**当前的**大标题 / 小标题顺序重认一遍框架。

    他把 2.1 和 1.1 调换、手改序号、改标题、加减小节之后点一下，这张图就该是新框架：
    顺位和层级照大纲，节点身份**先认序号、再认标题文字**（序号没变的、哪怕标题改了也还是同一个节点；
    序号认不出才按文字找），挂着的精读笔记跟着走；大纲里已经没有的小节连它挂的东西一起摘掉；
    手动坐标全清，画布按新层级重排。节点标题一律照抄大纲 —— 他改成什么序号，图上就是什么序号。
    """
    md = doc_read(db, task_id, "outline")["markdown"]
    cur = map_read(db, task_id)
    # mine=true 的节点是他自己用 ＋ 加的：大纲里没有它、也不是 agent 挂的资料 —— 重新整理只能重排大纲那部分，
    # 他的手写内容（连坐标、连边）一律原样留着，否则「按大纲重认」就等于删他的字。
    mine = [dict(n) for n in cur["nodes"] if n.get("mine")]
    mine_ids = {n["id"] for n in mine}
    olds = [n for n in cur["nodes"] if n.get("type") == "section" and n["id"] not in mine_ids]
    refs = [dict(n) for n in cur["nodes"] if n.get("type") != "section" and n["id"] not in mine_ids]
    old_order = [n["id"] for n in olds]
    by_num: dict[str, dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}
    for n in olds:
        k = _num_key(n["label"])
        if k:
            by_num.setdefault(k, n)
        by_title.setdefault(_norm_title(n["label"]), n)
    taken = {n["id"] for n in olds} | {n["id"] for n in refs}
    used: set[str] = set()

    def fresh(prefix: str) -> str:
        i = len(taken) + 1
        while f"{prefix}{i}" in taken:
            i += 1
        taken.add(f"{prefix}{i}")
        return f"{prefix}{i}"

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    chapter = ""
    for line in md.splitlines():
        m = re.match(r"^(#{2,3})\s+(\S.{0,80})$", line)
        if not m:
            continue
        level, title = len(m.group(1)), m.group(2).strip()
        old = by_num.get(_num_key(title)) or by_title.get(_norm_title(title))
        if old is not None and old["id"] in used:
            old = None
        nid = old["id"] if old else fresh("c" if level == 2 else "s")
        used.add(nid)
        nodes.append({"id": nid, "type": "section", "label": title,
                      "detail": (old or {}).get("detail") or ""})
        if level == 2:
            if chapter:
                edges.append({"from": chapter, "to": nid, "label": "下一章"})
            chapter = nid
        elif chapter:
            edges.append({"from": chapter, "to": nid, "label": "包含"})
    if not nodes:
        out = dict(cur)
        out["changed"] = ["大纲还是空的 —— 没有可整理的框架"]
        return out

    sec_ids = {n["id"] for n in nodes}
    ref_ids = {n["id"] for n in refs}
    for e in cur["edges"]:                         # 挂着的资料跟着小节走；小节被删了 → 这条也没处挂了
        if e.get("from") in sec_ids and e.get("to") in ref_ids:
            edges.append({"from": e["from"], "to": e["to"], "label": e.get("label") or "支撑"})
    hung = {e["to"] for e in edges}
    refs = [n for n in refs if n["id"] in hung]
    for n in refs:
        n.pop("x", None)
        n.pop("y", None)
        nodes.append(n)

    lab = {n["id"]: n["label"] for n in nodes}
    new_order = [n["id"] for n in nodes if n.get("type") == "section"]
    kept_old = [i for i in old_order if i in sec_ids]
    kept_new = [i for i in new_order if i in old_order]
    changed: list[str] = []
    added = [lab[i] for i in new_order if i not in old_order]
    dropped = [n["label"] for n in olds if n["id"] not in sec_ids]
    if added:
        changed.append(f"按大纲新增 {len(added)} 节：{'、'.join(added[:6])}")
    if dropped:
        changed.append(f"大纲里已经删掉 {len(dropped)} 节：{'、'.join(dropped[:6])}（连它挂的资料一起摘了）")
    moved = [(i, kept_old.index(i), kept_new.index(i)) for i in kept_new]
    moved = [(i, a, b) for i, a, b in moved if a != b]
    if moved:
        detail = "、".join(f"「{lab[i]}」第 {a + 1} → 第 {b + 1}" for i, a, b in moved[:5])
        changed.append(f"顺序按大纲重排：{detail}" + (f" 等 {len(moved)} 处" if len(moved) > 5 else ""))

    if mine:                                     # 手建节点排在最后追加：不参与上面的顺序比对，坐标和边原样带走
        have = {n["id"] for n in nodes}
        for m in mine:
            if m["id"] not in have:
                nodes.append(m)
                have.add(m["id"])
        seen_edge = {(e["from"], e["to"]) for e in edges}
        for e in cur["edges"]:
            k = (e.get("from"), e.get("to"))
            if (k[0] in mine_ids or k[1] in mine_ids) and k not in seen_edge and k[0] in have and k[1] in have:
                edges.append({"from": k[0], "to": k[1], "label": e.get("label") or ""})
                seen_edge.add(k)
        changed.append(f"手建的 {len(mine)} 个节点原样留着")

    out = map_write(db, task_id, {"nodes": nodes, "edges": edges}, keep_positions=False)
    out["changed"] = changed
    attached = {Path(str(n.get("ref") or "")).stem for n in out["nodes"] if n.get("ref")}
    notes = map_sources(db, task_id)["notes"]
    pick = [x for x in notes if "精读" in x["name"] or "阅读" in x["name"]] or notes
    out["unattached"] = list(dict.fromkeys(x["title"] for x in pick if x["id"] not in attached))
    return out


WRITE_OPENING = ("谱系图我已经看过了。你还有什么要修改的吗？撰写上有什么特殊要求吗"
                 "（投哪里 / 中文还是英文 / 篇幅 / 图表怎么安排），或者有可以参照的语料吗？")


def open_write(db: "WorkflowDB", task_id: str) -> dict[str, Any]:
    """谱系图交付 → 下一步「文章撰写」接手：给写作对话留一条开场白（只留一次）。"""
    for m in db.messages(task_id, "write"):
        if (m.get("content") or "").startswith("谱系图我已经看过了"):
            return {"opened": False}
    db.add_message(task_id, "assistant", WRITE_OPENING, scope="write", sender="写作")
    return {"opened": True, "message": WRITE_OPENING}


def map_seed(db: "WorkflowDB", task_id: str) -> dict[str, Any]:
    """大纲已经有了、谱系图还没有 → 直接按大纲长出「章 → 节」两级，不用等用户开口。

    只搬标题、不编内容：detail 留空，支撑结论 / 数据由助手后面挂上去。
    已经有图就原样返回，绝不覆盖。
    """
    cur = map_read(db, task_id)
    if cur["nodes"]:
        return cur
    md = doc_read(db, task_id, "outline")["markdown"]
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    chapter = ""
    for line in md.splitlines():
        m3 = re.match(r"^###\s+(\S.{0,80})$", line)
        if m3:
            sid = "s%d" % (len(nodes) + 1)
            nodes.append({"id": sid, "type": "section", "label": m3.group(1).strip(), "detail": ""})
            if chapter:
                edges.append({"from": chapter, "to": sid, "label": "包含"})
            continue
        m2 = re.match(r"^##\s+(\S.{0,80})$", line)
        if m2:
            cid = "c%d" % (len(nodes) + 1)
            nodes.append({"id": cid, "type": "section", "label": m2.group(1).strip(), "detail": ""})
            if chapter:
                edges.append({"from": chapter, "to": cid, "label": "下一章"})
            chapter = cid
    if not nodes:
        return cur                                  # 大纲还是空的，别造一张空图
    return map_write(db, task_id, {"nodes": nodes, "edges": edges})


def project_outs(db: "WorkflowDB", task_id: str, limit: int = 40) -> list[dict[str, Any]]:
    """项目里现在有哪些产出文件（论文正文 / 最终产物 / 其余 / 笔记），新的在前。

    右栏那个「产出目录」只是展示：不靠这一轮事件里有 tool_result.path 才有东西，
    落盘过的文件本来就该一直列在那里。
    """
    base = db.base_dir(task_id)
    roots = [base / "论文正文", base / "最终产物", base / "其余", base / "笔记"] if base else [OUT, NOTES]
    rows: list[tuple[float, str, str]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for f in root.rglob("*"):
            if not f.is_file() or f.name.lower() == "readme.md":
                continue
            try:
                rel = f.relative_to(base if base else root).as_posix()
                rows.append((f.stat().st_mtime, rel, f.suffix.lower()))
            except OSError:
                continue
    rows.sort(reverse=True)
    return [{"path": rel, "ext": ext} for _, rel, ext in rows[:limit]]


def map_sources(db: "WorkflowDB", task_id: str) -> dict[str, Any]:
    """谱系图能挂哪些东西：大纲小标题 / 精读笔记 / 文献库 PDF / 实验数据（项目其余/数据 + 上传区）。

    笔记给「标题 + id」而不是文件名：文件名是哈希，助手只能挂出一串乱码，
    读的人也不知道挂的是哪一篇。
    """
    md = doc_read(db, task_id, "outline")["markdown"]
    secs = re.findall(r"(?m)^###\s+(.+)$", md) or re.findall(r"(?m)^##\s+(.+)$", md)
    base = db.base_dir(task_id)
    def ls(d: Path, pats=("*",)):
        out = []
        if d.is_dir():
            for f in sorted(d.rglob("*")):
                if f.is_file() and any(f.match(x) for x in pats):
                    out.append(f.relative_to(d).as_posix())
        return out[:120]
    titles = {n["id"]: (n["title"] or "") for n in db.notes(task_id)}
    note_files = ls(base / "笔记") if base else ls(NOTES, ("*.md",))
    notes = [{"id": Path(f).stem, "name": f, "title": titles.get(Path(f).stem) or Path(f).name}
             for f in note_files
             if Path(f).suffix.lower() == ".md" and Path(f).name.lower() != "readme.md"]
    papers = [p["filename"] for p in db.papers_of(task_id)]
    data = (ls(base / "其余" / "数据") if base else []) + ls(DATA / "uploads")
    return {"sections": [x.strip() for x in secs][:60], "notes": notes, "papers": papers[:60],
            "data": list(dict.fromkeys(data))[:60], "map_path": str(map_path(db, task_id))}


def doc_write(db: "WorkflowDB", task_id: str, kind: str, markdown: str) -> dict[str, Any]:
    """就地覆盖写（他要的就是「修改调整都直接在原文件改」）。

    空内容一律拒写：模型偶尔发一个丢参数的工具调用（args 是空的），照写就把整份正文清成 1 字节
    —— 实测真发生过，左边展示区于是永远是空的，模型还回一句「已写好文件」。
    """
    if not (markdown or "").strip():
        raise ValueError("markdown 不能为空：这是整份覆盖写，空内容会把文件清空（已拒写）。要给完整正文。")
    p = doc_path(db, task_id, kind)
    p.parent.mkdir(parents=True, exist_ok=True)
    fileguard.before_write(p, f"{kind}正文覆盖")
    content = (markdown or "").strip() + "\n"
    import asset_service
    asset_service.atomic_write_text(p, content)
    asset_service.register_file(db, p, project_id=task_id, kind="document", title=(DOC_STEPS.get(kind) or DOC_STEPS["outline"])["file"],
                                source="workflow", body=content, metadata={"document_kind": kind})
    out = doc_read(db, task_id, kind)
    out["written"] = True
    return out




def outline_read(db: "WorkflowDB", task_id: str) -> dict[str, Any]:
    return doc_read(db, task_id, "outline")


def outline_write(db: "WorkflowDB", task_id: str, markdown: str) -> dict[str, Any]:
    return doc_write(db, task_id, "outline", markdown)


DRAFT_TEMPLATE = """# <题目>

## 摘要
（待写：三到五句，问题—方法—结果—意义）

## 1. 引言
（待写）

## 2. 方法
（待写）

## 3. 结果与讨论
（待写）

## 4. 结论
（待写）
"""


def doc_seed(db: "WorkflowDB", task_id: str, kind: str = "outline") -> dict[str, Any]:
    """起稿：大纲用固定模板；正文**按已确认的框架**生成骨架（标题层级照抄，正文留「待写」）。"""
    cur = doc_read(db, task_id, kind)
    if cur["exists"] and cur["markdown"].strip():
        return cur                                       # 已经有内容就别覆盖
    if kind == "outline":
        return doc_write(db, task_id, kind, OUTLINE_TEMPLATE)
    if kind == "write":
        out = doc_read(db, task_id, "outline")
        if not out["markdown"].strip():
            return doc_write(db, task_id, kind, DRAFT_TEMPLATE)
        body = []
        for line in out["markdown"].splitlines():
            if re.match(r"^#{1,3}\s+\S", line):
                body.append(line)
                body.append("（待写）")
                body.append("")
            elif line.strip().startswith("- 说明："):
                body.append("（按框架：%s）" % line.strip()[len("- 说明："):])
                body.append("")
        return doc_write(db, task_id, kind, "\n".join(body) or DRAFT_TEMPLATE)
    return cur


READ_MAX = 12000                                   # 附件一次最多读多少字（够 agent 判断，不炸上下文）


def read_attachment(path: str, chars: int = 8000) -> dict[str, Any]:
    """读用户上传/项目里的文件：PDF 用 pdfio 抓正文，其余按文本读。
    白名单不在这里再写一遍 —— 统一走 paths.resolve_allowed()（data/ + 用户建过的项目目录）。"""
    p = paths.resolve_allowed(path, must_be_file=True, what="附件")
    cap = max(500, min(int(chars or 8000), READ_MAX))
    if p.suffix.lower() == ".pdf":
        text = pdfio.text(p)
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
    return {"path": str(p), "chars": len(text), "text": text[:cap], "truncated": len(text) > cap}



def doc_tools(kind: str) -> list[dict[str, Any]]:
    d = DOC_STEPS.get(kind) or DOC_STEPS["outline"]
    return [
        _schema("doc_write", f"把**整份**{d['label']} markdown 覆盖写进项目里那一份 {d['file']}（就地改，不新建文件）。"
                             f"用户说「更新{d['label']}」「按这个改」时调用它，参数给完整全文。"
                             f"格式要求：{d['hint']}",
                {"markdown": {"type": "string", "description": f"完整 {d['label']} markdown 全文"}}, ["markdown"]),
        _schema("doc_read", f"读回当前{d['label']}文件（一般不用，system 里已经带了最新全文）。", {}, []),
        _schema("cite_add", "登记一条引用：正文里**不要**写 [1]/[2] 这类标号，凡是引用文献都用这个工具把"
                            "「位置 + 文献信息」记进后台的引用台账（题目/作者/出处/DOI/页数/引用页）。",
                {"section": {"type": "string", "description": "引用落在哪一节，例如「2.2 模型与流程」"},
                 "anchor": {"type": "string", "description": "正文里那句被引用的原话（照抄，用来对锚点）"},
                 "title": {"type": "string", "description": "文献题目"},
                 "authors": {"type": "string", "description": "作者，例如「Smith J., Li W. 等」"},
                 "venue": {"type": "string", "description": "期刊/会议名"},
                 "year": {"type": "string", "description": "年份"},
                 "doi": {"type": "string", "description": "DOI"},
                 "pages": {"type": "string", "description": "页码范围，例如「1-12」"},
                 "cite_page": {"type": "string", "description": "引用具体在第几页"},
                 "url": {"type": "string", "description": "链接（DOI 链接或 OA 链接）"},
                 "why": {"type": "string", "description": "为什么引它（一句话）"},
                 "paper_id": {"type": "string", "description": "如果这篇在项目文献库里，给 paper_id"}},
                ["section", "title"]),
        _schema("cite_bib", "把引用台账导成项目里的 文献/引用台账.bib（家格式）。元数据一律从 Zotero 本机库取，"
                            "Zotero 里没有的条目会原样报回来（不要自己补题录）。出 Word/PDF、交稿前调一次。",
                {}, []),
        _schema("read_attachment", "读用户上传或项目里的文件：PDF 抓正文、文本直接读。用户在消息里给了文件路径时用它。",
                {"path": {"type": "string", "description": "文件绝对路径（附件行里给出的那种）"},
                 "chars": {"type": "integer", "description": "最多读多少字，默认 8000"}}, ["path"]),
        _schema("library_overview", "看项目现状：文献数、证据候选、检索结果、已入库 PDF、笔记。", {}, []),
        _schema("knowledge_query", "在项目知识库（笔记 + 证据）里检索关键词。",
                {"query": {"type": "string", "description": "关键词"}}, ["query"]),
        _schema("memory", "把值得长期记住的结论/约束/导师要求记一条（add）。做法与流程不属于这里。",
                {"ops": {"type": "array", "description": "批量，一条也行", "items": {
                    "type": "object",
                    "properties": {"action": {"type": "string", "enum": ["add", "patch", "remove"]},
                                   "text": {"type": "string", "description": "一句话"},
                                   "old_text": {"type": "string", "description": "patch/remove：原文片段"},
                                   "kind": {"type": "string", "enum": ["fact", "constraint", "preference", "decision"]},
                                   "importance": {"type": "integer", "description": "0-3"}},
                    "required": ["action"]}}}, ["ops"]),
    ]


OUTLINE_TOOLS = doc_tools("outline")


def map_tools() -> list[dict[str, Any]]:
    """梳理精读这一步的工具：读写谱系 + 看数据源 + 附件/项目速览/检索/记忆（沿用 doc_tools 里那几个）。"""
    keep = {"read_attachment", "library_overview", "knowledge_query", "memory"}
    return [t for t in doc_tools("outline") if t["function"]["name"] in keep] + [
        _schema("map_write", "把**整份**谱系图 JSON 覆盖写进项目那一份 谱系.json（就地改，不新建）。"
                             "结构：{nodes:[{id,type,label,detail,ref}], edges:[{from,to,label}]}；"
                             "type = section（大纲小标题）/ paper（论文精读结论）/ note（笔记）/ data（实验数据）；"
                             "edges 的 from/to 必须都是已有节点 id。用户说「更新谱系」「按这个改」时就调它，给完整 JSON。",
                {"data": {"type": "string", "description": "完整 JSON 字符串（nodes + edges）"}}, ["data"]),
        _schema("map_read", "读回当前谱系图（一般不用，system 里已经带了最新那份）。", {}, []),
        _schema("map_sources", "看这一步能挂什么：大纲小标题、项目里的精读笔记、文献库 PDF、实验数据文件。",
                {}, []),
    ]


def outline_system(db: "WorkflowDB", task_id: str) -> str:
    """框架 agent 的 system：只做框架 + 固定格式 + 项目记忆 + 当前框架全文。"""
    task = db.summary(task_id)["task"]
    cur = doc_read(db, task_id, "outline")
    body = cur["markdown"][:6000] or "（还没有，等你给第一版）"
    return f"""你是「览籍智研」里「确认大纲」这一步的框架 agent，用中文工作，站在合作者（懂科研、但听用户拍板）的位置。

硬规则：
1. 这个对话 100% 绑定项目《{task['title']}》，你只做一件事：把这份研究的**框架（大纲）**跟用户敲定。不做文献检索/下载/精读/撰写成稿/格式转换，用户要这些就说明去别的步骤。
2. 格式必须照这个骨架（这是这一步的产出规范，别自创层级）：
   - 大标题用 `## `（例如 `## 2. 方法`）
   - 小标题用 `### `（例如 `### 2.1 数据与体系`）
   - **每个小标题下面紧跟一行** `- 说明：<一句话讲清这一块要写什么>`
3. 默认多轮：一次只问 1-3 个最关键的问题（题目/目标期刊或学位要求/已有数据与结论/导师的硬要求），别一口气拷问。
4. 用户说「更新框架」、或你们已经敲定了改动时，**必须调用 doc_write** 写**整份** markdown（覆盖同一个文件，不要新建、不要只给片段），写完用一两句说明改了什么。
5. 不编造数据与文献；拿不准的写「待核实」。你是草稿式建议，最终以导师与证据为准。
6. 用户上传的文件会以「[附件] … 路径」出现在消息里，需要内容就调 read_attachment（PDF 会自动抓正文）。
7. 框架文件只有这一份：`{cur['path']}`（{'已存在' if cur['exists'] else '还没有'}）。

【当前框架】
{body}

【项目记忆】
%%MEMORY%%"""


MAP_FALLBACK = ("把「大纲每个小标题」和「项目里的精读笔记 / 实验数据」连成一张谱系图：每一节挂上支撑它的论文结论"
                "（写清是哪篇、什么结论、支持还是反驳），没有出处的写「待补」，不要编。")


def map_system(db: "WorkflowDB", task_id: str) -> str:
    task = db.summary(task_id)["task"]
    out = doc_read(db, task_id, "outline")
    cur = map_read(db, task_id)
    src = map_sources(db, task_id)
    skills = skill_text("research", "paper-reading-reports", "SKILL.md", cap=2500, db=db, task_id=task_id) or MAP_FALLBACK
    return f"""你是「览籍智研」里「梳理精读」这一步的**结构整理 agent**，用中文工作。

硬规则：
1. 这个对话 100% 绑定项目《{task['title']}》，你只做一件事：**维护一张谱系图**（思维导图），把大纲的每个小标题和「支撑它的精读结论 / 实验数据」连起来。不做检索、下载、翻译、写作。
2. 图存在 `{cur['path']}`（{'已有图' if cur['exists'] else '还没有图'}）—— 结构：
   {{"nodes":[{{"id":"s1","type":"section","label":"2.1 掺杂元素筛选","detail":""}},
             {{"id":"p1","type":"paper","label":"Left 2025","detail":"Cu 掺杂使带隙…","ref":"<笔记文件名或 paper_id>"}},
             {{"id":"d1","type":"data","label":"XRD-2026-10.xlsx","detail":"主峰右移 0.2°","ref":"其余/数据/…"}}],
    "edges":[{{"from":"s1","to":"p1","label":"支撑"}}]}}
   type 只认 section / paper / note / data；edges 的 from/to 必须是已有节点 id。
3. 用户说「更新谱系」「把 X 挂到 Y 下面」、或你们敲定改动时，**必须调用 map_write** 写**整份** JSON（覆盖同一份，不要只给片段、不要新建文件）。
4. **必须有出处**：paper 节点的 detail 要写清是哪篇/哪条结论，data 节点要写清是哪个文件里的什么现象；**没有出处的节点不要画**，缺就写「待补：<还差什么>」。
5. 用户上传的文件以「[附件] … 路径」出现，读它用 read_attachment。要挂实验数据先调 map_sources 看现成的文件清单。
6. **能画就直接画**：用户给了材料就直接 map_write 落盘，然后说清这轮加了/改了哪几个节点；不要先反问一串。

【本机精读规范（节点的 detail 按这个写）】
{skills}

【大纲（谱系图的骨架来源）】
{out['markdown'][:5000] or '（还没有大纲 —— 提醒用户先去「确认大纲」）'}

【可以挂上来的东西】
- 大纲小标题：{'; '.join(src['sections']) or '（无）'}
- 精读笔记：{'; '.join(f"{x['title']}（{x['name']}）" for x in src['notes'][:20]) or '（无）'}
- 文献库 PDF：{'; '.join(src['papers'][:20]) or '（无）'}
- 实验数据：{'; '.join(src['data'][:20]) or '（无）'}

【当前谱系图】
{json.dumps({'nodes': cur['nodes'], 'edges': cur['edges']}, ensure_ascii=False)[:6000] or '（还没有）'}

【项目记忆】
%%MEMORY%%"""


# ---- 论文撰写：按「上一步敲定的框架」+ 本机写作工作流（paper-spine / paper-writer）写正文 ----
WRITE_FALLBACK = ("按框架逐节展开成正式论文正文：每节先给要写的内容，再落到句子；"
                  "缺数据的地方写「待补」，不编造数字与文献；术语前后一致。")


def write_system(db: "WorkflowDB", task_id: str, draft: bool = False) -> str:
    """写作 agent 的 system。`draft=True` 是「按谱系图整篇起稿」那一趟：那一版不留「待补」占位
    —— 没挂证据的节照框架做定性论述，绝不写「待补：…」让用户对着空壳改。"""
    from workflow_converters import fig_list        # 反向 import 会成环，用时再拿
    from workflow_converters import cite_sync
    task = db.summary(task_id)["task"]
    out = doc_read(db, task_id, "outline")
    cur = doc_read(db, task_id, "write")
    cite = cite_sync(db, task_id)                    # 每次进这一步都按当前正文重算台账状态
    figs = fig_list(db, task_id)["files"]        # 「图」文件夹：正文的图位只能从此处挑
    fig_txt = "\n".join(f"- {f['name']}（{f['ext'].upper()}，{max(1, f['size'] // 1024)} KB）" for f in figs[:40]) \
        or "（图文件夹还是空的 —— 让他先把成图放进项目的「图」文件夹；没有的就写「待补图」）"

    skills = "\n\n".join(x for x in (
        skill_text("academic-writing", "paper-spine", "SKILL.md", cap=4000, db=db, task_id=task_id),
        skill_text("paper-writer", "SKILL.md", cap=3500, db=db, task_id=task_id),
    ) if x) or WRITE_FALLBACK
    return f"""你是「览籍智研」里「论文撰写」这一步的写作 agent，用中文工作（用户要英文稿就写英文），站在一作+导师之间的位置。

硬规则：
1. 这个对话 100% 绑定项目《{task['title']}》，你只做一件事：**按已确认的框架把正文写出来/改好**。不做检索、下载、精读、格式转换。
2. **必须以上一步敲定的框架为骨架**（标题层级、每节要写什么，照它来；要动结构先说清为什么，用户同意再动框架文件）。
3. {('缺数据、缺文献的地方**做定性论述、用限定语**（在本文条件下 / 就现有报道看），正文里**不出现「待补」这类占位**；数字与文献一律不编造' if draft else '缺数据、缺文献、缺结论的地方，写「待补：<还差什么>」，**绝不编造**')}。数字只能来自用户给的材料或项目库。
4. 用户说「更新正文」「写进文件」、或你们敲定了改动时，**必须调用 doc_write** 写**整份** markdown（覆盖同一个文件，不要新建、不要只给片段）；可以先写一节让他看，但落盘永远是整份。
5. **能写就直接写**：用户让你写某一节、或说「按框架写」，就直接把那一节写出来（300-800 字量级，学术书面语），{('缺的东西做定性论述、写满写实' if draft else '缺的东西写「待补：<还差什么>」')}——**不要先反问一串再等**；确实缺到没法动笔（比如完全不知道研究对象）才问 1 个问题。写完落盘并说清改了哪一节。
6. 用户上传的文件会以「[附件] … 路径」出现，需要内容就调 read_attachment（PDF 自动抓正文）。
7. 正文文件：`{cur['path']}`（{'已有正文' if cur['exists'] else '还没有正文'}）。
7.1 **左栏就是这份正文**：用户说「写在左边」「在左边展示正文」「生成到左边」——都是叫你把整份正文调 doc_write 落盘，写完左边立刻换成新内容；不要在聊天里贴全文当交付。工具调用要带完整 `markdown` 参数，空参数会被服务端拒写。
7.2 **图位与图题**：凡是要配图的地方，**另起一行单独写**（不要塞进句子里）：`[图位 1] 图1 <一句话图题> | 文件：<图文件名>`。序号按全文出现顺序；文件名只能用下面【图文件夹】里有的，没有对应图就写 `[图位 N] 待补图：<该画什么> | 文件：待补`；图题自己拟、说清这张图讲什么、什么条件，别写「如图所示」这种空占位。
8. **引用不要写进正文**：正文里**不出现** `[1]` `[2]` 这类标号（也不要写「(Smith et al., 2024)」这种行内引用）；需要引用文献时，调 **cite_add** 把「这一处落在哪一节 + 被引用的那句原话（照抄，用来对锚点）+ 文献题目/作者/出处/年/DOI/页数/引用页」记进后台的引用台账。正文保持流畅通顺，引用关系由台账负责。台账里**文献信息一律来自 Zotero**（搜到哪条就引哪条，别照 PDF 排版抄）；出 Word/PDF 或交稿前调 **cite_bib** 生成 `文献/引用台账.bib`，它会把 Zotero 里查不到的条目报回来——这些要先导进 Zotero，不许自己补题录。

【本机写作工作流（按它的规范写）】
{skills}

【框架（上一步敲定的，必须遵守）】
{out['markdown'][:6000] or '（还没有框架 —— 提醒用户先去「确认大纲」敲定框架）'}

【图文件夹（项目里的「图」；图位只能引用这里的文件名）】
{fig_txt[:1500]}

【引用台账（后台文档，已有 {cite['count']} 条；正文里不写标号，引用都记这里）】
{(cite['markdown'][:2500] + ('…' if len(cite['markdown']) > 2500 else '')) if cite['count'] else '（还没有引用记录 —— 需要引用文献时用 cite_add 登记）'}

【当前正文】
{cur['markdown'][:6000] or '（还没有，等你第一版）'}

【项目记忆】
%%MEMORY%%"""


# ---- 文章撰写：后台多 agent 流水线（素材官 → 写手 → 审查 → 写手定稿）----
# 口径（2026-10-07）：这一步只要「比较好的论文初稿」，不做 0→出版全链条；agent 在后台协作，前台不加界面。
PIPELINE_WORDS = ("初稿", "整篇", "全文", "全篇", "通篇", "重写", "从头", "都写", "完整", "全部", "初版")

EVIDENCE_FALLBACK = ("把项目里已有的精读笔记与证据原文块整理成写正文能直接用的素材简报：按框架每一节给出"
                     "论断/数据 + 出处（文件名 p.页码），单列缺口；没有出处的素材不许写进简报，缺就写「无出处」。")

CRITIC_FALLBACK = ("以审稿人的位置逐节核对刚写的正文：每个论断有没有素材或引用台账支撑、有没有没出处的数字与文献、"
                   "框架里定的一节是否被漏写或写跑题、术语与符号是否前后一致。只挑能在正文里直接改掉的硬伤。")

EVIDENCE_TOOLS = [
    _schema("library_overview", "看项目现状：文献数、证据候选、检索结果、已入库 PDF、笔记。先调它了解手头有什么。", {}, []),
    _schema("knowledge_query", "在项目知识库（精读笔记 + 证据原文块）里按关键词检索，返回出处与上下文。",
            {"query": {"type": "string", "description": "关键词，例如「掺杂 循环保持率」"}}, ["query"]),
    _schema("read_attachment", "读项目里或用户给的文件：PDF 抓正文、文本直接读。需要原文细节时用它。",
            {"path": {"type": "string", "description": "文件绝对路径"},
             "chars": {"type": "integer", "description": "最多读多少字，默认 8000"}}, ["path"]),
]

CRITIC_TOOLS = [
    _schema("library_overview", "看项目现状，核对写手用的材料是否真的存在。", {}, []),
    _schema("knowledge_query", "在项目知识库（精读笔记 + 证据原文块）里按关键词检索，核对正文里的数字/论断有没有出处。",
            {"query": {"type": "string", "description": "关键词"}}, ["query"]),
]

# 委派能力缝到每个「有 agent 跟人对话」的受限工具表后面（能对话就能往下派活；
for _lst in (EVIDENCE_TOOLS, CRITIC_TOOLS):
    if DELEGATE_TOOL not in _lst:
        _lst.append(DELEGATE_TOOL)




def evidence_system(db: "WorkflowDB", task_id: str) -> str:
    """素材官（后台 agent）：只出素材简报，不写正文、不落盘、不登记台账。"""
    out = doc_read(db, task_id, "outline")
    return f"""你是「览籍智研」里「文章撰写」这一步的**素材官**（后台 agent，用户看不到你的对话，只看到进度）。你在为一篇论文的正文准备**可用素材**。

硬规则：
1. 你只管素材：不写正文、不调用 doc_write、不登记引用台账 —— 你的产出是一份简报，交给后面的写手。
2. 素材只能来自**项目已有材料**：先调 library_overview 看有什么（文献/证据候选/检索结果/笔记），再用 knowledge_query 按每节的关键词检索**精读笔记与证据原文块**（证据带「文件名 p.页码」，是可直接引用的原文），需要更多原文细节时用 read_attachment 读 PDF。**不许**凭记忆写文献、数字、结论。
3. 按下面框架的每一节，一行一条给出素材，格式固定：
   `- [节标题] 一句能写进正文的论断或数据 —— 出处：文件名 p.页码`
   出处必须来自工具返回的内容；确实找不到出处的，写 `—— 出处：无（待核）`，不要编。
4. 最后单列一段「缺口」：哪些节几乎没素材、哪些数字/对比拿不到、哪些结论还没证据。照实写，不许用「需要进一步研究」这类话术凑数。
5. 全文 ≤4000 字，宁少勿虚。只输出简报本身（Markdown），不要客套、不要解释你在做什么。

【框架（上一步敲定，正文必须按它的章节结构走）】
{out['markdown'][:6000] or '（还没有框架 —— 简报里直接写「缺框架，无法按节整理」）'}

【项目记忆】
%%MEMORY%%"""


def critic_system(db: "WorkflowDB", task_id: str, brief: str) -> str:
    """审查 agent（后台）：只挑能在正文里直接改掉的硬伤，输出可执行修改指令；不落盘。"""
    from workflow_converters import cite_sync
    out = doc_read(db, task_id, "outline")
    cur = doc_read(db, task_id, "write")
    cite = cite_sync(db, task_id)

    return f"""你是「览籍智研」里「文章撰写」这一步的**审查 agent**（后台 agent，用户看不到你的对话）。写手刚写完一版正文，你只挑**能在正文里直接改掉的硬伤**。

硬规则：
1. 逐节核对四件事：① 每个论断有没有素材/引用台账支撑；② 有没有**没出处的数字、文献、结论**（编造，最严重）；③ 框架里定的一节是否被漏写或写跑题；④ 术语、符号、单位、材料体系命名是否前后一致。
2. 输出 ≤5 条，按严重度从高到低排，每条格式固定：
   `- [节标题] 问题一句话 → 怎么改（具体到该删什么、该补什么、该把哪句改成什么）`
3. **不要**给「创新性不足」「建议加强讨论」「需要更多实验」这类空话；**不要**输出改好的正文（落盘由写手做，避免两个 agent 抢同一个文件）。
4. 一条硬伤都没有就只输出 `无硬伤`。
5. 需要核对材料是否存在时调 library_overview / knowledge_query；你的判断以项目里真实存在的材料为准。

【框架】
{out['markdown'][:3000] or '（没有框架）'}

【素材官给的素材简报（写手就是拿它写的）】
{brief[:3000] or '（简报为空）'}

【当前正文（要审的就是这一份）】
{cur['markdown'][:8000] or '（正文为空）'}

【引用台账（后台文档，共 {cite['count']} 条；正文里不写标号，引用都记这里）】
{(cite['markdown'][:1500] + '…') if cite['count'] else '（还没有引用记录）'}

【项目记忆】
%%MEMORY%%"""





# ---- 稿件审查：博导 skill 把关，先给大思路上的问题，再落到可改的细节 ----
REVIEW_FALLBACK = ("以严格审稿人的位置读全文：先说**大的思路层面**的问题（贡献是否成立、逻辑链是否闭合、"
                   "证据是否支撑结论、与前人工作的差别），再给可执行的修改建议；没说清的地方标注「需核实」。")


def review_system(db: "WorkflowDB", task_id: str) -> str:
    task = db.summary(task_id)["task"]
    cur = doc_read(db, task_id, "write")
    rep = doc_read(db, task_id, "review")
    skills = "\n\n".join(x for x in (
        skill_text("research", "research-topic-selection", "SKILL.md", cap=1800, db=db, task_id=task_id),
        skill_text("idea-evaluator", "SKILL.md", cap=3500, db=db, task_id=task_id),
        skill_text("manuscript-writing", "SKILL.md", cap=3000, db=db, task_id=task_id),
        skill_text("pre-submission-reviewer", "SKILL.md", cap=2500, db=db, task_id=task_id),
    ) if x) or REVIEW_FALLBACK
    return f"""你是「览籍智研」里「稿件审查」这一步的审查 agent，用中文工作，站在**博导/审稿人**的位置：既严格，也给建设性意见。

硬规则：
1. 这个对话 100% 绑定项目《{task['title']}》，你只做一件事：审这篇文章的正文并帮用户改。
2. **三重视角严格审稿模拟器**：用户说「审一遍」或请求评审时，从三位审稿人视角给出独立打分（1-10分）与犀利评审意见：
   - **Reviewer 1 (严谨派 / 方法论)**：核查实验对照组、显著性检验、基线模型对比以及实验复现可行性；
   - **Reviewer 2 (挑剔派 / 理论贡献与创新度)**：严苛检验主要贡献增量、与前人工作的区分度，是否存在过度声明 (Overclaiming)；
   - **Reviewer 3 (落地派 / 工程与泛化应用)**：关注实验条件苛刻度、跨领域适用性、成本与工业放大限制；
   最后给出综合决议（Accept / Minor Revision / Major Revision / Reject）和逐段可操作的修改建议。没有正文就提醒他先去「论文撰写」。
3. 用户把想改的细节贴在消息里（「[我的标注] …」这种也照此处理）—— 逐条回应，别漏。
4. **产出是审稿意见，不是正文**：把审稿意见整份 markdown 用 doc_write 写进 `{rep['path']}`（左边展示区就是它，写完整份、不要只给片段），**不要覆盖正文**；正文要改就让用户回「论文撰写」那一步改。
4.1 **能写就直接写**：用户说「审一遍」「给点意见」「放到左边」就是让你现在写出整份审查意见并 doc_write 落盘 —— 不要先反问「要不要我写成一份」；落盘后用一两句说清决议和最关键的三条。确实没正文才问。
5. 不编造文献与数据；页码/数字对不上的不当结论。你的意见是草稿式判断，最终以导师与证据为准。
6. 出现值得长期记住的结论/约束/导师要求时调用 memory 记一条（add）；做法留给 skill。
7. 被审的正文：`{cur['path']}`（{'已有正文' if cur['exists'] else '还没有正文 —— 提醒他先去「论文撰写」写一版'}）。

【审查框架（取自本机 skill）】
{skills}

【当前正文（上一步的产出）】
{cur['markdown'][:8000] or '（还没有正文）'}

【项目记忆】
%%MEMORY%%"""

