"""Sequential, durable project workflow execution with reviewed handoffs."""
from __future__ import annotations

import json
from pathlib import Path
import threading
import uuid

import asset_service
import execution
import run_service
import workflow
import workflow_layout
import workflow_state

_lock = threading.RLock()
_workers = {}
ACTIVE = ("queued", "running", "awaiting_review", "blocked", "interrupted")


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def detail(db, run_id):
    run = run_service.detail(db, run_id)
    saved = db.db.execute("SELECT * FROM workflow_executions WHERE run_id=?", (run_id,)).fetchone()
    if not run or not saved:
        raise ValueError("工作流执行不存在")
    run.update(layout=json.loads(saved["layout_snapshot"]), settings=json.loads(saved["settings"]))
    for node in run["nodes"]:
        for key in ("checkpoint", "acceptance", "input_assets", "output_assets"):
            node[key] = json.loads(node[key])
    return run


def latest(db, project_id):
    row = db.db.execute("SELECT r.id FROM workflow_runs r JOIN workflow_executions e ON e.run_id=r.id "
                        "WHERE r.project_id=? AND r.workflow='research-sequence' ORDER BY r.rowid DESC LIMIT 1", (project_id,)).fetchone()
    return detail(db, row["id"]) if row else None


def active(db, project_id):
    row = db.db.execute("SELECT r.id FROM workflow_runs r JOIN workflow_executions e ON e.run_id=r.id "
                        "WHERE r.project_id=? AND r.status IN ('queued','running','awaiting_review','blocked','interrupted') "
                        "ORDER BY r.rowid DESC LIMIT 1", (project_id,)).fetchone()
    return detail(db, row["id"]) if row else None


def _exclusive(db, run):
    other = db.db.execute("SELECT r.id FROM workflow_runs r JOIN workflow_executions e ON e.run_id=r.id "
        "WHERE r.project_id=? AND r.id<>? AND r.status IN ('queued','running','awaiting_review','blocked','interrupted') LIMIT 1",
        (run["project_id"], run["id"])).fetchone()
    if other:
        raise ValueError("项目已有其他执行或待审查任务，请先完成或停止现有运行")


def _status(db, run_id, status, error=""):
    db.db.execute("UPDATE workflow_runs SET status=?,error=?,ended_at=? WHERE id=?",
        (status, error[:2000], workflow.now() if status in ("succeeded", "failed", "cancelled") else "", run_id))
    db.db.execute("UPDATE workflow_executions SET updated_at=? WHERE run_id=?", (workflow.now(), run_id))
    db.db.commit()

    import schedule_workflow
    schedule_workflow.refresh(db, run_id)


def create(db, project_id, goal="", idempotency_key=""):
    workflow_layout._project(db, project_id)
    layout = workflow_state.definition(db, project_id)
    frozen = {**layout, **workflow_layout.validate_definition(layout)}
    with _lock:
        old = active(db, project_id)
        if old and old["workflow"] == "file-handoff":
            raise ValueError("项目有未结束的文件节点任务，请先完成审查或停止该任务")
        if old:
            return old
        if idempotency_key:
            row = db.db.execute("SELECT id FROM workflow_runs WHERE project_id=? AND idempotency_key=?",
                                (project_id, idempotency_key)).fetchone()
            if row:
                return detail(db, row["id"])
        # Validate every actor and selected capability before any model request.
        for node in frozen["nodes"]:
            _actor(db, project_id, workflow_layout.resolve_node(db, project_id, frozen["id"], node))
        if any(n["step"] == "idea" for n in frozen["nodes"]) and not db.idea_input(project_id).strip():
            task = db.summary(project_id)["task"]
            db.idea_input_save(project_id, str(goal).strip() or task.get("question") or task["title"])
        run = run_service.start(db, project_id, "research-sequence", idempotency_key=idempotency_key or "workflow:" + uuid.uuid4().hex)
        db.db.execute("INSERT INTO workflow_executions VALUES (?,?,?,?)", (run["id"], _json(frozen), _json({"goal": str(goal)[:20000]}), workflow.now()))
        _status(db, run["id"], "queued")
        return detail(db, run["id"])


