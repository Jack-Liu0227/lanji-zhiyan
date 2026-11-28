"""Sequential, durable project workflow execution with reviewed handoffs."""
from __future__ import annotations

import json
from pathlib import Path
import threading

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
    import execution_conversations
    run['chat_id']=execution_conversations.ensure(db,'workflow',run_id)
    for node in run["nodes"]:
        for key in ("checkpoint", "acceptance", "input_assets", "output_assets"):
            node[key] = json.loads(node[key])
    return run


def public_view(run):
    if run is None:
        return None
    return {**run, 'nodes': [{**node, 'checkpoint': {k: v for k, v in node.get('checkpoint', {}).items()
                 if k in ('reply', 'phase', 'turn', 'output_revision', 'canonical_task_id', 'canonical_run_id', 'delivery_source')}}
                 for node in run.get('nodes', [])]}


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
    import execution_conversations
    import execution_board
    execution_conversations.touch(db,'workflow',run_id)
    project=db.db.execute('SELECT project_id FROM workflow_runs WHERE id=?',(run_id,)).fetchone()[0]
    execution_board.publish(db,project,'workflow_'+status,run_id=run_id,source='workflow',activity=status)
    db.db.commit()

    import schedule_workflow
    schedule_workflow.refresh(db, run_id)


def create(db, project_id, goal="", idempotency_key=""):
    import workflow_tasks
    return workflow_tasks.prepare(db, project_id, goal, idempotency_key)


def _actor(db, project_id, node):
    import capabilities
    team = db.team(node.get('team_id',''))
    if team and team.get('project_id') != project_id:
        node = workflow_layout.resolve_node(db, project_id, '', node)
    if any(node.get(k) for k in ("execution_mode", "assistant_id", "team_id", "member_id")):
        import file_handoff
        resolved = file_handoff.actors(db, project_id, node)[0]
        node = {**node, **{k: resolved[k] for k in ("assistant_id", "team_id", "member_id")}}
    member = None
    team_id, member_id = node.get("team_id", ""), node.get("member_id", "")
    if team_id:
        team = db.team(team_id)
        if not team or team.get("status") == "archived" or team.get("project_id") != project_id:
            raise ValueError("节点团队不存在或绑定了其他项目")
        roster = db.team_members(team_id)
        member = next((m for m in roster if m["id"] == member_id), None) if member_id else next(
            (m for m in roster if m.get("step") == node["step"]), next((m for m in roster if m["role"] == "leader"), None))
        if not member:
            raise ValueError("节点团队没有可用执行成员，请选择成员或配置 Leader")
    elif member_id:
        raise ValueError("选择团队成员前请先选择团队")
    aid = node.get("assistant_id") or (member or {}).get("assistant_id")
    if not aid:
        raise ValueError("尚未配置执行专家或团队，请绑定项目工作流角色")
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
    state = workflow_state.states(db, project_id,run['layout'])
    if state[node["step"]]["blocked_by"]:
        raise ValueError("上游交付尚未验收或已变化：" + "、".join(state[node["step"]]["blocked_by"]))
    handoff, fingerprint, assets = workflow_state.handoff(db, project_id, node["step"],run['layout'])
    history = [{"node_id": n["id"], "step": n["stage"], "delivery_id": n["checkpoint"].get("delivery_id", ""),
                "reply": n["checkpoint"].get("reply", ""), "feedback": n["checkpoint"].get("feedback", ""),
                "output": n["checkpoint"].get("output", {})} for n in run["nodes"] if n["status"] == "delivered"]
    file_input = run["settings"].get("file_input")
    if file_input:
        assets = list(dict.fromkeys([*assets, file_input["asset_id"], *[f["asset_id"] for f in file_input.get("related_inputs", [])]]))
    return {"project": db.summary(project_id)["task"], "file_input": file_input,
            "handoff": json.loads(handoff or "[]"), "upstream_hash": fingerprint, "history": history,
            "memory": db.memory_block(project_id), "goal": run["settings"].get("goal", ""), "input_assets": assets}


