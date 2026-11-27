"""Compile workflow node identities into project tasks and coordinate their attempts."""
from __future__ import annotations

import json
import uuid

import project_bindings as bindings
import project_tasks as tasks
import project_task_execution as worker
import workflow_layout
import workflow_state


def compute_node_hierarchical_steps(nodes: list[dict], edges: list[dict]) -> dict[str, dict]:
    """计算工作流节点的拓扑波次与标准分层编号（如 Step 1, Step 2.1, Step 2.2, Step 3, Step 4）."""
    if not nodes:
        return {}
    in_deg = {n["id"]: 0 for n in nodes}
    adj = {n["id"]: [] for n in nodes}
    for e in edges or []:
        src, dst = e.get("source"), e.get("target")
        if src in adj and dst in in_deg:
            in_deg[dst] += 1
            adj[src].append(dst)

    depth = {}
    queue = [nid for nid, deg in in_deg.items() if deg == 0]
    for nid in queue:
        depth[nid] = 0

    rem_in_deg = dict(in_deg)
    while queue:
        u = queue.pop(0)
        d = depth.get(u, 0)
        for v in adj.get(u, []):
            depth[v] = max(depth.get(v, 0), d + 1)
            rem_in_deg[v] -= 1
            if rem_in_deg[v] == 0:
                queue.append(v)

    for idx, n in enumerate(nodes):
        if n["id"] not in depth:
            depth[n["id"]] = idx

    level_map = {}
    for n in nodes:
        lvl = depth.get(n["id"], 0)
        level_map.setdefault(lvl, []).append(n)

    result = {}
    import re
    for major_idx, lvl in enumerate(sorted(level_map.keys())):
        group = level_map[lvl]
        major_no = major_idx + 1
        is_parallel = len(group) > 1
        for sub_idx, n in enumerate(group):
            minor_no = sub_idx + 1
            step_no = f"{major_no}.{minor_no}" if is_parallel else f"{major_no}"
            raw = str(n.get("label") or n.get("title") or n.get("id") or "").strip()
            clean = re.sub(r"^Step\s*\d+(\.\d+)?\s*[:：]\s*", "", raw, flags=re.IGNORECASE).strip()
            canonical_title = f"Step {step_no}: {clean}"
            result[n["id"]] = {
                "step_no": step_no,
                "major_no": major_no,
                "minor_no": minor_no,
                "is_parallel": is_parallel,
                "clean_title": clean,
                "canonical_title": canonical_title,
                "wave_index": major_idx,
                "wave_total": len(group)
            }
    return result


def _save(db, run):
    db.db.execute("UPDATE workflow_executions SET settings=? WHERE run_id=?",
                  (json.dumps(run["settings"], ensure_ascii=False), run["id"]))
    db.db.commit()


def _separate_inputs(inputs):
    """Keep free-text workflow guidance out of the project-file input contract."""
    if inputs is None:
        return [], ""
    if not isinstance(inputs, list):
        raise ValueError("工作流 inputs 必须是项目资产或相对文件列表")
    references, guidance = [], []
    for item in inputs:
        if isinstance(item, str) and item.strip():
            references.append(item)
            continue
        if isinstance(item, dict) and item.get("type") == "text":
            text = str(item.get("content") or "").strip()
            if text:
                guidance.append(text)
            continue
        if not isinstance(item, dict) or not (item.get("asset_id") or item.get("relative")):
            raise ValueError("输入须引用项目资产或相对文件；文本要求请放在工作流目标中")
        references.append(item)
    return references, "\n\n".join(guidance)


def _repair_prepared_guidance(db, run):
    """Migrate old prepared runs that stored free text as file inputs."""
    settings = run["settings"]
    references, input_guidance = _separate_inputs(settings.get("task_inputs") or [])
    guidance = []
    for text in (settings.get("task_guidance") or "", input_guidance):
        if text and text not in guidance:
            guidance.append(text)
    pending = []
    for task_id in settings.get("task_ids", {}).values():
        task = tasks.get(db, task_id)
        if task.get("latest_run_id"):
            continue
        task_references, task_guidance = _separate_inputs(task.get("inputs") or [])
        if task_guidance and task_guidance not in guidance:
            guidance.append(task_guidance)
        pending.append((task, task_references))
    combined_guidance = "\n\n".join(guidance)
    changed = settings.get("task_inputs") != references or settings.get("task_guidance", "") != combined_guidance
    settings["task_inputs"] = references
    settings["task_guidance"] = combined_guidance
    for task, task_references in pending:
        goal = task["goal"]
        if combined_guidance:
            block = "【本次工作流要求】\n" + combined_guidance
            if block not in goal:
                goal = goal.rstrip() + "\n\n" + block
        if task["inputs"] != task_references or task["goal"] != goal:
            db.db.execute("UPDATE project_tasks SET inputs=?,goal=?,updated_at=? WHERE id=?",
                          (tasks._json(task_references), goal, tasks._now(), task["id"]))
            changed = True
    if changed:
        _save(db, run)