def _actor(db, project_id, node):
    import capabilities
    if any(node.get(k) for k in ("execution_mode", "assistant_id", "team_id", "member_id")):
        import file_handoff
        resolved = file_handoff.actors(db, project_id, node)[0]
        node = {**node, **{k: resolved[k] for k in ("assistant_id", "team_id", "member_id")}}
    member = None
    team_id, member_id = node.get("team_id", ""), node.get("member_id", "")
    if team_id:
        team = db.team(team_id)
        if not team or team.get("status") == "archived" or team.get("project_id") not in ("", project_id):
            raise ValueError("节点团队不存在或绑定了其他项目")
        roster = db.team_members(team_id)
        member = next((m for m in roster if m["id"] == member_id), None) if member_id else next(
            (m for m in roster if m.get("step") == node["step"]), next((m for m in roster if m["role"] == "leader"), None))
        if not member:
            raise ValueError("节点团队没有可用执行成员，请选择成员或配置 Leader")
    elif member_id:
        raise ValueError("选择团队成员前请先选择团队")
    aid = node.get("assistant_id") or (member or {}).get("assistant_id") or "builtin-steward"
    db.require_assistant(aid)
    project_dir = str(db.base_dir(project_id) or "")
    skills = []
    for skill_id in node.get("skills") or []:
        skill = capabilities.invoke_tool(db, aid, "skill_read", {"skill_id": skill_id}, project_dir)
        skills.append({"id": skill_id, "content": skill["content"], "hash": workflow_state.digest(skill["content"])})
    if node.get("tools") is not None:
        available = {s["id"] for s in capabilities.list_tools(db)}
        available.update(t["function"]["name"] for t in capabilities.capability_tool_specs(db, aid, project_dir))
        for name in node["tools"]:
            if name not in available or not capabilities.enabled(db, aid, "tool", name):
                raise ValueError("节点工具不可用或已禁用：" + name)
    return aid, member, skills


def _snapshot(db, run, node):
    project_id = run["project_id"]
    state = workflow_state.states(db, project_id)
    if state[node["step"]]["blocked_by"]:
        raise ValueError("上游交付尚未验收或已变化：" + "、".join(state[node["step"]]["blocked_by"]))
    handoff, fingerprint, assets = workflow_state.handoff(db, project_id, node["step"])
    history = [{"node_id": n["id"], "step": n["stage"], "delivery_id": n["checkpoint"].get("delivery_id", ""),
                "reply": n["checkpoint"].get("reply", ""), "feedback": n["checkpoint"].get("feedback", ""),
                "output": n["checkpoint"].get("output", {})} for n in run["nodes"] if n["status"] == "delivered"]
    file_input = run["settings"].get("file_input")
    if file_input:
        assets = list(dict.fromkeys([*assets, file_input["asset_id"], *[f["asset_id"] for f in file_input.get("related_inputs", [])]]))
    return {"project": db.summary(project_id)["task"], "file_input": file_input,
            "handoff": json.loads(handoff or "[]"), "upstream_hash": fingerprint, "history": history,
            "memory": db.memory_block(project_id), "goal": run["settings"].get("goal", ""), "input_assets": assets}


def _unchanged(db, run, snapshot, step):
    if run["settings"].get("handoff_id"):
        import file_handoff
        file_handoff.validate_run(db, run)
    current = workflow_state.definition(db, run["project_id"])
    normalized = workflow_layout.validate_definition(current)
    if any(normalized[key] != run["layout"][key] for key in ("nodes", "edges", "order")):
        raise ValueError("工作流定义已变化，请结束本次运行后重新启动")
    _, digest, _ = workflow_state.handoff(db, run["project_id"], step)
    if digest != snapshot["upstream_hash"] or workflow_state.gate(db, run["project_id"], step)["blocked"]:
        raise ValueError("上游交付已变化，请重新执行当前节点")


