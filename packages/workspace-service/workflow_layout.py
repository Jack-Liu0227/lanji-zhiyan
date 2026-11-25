"""Project-bound, validated workflow definitions and evidence-backed node state."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

import service_settings
from workflow import STAGE_LABEL, STAGES, now
from workflow_catalog import LEGACY_STAGE_LABEL, STAGE_CATALOG


SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_layouts (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  definition TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_workflow_layouts (
  task_id TEXT PRIMARY KEY, layout_id TEXT NOT NULL, selected_node TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS workflow_layouts_project_deleted
AFTER DELETE ON research_tasks BEGIN
  DELETE FROM project_workflow_layouts WHERE task_id=OLD.id;
END;
"""
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
DEFAULT_LAYOUT_ID = "builtin.workflow.research"    # 项目没选过布局时默认用它，别靠排序碰运气
_DONE = {"done", "completed", "success", "succeeded", "downloaded", "delivered"}
_RUNNING = {"running", "in_progress", "downloading", "reading", "translating"}
_FAILED = {"failed", "error", "manual"}
_OUTPUT_LINE = re.compile(r"(?:目标产出文件|目标文件|产物路径)\s*[:：]\s*([^\r\n]+)")


def required_outputs(node: dict[str, Any]) -> list[str]:
    """Resolve declared output paths, including paths embedded in legacy prompts."""
    contract = node.get("output_contract") or {}
    values = []
    for declared in ((contract.get("required_files") or []) if isinstance(contract, dict) else [], node.get("outputs") or []):
        if not isinstance(declared, list):
            raise ValueError("工作流产物路径必须用数组声明")
        values.extend(declared)
    prompt = str(node.get("prompt") or node.get("goal") or "")
    for match in _OUTPUT_LINE.finditer(prompt):
        values.extend(re.split(r"[、,，;；]\s*", match.group(1).strip()))

    result = []
    for value in values:
        if isinstance(value, dict):
            value = value.get("relative") or value.get("path") or ""
        raw = str(value or "").strip().replace("\\", "/")
        path = PurePosixPath(raw)
        if (not raw or path.is_absolute() or PureWindowsPath(raw).drive or
                any(part in ("", ".", "..") or part.startswith(".") for part in raw.split("/")) or
                any(c in raw for c in '<>:"|?*\x00')):
            raise ValueError("工作流产物路径必须是项目内的普通相对路径")
        if raw not in result:
            result.append(raw)
    return result


def normalize_agent_tools(node: dict[str, Any]) -> list[str] | None:
    """Expand explicit agent tool allowlists so contract writes and batch OA downloads work."""
    raw_tools = node.get("tools")
    if raw_tools is None:
        return None
    tools = list(dict.fromkeys(str(t) for t in raw_tools if t))
    if node.get("node_kind") in ("tool", "workflow"):
        return tools
    if required_outputs(node):
        for name in ("project_artifact_write", "workflow_context_read"):
            if name not in tools:
                tools.append(name)
    if (node.get("output_contract") or {}).get("requires_evidence"):
        for name in ("paper_focus", "paper_excerpt", "paper_evidence_record", "paper_evidence_get"):
            if name not in tools:
                tools.append(name)
    if node.get("step") in ("search", "download") and ("paper_search" in tools or "paper_download" in tools):
        for name in ("paper_download", "paper_download_batch"):
            if name not in tools:
                tools.append(name)
    return tools


def _linear(stages: list[str]) -> dict[str, Any]:
    if not stages or any(step not in STAGES for step in stages):
        raise ValueError("工作流需包含有效的科研平台步骤")
    return {"nodes": [{"id": s, "step": s, "label": STAGE_LABEL[s]} for s in stages],
            "edges": [{"source": a, "target": b} for a, b in zip(stages, stages[1:])],
            "order": stages}