def auto_bind_layout_roles(db, project_id, layout):
    """Automatically bind unbound roles in a layout to available project executors."""
    bindings.import_legacy(db, project_id)
    available = [b for b in bindings.list_bindings(db, project_id) if b.get("enabled") and b.get("available", True)]
    if not available:
        existing_steward = db.db.execute(
            "SELECT id, enabled FROM project_executor_bindings WHERE project_id=? AND kind='assistant' AND target_id='builtin-steward'",
            (project_id,),
        ).fetchone()
        if not existing_steward:
            steward = db.assistant("builtin-steward")
            if steward and steward.get("enabled"):
                available = [bindings.register(db, project_id, "assistant", "builtin-steward", source="auto")]
    if not available:
        return
    roles = {r["role_id"]: r for r in bindings.list_roles(db, project_id, layout["id"])}
    from workflow_catalog import LEGACY_MEMBER_STAGES
    stage_to_aid = {stage: aid for aid, stages in LEGACY_MEMBER_STAGES.items() for stage in stages}
    for node in layout.get("nodes", []):
        if node.get("node_kind") == "workflow" or node.get("executor_binding_id"):
            continue
        role = bindings.role_id(node)
        saved = roles.get(role, {})
        if saved.get("binding_id"):
            continue
        explicit_aid = node.get("assistant_id") or ""
        stage_aid = stage_to_aid.get(node.get("step", ""), "")
        chosen = None
        if node.get("execution_mode") == "team" or node.get("team_id"):
            chosen = next((b for b in available if b["kind"] == "team" and (not node.get("team_id") or b["target_id"] == node["team_id"])), None)
        if not chosen and explicit_aid:
            chosen = next((b for b in available if b["kind"] == "assistant" and b["target_id"] == explicit_aid), None)
        if not chosen:
            chosen = next((b for b in available if b["kind"] == "assistant" and b.get("source") not in ("legacy", "auto") and b["target_id"] != "builtin-steward"), None)
        if not chosen and stage_aid:
            chosen = next((b for b in available if b["kind"] == "assistant" and b["target_id"] == stage_aid), None)
        if not chosen:
            chosen = next((b for b in available if b["kind"] == "assistant" and b["target_id"] != "builtin-steward"), None) or available[0]
        if chosen:
            real_grant = bindings.register(db, project_id, chosen["kind"], chosen["target_id"], source="auto")
            bindings.bind_role(db, project_id, layout["id"], role, real_grant["id"])
            node["executor_binding_id"] = real_grant["id"]


def _executor(db, project_id, layout, raw, selected=None):
    if raw.get('node_kind') == 'workflow':
        raise ValueError('子工作流请通过“准备 / 查看运行”启动，准备时会展开并固定子节点')
    role = bindings.role_id(raw)
    saved = next((r for r in bindings.list_roles(db, project_id, layout["id"]) if r["role_id"] == role), {})
    effective = workflow_layout.resolve_node(db, project_id, layout["id"], raw)
    if selected:
        effective = {**effective, **selected}
    if raw.get('executor_binding_id'):
        executor=bindings.resolve(db,project_id,raw['executor_binding_id'],member_id=(selected or {}).get('member_id',raw.get('member_id','')))
    elif selected or any(raw.get(key) for key in ('assistant_id','team_id','member_id','executor_binding_id')) or saved.get("source") != "user":
        import file_handoff
        # Keep contradictory legacy executor fields an error instead of selecting a different person.
        choices = file_handoff.actors(db, project_id, effective)
        if not choices:
            raise ValueError("节点「" + raw["label"] + "」未配置执行者，请在项目的工作流角色中绑定专家或团队")
        choice = choices[0]
        kind = "team" if choice.get("team_id") else "assistant"
        target = choice.get('team_id') or choice['assistant_id']
        if kind=='team':
            profile=db.db.execute('SELECT template_team_id FROM team_execution_instances WHERE instance_team_id=?',(target,)).fetchone()
            if profile:target=profile[0]
        grant = bindings.register(db, project_id, kind, target, source="legacy")
        fixed_member = choice.get("member_id", "") if selected or raw.get("member_id") or (raw.get("team_id") and raw.get("assistant_id")) else ""
        executor = bindings.resolve(db, project_id, grant["id"], member_id=fixed_member)
    else:
        executor = bindings.resolve_role(db, project_id, layout["id"], role)
    import workflow_execution as engine
    resolved_node = {**raw, "execution_mode": "team" if executor.get("team_id") else "assistant",
                     **{k: executor.get(k, "") for k in ("assistant_id", "team_id", "member_id")}}
    engine._actor(db, project_id, resolved_node)
    if raw.get("node_kind", "executor") == "executor" and (raw.get("output_contract") or {}).get("requires_evidence"):
        allowed = worker._allowlists(executor.get("tools"), raw.get("tools"))
        needed = "paper_evidence_record" if raw.get("step") in ("task", "notes", "download") else "paper_evidence_get"
        if allowed is not None and needed not in allowed:
            raise ValueError(f"节点「{raw['label']}」要求全文证据，但未配置 {needed}；请先修正节点工具再准备运行")
    return executor, resolved_node