def _checkpoint(db, node_id, checkpoint):
    db.db.execute("UPDATE workflow_nodes SET checkpoint=? WHERE id=?", (_json(checkpoint), node_id))
    db.db.commit()


def _deliver(db, run, record, checkpoint):
    step = record["stage"]
    _unchanged(db, run, checkpoint["input"], step)
    current = workflow_state.capture(db, run["project_id"], step)
    if current["fingerprint"] != checkpoint["output"]["fingerprint"]:
        raise ValueError("待审查产物已变化，请重新执行当前节点后审查")
    accepted = workflow_state.accept(db, run["project_id"], step, checkpoint.get("feedback", ""))
    checkpoint.update(delivery_id=accepted["id"], reviewed_at=workflow.now())
    run_service.finish_node(db, record["id"], "delivered", outputs=record["output_assets"], checkpoint=checkpoint)
    # Persist useful conclusions and human decisions for later independent sessions.
    db.add_memory(run["project_id"], "episode", workflow.STAGE_LABEL[step] + "交付 " + accepted["id"] + "：" +
                  checkpoint.get("reply", "")[:1600] + ("\n人工反馈：" + checkpoint["feedback"] if checkpoint.get("feedback") else ""))
    _team_mark(db, checkpoint, "done")


def _team_mark(db, checkpoint, status, reason=""):
    if checkpoint.get("team_task_id"):
        db.team_task_mark(checkpoint["team_task_id"], status, result=checkpoint.get("reply", ""), reason=reason,
                         ended_at=workflow.now(), review_feedback=checkpoint.get("feedback", ""),
                         review_status="approved" if status == "done" else "pending")