def _checkpoint(db, node_id, checkpoint):
    import project_tasks
    task = project_tasks.find_origin(db, "workflow_node", node_id)
    if task:
        origin = next(o for o in task["origins"] if o["kind"] == "workflow_node" and o["legacy_id"] == node_id)
        project_tasks.save_checkpoint(db, origin["task_run_id"], **{**checkpoint, **checkpoint.get("agent", {})})
    db.db.execute("UPDATE workflow_nodes SET checkpoint=? WHERE id=?", (_json(checkpoint), node_id))
    db.db.commit()




def advance(db, run_id, cancel_check=None):
    import workflow_tasks
    return workflow_tasks.advance(db, run_id, cancel_check)


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


def review(db, run_id, node_id, approved, feedback="",reviewed_output_version=None, reviewed_run_id=''):
    import workflow_tasks
    return workflow_tasks.review(db, run_id, node_id, approved, feedback,reviewed_output_version, reviewed_run_id)


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
                with workflow.WorkflowDB(db_path) as db:
                    queued = (run_service.get(db, run_id) or {}).get('status') == 'queued'
                _workers.pop(key, None)
                if queued:
                    kick(db_path, run_id)
    threading.Thread(target=work, daemon=True, name="workflow-sequence").start()


def recover(db_path):
    import project_task_execution
    project_task_execution.recover(db_path)
    with workflow.WorkflowDB(db_path) as db:
        for row in db.db.execute("SELECT r.id FROM workflow_runs r JOIN workflow_executions e ON e.run_id=r.id "
                                 "WHERE r.status IN ('queued','running')").fetchall():
            key = (str(Path(db_path).resolve()), row["id"])
            if key not in _workers:
                if detail(db, row["id"])["settings"].get("prepared"):
                    continue
                task_statuses = [
                    r["status"]
                    for r in db.db.execute("SELECT status FROM project_tasks WHERE workflow_run_id=?", (row["id"],)).fetchall()
                ]
                if task_statuses and "awaiting_review" in task_statuses and not any(
                    s in ("running", "interrupted", "failed") for s in task_statuses
                ):
                    _status(db, row["id"], "awaiting_review", "")
                    continue
                if task_statuses and all(s in ("delivered", "succeeded") for s in task_statuses):
                    _status(db, row["id"], "succeeded", "")
                    continue
                db.db.execute("UPDATE workflow_nodes SET status='interrupted',error='服务重启，请核对产物后重试' "
                              "WHERE run_id=? AND status='running'", (row["id"],))
                _status(db, row["id"], "interrupted", "服务重启，已保存节点上下文；请核对当前产物后重试")