def compile_run(db, run):
    """Idempotent conversion, including a file-handoff run created by the old adapter."""
    if run["settings"].get("task_ids"):
        _repair_prepared_guidance(db, run)
        return run
    project_id, layout = run["project_id"], run["layout"]
    layout = {**layout, **workflow_layout.validate_definition(layout)}
    managed = run["settings"].get("managed_artifacts")
    if managed:
        from workflow_artifact_paths import bind_managed_paths
        if not isinstance(managed, dict) or managed.get("version") != 1:
            raise ValueError("不支持的托管产物路径策略")
        layout = bind_managed_paths(layout, run_segment=managed["run_segment"])
        layout = {**layout, **workflow_layout.validate_definition(layout)}
    run["layout"] = layout
    db.db.execute("UPDATE workflow_executions SET layout_snapshot=? WHERE run_id=?", (json.dumps(layout, ensure_ascii=False), run["id"]))
    db.db.commit()
    bindings.import_legacy(db, project_id)
    config = {n["id"]: n for n in layout["nodes"]}
    selected_ids = run["settings"].get("node_ids", layout["order"])
    history = {}
    for old in run["nodes"]:
        identity = old["acceptance"].get("node_id")
        if identity:
            history[identity] = old
    resolved = {}
    for nid in selected_ids:
        chosen = run["settings"].get("actor")
        previous = history.get(nid)
        if previous and not chosen:
            incoming = previous["checkpoint"].get("input") or {}
            old_config = previous["acceptance"].get("config") or {}
            member = incoming.get("member") or {}
            assistant = incoming.get("assistant_id") or old_config.get("assistant_id")
            team_id = old_config.get("team_id") or member.get("task_id", "")
            if assistant:
                chosen = {"execution_mode": "team" if team_id else "assistant", "assistant_id": assistant,
                          "team_id": team_id, "member_id": member.get("id") or old_config.get("member_id", "")}
        resolved[nid] = _executor(db, project_id, layout, config[nid], chosen)
    allowed = sorted({executor["binding_id"] for executor, _ in resolved.values()})
    members = sorted({executor["member_id"] for executor, _ in resolved.values() if executor.get("member_id")})
    stages = [n["step"] for n in layout["nodes"]]
    try:
        current_layout = workflow_state.definition(db, project_id)
    except (ValueError, KeyError):
        current_layout = {'id': ''}
    compat = (current_layout["id"] == layout["id"] and all(s in workflow_layout.STAGES for s in stages)
              and len(stages) == len(set(stages))
              and not any(workflow_layout.required_outputs(n) for n in layout["nodes"]))
    step_info_map = compute_node_hierarchical_steps(layout.get("nodes", []), layout.get("edges", []))
    mapping = {}
    for nid in selected_ids:
        executor, node = resolved[nid]
        step_info = step_info_map.get(nid, {})
        canonical_title = step_info.get("canonical_title") or node["label"]
        incoming = [e for e in layout["edges"] if e["target"] == nid and e["source"] in mapping]
        deps = [mapping[e["source"]] for e in incoming]
        handoffs = [{**e, "task_id": mapping[e["source"]]} for e in incoming if e.get("artifact")]
        inputs = list(run["settings"].get("task_inputs") or [])
        source = run["settings"].get("file_input")
        if source:
            root = tasks._root(db, project_id)
            from pathlib import Path
            inputs = [{"relative": Path(source["path"]).resolve().relative_to(root).as_posix(), "sha256": source["sha256"]}]
            inputs.extend({"relative": Path(f["path"]).resolve().relative_to(root).as_posix(), "sha256": f["sha256"]} for f in source.get("related_inputs", []))
        scope = {"kind": "workflow", "allowed_binding_ids": allowed}
        run_review_mode = run["settings"].get("review_mode") or bindings.workflow_review_mode(db)
        if run_review_mode in bindings.WORKFLOW_REVIEW_MODES:
            scope["workflow_review_mode"] = run_review_mode
        if node.get('manual_review_required'):
            scope['manual_review_required'] = True
        if node.get('manual_execution'):
            scope['manual_execution'] = True
        if members:
            # Team-wide nodes can dispatch to their own configured members only.
            members_for_flow = set(members)
            for ex, original in resolved.values():
                if ex.get("team_id") and not config[original["id"]].get("member_id"):
                    members_for_flow.update(m["id"] for m in db.team_members(ex["team_id"]))
            scope["allowed_member_ids"] = sorted(members_for_flow)
        if node.get("tools") is not None:
            scope["tools"] = node["tools"]
        goal = (run["settings"].get("goal") if run["settings"].get("single_node") else node.get("prompt")) or run["settings"].get("goal") or ("完成「" + canonical_title + "」，保存实际产物并说明结果。")
        if run["settings"].get("task_guidance"):
            goal += "\n\n【本次工作流要求】\n" + run["settings"]["task_guidance"]
        task = tasks.create(db, {"project_id": project_id, "title": canonical_title, "goal": goal,
            "executor_binding_id": executor["binding_id"], "member_id": executor.get("requested_member_id", "") if not run["settings"].get("actor") else executor.get("member_id", ""),
            "inputs": inputs, "dependencies": deps, "output_contract": node.get("output_contract") or {},
            "review_required": node.get("review_required", True), "workflow_run_id": run["id"], "workflow_node_id": nid,
            "scope": scope, "metadata": {"stage": node["step"], "node_config": node, "handoffs": handoffs,
                "legacy_stage_compat": compat, "layout_snapshot": layout,
                "workflow_node": {"run_id": run["id"], "node_id": nid, **step_info},
                **({"template_ref": run["settings"]["task_template_ref"]} if run["settings"].get("task_template_ref") else {})}},
            authority=tasks.CommandContext.system(project_id), request_key="workflow:" + run["id"] + ":" + nid)
        mapping[nid] = task["id"]
        old = history.get(nid)
        if old and not tasks.get(db, task["id"])["latest_run"]:
            task = tasks.restore_attempt(db, task["id"], old)
            if old["checkpoint"].get("team_task_id"):
                tasks.link_origin(db, task["id"], "team_task", old["checkpoint"]["team_task_id"], task["latest_run"]["id"])
        if run["settings"].get("handoff_id"):
            tasks.link_origin(db, task["id"], "handoff", run["settings"]["handoff_id"])
    run["settings"].update(task_ids=mapping, legacy_stage_compat=compat)
    _save(db, run)
    return run


