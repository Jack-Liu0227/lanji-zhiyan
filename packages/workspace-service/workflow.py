"""览籍智研 · 工作流引擎

在 research_core.ResearchDB 之上补齐「创新点审查 → 论文搜索 → 下载精读 → 知识库笔记
→ 文章撰写 → 审查 → 格式转换」七步工作流，外加任务/日历体系、模型切换、主题色。

设计原则（沿用 1.0）：
  * 证据优先：候选证据一律带页码 + 原文片段，走 research_core 的严格闸门。
  * 只走开放获取：下载只对 OA 链接放行，拿不到就如实返回「无 OA 全文」。
  * 模型只做草稿与建议：审查/撰写产出是草稿，不是结论。
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os

import fileguard        # 文件安全：子进程环境洗白 / 写前快照 / 打开白名单 / 审计
import guards          # 出站脱敏 + 引用核验（见 guards.py）
import memory_store    # 记忆体系：全局/项目两层，可改可删，FTS 检索（见 memory_store.py 顶部口径）
import paths            # 唯一路径来源（data / user / executors 都在 HOME 下）
import securestore      # 凭据保护（DPAPI，见 securestore.py）
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_core import ResearchDB

from workflow_db import (        # 库表层在 workflow_db.py，这里原样再导出：各处 import workflow 不用改
    WorkflowDB, PROJ_DIRS, STAGES, STAGE_LABEL, WELCOME_TEXT, BUILTIN_SCHEDULES,
    BUILTIN_ASSISTANTS, PROVIDER_CATALOG,
    DATA, PAPERS, NOTES, OUT, NOTE_CATEGORY, UA,
    CATALOG_PATH, WIRE_LABEL, USABLE_WIRES, CUSTOM_WIRES, OAUTH_CONSOLE,
    now, current_stage_key, make_project_tree, encrypt_legacy_secrets,
    _assert_key_host, _host_of, _stored_key, _reveal_key, _text, _norm_hhmm,
)

def open_url(req: Any, timeout: int = 30) -> Any:
    """出网统一入口。

    环境里可能挂着代理（HTTP(S)_PROXY=127.0.0.1:10090），但代理（猫猫云/mihomo）没开时，
    所有请求都会被"积极拒绝"→ 页面显示「无法连接」，跟 key 一点关系都没有。
    所以这里先探一下代理端口：活着就用代理，死了这一程直连（国内接口直连没问题，真被墙的站点再开代理）。
    """
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or \
            os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or ""
    if proxy:
        parts = urllib.parse.urlsplit(proxy if "://" in proxy else "http://" + proxy)
        try:
            with socket.create_connection((parts.hostname, parts.port or 80), timeout=0.6):
                pass                                    # 代理活着：走它
        except OSError:
            return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def chat(profile: dict[str, Any], messages: list[dict[str, str]], temperature: float = 0.2,
         timeout: int = 180, max_tokens: int = 4096) -> str:
    """OpenAI 兼容 /chat/completions。没有配置模型时给出可执行的提示。

    回空不一定是模型不会答：推理模型（deepseek-v4-flash 这类）的思维链也计入 max_tokens，
    预算小的时候思考把额度吃光、正文就是空串（实测 3000 全是 reasoning、content_len=0，
    finish_reason=length）。所以空回就翻倍预算再要来一次，只这一次。
    """
    out = chat_raw(profile, messages, temperature=temperature, timeout=timeout, max_tokens=max_tokens).get("content") or ""
    if out.strip():
        return out
    return chat_raw(profile, messages, temperature=temperature, timeout=timeout,
                    max_tokens=max_tokens * 2).get("content") or ""


class ModelInvoker:
    """模型调用门面：支持注册全局拦截器，提供测试与插件的无缝替换机制。"""
    _handler: Any = None

    @classmethod
    def register(cls, handler: Any) -> None:
        cls._handler = handler

    @classmethod
    def unregister(cls) -> None:
        cls._handler = None

    @classmethod
    def invoke(cls, profile: dict[str, Any], messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
               temperature: float = 0.2, timeout: int = 180, max_tokens: int = 4096) -> dict[str, Any] | None:
        if cls._handler is not None:
            return cls._handler(profile, messages, tools=tools, temperature=temperature, timeout=timeout, max_tokens=max_tokens)
        return None

    @classmethod
    def call(cls, profile: dict[str, Any], messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, **kwargs) -> dict[str, Any]:
        if cls._handler is not None:
            return cls._handler(profile, messages, tools=tools, **kwargs)
        return chat_raw(profile, messages, tools=tools, **kwargs)


def chat_raw(profile: dict[str, Any], messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
             temperature: float = 0.2, timeout: int = 180, max_tokens: int = 4096,
             on_delta: Any = None, cancel_check: Any = None, on_reasoning: Any = None) -> dict[str, Any]:
    """返回 assistant message 原始结构（含 tool_calls），供 agent 循环使用。"""
    if not profile:
        raise RuntimeError("尚未配置模型：到「模型」页添加一个 OpenAI 兼容接口并设为当前模型。")
    _assert_key_host(profile)          # key 只发它被填进来时的那个域名
    invoked = ModelInvoker.invoke(profile, messages, tools=tools, temperature=temperature, timeout=timeout, max_tokens=max_tokens)
    if invoked is not None:
        return invoked
    try:
        import runtime
        return runtime.call(profile, messages, tools=tools, temperature=temperature, timeout=timeout,
                            max_tokens=max_tokens, on_delta=on_delta, cancel_check=cancel_check,
                            on_reasoning=on_reasoning)
    except (ImportError, ModuleNotFoundError):
        pass
    guards.scrub(messages, note=f"→ {profile.get('model') or '?'}")   # 出站闸门：个人信息/密钥就地打码，再发出去
    if profile.get("api", "openai-completions") != "openai-completions":
        from agent_protocols import complete
        return complete(profile, messages, tools, temperature, timeout, max_tokens,
                        on_delta=on_delta, cancel_check=cancel_check, on_reasoning=on_reasoning)
    payload = {"model": profile["model"], "temperature": temperature, "max_tokens": max_tokens, "messages": messages}
    if profile.get("reasoning", "auto") != "auto":
        payload["reasoning_effort"] = profile["reasoning"]
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    req = urllib.request.Request(
        profile["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {profile['api_key']}"} if profile.get("api_key") else {})},
        method="POST",
    )
    try:
        with open_url(req, timeout=timeout) as res:
            data = json.loads(res.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"模型接口 HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"模型接口不可达：{exc}") from exc
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"模型回包无法解析：{str(data)[:300]}") from exc
    usage = data.get("usage") or {}
    return {"role": "assistant", "content": (msg.get("content") or "").strip(),
            "tool_calls": msg.get("tool_calls") or [],
            "usage": {"prompt": int(usage.get("prompt_tokens") or 0),
                      "completion": int(usage.get("completion_tokens") or 0),
                      "total": int(usage.get("total_tokens") or 0), "exact": bool(usage)}}


def _json_in(text: str) -> Any | None:
    """从模型回包里抠出第一个完整 JSON（对象或数组）。

    比「贪婪正则 + json.loads」结实一点：模型常先讲一段话，或者把 JSON 截断在半路，
    贪婪匹配会把后面的文字一起吞进去、然后 json.loads 直接抛。这里做括号配平，只取成对的那一截。
    """
    raw = re.sub(r"^```(?:json)?\s*|```$", "", (text or "").strip(), flags=re.I).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = raw.find(opener)
        if start < 0:
            continue
        depth, in_str, esc = 0, False, False
        for i in range(start, len(raw)):
            ch = raw[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start:i + 1])
                    except ValueError:
                        break
    return None


_JSON_MAX_TOKENS = 8000   # 推理模型的思维链也记在这个上限里，见 chat_json
WRITE_MAX_TOKENS = 8000   # 写作那步要吐整份正文（一个巨大的 markdown 参数），预算得跟 chat_json 一档


def chat_json(profile: dict[str, Any], messages: list[dict[str, str]], **kw: Any) -> Any:
    """要模型回 JSON。本地小模型偶尔先讲一段人话、把 JSON 截断，或者把单条结果包成数组
    —— 再要一次，只这一次。

    预算不能跟着 chat() 的 4096 走：推理模型（deepseek-v4-flash 这类）的思维链也计入
    max_tokens（实测 4001/4096 全是思考，正文只剩两千来字），4096 下正文经常被截断甚至
    整段空回，调用方只会看到「模型没有返回 JSON」。
    """
    kw.setdefault("max_tokens", _JSON_MAX_TOKENS)
    raw = chat(profile, messages, **kw)
    got = _json_in(raw)
    if got is None:
        retry = messages + [{"role": "user",
                             "content": "只输出 JSON 本体：不要解释、不要前后缀、不要代码块围栏。"}]
        raw2 = chat(profile, retry, **kw)
        got = _json_in(raw2)
        if got is None:
            raise RuntimeError(f"模型没有返回 JSON：{raw2[:200]}")
    if isinstance(got, list):          # 模型把单条判定包成 [{...}] 时取第一个对象
        got = next((x for x in got if isinstance(x, dict)), {})
    return got


def discover_models(base_url: str, api_key: str = "", timeout: int = 25) -> list[dict[str, str]]:
    """GET {base_url}/models —— 一键发现模型。OpenAI 兼容网关基本都认这条。"""
    req = urllib.request.Request(
        base_url.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
    )
    try:
        with open_url(req, timeout=timeout) as res:
            data = json.loads(res.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"发现模型失败：{exc}") from exc
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        # ponytail: 有些网关只给非标准结构，这时让用户手填模型 id
        raise RuntimeError("该网关没有返回标准模型列表，请手动填写模型 id")
    return [{"id": str(r.get("id") or r.get("name") or ""),
             "name": str(r.get("name") or r.get("id") or "")} for r in rows if (r.get("id") or r.get("name"))]


def test_provider(base_url: str, api_key: str, model: str, timeout: int = 60) -> str:
    """发一条最小消息，证明 Key/地址/模型名三者真的能打通。"""
    return chat({"base_url": base_url, "api_key": api_key, "model": model},
                [{"role": "user", "content": "回复两个字：可用"}], timeout=timeout, max_tokens=64)


# ============================ 论文搜索（开放接口） ============================

from workflow_search import (
    SOURCES, _openalex_works,
    litgraph, search, make_project_tree, download_pdf, _GRAPH_CACHE
)

# ============================ 七步工作流 ============================

from workflow_stages import (
    stage_idea, _relevant, stage_search, stage_download, stage_download_selected,
    stage_zotero_import, is_paper_pdf, deepread_state, deepread_stop, deepread_kick,
    stage_read, stage_notes, stage_write, stage_review, stage_convert,
    DeepreadStopped
)


from workflow_converters import (
    cite_read, cite_add, cite_bib, conv_run, conv_files, conv_compile, conv_open, _safe_path,
    fig_dir, fig_list, fig_rename, conv_preview, open_convert, convert_system,
    tree_list, file_hand, files_system,
    en_state, en_run, compose_draft
)


ACTION = {
    "idea": stage_idea, "search": stage_search, "download": stage_download,
    "read": stage_read, "notes": stage_notes, "write": stage_write,
    "review": stage_review, "convert": stage_convert,
}


# ============================ Agent：把七步当工具调用 ============================

from workflow_prompts import (
    TOOL_SPECS, IDEA_TOOLS,
    AGENT_SYSTEM, IDEA_FALLBACK, _DELEGATE_SYSTEM,
    skill_text
)



def idea_system(db: "WorkflowDB", task_id: str) -> str:
    """创新点审查专用 system：限定工具 + 博导 skill 框架 + 项目记忆。"""
    task = db.summary(task_id)["task"]
    skills = "\n\n".join(x for x in (
        skill_text("research", "research-topic-selection", "SKILL.md", cap=2000, db=db, task_id=task_id),
        skill_text("idea-evaluator", "SKILL.md", cap=4500, db=db, task_id=task_id),
    ) if x) or IDEA_FALLBACK
    return f"""你是「览籍智研」里这一步的创新点审查助手，用中文工作，站在博导（既严格又给建设性建议）的位置。