def ensure_schema(db: Any) -> None:
    if getattr(db, "_workflow_layout_initialized", False):
        return
    db.db.executescript(SCHEMA)
    seeds = [
        ("builtin.workflow.research", "完整科研工作流", "保留现有全部研究步骤", STAGES),
        ("builtin.workflow.reading", "文献精读", "检索、下载、翻译与整理", ["search", "download", "notes", "map"]),
        ("builtin.workflow.writing", "论文写作", "大纲、写作、审查与导出", ["outline", "map", "write", "review", "entrans", "convert"]),
    ]
    # Seed only once, including after the user deliberately deletes a template.
    catalog = service_settings.read(db, "workflow_catalog")
    if not catalog.get("seeded") and not db.db.execute("SELECT 1 FROM workflow_layouts LIMIT 1").fetchone():
        # now() 在 Windows 上同一时钟刻度里连着调三次会给出一模一样的字符串，
        # 那样 ORDER BY created_at,id 就退化成按 id 排序（"reading" < "research"），
        # 新项目默认打开的工作流会随机变成「文献精读」。逐条 +1µs，让列表顺序 = 声明顺序。
        base = datetime.fromisoformat(now())
        for index, (wid, name, description, stages) in enumerate(seeds):
            stamp = (base + timedelta(microseconds=index)).isoformat()
            db.db.execute("INSERT INTO workflow_layouts VALUES (?,?,?,?,?,?)",
                          (wid, name, description, json.dumps(_linear(stages), ensure_ascii=False), stamp, stamp))
    if not catalog.get("labels_migrated"):
        for row in db.db.execute("SELECT id,definition FROM workflow_layouts").fetchall():
            definition = json.loads(row["definition"])
            changed = False
            for node in definition.get("nodes", []):
                step = node.get("step")
                if node.get("label") == LEGACY_STAGE_LABEL.get(step) and node["label"] != STAGE_LABEL.get(step):
                    node["label"] = STAGE_LABEL[step]
                    changed = True
            if changed:
                db.db.execute("UPDATE workflow_layouts SET definition=? WHERE id=?",
                              (json.dumps(definition, ensure_ascii=False), row["id"]))
    service_settings.write(db, "workflow_catalog", {**catalog, "labels_migrated": True, "seeded": True})
    db.db.commit()
    db._workflow_layout_initialized = True


