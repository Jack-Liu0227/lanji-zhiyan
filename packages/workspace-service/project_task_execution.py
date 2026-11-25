"""One execution worker for project, team and workflow tasks."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
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
HEARTBEAT_INTERVAL = 5.0
MAX_AGENT_STEPS = 64


class _RunMonitor:
    """Persist worker liveness independently of blocked model/tool I/O."""
    def __init__(self, db_path, run_id):
        self.db_path, self.run_id = db_path, run_id
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.last_progress_at = tasks._now()
        self.phase_started_at = self.last_progress_at
        self.phase = 'preparing'
        self.thread = None

    def progress(self, event):
        kind = event.get('type') or event.get('kind') or ''
        phase = {'model_request': 'model', 'model_response': 'model', 'reasoning_start': 'model',
                 'reasoning_delta': 'reasoning', 'reasoning_done': 'reasoning', 'assistant_delta': 'responding',
                 'tool_call': 'tool', 'tool_result': 'tool_result', 'error': 'error', 'done': 'finalizing'}.get(kind)
        if phase:
            with self.lock:
                if phase != self.phase:
                    self.phase_started_at = tasks._now()
                self.last_progress_at, self.phase = tasks._now(), phase

    def _write(self, connection, *, alive=True):
        with self.lock:
            progress_at, phase, phase_started_at = self.last_progress_at, self.phase, self.phase_started_at
        # Change only monitoring keys, preserving resumable agent state.
        with tasks._lock, connection:
            changed = connection.execute('''UPDATE project_task_runs SET checkpoint=json_set(checkpoint,
                '$.monitoring.heartbeat_at',?, '$.monitoring.last_progress_at',?,
                '$.monitoring.phase',?, '$.monitoring.phase_started_at',?, '$.monitoring.worker_alive',json(?))
                WHERE id=? AND status='running' AND EXISTS (
                  SELECT 1 FROM project_tasks t WHERE t.id=project_task_runs.task_id
                    AND t.latest_run_id=project_task_runs.id AND t.status='running')''',
                (tasks._now(), progress_at, phase, phase_started_at, 'true' if alive else 'false', self.run_id))
        return bool(changed.rowcount)

    def _tick(self, *, alive=True):
        try:
            with closing(sqlite3.connect(self.db_path, timeout=0.5)) as connection:
                return self._write(connection, alive=alive)
        except sqlite3.OperationalError as exc:
            if 'locked' not in str(exc).lower() and 'busy' not in str(exc).lower():
                raise
            return True

    def start(self):
        if str(self.db_path) == ':memory:':
            return
        if not self._tick():
            return
        def pulse():
            while not self.stopped.wait(HEARTBEAT_INTERVAL):
                if not self._tick():
                    break
        self.thread = threading.Thread(target=pulse, daemon=True, name='task-heartbeat-' + self.run_id)
        self.thread.start()

    def stop(self):
        if self.stopped.is_set():
            return
        self.stopped.set()
        if self.thread:
            self.thread.join(2)
            self._tick(alive=False)


def _accepted_upstream(db, project_id, dependencies):
    root = Path(db.base_dir(project_id)).resolve()
    accepted = []
    for dependency in dependencies or []:
        source = db.db.execute('SELECT workflow_run_id,workflow_node_id FROM project_tasks WHERE id=? AND project_id=?',
                               (dependency.get('task_id', ''), project_id)).fetchone()
        files = sorted(dependency.get('outputs') or [], key=lambda item: (
            item.get('revision_source') != 'human',
            (item.get('name') or Path(item.get('path', '')).name) not in ('result.md', 'reviewed-result.json'),
            item.get('name') or ''))
        outputs = []
        for item in files:
            path = Path(item.get('path', '')).resolve()
            from project_artifacts import TEXT_SUFFIXES
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
                continue
            if tasks._hash(path) != item.get('content_hash', item.get('sha256')):
                raise ValueError('Accepted upstream output changed; review the upstream node again')
            content = path.read_text(encoding='utf-8-sig')
            outputs.append({'name': item.get('name') or path.name,
                            'revision_source': item.get('revision_source', ''),
                            'content_hash': item.get('content_hash') or item.get('sha256'),
                            'total_chars': len(content),
                            'read_with': {'tool': 'workflow_context_read', 'run_id': source['workflow_run_id'],
                                          'node_id': source['workflow_node_id'], 'section': 'artifact',
                                          'artifact': item.get('name') or path.name, 'offset': 0, 'limit': 16000}
                                if source and source['workflow_run_id'] else None,
                            'content': content})
        if outputs:
            accepted.append({'task_id': dependency.get('task_id', ''),
                             'delivery_id': dependency.get('delivery_id', ''), 'outputs': outputs})
    return accepted


def _accepted_candidate_csv(context):
    for dependency in context.get('accepted_upstream', []):
        for output in dependency.get('outputs', []):
            name = output.get('name') or Path(output.get('relative') or '').name
            if Path(name).name.casefold() == 'candidate_papers.csv':
                return str(output.get('content') or '')
    return ''


def _requires_output(task, filename):
    for item in task['output_contract'].get('required_files', []):
        relative = item.get('relative') or item.get('path') if isinstance(item, dict) else str(item)
        if Path(relative).name.casefold() == filename.casefold():
            return True
    return False


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
    evidence_task = bool(task.get("output_contract", {}).get("requires_evidence"))
    specs = [s for s in specs if s["function"]["name"] not in forbidden and
             (effective_allowlist is None or s["function"]["name"] in effective_allowlist) and
             not (evidence_task and s["function"]["name"] in {"paper_excerpt", "paper_evidence_get"})]
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
    working = asset_service.path_asset(db, task['project_id'], source)
    asset = asset_service.register_file(db, destination, project_id=task["project_id"], kind="task_output", source="project_task", run_id=run["id"],
        metadata={"project_task_id": task["id"], "task_run_id": run["id"],
                  "workflow_run_id": task.get('workflow_run_id', ''), "workflow_node_id": task.get('workflow_node_id', ''),
                  "working_asset_id": working.get('id', ''), "working_revision_id": working.get('current_revision_id', ''),
                  "working_relative": source.relative_to(root).as_posix(), "working_content_hash": digest})
    return {"asset_id": asset["id"], "path": str(destination), "content_hash": digest, "sha256": digest,
            "original_path": str(source), "relative": source.relative_to(root).as_posix(), "name": source.name}


def validate_fixed_tool(db, project_id, node, executor, *, args=None, bound_arguments=()):
    import workflow
    import task_tools
    import agent_settings
    import capabilities
    # Orchestration and human acceptance cannot be invoked as a leaf tool.
    name = node.get('tool_name', '')
    specs, _ = _tools(db, {'project_id': project_id, 'scope': {}, 'metadata': {'stage': node['step']}},
                      {'executor_snapshot': executor}, node)
    specs = capabilities.filter_tools(db, executor['assistant_id'], specs, str(db.base_dir(project_id)))
    spec = next((s['function'] for s in specs if s['function']['name'] == name), None)
    if name in task_tools.NAMES or name.startswith('graph_workflow_') or spec is None:
        raise ValueError('固定节点工具未获授权或属于工作流控制工具：' + name)
    properties = spec.get('parameters', {}).get('properties', {})
    for argument in bound_arguments:
        if properties.get(argument, {}).get('type') != 'string':
            raise ValueError(f'固定工具 {name} 没有可绑定文件路径的文本参数：{argument}')
    cfg = executor.get('runtime_config') or agent_settings.runtime_config(db, project_id, executor['assistant_id'], executor.get('member'))
    if name.startswith('mcp_') and name != 'mcp_register' and cfg['permission'] != 'full_access':
        raise ValueError('项目内 MCP 工具需要执行者的完全访问权限')
    args = node['tool_args'] if args is None else args
    workflow.enforce_tool_policy(name, args, cfg['permission'])
    workflow.enforce_project_scope(db, project_id, name, args)


def _fixed_tool(db, task, run, node, emit):
    import workflow
    import agent_integrations
    import capabilities
    name, args = node['tool_name'], dict(node['tool_args'])
    for handoff in run['input_snapshot'].get('handoffs', []):
        if handoff.get('argument'):
            args[handoff['argument']] = handoff['path']
    validate_fixed_tool(db, task['project_id'], node, run['executor_snapshot'], args=args)
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


def _outputs(db, task, run, out, capture, *, folder=None, candidate_sources_content=''):
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
    missing: list[str] = []
    stale: list[str] = []
    carried: list[str] = []
    for relative in task["output_contract"].get("required_files", []):
        if isinstance(relative, dict):
            relative = relative.get("relative") or relative.get("path") or ""
        candidate = (root / str(relative)).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            missing.append(str(relative))
            continue
        asset = asset_service.path_asset(db, task["project_id"], candidate)
        if not asset.get("id"):
            # A file the executor wrote with its own tools is not registry-tracked yet.
            paths.append(str(candidate))
            continue
        metadata = asset.get("metadata") or {}
        metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
        if asset.get("source") != "workflow_artifact":
            paths.append(str(candidate))
            continue
        if metadata.get("project_task_id") != task["id"]:
            stale.append(str(relative))
            continue
        if metadata.get("task_run_id") != run["id"]:
            # A file this same node registered in an earlier attempt carries over
            # only while the bytes on disk are still the registered version.
            registered = str(asset.get("content_hash") or "")
            if not registered or asset_service.sha256_file(candidate) != registered:
                stale.append(str(relative))
                continue
            carried.append(str(relative))
        paths.append(str(candidate))
    if missing:
        raise ValueError("缺少约定产物：" + "、".join(missing))
    if stale:
        raise ValueError("约定产物不是当前尝试保存的版本：" + "、".join(stale))
    files = [_freeze_file(db, task, run, path, folder, index) for index, path in enumerate(dict.fromkeys(paths))]
    reply = str(out.get("reply") or "").strip()
    content = (capture or {}).get("content", reply)
    if not capture and task["output_contract"].get("required_files"):
        declared = {str(item.get("relative") or item.get("path")) if isinstance(item, dict) else str(item)
                    for item in task["output_contract"]["required_files"]}
        from project_artifacts import TEXT_SUFFIXES
        texts = [Path(item["path"]).read_text(encoding="utf-8-sig")
                 for item in files if item.get("relative") in declared and Path(item["path"]).suffix.lower() in TEXT_SUFFIXES]
        if texts:
            content = "\n\n".join(texts)
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
    if task["output_contract"].get("requires_evidence"):
        output["evidence_ids"] = [row["id"] for row in db.evidence(task["project_id"])
                                  if row["id"] in text]
    tasks._validate_contract(task["output_contract"], output)
    if candidate_sources_content:
        for item in task["output_contract"].get("required_files", []):
            relative = item.get("relative") or item.get("path") if isinstance(item, dict) else str(item)
            if Path(relative).name.casefold() == 'methods_metrics.csv':
                import artifact_quality
                content = (root / relative).read_text(encoding='utf-8-sig')
                artifact_quality.validate_methods_metrics_candidates(relative, content, candidate_sources_content)
    if task['metadata'].get('stage') == 'convert':
        import workflow_export_revision
        output['export_source'] = workflow_export_revision.source(db, task, {**run, 'output_snapshot': output})
    return output


def _error_detail(exc):
    message=str(exc).strip()
    if not message or message.casefold() in {'error','failed','execution failed'}:
        return f"{type(exc).__name__}: {message or '未提供错误详情'}"
    return message


def _effective_output_contract(task):
    contract = dict(task.get("output_contract") or {})
    node = (task.get("metadata") or {}).get("node_config") or {}
    if not contract.get("required_files") and node:
        import workflow_layout
        required = workflow_layout.required_outputs({**node, "output_contract": contract})
        if required:
            contract["required_files"] = required
    return contract


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
    names = ("runtime_model", "runtime_model_candidates", "project_task_context", "job_checkpoint", "save_agent_checkpoint", "resuming",
             "template_snapshot", "template_resolved", "template_request", "workflow_end_to_end",
             "workflow_context_run_id", "workflow_context_node_id", "template_schedule_id",
              "workflow_node_search_capture", "usage_context")
    previous = {name: getattr(db, name, _MISSING) for name in names}
    owner_thread = threading.get_ident()
    cancelled = threading.Event()
    monitor = _RunMonitor(db.path, task_run_id)

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
        task["output_contract"] = _effective_output_contract(task)
        file_outputs = bool(task["output_contract"].get("required_files"))
        monitor.start()
        with execution.scope(check):
            tasks._validate_versions(db, task, run, outputs=False)
            validator = tasks._adapter("validate")
            if validator:
                validator(db, {**task, "metadata": metadata}, run)
            project_bindings.resolve(db, task["project_id"], task["executor_binding_id"], member_id=executor.get("member_id", ""))
            import team_orchestration
            if team_orchestration.prepare_office_members(db, task, executor):
                monitor.stop()
                return tasks.complete(db, run["id"], {"reply": "已派发独立专家任务，等待成员交付后汇总。"})
            member = executor.get("member") or None
            cfg = executor.get("runtime_config") or agent_settings.runtime_config(
                db, task["project_id"], executor["assistant_id"], member, node=node, task=task)
            db.usage_context = {"project_id": task["project_id"], "run_id": run["id"], "assistant_id": executor["assistant_id"],
                                "owner_id": task.get("chat_id", ""), "source": "project_task"}
            schedule_id = metadata.get("schedule_id")
            if not schedule_id and task.get("workflow_run_id"):
                scheduled = db.db.execute("SELECT s.schedule_id FROM schedule_workflow_runs w JOIN schedule_runs s ON s.id=w.schedule_run_id WHERE w.workflow_run_id=?", (task["workflow_run_id"],)).fetchone()
                schedule_id = scheduled[0] if scheduled else ""
            if schedule_id:
                from datetime import date
                db.usage_context.update(source="schedule", budget_key="schedule:" + schedule_id + ":" + date.today().isoformat())
            elif task.get("workflow_run_id"):
                db.usage_context.update(source="workflow", budget_key="workflow:" + task["workflow_run_id"])
            try:
                model = agent_settings.load_frozen_model(db, cfg, allow_credential_refresh=True)
            except ValueError as frozen_exc:
                if cfg.get("model_policy") == "strict":
                    raise
                fallback_chain = agent_settings.resolve_model_chain(db, cfg)
                if not fallback_chain:
                    raise frozen_exc
                model = fallback_chain[0]
            if model and (cfg.get("model_identity") or {}).get("revision") and model.get("runtime_revision") != cfg["model_identity"]["revision"]:
                tasks._event(db, task["id"], "credential_refreshed", {"model_id": model.get("id", ""), "model": model.get("model", "")}, run["id"])
            has_images = any(
                item.get("path") and (asset_service.get(db, item.get("asset_id", "")) or {}).get("source") == "image_upload"
                for item in run["input_snapshot"].get("assets", [])
            )
            req_caps = ("vision",) if has_images else ()
            candidates = agent_settings.resolve_model_chain(db, cfg, required_capabilities=req_caps)
            if model and all(c.get("id") != model.get("id") for c in candidates):
                candidates = [model, *candidates]
            db.runtime_model_candidates = candidates
            db.runtime_model = candidates[0] if candidates else model
            db.project_task_context = tasks.CommandContext.agent(task["project_id"], task["id"])
            db.job_checkpoint = dict(run["checkpoint"])
            db.resuming = bool(db.job_checkpoint.get("messages") or db.job_checkpoint.get("completed_calls"))
            def save_checkpoint(**values):
                with tasks._lock:
                    return tasks.save_checkpoint(db, task_run_id, **values)
            db.save_agent_checkpoint = save_checkpoint
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
            for skill_id in dict.fromkeys([*(metadata.get("required_skills") or []), *(node.get("skills") or [])]):
                try:
                    skill = capabilities.invoke_tool(db, executor["assistant_id"], "skill_read", {"skill_id": skill_id}, str(db.base_dir(task["project_id"]) or ""))
                    if not skill["content"].strip():
                        raise ValueError("Skill 文件为空")
                except (ValueError, OSError) as exc:
                    raise ValueError(f"无法加载 Skill {skill_id}：{exc}。请检查设置 → Skills 及该执行专家的权限。") from exc
                skills.append({"id": skill_id, "content": skill["content"], "hash": tasks._digest(skill["content"])})
            context = {**(metadata.get("upstream_context") or {}), "task_id": task["id"],
                       "task_goal": task["goal"], "task_inputs": {k: v for k, v in run["input_snapshot"].items() if k != "execution_context"},
                       "assistant_id": executor["assistant_id"], "member": member, "skills": skills,
                       "model_id": (model or {}).get("id", ""), "tool_permission": cfg["permission"]}
            context['accepted_upstream'] = _accepted_upstream(
                db, task['project_id'], run['input_snapshot'].get('dependencies', []))
            candidate_sources_content = ''
            candidate_table = ''
            if task.get('workflow_run_id') and _requires_output(task, 'methods_metrics.csv'):
                candidate_sources_content = _accepted_candidate_csv(context)
                if not candidate_sources_content:
                    raise ValueError('methods_metrics.csv requires the accepted upstream candidate_papers.csv')
                import artifact_quality
                candidate_table = json.dumps([
                    {'title': item['title'], 'doi_or_stable_url': item['doi_or_stable_url']}
                    for item in artifact_quality.candidate_sources(candidate_sources_content)
                ], ensure_ascii=False)
            if run['input_snapshot'].get('handoffs'):
                context['handoffs'] = run['input_snapshot']['handoffs']
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
                context_text = json.dumps({k: v for k, v in context.items() if k != 'skills'}, ensure_ascii=False, default=str)
            db.workflow_node_search_capture = bool(owner and stage == 'search')
            if member and member.get("prompt"):
                context_text += "\n【团队成员职责】\n" + member["prompt"]
            for skill in skills:
                context_text += "\n【指定技能 " + skill["id"] + "】\n" + skill["content"]
            feedback = run["input_snapshot"].get("feedback") or ""
            if feedback:
                context_text = "【人工退回意见，须落实】\n" + feedback + "\n\n" + context_text
            purpose = node.get("label") or task["title"]
            prompt = node.get("prompt") or task["goal"]
            system = "执行当前项目任务「" + purpose + "」。依据版本化输入完成目标，使用工具保存实际产物；需要派生工作时只能使用当前任务允许的执行者和范围。"
            if metadata.get("schedule_id") and not stage:
                system = ("执行定时任务：" + purpose + "。根据项目目标和可核验的来源写出完整的 Markdown 回复。"
                          "任务服务会把最终回复写到设定的输出目录并归档到项目笔记。")
            from workflow_catalog import STAGE_GUIDES
            guide = STAGE_GUIDES.get(stage)
            if guide and not file_outputs:
                system += f"\n本阶段输入：{guide['input']}\n必须交付：{guide['output']}\n操作：{guide['action']}"
            elif guide and file_outputs:
                system += f"\n本阶段专业指引（{stage}）：输入要求={guide['input']}；操作方法={guide['action']}"
            if file_outputs:
                system += ("\n本节点按指定文件交付。必须使用 project_artifact_write 按 required_files 中的相对路径一次性保存完整最终产物，"
                           "不要改存为随机笔记。严禁先写入“校验中/占位/待补充”之类过渡文本占用目标文件，避免触发哈希版本锁；"
                           "若确需更新已写文件，先调 workflow_context_read(section=\"artifacts\") 读取当前 content_hash 后再传 expected_hash。"
                           "目标 CSV 只写标准 RFC4180 CSV 文本且确保每行列数严格一致。"
                           "ID 与阅读规范：paper_search / paper_abstracts 返回的是检索题录 result_id，不可直接传给 paper_focus；"
                           "需要全文核查时，先调用 paper_download_batch（或 paper_download）获取已入库 PDF 的 paper_id，再调用 paper_focus / paper_excerpt / paper_evidence_record。"
                           "上游文件内容若在上下文中被截断，必须按 accepted_upstream 中的 read_with（section=\"artifact\", artifact=文件路径）分页读完再引用或汇总；"
                           "未能取得全文的条目如实记录缺口，严禁编造页码、数据或结论。\n"
                           + json.dumps(task["output_contract"], ensure_ascii=False))
                if candidate_sources_content:
                    system += ("\nmethods_metrics.csv 必须逐篇对应已验收的 A 节点候选池。citation 列原样使用以下完整题名，"
                               "doi_or_stable_url 列原样使用同一行链接；必须覆盖每个候选，不能填写 result_id、待核占位或其他来源。"
                               "仅摘要候选只能标为摘要级并明确指标/条件未核，不得补造数值。候选映射：\n"
                               + candidate_table)
                if task["output_contract"].get("requires_evidence"):
                    system += ("\n必须用 paper_evidence_record 登记经页码和原文校验的全文证据，"
                               "并在产物中引用返回的证据 ID。只有题录或摘要不能满足本节点的全文证据交付要求；"
                               "paper_focus 返回 excerpt_id 后，优先将 excerpt_id、field 和 value 传给 paper_evidence_record；"
                               "服务端会从本次尝试已保存的 paper_focus 结果恢复原文，避免手工复制 start/end。"
                               "下载失败可继续寻找其他开放版本或其他相关研究，保留排除与待核清单。")
                if task["output_contract"].get("requires_evidence"):
                    system += ("\n调用预算：对每篇已入库 PDF 最多调用一次 paper_focus。它返回的匹配已含原文、page_no、start 和 end；"
                               "用对应 excerpt_id、field 和 value 调用 paper_evidence_record，由服务端从原 PDF 取回原文。除非 paper_focus 无匹配，"
                               "不要再调用 paper_excerpt，也不要为同一主张重复检索。登记到足够的真实证据 ID 后，立即写完 required_files。")
                root = tasks._root(db, task["project_id"])
                target_versions = {}
                for declared in task["output_contract"]["required_files"]:
                    relative = declared.get("relative") or declared.get("path") if isinstance(declared, dict) else str(declared)
                    path = (root / relative).resolve()
                    if path.is_relative_to(root) and path.is_file():
                        target_versions[relative] = tasks._hash(path)
                if target_versions:
                    system += ("\n目标位置已有工作文件。以下哈希仅供写入时传 expected_hash 检查冲突，"
                               "这些文件不自动成为本轮当前尝试的交付产物；必须对 required_files 中的每一个文件"
                               "（即使内容无需修改）都在本轮调用 project_artifact_write 传入对应 expected_hash 重新保存：\n"
                               + json.dumps(target_versions, ensure_ascii=False))
                names = {spec["function"]["name"] for spec in specs}
                enabled = capabilities.enabled(db, executor["assistant_id"], "tool", "project_artifact_write")
                if "project_artifact_write" not in names or not enabled or cfg["permission"] == "read_only":
                    raise PermissionError("此节点需要 project_artifact_write 项目写入权限，请在执行专家或节点工具设置中启用")
            import team_orchestration
            system += team_orchestration.prompt(task, run)
            system += "\n当前本地执行时间：" + datetime.now().astimezone().isoformat(timespec="seconds") + "。检索日志使用实际执行日期。"
            max_steps = MAX_AGENT_STEPS
            def board_emit(event):
                monitor.progress(event)
                import execution_conversations
                execution_conversations.record(db,task,run,event)
                import execution_board
                kind = event.get('type', '')
                if kind in ('tool_call', 'tool_result', 'model_request', 'model_response', 'error'):
                    execution_board.publish(db, task['project_id'], kind, task_id=task['id'], run_id=run['id'], activity=event.get('name') or kind)
                    db.db.commit()
                if getattr(db, 'project_task_emit', None): db.project_task_emit(event)
            layout_nodes = (metadata.get("layout_snapshot") or {}).get("nodes", [])
            agent_kwargs = None
            if not file_outputs and stage == "write" and (not owner or any(item["step"] == "map" for item in layout_nodes)
                                     or workflow.map_read(db, task["project_id"])["exists"]):
                from workflow_runners import draft_from_lineage
                permission = (run["executor_snapshot"].get("runtime_config") or {}).get("permission", "auto")
                workflow.enforce_tool_policy("draft_write", {}, permission)
                if not any(tool["function"]["name"] in {"doc_write", "draft_write"} for tool in specs):
                    raise PermissionError("当前执行者未获得正文写入工具权限")
                out = draft_from_lineage(db, task["project_id"], require_complete=True)
                board_emit({"type": "tool_result", "name": "draft_write", "ok": True, "summary": out["reply"]})
            elif node.get("node_kind") == "tool":
                out = _fixed_tool(db, task, run, node, board_emit)
            else:
                agent_kwargs = {
                    "system": system, "tools": specs, "emit": board_emit, "profile": db.runtime_model,
                    "extra_context": context_text, "assistant_id": executor["assistant_id"], "member": member,
                    "step": stage if known_stage else "", "scope": stage if known_stage else "task:" + task["id"],
                    "publish": bool(metadata.get("legacy_stage_compat")), "max_steps": max_steps,
                    "cancel_check": check, "tool_allowlist": allowlist,
                    "refresh_system_prompt": bool(candidate_sources_content or task["output_contract"].get("requires_evidence")),
                    "image_inputs": [item for item in run['input_snapshot'].get('assets', []) if item.get('path')
                                     and (asset_service.get(db, item.get('asset_id', '')) or {}).get('source') == 'image_upload'],
                }
                out = workflow.run_agent(db, task["project_id"], prompt, **agent_kwargs)
            execution.check()
            capture = None
            current = tasks.get(db, task['id'])
            waiting_for_team = bool(task['metadata'].get('_team_orchestration')) and (
                any(child['status'] != 'delivered' for child in current['children']) or team_orchestration.needs_aggregation(current, run))
            if known_stage and not file_outputs and not waiting_for_team:
                import workflow_state
                if stage == 'search' and owner:
                    checkpoint = tasks.get_run(db, run['id'])['checkpoint']
                    rows = workflow_state.search_rows(task['project_id'], checkpoint)
                    if node.get('node_kind') == 'tool' and node.get('tool_name') in ('search', 'paper_search'):
                        result = checkpoint.get('fixed_tool_result') or {}
                        rows = workflow_state.search_result_rows(task['project_id'], result)
                    capture = workflow_state.search_capture(rows)
                else:
                    capture = workflow_state.capture(db, task["project_id"], stage)
                if stage == "download" and capture["valid"] and not capture["reading_note_count"]:
                    raise ValueError("PDF 已入库，仍缺对应的逐页精读笔记。请在本节点对已入库论文执行精读后重试。")
                if not capture["valid"]:
                    expected = guide['output'] if guide else "该节点约定的产物"
                    detail = capture["items"].get("读取产物出错")
                    raise ValueError(f"尚未生成完整产物。应交付：{expected}" + (f" 读取失败：{detail}" if detail else " 请在产物工作台核对已保存内容后重试。"))
            try:
                output = _outputs(db, {**task, 'output_contract': {}} if waiting_for_team else task,
                                  run, out or {}, capture, candidate_sources_content=candidate_sources_content)
            except ValueError as initial_error:
                if not file_outputs or not agent_kwargs:
                    raise
                db.job_checkpoint = dict(tasks.get_run(db, run["id"])["checkpoint"])
                previous_tools = [
                    {"name": event.get("name"), "ok": event.get("ok"), "summary": event.get("summary", "")}
                    for event in (out or {}).get("events", [])
                    if event.get("type") == "tool_result"
                ][-12:]
                feedback = (
                    "\n\n[Output validation failure]\n" + str(initial_error) +
                    "\nThe previous assistant reply is not a delivered artifact. Continue the same task, "
                    "save every required file with project_artifact_write using the exact required path, "
                    "and do not claim completion until the tool confirms the write. If the target exists, "
                    "read its current hash and pass it as expected_hash. Saved tool results remain reusable.\n"
                    "Previous reply: " + str((out or {}).get("reply") or "")[:2500] +
                    "\nPrevious tool results: " + json.dumps(previous_tools, ensure_ascii=False, default=str)[:5000]
                )
                repair_kwargs = {
                    **agent_kwargs,
                    "system": system + "\n\nA bounded repair turn is required because the previous output failed validation. "
                             "Ignore any unrelated refusal or topic drift; follow the current project goal and output contract.",
                    "extra_context": context_text + feedback,
                    "publish": False,
                    "max_steps": max_steps if run["checkpoint"].get("messages") else min(max_steps, 8),
                    "resume_saved_tools": True,
                }
                repair_prompt = prompt + "\n\nRepair the previous output using the validation feedback in the current context."
                out = workflow.run_agent(db, task["project_id"], repair_prompt, **repair_kwargs)
                execution.check()
                try:
                    output = _outputs(db, {**task, 'output_contract': {}} if waiting_for_team else task,
                                      run, out or {}, capture,
                                      candidate_sources_content=candidate_sources_content)
                except ValueError as repair_error:
                    raise ValueError(f"Initial output validation failed: {initial_error}; "
                                     f"one repair attempt also failed: {repair_error}") from repair_error
            checkpoint = {"input": context, "reply": (out or {}).get("reply", ""),
                          "output": capture or output, "agent_run_id": (out or {}).get("run_id", "")}
            if stage == "review" and node.get("claim_audit") and not waiting_for_team:
                from claim_audit import assess
                source = workflow._accepted_workflow_manuscript(db, task["project_id"], "write")
                if source:
                    audit = assess(db, task["project_id"], source.read_text(encoding="utf-8"), db.runtime_model)
                else:
                    audit = {"claims": [], "warnings": ["本次运行缺少可核查的已交付原稿。"], "requires_review": True}
                checkpoint["claim_audit"] = audit
            execution.check()
            monitor.stop()
            return tasks.complete(db, run["id"], {"reply": checkpoint["reply"], "output_snapshot": output, "checkpoint": checkpoint})
    except Exception as exc:
        monitor.stop()
        error_kind = getattr(exc, "error_kind", "")
        if error_kind:
            try:
                tasks.save_checkpoint(db, run["id"], last_error_kind=error_kind, last_error_code=getattr(exc, "error_code", ""))
            except Exception:
                pass
        return tasks.fail(db, run["id"], _error_detail(exc), cancelled=isinstance(exc, execution.Cancelled) or check())
    finally:
        monitor.stop()
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