硬规则：
1. 这个对话 100% 绑定项目《{task['title']}》，你只做「创新点审查」这一件事；不下载文献、不精读、不撰写、不导出格式，用户要这些就说明去别的步骤。
2. 默认多轮：先问清硬约束（能拿到的数据 / 算力 / 时间 / 技能 / 目标期刊或学位要求），一次只问 1-3 个最关键的问题，别一口气拷问。
3. 信息够了再调用 idea_review 出正式判定（会落成项目笔记 + 长期记忆）；结论要落到「下一步做什么」。
4. 出现值得长期记住的结论、约束或用户偏好时，调用 memory 记一条（一句话，别刷屏）；判断和评分这类能复用的做法留给 skill，不写进记忆。
5. 不编造文献与数据；没有检索能力就写「未核实，需查文献」。你是草稿式意见，最终以导师与证据为准。

【评估框架（取自本机 skill）】
{skills}

【项目记忆】
%%MEMORY%%"""


def _overview(db: "WorkflowDB", task_id: str) -> dict[str, Any]:
    s = db.summary(task_id)
    scoped = bool(db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (task_id,)).fetchone())
    return {"任务": s["task"]["title"], "阶段": s["task"]["stage"], "文献": s["papers"], "证据候选": s["evidence"],
            "检索结果": [{"id": r["id"], "title": r["title"][:90], "source": r["source"], "year": r["year"], "oa": bool(r["pdf_url"])} for r in db.results(task_id)[:10]],
            "文献库": [{"id": p["id"], "name": p["filename"], "pages": p["pages"]} for p in db.papers_of(task_id)[:10]],
            "笔记": [{"id": n["id"], "title": n["title"]} for n in db.notes(task_id)[:10]],
            "日历": [{"title": e["title"], "date": e["date"]} for e in db.events()
                     if not scoped or e.get("task_id") == task_id][:10]}


def _resolve_project(db: "WorkflowDB", needle: str) -> dict[str, Any] | None:
    """项目名或 id → 项目行（先精确再包含；找不到返回 None）。"""
    needle = (needle or "").strip()
    if not needle:
        return None
    tasks = db.tasks()
    for t in tasks:
        if t["id"] == needle:
            return dict(t)
    for t in tasks:
        if (t["title"] or "").strip() == needle:
            return dict(t)
    for t in tasks:
        if needle in (t["title"] or ""):
            return dict(t)
    return None


def _search_scope(db: "WorkflowDB", task_id: str, project: str) -> list[tuple[str, str]]:
    """这次检索覆盖哪些项目：指定项目 > 当前项目 > 全部项目（首页 Agent 没有项目时）。"""
    if project:
        row = _resolve_project(db, project)
        if not row:
            raise ValueError("找不到这个项目：%s（可先用 library_overview 或问用户）" % project)
        return [(row["id"], row["title"])]
    if task_id and db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (task_id,)).fetchone():
        return [(task_id, db.summary(task_id)["task"]["title"])]
    chat = db.db.execute("SELECT 1 FROM chats WHERE id=?", (task_id,)).fetchone() if task_id else None
    if chat:
        return [(t["id"], t["title"]) for t in db.tasks()]      # 首页/通用对话：跨项目
    return [(task_id, "")] if task_id else [(t["id"], t["title"]) for t in db.tasks()]


def _knowledge_query(db: "WorkflowDB", task_id: str, query: str, project: str = "") -> dict[str, Any]:
    """知识检索：语义层双路召回（FTS5 trigram + 向量，RRF 融合），索引没建好时退回子串扫。

    「最高标准」指的是命中要能回指原文页码，不是相似度数字好看 —— 所以论文按页切块，
    每条命中都带 citation（文件名 p.N）。
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("要给检索关键词")
    scope = _search_scope(db, task_id, project)
    scoped = bool(project) or bool(db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (task_id,)).fetchone())
    ids = [pid for pid, _title in scope]
    literal = _knowledge_query_literal(db, q, scope, scoped)["hits"]
    try:
        import semantic
        out = semantic.hybrid(db, q, project_ids=ids if scoped else None, limit=20)
        if out.get("hits"):
            return _fuse_literal(out, literal)
        reason = out.get("mode") or "empty"
    except Exception as exc:                              # 语义层是增强，不是前置依赖
        reason = "semantic_failed: %s" % exc
    return dict(_knowledge_query_literal(db, q, scope, scoped), mode="lexical", reason=reason)


def _fuse_literal(out: dict[str, Any], literal: list[dict[str, Any]]) -> dict[str, Any]:
    """第三路：笔记/证据的字面命中。

    中文术语经常是「笔记写 Cu掺杂、用户问 铜掺杂」——向量能连上、trigram 连不上，
    所以把老的字面扫也并进 RRF，一个词对得上的笔记不会被长句查询挤下去。
    """
    hits = {(h["kind"], h["id"], h.get("page_no") or 0): dict(h) for h in out["hits"]}
    for rank, h in enumerate(literal):
        key = (h["kind"], h["id"], 0)                    # 笔记/证据没有页码，按源合并
        gain = 1.0 / (60 + rank + 1)
        if key in hits:
            hits[key]["rrf"] = round(hits[key]["rrf"] + gain, 5)
            if "literal" not in hits[key]["matched"]:
                hits[key]["matched"].append("literal")
        else:
            hits[key] = dict(h, rrf=round(gain, 5), matched=["literal"])
    merged = sorted(hits.values(), key=lambda h: -h["rrf"])[:20]
    mode = "hybrid" if literal else out["mode"]
    return {**out, "hits": merged, "total": len(merged), "mode": mode}


def _knowledge_query_literal(db: "WorkflowDB", q: str, scope: list[tuple[str, str]],
                             scoped: bool) -> dict[str, Any]:
    """老的子串扫：索引还没建、或语义层坏掉时的兜底。口径与升级前一致。"""
    q = q.lower()
    hits: list[dict[str, Any]] = []
    for pid, ptitle in scope:
        for n in db.notes(pid):
            if q in (n["title"] or "").lower() or q in (n["markdown"] or "").lower():
                idx = (n["markdown"] or "").lower().find(q)
                hits.append({"kind": "note", "project": ptitle, "id": n["id"], "title": n["title"],
                             "context": (n["markdown"] or "")[max(0, idx - 120): idx + 240]})
        for e in db.evidence(pid):
            if q in (e["value"] or "").lower() or q in (e["excerpt"] or "").lower() or q in (e["field"] or "").lower():
                hits.append({"kind": "evidence", "project": ptitle, "field": e["field"], "value": e["value"],
                             "citation": f"{e['filename']} p.{e['page_no']}", "context": e["excerpt"]})
    scope_ids = dict(scope)
    for note in db.md_notes():                                  # 笔记页那批「大笔记」
        if scoped and note.get("task_id") not in scope_ids:
            continue
        body = (note.get("content") or "")
        if q in (note.get("title") or "").lower() or q in body.lower():
            idx = body.lower().find(q)
            hits.append({"kind": "big-note", "project": scope_ids.get(note.get("task_id"), ""), "id": note.get("id"),
                         "title": note.get("title"), "context": body[max(0, idx - 120): idx + 240]})
    return {"query": q, "hits": hits[:20], "total": len(hits)}


def _calendar_list(db: "WorkflowDB", args: dict[str, Any], project_id: str = "") -> dict[str, Any]:
    """看日程：给月份就按月，给区间就按区间，都不给就是今天。"""
    from datetime import date as _date
    month = str(args.get("month") or "").strip()
    start = str(args.get("date_from") or "").strip()
    end = str(args.get("date_to") or "").strip() or start
    if month:
        rows, label = db.events(month[:7]), month[:7]
    else:
        if not start:
            start = end = _date.today().isoformat()
        rows = [e for e in db.events() if start <= (e.get("date") or "") <= end]
        label = start if start == end else "%s ~ %s" % (start, end)
    titles = {t["id"]: t["title"] for t in db.tasks()}
    if project_id:
        rows = [e for e in rows if e.get("task_id") == project_id]
    items = [{"id": e["id"], "date": e["date"], "time": e.get("time") or "", "title": e["title"],
              "kind": e.get("kind") or "", "done": bool(e.get("done")),
              "project": titles.get(e.get("task_id") or "", "")} for e in rows]
    return {"range": label, "count": len(items), "events": items}