def prepare(db, project_id, goal="", idempotency_key="", template_id="", *, inputs=None, managed_artifacts=False, review_mode=""):
    import workflow_execution as engine
    import run_service
    import workflow
    workflow_layout._project(db, project_id)
    workflow_layout.ensure_schema(db)
    if review_mode and review_mode not in bindings.WORKFLOW_REVIEW_MODES:
        raise ValueError("review_mode 只能是 human_review 或 auto")
    if idempotency_key:
        row = db.db.execute("SELECT id FROM workflow_runs WHERE project_id=? AND idempotency_key=?", (project_id, idempotency_key)).fetchone()
        if row:
            return engine.detail(db, row["id"])
    old = engine.active(db, project_id)
    if old:
        if old["workflow"] == "file-handoff":
            raise ValueError("项目有未结束的文件节点任务，请先完成审查或停止该任务")
        return old
    if not isinstance(managed_artifacts, bool):
        raise ValueError("managed_artifacts 必须是布尔值")
    inputs, task_guidance = _separate_inputs(inputs)
    import project_workflows
    layout = project_workflows.current(db,project_id,template_id) if template_id else workflow_state.definition(db, project_id)
    from workflow_composition import expand
    frozen = expand(db, project_id, layout)
    bindings.import_legacy(db, project_id)
    resolved_executors = {}
    for node in frozen["nodes"]:
        executor, _ = _executor(db, project_id, frozen, node)
        resolved_executors[node["id"]] = executor
        if node.get('node_kind') == 'tool':
            arguments = [edge['argument'] for edge in frozen['edges']
                         if edge['target'] == node['id'] and edge.get('argument')]
            worker.validate_fixed_tool(db, project_id, node, executor, bound_arguments=arguments)
    import agent_settings
    preflight = agent_settings.preflight_layout(db, project_id, frozen, executors=resolved_executors)
    if not preflight["ok"]:
        raise ValueError(preflight["errors"][0])
    if any(n["step"] == "idea" for n in frozen["nodes"]) and not db.idea_input(project_id).strip():
        project = db.summary(project_id)["task"]
        db.idea_input_save(project_id, str(goal).strip() or project.get("question") or project["title"])
    run = run_service.start(db, project_id, "research-sequence", idempotency_key=idempotency_key or "workflow:" + uuid.uuid4().hex)
    effective_review = review_mode or bindings.workflow_review_mode(db)
    settings = {"goal": str(goal), "policy": bindings.settings(db, project_id), "review_mode": effective_review,
                "prepared": True, "task_inputs": inputs, "task_guidance": task_guidance, "preflight": preflight}
    if managed_artifacts:
        settings["managed_artifacts"] = {"version": 1, "run_segment": run["id"]}
    order = frozen["order"]
    links = {(edge["source"], edge["target"]) for edge in frozen["edges"]}
    if links != set(zip(order, order[1:])):
        settings["dag_scheduler"] = True
    db.db.execute("INSERT INTO workflow_executions VALUES (?,?,?,?)", (run["id"], json.dumps(frozen, ensure_ascii=False), json.dumps(settings, ensure_ascii=False), workflow.now()))
    engine._status(db, run["id"], "queued")
    try:
        compile_run(db, engine.detail(db, run["id"]))
    except ValueError as exc:
        if managed_artifacts:
            engine._status(db, run["id"], "failed", str(exc))
        raise
    return engine.detail(db, run["id"])


def prepare_node(db, body):
    """Prepare an explicit current-workflow target without invoking a model."""
    import file_handoff
    import workflow_execution as engine
    import run_service
    import workflow
    project_id = str(body.get("project_id") or "")
    workflow_layout._project(db, project_id)
    if body.get("task_id") and body["task_id"] != project_id:
        raise ValueError("项目范围不一致")
    node, actor = file_handoff._target(db, body)
    goal = str(body.get("goal") or "").strip()
    inputs = body.get("inputs") or []
    if not goal or not isinstance(inputs, list):
        raise ValueError("请填写有效目标和项目材料")
    # Pin saved input versions before creating any workflow records.
    import asset_service
    checked = []
    for item in inputs:
        if not isinstance(item, dict):
            raise ValueError("输入须引用项目资产或相对文件")
        if item.get("relative"):
            source = file_handoff._file(db, project_id, item["relative"])
            digest = tasks._hash(source)
            if (item.get("sha256") or item.get("content_hash") or digest) != digest:
                raise ValueError("输入版本已变化")
            checked.append({"relative": item["relative"], "sha256": digest})
        else:
            asset = asset_service.get(db, str(item.get("asset_id") or ""))
            if not asset or asset["project_id"] != project_id or asset_service.is_instruction_asset(asset):
                raise ValueError("请选择当前项目的资料资产")
            checked.append({"asset_id": asset["id"], "content_hash": asset.get("content_hash", "")})
    inputs = checked
    key = str(body.get("request_key") or "")
    command = {"goal": goal, "inputs": inputs, "node_id": node["id"],
               "layout_id": body["layout_id"], "revision": body["revision"], "actor": actor,
               "template_ref": body.get("template_ref")}
    if key:
        old = db.db.execute("SELECT id FROM project_tasks WHERE project_id=? AND request_key=?", (project_id, key)).fetchone()
        if old:
            task = tasks.get(db, old["id"])
            if task["metadata"].get("node_command") != command:
                raise ValueError("该请求标识已用于另一份节点任务")
            return {"task": task, "run": engine.detail(db, task["workflow_run_id"])}
    layout = workflow_state.definition(db, project_id)
    frozen = {**layout, **workflow_layout.validate_definition(layout)}
    raw = next(n for n in frozen["nodes"] if n["id"] == node["id"])
    executor, resolved = _executor(db, project_id, frozen, raw, actor)
    current = engine.active(db, project_id)
    if current:
        if any(current["layout"].get(k) != frozen.get(k) for k in ("id", "nodes", "edges")):
            raise ValueError("进行中的运行使用其他工作流版本，请先结束该运行")
        current = compile_run(db, current)
        parent_id = current["settings"]["task_ids"].get(node["id"])
        if not parent_id:
            raise ValueError("该节点不在当前运行内，请先完成或停止当前运行")
        parent = tasks.get(db, parent_id)
        metadata = {**parent["metadata"], "node_config": resolved, "node_command": command}
        if body.get("template_ref"):
            metadata["template_ref"] = body["template_ref"]
        metadata.pop("upstream_context", None)
        task = tasks.create(db, {"project_id": project_id, "parent_task_id": parent_id,
            "title": node["label"] + " · " + goal[:60], "goal": goal, "inputs": inputs,
            "executor_binding_id": executor["binding_id"], "member_id": executor.get("member_id", ""),
            "dependencies": parent["dependencies"], "scope": parent["scope"],
            "output_contract": raw.get("output_contract") or {}, "review_required": True, "metadata": metadata},
            authority=tasks.CommandContext.ui(project_id), request_key=key)
    else:
        stages = [n["step"] for n in frozen["nodes"]]
        compat = all(s in workflow_layout.STAGES for s in stages) and len(stages) == len(set(stages))
        if not compat and any(e["target"] == node["id"] for e in frozen["edges"]):
            raise ValueError("该节点有前置交付，请先准备完整工作流，再为其中节点追加任务")
        run = run_service.start(db, project_id, "research-sequence", idempotency_key="node:" + (key or uuid.uuid4().hex))
        settings = {"goal": goal, "policy": bindings.settings(db, project_id), "prepared": True,
                    "single_node": True, "node_ids": [node["id"]], "actor": actor, "task_inputs": inputs,
                    "task_template_ref": body.get("template_ref")}
        db.db.execute("INSERT INTO workflow_executions VALUES (?,?,?,?)", (run["id"], json.dumps(frozen, ensure_ascii=False), json.dumps(settings, ensure_ascii=False), workflow.now()))
        engine._status(db, run["id"], "queued")
        current = compile_run(db, engine.detail(db, run["id"]))
        task_id = current["settings"]["task_ids"][node["id"]]
        task = tasks.get(db, task_id)
        db.db.execute("UPDATE project_tasks SET request_key=?,metadata=? WHERE id=?",
            (key or task["request_key"], json.dumps({**task["metadata"], "node_command": command}, ensure_ascii=False), task_id))
        db.db.commit()
        task = tasks.get(db, task_id)
    return {"task": task, "run": current}