def resume(db, run_id, *, restart_node=False, node_id='', request_key='', expected_task_run_id='', authority=None, allow_model_switch=False, model_id=''):
    import project_tasks
    import workflow_tasks
    with _lock:
        run = detail(db, run_id)
        authority = authority or project_tasks.CommandContext.ui(run['project_id'])
        project_tasks._authority(authority, run['project_id'])
        if authority.parent_task_id:
            raise ValueError('节点任务不能自行恢复整个工作流')
        if restart_node and authority.source == 'agent':
            raise ValueError('重新执行本节点须由用户明确操作')
        model_id = str(model_id or '').strip()
        if model_id:
            allow_model_switch = True
        receipts = run['settings'].get('resume_requests', {})
        signature = {'node_id': node_id, 'restart_node': restart_node, 'expected_task_run_id': expected_task_run_id, 'allow_model_switch': bool(allow_model_switch), 'model_id': model_id}
        if request_key and request_key in receipts:
            if receipts[request_key] != signature:
                raise ValueError('恢复请求标识已用于其他操作，请刷新后重试')
            return run
        if (str(Path(db.path).resolve()), run_id) in _workers:
            raise ValueError('工作流仍在保存或等待其他分支结束，请稍后恢复')
        if run["status"] not in ("failed", "blocked", "interrupted", "queued", "awaiting_review"):
            raise ValueError("当前运行不能恢复，请先检查任务状态")
        _exclusive(db, run)
        run = workflow_tasks.compile_run(db, run)
        tasks = project_tasks.list_tasks(db, run['project_id'], workflow_run_id=run_id)
        if any(task['status'] == 'running' for task in tasks):
            raise ValueError('工作流仍有节点执行中，请等待当前分支结束')
        errors = dict(run['settings'].get('dag_errors', {}))
        recoverable = []
        for task in tasks:
            attempt = task.get('latest_run') or {}
            checkpoint = attempt.get('checkpoint', {})
            nid = task['workflow_node_id']
            if task['metadata'].get('node_config', {}).get('manual_execution') or checkpoint.get('waiting_children'):
                continue
            if task['status'] in ('delivered', 'awaiting_review', 'cancelled'):
                continue
            if task['status'] in ('failed', 'interrupted') or nid in errors or (
                    task['status'] == 'blocked' and attempt.get('status') in ('failed', 'interrupted')):
                recoverable.append(task)
        targets = [task for task in recoverable if not node_id or task['workflow_node_id'] == node_id]
        if not targets:
            # Review approval queues continuations through its own command.
            if run['status'] == 'queued' and not node_id:
                kick(db.path, run_id)
                return run
            raise ValueError('没有可恢复的失败节点，请检查人工核查、人工提交或依赖状态')
        if expected_task_run_id and (len(targets) != 1 or targets[0].get('latest_run_id') != expected_task_run_id):
            raise ValueError('节点已有新的尝试，请刷新状态后重试')
        for task in targets:
            if (task.get('latest_run') or {}).get('checkpoint', {}).get('phase') == 'tool' and not restart_node:
                raise ValueError('工具结果未确认，请核对后选择重新执行当前节点')
        if run["settings"].get("handoff_id"):
            import file_handoff
            file_handoff.validate_run(db, run)
        # Keep all non-target failures excluded when retrying a single branch.
        for task in recoverable:
            errors.setdefault(task['workflow_node_id'], (task.get('latest_run') or {}).get('error') or '节点等待恢复')
        for task in targets:
            nid = task['workflow_node_id']
            project_tasks.start(db, task['id'], authority=authority,
                                request_key=(request_key + ':' + nid) if request_key else '', restart=restart_node,
                                allow_model_switch=allow_model_switch, model_id=model_id)
            errors.pop(nid, None)
            workflow_tasks._sync_workflow_node(db, run, project_tasks.get(db, task['id']))
        run = detail(db, run_id)
        run['settings']['dag_errors'] = errors
        if request_key:
            run['settings'].setdefault('resume_requests', {})[request_key] = signature
        workflow_tasks._save(db, run)
        _status(db, run_id, "queued")
        kick(db.path, run_id)
        return detail(db, run_id)


