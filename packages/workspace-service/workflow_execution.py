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
    run['chat_id']=(execution_conversations.link(db,'workflow',run_id) or {}).get('chat_id','')
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


def review(db, run_id, node_id, approved, feedback="",reviewed_output_version=None):
    import workflow_tasks
    return workflow_tasks.review(db, run_id, node_id, approved, feedback,reviewed_output_version)


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
    import workflow_tasks
    action = path.removeprefix(prefix).strip("/")
    run_id = str(body.get("id") or "")
    if action == "prepare-node":
        with _lock:
            return workflow_tasks.prepare_node(db, body)
    if action in ("prepare", "start"):
        run = detail(db, run_id) if run_id else workflow_tasks.prepare(db,
            str(body.get("task_id") or body.get("project_id") or ""), body.get("goal", ""),
            body.get("idempotency_key", ""), str(body.get("template_id") or ""))
        if any(body.get(k) and str(body[k]) != run["project_id"] for k in ("task_id", "project_id")):
            raise ValueError("运行不属于当前项目")
        if body.get("autonomy_mode") is not None:
            run = workflow_tasks.set_mode(db, run["id"], body["autonomy_mode"])
        if action == "start":
            run["settings"]["prepared"] = False
            workflow_tasks._save(db, run)
            kick(db.path, run["id"])
    else:
        with _lock:
            run = detail(db, run_id)
            if any(body.get(k) and str(body[k]) != run["project_id"] for k in ("task_id", "project_id")):
                raise ValueError("运行不属于当前项目")
            if action == "review":
                run = review(db, run_id, str(body.get("node_id") or ""), body.get("approved"), body.get("feedback", ""),body.get('reviewed_output_version'))
                if run["status"] == "queued":
                    kick(db.path, run_id)
            elif action == "mode":
                run = workflow_tasks.set_mode(db, run_id, str(body.get("autonomy_mode") or ""))
                if run["status"] == "queued":
                    kick(db.path, run_id)
            elif action == "resume":
                if run["status"] not in ("failed", "blocked", "interrupted", "queued"):
                    raise ValueError("当前运行不能重试")
                _exclusive(db, run)
                import project_tasks
                run = workflow_tasks.compile_run(db, run)
                current = project_tasks.find_origin(db, "workflow_node", run["current_node"]) if run["current_node"] else None
                if current:
                    if body.get("restart_node") is True:
                        old = next(n for n in run["nodes"] if n["id"] == run["current_node"])
                        _checkpoint(db, old["id"], {**old["checkpoint"], "restart_approved": True})
                        project_tasks.start(db, current["id"], authority=project_tasks.CommandContext.ui(run["project_id"]), restart=True)
                    elif (current.get("latest_run") or {}).get("checkpoint", {}).get("phase") == "tool":
                        raise ValueError("工具结果未确认，请核对后选择重新执行当前节点")
                if run["settings"].get("handoff_id"):
                    import file_handoff
                    file_handoff.validate_run(db, run)
                _status(db, run_id, "queued")
                kick(db.path, run_id)
            elif action == "cancel":
                signal = _workers.get((str(Path(db.path).resolve()), run_id))
                if signal:
                    signal.set()
                run = workflow_tasks.cancel(db, run_id)
            else:
                raise ValueError("不支持的工作流操作")
    return {"run": detail(db, run["id"])}
