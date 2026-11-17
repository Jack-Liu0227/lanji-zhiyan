"""Compile the user's graph into manuscript structure and source-bound evidence."""
from hashlib import sha256
import json
from pathlib import Path
import re


SEQUENCE = {"下一章", "下一节"}


def structure(nodes, edges):
    sections = {node["id"]: node for node in nodes if node.get("type") == "section"}
    parent = {}
    order_edges = []
    for edge in edges:
        a, b = edge["from"], edge["to"]
        if a not in sections or b not in sections:
            continue
        if edge.get("label") in SEQUENCE:
            order_edges.append((a, b))
        elif b in parent and parent[b] != a:
            raise ValueError(f"章节「{sections[b]['label']}」有多个上级")
        else:
            parent[b] = a
    for first in sections:
        chain, current = set(), first
        while current in parent:
            if current in chain:
                raise ValueError("章节包含关系形成循环")
            chain.add(current)
            current = parent[current]
    for a, b in order_edges:
        if parent.get(a) != parent.get(b):
            raise ValueError("章节顺序连线只能连接同一上级的章节")
    siblings = {}
    for key in sections:
        siblings.setdefault(parent.get(key), []).append(key)
    result = []

    def visit(owner, depth):
        if depth > 6 and owner in siblings:
            raise ValueError("章节层级超过 Markdown 支持的范围")
        remaining = siblings.get(owner, []).copy()
        while remaining:
            key = next((item for item in remaining
                        if not any(b == item and a in remaining for a, b in order_edges)), None)
            if key is None:
                raise ValueError("章节顺序连线形成循环")
            remaining.remove(key)
            result.append((key, depth, key not in siblings))
            visit(key, depth + 1)

    visit(None, 2)
    return result


def graph_revision(nodes, edges):
    data = [[{key: value for key, value in node.items() if key not in {"x", "y"}} for node in nodes], edges]
    return sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _one_line(text):
    return " ".join(str(text or "").split()).replace("<!--", "‹!--").replace("-->", "--›")


def _source(db, project, node, registered, notes):
    """Resolve exact sources, never substitute an unrelated project resource."""
    ref = node.get("ref") or ""
    kind = node["type"]
    if kind == "evidence":
        from research_core import excerpt_matches
        row = registered.get(ref)
        if row:
            page = db.db.execute("SELECT text FROM paper_pages WHERE paper_id=? AND page_no=?",
                                 (row["paper_id"], row["page_no"])).fetchone()
            if page and excerpt_matches(row["excerpt"], page["text"]):
                return f"{row['filename']} p.{row['page_no']} · 证据 {ref}", row["excerpt"]
        return None
    if kind == "note":
        note = notes.get(ref) or notes.get(Path(ref).stem)
        if note:
            return f"项目笔记 {note['title']} · {note['id']}", note["markdown"]
        root = db.base_dir(project)
        base = (root / "笔记").resolve() if root else None
        path = (base / ref).resolve() if base and ref else None
        if path and path.is_relative_to(base) and path.is_file() and path.suffix.lower() == ".md":
            return f"项目笔记 {path.relative_to(base).as_posix()}", path.read_text(encoding="utf-8")
    if kind == "data":
        import asset_service
        root = db.base_dir(project)
        base = (root / "其余" / "数据").resolve() if root else None
        path = (base / ref).resolve() if base and ref else None
        if path and path.is_relative_to(base) and path.is_file() and node.get("detail"):
            version = asset_service.sha256_file(path)[:12]
            return f"项目数据 {path.relative_to(base).as_posix()} · 版本 {version} · 图中登记的结果", node["detail"]
    return None