def _record_attempt(db, run, nid, task, task_run, snapshot):
    import run_service
    origin = next((o for o in task["origins"] if o["kind"] == "workflow_node" and o["task_run_id"] == task_run["id"]), None)
    if origin:
        return
    node = task["metadata"]["node_config"]
    record = run_service.node_start(db, run["id"], node["step"], inputs=snapshot.get("input_assets", []),
        acceptance={"node_id": nid, "review_required": node.get("review_required", True), "config": node})
    tasks.link_origin(db, task["id"], "workflow_node", record["id"], task_run["id"])
    member = task_run["executor_snapshot"].get("member")
    if member:
        old = db.team_task_add(task_run["executor_snapshot"]["team_id"], member["id"], member["name"], node["label"], task["goal"], trigger="workflow", origin="workflow:" + run["id"])
        tasks.link_origin(db, task["id"], "team_task", old["id"], task_run["id"])


def _sync_workflow_node(db, run, task, task_run=None):
    """Project canonical task progress back to the LangGraph node row."""
    attempt = task_run or task.get("latest_run") or {}
    attempt_id = attempt.get("id")
    origin = next((item for item in task.get("origins", [])
                   if item.get("kind") == "workflow_node" and
                   (not attempt_id or item.get("task_run_id") == attempt_id)), None)
    if not origin and not attempt_id:
        origin = next((item for item in task.get("origins", []) if item.get("kind") == "workflow_node"), None)
    if not origin:
        return
    status = {"ready": "queued", "queued": "queued", "running": "running",
              "awaiting_review": "awaiting_review", "delivered": "delivered",
              "blocked": "blocked", "failed": "failed", "cancelled": "cancelled",
              "interrupted": "interrupted"}.get(task.get("status"), task.get("status", "queued"))
    snapshot = attempt.get("output_snapshot") or {}
    assets = [item.get("asset_id") or item.get("id") for item in task.get("outputs", [])
              if isinstance(item, dict) and (item.get("asset_id") or item.get("id"))]
    existing = db.db.execute("SELECT checkpoint FROM workflow_nodes WHERE id=?", (origin["legacy_id"],)).fetchone()
    checkpoint = {}
    if existing and existing["checkpoint"]:
        try:
            checkpoint = json.loads(existing["checkpoint"])
        except (TypeError, ValueError):
            checkpoint = {}
    checkpoint.update(attempt.get("checkpoint") or {})
    checkpoint.update(canonical_task_id=task["id"], canonical_run_id=attempt.get("id", ""))
    if snapshot:
        checkpoint.setdefault("output", snapshot)
    decisions = task.get("deliveries") or []
    if decisions:
        decision = decisions[-1]
        checkpoint.update(delivery=decision, delivery_source=decision.get("source", ""),
                         feedback=decision.get("feedback", ""))
    ended_at = attempt.get("ended_at", "") or (task.get("updated_at", "") if status in
                                                   ("delivered", "awaiting_review", "blocked", "failed", "cancelled") else "")
    error = attempt.get("error", "") or (run.get("error", "") if status in ("failed", "cancelled") else "")
    db.db.execute("UPDATE workflow_nodes SET status=?,checkpoint=?,output_assets=?,error=?,ended_at=? WHERE id=?",
                  (status, json.dumps(checkpoint, ensure_ascii=False, default=str),
                   json.dumps(assets, ensure_ascii=False), error[:2000], ended_at, origin["legacy_id"]))
    db.db.execute("UPDATE workflow_runs SET current_node=? WHERE id=?", (origin["legacy_id"], run["id"]))
    row = db.db.execute("SELECT settings FROM workflow_executions WHERE run_id=?", (run["id"],)).fetchone()
    if row:
        settings = json.loads(row["settings"] or "{}")
        settings["current_node_id"] = task.get("workflow_node_id", "")
        db.db.execute("UPDATE workflow_executions SET settings=?,updated_at=? WHERE run_id=?",
                      (json.dumps(settings, ensure_ascii=False), tasks._now(), run["id"]))
    db.db.commit()


def _accepted_history(task):
    delivery = next(item for item in task['deliveries'] if item['id'] == task['accepted_delivery_id'])
    output = delivery['output_versions']
    return {'task_id': task['id'], 'node_id': task['workflow_node_id'], 'delivery_id': delivery['id'],
            'output_revision': task['latest_run']['checkpoint'].get('output_revision', 0),
            'content': output.get('content', ''), 'outputs': output.get('files', []),
            'feedback': delivery.get('feedback', '')}