def route(method, path, body, query, db):
    prefix = "/api/workflow/execution"
    if not path.startswith(prefix):
        return None
    if method == "GET":
        project_id = (query.get("project_id") or [""])[0]
        if path == prefix + "/preflight" and project_id:
            import agent_settings
            import project_workflows
            template_id = (query.get("template_id") or [""])[0]
            layout = project_workflows.current(db, project_id, template_id)
            return agent_settings.preflight_layout(db, project_id, layout)
        if path == prefix and project_id and not query.get("id"):
            rows = db.db.execute(
                "SELECT r.id, r.started_at, r.status, json_extract(e.layout_snapshot, '$.id') AS layout_id, "
                "json_extract(e.layout_snapshot, '$.name') AS layout_name, "
                "json_extract(e.layout_snapshot, '$.order') AS node_order "
                "FROM workflow_runs r JOIN workflow_executions e ON e.run_id=r.id "
                "WHERE r.project_id=? ORDER BY r.started_at DESC, r.rowid DESC LIMIT 100", (project_id,))
            return {"runs": [{**dict(row), "node_order": json.loads(row["node_order"] or "[]")} for row in rows]}
        run_id = (query.get("id") or [""])[0]
        return {"run": public_view(detail(db, run_id) if run_id else latest(db, (query.get("task_id") or [""])[0]))}
    import workflow_tasks
    action = path.removeprefix(prefix).strip("/")
    run_id = str(body.get("id") or "")
    if action == "prepare-node":
        with _lock:
            return workflow_tasks.prepare_node(db, body)
    if action in ("prepare", "start"):
        existing = detail(db, run_id) if run_id else None
        project_id = existing['project_id'] if existing else str(body.get('task_id') or body.get('project_id') or '')
        if any(body.get(k) and str(body[k]) != project_id for k in ('task_id', 'project_id')):
            raise ValueError('运行不属于当前项目')
        origin_chat_id = ''
        if 'chat_id' in body:
            import workspace_trajectory
            origin_chat_id = str(body.get('chat_id') or '')
            workspace_trajectory.owner(db, origin_chat_id, project_id)
        run = existing if existing else workflow_tasks.prepare(db,
            str(body.get("task_id") or body.get("project_id") or ""), body.get("goal", ""),
            body.get("idempotency_key", ""), str(body.get("template_id") or ""),
            managed_artifacts=body.get("managed_artifacts", False),
            review_mode=str(body.get("review_mode") or ""))
        if origin_chat_id:
            run['settings']['origin_chat_id'] = origin_chat_id
            workflow_tasks._save(db, run)
        if any(body.get(k) and str(body[k]) != run["project_id"] for k in ("task_id", "project_id")):
            raise ValueError("运行不属于当前项目")
        if body.get("autonomy_mode") is not None or body.get("review_mode") is not None:
            run = workflow_tasks.set_mode(db, run["id"], str(body.get("autonomy_mode") or ""),
                                          review_mode=str(body.get("review_mode") or ""))
        if action == "start":
            run["settings"]["prepared"] = False
            workflow_tasks._save(db, run)
            kick(db.path, run["id"])
    else:
        with _lock:
            run = detail(db, run_id)
            if any(body.get(k) and str(body[k]) != run["project_id"] for k in ("task_id", "project_id")):
                raise ValueError("运行不属于当前项目")
            if action == "submit-manual":
                if (str(Path(db.path).resolve()), run_id) in _workers:
                    raise ValueError("请稍等，工作流正在保存状态")
                run = workflow_tasks.submit_manual(db, run, body)
            elif action == "review":
                if not body.get('reviewed_run_id') or body.get('reviewed_output_version') is None:
                    raise ValueError('请在当前节点产物页核查，并提交对应尝试和修订版本')
                run = review(db, run_id, str(body.get("node_id") or ""), body.get("approved"), body.get("feedback", ""),
                             body.get('reviewed_output_version'), str(body['reviewed_run_id']))
                if run["status"] == "queued":
                    kick(db.path, run_id)
            elif action == "mode":
                run = workflow_tasks.set_mode(db, run_id, str(body.get("autonomy_mode") or ""),
                                              review_mode=str(body.get("review_mode") or ""))
                if run["status"] == "queued":
                    kick(db.path, run_id)
            elif action == "resume":
                model_id = str(body.get('model_id') or '')
                run = resume(db, run_id, restart_node=body.get("restart_node") is True,
                             node_id=str(body.get('node_id') or ''), request_key=str(body.get('request_key') or ''),
                             expected_task_run_id=str(body.get('expected_task_run_id') or ''),
                             allow_model_switch=bool(body.get('allow_model_switch') is True or model_id),
                             model_id=model_id)
            elif action == "cancel":
                signal = _workers.get((str(Path(db.path).resolve()), run_id))
                if signal:
                    signal.set()
                run = workflow_tasks.cancel(db, run_id)
            elif action == "delete":
                if (str(Path(db.path).resolve()), run_id) in _workers or run["status"] == "running" or (
                        run["status"] == "queued" and not run["settings"].get("prepared")):
                    raise ValueError("无法删除正在运行中的工作流，请先停止运行")
                with db.db:
                    task_ids = [r[0] for r in db.db.execute("SELECT id FROM project_tasks WHERE workflow_run_id=?", (run_id,)).fetchall()]
                    entity_ids = [run_id, *task_ids]
                    entity_marks = ",".join("?" for _ in entity_ids)
                    chat_ids = list({
                        "execution-" + run_id,
                        *("execution-" + tid for tid in task_ids),
                        *(r[0] for r in db.db.execute(f"SELECT chat_id FROM execution_conversations WHERE entity_id IN ({entity_marks})", entity_ids).fetchall()),
                    })
                    db.db.execute("DELETE FROM review_reminders WHERE node_id IN (SELECT id FROM workflow_nodes WHERE run_id=?)", (run_id,))
                    db.db.execute("DELETE FROM workflow_nodes WHERE run_id=?", (run_id,))
                    db.db.execute("DELETE FROM schedule_workflow_runs WHERE workflow_run_id=?", (run_id,))
                    db.db.execute("DELETE FROM workflow_executions WHERE run_id=?", (run_id,))
                    db.db.execute("DELETE FROM workflow_runs WHERE id=?", (run_id,))
                    for tid in task_ids:
                        db.db.execute("DELETE FROM project_task_runs WHERE task_id=?", (tid,))
                        db.db.execute("DELETE FROM project_task_deliveries WHERE task_id=?", (tid,))
                        db.db.execute("DELETE FROM project_task_events WHERE task_id=?", (tid,))
                        db.db.execute("DELETE FROM execution_conversations WHERE entity_id=?", (tid,))
                    db.db.execute("DELETE FROM project_tasks WHERE workflow_run_id=?", (run_id,))
                    db.db.execute("DELETE FROM execution_conversations WHERE entity_id=?", (run_id,))
                    if chat_ids:
                        chat_marks = ",".join("?" for _ in chat_ids)
                        agent_runs = [r[0] for r in db.db.execute(f"SELECT id FROM tasks WHERE owner_id IN ({chat_marks}) AND kind='agent_run'", chat_ids).fetchall()]
                        if agent_runs:
                            run_marks = ",".join("?" for _ in agent_runs)
                            db.db.execute(f"DELETE FROM task_attempts WHERE task_id IN ({run_marks})", agent_runs)
                            db.db.execute(f"DELETE FROM run_events WHERE run_id IN ({run_marks})", agent_runs)
                            db.db.execute(f"DELETE FROM background_jobs WHERE id IN ({run_marks})", agent_runs)
                            db.db.execute(f"DELETE FROM tasks WHERE id IN ({run_marks})", agent_runs)
                        db.db.execute(f"DELETE FROM chat WHERE task_id IN ({chat_marks})", chat_ids)
                        import memory_store
                        memory_store.safe_delete_fts(db.db, "chat_fts", f"task_id IN ({chat_marks})", chat_ids)
                        db.db.execute(f"DELETE FROM chats WHERE id IN ({chat_marks})", chat_ids)
                        if db.db.execute("SELECT 1 FROM sqlite_master WHERE name='execution_board_hidden'").fetchone():
                            db.db.execute(f"DELETE FROM execution_board_hidden WHERE project_id=? AND item_kind='conversation' AND item_id IN ({chat_marks})", [run["project_id"], *chat_ids])
                    if task_ids and db.db.execute("SELECT 1 FROM sqlite_master WHERE name='execution_board_hidden'").fetchone():
                        tid_marks = ",".join("?" for _ in task_ids)
                        db.db.execute(f"DELETE FROM execution_board_hidden WHERE project_id=? AND item_kind='task' AND item_id IN ({tid_marks})", [run["project_id"], *task_ids])
                    import execution_board
                    execution_board.publish(db, run["project_id"], "workflow_deleted", run_id=run_id, source="workflow", activity="工作流运行已删除")
                return {"deleted": True, "id": run_id}
            else:
                raise ValueError("不支持的工作流操作")
    return {"run": detail(db, run["id"])}
