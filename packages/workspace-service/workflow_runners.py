# -*- coding: utf-8 -*-
"""工作流各阶段步骤 Agent 运行器与流水线调度。"""
from __future__ import annotations

from typing import Any
import guards
from workflow_documents import (
    doc_read, doc_tools, map_tools, outline_system,
    map_system, write_system, review_system, evidence_system,
    critic_system, EVIDENCE_TOOLS, CRITIC_TOOLS, PIPELINE_WORDS,
)
from workflow_converters import cite_paths, _cite_load
from workflow_prompts import IDEA_TOOLS


def run_idea(db: Any, task_id: str, text: str, emit: Any = None) -> dict[str, Any]:
    """创新点审查这一步：受限工具（只看创新点）+ 博导 skill 框架 + 项目长期/短期记忆。"""
    import workflow
    system = workflow.idea_system(db, task_id).replace("%%MEMORY%%", db.memory_block(task_id))
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


def _stage(db: Any, task_id: str, prompt: str, system: str, tools: list[dict[str, Any]],
           emit: Any, max_steps: int) -> dict[str, Any]:
    """跑一个后台子 agent：不进对话历史、不发 done，进度照旧流给前台。"""
    import workflow
    return workflow.run_agent(db, task_id, prompt, emit, max_steps=max_steps, system=system,
                              tools=tools, scope="write", publish=False)


def _bullets(text: str) -> int:
    return sum(1 for line in (text or "").splitlines() if line.lstrip().startswith("-"))


def _wants_draft(text: str, has_body: bool, has_outline: bool) -> bool:
    if not has_outline:
        return False
    if not has_body:
        return True
    return any(w in (text or "") for w in PIPELINE_WORDS)


def _audit(db: Any, task_id: str, online: bool = False) -> dict[str, Any]:
    r = guards.audit(doc_read(db, task_id, "write")["markdown"], _cite_load(db, task_id),
                     db.papers_of(task_id), online=online)
    if r["bad"]:
        out = cite_paths(db, task_id)[0].with_name("引用核验.md")
        out.write_text(guards.report(r), encoding="utf-8")
        r["path"] = str(out)
    return r


def _run_write_pipeline(db: Any, task_id: str, text: str, emit: Any, system: str) -> dict[str, Any]:
    say = emit or (lambda _e: None)
    events: list[dict[str, Any]] = []
    db.add_message(task_id, "user", text, scope="write")
    say({"type": "user", "content": text})

    material = _stage(db, task_id, "整理这个项目可用于写正文的素材简报。", 
                      evidence_system(db, task_id).replace("%%MEMORY%%", db.memory_block(task_id)),
                      EVIDENCE_TOOLS, emit, 6)
    events += material["events"]
    brief = (material.get("reply") or "").strip()
    say({"type": "assistant", "content": f"（后台）① 素材官：{_bullets(brief)} 条素材／缺口，交给写手。"})

    brief_block = ("\n\n【素材简报（素材官整理：只能用这里的材料与出处；缺的地方写「待补：<还差什么>」，不许自己补文献和数据）】\n"
                   + (brief[:5000] or "（素材官没找到可用素材 —— 正文只能写结构，材料处一律「待补」）"))

    draft = _stage(db, task_id, text, system + brief_block, doc_tools("write"), emit, 8)
    events += draft["events"]
    say({"type": "assistant", "content": f"（后台）② 写手：初稿 {len(doc_read(db, task_id, 'write')['markdown'])} 字。"})

    audit = _audit(db, task_id)
    if audit["bad"]:
        say({"type": "assistant", "content":
             f"（后台）③ 引用核验：台账 {audit['total']} 条，对不上号 {len(audit['bad'])} 条 —— 已写进「引用核验.md」。"})

    critic_run = _stage(db, task_id, "审查刚写的这一版正文，按硬规则给出修改指令。",
                        critic_system(db, task_id, brief).replace("%%MEMORY%%", db.memory_block(task_id))
                        + guards.audit_block(audit),
                        CRITIC_TOOLS, emit, 4)
    events += critic_run["events"]
    critic = (critic_run.get("reply") or "").strip()
    n_issues = _bullets(critic)

    fix = ("按下面的审查意见把正文改一遍：能改的直接改；确实缺材料/缺数据改不了的，写「待补：<还差什么>」并说明为什么没改；"
           "不许编造数字与文献。改完必须调用 doc_write 写**整份** markdown 覆盖当前正文。\n\n"
           f"【审查意见】\n{critic[:4000] or '（审查没给出意见）'}")
    final = _stage(db, task_id, fix, system + brief_block, doc_tools("write"), emit, 8)
    events += final["events"]

    cur = doc_read(db, task_id, "write")
    head = next((l for l in critic.splitlines() if l.lstrip().startswith("-")), "")
    digest = (f"（后台流水线完成：素材官 → 写手 → 审查 → 定稿）\n"
              f"- 素材：{_bullets(brief)} 条（含缺口）\n"
              f"- 审查意见：{n_issues} 条" + (f"，最严重一条：{head[:120]}" if head else "（无硬伤）") + "\n"
              f"- 正文：{len(cur['markdown'])} 字，已落盘 `{cur['path']}`\n"
              f"- 改法：审查意见已逐条处理；没处理的在正文里标了「待补」。要再改就说改哪节。")
    db.add_message(task_id, "assistant", digest, events, scope="write")
    db.log_step(task_id, "agent", "done", "文章撰写流水线")
    say({"type": "done", "content": digest})
    return {"reply": digest, "events": events}


def run_write(db: Any, task_id: str, text: str, emit: Any = None) -> dict[str, Any]:
    import workflow
    out = doc_read(db, task_id, "outline")
    cur = doc_read(db, task_id, "write")
    system = write_system(db, task_id).replace("%%MEMORY%%", db.memory_block(task_id))
    if not _wants_draft(text, cur["exists"], bool(out["markdown"].strip())):
        return workflow.run_agent(db, task_id, text, emit, max_steps=8, system=system,
                                 tools=doc_tools("write"), scope="write")
    return _run_write_pipeline(db, task_id, text, emit, system)


def run_review(db: Any, task_id: str, text: str, emit: Any = None) -> dict[str, Any]:
    import workflow
    system = review_system(db, task_id).replace("%%MEMORY%%", db.memory_block(task_id))
    return workflow.run_agent(db, task_id, text, emit, max_steps=8, system=system, tools=doc_tools("review"), scope="review")