def submit_manual(db, run, body):
    from pathlib import Path
    import workflow_execution as engine
    node_id = str(body.get('node_id') or '')
    task_id = run['settings'].get('task_ids', {}).get(node_id)
    if not task_id:
        raise ValueError('节点不属于本次运行')
    task = tasks.get(db, task_id, project_id=run['project_id'])
    pending = run['settings'].get('manual_pending', {})
    if pending.get('node_id') != node_id or run['status'] not in ('blocked', 'awaiting_review'):
        raise ValueError('该节点当前无法提交人工产物')
    if task['status'] not in ('ready', 'blocked', 'failed'):
        raise ValueError('节点已提交或尚未就绪')
    text = str(body.get('text') or '').strip()
    paths = body.get('paths') or []
    if not isinstance(paths, list) or len(paths) > 20:
        raise ValueError('最多提交 20 个项目文件')
    root = tasks._root(db, run['project_id'])
    files = []
    for value in paths:
        relative = Path(str(value))
        path = (root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root) or not path.is_file() or not path.stat().st_size:
            raise ValueError('请提供存在且非空的项目相对文件：' + str(value))
        files.append(str(path))
    if not text and not files:
        raise ValueError('请输入结果或提交至少一个项目文件')
    attempt = tasks.start(db, task_id, authority=tasks.CommandContext.ui(run['project_id']), restart=True, manual_submission=True)
    _record_attempt(db, run, node_id, task, attempt, attempt['input_snapshot'])
    try:
        output = worker._outputs(db, task, attempt, {}, {'content': text, 'files': files})
        task = tasks.complete(db, attempt['id'], {'output_snapshot': output, 'reply': '已提交人工产物',
                                                  'checkpoint': {'execution_source': 'human'}})
    except Exception as exc:
        task = tasks.fail(db, attempt['id'], worker._error_detail(exc))
        _sync_workflow_node(db, run, task)
        raise
    _sync_workflow_node(db, run, task)
    latest = engine.detail(db, run['id'])
    latest['settings'].pop('manual_pending', None)
    _save(db, latest)
    engine._status(db, run['id'], task['status'])
    return engine.detail(db, run['id'])


def advance(db, run_id, cancel_check=None):
    import workflow_execution as engine
    import execution
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
    from threading import Event
    from workflow_db import WorkflowDB
    run = engine.detail(db, run_id)
    if run["status"] in ("awaiting_review", "succeeded", "cancelled"):
        return run
    try:
        run = compile_run(db, run)
        run["settings"]["prepared"] = False
        _save(db, run)
        engine._exclusive(db, run)
        if run["settings"].get("handoff_id"):
            import file_handoff
            file_handoff.validate_run(db, run)
        engine._status(db, run_id, "running")
        def prepare_node_attempt(nid, task_id):
            with execution.scope(cancel_check):
                execution.check()
                run = engine.detail(db, run_id)
                if run["status"] == "cancelled":
                    return {"status": run["status"]}
                task = tasks.get(db, task_id)
                if task["status"] == "delivered":
                    _record_attempt(db, run, nid, task, task["latest_run"], task["latest_run"]["checkpoint"].get("input") or {})
                    tasks._validate_versions(db, task, task["latest_run"])
                    _sync_workflow_node(db, run, task, task["latest_run"])
                    return {"status": "delivered"}
                if task["status"] == "awaiting_review":
                    _sync_workflow_node(db, run, task, task.get("latest_run"))
                    if run["settings"].get("dag_scheduler"):
                        return {"status": "awaiting_review"}
                    engine._status(db, run_id, "awaiting_review")
                    return {"status": engine.detail(db, run_id)["status"]}
                node = task["metadata"]["node_config"]
                run["settings"]["current_node_id"] = nid
                if node.get('manual_execution'):
                    run['settings']['manual_pending'] = {'node_id': nid, 'task_id': task_id, 'label': node['label']}
                    _save(db, run)
                    engine._status(db, run_id, 'blocked', '等待提交人工产物：' + node['label'])
                    return {'status': 'blocked'}
                _save(db, run)
                if run["settings"].get("legacy_stage_compat"):
                    snapshot = engine._snapshot(db, run, node)
                else:
                    snapshot = {"goal": run["settings"].get("goal", ""), "history": [
                        _accepted_history(t)
                        for t in tasks.list_tasks(db, run["project_id"], workflow_run_id=run_id) if t["status"] == "delivered"],
                        "memory": db.memory_block(run["project_id"]), "input_assets": [], "file_input": run["settings"].get("file_input")}
                metadata = {**task["metadata"], "upstream_context": snapshot}
                db.db.execute("UPDATE project_tasks SET metadata=? WHERE id=?", (json.dumps(metadata, ensure_ascii=False), task_id))
                db.db.commit()
                task_run = tasks.start(db, task_id, authority=tasks.CommandContext.system(run["project_id"]))
                _record_attempt(db, run, nid, tasks.get(db, task_id), task_run, snapshot)
                return {"attempt_id": task_run["id"]}

        def finish_node(task_id):
            current = engine.detail(db, run_id)
            task = tasks.get(db, task_id)
            _sync_workflow_node(db, current, task, task.get("latest_run"))
            status = task["status"] if task["status"] in ("delivered", "awaiting_review", "blocked", "cancelled", "running", "queued") else "failed"
            if status != "delivered" and not current["settings"].get("dag_scheduler"):
                attempt = task["latest_run"] or {}
                error = attempt.get("error", "")
                if status == "blocked" and attempt.get("checkpoint", {}).get("waiting_children"):
                    error = "等待团队成员交付"
                engine._status(db, run_id, status, error)
            return {"status": status}

        def execute_node(nid, task_id):
            prepared = prepare_node_attempt(nid, task_id)
            if "attempt_id" not in prepared:
                return prepared
            worker.execute(db, prepared["attempt_id"], cancel_check)
            return finish_node(task_id)

        def execute_worker(attempt_id, stopped):
            with WorkflowDB(db.path) as node_db:
                worker.execute(node_db, attempt_id, lambda: stopped.is_set() or bool(cancel_check and cancel_check()))

        def execute_wave(batch, *, excluded=()):
            """Drain the ready frontier, including successors unlocked by completed workers."""
            if str(db.path) == ':memory:':
                outcomes = {}
                for nid, task_id in batch:
                    try:
                        outcomes[nid] = execute_node(nid, task_id)
                    except (ValueError, OSError) as exc:
                        tasks.block_start(db, task_id, exc, authority=tasks.CommandContext.system(run['project_id']))
                        outcomes[nid] = {"status": "failed", "error": str(exc)}
                    if outcomes[nid]['status'] == 'cancelled':
                        break
                return outcomes
            outcomes, pending, attempted = {}, {}, set(excluded)
            stopped = Event()

            def finish_future(future):
                nid, task_id = pending.pop(future)
                try:
                    future.result()
                except execution.Cancelled:
                    outcomes[nid] = {'status': 'cancelled'}
                except (ValueError, OSError) as exc:
                    outcomes[nid] = {'status': 'failed', 'error': str(exc)}
                else:
                    outcomes[nid] = finish_node(task_id)
                if outcomes[nid]['status'] == 'cancelled':
                    stopped.set()

            # Only this connection prepares/finalizes attempts. Each worker opens its own connection.
            with ThreadPoolExecutor(max_workers=3, thread_name_prefix='workflow-node') as pool:
                try:
                    while batch or pending:
                        with execution.scope(cancel_check):
                            if stopped.is_set() or engine.detail(db, run_id)['status'] == 'cancelled':
                                raise execution.Cancelled('运行已中断')
                            for nid, task_id in batch:
                                if len(pending) >= 3:
                                    break
                                if nid in attempted:
                                    continue
                                attempted.add(nid)
                                try:
                                    prepared = prepare_node_attempt(nid, task_id)
                                except (ValueError, OSError) as exc:
                                    tasks.block_start(db, task_id, exc, authority=tasks.CommandContext.system(run['project_id']))
                                    prepared = {'status': 'failed', 'error': str(exc)}
                                if 'attempt_id' in prepared:
                                    future = pool.submit(execute_worker, prepared['attempt_id'], stopped)
                                    pending[future] = (nid, task_id)
                                else:
                                    outcomes[nid] = prepared
                                    if prepared['status'] == 'cancelled':
                                        raise execution.Cancelled('运行已中断')
                        if pending:
                            completed, _ = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
                            for future in completed:
                                finish_future(future)
                            if not completed:
                                continue
                        rows = {item['id']: item for item in tasks.list_tasks(db, run['project_id'], workflow_run_id=run_id)}
                        in_flight = {task_id for _, task_id in pending.values()}
                        batch = [(nid, task_id) for nid, task_id in run['settings']['task_ids'].items()
                                 if nid not in attempted and rows[task_id]['status'] in ('ready', 'queued', 'blocked', 'failed', 'interrupted')
                                 and not rows[task_id]['metadata']['node_config'].get('manual_execution')
                                 and all(parent not in in_flight and rows[parent]['status'] == 'delivered'
                                         for parent in rows[task_id]['dependencies'])]
                    with execution.scope(cancel_check):
                        if stopped.is_set() or engine.detail(db, run_id)['status'] == 'cancelled':
                            raise execution.Cancelled('运行已中断')
                finally:
                    stopped.set()
                    for future in list(pending):
                        finish_future(future)
            return outcomes
        from workflow_checkpoint import drive
        result = drive(db, run, execute_node, execute_wave)
        if result.get('__interrupt__') or result["status"] != "delivered":
            return engine.detail(db, run_id)
        engine._status(db, run_id, "succeeded")
    except Exception as exc:
        engine._status(db, run_id, "cancelled" if isinstance(exc, execution.Cancelled) else "failed", worker._error_detail(exc))
    return engine.detail(db, run_id)


