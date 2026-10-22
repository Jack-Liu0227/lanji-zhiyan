# -*- coding: utf-8 -*-
"""工作流各阶段步骤 Agent 运行器与流水线调度。"""
from __future__ import annotations

from typing import Any
import guards
from workflow_documents import (
    doc_read, doc_write, doc_tools, map_read, map_tools, outline_system,
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


# ---- 后台 agent 之间的交班：前台右栏按「谁 → 谁」一条条显示（chat-views.js 的 team 分支）----
# 事件只带语义角色，配色/版式归前端；这样右栏跟「团队页」是同一套身份，不用两边同步色号。
TEAM_ROLE = {"evidence": ("素材官", "📚"), "writer": ("论文写作", "✍️"), "critic": ("论文审查", "🧐")}


def _clip(text: str, n: int) -> str:
    t = (text or "").strip()
    return t if len(t) <= n else t[:n].rstrip() + "…"


def _team(say: Any, events: list[dict[str, Any]], frm: str, to: str, tag: str, content: str) -> None:
    """发一条「agent 对 agent」的发言：进事件流（前台边跑边画）+ 进 events（随助手消息落库，刷新还在）。"""
    name, emoji = TEAM_ROLE[frm]
    ev = {"type": "team", "role": frm, "name": name, "emoji": emoji,
          "to": to, "tag": tag, "content": (content or "").strip()}
    events.append(ev)
    say(ev)


def _wants_draft(text: str, has_body: bool, has_outline: bool) -> bool:
    """要不要整篇起稿重写。已有正文时**只认「整篇/全文/从头」这类说法** ——
    「重写 4.2 这节」是改一节，不是把改好的整篇推倒（两种路子都会覆盖整份文件）。"""
    if not has_outline:
        return False
    if not has_body:
        return True
    t = text or ""
    return any(w in t for w in PIPELINE_WORDS if w != "重写")


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
    _team(say, events, "evidence", "论文写作", f"{_bullets(brief)} 条素材",
          _clip(brief, 420) or "（没找到可用素材，正文只能写结构，材料处一律「待补」）")

    brief_block = ("\n\n【素材简报（素材官整理：只能用这里的材料与出处；缺的地方写「待补：<还差什么>」，不许自己补文献和数据）】\n"
                   + (brief[:5000] or "（素材官没找到可用素材 —— 正文只能写结构，材料处一律「待补」）"))

    draft = _stage(db, task_id, text, system + brief_block, doc_tools("write"), emit, 8)
    events += draft["events"]
    _team(say, events, "writer", "论文审查", f"初稿 {len(doc_read(db, task_id, 'write')['markdown'])} 字",
          _clip(draft.get("reply"), 220) or "按素材简报写完初稿，缺口处都标了「待补」。发给审查。")

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
    _team(say, events, "critic", "论文写作", f"{n_issues or '无'} 条硬伤",
          _clip(critic, 800) or "无硬伤")

    fix = ("按下面的审查意见把正文改一遍：能改的直接改；确实缺材料/缺数据改不了的，写「待补：<还差什么>」并说明为什么没改；"
           "不许编造数字与文献。改完必须调用 doc_write 写**整份** markdown 覆盖当前正文。\n\n"
           f"【审查意见】\n{critic[:4000] or '（审查没给出意见）'}")
    final = _stage(db, task_id, fix, system + brief_block, doc_tools("write"), emit, 8)
    events += final["events"]

    cur = doc_read(db, task_id, "write")
    n_todo = cur["markdown"].count("待补")
    _team(say, events, "writer", "左边正文", f"定稿 {len(cur['markdown'])} 字 · 待补 {n_todo} 处",
          _clip(final.get("reply"), 320) or f"整份覆盖写回左边正文：{len(cur['markdown'])} 字。")
    head = next((l for l in critic.splitlines() if l.lstrip().startswith("-")), "")
    digest = (f"（后台流水线完成：素材官 → 论文写作 → 论文审查 → 定稿）\n"
              f"- 素材：{_bullets(brief)} 条（含缺口）\n"
              f"- 审查意见：{n_issues} 条" + (f"，最严重一条：{head[:120]}" if head else "（无硬伤）") + "\n"
              f"- 正文：{len(cur['markdown'])} 字，已落盘 `{cur['path']}`\n"
              f"- 改法：审查意见已逐条处理；没处理的在正文里标了「待补」。要再改就说改哪节。")
    db.add_message(task_id, "assistant", digest, events, scope="write")
    db.log_step(task_id, "agent", "done", "文章撰写流水线")
    say({"type": "done", "content": digest})
    return {"reply": digest, "events": events}


def _map_plan(mp: dict[str, Any]) -> list[dict[str, Any]]:
    """谱系图 → 起稿计划：根（章）=##，它的「包含」子（节）=###，按图上的顺序深度优先。

    起稿只认这张图：大纲里有、图上没有的小节不会写进正文（要写就先把图补全）。
    「下一章」这种同级串联不算父子，只用来排章的顺序。
    """
    nodes = {n["id"]: n for n in mp["nodes"]}
    kids: dict[str, list[str]] = {}
    incoming: set[str] = set()
    for e in mp["edges"]:
        f, t = e.get("from"), e.get("to")
        if f not in nodes or t not in nodes or (e.get("label") or "") in ("下一章", "下一节"):
            continue
        if nodes[t].get("type") != "section":
            continue                             # 挂着的资料是证据，不是要写的小节（由 _map_evid 收进那一节）
        kids.setdefault(f, []).append(t)
        incoming.add(t)
    plan: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(nid: str, level: int) -> None:
        if nid in seen:
            return
        seen.add(nid)
        plan.append({"id": nid, "level": min(level, 4), "label": nodes[nid]["label"],
                     "ev": _map_evid(mp, nodes, nid)})
        for k in kids.get(nid, []):
            walk(k, level + 1)

    for n in mp["nodes"]:
        if n.get("type") == "section" and n["id"] not in incoming:
            walk(n["id"], 2)
    for n in mp["nodes"]:                        # 孤点小节（没挂进树）也写成一节，别悄悄丢掉
        if n.get("type") == "section":
            walk(n["id"], 3)
    return plan


def _map_evid(mp: dict[str, Any], nodes: dict[str, Any], nid: str) -> str:
    """这一节挂着哪些证据：子节点（非小节）的标题 + 谱系里那两行精读结论 + 边上的用途标签。"""
    out: list[str] = []
    own = (nodes[nid].get("detail") or "").strip().replace("\n", " ")
    if own:
        out.append(f"本节的说明：{own[:200]}")
    for e in mp["edges"]:
        if e.get("from") != nid:
            continue
        t = nodes.get(e.get("to"))
        if not t or t.get("type") == "section":
            continue
        d = (t.get("detail") or "").strip().replace("\n", " ")
        out.append(f"{t['label']}（{e.get('label') or '支撑'}）" + (f"：{d[:200]}" if d else ""))
    return "\n".join(f"- {x}" for x in out)


def _draft_from_map(db: Any, task_id: str, text: str, emit: Any, system: str) -> dict[str, Any]:
    """按谱系图逐节起稿（只在还没有正文时走这条路）：一节一次模型调用，最后拼成整份落盘。

    一次性动作：写完就完事，之后改谱系图不会动已经写好的正文。
    ponytail: 顺着一节一节调，不做并发；21 节大概两三分钟，并发要处理限流和乱序拼接，收益不值。
    """
    import re
    import workflow
    say = emit or (lambda _e: None)
    events: list[dict[str, Any]] = []
    db.add_message(task_id, "user", text, scope="write")
    say({"type": "user", "content": text})

    mp = map_read(db, task_id)
    plan = _map_plan(mp)
    profile = workflow.agent_profile(db, task_id)
    title = db.summary(task_id)["task"]["title"]
    # 这一版是整篇起稿：全篇不许出现「待补」占位（技能包里那句"缺数据写待补"在这趟里作废）
    system = (system + "\n\n【这一版的口径】整篇起稿，直接写成完整正文："
              "正文里不出现「待补」这类占位，没挂证据的地方按框架做定性论述、用限定语，数字与文献一律不编。")
    parts: list[str] = []
    empty: list[str] = []
    for i, sec in enumerate(plan, 1):
        say({"type": "assistant", "content": f"（后台·按谱系图起稿）{i}/{len(plan)}「{sec['label']}」"})
        if sec["ev"]:                                    # 挂了证据：每条都要落进正文
            sys = (system + f"\n\n【这一节】只写「{sec['label']}」这一节，别的节一个字都不写。\n"
                   f"【这一节挂着的证据（照谱系图来的，只能用这些）】\n{sec['ev']}\n"
                   "【硬要求】上面每条证据都要落进这一节的正文；确实用不上的，在正文里一句话说清为什么不用。")
        else:                                            # 没挂证据：按框架自己写，不留「待补」
            sys = (system + f"\n\n【这一节】只写「{sec['label']}」这一节，别的节一个字都不写。\n"
                   "【这一节没挂证据】照框架里这一节该讲的内容，直接写成正常正文（定性论述、机制解释为主）。\n"
                   "【硬要求】不许编造具体数字、文献、实验结果；正文里不要出现「待补」这类占位说明 —— "
                   "拿不准的地方用限定语（在本文条件下、就现有报道看）说过去，不要空着。")
        ask = (f"写出《{title}》里「{sec['label']}」这一节的正文，300-800 字，学术书面语。"
               f"只输出这一节的 markdown：以 {'#' * sec['level']} {sec['label']} 开头，"
               "不要写导语、不要写其他节、不要解释你在做什么。")
        try:
            body = (workflow.chat(profile, [{"role": "system", "content": sys},
                                           {"role": "user", "content": ask}],
                                  temperature=0.3, max_tokens=2000) or "").strip()
        except Exception as exc:                 # 一节挂了不该毁掉整篇：记下来，接着写
            body = ""
            say({"type": "assistant", "content": f"（后台）「{sec['label']}」失败：{type(exc).__name__}: {exc}"})
        lines = body.splitlines()
        if lines and re.match(r"^#{1,6}\s", lines[0]):
            body = "\n".join(lines[1:]).strip()  # 模型自己带了标题就去掉，标题统一由这里出
        if not body:
            empty.append(sec["label"])
            body = "（本节未生成 —— 跟 agent 说「重写 <这一节标题>」补上）"
        parts.append(f"{'#' * sec['level']} {sec['label']}\n\n{body}")

    md = "\n\n".join(parts)
    doc_write(db, task_id, "write", md)
    cur = doc_read(db, task_id, "write")
    digest = (f"（按谱系图起稿完成：{len(plan)} 节逐节生成 → 拼成整份）\n"
              f"- 正文：{len(cur['markdown'])} 字，已落盘 `{cur['path']}`\n"
              f"- 依据：谱系图上挂的证据（每节只用挂在该节下面的那几条）\n"
              + (f"- 没生成出来的节：{'、'.join(empty)}（说「重写 <标题>」补上）\n" if empty else "")
              + "- 后面改谱系图不会再动这份正文；要按新图重写就明说「按谱系图重写整篇」。")
    db.add_message(task_id, "assistant", digest, events, scope="write")
    db.log_step(task_id, "agent", "done", "文章撰写·按谱系图起稿")
    say({"type": "done", "content": digest})
    return {"reply": digest, "events": events}


def run_write(db: Any, task_id: str, text: str, emit: Any = None) -> dict[str, Any]:
    import workflow
    out = doc_read(db, task_id, "outline")
    cur = doc_read(db, task_id, "write")
    has_outline = bool(out["markdown"].strip())
    # 按谱系图逐节起稿只干一件事：没有正文时从零写一遍（图是这次起稿的骨架，写完就与图脱钩）。
    # 已有正文 → 走对话改（说「整篇/全文/从头」才算要整篇重写），调图不该改变已成稿的输出。
    draft_map = bool(map_read(db, task_id)["sections"]) and _wants_draft(text, cur["exists"], has_outline)
    system = write_system(db, task_id, draft=draft_map).replace("%%MEMORY%%", db.memory_block(task_id))
    if draft_map:
        return _draft_from_map(db, task_id, text, emit, system)
    if not _wants_draft(text, cur["exists"], has_outline):
        return workflow.run_agent(db, task_id, text, emit, max_steps=8, system=system,
                                 tools=doc_tools("write"), scope="write")
    return _run_write_pipeline(db, task_id, text, emit, system)


def run_review(db: Any, task_id: str, text: str, emit: Any = None) -> dict[str, Any]:
    import workflow
    system = review_system(db, task_id).replace("%%MEMORY%%", db.memory_block(task_id))
    return workflow.run_agent(db, task_id, text, emit, max_steps=8, system=system, tools=doc_tools("review"), scope="review")
