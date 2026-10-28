"""Durable Agent requests; connections only observe, workers own execution."""
from __future__ import annotations

import threading
import uuid

import execution
import conversation_history
from run_manager import RunManager

import workflow_runners


TERMINAL = {"done", "failed", "cancelled", "interrupted"}


@conversation_history.synchronized
def start(db_path, request: dict, step: str = "", *, resume_id: str = ""):
    import workflow
    manager = RunManager.get_instance()
    with workflow.WorkflowDB(db_path) as db:
        request = dict(request)
        step = step or str(request.get("step") or "")
        if resume_id:
            task = db.get_async_task(resume_id)
            if not task or not task["checkpoint"].get("request"):
                raise ValueError("没有可恢复的请求")
            if task["status"] in ("done", "cancelled"):
                raise ValueError("已完成或取消的任务不能恢复")
            if task["checkpoint"].get("phase") == "tool":
                raise ValueError("中断发生在工具执行中，结果尚未确认；请核对产物后发起新的任务")
            request, step = task["checkpoint"]["request"], task["checkpoint"]["step"]
        tid = str(request.get("task_id") or request.get("chat_id") or "")
        if not tid and request.get("idempotency_key"):
            existing = db.get_async_task_by_idempotency(request["idempotency_key"])
            if existing:
                tid = existing["owner_id"]
        if not (db.db.execute("SELECT 1 FROM chats WHERE id=?", (tid,)).fetchone()
                or db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (tid,)).fetchone()):
            if resume_id:
                raise ValueError("原项目或会话已删除")
            tid = db.create_chat("")
        request["task_id"] = tid
        hidden=db.db.execute('SELECT deleted_at,archived_at FROM chats WHERE id=?',(tid,)).fetchone()
        if hidden and (hidden['deleted_at'] or hidden['archived_at']):raise ValueError('会话已归档或删除，请先恢复再执行')
        if not resume_id:
            existing = db.get_async_task_by_idempotency(str(request.get("idempotency_key") or ""))
            saved_request = (existing or {}).get("checkpoint", {}).get("request")
            if saved_request:
                if existing["owner_id"] != tid:
                    raise ValueError("该任务标识已用于其他会话或项目")
                request = dict(saved_request)
            else:
                request.pop("_template_snapshot", None)
                chat = db.db.execute("SELECT task_id FROM chats WHERE id=?", (tid,)).fetchone()
                project = chat["task_id"] if chat else (tid if db.base_dir(tid) else "")
                if request.get("template_ref") is not None and not project:
                    raise ValueError("引用提示词模板前请先绑定项目")
                if project:
                    import prompt_templates
                    import agent_settings
                    import workspace_chat
                    cfg = workspace_chat.snapshot(db, tid, request) if chat else agent_settings.runtime_config(db, tid)
                    request["_template_snapshot"] = prompt_templates.resolve(db, project, request.get("template_ref"), step=step, assistant_id=cfg["assistant_id"], member_id=cfg.get('member_id',''))
        if not resume_id and not saved_request:
            import workspace_chat
            request['_runtime_snapshot']=workspace_chat.snapshot(db,tid,request)
        request["idempotency_key"] = str(request.get("idempotency_key") or uuid.uuid4().hex)
        run = manager.create_run(tid, request["idempotency_key"], db)
        if resume_id:
            if run.worker_started:
                return run
            run.status = "running"
            run.cancel_requested = False
        if not run.claim():
            return run
        checkpoint = (db.get_async_task(run.run_id) or {}).get("checkpoint") or {}
        checkpoint.update({"request": request, "step": step})
        db.update_async_task(run.run_id, status="running", checkpoint=checkpoint)
        run.emit({"type": "run_started", "run_id": run.run_id, "task_id": tid,
                  "idempotency_key": request["idempotency_key"]}, db)
    thread = threading.Thread(target=_work, args=(db_path, run, request, step), daemon=True,
                              name="agent-" + run.run_id)
    thread.start()
    return run


def _work(db_path, run, request, step):
    try:
        _execute(db_path, run, request, step)
    finally:
        run.worker_started = False