def review(db, run_id, node_id, approved, feedback="",reviewed_output_version=None, reviewed_run_id=''):
    import workflow_execution as engine
    run = compile_run(db, engine.detail(db, run_id))
    task = tasks.find_origin(db, "workflow_node", node_id)
    if not task or task["workflow_run_id"] != run_id or run["status"] != "awaiting_review":
        raise ValueError("该节点已审查或不是当前待审查节点")
    origin = next((item for item in task['origins'] if item['kind'] == 'workflow_node' and item['legacy_id'] == node_id), None)
    if not origin or origin['task_run_id'] != task['latest_run_id'] or (reviewed_run_id and reviewed_run_id != origin['task_run_id']):
        raise ValueError('该节点已有新的尝试，请重新打开当前产物后核查')
    tasks.review(db, task["id"], approved=approved, feedback=feedback, authority=tasks.CommandContext.ui(run["project_id"]),
                 reviewed_run_id=origin['task_run_id'], reviewed_output_version=reviewed_output_version)
    reviewed = tasks.get(db, task["id"])
    _sync_workflow_node(db, run, reviewed, reviewed.get("latest_run"))
    engine._status(db, run_id, "queued" if approved else "blocked", "" if approved else "人工审查已退回，请按修改意见重试节点")
    return engine.detail(db, run_id)


def rework_targets(db, task):
    """Return only direct or transitive prerequisites of this canonical task."""
    if not task.get("workflow_run_id") or task.get("parent_task_id") or task["status"] != "awaiting_review":
        return []
    import workflow_execution as engine
    run = engine.detail(db, task["workflow_run_id"])
    allowed = set(run["settings"].get("task_ids", {}).values())
    ancestors = {}
    pending = list(task["dependencies"])
    while pending:
        task_id = pending.pop()
        if task_id in ancestors or task_id not in allowed:
            continue
        parent = tasks.get(db, task_id, project_id=task["project_id"])
        ancestors[task_id] = parent
        pending.extend(parent["dependencies"])
    return [{"node_id": nid, "title": ancestors[tid]["title"], "stage": ancestors[tid]["metadata"].get("stage")}
            for nid, tid in run["settings"]["task_ids"].items()
            if tid in ancestors and ancestors[tid]["status"] == "delivered"]


def rework_receipt(db, task_id, request_key, request_digest):
    row = db.db.execute(
        "SELECT data FROM project_task_events WHERE task_id=? AND kind='returned_for_rework' "
        "AND json_extract(data, '$.request_key')=?", (task_id, request_key)).fetchone()
    if not row:
        return None
    data = json.loads(row["data"])
    if data["request_digest"] != request_digest:
        raise ValueError("该请求标识已用于另一次返工")
    return {key: data[key] for key in ("workflow_run_id", "target_node_id", "target_task_id")}