def validate_definition(data: dict[str, Any]) -> dict[str, Any]:
    """Validate a DAG whose identity is the node ID, independent of its stage."""
    if not isinstance(data, dict):
        raise ValueError("工作流定义必须是 JSON 对象")
    nodes, edges = data.get("nodes"), data.get("edges", [])
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 100:
        raise ValueError("工作流需包含 1 至 100 个节点")
    if not isinstance(edges, list):
        raise ValueError("edges 必须是数组")
    clean, ids, steps = [], set(), set()
    for raw in nodes:
        if not isinstance(raw, dict):
            raise ValueError("每个节点必须是 JSON 对象")
        nid, step = str(raw.get("id", "")), str(raw.get("step", ""))
        if not _ID.fullmatch(nid) or nid in ids:
            raise ValueError("节点 id 须唯一且仅包含字母、数字、下划线、点和横线")
        if step not in (*STAGES, "task"):
            raise ValueError("不支持的工作流步骤：" + step)
        label = str(raw.get("label") or STAGE_LABEL.get(step, "项目任务")).strip()
        if not label or len(label) > 120:
            raise ValueError("节点名称须为 1 至 120 字符")
        review = raw.get("review_required", True)
        if not isinstance(review, bool):
            raise ValueError("review_required 必须是布尔值，默认需要人工审查")
        config = {"review_required": review}
        manual = raw.get('manual_review_required', False)
        if not isinstance(manual, bool):
            raise ValueError('manual_review_required 必须是布尔值')
        if manual:
            config.update(manual_review_required=True, review_required=True)
        if raw.get('manual_execution') is True:
            config.update(manual_execution=True, manual_review_required=True, review_required=True)
        if step == 'review' and not config.get('manual_execution') and raw.get('claim_audit') is True:
            config['claim_audit'] = True
        kind = raw.get('node_kind', 'executor')
        if kind not in ('executor', 'tool', 'workflow'):
            raise ValueError('节点类型须为执行者、固定工具或子工作流')
        if kind == 'workflow' and config.get('manual_execution'):
            raise ValueError('请在子工作流内配置人工完成的节点')
        if kind != 'executor':
            config['node_kind'] = kind
        if kind == 'tool':
            name, args = raw.get('tool_name'), raw.get('tool_args', {})
            if not isinstance(name, str) or not name.strip() or len(name) > 200 or not isinstance(args, dict):
                raise ValueError('固定工具节点需指定工具 ID 和 JSON 参数')
            if len(json.dumps(args, ensure_ascii=False)) > 100000:
                raise ValueError('工具参数过长')
            config.update(tool_name=name.strip(), tool_args=args)
        if kind == 'workflow':
            template = raw.get('workflow_template_id')
            if not isinstance(template, str) or not template or len(template) > 120:
                raise ValueError('子工作流节点需指定模板 ID')
            config['workflow_template_id'] = template
            if step != 'task':
                raise ValueError('子工作流节点请使用通用项目任务步骤')
        mode = raw.get("execution_mode", "")
        if mode not in ("", "assistant", "team"):
            raise ValueError("节点执行方式必须是专家或团队")
        if mode:
            config["execution_mode"] = mode
        for key in ("assistant_id", "team_id", "member_id", "prompt", "executor_binding_id"):
            value = raw.get(key, "")
            if not isinstance(value, str) or len(value) > (20000 if key == "prompt" else 120):
                raise ValueError("节点执行配置无效：" + key)
            config[key] = value.strip()
        model_id = raw.get("model_id", "")
        if model_id is not None:
            if not isinstance(model_id, str) or len(model_id) > 120:
                raise ValueError("节点模型配置无效：model_id")
            if model_id.strip():
                config["model_id"] = model_id.strip()
        if raw.get("fallback_model_ids") is not None:
            fallbacks = raw.get("fallback_model_ids")
            if not isinstance(fallbacks, list) or len(fallbacks) > 20 or any(not isinstance(x, str) or len(x) > 120 for x in fallbacks):
                raise ValueError("节点备用模型列表无效")
            clean_fb = list(dict.fromkeys(x.strip() for x in fallbacks if x.strip()))
            if clean_fb:
                config["fallback_model_ids"] = clean_fb
        node_reasoning = raw.get("reasoning", "")
        if node_reasoning:
            if node_reasoning not in ("auto", "none", "minimal", "low", "medium", "high", "xhigh"):
                raise ValueError("不支持的节点推理强度：" + str(node_reasoning))
            if node_reasoning != "auto":
                config["reasoning"] = node_reasoning
        model_policy = raw.get("model_policy", "")
        if model_policy:
            if model_policy not in ("fallback", "strict"):
                raise ValueError("不支持的节点模型容错策略：" + str(model_policy))
            if model_policy != "fallback":
                config["model_policy"] = model_policy
        role = str(raw.get("executor_role_id") or raw.get("executor_role") or raw.get("role_id") or nid)
        if not _ID.fullmatch(role):
            raise ValueError("执行角色 ID 格式无效")
        config["executor_role_id"] = role
        contract = raw.get("output_contract") or {}
        if not isinstance(contract, dict):
            raise ValueError("节点输出约定必须是 JSON 对象")
        contract = dict(contract)
        required_files = required_outputs({**raw, "output_contract": contract})
        if required_files:
            contract["required_files"] = required_files
        import artifact_quality
        artifact_quality.validate_rules(contract)
        config["output_contract"] = contract
        for key in ("tools", "skills"):
            value = raw.get(key)
            if value is not None and (not isinstance(value, list) or len(value) > 300 or any(not isinstance(x, str) or not x or len(x) > 200 for x in value)):
                raise ValueError("节点工具与技能必须是 ID 数组")
            config[key] = list(dict.fromkeys(value)) if value is not None else None
        clean.append({"id": nid, "step": step, "label": label, **config})
        ids.add(nid)
        steps.add(step)
    links, seen = [], set()
    parents = {n["id"]: [] for n in clean}
    children = {n["id"]: [] for n in clean}
    by_id = {n["id"]: n for n in clean}
    arguments = set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("每条连线必须是 JSON 对象")
        source, target = str(edge.get("source", "")), str(edge.get("target", ""))
        if source not in ids or target not in ids or source == target:
            raise ValueError("连线必须连接两个存在且不同的节点")
        if (source, target) in seen:
            raise ValueError("工作流中存在重复连线")
        seen.add((source, target))
        parents[target].append(source)
        children[source].append(target)
        link = {"source": source, "target": target}
        artifact, argument = edge.get("artifact", ""), edge.get("argument", "")
        if not isinstance(artifact, str) or not isinstance(argument, str):
            raise ValueError("交接文件和工具参数必须为文本")
        artifact, argument = artifact.strip(), argument.strip()
        if artifact:
            raw_norm = artifact.replace("\\", "/")
            norm_artifact = raw_norm.strip("/")
            parts = [part for part in norm_artifact.split("/") if part]
            if (len(norm_artifact) > 255 or not norm_artifact or raw_norm.startswith("/")
                    or ":" in artifact or "\x00" in artifact
                    or any(part in (".", "..") for part in parts) or raw_norm.endswith("/")):
                raise ValueError("交接文件必须填写有效的产物文件名或项目内相对路径")
            link["artifact"] = norm_artifact
        if argument:
            if not artifact or by_id[target].get("node_kind") != "tool" or not argument.isidentifier():
                raise ValueError("工具参数绑定需要文件名和有效的固定工具参数名")
            if (target, argument) in arguments:
                raise ValueError("同一个固定工具参数只能绑定一个上游文件")
            arguments.add((target, argument))
            link["argument"] = argument
        links.append(link)
    waiting = {key: len(value) for key, value in parents.items()}
    queue = [n["id"] for n in clean if not waiting[n["id"]]]
    ordered = []
    while queue:
        nid = queue.pop(0)
        ordered.append(nid)
        for child in children[nid]:
            waiting[child] -= 1
            if not waiting[child]:
                queue.append(child)
    if len(ordered) != len(clean):
        raise ValueError("工作流不能包含循环依赖（必须是 DAG）")
    order = data.get("order", ordered)
    if not isinstance(order, list) or any(not isinstance(n, str) for n in order):
        raise ValueError("order 必须是节点 id 数组")
    if len(order) != len(ids) or set(order) != ids:
        raise ValueError("order 必须且只能包含每个节点一次")
    ranks = {nid: index for index, nid in enumerate(order)}
    if any(ranks[e["source"]] >= ranks[e["target"]] for e in links):
        raise ValueError("节点顺序必须与连线依赖一致")
    return {"nodes": clean, "edges": links, "order": order}