def advance(db, run_id, cancel_check=None):
    """Run in order until the next human gate, failure or completion."""
    run = detail(db, run_id)
    if run["status"] in ("awaiting_review", "succeeded", "cancelled"):
        return run
    _status(db, run_id, "running")
    config = {n["id"]: n for n in run["layout"]["nodes"]}
    try:
        _exclusive(db, run)
        if run["settings"].get("handoff_id"):
            import file_handoff
            file_handoff.validate_run(db, run)
        for node_id in run["settings"].get("node_ids", run["layout"]["order"]):
            with execution.scope(cancel_check):
                run = detail(db, run_id)
                node = workflow_layout.resolve_node(db, run["project_id"], run["layout"]["id"], config[node_id])
                if run["settings"].get("handoff_id"):
                    node = {**node, **{k: run["settings"]["actor"][k] for k in
                                      ("execution_mode", "assistant_id", "team_id", "member_id")}}
                previous = [n for n in run["nodes"] if n["acceptance"].get("node_id") == node_id]
                if previous and previous[-1]["status"] == "delivered":
                    if workflow_state.states(db, run["project_id"])[node["step"]]["status"] != "delivered":
                        raise ValueError("已完成节点的产物发生变化，请结束本次运行并重新启动")
                    continue
                if run["status"] == "cancelled":
                    return run
                snapshot = _snapshot(db, run, node)
                aid, member, skills = _actor(db, run["project_id"], node)
                snapshot.update(assistant_id=aid, member=member, skills=skills)
                import agent_settings
                cfg = agent_settings.runtime_config(db, run['project_id'], aid, member)
                selected_model = db.model(cfg['model_id']) if cfg['model_id'] else db.active_model()
                snapshot['runtime'] = {**cfg, 'model_id': (selected_model or {}).get('id', ''),
                    'model': (selected_model or {}).get('model', '')}
                _unchanged(db, run, snapshot, node['step'])
                prior = previous[-1]['checkpoint'] if previous else {}
                can_resume = bool(previous and previous[-1]['status'] in ('failed', 'interrupted') and not prior.get('restart_approved')
                    and all(prior.get('input', {}).get(k) == snapshot.get(k) for k in ('upstream_hash', 'assistant_id', 'member', 'skills', 'goal', 'runtime')))
                if can_resume and prior.get('agent', {}).get('phase') == 'tool':
                    raise ValueError('工具中断时结果未确认，请核对后选择重新执行当前节点')
                record = run_service.node_start(db, run_id, node["step"], inputs=snapshot["input_assets"],
                    acceptance={"node_id": node_id, "review_required": node.get("review_required", True), "config": node})
                checkpoint = {"input": snapshot, "feedback": previous[-1]["checkpoint"].get("feedback", "") if previous else ""}
                checkpoint['agent'] = prior.get('agent', {}) if can_resume else {}
                checkpoint['resumed_from'] = previous[-1]['id'] if can_resume else ''
                _checkpoint(db, record["id"], checkpoint)
                db.log_step(run["project_id"], node["step"], "running", "工作流顺序执行")
                saved_runtime = {name: getattr(db, name, None) for name in ('job_checkpoint', 'save_agent_checkpoint', 'workflow_context_run_id')}
                db.job_checkpoint = checkpoint['agent']
                db.workflow_context_run_id = run_id
                def save_agent(**values):
                    checkpoint['agent'].update(values)
                    _checkpoint(db, record['id'], checkpoint)
                db.save_agent_checkpoint = save_agent
                try:
                    if member:
                        task = db.team_task_add(node["team_id"], member["id"], member["name"], node["label"], node.get("prompt", ""),
                                                trigger="workflow", origin="workflow:" + run_id, commit=False)
                        db.db.execute("UPDATE team_tasks SET status='running',run_id=? WHERE id=?", (run_id, task["id"]))
                        db.db.commit()
                        checkpoint["team_task_id"] = task["id"]
                        db.team_event_add(task["id"], node["team_id"], "start", "工作流执行「" + node["label"] + "」")
                        _checkpoint(db, record["id"], checkpoint)
                    # Full snapshots remain on disk; prompts carry each preceding result and its evidence references.
                    import workflow_context
                    context = "【工作流连续上下文与已确认的前序结果】\n" + workflow_context.prompt(snapshot, run_id, record['id'])
                    if snapshot.get("file_input"):
                        context += "\n【文件交接任务】仅处理当前节点。使用 file_input.path 中的固定输入快照，原始路径仅用于追溯。任务要求：\n" + snapshot["goal"]
                    if checkpoint["feedback"]:
                        context += "\n【人工退回意见，须落实】\n" + checkpoint["feedback"]
                    if member:
                        context += "\n【团队成员职责】\n" + member.get("prompt", "")
                    for skill in skills:
                        context += "\n【本节点指定技能 " + skill["id"] + "】\n" + skill["content"]
                    prompt = node.get("prompt") or ("完成「" + node["label"] + "」，通过工作台工具保存本节点实际产物，复用上游证据与结论，并说明完成情况。")
                    from workflow_catalog import STAGE_PURPOSE
                    system = ("你在执行有依赖关系的科研工作流。当前节点职责：" + STAGE_PURPOSE[node["step"]] +
                              "。必须承接提供的上游成果和人工反馈，使用工具落盘真实产物。只执行当前节点，不启动其他节点或另行派活。")
                    tools = _tools(db, aid, node, run["project_id"])
                    if '已节选' in context or '超出本次输入预算' in context:
                        if not any(s['function']['name'] == 'workflow_context_read' for s in tools):
                            raise ValueError('上下文需要分页读取，请在本节点允许的工具中加入 workflow_context_read')
                    db.workflow_end_to_end = True
                    import agent_settings
                    cfg = agent_settings.runtime_config(db, run["project_id"], aid, member)
                    selected_model = db.model(cfg["model_id"]) if cfg["model_id"] else db.active_model()
                    db.runtime_model = {**selected_model, "reasoning": cfg["reasoning"], "permission": cfg["permission"]} if selected_model else None
                    out = workflow.run_agent(db, run["project_id"], prompt, system=system, tools=tools,
                        extra_context=context, assistant_id=aid, member=member, scope=node["step"], step=node["step"],
                        publish=True, max_steps=40, cancel_check=cancel_check, tool_allowlist=node.get("tools"))
                    execution.check()
                    _unchanged(db, run, snapshot, node["step"])
                    output = workflow_state.capture(db, run["project_id"], node["step"])
                    if not output["valid"]:
                        raise ValueError("节点未生成完整产物，请检查工具记录并重试")
                    checkpoint.update(reply=out.get("reply", ""), output=output, agent_run_id=out.get("run_id", ""))
                    assets = out.get("output_assets") or []
                    db.log_step(run["project_id"], node["step"], "succeeded", "产物已生成，等待交付审查")
                    run_service.finish_node(db, record["id"], "awaiting_review", outputs=assets, checkpoint=checkpoint)
                    record["output_assets"] = assets
                    if node.get("review_required", True):
                        _team_mark(db, checkpoint, "review", "请在工作流中进行人工审查")
                        _status(db, run_id, "awaiting_review")
                        return detail(db, run_id)
                    _deliver(db, run, record, checkpoint)
                except Exception as exc:
                    status = "cancelled" if isinstance(exc, execution.Cancelled) else "failed"
                    run_service.finish_node(db, record["id"], status, checkpoint=checkpoint, error=str(exc))
                    _team_mark(db, checkpoint, "canceled" if status == "cancelled" else "failed", str(exc))
                    db.log_step(run["project_id"], node["step"], status, str(exc)[:300])
                    raise
                finally:
                    db.runtime_model = None
                    db.workflow_end_to_end = False
                    for name, value in saved_runtime.items():
                        if value is None:
                            db.__dict__.pop(name, None)
                        else:
                            setattr(db, name, value)
        _status(db, run_id, "succeeded")
    except Exception as exc:
        _status(db, run_id, "cancelled" if isinstance(exc, execution.Cancelled) else "failed", str(exc))
    return detail(db, run_id)


