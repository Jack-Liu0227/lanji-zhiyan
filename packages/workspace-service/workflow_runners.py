# -*- coding: utf-8 -*-
"""工作流各阶段步骤 Agent 运行器与流水线调度。"""
from __future__ import annotations

from typing import Any
from workflow_documents import (
    doc_path, doc_read, doc_write, doc_tools, map_read, map_tools, outline_system,
    map_system, write_system, review_system, PIPELINE_WORDS,
)
from workflow_prompts import IDEA_TOOLS


def run_idea(db: Any, task_id: str, text: str, emit: Any = None) -> dict[str, Any]:
    """创新点审查这一步：受限工具（只看创新点）+ 博导 skill 框架 + 项目长期/短期记忆。"""
    import workflow
    system = workflow.idea_system(db, task_id).replace("%%MEMORY%%", db.memory_block(task_id))
    system += ("\n有候选论文时先用 paper_abstracts 初筛。根据创新主张提出会改变结论的核查问题，"
               "用 paper_focus 查相关原文并登记有效证据。已有证据用 paper_evidence_get 复用。"
               "保存审查时把证据 ID 交给 idea_review；资料不足写明暂定和待核点。")
    mine = (db.idea_input(task_id) or "").strip()          # 用户在上区写的创新点原文：每轮都带上，他改了就按新的看
    extra = ("【用户在上区「我的创新点」里写的原文 —— 以这份为准，他会随时编辑】\n" + mine) if mine else ""
    return workflow.run_agent(db, task_id, text, emit, max_steps=6, system=system, tools=IDEA_TOOLS,
                              scope="idea", extra_context=extra)


def run_map(db: Any, task_id: str, text: str, emit: Any = None) -> dict[str, Any]:
    import workflow
    system = map_system(db, task_id)
    return workflow.run_agent(db, task_id, text, emit, max_steps=8, system=system, tools=map_tools(), scope="map")


def run_outline(db: Any, task_id: str, text: str, emit: Any = None) -> dict[str, Any]:
    import workflow
    system = outline_system(db, task_id)
    return workflow.run_agent(db, task_id, text, emit, max_steps=8, system=system, tools=doc_tools("outline"), scope="outline")


def _wants_draft(text: str, has_body: bool, has_outline: bool) -> bool:
    """要不要整篇起稿重写。已有正文时**只认「整篇/全文/从头」这类说法** ——
    「重写 4.2 这节」是改一节，不是把改好的整篇推倒（两种路子都会覆盖整份文件）。"""
    if not has_outline:
        return False
    if not has_body:
        return True
    t = text or ""
    return any(w in t for w in PIPELINE_WORDS if w != "重写")


def translate_doc(db: Any, task_id: str, style: str = "formal") -> dict[str, Any]:
    """整篇中译英：读 正文.md → 分片并行翻 → 写 正文_en.md。

    这是「翻译」的正确走法：一次工具调用搞定，只把摘要（多少字/多少片/耗时）回给模型；
    逐段手工翻会把 3 万字正文和越来越长的英文稿一轮轮塞进上下文，越翻越慢。
    """
    import workflow
    import workflow_converters as conv
    import asset_service
    import fileguard
    src = doc_path(db, task_id, "write")
    if not src.is_file():
        raise ValueError("还没有正文：先在「论文撰写」把正文写出来")
    md = src.read_text(encoding="utf-8", errors="replace")
    if not md.strip():
        raise ValueError("正文文件是空的")
    from lineage_draft import require_for_manuscript
    require_for_manuscript(db, task_id, md)
    profile = db.active_model()
    if not profile:
        raise RuntimeError("还没配模型：去「模型」页加一个 OpenAI 兼容接口并设为当前模型")
    out = conv.en_translate(profile, md, style=style)
    if not out.get("text", "").strip():
        raise RuntimeError("模型没有返回译文，原文件未改动")
    dest = src.with_name(src.stem + "_en.md")
    fileguard.before_write(dest, "整篇中译英")
    asset_service.atomic_write_text(dest, out["text"] + "\n")
    asset_service.register_file(db, dest, project_id=task_id, kind="document", source="translation",
                                body=out["text"], metadata={"document_kind": "entrans"})
    import service_settings
    service_settings.write(db, "translation:" + task_id, {"result_path": str(dest), "source_text": md,
                           "segments": out.get("segments", []), "custom_source": False})
    db.log_step(task_id, "entrans", "done",
                "整篇译成 %s（%d 字 / %d 片 / %.0f 秒）" % (dest.name, len(out["text"]), out["chunks"], out["ms"] / 1000))
    return {"path": str(dest), "name": dest.name, "src_chars": len(md), "chars": len(out["text"]),
            "chunks": out["chunks"], "ms": out["ms"], "style": style}