def _public(row: Any) -> dict[str, Any]:
    data = dict(row)
    data.update(json.loads(data.pop("definition")))
    return data


def _layout(db: Any, wid: str) -> dict[str, Any]:
    row = db.db.execute("SELECT * FROM workflow_layouts WHERE id=?", (wid,)).fetchone()
    if not row:
        raise KeyError("工作流不存在：" + wid)
    return _public(row)


def _project(db: Any, task_id: str) -> None:
    if not task_id or not db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (task_id,)).fetchone():
        raise ValueError("请先选择有效项目")


def list_layouts(db: Any) -> dict[str, Any]:
    ensure_schema(db)
    return {"layouts": [_public(row) for row in db.db.execute("SELECT * FROM workflow_layouts ORDER BY created_at,id")]}


def resolve_node(db: Any, project_id: str, layout_id: str, node: dict, *, materialize=True) -> dict:
    """Read the effective binding of a node, including legacy built-in offices."""
    if node.get('executor_binding_id'):
        import project_bindings
        actor=project_bindings.resolve(db,project_id,node['executor_binding_id'],member_id=node.get('member_id',''),materialize=materialize)
        return {**node,'execution_mode':'team' if actor.get('team_id') else 'assistant','team_id':actor.get('team_id',''),'member_id':actor.get('requested_member_id',''),'assistant_id':(node.get('assistant_id') or '') if actor.get('team_id') else actor['assistant_id']}
    if node.get('team_id'):
        import project_bindings
        grant=db.db.execute("SELECT id FROM project_executor_bindings WHERE project_id=? AND kind='team' AND target_id=?",(project_id,node['team_id'])).fetchone()
        actor=project_bindings.resolve(db,project_id,grant['id'] if grant else 'global:team:'+node['team_id'],member_id=node.get('member_id',''),materialize=materialize)
        return {**node,'team_id':actor['team_id'],'member_id':actor['requested_member_id'],'assistant_id':node.get('assistant_id') or '', 'execution_mode':'team'}
    from workflow_catalog import LEGACY_TEAM_WORKFLOWS, LEGACY_TEAM_STAGES, LEGACY_MEMBER_STAGES
    # execution_mode is a role hint, not an executor identity. A project role
    # binding must still be resolved when only the mode is present.
    if any(node.get(k) for k in ("assistant_id", "team_id", "member_id")):
        return node
    if layout_id not in LEGACY_TEAM_WORKFLOWS:
        return node
    matches = []
    for row in db.db.execute("SELECT * FROM teams WHERE project_id=? AND kind='fixed'", (project_id,)):
        roster = db.team_members(row["id"])
        leaders = [m for m in roster if m["role"] == "leader"]
        if not any(node["step"] in LEGACY_TEAM_STAGES.get(m["assistant_id"], ()) for m in leaders):
            continue
        eligible = []
        for member in roster:
            if member.get("step"):
                stages = (member["step"],)
            elif member["role"] == "leader":
                stages = LEGACY_TEAM_STAGES.get(member["assistant_id"], ())
            else:
                stages = LEGACY_MEMBER_STAGES.get(member["assistant_id"], ())
            if node["step"] in stages:
                eligible.append(member["id"])
        matches.append({**node, "team_id": row["id"], "execution_mode": "team",
                        "eligible_member_ids": eligible, "binding_source": "project_team"})
    if len(matches) > 1:
        raise ValueError("多个常驻团队匹配此节点，请在工作流中明确指定执行团队")
    return matches[0] if matches else node