def _execute(db_path, run, request, step):
    import workflow
    with workflow.WorkflowDB(db_path) as db:
        tid = request["task_id"]
        checkpoint = (db.get_async_task(run.run_id) or {}).get("checkpoint") or {}
        db.job_checkpoint = checkpoint
        db.template_request = request.get("template_ref")
        db.template_snapshot = request.get("_template_snapshot")
        db.template_resolved = "_template_snapshot" in request
        db.resuming = bool(checkpoint.get("messages") or checkpoint.get("completed_calls"))
        owner=db.db.execute('SELECT task_id FROM chats WHERE id=?',(tid,)).fetchone()
        db.usage_context={'owner_id':tid,'project_id':owner['task_id'] if owner else tid,'run_id':run.run_id,'assistant_id':(request.get('_runtime_snapshot') or {}).get('assistant_id','')}
        import workspace_chat
        selected = request.get('_runtime_snapshot') or {}

        def save(**values):
            checkpoint.update(values)
            db.update_async_task(run.run_id, checkpoint=checkpoint)

        db.save_agent_checkpoint = save
        final = ""
        error = ""

        def emit(event):
            nonlocal final, error
            if event.get("type") == "done":
                final = event.get("content") or ""
                return
            if event.get("type") == "error":
                error = event.get("message") or "执行失败"
                return
            run.emit(event, db)

        track = bool(step in workflow.STAGES and db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (tid,)).fetchone())
        try:
            workspace_chat.apply(db,request.get('_runtime_snapshot'))
            if selected.get('executor_binding_id'):
                import project_bindings
                current = project_bindings.resolve(db, db.chat(tid)['task_id'], selected['executor_binding_id'], member_id=selected.get('member_id',''))
                if current['assistant_id'] != selected['assistant_id']:
                    raise ValueError('执行者配置已改变，请重新发送消息')
                selected = {**selected, 'tools': project_bindings._intersect(selected.get('tools'), current['tools'])}
            with execution.scope(lambda: run.cancel_requested):
                if track:
                    db.log_step(tid, step, "running", "恢复执行" if db.resuming else "开始执行")
                runners = {"idea": workflow.run_idea, "outline": workflow.run_outline, "map": workflow.run_map,
                           "write": workflow.run_write, "review": workflow.run_review}
                if step == 'paper_toolbox':
                    import paper_toolbox
                    result = paper_toolbox.execute(db, request, emit)
                elif step in runners:
                    result = runners[step](db, tid, request.get("message", ""), emit)
                else:
                    wstep = step or str(request.get("step") or "")
                    extra = db.tr_hl_context(tid) if track else ""
                    if selected.get('member_prompt'):
                        extra += '\n【当前团队成员职责】\n' + selected['member_prompt']
                    if wstep == "files" and request.get("sel"):
                        extra += "\n【当前选中的文件】" + str(request["sel"])
                    on_project = bool(db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (tid,)).fetchone())
                    result = workflow.run_agent(db, tid, request.get("message", ""), emit, extra_context=extra,
                        system=(workflow.files_system(db, tid) if wstep == "files" else
                                workflow.convert_system(db, tid) if wstep == "convert" else
                                workflow_runners.entrans_system(db, tid) if wstep == "entrans" else None),
                        step=wstep, scope=wstep if on_project else "", cancel_check=lambda: run.cancel_requested,
                        tool_allowlist=selected.get('tools'), image_ids=request.get('image_ids'))
                execution.check()
                if error:
                    raise RuntimeError(error)
                if track:
                    db.log_step(tid, step, "succeeded", "执行完成，等待确认交付")
                run.emit({"type": "done", "content": final or (result or {}).get("reply", "")}, db)
        except Exception as exc:
            cancelled = isinstance(exc, execution.Cancelled) or run.cancel_requested
            if track and db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (tid,)).fetchone():
                db.log_step(tid, step, "cancelled" if cancelled else "failed", str(exc)[:300])
            run.emit({"type": "cancelled" if cancelled else "error", "message": str(exc)}, db)


def recover(db_path) -> dict:
    """Resume saved model boundaries; never replay an uncertain in-flight tool."""
    import workflow
    resumed, interrupted = [], []
    with workflow.WorkflowDB(db_path) as db:
        ids = [r["id"] for r in db.db.execute(
            "SELECT id FROM tasks WHERE kind='agent_run' AND status IN ('pending','running','suspended') AND cancel_requested=0")]
        for rid in ids:
            live = RunManager.get_instance().get_run(rid)
            if live and live.worker_started:
                continue
            row = db.get_async_task(rid)
            cp = row["checkpoint"]
            if cp.get("request") and cp.get("phase") != "tool":
                resumed.append(rid)
            else:
                db.update_async_task(rid, status="interrupted")
                db.update_background_job(rid, status="interrupted")
                interrupted.append(rid)
    started = []
    for rid in resumed:
        try:
            start(db_path, {}, resume_id=rid)
            started.append(rid)
        except Exception as exc:
            with workflow.WorkflowDB(db_path) as db:
                checkpoint = (db.get_async_task(rid) or {}).get("checkpoint", {})
                db.update_async_task(rid, status="interrupted", checkpoint={**checkpoint, "recovery_error": str(exc)})
                db.update_background_job(rid, status="interrupted")
            interrupted.append(rid)
    return {"resumed": started, "interrupted": interrupted}