def _tools(db, aid, node, project_id):
    import agent_integrations
    import capabilities
    from workflow_documents import doc_tools, map_tools
    scoped = doc_tools(node["step"]) if node["step"] in ("outline", "write", "review") else map_tools() if node["step"] == "map" else []
    specs = [*workflow.TOOL_SPECS, *agent_integrations.tool_specs(), *scoped,
             *capabilities.capability_tool_specs(db, aid, str(db.base_dir(project_id) or ""))]
    # Orchestration remains owned by this durable run, including when a broad assistant is selected.
    forbidden = {"delegate", "team_assign", "team_manage", "workflow_manage", "schedule_manage"}
    return [s for s in specs if s["function"]["name"] not in forbidden and
            (node.get("tools") is None or s["function"]["name"] in node["tools"])]


def review(db, run_id, node_id, approved, feedback=""):
    if not isinstance(approved, bool):
        raise ValueError("请明确选择通过或退回")
    run = detail(db, run_id)
    node = next((n for n in run["nodes"] if n["id"] == node_id), None)
    if not node or node_id != run["current_node"] or run["status"] != "awaiting_review" or node["status"] != "awaiting_review":
        raise ValueError("该节点已审查或不是当前待审查节点")
    checkpoint = node["checkpoint"]
    checkpoint["feedback"] = str(feedback).strip()[:20000]
    if approved:
        _deliver(db, run, node, checkpoint)
        _status(db, run_id, "queued")
    else:
        if not checkpoint["feedback"]:
            raise ValueError("退回节点时请填写修改意见")
        run_service.finish_node(db, node_id, "rejected", checkpoint=checkpoint, outputs=node["output_assets"])
        _team_mark(db, checkpoint, "failed", "人工审查退回")
        _status(db, run_id, "blocked", "人工审查已退回，请按修改意见重试节点")
    return detail(db, run_id)