def _schedule_manage(db: "WorkflowDB", args: dict[str, Any], project_id: str = "") -> dict[str, Any]:
    """定时任务：list / create / toggle / delete。"""
    import schedule as sched_lib
    action = str(args.get("action") or "list").strip()
    titles = {t["id"]: t["title"] for t in db.tasks()}

    def public(row: dict[str, Any]) -> dict[str, Any]:
        return {"id": row["id"], "name": row["name"], "cron": row["cron"],
                "enabled": bool(row["enabled"]), "project": titles.get(row.get("project_id") or "", ""),
                "next_run_at": row.get("next_run_at") or "", "prompt": (row.get("prompt") or "")[:200]}

    if action == "list":
        rows = [public(r) for r in db.schedules() if not project_id or r.get("project_id") == project_id]
        return {"count": len(rows), "schedules": rows}

    if action == "create":
        name = str(args.get("name") or "").strip()
        prompt = str(args.get("prompt") or "").strip()
        cron = str(args.get("cron") or "0 9 * * *").strip()
        if not name:
            raise ValueError("定时任务要有名字")
        if not prompt:
            raise ValueError("要写清楚到点了让 Agent 做什么（prompt）")
        ok, why = sched_lib.valid(cron)
        if not ok:
            raise ValueError(why)
        ok2, folder = sched_lib.inspect_output_dir(str(args.get("output_dir") or "定时任务"))
        if not ok2:
            raise ValueError(folder)
        project = str(args.get("project") or "").strip()
        pid = project_id
        if project:
            row = _resolve_project(db, project)
            if not row:
                raise ValueError("找不到这个项目：%s" % project)
            pid = row["id"]
        if not pid:
            raise ValueError("请指定定时任务所属的项目")
        assistant_id = str(args.get("assistant_id") or "").strip()
        if not assistant_id:
            assistant_id = "builtin-steward"
        enabled = args.get("enabled", True) is not False
        saved = db.schedule_save({"name": name, "prompt": prompt, "project_id": pid,
                                  "assistant_id": assistant_id,
                                  "frequency": "cron", "cron": cron, "output_dir": folder,
                                  "enabled": enabled,
                                  "next_run_at": sched_lib.next_run_at(cron) if enabled else ""})
        return {"created": True, "schedule": public(saved)}

    sid = str(args.get("id") or "").strip()
    row = db.schedule(sid)
    if not row:
        raise ValueError("找不到这个定时任务（先 action=list 拿 id）")
    if project_id and row.get("project_id") != project_id:
        raise PermissionError("只能管理当前项目的定时任务")
    if action == "toggle":
        enabled = args.get("enabled")
        enabled = (not bool(row["enabled"])) if enabled is None else bool(enabled)
        db.schedule_set_enabled(sid, enabled)
        db.schedule_mark_run(sid, row.get("last_run_at") or "",
                             sched_lib.next_run_at(row["cron"]) if enabled else "")
        return {"toggled": True, "schedule": public(db.schedule(sid) or row)}
    if action == "delete":
        db.schedule_delete(sid)
        return {"deleted": True, "id": sid, "name": row["name"]}
    raise ValueError("action 只支持 list / create / toggle / delete")


# 子 agent 只给只读工具（写操作留在父对话手里，子 agent 不许越权）
_SUB_TOOL_NAMES = ("library_overview", "knowledge_query", "paper_search", "paper_read")
_SUB_TOOLS = [t for t in TOOL_SPECS if t.get("function", {}).get("name") in _SUB_TOOL_NAMES]


TOOL_RISK = {
    # Pure reads stay available to read-only assistants.
    "library_overview": "read_only", "knowledge_query": "read_only",
    "outline_read": "read_only", "map_read": "read_only", "map_sources": "read_only",
    "doc_read": "read_only", "cite_read": "read_only", "read_attachment": "read_only",
    "file_list": "read_only", "file_hand": "workspace_write",
    "paper_search": "network_read", "search": "network_read",
    "mail_accounts": "network_read", "mail_list": "network_read", "mail_read": "network_read",
    "zotero_collections": "network_read",
    "delegate": "local_execution", "format_convert": "local_execution",
    "paper_download": "network_read", "download": "network_read",
    "outline_write": "workspace_write", "map_write": "workspace_write", "doc_write": "workspace_write",
    "cite_add": "workspace_write", "cite_bib": "workspace_write", "memory_save": "workspace_write", "notes": "workspace_write",
    "memory": "workspace_write", "recall": "read_only", "skill_write": "workspace_write",
    "notes_compose": "workspace_write", "idea_review": "workspace_write", "paper_read": "workspace_write",
    "read": "workspace_write", "draft_write": "workspace_write", "draft_review": "workspace_write",
    "calendar_add": "workspace_write", "team_assign": "workspace_write", "team_form": "workspace_write",
    "project_create": "workspace_write", "schedule_manage": "workspace_write", "calendar_list": "read_only",
    "team_board": "read_only", "mail_send": "external_write", "feishu_send": "external_write",
    "zotero_write": "external_write", "zotero_sync": "external_write",
    "project_note_list": "read_only", "project_note_read": "read_only",
    "project_task_manage": "workspace_write", "assistant_manage": "workspace_write",
    "project_list": "read_only", "skill_read": "read_only",
    "semantic_index": "local_execution",
    "zotero_search": "read_only", "zotero_item": "read_only", "zotero_import_pdf": "workspace_write",
}


def tool_risk(name: str) -> str:
    """Return a stable policy category for every tool, including future MCP tools."""
    if name == "mcp_register":
        return "workspace_write"
    if name.startswith("mcp_"):
        return "external_write"
    return TOOL_RISK.get(name, "workspace_write")


def enforce_tool_policy(name: str, args: dict[str, Any], permission: str) -> None:
    """Apply policy at the execution boundary, not only by hiding tool schemas."""
    risk = tool_risk(name)
    action_read = name in {"assistant_manage", "project_task_manage", "schedule_manage"} and args.get("action", "list") == "list"
    connector_read = name in {"mail_accounts", "mail_list", "mail_read", "zotero_collections"}
    if permission == "read_only" and risk != "read_only" and not action_read and not connector_read:
        raise PermissionError(f"只读权限禁止 {risk} 工具：{name}")
    if risk == "external_write" and permission != "full_access":
        raise PermissionError("远程写入或外发需要用户在专家设置中开启 Full Access")


def enforce_project_scope(db: "WorkflowDB", project_id: str, name: str, args: dict[str, Any]) -> None:
    """Validate ownership before any built-in, integration or nested tool runs."""
    if not project_id or not db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (project_id,)).fetchone():
        return
    for field in ("project_id", "task_id"):
        if args.get(field) and str(args[field]) != project_id:
            raise PermissionError("项目内的工具只能操作当前项目")
    if args.get("project"):
        selected = _resolve_project(db, str(args["project"]))
        if not selected or selected["id"] != project_id:
            raise PermissionError("项目内的工具只能操作当前项目")
    if name == "project_create":
        raise PermissionError("请在未绑定项目的智研管家对话中创建新项目")
    for field in ("path", "vault", "base_dir"):
        raw = args.get(field)
        if not raw or name.startswith("project_note_") or name == "skill_read":
            continue
        root = db.base_dir(project_id)
        candidate = Path(str(raw)).expanduser()
        if root and not candidate.is_absolute():
            candidate = root / candidate
        if not root or not candidate.resolve().is_relative_to(root.resolve()):
            raise PermissionError("文件操作只允许在当前项目目录内进行")
        if field == "path":
            args[field] = str(candidate.resolve())
    for field, table in (("paper_id", "papers"), ("note_id", "notes"), ("result_id", "search_cache")):
        if not args.get(field):
            continue
        row = db.db.execute(f"SELECT task_id FROM {table} WHERE id=?", (str(args[field]),)).fetchone()
        if row and row["task_id"] != project_id:
            raise PermissionError("文献、笔记或检索结果不属于当前项目")


def _delegate(db: "WorkflowDB", task_id: str, jobs: list[dict[str, Any]], emit: Any = None,
              profile: dict[str, Any] | None = None, scope: str = "", parallel: int = 3) -> dict[str, Any]:
    """把子任务并行派给**独立上下文**的子 agent，只把摘要带回父对话。

    隔离靠三件已有机制拼出来，不新建东西：
      * 每个子 agent 一个全新的 `scope`（db.messages(task_id, scope) 因此是空的）→ 看不到父对话历史；
      * `publish=False` → 子 agent 不写对话历史、不落 assistant 回复（跟「撰写流水线内部子 agent」同一条路）；
      * 只读工具表 `_SUB_TOOLS` → 子 agent 不能改库。
    子 agent 各自**新开一条连接**（同库不同连接），事件打上 `sub`/`sub_title` 供前台分行显示。
    # ponytail: 单机 SQLite，父子各一条连接 + busy_timeout；真要高并发再上 WAL 或连接池
    """
    jobs = [j for j in (jobs or []) if str(j.get("prompt") or "").strip()][:4]
    if not jobs:
        return {"ok": False, "error": "delegate 需要至少一个子任务（jobs[].prompt）"}
    path = db.path

    sub_events: list[dict[str, Any]] = []          # 子 agent 的事件也回给父，回放（F5）时才有分行

    def one(i: int, job: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        title = str(job.get("title") or f"子任务 {i + 1}")[:40]
        sid = "sub-" + uuid.uuid4().hex[:8]
        rec: dict[str, Any] = {"i": i, "title": title, "scope": sid, "summary": "", "steps": 0, "ok": True}

        def child_emit(ev: dict[str, Any]) -> None:
            tagged = {**ev, "sub": sid, "sub_title": title}
            sub_events.append(tagged)              # 收进父的事件流 → F5 回放也能看到分行
            if emit:
                emit(tagged)

        try:
            with WorkflowDB(path) as cdb:                      # 子 agent 自己一条连接
                sub_events.append({"type": "sub", "phase": "start", "sub": sid, "sub_title": title})
                if emit:
                    emit(sub_events[-1])
                r = run_agent(cdb, task_id, str(job["prompt"]), emit=child_emit,
                              max_steps=int(job.get("max_steps") or 4), system=_DELEGATE_SYSTEM,
                              tools=_SUB_TOOLS, publish=False, scope=sid, profile=profile)
            rec["summary"] = (r.get("reply") or "").strip()[:2000]
            rec["steps"] = len(r.get("events") or [])
        except Exception as exc:                               # 一个子 agent 崩了不影响其它
            rec["ok"] = False
            rec["summary"] = f"（子任务失败：{exc}）"
        sub_events.append({"type": "sub", "phase": "done", "sub": sid, "sub_title": title,
                          "ok": rec["ok"], "summary": rec["summary"][:160]})
        if emit:
            emit(sub_events[-1])
        return i, rec

    got: dict[int, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(parallel, len(jobs)))) as ex:
        for i, rec in ex.map(lambda p: one(*p), list(enumerate(jobs))):
            got[i] = rec
    order = [got[i] for i in range(len(jobs))]
    _nl = chr(10)
    digest = (_nl + _nl).join("## " + r["title"] + _nl + (r["summary"] or "（空）") for r in order)
    return {"ok": True, "delegated": order, "digest": digest, "sub_events": sub_events,
            "hint": "把 digest 里的要点综合进你的回答；需要细节再向用户说明是哪路子 agent 给的。"}


