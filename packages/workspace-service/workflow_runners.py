# -*- coding: utf-8 -*-
"""工作流各阶段步骤 Agent 运行器与流水线调度。"""
from __future__ import annotations

from typing import Any
import guards
from workflow_documents import (
    doc_path, doc_read, doc_write, doc_tools, map_read, map_tools, outline_system,
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
    """发一条「agent 对 agent」的发言：进事件流（前台边跑边画）+ 进 events（随专家消息落库，刷新还在）。"""
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


PLACEHOLDER = "（待写）"        # 骨架里每节的占位：逐节写完就把它替换掉，用户看得见「哪节还没写」


def _split_sections(md: str) -> dict[str, str]:
    """按标题切正文：「## 1 引言」→ 它下面的正文。键是标题文字，续跑/重跑靠它认出哪节已经写好了。"""
    import re as _re
    out: dict[str, str] = {}
    cur: str | None = None
    for line in (md or "").splitlines():
        m = _re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if m:
            cur = m.group(2).strip()
            out[cur] = ""
        elif cur is not None:
            out[cur] += line + "\n"
    return out


_LEAK_MARKS = ("<tool_call>", "<function_call", "<|tool", "<｜｜DSML｜｜", "｜｜DSML｜｜")


def _clean_body(md: str) -> str:
    """一节正文的清洗：模型偶尔把工具调用语法（<tool_call>… / ｜｜DSML｜｜ …）混进正文，
    或者自己又写一遍标题（前面还带空行，原来的 strip 只看第一行就漏了）。
    清完不足 80 字 = 这一节其实没写成，返回空串让调用方留「（待写）」占位。"""
    import re as _re
    body = (md or "").strip()
    for mark in _LEAK_MARKS:
        i = body.find(mark)
        if i >= 0:
            body = body[:i]
    lines = body.strip().splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and _re.match(r"^#{1,6}\s", lines[0]):
        lines.pop(0)
    body = "\n".join(lines).strip()
    return body if len(body) >= 80 else ""


def _draft_from_map(db: Any, task_id: str, text: str, emit: Any, system: str) -> dict[str, Any]:
    """按谱系图起稿，两拍走：

    ① 还没骨架：把谱系图铺成「每节一行标题 + （待写）」写进正文，然后停下问一句「确认就开始逐节写」——
       左边立刻能看到整份结构，用户先审骨架，不再有一按就锁死的一次性大动作。
    ② 骨架已在（正文里还有「（待写）」）：逐节生成，每写完一节就把整份骨架重写一次，
       那一节的「（待写）」被正文替换掉 —— 左边一段一段变成真内容。

    骨架已经写好的节不会被重写；一节失败只留它的占位，不毁整篇。
    """
    import workflow
    say = emit or (lambda _e: None)
    events: list[dict[str, Any]] = []
    db.add_message(task_id, "user", text, scope="write")
    say({"type": "user", "content": text})

    mp = map_read(db, task_id)
    plan = _map_plan(mp)
    title = db.summary(task_id)["task"]["title"]
    profile = workflow.agent_profile(db, task_id)
    # 这一版是整篇起稿：全篇不许出现「待补」占位（技能包里那句"缺数据写待补"在这趟里作废）
    system = (system + "\n\n【这一版的口径】整篇起稿，直接写成完整正文："
              "正文里不出现「待补」这类占位，没挂证据的地方按框架做定性论述、用限定语，数字与文献一律不编。")

    cur_md = doc_read(db, task_id, "write")["markdown"]
    filled = _split_sections(cur_md)
    parts: list[str] = []
    for sec in plan:
        got = _clean_body(filled.get(sec["label"]) or "")
        body = got if got and PLACEHOLDER not in got else PLACEHOLDER
        parts.append(f"{'#' * sec['level']} {sec['label']}\n\n{body}")

    def flush() -> dict[str, Any]:
        return doc_write(db, task_id, "write", "\n\n".join(parts), mode="replace")

    # 「真稿」= 有正文且没有（待写）占位。这种稿子绝不能被骨架盖掉：
    # 改写失败要整份回滚并抛「原稿未改动」，否则用户审过的那版就没了。
    has_real = bool(cur_md.strip()) and PLACEHOLDER not in cur_md   # 别看字数：一句「KEEP」也是他审过的稿

    if PLACEHOLDER not in cur_md and not has_real:    # ① 空稿才铺骨架，等他确认
        flush()
        reply = (f"谱系图我按 **{len(plan)} 节** 展开成骨架写到左边了，每节都标着「{PLACEHOLDER}」。\n\n"
                 f"- 确认这份骨架就回一句「开始」，我逐节写：写一节，左边那一节的「待写」就换成正文；\n"
                 f"- 要加、删、合并或调顺序，先说，我改完骨架再动笔（谱系图改了记得重进这一步）。")
        db.add_message(task_id, "assistant", reply, events, scope="write")
        db.log_step(task_id, "write", "ready", f"骨架已铺开（{len(plan)} 节），等确认")
        say({"type": "done", "content": reply})
        return {"reply": reply, "events": events, "awaiting": True}

    todo = [i for i, part in enumerate(parts) if PLACEHOLDER in part]
    failed: list[str] = []
    for idx in todo:
        sec = plan[idx]
        i = idx + 1
        say({"type": "assistant", "content": f"（后台·按谱系图起稿）{i}/{len(plan)}「{sec['label']}」"})
        if sec["ev"]:                                 # 挂了证据：每条都要落进正文
            sys = (system + f"\n\n【这一节】只写「{sec['label']}」这一节，别的节一个字都不写。\n"
                   f"【这一节挂着的证据（照谱系图来的，只能用这些）】\n{sec['ev']}\n"
                   "【硬要求】上面每条证据都要落进这一节的正文；确实用不上的，在正文里一句话说清为什么不用。")
        else:                                         # 没挂证据：按框架自己写，不留「待补」
            sys = (system + f"\n\n【这一节】只写「{sec['label']}」这一节，别的节一个字都不写。\n"
                   "【这一节没挂证据】照框架里这一节该讲的内容，直接写成正常正文（定性论述、机制解释为主）。\n"
                   "【硬要求】不许编造具体数字、文献、实验结果；正文里不要出现「待补」这类占位说明 —— "
                   "拿不准的地方用限定语（在本文条件下、就现有报道看）说过去，不要空着。")
        ask = (f"写出《{title}》里「{sec['label']}」这一节的正文，300-800 字，学术书面语。"
               f"只输出这一节的 markdown：以 {'#' * sec['level']} {sec['label']} 开头，"
               "不要写导语、不要写其他节、不要解释你在做什么。"
               "【重要】不要调用任何工具，直接输出纯 markdown 正文文本。")
        body, last_err = "", ""
        for attempt in range(2):                      # 工具模型偶尔整段只吐工具调用语法，重试一次
            try:
                msg = [{"role": "system", "content": sys},
                       {"role": "user", "content": ask if attempt == 0 else
                        (ask + "\n\n（上一次的输出里没有可用的正文，只有工具调用标记。"
                               "请这次**只输出正文文本**，一个工具调用都不要发。）")}]
                raw = workflow.chat_raw(profile, msg, temperature=0.3, max_tokens=2000) or {}
                # 这一路不走 agent 循环，右栏的「token / 工具数」得自己报：否则这两步统计永远是 0
                if raw.get("usage"):
                    ev = {"type": "usage", **(raw.get("usage") or {})}
                    say(ev); events.append(ev)
                body = _clean_body(raw.get("content") or "")
            except Exception as exc:                  # 一节挂了不该毁掉整篇：记下来，接着写
                body, last_err = "", f"{type(exc).__name__}: {exc}"
            if body:
                break
        if not body:
            failed.append(sec["label"])
            say({"type": "assistant",
                 "content": f"（后台）「{sec['label']}」两次都没吐出正文" + (f"：{last_err}" if last_err else "（只有工具调用标记）")})
            continue
        parts[idx] = f"{'#' * sec['level']} {sec['label']}\n\n{body}"
        _team(say, events, "writer", "论文审查", f"{i}/{len(plan)} 节 · {sec['label']}",
              _clip(body, 160) or "（这一节写空了）")
        try:
            out = flush()                             # 整份重写：这一节的「待写」被正文替换掉
            call_id = "draft-%d" % i
            for ev in ({"type": "tool_call", "name": "doc_write", "args": {"mode": "replace", "section": sec["label"]},
                        "id": call_id},
                       {"type": "tool_result", "name": "doc_write", "ok": True,
                        "summary": f"正文 · {len(out.get('markdown') or '')} 字", "path": out.get("path") or "",
                        "id": call_id}):
                events.append(ev)                     # 进事件流：前端当场计数，落库后重进这一步也还在
                say(ev)
        except Exception as exc:
            say({"type": "assistant", "content": f"（后台）「{sec['label']}」落盘失败：{exc}"})

    if failed and has_real:                           # 真稿被改写却没写全：回滚，保持原稿
        doc_write(db, task_id, "write", cur_md, mode="replace")
        db.log_step(task_id, "write", "failed", f"{len(failed)} 节没写出来，原稿未改动")
        raise RuntimeError(f"{len(failed)} 节没写出来（" + "、".join(failed) + "），原稿未改动")

    left = [plan[i]["label"] for i, part in enumerate(parts) if PLACEHOLDER in part]
    cur = doc_read(db, task_id, "write")
    digest = (f"（按谱系图逐节写完：{len(plan) - len(left)}/{len(plan)} 节）\n"
              f"- 正文：{len(cur['markdown'])} 字，已落盘 `{cur['path']}`\n"
              f"- 依据：谱系图上挂的证据（每节只用挂在该节下面的那几条）\n")
    if left:
        digest += ("- 还没写成：" + "、".join(left) + "（左边仍标「（待写）」，回一句「继续」我再补这几节）\n")
    digest += "- 后面改谱系图不会再动这份正文；要按新图重写就明说「按谱系图重写整篇」。"
    db.add_message(task_id, "assistant", digest, events, scope="write")
    db.log_step(task_id, "agent", "done", "文章撰写·按谱系图逐节写完")
    say({"type": "done", "content": digest})
    return {"reply": digest, "events": events}


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


def run_write(db: Any, task_id: str, text: str, emit: Any = None) -> dict[str, Any]:
    import workflow
    out = doc_read(db, task_id, "outline")
    cur = doc_read(db, task_id, "write")
    has_outline = bool(out["markdown"].strip())
    # 按谱系图逐节起稿只干一件事：没有正文时从零写一遍（图是这次起稿的骨架，写完就与图脱钩）。
    # 已有正文 → 走对话改（说「整篇/全文/从头」才算要整篇重写），调图不该改变已成稿的输出。
    draft_map = bool(map_read(db, task_id)["sections"]) and _wants_draft(text, cur["exists"], has_outline)
    if not draft_map and map_read(db, task_id)["sections"] and PLACEHOLDER in (cur["markdown"] or ""):
        draft_map = True          # 骨架已铺开、还有「（待写）」：这一句就是「继续写」，接着逐节填
    checkpoint = getattr(db, "job_checkpoint", {})
    mode = checkpoint.get("write_mode")
    if not mode:
        mode = "map" if draft_map else "pipeline" if _wants_draft(text, cur["exists"], has_outline) else "edit"
        # Older checkpoints did not persist the chosen route. A completed writer
        # call followed by an interrupted stage belongs to the original pipeline.
        if getattr(db, "resuming", False) and has_outline and not map_read(db, task_id)["sections"]:
            import hashlib
            import json
            signature = hashlib.sha256(json.dumps([(task_id, text), "write"]).encode()).hexdigest()
            material = hashlib.sha256(json.dumps([(task_id, "整理这个项目可用于写正文的素材简报。"), "write"]).encode()).hexdigest()
            completed = checkpoint.get("completed_calls", {})
            if material in completed and (signature in completed or checkpoint.get("signature") == signature):
                mode = "pipeline"
        checkpoint["write_mode"] = mode
        getattr(db, "save_agent_checkpoint", lambda **values: None)(write_mode=mode)
    system = write_system(db, task_id, draft=mode == "map").replace("%%MEMORY%%", db.memory_block(task_id))
    if mode == "map":
        return _draft_from_map(db, task_id, text, emit, system)
    if mode == "edit":
        return workflow.run_agent(db, task_id, text, emit, max_steps=8, system=system,
                                 tools=doc_tools("write"), scope="write")
    return _run_write_pipeline(db, task_id, text, emit, system)


def run_review(db: Any, task_id: str, text: str, emit: Any = None) -> dict[str, Any]:
    import workflow
    system = review_system(db, task_id).replace("%%MEMORY%%", db.memory_block(task_id))
    return workflow.run_agent(db, task_id, text, emit, max_steps=8, system=system, tools=doc_tools("review"), scope="review")