def return_for_rework(db, task, attempt, target_node, feedback, request_key, request_digest, authority):
    """Revise a predecessor and invalidate only deliveries that consume it."""
    import workflow_execution as engine

    project_id, run_id = task["project_id"], task["workflow_run_id"]
    tasks._authority(authority, project_id, human=True)
    feedback = str(feedback or "").strip()
    if not feedback or not request_key or len(request_key) > 200:
        raise ValueError("请选择返工节点并填写核查意见")
    with tasks._lock, tasks._atomic(db):
        receipt = rework_receipt(db, task["id"], request_key, request_digest)
        if receipt:
            return receipt
        run = engine.detail(db, run_id)
        task = tasks.get(db, task["id"], project_id=project_id)
        latest = task.get("latest_run") or {}
        if (task["status"] != "awaiting_review" or run["status"] != "awaiting_review"
                or latest.get("id") != attempt["id"]
                or latest.get("checkpoint", {}).get("output_revision", 0) != attempt["checkpoint"].get("output_revision", 0)):
            raise ValueError("当前核查版本已变化，请重新打开产物")
        if target_node not in {item["node_id"] for item in rework_targets(db, task)}:
            raise ValueError("只能退回本次运行中已交付的前置节点")
        mapping = run["settings"]["task_ids"]
        target = mapping[target_node]
        rows = {item["id"]: item for item in tasks.list_tasks(db, project_id, workflow_run_id=run_id)}
        affected = {target}
        for nid in run["layout"]["order"]:
            tid = mapping.get(nid)
            if tid and affected.intersection(rows[tid]["dependencies"]):
                affected.add(tid)
        pending = list(affected)
        while pending:
            for child in rows[pending.pop()]["children"]:
                if child["id"] not in affected:
                    affected.add(child["id"])
                    rows[child["id"]] = tasks.get(db, child["id"], project_id=project_id)
                    pending.append(child["id"])
        if any(item["status"] in ("queued", "running") for item in rows.values()):
            raise ValueError("本次运行仍有任务在执行，请稍后再退回")
        tasks._decision(db, task, latest, approved=False, source="human", actor_id=authority.actor_id,
                        feedback=feedback, request_key=request_key)
        stamp = tasks._now()
        for tid in affected:
            item = rows[tid]
            metadata = dict(item["metadata"])
            metadata.pop("_start_error", None)
            metadata.pop("_return_feedback", None)
            metadata.pop("_return_base_run", None)
            previous = item.get("latest_run") or {}
            if previous.get("status") == "succeeded" and previous.get("output_snapshot"):
                metadata["_return_base_run"] = previous["id"]
            if tid == target or item["parent_task_id"] == target:
                metadata["_return_feedback"] = feedback
            db.db.execute("UPDATE project_tasks SET status='ready',latest_run_id='',accepted_delivery_id='',metadata=?,"
                          "version=version+1,updated_at=? WHERE id=?", (tasks._json(metadata), stamp, tid))
            origins = [origin["legacy_id"] for origin in item["origins"] if origin["kind"] == "workflow_node"]
            db.db.executemany("UPDATE workflow_nodes SET status='superseded' WHERE id=?", [(oid,) for oid in origins])
            event = {"workflow_run_id": run_id, "target_node_id": target_node, "target_task_id": target,
                     "review_task_id": task["id"]}
            if tid == task["id"]:
                event.update(request_key=request_key, request_digest=request_digest)
            tasks._event(db, tid, "returned_for_rework", event, item["latest_run_id"])
        settings = run["settings"]
        settings["checkpoint_epoch"] = int(settings.get("checkpoint_epoch", 0)) + 1
        settings["current_node_id"] = target_node
        if "dag_errors" in settings:
            settings["dag_errors"] = {nid: error for nid, error in settings["dag_errors"].items()
                                      if mapping.get(nid) not in affected}
        for key in ("checkpoint", "manual_pending"):
            settings.pop(key, None)
        db.db.execute("UPDATE workflow_executions SET settings=?,updated_at=? WHERE run_id=?",
                      (tasks._json(settings), stamp, run_id))
        db.db.execute("UPDATE workflow_runs SET status='queued',current_node='',error='',ended_at='' WHERE id=?", (run_id,))
    return {"workflow_run_id": run_id, "target_task_id": target, "target_node_id": target_node}


def set_mode(db, run_id, mode, *, review_mode=""):
    import workflow_execution as engine
    run = compile_run(db, engine.detail(db, run_id))
    if mode:
        if mode not in ("supervised", "full_access"):
            raise ValueError("无效的执行模式")
        for task in tasks.list_tasks(db, run["project_id"], workflow_run_id=run_id):
            if task["status"] not in ("delivered", "cancelled"):
                tasks.set_mode(db, task["id"], mode, authority=tasks.CommandContext.ui(run["project_id"]))
        run["settings"]["policy"] = {"autonomy_mode": mode, "source": "user"}
    if review_mode:
        if review_mode not in bindings.WORKFLOW_REVIEW_MODES:
            raise ValueError("无效的工作流审查模式")
        for task in tasks.list_tasks(db, run["project_id"], workflow_run_id=run_id):
            if task["status"] not in ("delivered", "cancelled"):
                tasks.set_review_mode(db, task["id"], review_mode, authority=tasks.CommandContext.ui(run["project_id"]))
        run["settings"]["review_mode"] = review_mode
    if not mode and not review_mode:
        raise ValueError("无效的执行模式")
    _save(db, run)
    return engine.detail(db, run_id)


def cancel(db, run_id, *, authority=None):
    import workflow_execution as engine
    run = engine.detail(db, run_id)
    command = authority or tasks.CommandContext.ui(run["project_id"])
    if command.parent_task_id:
        parent = tasks.get(db, command.parent_task_id, project_id=run["project_id"])
        if parent["workflow_run_id"] != run_id:
            raise ValueError("只能停止当前任务所属的工作流")
        command = tasks.CommandContext.system(run["project_id"])
    for task in tasks.list_tasks(db, run["project_id"], workflow_run_id=run_id):
        if task["status"] not in ("delivered", "cancelled"):
            tasks.cancel(db, task["id"], authority=command)
    engine._status(db, run_id, "cancelled", "用户停止运行")
    return engine.detail(db, run_id)
