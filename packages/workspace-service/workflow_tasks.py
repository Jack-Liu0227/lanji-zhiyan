"""Compile workflow node identities into project tasks and coordinate their attempts."""
from __future__ import annotations

import json
import uuid

import project_bindings as bindings
import project_tasks as tasks
import project_task_execution as worker
import workflow_layout
import workflow_state


def _save(db, run):
    db.db.execute("UPDATE workflow_executions SET settings=? WHERE run_id=?",
                  (json.dumps(run["settings"], ensure_ascii=False), run["id"]))
    db.db.commit()


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
    elif selected or any(raw.get(key) for key in ('assistant_id','team_id','execution_mode')) or saved.get("source") != "user":
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
    return executor, resolved_node


def compile_run(db, run):
    """Idempotent conversion, including a file-handoff run created by the old adapter."""
    if run["settings"].get("task_ids"):
        return run
    project_id, layout = run["project_id"], run["layout"]
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
    current_layout = workflow_state.definition(db, project_id)
    compat = (current_layout["id"] == layout["id"] and all(s in workflow_layout.STAGES for s in stages)
              and len(stages) == len(set(stages)))
    mapping = {}
    for nid in selected_ids:
        executor, node = resolved[nid]
        deps = [mapping[e["source"]] for e in layout["edges"] if e["target"] == nid and e["source"] in mapping]
        inputs = list(run["settings"].get("task_inputs") or [])
        source = run["settings"].get("file_input")
        if source:
            root = tasks._root(db, project_id)
            from pathlib import Path
            inputs = [{"relative": Path(source["path"]).resolve().relative_to(root).as_posix(), "sha256": source["sha256"]}]
            inputs.extend({"relative": Path(f["path"]).resolve().relative_to(root).as_posix(), "sha256": f["sha256"]} for f in source.get("related_inputs", []))
        scope = {"kind": "workflow", "allowed_binding_ids": allowed}
        if members:
            # Team-wide nodes can dispatch to their own configured members only.
            members_for_flow = set(members)
            for ex, original in resolved.values():
                if ex.get("team_id") and not config[original["id"]].get("member_id"):
                    members_for_flow.update(m["id"] for m in db.team_members(ex["team_id"]))
            scope["allowed_member_ids"] = sorted(members_for_flow)
        if node.get("tools") is not None:
            scope["tools"] = node["tools"]
        goal = (run["settings"].get("goal") if run["settings"].get("single_node") else node.get("prompt")) or run["settings"].get("goal") or ("完成「" + node["label"] + "」，保存实际产物并说明结果。")
        task = tasks.create(db, {"project_id": project_id, "title": node["label"], "goal": goal,
            "executor_binding_id": executor["binding_id"], "member_id": executor.get("requested_member_id", "") if not run["settings"].get("actor") else executor.get("member_id", ""),
            "inputs": inputs, "dependencies": deps, "output_contract": node.get("output_contract") or {},
            "review_required": node.get("review_required", True), "workflow_run_id": run["id"], "workflow_node_id": nid,
            "scope": scope, "metadata": {"stage": node["step"], "node_config": node,
                "legacy_stage_compat": compat, "layout_snapshot": layout,
                "workflow_node": {"run_id": run["id"], "node_id": nid},
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


def prepare(db, project_id, goal="", idempotency_key="", template_id="", *, inputs=None):
    import workflow_execution as engine
    import run_service
    import workflow
    workflow_layout._project(db, project_id)
    workflow_layout.ensure_schema(db)
    if idempotency_key:
        row = db.db.execute("SELECT id FROM workflow_runs WHERE project_id=? AND idempotency_key=?", (project_id, idempotency_key)).fetchone()
        if row:
            return engine.detail(db, row["id"])
    old = engine.active(db, project_id)
    if old:
        if old["workflow"] == "file-handoff":
            raise ValueError("项目有未结束的文件节点任务，请先完成审查或停止该任务")
        return old
    import project_workflows
    layout = project_workflows.current(db,project_id,template_id) if template_id else workflow_state.definition(db, project_id)
    from workflow_composition import expand
    frozen = expand(db, project_id, layout)
    bindings.import_legacy(db, project_id)
    for node in frozen["nodes"]:
        executor, _ = _executor(db, project_id, frozen, node)
        if node.get('node_kind') == 'tool':
            worker.validate_fixed_tool(db, project_id, node, executor)
    if any(n["step"] == "idea" for n in frozen["nodes"]) and not db.idea_input(project_id).strip():
        project = db.summary(project_id)["task"]
        db.idea_input_save(project_id, str(goal).strip() or project.get("question") or project["title"])
    run = run_service.start(db, project_id, "research-sequence", idempotency_key=idempotency_key or "workflow:" + uuid.uuid4().hex)
    settings = {"goal": str(goal)[:20000], "policy": bindings.settings(db, project_id), "prepared": True, "task_inputs": inputs or []}
    db.db.execute("INSERT INTO workflow_executions VALUES (?,?,?,?)", (run["id"], json.dumps(frozen, ensure_ascii=False), json.dumps(settings, ensure_ascii=False), workflow.now()))
    engine._status(db, run["id"], "queued")
    compile_run(db, engine.detail(db, run["id"]))
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
    if not goal or len(goal) > 100000 or not isinstance(inputs, list):
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


def advance(db, run_id, cancel_check=None):
    import workflow_execution as engine
    import run_service
    import execution
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
        def execute_node(nid, task_id):
            with execution.scope(cancel_check):
                execution.check()
                run = engine.detail(db, run_id)
                if run["status"] == "cancelled":
                    return {"status": run["status"]}
                task = tasks.get(db, task_id)
                if task["status"] == "delivered":
                    _record_attempt(db, run, nid, task, task["latest_run"], task["latest_run"]["checkpoint"].get("input") or {})
                    tasks._validate_versions(db, task, task["latest_run"])
                    return {"status": "delivered"}
                if task["status"] == "awaiting_review":
                    engine._status(db, run_id, "awaiting_review")
                    return {"status": engine.detail(db, run_id)["status"]}
                node = task["metadata"]["node_config"]
                if run["settings"].get("legacy_stage_compat"):
                    snapshot = engine._snapshot(db, run, node)
                else:
                    snapshot = {"goal": run["settings"].get("goal", ""), "history": [
                        {"task_id": t["id"], "node_id": t["workflow_node_id"], "outputs": t["outputs"], "deliveries": t["deliveries"]}
                        for t in tasks.list_tasks(db, run["project_id"], workflow_run_id=run_id) if t["status"] == "delivered"],
                        "memory": db.memory_block(run["project_id"]), "input_assets": [], "file_input": run["settings"].get("file_input")}
                metadata = {**task["metadata"], "upstream_context": snapshot}
                db.db.execute("UPDATE project_tasks SET metadata=? WHERE id=?", (json.dumps(metadata, ensure_ascii=False), task_id))
                db.db.commit()
                task_run = tasks.start(db, task_id, authority=tasks.CommandContext.system(run["project_id"]))
                _record_attempt(db, run, nid, tasks.get(db, task_id), task_run, snapshot)
                task = worker.execute(db, task_run["id"], cancel_check)
                task = tasks.get(db, task_id)
                if task["status"] != "delivered":
                    status = task["status"] if task["status"] in ("awaiting_review", "blocked", "cancelled", "running", "queued") else "failed"
                    engine._status(db, run_id, status, (task["latest_run"] or {}).get("error", ""))
                    return {"status": engine.detail(db, run_id)["status"]}
            return {"status": "delivered"}
        from workflow_checkpoint import drive
        result = drive(db, run, execute_node)
        if result.get('__interrupt__') or result["status"] != "delivered":
            return engine.detail(db, run_id)
        engine._status(db, run_id, "succeeded")
    except Exception as exc:
        engine._status(db, run_id, "cancelled" if isinstance(exc, execution.Cancelled) else "failed", str(exc))
    return engine.detail(db, run_id)


def review(db, run_id, node_id, approved, feedback="",reviewed_output_version=None):
    import workflow_execution as engine
    run = compile_run(db, engine.detail(db, run_id))
    task = tasks.find_origin(db, "workflow_node", node_id)
    if not task or task["workflow_run_id"] != run_id or run["status"] != "awaiting_review":
        raise ValueError("该节点已审查或不是当前待审查节点")
    tasks.review(db, task["id"], approved=approved, feedback=feedback, authority=tasks.CommandContext.ui(run["project_id"]),reviewed_output_version=reviewed_output_version)
    engine._status(db, run_id, "queued" if approved else "blocked", "" if approved else "人工审查已退回，请按修改意见重试节点")
    return engine.detail(db, run_id)


def set_mode(db, run_id, mode):
    import workflow_execution as engine
    run = compile_run(db, engine.detail(db, run_id))
    if mode not in ("supervised", "full_access"):
        raise ValueError("无效的执行模式")
    for task in tasks.list_tasks(db, run["project_id"], workflow_run_id=run_id):
        if task["status"] not in ("delivered", "cancelled"):
            tasks.set_mode(db, task["id"], mode, authority=tasks.CommandContext.ui(run["project_id"]))
    run["settings"]["policy"] = {"autonomy_mode": mode, "source": "user"}
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