def _tool_impl(name: str, db: "WorkflowDB", task_id: str, args: dict[str, Any], scope: str = "",
               emit: Any = None, profile: dict[str, Any] | None = None) -> Any:
    """scope = 当前步骤（''/'idea'/'outline'/'write'/'review'）—— doc_read/doc_write 据此决定动哪个文件。"""
    enforce_project_scope(db, task_id, name, args)
    scoped_project = task_id if db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (task_id,)).fetchone() else ""
    if name == "project_list":
        rows = db.db.execute("SELECT id,title,base_dir FROM research_tasks ORDER BY updated_at DESC")
        return {"current_project_id": scoped_project, "projects": [dict(row) for row in rows]}
    if name in ("project_note_list", "project_note_read", "project_note_write", "project_task_manage", "team_create", "workflow_create", "skill_create"):
        project_id = _team_project(db, task_id, str(args.get("project") or ""))
        if name.startswith("project_note_"):
            import project_notes
            if name == "project_note_list":
                return project_notes.dashboard(db, project_id)
            if name == "project_note_read":
                return project_notes.read(db, project_id, str(args.get("path") or ""))
            return project_notes.write(db, project_id, str(args.get("path") or ""), str(args.get("content") or ""))
        if name == "project_task_manage":
            import project_todos
            action = str(args.get("action") or "list")
            if action == "list":
                return {"tasks": project_todos.list_todos(db, project_id)}
            if action == "create":
                return project_todos.add_todo(db, project_id, str(args.get("title") or ""),
                                              str(args.get("description") or ""), str(args.get("priority") or "P2"),
                                              str(args.get("assistant_id") or ""), str(args.get("due_date") or ""))
            if action == "update":
                todo_id = str(args.get("id") or "")
                if not any(row["id"] == todo_id for row in project_todos.list_todos(db, project_id)):
                    raise ValueError("这个待办不属于当前项目")
                fields = {k: args[k] for k in ("title", "description", "status", "priority", "due_date") if k in args}
                if "status" in fields:
                    fields["status"] = str(fields["status"]).upper()
                    if fields["status"] not in project_todos.STATUSES:
                        raise ValueError("任务状态必须是 TODO / IN_PROGRESS / REVIEW / DONE")
                if "assistant_id" in args:
                    fields["assigned_assistant"] = args["assistant_id"]
                return project_todos.update_todo(db, todo_id, **fields)
            raise ValueError("action 只支持 list/create/update")
        if name == "team_create":
            ids = args.get("assistant_ids") or ["builtin-steward"]
            if not isinstance(ids, list) or len(ids) > 12 or len(set(ids)) != len(ids):
                raise ValueError("团队需指定 1–12 个不重复的专家 ID")
            assistants = [db.require_assistant(str(aid)) for aid in ids]
            team_row = db.team_add(str(args.get("name") or ""), project_id=project_id,
                                   purpose=str(args.get("purpose") or ""))
            members = []
            for index, assistant in enumerate(assistants):
                members.append(db.team_member_add(team_row["id"], assistant["name"],
                                                  role="leader" if index == 0 else "member",
                                                  assistant_id=assistant["id"]))
            return {"team": team_row, "members": members}
        if name == "workflow_create":
            import workflow_layout
            workflow_layout.ensure_schema(db)
            steps = [str(s) for s in (args.get("steps") or [])]
            definition = workflow_layout._linear(steps)
            saved = workflow_layout.save_layout(db, {"name": str(args.get("name") or ""),
                                                       "description": str(args.get("description") or ""), **definition})
            workflow_layout.select_layout(db, {"task_id": project_id, "layout_id": saved["layout"]["id"]})
            return saved
        if name == "skill_create":
            import asset_service
            import project_notes
            skill_name = str(args.get("name") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", skill_name):
                raise ValueError("Skill 名称只允许字母、数字、下划线和横线")
            root = project_notes.project_root(db, project_id)
            target = root / ".agents" / "skills" / skill_name / "SKILL.md"
            if not target.resolve().is_relative_to(root):
                raise ValueError("Skill 路径越过项目文件夹")
            if target.exists():
                raise ValueError("同名 Skill 已存在")
            description = str(args.get("description") or "").replace("\n", " ")[:300]
            instructions = str(args.get("instructions") or "")
            content = f"---\nname: {skill_name}\ndescription: {json.dumps(description, ensure_ascii=False)}\n---\n\n{instructions}\n"
            if len(content.encode("utf-8")) > 1024 * 1024:
                raise ValueError("Skill 超过 1 MB")
            target.parent.mkdir(parents=True, exist_ok=True)
            asset_service.atomic_write_text(target, content)
            return {"name": skill_name, "path": str(target)}
    if name == "assistant_manage":
        action = str(args.get("action") or "list")
        if action == "list":
            return {"assistants": [{"id": a["id"], "name": a["name"], "enabled": a["enabled"]} for a in db.assistants()]}
        if action not in ("create", "update"):
            raise ValueError("action 只支持 list/create/update")
        aid = str(args.get("id") or "") if action == "update" else uuid.uuid4().hex
        if action == "update" and not db.assistant(aid):
            raise ValueError("专家不存在")
        fields = {k: args[k] for k in ("name", "rules", "description", "model_id") if k in args}
        return {"assistant": db.assistant_save(aid, **fields)}
    if name == "mcp_register":
        import capabilities
        server_name = str(args.get("name") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", server_name):
            raise ValueError("MCP 名称格式无效")
        cfg = capabilities._config(db)
        if server_name in cfg["mcpServers"]:
            raise ValueError("同名 MCP 已存在")
        url, command = str(args.get("url") or ""), str(args.get("command") or "")
        if bool(url) == bool(command):
            raise ValueError("MCP 需要且只需要 URL 或 command")
        cfg["mcpServers"][server_name] = ({"url": url, "transport": "streamable-http", "enabled": False}
                                           if url else {"command": command, "args": args.get("args") or [],
                                                        "transport": "stdio", "enabled": False})
        capabilities.save_mcp(db, cfg)
        return {"name": server_name, "enabled": False}
    if name == "delegate":
        return _delegate(db, task_id, args.get("jobs") or [], emit=emit, profile=profile,
                         scope=scope, parallel=int(args.get("parallel") or 3))
    if name in ("team_board", "team_assign", "team_form"):
        import team as _team                                  # 延迟导入：team 反向依赖 workflow
        project_id = _team_project(db, task_id, args.get("project", ""))
        tid = str(args.get("team_id") or project_id)
        if args.get("team_id"):
            selected_team = db.team(tid)
            if not selected_team or selected_team.get("project_id") != project_id:
                raise PermissionError("团队不属于当前项目")
        if name == "team_board":
            return {**_team.board(tid), "team_id": tid,
                    "teams": [row for row in db.teams() if row.get("project_id") == project_id]}
        if name == "team_form":
            return _team.form_team(project_id, args.get("purpose", ""), args.get("members") or [])
        return _team.assign(tid, args.get("member", ""), args.get("prompt", ""), trigger="leader",
                            origin="leader@" + task_id[:8], depends_on=args.get("depends_on"),
                            output_contract=args.get("output_contract"), reviewer_id=args.get("reviewer_id", ""))
    if name == "memory":
        return memory_store.apply(db, task_id, args)
    if name == "recall":
        q = str(args.get("query") or "")
        found = memory_store.recall(db, q, task_id, int(args.get("limit") or 8))
        try:                                   # 笔记/证据的原文块也一起给（那里带页码出处）
            found["notes"] = _knowledge_query(db, task_id, q)["hits"][:8]
        except Exception:
            found["notes"] = []
        return found
    if name == "memory_save":                 # 老工具名：老对话/老提示词还在用，等价于 memory add
        return memory_store.add(db, args.get("text", ""), scope_id=task_id,
                                kind="episode" if args.get("kind") == "short" else "fact",
                                source="agent", importance=2)
    if name == "library_overview":
        return _overview(db, task_id)
    if name == "knowledge_query":
        return _knowledge_query(db, task_id, args.get("query", ""), str(args.get("project") or ""))
    if name == "semantic_index":
        import semantic
        if str(args.get("action") or "status") == "sync":
            return semantic.sync(db, limit=int(args.get("limit") or 0))
        return semantic.status(db)
    if name == "calendar_list":
        return _calendar_list(db, args, scoped_project)
    if name == "schedule_manage":
        return _schedule_manage(db, args, scoped_project)
    if name == "project_create":
        title = str(args.get("title") or "").strip()
        if not title:
            raise ValueError("新建项目至少要有标题")
        base = str(args.get("base_dir") or "").strip()
        if base:
            paths.validate_project_dir(base)
        if base and not paths.grant_dir(base):
            raise PermissionError("该目录尚未由文件夹选择器授权，请在项目界面选择目录")
        tid = db.create_task(title, str(args.get("question") or ""), str(args.get("material_system") or ""), base)
        made = make_project_tree(base, db.summary(tid)["task"]) if base else []
        return {"project_id": tid, "title": title, "base_dir": str(db.base_dir(tid) or ""),
                "dirs": made or [name for name, _ in PROJ_DIRS]}
    if name == "calendar_add":
        return db.add_event(args.get("title", ""), args.get("date", ""), task_id, args.get("kind", "milestone"),
                            time=args.get("time", ""))
    if name in ("search", "paper_search"):
        return stage_search(db, task_id, args.get("query", ""), args.get("sources"),
                            int(args.get("rows", 30)),
                            limit=int(args.get("limit") or 100),
                            sort_by=str(args.get("sort_by") or "composite"))
    if name == "paper_download":
        return stage_download(db, task_id, args.get("result_id", ""))
    if name == "download":
        return stage_download(db, task_id, args.get("result_id", ""))
    if name == "paper_read":
        return stage_read(db, task_id, args.get("paper_id", ""))
    if name == "read":
        return stage_read(db, task_id, args.get("paper_id", ""))
    if name == "notes_compose":
        return stage_notes(db, task_id, args.get("note_id", ""), args.get("vault") or None)
    if name == "notes":
        return stage_notes(db, task_id, args.get("note_id", ""))
    if name == "idea_review":
        return stage_idea(db, task_id, args.get("idea", ""))
    if name == "outline_write":
        return outline_write(db, task_id, args.get("markdown", ""))
    if name == "outline_read":
        return outline_read(db, task_id)
    if name == "map_write":
        return map_write(db, task_id, args.get("data") or {})
    if name == "map_read":
        return map_read(db, task_id)
    if name == "map_sources":
        return map_sources(db, task_id)
    if name == "doc_write":
        return doc_write(db, task_id, scope or "outline", args.get("markdown", ""))
    if name == "doc_read":
        return doc_read(db, task_id, scope or "outline")
    if name == "cite_add":
        return cite_add(db, task_id, args)
    if name == "cite_read":
        return cite_read(db, task_id)
    if name == "cite_bib":
        return cite_bib(db, task_id, args)
    if name == "read_attachment":
        return read_attachment(args.get("path", ""), int(args.get("chars") or 8000))
    if name == "file_list":                                   # 项目文件夹：看一层目录
        return tree_list(db, task_id, str(args.get("rel") or ""))
    if name == "file_hand":                                   # 把文件转到某一步的对话里
        return file_hand(db, task_id, str(args.get("rel") or ""), str(args.get("step") or ""),
                         str(args.get("note") or ""))
    if name == "draft_write":
        return stage_write(db, task_id, args.get("title", ""), args.get("section", "引言"))
    if name == "draft_review":
        return stage_review(db, task_id, args.get("draft") or compose_draft(db, task_id))
    if name == "format_convert":
        fmt = (args.get("format") or "docx").lower()
        if fmt in ("pdf", "latex", "tex"):        # PDF / LaTeX 走本机 MiKTeX（正文 md→tex→pdf，或直接编现成 .tex）
            return conv_run(db, task_id, fmt, str(args.get("source") or ""),
                           str(args.get("template") or ""), str(args.get("cite_style") or ""))
        return stage_convert(db, task_id, args.get("markdown") or compose_draft(db, task_id), fmt, args.get("name", "paper"))
    raise ValueError(f"未知工具：{name}")


from workflow_documents import (
    doc_path, doc_read, doc_write,
    outline_read, outline_write, doc_tools, map_tools, doc_seed, read_attachment, map_path, map_read, map_write, map_sources,
    map_seed, project_outs, map_tidy, open_write,
)
from workflow_runners import (
    run_idea, run_map, run_outline, run_write, run_review, _wants_draft,
)



def _team_project(db: "WorkflowDB", task_id: str, raw: str) -> str:
    """团队派活的落点项目：给了名字/ID 用它；没给就用当前 task（项目对话时就是该项目）。
    首页通用对话没有项目 → 必须点名，否则直接报错（不许瞎猜一个项目去执行）。"""
    raw = (raw or "").strip()
    tasks = {t["id"]: t for t in db.tasks()}
    if raw:
        if raw in tasks:
            return raw
        low = raw.lower()
        for tid, t in tasks.items():
            if low and low in (t["title"] or "").lower():
                return tid
        raise ValueError("没有叫「%s」的项目（现有：%s）" % (raw, "、".join(t["title"] for t in tasks.values()) or "空"))
    row = db.db.execute("SELECT id FROM chats WHERE id=?", (task_id,)).fetchone()
    if row:
        raise ValueError("这是首页通用对话，没绑项目 —— 请用 project 参数指明要派给哪个项目")
    return task_id


def _global_brief(db: "WorkflowDB") -> str:
    """智研管家 的简报：有哪些项目、各自走到哪、库里有多少东西。
    首页 Agent 以前完全不知道项目存在（ctx 只说「没有绑定任何项目」）—— 这就是 Leader 缺的视野。"""
    lines: list[str] = []
    for t in db.tasks():
        tid = t["id"]
        try:
            papers = db.db.execute("SELECT COUNT(*) AS c FROM papers WHERE task_id=?", (tid,)).fetchone()["c"]
            notes = db.db.execute("SELECT COUNT(*) AS c FROM notes WHERE task_id=?", (tid,)).fetchone()["c"]
        except Exception:
            papers = notes = 0
        lines.append(f"  · {t['title']}｜阶段 {db.stage_now(tid, t['stage'])}｜文献 {papers}｜笔记 {notes}｜更新 {(t['updated_at'] or '')[:16]}")
    out = ("\n【全局简报】共 %d 个项目：%s" % (len(lines), ("\n" + "\n".join(lines)) if lines else "（还没有项目 —— 用 project_create 工具新建一个再往下走）"))
    try:
        out += "\n【大笔记】%d 篇（笔记页那批）" % len(db.md_notes())
    except Exception:
        pass
    return out


def agent_profile(db: "WorkflowDB", task_id: str) -> dict[str, Any] | None:
    """这个对话该用哪个模型：**绑了助手就用助手接的那个模型**（deepseek 助手跑 deepseek、gpt 助手跑 gpt），
    没绑（或助手没指定）就退回当前激活模型。项目工作流仍用激活模型。"""
    try:
        row = db.db.execute("SELECT assistant_id FROM chats WHERE id=?", (task_id,)).fetchone()
    except Exception:
        return db.active_model()
    aid = (row["assistant_id"] if row else "") or ""
    if aid:
        a = db.require_assistant(aid)
        if a and a.get("model_id"):
            try:
                m = db.model(a["model_id"])
            except KeyError:
                raise ValueError("助手绑定的模型已移除，请重新选择模型") from None
            if m and m.get("base_url") and m.get("model"):
                return m
    return db.active_model()


STEP_AGENT = {                       # 十步各有一个助手身份（跟前端 WF_AGENT 同名，改一处两边对齐）
    "idea": "创新点审查助手", "search": "文献检索助手", "download": "入库助手", "notes": "翻译助手",
    "outline": "框架助手", "map": "谱系助手", "write": "写作助手", "review": "审查助手",
    "entrans": "中译英助手", "convert": "格式助手",
}

# 检索跑完要主动收口（问用户还改不改检索式）：提示词已要求，这里再兜一层——模型偶尔只回一句「检索完了」。
SEARCH_CLOSING = ("\n\n检索完毕。还要调整检索式吗——加点或减点关键词，"
                  "或者按年份、期刊、只看开放获取再筛一遍？")


def _plan_context(db: "WorkflowDB", tid: str, step: str) -> str:
    """写「本步计划」时给模型的料：项目底子 + 上一步的交付 + 库里已有啥。

    顺序就是发言顺序：**上一步的交付排在最前**（它才是这一步要同步的对象），
    项目最初的创新点降到最后当背景 —— 以前把它排前面，每个助手都开口就复述创新点审查。
    """
    task = (db.summary(tid) or {}).get("task") or {}
    parts = ["项目：%s\n研究问题：%s\n材料体系：%s" % (
        task.get("title") or "", task.get("question") or "（没填）", task.get("material_system") or "（没填）")]
    i = STAGES.index(step) if step in STAGES else 0
    prev = STAGES[i-1] if i else ""
    if prev:
        label = STAGE_LABEL.get(prev, prev)
        pm = db.messages(tid, prev)
        if pm:
            parts.append("【上一步「%s」最后的结论 —— 你这一条要同步的就是它】\n%s"
                         % (label, (pm[-1].get("content") or "")[:1500]))
        if prev in ("outline", "write", "review"):
            body = (doc_read(db, tid, prev) or {}).get("markdown") or ""
            if body:
                parts.append("【上一步「%s」落下的正文】\n%s" % (label, body[:3000]))
        pl = db.papers_of(tid) or []
        if pl:
            parts.append("【项目文献库里已有的】\n" + "\n".join(
                "- %s (%s)" % (p.get("title") or "", p.get("year") or "") for p in pl[:8]))
        ns = db.notes(tid) or []
        if ns:
            parts.append("【项目笔记】\n" + "\n".join("- %s" % (n.get("title") or "") for n in ns[:6]))
    mine = (db.idea_input(tid) or "").strip()
    v = db.idea_verdict(tid) or {}
    if mine or v.get("markdown"):
        bg = ["【背景：项目最初的创新点，只作背景，不要当成上一步来同步】"]
        if mine:
            bg.append("用户原文：" + mine[:600])
        if v.get("markdown"):
            bg.append("判定摘要：" + v["markdown"][:600])
        parts.append("\n".join(bg))
    return "\n\n".join(parts)


def step_done(db: "WorkflowDB", task_id: str, step: str) -> bool:
    """这一步交付过没有：steps 台账里该步出现过 done 就算（跟 current_stage_key 一个口径）。"""
    try:
        return any(r.get("stage") == step and r.get("status") == "done" for r in db.steps(task_id))
    except Exception:
        return False


def step_delivery(db: "WorkflowDB", task_id: str, step: str) -> dict[str, Any]:
    """这一步「交付了什么」—— 交付那一刻现算一份记录，落进 steps.delivery。

    全从库和文件里读，不采信模型自报（模型说「已交付 12 篇」不算数）。
    下一步的同步、交接、事后追查都看这份，而不是重算一遍猜。
    """
    items: dict[str, Any] = {}
    files: list[str] = []
    try:
        if step == "idea":
            items["创新点原文"] = "%d 字" % len((db.idea_input(task_id) or "").strip())
            items["判定报告"] = "%d 字" % len((db.idea_verdict(task_id) or {}).get("markdown") or "")
        elif step == "search":
            rs = db.results(task_id) or []
            items["检索结果"] = "%d 条" % len(rs)
            items["来源"] = "、".join(sorted({r.get("source") or "" for r in rs if r.get("source")})) or "—"
        elif step == "download":
            ps = db.papers_of(task_id) or []
            items["入库论文"] = "%d 篇" % len(ps)
            items["最近入库"] = "；".join((p.get("title") or p.get("filename") or "")[:40] for p in ps[:3]) or "—"
        elif step == "notes":
            items["项目笔记"] = "%d 条" % len(db.notes(task_id) or [])
        elif step in ("outline", "write", "review"):
            d = doc_read(db, task_id, step)
            items["文件"] = Path(d["path"]).name if d.get("path") else "—"
            items["字数"] = "%d" % d.get("chars", 0)
            items["结构"] = "%d 节 / %d 小节" % (d.get("sections", 0), d.get("subs", 0))
            if d.get("exists"):
                files.append(d["path"])
        elif step == "map":
            m = map_read(db, task_id)
            items["谱系节点"] = "%d 个（正文 %d / 资料 %d）" % (
                len(m.get("nodes") or []), m.get("sections", 0), m.get("refs", 0))
            items["连线"] = "%d 条" % len(m.get("edges") or [])
            if m.get("exists"):
                files.append(m["path"])
        elif step == "entrans":
            e = en_state(db, task_id)
            items["原文"] = "%d 字" % len(e.get("source_text") or "")
            items["译文"] = "%d 字" % len(e.get("result_text") or "")
            if e.get("result_exists"):
                files.append(e["result_path"])
        elif step == "convert":
            got = conv_files(db, task_id) or {}
            fs = got.get("files") or []
            items["产物文件"] = "%d 个" % len(fs)
            items["最新"] = fs[0]["name"] if fs else "—"
            files += [f["path"] for f in fs[:5] if f.get("path")]
    except Exception as exc:                       # 记产物失败不能连累交付
        items["读取产物出错"] = "%s: %s" % (type(exc).__name__, str(exc)[:80])
    msgs = db.messages(task_id, step) or []
    last = next((m.get("content") or "" for m in reversed(msgs) if m.get("role") == "assistant"), "")
    return {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "step": step,
        "step_label": STAGE_LABEL.get(step, step),
        "items": items,
        "files": files,
        "turns": len([m for m in msgs if m.get("role") == "user"]),
        "last_reply": last[:200],
    }


def step_plan(db: "WorkflowDB", task_id: str, step: str) -> dict[str, Any]:
    """每一步的「进度同步 + 本步计划」：让这一步的助手看着上一步的交付**现写**一条，
    落成该步对话的第一条 assistant 消息（events=[{type:plan}]）。已有就复用，不重复烧模型。

    硬限制：上一步没交付（台账里没有 done）→ 这一步此刻不是流程步，只是独立功能块，
    直接返回 blocked，既不写计划也不落库 —— 否则空计划会被缓存，上一步补完也不会重写。
    """
    step = step if step in STAGES else "idea"
    i = STAGES.index(step) if step in STAGES else 0
    if i and not step_done(db, task_id, STAGES[i-1]):
        prev = STAGES[i-1]
        return {"text": "", "cached": False, "blocked": True, "prev": prev,
                "prev_label": STAGE_LABEL.get(prev, prev)}
    msgs = db.messages(task_id, step)
    if msgs and msgs[-1].get("role") == "assistant" and any(
            isinstance(e, dict) and e.get("type") == "plan" for e in (msgs[-1].get("events") or [])):
        return {"text": msgs[-1].get("content") or "", "cached": True}
    who, label = STEP_AGENT.get(step, ""), STAGE_LABEL.get(step, step)
    i = STAGES.index(step) if step in STAGES else 0
    prev_label = STAGE_LABEL.get(STAGES[i-1], STAGES[i-1]) if i else ""
    sync_target = f"上一步「{prev_label}」" if prev_label else "项目最初的定义"
    system = (
        f"你在一套科研工作流里当「{who}」，现在轮到「{label}」这一步，只负责这一步的事。\n"
        f"用户刚确认完{sync_target}。先用一句话说清你从{sync_target}同步到了什么"
        "（要点到具体结论或数字，别写空话，**不要复述项目最初的创新点**，那是背景不是上一步），"
        "再用 3-5 条列出这一步的具体计划：要检索什么词 / 看哪些数据 / 改哪几节，都要写具体，"
        "不许写「进行分析」「进一步研究」这种空话；最后一句问他按这个来行不行。\n"
        "只写计划，不要执行任何操作、不要调用工具、不要寒暄、不要重复项目背景。")
    prof = agent_profile(db, task_id) or {}
    try:
        text = (chat(prof, [{"role": "system", "content": system},
                            {"role": "user", "content": _plan_context(db, task_id, step)}],
                     temperature=0.3, timeout=180, max_tokens=900) or "").strip()
    except Exception as exc:
        return {"text": "", "cached": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
    if not text:
        return {"text": "", "cached": False, "error": "模型没有返回内容"}
    db.add_message(task_id, "assistant", text, [{"type": "plan"}], scope=step)
    return {"text": text, "cached": False}



def _system_for(who: str, system: str | None) -> str:
    """第一段 system。十步各有助手身份时，把 AGENT_SYSTEM 开头那两句「你叫览籍智研 / 答：你是览籍智研」
    换成本步的名字 —— 只加在后面 ctx 里没用，模型会照第一行复述。其余规则原样保留。"""
    base = system or AGENT_SYSTEM
    if not who or system:
        return base
    return base.replace(
        "你叫「览籍智研」，是这个软件的主 Agent，也是一位做科研的研究生的科研助手",
        "你叫「%s」，是这个软件里负责这一步的副手，服务对象是做科研的研究生" % who, 1,
    ).replace(
        "用户怎么问「你是谁」就怎么答：你是览籍智研，他的科研助手。",
        "用户怎么问「你是谁」就怎么答：你是%s。" % who, 1,
    )


HISTORY_CHARS = 40_000      # 默认历史预算；设置页「权限 → 上下文预算」存了就按存的来


def context_budget(db: Any = None) -> int:
    """Agent 每次对话喂多少字历史：设置页存的值优先，没存/值不合法用默认。"""
    if db is not None:
        try:
            import service_settings as kv
            chars = int(kv.read(db, "security").get("history_chars") or 0)
        except (ImportError, OSError, ValueError, TypeError, KeyError):
            chars = 0                          # KV 表没建 / 值坏了 → 用默认，别让整个对话跑不起来
        if 2_000 <= chars <= 400_000:
            return chars
    return HISTORY_CHARS


def recent_dialogue(messages: list[dict[str, Any]], chars: int = HISTORY_CHARS) -> list[dict[str, Any]]:
    """从最新往前按字符预算装历史，装不下就停（至少留最新一条）。

    为什么不用「最近 N 条」：一条长判定能顶十条短问答，卡死条数会让上下文时多时少 ——
    短问答时浪费额度，长材料时又把最早的原文挤掉。
    """
    picked: list[dict[str, Any]] = []
    used = 0
    for m in reversed(list(messages)):
        n = len(str(m.get("content") or ""))
        if picked and used + n > chars:
            break
        picked.append(m)
        used += n
    picked.reverse()
    return picked


def run_agent(db: "WorkflowDB", task_id: str, text: str, emit: Any = None, max_steps: int = 6,
              system: str | None = None, tools: list[dict[str, Any]] | None = None,
              extra_context: str = "", scope: str = "", publish: bool = True,
              profile: dict[str, Any] | None = None, assistant_id: str = "",
              member: dict[str, Any] | None = None, step: str = "", cancel_check: Any = None) -> dict[str, Any]:
    """工具调用循环。emit(event) 每产生一条事件调用一次（SSE 用）。
    system/tools 不传就是通用 Agent（AGENT_SYSTEM + 全量工具）；创新点审查/确认大纲那两步会传受限工具 + 记忆块。
    scope 决定这轮对话和哪一步共用历史（'' = 创新点审查/通用，'outline' = 确认大纲）。
    publish=False = 后台子 agent（文章撰写流水线内部用）：不写对话历史、不落 assistant 回复、不发 done，
    工具调用与文本照旧流给前台看进度 —— 由调用方统一收尾。"""
    say = emit or (lambda _e: None)
    import agent_settings
    import capabilities
    import agent_integrations
    cfg = agent_settings.runtime_config(db, task_id, assistant_id or (profile or {}).get("assistant_id", ""), member)
    if profile is None:
        profile = db.model(cfg["model_id"]) if cfg["model_id"] else agent_profile(db, task_id)
    profile = dict(profile or {})
    for key in ("reasoning", "permission"):
        profile.setdefault(key, cfg[key])
    permission = profile.get("permission", "auto")
    aid = cfg["assistant_id"]
    profile["assistant_id"] = aid
    if publish:
        db.add_message(task_id, "user", text, scope=scope)
        say({"type": "user", "content": text})
    task = db.summary(task_id)["task"]
    chat_row = db.db.execute("SELECT * FROM chats WHERE id=?", (task_id,)).fetchone()
    bound = ""                                     # 这个对话绑的项目（'' = 随意对话）
    if chat_row:
        try:
            bound = (chat_row["task_id"] or "").strip()
        except Exception:
            bound = ""
        if bound and not db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (bound,)).fetchone():
            raise ValueError("绑定的项目已不可用，请重新选择项目")
    elif db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (task_id,)).fetchone():
        bound = task_id
    ctx_id = bound or task_id                       # 工具按这个 id 干活：绑了项目就落在项目里
    if publish and bound:                           # 用户当场定的口径：不等下轮，先记成一条约束
        try:
            memory_store.capture(db, bound, text)
        except Exception:
            pass
    if bound:
        root = db.base_dir(bound)
        if not root:
            raise ValueError("绑定项目尚未设置文件夹，请重新选择项目")
        paths.validate_project_dir(root)
    import run_service
    tracked_run = run_service.start(db, ctx_id, "agent", idempotency_key="")
    tracked_node = run_service.node_start(db, tracked_run["id"], scope or "agent")
    if chat_row and bound:                        # 绑了项目 → Partner Agent（对话历史独立，视野与产出跟项目走）
        pt = db.summary(bound)["task"]
        ctx = (f"你是《{pt['title']}》项目的 **Partner Agent**：这个对话绑在该项目上 —— 对话历史自己一份，"
               f"但文献/笔记/记忆/产出都按项目来。\n"
               f"当前项目：{pt['title']}\n研究问题：{pt['question']}\n关键词 / 材料体系：{pt['material_system']}\n"
               f"项目 ID：{bound}\n产出目录：{db.pdir(bound, '其余')}")
        try:
            ctx += "\n\n" + db.memory_block(bound)     # 项目的长期/短期记忆
        except Exception:
            pass
    elif chat_row:  # 首页 Agent = 智研管家：不绑项目，但有全局视野
        ctx = ("你叫「览籍智研」，是这个软件的主 Agent（首页就叫「智研管家」），用户的科研助手。"
               "有人问「你是谁」，答：我是览籍智研，你的科研助手。\n"
               "你是 **智研管家**（首页 Agent，不属于任何单个项目）：可以跨项目工作 —— 查任意项目、"
               "任意文献/笔记/日程，也能新建项目、加日程、检索下载精读文献、写笔记。\n"
               "项目工作流里跑的是 **绑定单个项目的 Partner Agent**；要在某个项目内部推进，就按「哪个项目、要什么产出」把话说清，"
               "或建议用户进那个项目的「项目空间 / 工作流」。\n"
               "你也可以用 `team_board` 看某项目团队的成员与执行状态、用 `team_assign` 直接把活派给某个项目的 Partner Agent"
               "（它们排队执行，产出落在该项目目录的「团队/」里）。\n"
               f"这是通用对话「{task['title']}」，产出目录：{db.out_dir(task_id)}" + _global_brief(db))
    else:
        ctx = (f"你是《{task['title']}》项目的 **Partner Agent**：绑定单个项目，只在这个项目范围内工作；"
               "跨项目的事让用户回首页 Agent（智研管家）做。\n"
               f"当前项目：{task['title']}\n研究问题：{task['question']}\n"
               f"关键词 / 材料体系：{task['material_system']}\n项目 ID：{task_id}\n产出目录：{db.out_dir(task_id)}")
        ctx += "\n\n" + db.memory_block(task_id)
    # builtin-steward 的 rules 就是 AGENT_SYSTEM，首页/通用对话上面已经整段写过了，再套一层【当前助手】等于把同一段话重复一遍
    if aid and aid != "builtin-steward":   # Also applies to each team's selected assistant.
        try:
            asst = db.require_assistant(aid)
        except Exception:
            asst = None
        if asst:
            ctx += "\n\n【当前助手】%s（%s）\n规则：%s" % (
                asst["name"], "公开内置" if asst["source"] == "builtin" else "我的助手", asst["rules"] or "（没写规则）")
    who = STEP_AGENT.get(step or scope or "", "")
    if who:      # 工作流里的每一步都是一个独立助手，别让它自称「览籍智研 / 智研管家 / Partner Agent」
        ctx += (f"\n\n【你的身份】这一步你是「{who}」，只负责这一步的事；上面提到的「览籍智研 / 智研管家 / Partner Agent」是"
                f"整台机器和首页的称呼，不是你的自称。有人问「你是谁」，就答「我是{who}」，再用一句话说这一步负责什么。")
    if step == "search":     # 检索完必须向用户收口 —— 只甩一串结果，用户不知道还能不能改检索式
        ctx += ("\n\n【本步收口】检索（paper_search）跑完或改关键词重跑完，最后一段必须收口："
                "这次命中多少条、其中多少条有开放获取全文、入库多少条，然后问一句"
                "「还要调整检索式吗——加点或减点关键词，或者按年份、期刊、只看开放获取再筛一遍？」"
                "不许只报个数就结束，也别替用户决定下一步。")
    if extra_context:
        ctx += "\n\n" + extra_context
    project_dir = str((db.summary(bound or task_id).get("task") or {}).get("base_dir") or "")
    ctx += "\n\n" + capabilities.skill_context(db, aid, project_dir)
    if tools is None:
        ctx += capabilities.steward_context(db, aid, project_dir)
    ctx += ("\n\n【记忆纪律】记忆分两层：全局（跨项目的口径/偏好，scope=global）与项目（这个项目的事实、决定、约束）。\n"
            "- 值得留下的结论、用户偏好、导师要求、口径 → 用 memory 工具记（add；同一件事改已有的用 patch，不堆重复）。\n"
            "- 用户纠正你、或当场定了规矩（「以后都…」「不要…」）→ 立刻 memory add(kind=constraint)，别等下一轮。\n"
            "- 「做法/流程/踩坑」不是记忆，是 skill → skill_write 写进 SKILL.md；下次同类任务自己会被索引到。\n"
            "- 要用到以前说过的事 → 先 recall 检索（记忆 + 历史对话 + 笔记），不要凭印象编。\n"
            "- 记忆块满了会提示条数；要腾地方就 patch 合并或 archive，不要直接放弃旧约束。")
    ctx += "\n\n权限模式：%s。%s" % (permission, {
        "auto": "Choose actions for the user goal. Sending mail or messages and remote writes require user enabled Full Access.",
        "read_only": "只允许只读工具，禁止写入文件、数据库、邮件或外部服务。",
        "workspace_write": "You may change workspace content. Sending mail or messages and remote writes require user enabled Full Access.",
        "full_access": "危险：用户授予完全访问权限，可使用所有已启用工具和外部写入，不再要求逐次确认；谨慎处理不可逆操作。",
    }[permission])
    history = [{"role": m["role"], "content": m["content"]}
               for m in recent_dialogue(db.messages(task_id, scope or "idea")[:-1], context_budget(db))]
    tool_specs = list(TOOL_SPECS if tools is None else tools)
    if tools is None:
        tool_specs += agent_integrations.tool_specs()
        tool_specs += capabilities.capability_tool_specs(db, aid, project_dir)
    elif any(t.get("function", {}).get("name") in ("skill_read", "skill_write")
             for t in capabilities.capability_tool_specs(db, aid, project_dir)):
        tool_specs += [t for t in capabilities.capability_tool_specs(db, aid, project_dir)
                       if t.get("function", {}).get("name") in ("skill_read", "skill_write")]
    tool_specs = capabilities.filter_tools(db, aid, tool_specs, project_dir)
    if bound and permission != "full_access":
        tool_specs = [t for t in tool_specs if t.get("function", {}).get("name") == "mcp_register"
                      or not t.get("function", {}).get("name", "").startswith("mcp_")]
    if permission == "read_only":
        tool_specs = [t for t in tool_specs if t.get("function", {}).get("name") in agent_settings.READ_TOOLS
                      or t.get("function", {}).get("name") in {"project_list", "zotero_search", "zotero_item", "assistant_manage", "project_task_manage", "schedule_manage"}]
    tool_specs = list({t["function"]["name"]: t for t in tool_specs}.values())
    allowed_names = {t.get("function", {}).get("name") for t in tool_specs}
    integration_names = {t.get("function", {}).get("name") for t in agent_integrations.tool_specs()}
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_for(who, system)},
        {"role": "system", "content": ctx},
        *history, {"role": "user", "content": text},
    ]
    events: list[dict[str, Any]] = []
    final = ""
    tokens = {"prompt": 0, "completion": 0, "total": 0, "exact": True}
    for turn in range(max_steps):   # 别用 step：它在上面是本步的名字（助手身份/收口规则都靠它）
        if cancel_check and cancel_check():
            run_service.finish_node(db, tracked_node["id"], "failed", error="运行已中断")
            run_service.finish(db, tracked_run["id"], "failed", error="运行已中断")
            raise RuntimeError("运行已中断")
        partial: list[str] = []
        reasoning_parts: list[str] = []
        reasoning_id = f"{tracked_run['id']}:{turn}"
        model_started = time.monotonic()
        say({"type": "reasoning_start", "id": reasoning_id})
        def forward_reasoning(piece: str) -> None:
            reasoning_parts.append(piece)
            say({"type": "reasoning_delta", "id": reasoning_id, "content": piece})
        def forward_delta(piece: str) -> None:
            partial.append(piece)
            say({"type": "assistant_delta", "content": piece})
        try:
            msg = chat_raw(profile, messages, tools=tool_specs,
                           # 写作这步的产出（整份正文）就是一个大参数：4096 预算下推理模型的思维链把额度吃光，
                           # doc_write 的 markdown 直接被截断 → 工具调用变成「空参数」，文件被清空、左边永远空白。
                           max_tokens=WRITE_MAX_TOKENS if scope == "write" else 4096,
                           on_delta=forward_delta if emit else None, cancel_check=cancel_check,
                           on_reasoning=forward_reasoning if emit else None)
        except Exception as exc:
            say({"type": "reasoning_done", "id": reasoning_id, "content": "".join(reasoning_parts),
                 "duration_ms": int((time.monotonic() - model_started) * 1000)})
            if cancel_check and cancel_check() and publish and partial:
                db.add_message(task_id, "assistant", "".join(partial), scope=scope)
            run_service.finish_node(db, tracked_node["id"], "failed", error=str(exc))
            run_service.finish(db, tracked_run["id"], "failed", error=str(exc))
            raise
        thought = msg.get("reasoning") or "".join(reasoning_parts)
        reasoning_event = {"type": "reasoning_done", "id": reasoning_id,
                           "content": thought if isinstance(thought, str) else "",
                           "duration_ms": int((time.monotonic() - model_started) * 1000)}
        say(reasoning_event)
        if reasoning_event["content"]:
            events.append(reasoning_event)
        use = msg.get("usage") or {}
        if use:
            # 每次回包发「这一次」的用量（可累加），不发累计值——否则存进事件流后回放会重复计数
            delta = {"prompt": use["prompt"], "completion": use["completion"],
                     "total": use["total"] or (use["prompt"] + use["completion"]), "exact": bool(use["exact"])}
            tokens["prompt"] += delta["prompt"]; tokens["completion"] += delta["completion"]
            tokens["total"] += delta["total"]; tokens["exact"] = tokens["exact"] and delta["exact"]
            ev = {"type": "usage", **delta}
            say(ev); events.append(ev)
        calls = msg.get("tool_calls") or []
        if (not calls and step == "search"
                and any(e.get("type") == "tool_result" and e.get("name") == "paper_search" for e in events)
                and "？" not in (msg.get("content") or "") and "?" not in (msg.get("content") or "")):
            # 检索跑完了却没问「还改不改」→ 补一句（它自己问过就不重复问）
            msg = {**msg, "content": (msg.get("content") or "").rstrip() + SEARCH_CLOSING}
        if not calls and msg.get("content"):
            # 证据闸门：本轮工具返回里找不到出处的数字，明说出来（不改模型原文，只追加一行）
            import verify
            seen = "\n".join(m.get("content") or "" for m in messages if m.get("role") == "tool")
            rep = verify.audit(msg["content"], corpus=seen, label="chat")
            if rep["bad"] and verify.enabled():
                msg = {**msg, "content": msg["content"].rstrip()
                       + "\n\n> ⚠ 本次工具返回里没找到这些数字的出处："
                       + "、".join(x["value"] for x in rep["bad"][:8])}
        if msg.get("content"):
            say({"type": "assistant", "content": msg["content"], "step": step})
            events.append({"type": "assistant", "content": msg["content"]})
        if not calls:
            final = msg.get("content") or "（模型没有返回内容）"
            messages.append({"role": "assistant", "content": final})
            break
        assistant_message = {"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls}
        if msg.get("_protocol"):
            assistant_message["_protocol"] = msg["_protocol"]
        messages.append(assistant_message)
        for call in calls:
            if cancel_check and cancel_check():
                run_service.finish_node(db, tracked_node["id"], "failed", error="运行已中断")
                run_service.finish(db, tracked_run["id"], "failed", error="运行已中断")
                raise RuntimeError("运行已中断")
            fn = (call.get("function") or {})
            name = fn.get("name") or "?"
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            ev = {"type": "tool_call", "name": name, "args": args, "id": call.get("id")}
            say(ev); events.append(ev)
            t_tool_start = time.monotonic()
            try:
                if name not in allowed_names:
                    raise PermissionError("当前助手未获准使用此工具：" + name)
                enforce_tool_policy(name, args, permission)
                enforce_project_scope(db, bound, name, args)
                if name in integration_names:
                    result = agent_integrations.invoke(name, db, ctx_id, args, assistant_id=aid, permission=permission)
                elif name in ("skill_read", "skill_write") or (name.startswith("mcp_") and name != "mcp_register"):
                    result = capabilities.invoke_tool(db, aid, name, args, project_dir)
                else:
                    result = _tool_impl(name, db, ctx_id, args, scope, emit=emit, profile=profile)
                if isinstance(result, dict) and result.get("sub_events"):
                    events.extend(result["sub_events"])      # 子 agent 的事件并进本轮 → 回放可还原
                ok, brief = True, _brief(name, result)
            except Exception as exc:
                result, ok, brief = {"error": f"{type(exc).__name__}: {exc}"}, False, f"失败：{exc}"
            duration_ms = int((time.monotonic() - t_tool_start) * 1000)
            try:
                inp_hash = hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]
                db.record_audit_event(actor=aid or "user", assistant_id=aid, tool=name, project_id=ctx_id,
                                      input_hash=inp_hash, outcome="ok" if ok else "error", duration_ms=duration_ms)
            except Exception:
                pass
            path = result.get("path") if isinstance(result, dict) else None
            ev2 = {"type": "tool_result", "name": name, "ok": ok, "summary": brief,
                   "path": path or "", "id": call.get("id")}
            say(ev2); events.append(ev2)
            messages.append({"role": "tool", "tool_call_id": call.get("id") or name, "name": name,
                             "content": json.dumps(result, ensure_ascii=False, default=str)[:4000]})
    else:
        outcomes = [event for event in events if event.get("type") == "tool_result"]
        lines = []
        for event in outcomes[-6:]:
            status = "成功" if event.get("ok") else "失败"
            detail = event.get("path") or event.get("summary") or "无详情"
            lines.append(f"- {event.get('name', '工具')}（{status}）：{detail}")
        final = "本轮已达到工具调用上限，暂停继续执行。"
        if lines:
            final += "\n\n实际执行结果：\n" + "\n".join(lines)
        final += "\n\n以上为工具执行记录，尚未完成最终核验；可以发送后续指令继续。"
        say({"type": "assistant", "content": final})
    if publish:
        db.add_message(task_id, "assistant", final, events, scope=scope)
        db.log_step(task_id, "agent", "done", text[:60])
        if chat_row:  # 对话置顶 + 首次提问当标题（默认名用久了列表没法认）
            db.db.execute("UPDATE chats SET updated_at=? WHERE id=?",
                          (datetime.now(timezone.utc).isoformat(), task_id))
            if chat_row["title"] in ("", "新对话"):
                db.db.execute("UPDATE chats SET title=? WHERE id=?", (text.strip().replace("\n", " ")[:24], task_id))
            db.db.commit()
        say({"type": "done", "content": final})
    run_service.finish_node(db, tracked_node["id"], "succeeded")
    run_service.finish(db, tracked_run["id"], "succeeded")
    return {"reply": final, "events": events, "run_id": tracked_run["id"]}