def compile_draft(db, project, graph):
    import asset_service
    import note_catalog

    if graph.get("broken"):
        raise ValueError("谱系图无法读取，请先修复并保存")
    sections = structure(graph["nodes"], graph["edges"])
    if not sections:
        raise ValueError("请先打开「梳理精读」，设置谱系图章节并挂上证据")
    nodes = {node["id"]: node for node in graph["nodes"]}
    eligibility = asset_service.eligibility_index(db, project)
    registered = {row["id"]: dict(row) for row in db.evidence(project)
                  if asset_service.legacy_context_available(db, project, "evidence", row["id"], index=eligibility)}
    notes = {note["id"]: note for note in db.notes(project)
             if not note_catalog.is_template(note["markdown"])
             and asset_service.legacy_context_available(db, project, "note", note["id"], index=eligibility)}
    blocks, gaps, sources = [], [], {}
    outgoing = {}
    for edge in graph["edges"]:
        if nodes[edge["to"]]["type"] != "section":
            outgoing.setdefault(edge["from"], []).append(edge)

    def linked(section):
        seen = set()

        def walk(origin, parents, route):
            for edge in outgoing.get(origin, []):
                target = edge["to"]
                if target in parents:
                    raise ValueError("谱系图的证据链形成循环，请修正连线")
                label = _one_line(edge.get("label")) or "关联"
                identity = (origin, target, label)
                if identity in seen:
                    continue
                seen.add(identity)
                path = (*route, label)
                yield nodes[target], " → ".join(path)
                yield from walk(target, parents | {target}, path)

        return walk(section, {section}, ())

    for key, depth, leaf in sections:
        section = nodes[key]
        title = _one_line(section["label"])
        lines = [f"{'#' * depth} {title}"]
        if section.get("detail"):
            lines.append(f"本节论证目标：{_one_line(section['detail'])}")
        attached = list(linked(key))
        if leaf and not attached:
            gaps.append(f"{title}：尚未挂接证据")
            lines.append("**待补证据**。")
        for node, relation in attached:
            label = _one_line(node["label"])
            resolved = _source(db, project, node, registered, notes)
            if not resolved:
                gaps.append(f"{title}：{label} 缺少可追溯来源")
                lines.append(f"**待核来源**：{label}。")
                continue
            locator, original = resolved
            if node["type"] == "note":
                original = "\n".join(line for line in original.splitlines() if line.strip() and not line.startswith("#"))
            excerpt = _one_line(original)
            if not excerpt:
                gaps.append(f"{title}：{label} 的来源没有可用内容")
                lines.append(f"**待补内容**：{label}。")
                continue
            # A note is an excerpt from the note, not a PDF-page quotation.
            clipped = node["type"] == "note" and len(excerpt) > 600
            excerpt = excerpt[:600] + " […]" if clipped else excerpt
            lines.extend((f"**{relation}** · {label}", f"> {excerpt}", f"出处：{_one_line(locator)}"))
            sources.setdefault(key, []).append({"node_id": node["id"], "relation": relation,
                                                 "source": locator, "excerpt": excerpt})
        blocks.append("\n\n".join(lines))
    revision = graph.get("revision") or graph_revision(graph["nodes"], graph["edges"])
    markdown = f"<!-- lineage:{revision} -->\n\n" + "\n\n".join(blocks) + "\n"
    return {"markdown": markdown, "revision": revision, "gaps": gaps, "sources": sources,
            "sections": len(sections)}


def validate_draft(db, project, markdown, *, require_complete=True):
    from workflow_documents import map_read
    result = compile_draft(db, project, map_read(db, project))
    if str(markdown or "").strip() != result["markdown"].strip():
        raise ValueError("正文的章节、证据或谱系版本已变化，请到文章撰写按当前谱系图重新生成")
    if require_complete and result["gaps"]:
        raise ValueError("谱系证据尚未齐全：" + "；".join(result["gaps"][:5]))
    return result


def require_for_manuscript(db, project, markdown):
    from workflow_documents import map_read
    graph = map_read(db, project)
    if graph["exists"] or str(markdown or "").startswith("<!-- lineage:"):
        return validate_draft(db, project, markdown)
    return None