def save_layout(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(db)
    import project_tasks
    with project_tasks._lock, project_tasks._atomic(db):
        return _save_layout(db, body)


def _save_layout(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    name, description = str(body.get("name", "")).strip(), str(body.get("description", "")).strip()
    if not name or len(name) > 120:
        raise ValueError("工作流名称须为 1 至 120 字符")
    if len(description) > 4000:
        raise ValueError("工作流说明不能超过 4000 字符")
    import shared_executors
    definition = validate_definition({**body,'nodes':[shared_executors.portable_node(db,node) for node in body.get('nodes',[])]})
    wid, stamp = str(body.get("id") or ""), now()
    import workflow_template_catalog as catalog
    key = catalog.identity({"name": name, **definition})
    matches = []
    name_conflict = False
    for item in list_layouts(db)['layouts']:
        if item['id'] == wid:
            continue
        try:
            other = catalog.identity(item)
        except (ValueError, KeyError, TypeError):
            continue
        if other == key:
            matches.append(item)
        elif other[0] == key[0]:
            name_conflict = True
    if matches:
        match = max(matches, key=lambda item: (item['updated_at'], item['id']))
        if wid and db.db.execute('SELECT 1 FROM workflow_layouts WHERE id=?', (wid,)).fetchone():
            # An explicit edit owns its ID. Reject collisions without changing references.
            raise ValueError('保存后与已有模板完全重复，请使用已有模板：' + match['name'] + ' (' + match['id'] + ')')
        if match['description'] != description:
            db.db.execute('UPDATE workflow_layouts SET description=?,updated_at=? WHERE id=?', (description, stamp, match['id']))
        return {"ok": True, "reused": True, "layout": _layout(db, match['id'])}
    if wid and db.db.execute("SELECT 1 FROM workflow_layouts WHERE id=?", (wid,)).fetchone():
        db.db.execute("UPDATE workflow_layouts SET name=?,description=?,definition=?,updated_at=? WHERE id=?",
                      (name, description, json.dumps(definition, ensure_ascii=False), stamp, wid))
        valid = {n["id"] for n in definition["nodes"]}
        for binding in db.db.execute("SELECT task_id,selected_node FROM project_workflow_layouts WHERE layout_id=?", (wid,)).fetchall():
            if binding["selected_node"] not in valid:
                db.db.execute("UPDATE project_workflow_layouts SET selected_node=?,updated_at=? WHERE task_id=?",
                              (definition["order"][0], stamp, binding["task_id"]))
    else:
        wid = wid or uuid.uuid4().hex
        db.db.execute("INSERT INTO workflow_layouts VALUES (?,?,?,?,?,?)",
                      (wid, name, description, json.dumps(definition, ensure_ascii=False), stamp, stamp))
    return {"ok": True, "name_conflict": name_conflict, "layout": _layout(db, wid)}


def select_layout(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    tid, wid = str(body.get("task_id", "")), str(body.get("layout_id", ""))
    _project(db, tid)
    import project_workflows
    layout = project_workflows.effective(db,tid,_layout(db,wid))
    selected = str(body.get("node_id") or layout["order"][0])
    if selected not in {n["id"] for n in layout["nodes"]}:
        raise ValueError("所选节点不属于当前工作流")
    db.db.execute("INSERT INTO project_workflow_layouts VALUES (?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
                  "layout_id=excluded.layout_id,selected_node=excluded.selected_node,updated_at=excluded.updated_at",
                  (tid, wid, selected, now()))
    db.db.commit()
    import project_bindings
    project_bindings.import_legacy(db, tid)
    return state(db, tid)


def _status(raw: str) -> str:
    if raw in _DONE:
        return "done"
    if raw in _RUNNING:
        return "running"
    if raw in _FAILED:
        return "failed"
    if raw in {"cancelled", "canceled", "stopped"}:
        return "cancelled"
    if raw == "blocked":
        return "blocked"
    return "pending"


def _evidence(db: Any, tid: str) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    moments: dict[str, tuple[float, int]] = {}

    def moment(stamp: str) -> float:
        try:
            # Team records historically use local wall time without an offset;
            # datetime.timestamp handles that, unlike lexical ISO comparison.
            return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
        except (OSError, OverflowError, TypeError, ValueError):
            return float("-inf")

    def offer(stage: str, raw: str, summary: str, stamp: str, source: str, ref: str) -> None:
        if stage not in STAGES:
            return
        value = {"status": _status(raw), "raw_status": raw, "summary": summary,
                 "updated_at": stamp, "source": source, "record_id": ref}
        # Legacy records have second precision. Explicit stage results must win
        # over a chat response from the same second. Within each source the
        # ordered query still lets the latest record win.
        point = (moment(stamp), 2 if source == "steps" else 1)
        if stage not in evidence or point >= moments[stage]:
            evidence[stage] = value
            moments[stage] = point

    for row in db.db.execute("SELECT * FROM steps WHERE task_id=? ORDER BY created_at,rowid", (tid,)):
        offer(row["stage"], row["status"], row["summary"], row["created_at"], "steps", row["id"])
    # Existing streaming stages historically recorded only chat, not a stage row.
    for row in db.db.execute("SELECT * FROM chat WHERE task_id=? AND role='assistant' ORDER BY created_at,rowid", (tid,)):
        stage = row["scope"] or "idea"
        try:
            events = json.loads(row["events"] or "[]")
        except (TypeError, ValueError):
            events = []
        if any(isinstance(ev, dict) and ev.get("type") == "plan" for ev in events):
            continue
        # soft 的 error = 撞工具上限这类「暂停」，产物齐了就该能交付（老记录没有 soft 标记，按文案认）
        failed = any(isinstance(ev, dict) and ev.get("type") == "error"
                     and not (ev.get("soft") or "工具调用上限" in str(ev.get("content") or ""))
                     for ev in events)
        offer(stage, "failed" if failed else "done", "最近一次步骤对话" + ("失败" if failed else "已完成"),
              row["created_at"], "chat", row["id"])
    for row in db.db.execute("SELECT t.*,m.step FROM team_tasks t JOIN team_members m ON m.id=t.member_id "
                             "WHERE t.task_id=? ORDER BY t.created_at,t.rowid", (tid,)):
        offer(row["step"], row["status"], row["reason"] or row["title"],
              row["ended_at"] or row["started_at"] or row["created_at"], "team_tasks", row["id"])
    queue = list(db.db.execute("SELECT * FROM dl_queue WHERE task_id=?", (tid,)))
    if queue:
        raw = ("running" if any(r["status"] in _RUNNING for r in queue) else
               "failed" if any(r["status"] in _FAILED for r in queue) else
               "queued" if any(r["status"] in {"queued", "pending"} for r in queue) else "done")
        latest = max(queue, key=lambda row: moment(row["updated_at"]))
        offer("download", raw, f"下载队列：{len(queue)} 项", latest["updated_at"], "dl_queue", latest["result_id"])
    return evidence


def state(db: Any, task_id: str) -> dict[str, Any]:
    _project(db, task_id)
    layouts = [_public(row) for row in db.db.execute("SELECT * FROM workflow_layouts ORDER BY created_at,id")]
    binding = db.db.execute("SELECT * FROM project_workflow_layouts WHERE task_id=?", (task_id,)).fetchone()
    fallback = next((item for item in layouts if item["id"] == DEFAULT_LAYOUT_ID), layouts[0] if layouts else None)
    layout = next((item for item in layouts if binding and item["id"] == binding["layout_id"]), None) if binding else fallback
    if not layout:
        return {"task_id": task_id, "layouts": layouts, "layout": None, "selected_node": "",
                "stages": STAGE_CATALOG, "nodes": [], "edges": [], "observed_at": now()}
    import project_workflows
    layout=project_workflows.effective(db,task_id,layout)
    if not binding or binding["layout_id"] != layout["id"]:
        db.db.execute("INSERT OR REPLACE INTO project_workflow_layouts VALUES (?,?,?,?)",
                      (task_id, layout["id"], layout["order"][0], now()))
        db.db.commit()
        selected = layout["order"][0]
    else:
        selected = binding["selected_node"] or layout["order"][0]
    import workflow_state
    evidence = workflow_state.states(db, task_id, layout)
    stages = [n["step"] for n in layout["nodes"]]
    by_node = "task" in stages or len(stages) != len(set(stages))
    nodes = {node["id"]: dict(node) for node in layout["nodes"]}
    for node in nodes.values():
        node.update(evidence.get(node["id"] if by_node else node["step"], {"status": "pending", "raw_status": "", "summary": "尚无执行记录",
                                              "updated_at": "", "source": "", "record_id": ""}))
    for nid in layout["order"]:
        node = nodes[nid]
        parents = [edge["source"] for edge in layout["edges"] if edge["target"] == nid]
        node["dependencies"] = parents
        node["level"] = max((nodes[parent]["level"] + 1 for parent in parents), default=0)
        if node["status"] == "pending":
            node["status"] = "ready" if all(nodes[parent]["status"] == "delivered" for parent in parents) else "waiting"
    return {"task_id": task_id, "layouts": layouts, "layout": layout, "selected_node": selected,
            "stages": STAGE_CATALOG,
            "nodes": [nodes[nid] for nid in layout["order"]], "edges": layout["edges"],
            "observed_at": now(), "status_note": "节点状态来自真实步骤、对话、团队任务和下载队列；依赖展示不会自动执行步骤。"}


def delete_node_from_layout(layout: dict[str, Any], node_id: str, restitch: bool = True) -> dict[str, Any]:
    nodes = list(layout.get("nodes", []))
    edges = list(layout.get("edges", []))
    if len(nodes) <= 1:
        raise ValueError("工作流必须保留至少一个节点，无法删除唯一节点")
    if not any(n["id"] == node_id for n in nodes):
        raise ValueError(f"节点 {node_id} 不存在于当前工作流")

    parents = [e["source"] for e in edges if e["target"] == node_id]
    children = [e["target"] for e in edges if e["source"] == node_id]

    new_edges = [e for e in edges if e["source"] != node_id and e["target"] != node_id]

    if restitch and parents and children:
        for p in parents:
            for c in children:
                if p != c and not any(e["source"] == p and e["target"] == c for e in new_edges):
                    new_edges.append({"source": p, "target": c})

    new_nodes = [n for n in nodes if n["id"] != node_id]

    in_degree = {n["id"]: 0 for n in new_nodes}
    for e in new_edges:
        if e["target"] in in_degree:
            in_degree[e["target"]] += 1

    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    order = []
    while queue:
        u = queue.pop(0)
        order.append(u)
        for e in new_edges:
            if e["source"] == u:
                v = e["target"]
                if v in in_degree:
                    in_degree[v] -= 1
                    if in_degree[v] == 0:
                        queue.append(v)

    if len(order) != len(new_nodes):
        raise ValueError("删除节点并缝合后检测到依赖环路，操作已终止")

    updated = dict(layout)
    updated["nodes"] = new_nodes
    updated["edges"] = new_edges
    updated["order"] = order
    return updated


def delete_layout(db: Any, wid: str) -> dict[str, Any]:
    ensure_schema(db)
    layout = _layout(db, wid)
    import workflow_template_catalog as catalog
    import project_tasks
    refs = catalog.references(db, wid)
    with project_tasks._lock, project_tasks._atomic(db):
        catalog.archive(db, 'delete', layout, refs)
        db.db.execute("UPDATE project_workflow_layouts SET layout_id='',selected_node='',updated_at=? WHERE layout_id=?", (now(), wid))
        db.db.execute("DELETE FROM project_workflow_roles WHERE template_id=?", (wid,))
        # Keep subworkflow definitions intact so the editor can identify the missing reference.
        # Schedule links remain explicit missing references and cannot silently run another template.
        db.db.execute("DELETE FROM workflow_layouts WHERE id=?", (wid,))
    return {"ok": True, "references": {key: len(value) for key, value in refs.items()}}


def route(method: str, path: str, body: dict[str, Any] | None, query: dict[str, Any], db: Any) -> dict[str, Any] | None:
    import project_workflows
    project_response=project_workflows.route(method,path,body or {},query,db)
    if project_response is not None:return project_response
    endpoints = {("GET", "/api/workflow/layouts"), ("GET", "/api/workflow/layouts/state"),
                 ("GET", "/api/workflow/layouts/delete-impact"),
                 ("POST", "/api/workflow/layouts/save"), ("POST", "/api/workflow/layouts/delete"),
                 ("POST", "/api/workflow/layouts/select"), ("POST", "/api/workflow/layouts/delete-node")}
    if (method, path) not in endpoints:
        return None
    ensure_schema(db)
    body = body or {}
    if path.endswith('/delete-impact'):
        import workflow_template_catalog
        raw = query.get('id', '')
        wid = str(raw[0] if isinstance(raw, list) and raw else raw)
        _layout(db, wid)
        refs = workflow_template_catalog.references(db, wid)
        return {'projects': len(refs['project_workflow_layouts']), 'schedules': len(refs['schedule_workflows']),
                'subworkflows': len(refs['subworkflows'])}
    if path == "/api/workflow/layouts":
        import agent_settings
        import capabilities
        import model_health
        health_map = model_health.list_states(db)
        models = [{**agent_settings.public(m), "health": health_map.get(m["id"], model_health.get(db, m["id"]))} for m in db.models()]
        return {"layouts": [_public(row) for row in db.db.execute("SELECT * FROM workflow_layouts ORDER BY created_at,id")],
                "stages": STAGE_CATALOG,
                "execution_options": {"assistants": db.selectable_assistants(),
                    "teams": [{**t, "members": db.team_members(t["id"])} for t in db.teams() if t.get("status") != "archived"],
                    "models": models,
                    "tools": capabilities.list_tools(db), "skills": capabilities.list_skills()}}
    if path.endswith("/state"):
        raw = query.get("task_id", query.get("id", ""))
        return state(db, str(raw[0] if isinstance(raw, list) and raw else raw))
    if path.endswith("/save"):
        return save_layout(db, body)
    if path.endswith("/select"):
        return select_layout(db, body)
    if path.endswith("/delete-node"):
        layout = body.get("layout") or {}
        node_id = str(body.get("node_id") or "")
        restitch = bool(body.get("restitch", True))
        return {"ok": True, "layout": delete_node_from_layout(layout, node_id, restitch=restitch)}
    wid = str(body.get("id", ""))
    return delete_layout(db, wid)