def _brief(name: str, result: Any) -> str:
    if not isinstance(result, dict):
        return str(result)[:120]
    if name == "team_board":
        c = result.get("counts") or {}
        return "团队 %d 人 · 排队 %d · 运行 %d · 完成 %d · 失败 %d" % (
            len(result.get("members") or []), c.get("queued", 0), c.get("running", 0),
            c.get("done", 0), c.get("failed", 0))
    if name == "team_assign":
        return "已派给「%s」，排队等待执行" % result.get("member_name", "")
    if name == "team_form":
        return "已组队「%s」：%d 人（加急办公室）" % (result.get("name", ""), len(result.get("added") or []))
    if name == "project_create":
        return "已建项目「%s」· %s" % (result.get("title", ""), str(result.get("project_id", ""))[:8])
    if name == "calendar_list":
        return "%s 共 %d 条日程" % (result.get("range", ""), result.get("count", 0))
    if name == "schedule_manage":
        if result.get("schedules") is not None:
            return "定时任务 %d 个" % result.get("count", 0)
        if result.get("created"):
            return "已建定时任务「%s」%s" % ((result.get("schedule") or {}).get("name", ""), (result.get("schedule") or {}).get("cron", ""))
        if result.get("toggled"):
            s = result.get("schedule") or {}
            return "「%s」已%s" % (s.get("name", ""), "启用" if s.get("enabled") else "暂停")
        if result.get("deleted"):
            return "已删除定时任务「%s」" % result.get("name", "")
    if name == "knowledge_query":
        return "命中 %d 条（%s）" % (result.get("total", 0), "、".join(sorted({h.get("project") or "大笔记" for h in (result.get("hits") or [])}))[:60])
    if name == "paper_search":
        return f"检索到 {result.get('found')} 条，新增入库 {len(result.get('saved') or [])} 条"
    if name == "paper_download":
        p = result.get("paper") or {}
        return f"已入库 {p.get('filename')}（{len(p.get('pages') or [])} 页）"
    if name in ("paper_read", "read"):
        return f"精读完成，笔记 {result.get('note_id', '')[:8]}，{result.get('chunks', 1)} 段"
    if name in ("notes_compose", "notes"):
        return f"知识库笔记已生成，证据 {result.get('evidence', 0)} 条"
    if name == "idea_review":
        return f"审查判定：{(result.get('verdict') or {}).get('verdict', '')}"
    if name == "draft_write":
        return f"草稿已写入 {result.get('path', '')}"
    if name == "draft_review":
        return f"审查结论：{(result.get('review') or {}).get('verdict', '')}"
    if name == "format_convert":
        return f"已导出 {result.get('path', '')}"
    if name == "calendar_add":
        return f"已加日程 {result.get('title')} @ {result.get('date')} {result.get('time') or ''}".strip()

    if name == "library_overview":
        return f"文献 {result.get('文献')} 篇 / 检索结果 {len(result.get('检索结果') or [])} 条 / 笔记 {len(result.get('笔记') or [])} 篇"
    if name == "memory_save":
        return f"已记入长期记忆：{result.get('text','')[:60]}"
    if name == "memory":
        rows = result.get("results") or []
        bad = [r.get("error") for r in rows if isinstance(r, dict) and r.get("error")]
        if result.get("hits") is not None:
            return "检索记忆 %d 条" % len(result["hits"])
        if bad:
            return "记忆写入失败：%s" % bad[0]
        dup = sum(1 for r in rows if isinstance(r, dict) and r.get("duplicate"))
        return "记忆已更新 %d 条%s" % (len(rows), "（%d 条本来就记着）" % dup if dup else "")
    if name == "recall":
        return "回想：记忆 %d 条 / 历史对话 %d 条 / 笔记 %d 条" % (
            len(result.get("memory") or []), len(result.get("chats") or []), len(result.get("notes") or []))
    if name == "skill_write":
        return "已写入 skill「%s」（%s）" % (result.get("id", ""), result.get("scope", ""))
    return "完成"


guards.LOG_PATH = OUT / "隐私命中.log"
