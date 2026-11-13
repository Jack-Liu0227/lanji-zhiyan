"""One execution worker for project, team and workflow tasks."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import threading

import execution
import asset_service
import project_tasks as tasks

_lock = threading.RLock()
_workers = {}
_executing = set()
_MISSING = object()


def _accepted_upstream(db, project_id, dependencies):
    root = Path(db.base_dir(project_id)).resolve()
    accepted = []
    for dependency in dependencies or []:
        files = sorted(dependency.get('outputs') or [], key=lambda item: (
            item.get('revision_source') != 'human',
            (item.get('name') or Path(item.get('path', '')).name) not in ('result.md', 'reviewed-result.json'),
            item.get('name') or ''))
        outputs = []
        for item in files:
            path = Path(item.get('path', '')).resolve()
            if path.suffix.lower() not in {'.md', '.markdown', '.txt', '.json', '.csv', '.tex'}:
                continue
            if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
                continue
            if tasks._hash(path) != item.get('content_hash', item.get('sha256')):
                raise ValueError('Accepted upstream output changed; review the upstream node again')
            outputs.append({'name': item.get('name') or path.name,
                            'revision_source': item.get('revision_source', ''),
                            'content': path.read_text(encoding='utf-8')})
        if outputs:
            accepted.append({'task_id': dependency.get('task_id', ''),
                             'delivery_id': dependency.get('delivery_id', ''), 'outputs': outputs})
    return accepted


def is_active(db_path, run_id):
    key = (str(Path(db_path).resolve()), run_id)
    with _lock:
        return key in _workers or key in _executing


def request_cancel(run_id):
    with _lock:
        for (_, key), event in _workers.items():
            if key == run_id:
                event.set()


def _allowlists(*values):
    lists = [set(value) for value in values if value is not None]
    return sorted(set.intersection(*lists)) if lists else None


def _tools(db, task, run, node):
    import workflow
    import capabilities
    import agent_integrations
    stage = task["metadata"].get("stage", "")
    executor = run["executor_snapshot"]
    if stage and stage in workflow.STAGES:
        import workflow_execution
        specs = workflow_execution._tools(db, executor["assistant_id"], {**node, "step": stage}, task["project_id"])
    else:
        specs = [*workflow.TOOL_SPECS, *agent_integrations.tool_specs(),
                 *capabilities.capability_tool_specs(db, executor["assistant_id"], str(db.base_dir(task["project_id"]) or ""))]
    forbidden = {"delegate", "team_manage", "workflow_create", "schedule_manage", "assistant_manage"}
    # Context pagination is an internal, read-only capability for end-to-end
    # workflow nodes. Generic project/team tasks keep their explicitly selected
    # tool surface unchanged.
    context_reader = "workflow_context_read"
    workflow_node = bool(task.get("workflow_run_id") or task.get("metadata", {}).get("workflow_node"))
    allowlist = _allowlists(executor.get("tools"), task["scope"].get("tools"), node.get("tools"))
    effective_allowlist = None if allowlist is None else set(allowlist) | ({context_reader} if workflow_node else set())
    specs = [s for s in specs if s["function"]["name"] not in forbidden and
             (effective_allowlist is None or s["function"]["name"] in effective_allowlist)]
    if workflow_node and not any(s["function"]["name"] == context_reader for s in specs):
        fallback = next((s for s in workflow.TOOL_SPECS if s["function"]["name"] == context_reader), None)
        if fallback:
            specs.append(fallback)
    import team_orchestration
    return team_orchestration.tools(task, list({s["function"]["name"]: s for s in specs}.values())), allowlist


def _freeze_file(db, task, run, source, folder, index):
    import asset_service
    root = tasks._root(db, task["project_id"])
    source = Path(source).resolve()
    if not source.is_relative_to(root) or not source.is_file() or not source.stat().st_size:
        raise ValueError("任务产物不存在、为空或超出项目范围")
    destination = folder / (str(index) + "-" + source.name)
    if not destination.resolve().is_relative_to(root):
        raise ValueError("任务产物目录超出项目范围")
    digest = tasks._hash(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    if tasks._hash(destination) != digest:
        raise ValueError("固定产物时文件发生变化")
    asset = asset_service.register_file(db, destination, project_id=task["project_id"], kind="task_output", source="project_task", run_id=run["id"],
                                       metadata={"project_task_id": task["id"], "task_run_id": run["id"]})
    return {"asset_id": asset["id"], "path": str(destination), "content_hash": digest, "sha256": digest,
            "original_path": str(source), "relative": source.relative_to(root).as_posix(), "name": source.name}


def validate_fixed_tool(db, project_id, node, executor):
    import workflow
    import task_tools
    import agent_settings
    import capabilities
    # Orchestration and human acceptance cannot be invoked as a leaf tool.
    name = node.get('tool_name', '')
    specs, _ = _tools(db, {'project_id': project_id, 'scope': {}, 'metadata': {'stage': node['step']}},
                      {'executor_snapshot': executor}, node)
    specs = capabilities.filter_tools(db, executor['assistant_id'], specs, str(db.base_dir(project_id)))
    if name in task_tools.NAMES or name.startswith('graph_workflow_') or name not in {s['function']['name'] for s in specs}:
        raise ValueError('固定节点工具未获授权或属于工作流控制工具：' + name)
    cfg = executor.get('runtime_config') or agent_settings.runtime_config(db, project_id, executor['assistant_id'], executor.get('member'))
    if name.startswith('mcp_') and name != 'mcp_register' and cfg['permission'] != 'full_access':
        raise ValueError('项目内 MCP 工具需要执行者的完全访问权限')
    workflow.enforce_tool_policy(name, node['tool_args'], cfg['permission'])
    workflow.enforce_project_scope(db, project_id, name, node['tool_args'])


def _fixed_tool(db, task, run, node, emit):
    import workflow
    import agent_integrations
    import capabilities
    validate_fixed_tool(db, task['project_id'], node, run['executor_snapshot'])
    name, args = node['tool_name'], node['tool_args']
    if task['scope'].get('tools') is not None and name not in task['scope']['tools']:
        raise ValueError('工具超出当前任务范围：' + name)
    if 'fixed_tool_result' in run['checkpoint']:
        result = run['checkpoint']['fixed_tool_result']
    else:
        tasks.save_checkpoint(db, run['id'], phase='tool', inflight={'name': name, 'args': args})
        emit({'type': 'tool_call', 'name': name, 'args': args})
        permission = run['executor_snapshot']['runtime_config']['permission']
        aid = run['executor_snapshot']['assistant_id']
        if name in {s['function']['name'] for s in agent_integrations.tool_specs()}:
            result = agent_integrations.invoke(name, db, task['project_id'], args, assistant_id=aid, permission=permission)
        elif name.startswith('mcp_') or name in ('skill_read', 'skill_write'):
            result = capabilities.invoke_tool(db, aid, name, args, str(tasks._root(db, task['project_id'])))
        else:
            result = workflow._tool_impl(name, db, task['project_id'], args, scope=node['step'], emit=emit, profile=db.runtime_model)
        if isinstance(result, dict) and (result.get('error') or result.get('ok') is False):
            raise ValueError(str(result.get('error') or '固定工具执行失败'))
        tasks.save_checkpoint(db, run['id'], phase='model', fixed_tool_result=result, inflight=None)
        emit({'type': 'tool_result', 'name': name, 'ok': True})
    return {'reply': json.dumps(result, ensure_ascii=False, default=str),
            'output_assets': result.get('output_assets', []) if isinstance(result, dict) else []}


def _outputs(db, task, run, out, capture, *, folder=None):
    import asset_service
    root = tasks._root(db, task["project_id"], writable=True)
    folder = folder or root / ".tasks" / task["id"] / run["id"] / "outputs"
    if not folder.resolve().is_relative_to(root):
        raise ValueError("任务产物目录超出项目范围")
    paths = list((capture or {}).get("files") or [])
    for aid in out.get("output_assets") or []:
        asset = asset_service.get(db, aid)
        if asset and asset["project_id"] == task["project_id"] and asset.get("storage") == "file":
            paths.append(asset["canonical_uri"])
    for relative in task["output_contract"].get("required_files", []):
        if isinstance(relative, dict):
            relative = relative.get("relative") or relative.get("path") or ""
        candidate = (root / str(relative)).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise ValueError("缺少约定产物：" + str(relative))
        paths.append(str(candidate))
    files = [_freeze_file(db, task, run, path, folder, index) for index, path in enumerate(dict.fromkeys(paths))]
    reply = str(out.get("reply") or "").strip()
    content = (capture or {}).get("content", reply)
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
    if not text.strip() and not files:
        raise ValueError("任务未生成实际内容")
    report = folder / "result.md"
    asset_service.atomic_write_text(report, text or reply or "本次任务产物见关联文件。")
    asset = asset_service.register_file(db, report, project_id=task["project_id"], kind="task_output", title=task["title"],
        source="project_task", run_id=run["id"], body=text, metadata={"project_task_id": task["id"], "task_run_id": run["id"]})
    files.append({"asset_id": asset["id"], "path": str(report), "content_hash": tasks._hash(report), "sha256": tasks._hash(report), "name": "result.md", "relative": report.relative_to(root).as_posix()})
    output = {"files": files, "content": content, "reply": reply,
            "original_versions": [{"path": item["original_path"], "sha256": item["content_hash"]} for item in files if item.get("original_path")]}
    if task['metadata'].get('stage') == 'convert':
        import workflow_export_revision
        output['export_source'] = workflow_export_revision.source(db, task, {**run, 'output_snapshot': output})
    return output


def _error_detail(exc):
    message=str(exc).strip()
    if not message or message.casefold() in {'error','failed','execution failed'}:
        return f"{type(exc).__name__}: {message or '未提供错误详情'}"
    return message


def execute(db, task_run_id, cancel_check=None):
    import workflow
    import project_bindings
    import capabilities
    import agent_settings
    claimed = tasks.claim(db, task_run_id)
    if not claimed:
        return tasks.get(db, tasks.get_run(db, task_run_id)["task_id"])
    task, run = claimed["task"], claimed["run"]
    execution_key = (str(Path(db.path).resolve()), task_run_id)
    with _lock:
        _executing.add(execution_key)
    executor = run["executor_snapshot"]
    metadata = run["input_snapshot"].get("execution_context", task["metadata"])
    node = dict(metadata.get("node_config") or {})
    stage = str(metadata.get("stage") or "")
    known_stage = stage in workflow.STAGES
    names = ("runtime_model", "project_task_context", "job_checkpoint", "save_agent_checkpoint", "resuming",
             "template_snapshot", "template_resolved", "template_request", "workflow_end_to_end",
             "workflow_context_run_id", "workflow_context_node_id", "template_schedule_id")
    previous = {name: getattr(db, name, _MISSING) for name in names}
    owner_thread = threading.get_ident()
    cancelled = threading.Event()

    def check():
        if cancelled.is_set():
            return True
        if threading.get_ident() == owner_thread:
            stopped = bool(cancel_check and cancel_check()) or tasks._task(db, task["id"])["status"] == "cancelled"
        elif str(db.path) != ":memory:":
            # Model I/O callbacks run in another thread; SQLite connections stay with their owner.
            try:
                with closing(sqlite3.connect(db.path, timeout=1)) as connection:
                    row = connection.execute("SELECT status FROM project_tasks WHERE id=?", (task["id"],)).fetchone()
                    stopped = not row or row[0] == "cancelled"
            except sqlite3.Error:
                stopped = False
        else:
            stopped = False
        if stopped:
            cancelled.set()
        return stopped

    try:
        with execution.scope(check):
            tasks._validate_versions(db, task, run, outputs=False)
            validator = tasks._adapter("validate")
            if validator:
                validator(db, {**task, "metadata": metadata}, run)
            project_bindings.resolve(db, task["project_id"], task["executor_binding_id"], member_id=executor.get("member_id", ""))
            member = executor.get("member") or None
            cfg = executor.get("runtime_config") or agent_settings.runtime_config(db, task["project_id"], executor["assistant_id"], member)
            model = db.model(cfg["model_id"]) if cfg["model_id"] else db.active_model()
            db.runtime_model = {**model, "reasoning": cfg["reasoning"], "permission": cfg["permission"]} if model else None
            db.project_task_context = tasks.CommandContext.agent(task["project_id"], task["id"])
            db.job_checkpoint = dict(run["checkpoint"])
            db.resuming = bool(db.job_checkpoint.get("messages") or db.job_checkpoint.get("completed_calls"))
            db.save_agent_checkpoint = lambda **values: tasks.save_checkpoint(db, task_run_id, **values)
            db.template_snapshot = metadata.get("template_snapshot") or run["checkpoint"].get("template_snapshot")
            db.template_resolved = "template_snapshot" in metadata or bool(db.template_snapshot)
            db.template_request = metadata.get("template_ref")
            db.template_schedule_id = metadata.get("schedule_id", "")
            owner = metadata.get("workflow_node") or {}
            db.workflow_end_to_end = bool(owner)
            db.workflow_context_run_id = owner.get("run_id", "")
            legacy_origin = next((o for o in task["origins"] if o["kind"] == "workflow_node" and o["task_run_id"] == run["id"]), None)
            db.workflow_context_node_id = (legacy_origin or {}).get("legacy_id") or owner.get("record_id", "")
            skills = []
            for skill_id in node.get("skills") or []:
                skill = capabilities.invoke_tool(db, executor["assistant_id"], "skill_read", {"skill_id": skill_id}, str(db.base_dir(task["project_id"])))
                skills.append({"id": skill_id, "content": skill["content"], "hash": tasks._digest(skill["content"])})
            context = {**(metadata.get("upstream_context") or {}), "task_id": task["id"],
                       "task_goal": task["goal"], "task_inputs": {k: v for k, v in run["input_snapshot"].items() if k != "execution_context"},
                       "assistant_id": executor["assistant_id"], "member": member, "skills": skills,
                       "model_id": (model or {}).get("id", ""), "tool_permission": cfg["permission"]}
            context['accepted_upstream'] = _accepted_upstream(
                db, task['project_id'], run['input_snapshot'].get('dependencies', []))
            if metadata.get('_handoff_context'):
                context['handoff_context'] = metadata['_handoff_context']
            if metadata.get("legacy_stage_compat") and not context.get("upstream_hash"):
                import workflow_execution
                upstream = workflow_execution._snapshot(db, workflow_execution.detail(db, owner["run_id"]), node)
                context = {**upstream, **context}
            context.setdefault("goal", task["goal"])
            context.setdefault("history", [])
            context.setdefault("input_assets", [a["asset_id"] for a in run["input_snapshot"].get("assets", []) if a.get("asset_id")])
            context.setdefault("memory", db.memory_block(task["project_id"]))
            tasks.save_checkpoint(db, run["id"], input=context)
            specs, allowlist = _tools(db, task, run, node)
            if owner:
                import workflow_context
                context_text = workflow_context.prompt(context, owner["run_id"], db.workflow_context_node_id)
                # Large handoffs are compacted for the model, while the complete
                # checkpoint remains addressable through workflow_context_read.
                # Never fail the node merely because the handoff was compacted.
            else:
                context_text = json.dumps(context, ensure_ascii=False, default=str)
            if member and member.get("prompt"):
                context_text += "\n【团队成员职责】\n" + member["prompt"]
            for skill in skills:
                context_text += "\n【指定技能 " + skill["id"] + "】\n" + skill["content"]
            feedback = run["input_snapshot"].get("feedback") or ""
            if feedback:
                context_text += "\n【人工退回意见，须落实】\n" + feedback
            purpose = node.get("label") or task["title"]
            prompt = node.get("prompt") or task["goal"]
            system = "执行当前项目任务「" + purpose + "」。依据版本化输入完成目标，使用工具保存实际产物；需要派生工作时只能使用当前任务允许的执行者和范围。"
            import team_orchestration
            system += team_orchestration.prompt(task, run)
            max_steps = None
            def board_emit(event):
                import execution_conversations
                execution_conversations.record(db,task,run,event)
                import execution_board
                kind = event.get('type', '')
                if kind in ('tool_call', 'tool_result', 'model_request', 'model_response', 'error'):
                    execution_board.publish(db, task['project_id'], kind, task_id=task['id'], run_id=run['id'], activity=event.get('name') or kind)
                    db.db.commit()
                if getattr(db, 'project_task_emit', None): db.project_task_emit(event)
            out = _fixed_tool(db, task, run, node, board_emit) if node.get('node_kind') == 'tool' else workflow.run_agent(db, task["project_id"], prompt, system=system, tools=specs,
                emit=board_emit,
                profile=db.runtime_model,
                extra_context=context_text, assistant_id=executor["assistant_id"], member=member,
                step=stage if known_stage else "", scope=stage if known_stage else "task:" + task["id"],
                publish=bool(metadata.get("legacy_stage_compat")), max_steps=max_steps,
                cancel_check=check, tool_allowlist=allowlist,
                image_inputs=[item for item in run['input_snapshot'].get('assets',[]) if item.get('path')
                    and (asset_service.get(db,item.get('asset_id','')) or {}).get('source')=='image_upload'])
            execution.check()
            capture = None
            current = tasks.get(db, task['id'])
            waiting_for_team = bool(task['metadata'].get('_team_orchestration')) and (
                any(child['status'] != 'delivered' for child in current['children']) or team_orchestration.needs_aggregation(current, run))
            if known_stage and not waiting_for_team:
                import workflow_state
                capture = workflow_state.capture(db, task["project_id"], stage)
                if not capture["valid"]:
                    raise ValueError("节点未生成完整产物，请检查工具记录并重试；已保存文件仍可在产物页面预览")
            output = _outputs(db, {**task, 'output_contract': {}} if waiting_for_team else task, run, out or {}, capture)
            checkpoint = {"input": context, "reply": (out or {}).get("reply", ""),
                          "output": capture or output, "agent_run_id": (out or {}).get("run_id", "")}
            execution.check()
            return tasks.complete(db, run["id"], {"reply": checkpoint["reply"], "output_snapshot": output, "checkpoint": checkpoint})
    except Exception as exc:
        return tasks.fail(db, run["id"], _error_detail(exc), cancelled=isinstance(exc, execution.Cancelled) or check())
    finally:
        with _lock:
            _executing.discard(execution_key)
        for name, value in previous.items():
            if value is _MISSING:
                db.__dict__.pop(name, None)
            else:
                setattr(db, name, value)


def kick(db_path, task_run_id):
    key = (str(Path(db_path).resolve()), task_run_id)
    with _lock:
        if key in _workers:
            return
        signal = threading.Event()
        _workers[key] = signal

    def work():
        import workflow
        try:
            with workflow.WorkflowDB(db_path) as db:
                queued_run = tasks.get_run(db, task_run_id)
                queued_task = tasks.get(db, queued_run["task_id"])
                if queued_task["workflow_run_id"] and not queued_task["parent_task_id"] and db.db.execute(
                        "SELECT 1 FROM workflow_executions WHERE run_id=?", (queued_task["workflow_run_id"],)).fetchone():
                    import workflow_execution
                    import workflow_tasks
                    workflow_tasks._record_attempt(db, workflow_execution.detail(db, queued_task["workflow_run_id"]),
                        queued_task["workflow_node_id"], queued_task, queued_run,
                        queued_run["checkpoint"].get("input") or queued_run["input_snapshot"].get("execution_context", {}).get("upstream_context") or {})
                import team_orchestration
                with team_orchestration.slot(db_path, queued_task, signal.is_set):
                    task = execute(db, task_run_id, signal.is_set)
                if task["status"] in ('delivered', 'awaiting_review') and task["workflow_run_id"] and not task["parent_task_id"]:
                    import workflow_execution
                    workflow_execution.kick(db_path, task["workflow_run_id"])
        except execution.Cancelled:
            pass
        finally:
            with _lock:
                _workers.pop(key, None)

    threading.Thread(target=work, daemon=True, name="project-task-" + task_run_id).start()


def recover(db_path):
    """Mark abandoned attempts, preserving checkpoints; never restart models on read."""
    import workflow
    with workflow.WorkflowDB(db_path) as db:
        import task_output_versions
        task_output_versions.recover(db)
        for row in db.db.execute("SELECT id FROM project_task_runs WHERE status IN ('queued','running')").fetchall():
            with _lock:
                key = (str(Path(db_path).resolve()), row["id"])
                active = key in _workers or key in _executing
            if not active:
                tasks.interrupt(db, row["id"])
        for row in db.db.execute("SELECT id FROM project_tasks").fetchall():
            tasks._notify(db, row["id"], reconcile=True)
        import schedule_workflow
        schedule_workflow.reconcile(db)