def entrans_system(db: Any, task_id: str) -> str:
    """中译英这一步的 system：源就是「论文撰写」那份正文.md，产物是同一目录的 正文_en.md。

    没有这段的时候，这一步的 agent 是通用人格 —— 它不知道源在哪，会自己去 笔记/ 下写
    「译文对照」「FrontMatter-EN」这类零碎文件，正文一个字没翻。这里把源、产物、读法钉死。
    """
    task = db.summary(task_id)["task"]
    src = doc_path(db, task_id, "write")
    chars = 0
    try:
        chars = len(src.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        pass
    dest = src.with_name(src.stem + "_en.md")
    return f"""你是「览籍智研」里「中译英」这一步的翻译专家：产出英文，跟用户用中文说话。

硬规则：
1. **源只有一份**：《{task['title']}》的正文 `{src}`（现在约 {chars} 字）。你只翻它，不要另找文件，也不要自己重新拼一份稿子。
2. **整篇翻就用 `translate_doc`（style=formal/concise/impactful）**：它按节分片、4 路并行，几十秒到两分钟出 `正文_en.md`，不往对话里灌全文 —— 不要自己一段一段手译，那样每轮都要重发整份正文和英文稿，越翻越慢。
   要改某几句/某一段时才自己动手：先 `read_attachment(path="{src}", chars=40000)` 读到那一段再改。
3. **产物是 `{dest}`**（和正文同目录）：用 project_note_write，path 直接给这个绝对路径。markdown 结构、公式、DOI、`[图位 N]` 图位行都原样保留，只把中文换成英文；图表标题一起译。
4. **一段一段来**：每译完一节就落盘一次（覆盖写整份，把已译的接上），不要憋到最后一次 —— 左边/产出目录要能看到它在长。
5. 术语先统一（同一个中文词永远同一个英文词），正文里不出现中文，也不写「TBD/待补」这类占位；拿不准的地方用限定语（to the best of our knowledge）带过去。
6. **不许另建零碎文件**：不要写「译文对照」「FrontMatter-EN」「术语表」这类附属 md。用户要的是一份能直接投的 `{dest}`；他另外点名要什么再单独给。
7. 不编造数据与文献；数字、DOI、单位照原文搬。"""


def draft_from_lineage(db, task_id, *, require_complete=False, replace=True):
    import execution
    import project_tasks
    from lineage_draft import compile_draft

    with project_tasks._lock:
        execution.check()
        result = compile_draft(db, task_id, map_read(db, task_id))
        current = doc_read(db, task_id, "write")
        if result["gaps"] and (require_complete or (current["markdown"].strip()
                                                   and not current["markdown"].startswith("<!-- lineage:"))):
            raise ValueError("谱系证据尚未齐全，现有正文保留：" + "；".join(result["gaps"][:5]))
        written = current["markdown"].strip() != result["markdown"].strip()
        if not replace and current["markdown"].strip() and written:
            return {**result, "path": current["path"], "written": False, "stale": True,
                    "reply": "当前正文与谱系底稿有差异。明确发送「按谱系图重新生成」后会保存新底稿。"}
        if written:
            current = doc_write(db, task_id, "write", result["markdown"], lineage=True)
        reply = f"已按谱系版本 {result['revision'][:12]} 生成 {result['sections']} 个章节。"
        if result["gaps"]:
            reply += "\n待核：" + "；".join(result["gaps"][:8])
        return {**result, "path": current["path"], "written": written, "reply": reply}


def run_write(db: Any, task_id: str, text: str, emit: Any = None) -> dict[str, Any]:
    say = emit or (lambda _event: None)
    import workflow
    permission = (getattr(db, "agent_runtime_snapshot", None) or {}).get("permission", "auto")
    workflow.enforce_tool_policy("draft_write", {}, permission)
    replace = any(phrase in text for phrase in ("按谱系图", "整篇重写", "重新生成证据底稿"))
    result = draft_from_lineage(db, task_id, replace=replace)
    reply = result["reply"]
    events = [{"type": "tool_result", "name": "draft_write", "ok": True, "path": result["path"],
               "summary": reply, "result": {"revision": result["revision"], "gaps": result["gaps"]}}]
    db.add_message(task_id, "user", text, scope="write")
    db.add_message(task_id, "assistant", reply, events, scope="write")
    db.log_step(task_id, "write", "ready" if result["gaps"] or result.get("stale") else "done", reply[:200])
    for event in events:
        say(event)
    say({"type": "done", "content": reply})
    return {**result, "events": events}


def run_review(db: Any, task_id: str, text: str, emit: Any = None) -> dict[str, Any]:
    import workflow
    system = review_system(db, task_id).replace("%%MEMORY%%", db.memory_block(task_id))
    return workflow.run_agent(db, task_id, text, emit, max_steps=8, system=system, tools=doc_tools("review"), scope="review")