def kick(db_path, run_id):
    key = (str(Path(db_path).resolve()), run_id)
    with _lock:
        if key in _workers:
            return
        signal = threading.Event()
        _workers[key] = signal
    def work():
        try:
            with workflow.WorkflowDB(db_path) as db:
                advance(db, run_id, signal.is_set)
        finally:
            with _lock:
                _workers.pop(key, None)
                with workflow.WorkflowDB(db_path) as db:
                    if (run_service.get(db, run_id) or {}).get("status") == "queued":
                        kick(db_path, run_id)
    threading.Thread(target=work, daemon=True, name="workflow-sequence").start()


def recover(db_path):
    with workflow.WorkflowDB(db_path) as db:
        for row in db.db.execute("SELECT r.id FROM workflow_runs r JOIN workflow_executions e ON e.run_id=r.id "
                                 "WHERE r.status IN ('queued','running')").fetchall():
            key = (str(Path(db_path).resolve()), row["id"])
            if key not in _workers:
                db.db.execute("UPDATE workflow_nodes SET status='interrupted',error='服务重启，请核对产物后重试' "
                              "WHERE run_id=? AND status='running'", (row["id"],))
                _status(db, row["id"], "interrupted", "服务重启，已保存节点上下文；请核对当前产物后重试")


def route(method, path, body, query, db):
    prefix = "/api/workflow/execution"
    if not path.startswith(prefix):
        return None
    if method == "GET":
        run_id = (query.get("id") or [""])[0]
        return {"run": detail(db, run_id) if run_id else latest(db, (query.get("task_id") or [""])[0])}
    action = path.removeprefix(prefix).strip("/")
    if action == "start":
        run = create(db, str(body.get("task_id") or ""), body.get("goal", ""), body.get("idempotency_key", ""))
        kick(db.path, run["id"])
    else:
        run_id = str(body.get("id") or "")
        with _lock:
            run = detail(db, run_id)
            if action == "review":
                run = review(db, run_id, str(body.get("node_id") or ""), body.get("approved"), body.get("feedback", ""))
                if run["status"] == "queued":
                    kick(db.path, run_id)
            elif action == "resume":
                if run["status"] not in ("failed", "blocked", "interrupted", "queued"):
                    raise ValueError("当前运行不能重试")
                _exclusive(db, run)
                current = next((n for n in run['nodes'] if n['id'] == run['current_node']), None)
                if current and body.get('restart_node') is True:
                    _checkpoint(db, current['id'], {**current['checkpoint'], 'restart_approved': True})
                elif current and current['checkpoint'].get('agent', {}).get('phase') == 'tool':
                    raise ValueError('工具结果未确认，请核对后选择重新执行当前节点')
                if run["settings"].get("handoff_id"):
                    import file_handoff
                    file_handoff.validate_run(db, run)
                _status(db, run_id, "queued")
                kick(db.path, run_id)
            elif action == "cancel":
                if run["status"] in ("succeeded", "cancelled"):
                    return {"run": run}
                signal = _workers.get((str(Path(db.path).resolve()), run_id))
                if signal:
                    signal.set()
                elif run["current_node"]:
                    current = next(n for n in run["nodes"] if n["id"] == run["current_node"])
                    if current["status"] == "awaiting_review":
                        run_service.finish_node(db, current["id"], "cancelled", outputs=current["output_assets"],
                                                checkpoint=current["checkpoint"], error="用户停止运行")
                        _team_mark(db, current["checkpoint"], "canceled", "用户停止运行")
                _status(db, run_id, "cancelled", "用户停止运行")
            else:
                raise ValueError("不支持的工作流操作")
    return {"run": detail(db, run["id"])}
