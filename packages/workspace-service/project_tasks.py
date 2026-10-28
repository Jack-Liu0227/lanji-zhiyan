"""Canonical project work, attempts, scope inheritance and delivery decisions."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import shutil
import threading
import uuid

MODES = frozenset(("supervised", "full_access"))
TERMINAL = frozenset(("delivered", "cancelled"))
_lock = threading.RLock()
_TASK_JSON = ("inputs", "output_contract", "scope", "metadata", "dependencies")
_RUN_JSON = ("policy_snapshot", "input_snapshot", "executor_snapshot", "checkpoint", "output_snapshot")


@dataclass(frozen=True)
class CommandContext:
    project_id: str
    source: str
    actor_id: str = ""
    parent_task_id: str = ""

    @classmethod
    def ui(cls, project_id, actor_id="local-user"):
        return cls(str(project_id), "ui", str(actor_id))

    @classmethod
    def agent(cls, project_id, parent_task_id=""):
        return cls(str(project_id), "agent", "", str(parent_task_id))

    @classmethod
    def system(cls, project_id, parent_task_id=""):
        return cls(str(project_id), "system", "system", str(parent_task_id))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _id(prefix):
    return prefix + uuid.uuid4().hex


@contextmanager
def _atomic(db):
    nested = db.db.in_transaction
    name = "project_task_" + uuid.uuid4().hex
    db.db.execute("SAVEPOINT " + name if nested else "BEGIN IMMEDIATE")
    try:
        yield
        db.db.execute("RELEASE SAVEPOINT " + name) if nested else db.db.commit()
    except BaseException:
        if nested:
            db.db.execute("ROLLBACK TO SAVEPOINT " + name)
            db.db.execute("RELEASE SAVEPOINT " + name)
        else:
            db.db.rollback()
        raise


def _project(db, project_id):
    if not db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (project_id,)).fetchone():
        raise ValueError("请选择有效项目")


def _authority(authority, project_id, *, human=False):
    if not isinstance(authority, CommandContext) or authority.project_id != project_id or authority.source not in ("ui", "agent", "system"):
        raise ValueError("操作上下文不属于当前项目")
    if human and authority.source != "ui":
        raise ValueError("人工验收或模式升级必须来自用户操作")


def _decode(row, fields):
    value = dict(row)
    for field in fields:
        value[field] = json.loads(value[field] or ("[]" if field in ("inputs", "dependencies") else "{}"))
    return value


def _task(db, task_id):
    row = db.db.execute("SELECT * FROM project_tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise ValueError("项目任务不存在")
    value = _decode(row, _TASK_JSON)
    value["review_required"] = bool(value["review_required"])
    return value


def get_run(db, run_id):
    row = db.db.execute("SELECT * FROM project_task_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise ValueError("项目任务执行记录不存在")
    return _decode(row, _RUN_JSON)


def get(db, task_id, *, project_id=""):
    task = _task(db, task_id)
    if project_id and task["project_id"] != project_id:
        raise ValueError("任务不属于当前项目")
    import execution_conversations
    task['chat_id']=(execution_conversations.link(db,'task',task_id) or {}).get('chat_id','')
    task["latest_run"] = get_run(db, task["latest_run_id"]) if task["latest_run_id"] else None
    task["runs"] = [_decode(r, _RUN_JSON) for r in db.db.execute("SELECT * FROM project_task_runs WHERE task_id=? ORDER BY attempt", (task_id,))]
    task["deliveries"] = [_decode(r, ("input_versions", "output_versions", "policy_snapshot")) for r in db.db.execute("SELECT * FROM project_task_deliveries WHERE task_id=? ORDER BY rowid", (task_id,))]
    task["outputs"] = (task["latest_run"] or {}).get("output_snapshot", {}).get("files", [])
    task['output_revisions']=[dict(row) for row in db.db.execute('SELECT * FROM project_task_output_revisions WHERE task_id=? ORDER BY created_at,revision',(task_id,))]
    task["children"] = [_decode(r, _TASK_JSON) for r in db.db.execute("SELECT * FROM project_tasks WHERE parent_task_id=? ORDER BY created_at,id", (task_id,))]
    task["origins"] = [dict(r) for r in db.db.execute("SELECT * FROM project_task_origins WHERE task_id=?", (task_id,))]
    task["events"] = [_decode(r, ("data",)) for r in db.db.execute("SELECT * FROM project_task_events WHERE task_id=? ORDER BY rowid", (task_id,))]
    return task


def list_tasks(db, project_id, *, workflow_run_id="", parent_task_id="", state=""):
    _project(db, project_id)
    sql, args = "SELECT id FROM project_tasks WHERE project_id=?", [project_id]
    if not workflow_run_id:
        sql += " AND COALESCE(json_extract(metadata,'$.deleted_at'),'')=''"
    for field, value in (("workflow_run_id", workflow_run_id), ("parent_task_id", parent_task_id), ("status", state)):
        if value:
            sql += " AND " + field + "=?"
            args.append(value)
    return [get(db, row["id"]) for row in db.db.execute(sql + " ORDER BY updated_at DESC,id", args).fetchall()]


def _event(db, task_id, kind, data=None, run_id=""):
    import execution_conversations
    execution_conversations.touch(db,'task',task_id)
    db.db.execute("INSERT INTO project_task_events VALUES (?,?,?,?,?,?)", (_id("pte-"), task_id, run_id, kind, _json(data or {}), _now()))
    import execution_board
    execution_board.task_event(db, task_id, kind, data, run_id)


def _adapter(name):
    import task_adapters
    return getattr(task_adapters, name, None)


def _notify(db, task_id, *, reconcile=False):
    task = get(db, task_id)
    function = _adapter("project")
    callbacks = [("legacy", lambda: function(task, db))] if function else []
    if task["workflow_run_id"] or any(o["kind"] == "schedule_run" for o in task["origins"]):
        import schedule_workflow
        callbacks.append(("schedule", lambda: (schedule_workflow.refresh_task if reconcile else schedule_workflow.task_changed)(db, task)))
    for name, callback in callbacks:
        try:
            callback()
        except Exception as exc:
            db.db.rollback()
            with _atomic(db):
                _event(db, task_id, "projection_error", {"adapter": name, "error": str(exc)[:2000]}, task["latest_run_id"])
    import execution_board
    with db.db:
        execution_board.refresh_plans(db, task_id)


def _wake_parent(db, task):
    if not task["parent_task_id"] or task["status"] != "delivered":
        return
    parent = get(db, task["parent_task_id"])
    run = parent["latest_run"]
    if parent["status"] != "blocked" or not run or not run["checkpoint"].get("waiting_children"):
        return
    if any(child["status"] != "delivered" for child in parent["children"]):
        return
    try:
        attempt = start(db, parent["id"], authority=CommandContext.system(parent["project_id"]), request_key="children:" + run["id"])
        import project_task_execution
        project_task_execution.kick(db.path, attempt["id"])
    except Exception as exc:
        with _atomic(db):
            _event(db, parent["id"], "aggregation_blocked", {"error": str(exc)[:2000]}, run["id"])


def link_origin(db, task_id, kind, legacy_id, task_run_id=""):
    task = _task(db, task_id)
    existing = db.db.execute("SELECT task_id FROM project_task_origins WHERE kind=? AND legacy_id=?", (kind, legacy_id)).fetchone()
    if existing and existing["task_id"] != task_id:
        raise ValueError("旧记录已关联其他项目任务")
    with _lock, _atomic(db):
        db.db.execute("INSERT INTO project_task_origins VALUES (?,?,?,?) ON CONFLICT(kind,legacy_id) DO UPDATE SET task_run_id=excluded.task_run_id", (kind, legacy_id, task_id, task_run_id))
    if kind=='todo':
        with db.db:
            db.db.execute('INSERT OR IGNORE INTO plan_task_links VALUES (?,?,?)',(legacy_id,task_id,_now()))
    _notify(db, task["id"])
    return get(db, task_id)


def find_origin(db, kind, legacy_id):
    row = db.db.execute("SELECT task_id FROM project_task_origins WHERE kind=? AND legacy_id=?", (kind, legacy_id)).fetchone()
    return get(db, row["task_id"]) if row else None


def restore_attempt(db, task_id, legacy_node, *, output_snapshot=None, policy=None, origin_kind="workflow_node"):
    """Import saved history without models or invented human approvals.

    Returns the task. Unverifiable old outputs remain blocked with the old checkpoint.
    Callers compile task definitions first, then import nodes in dependency order.
    """
    import project_bindings
    task = _task(db, task_id)
    legacy_id = str(legacy_node["id"])
    old = find_origin(db, origin_kind, legacy_id)
    if old:
        if old["id"] != task_id:
            raise ValueError("历史执行已归属其他任务")
        return old
    if task["latest_run_id"]:
        raise ValueError("已有规范执行的任务不能替换为历史尝试")
    checkpoint = legacy_node.get("checkpoint") or {}
    if isinstance(checkpoint, str):
        checkpoint = json.loads(checkpoint)
    checkpoint = {**checkpoint, "legacy_imported": True, "legacy_node_id": legacy_id}
    if isinstance(checkpoint.get("agent"), dict):
        checkpoint = {**checkpoint, **checkpoint["agent"]}
    selected = project_bindings.resolve(db, task["project_id"], task["executor_binding_id"], member_id=task["member_id"])
    run_id = _id("ptr-")
    policy = {"autonomy_mode": (policy or {}).get("autonomy_mode", "supervised"), "source": "legacy", "actor_id": "", "review_required": task["review_required"]}
    if policy["autonomy_mode"] not in MODES:
        policy["autonomy_mode"] = "supervised"
    status = legacy_node.get("status", "interrupted")
    run_status = "interrupted" if status in ("running", "interrupted") else "queued" if status == "queued" else "cancelled" if status in ("cancelled", "canceled") else "failed" if status == "failed" else "succeeded"
    task_status = "queued" if run_status == "queued" else "cancelled" if run_status == "cancelled" else "failed" if run_status == "failed" else "blocked"
    error, output = str(legacy_node.get("error") or ""), {}
    try:
        inputs = _snapshot_inputs(db, task, run_id)
        if checkpoint.get("input"):
            inputs["execution_context"] = {**inputs["execution_context"], "upstream_context": checkpoint["input"]}
        if checkpoint.get("feedback"):
            inputs["feedback"] = checkpoint["feedback"]
    except ValueError as exc:
        inputs = {"goal": task["goal"], "assets": [], "dependencies": [], "execution_context": task["metadata"]}
        error = str(exc)
        task_status = "blocked"
    run = {"id": run_id, "task_id": task_id, "executor_snapshot": selected, "input_snapshot": inputs,
           "policy_snapshot": policy, "checkpoint": checkpoint, "output_snapshot": output}
    if run_status == "succeeded" and not error:
        try:
            capture = checkpoint.get("output") or {}
            supplied = output_snapshot or {}
            versions = supplied.get("files") or capture.get("file_versions") or []
            for item in versions:
                if not isinstance(item, dict) or not item.get("content_hash", item.get("sha256")) or not Path(item["path"]).is_file() or _hash(Path(item["path"])) != item.get("content_hash", item.get("sha256")):
                    raise ValueError("历史产物文件版本无法验证")
            if capture.get("files") and not versions:
                raise ValueError("历史产物缺少文件版本证据")
            if capture.get("valid") is False:
                raise ValueError("历史产物不完整")
            if status == "delivered" and not versions and not capture.get("fingerprint"):
                raise ValueError("历史交付缺少可验证的产物版本")
            if task["metadata"].get("stage") and capture.get("fingerprint"):
                import workflow_state
                current = workflow_state.capture(db, task["project_id"], task["metadata"]["stage"])
                if current.get("fingerprint") != capture["fingerprint"] or not current.get("valid"):
                    raise ValueError("历史阶段产物已变化")
            import project_task_execution
            frozen_capture = dict(capture)
            frozen_capture["files"] = [item["path"] for item in versions] if versions else capture.get("files", [])
            if supplied.get("content") is not None:
                frozen_capture["content"] = supplied["content"]
            output = project_task_execution._outputs(db, task, run, {"reply": checkpoint.get("reply", "")}, frozen_capture)
            run["output_snapshot"] = output
            checkpoint.setdefault("output", supplied or output)
            _validate_versions(db, task, run)
            task_status = "delivered" if status == "delivered" else "blocked" if status == "rejected" else "awaiting_review"
        except (ValueError, OSError, KeyError) as exc:
            task_status, error = "blocked", str(exc)
    with _lock, _atomic(db):
        db.db.execute("INSERT INTO project_task_runs(id,task_id,attempt,status,request_key,policy_snapshot,input_snapshot,executor_snapshot,checkpoint,output_snapshot,error,started_at,ended_at) VALUES (?,?,1,?,?,?,?,?,?,?,?,?,?)",
            (run_id, task_id, run_status, "legacy:" + legacy_id, _json(policy), _json(inputs), _json(selected), _json(checkpoint), _json(output), error,
             legacy_node.get("started_at") or _now(), legacy_node.get("ended_at") or ""))
        db.db.execute("UPDATE project_tasks SET status=?,latest_run_id=?,version=version+1,updated_at=? WHERE id=?", (task_status, run_id, _now(), task_id))
        db.db.execute("INSERT INTO project_task_origins VALUES (?,?,?,?)", (origin_kind, legacy_id, task_id, run_id))
        _event(db, task_id, "legacy_restored", {"legacy_id": legacy_id, "legacy_status": status, "error": error}, run_id)
        if task_status == "delivered":
            _decision(db, task, run, approved=True, source="legacy", actor_id="", feedback=checkpoint.get("feedback", ""), request_key="legacy:" + legacy_id)
        elif task_status == "blocked" and status == "rejected" and not error:
            _decision(db, task, run, approved=False, source="legacy", actor_id="", feedback=checkpoint.get("feedback", ""), request_key="legacy:" + legacy_id)
    _notify(db, task_id, reconcile=True)
    return get(db, task_id)


def _scope_binding(scope, binding_id, member_id=""):
    allowed = scope.get("allowed_binding_ids")
    if allowed is not None and binding_id not in allowed:
        raise ValueError("执行者超出当前任务或工作流允许的范围")
    members = scope.get("allowed_member_ids")
    if members is not None and member_id and member_id not in members:
        raise ValueError("执行成员超出当前节点允许的范围")


def _check_json_schema(schema):
    supported = {"type", "enum", "required", "properties", "additionalProperties", "items", "minItems", "minLength",
                 "$schema", "title", "description", "default", "examples"}
    if not isinstance(schema, dict) or set(schema) - supported:
        raise ValueError("JSON 产物约定包含当前不支持的校验规则")
    if schema.get("type") not in (None, "object", "array", "string", "number", "integer", "boolean", "null"):
        raise ValueError("JSON 产物约定类型无效")
    if schema.get("additionalProperties") not in (None, True, False):
        raise ValueError("additionalProperties 须为布尔值")
    for rule in (schema.get("properties") or {}).values():
        _check_json_schema(rule)
    if "items" in schema:
        _check_json_schema(schema["items"])


def create(db, spec, *, authority, request_key=""):
    import project_bindings
    project_id = str(spec.get("project_id") or authority.project_id)
    _authority(authority, project_id)
    _project(db, project_id)
    goal = str(spec.get("goal") or spec.get("title") or "").strip()
    if not goal or len(goal) > 100000:
        raise ValueError("请填写有效的任务目标")
    binding_id = str(spec.get("executor_binding_id") or "")
    if not binding_id:
        raise ValueError("请先选择项目执行者")
    inputs, contract = spec.get("inputs") or [], spec.get("output_contract") or {}
    if not isinstance(inputs, list) or not isinstance(contract, dict):
        raise ValueError("任务输入须为数组，输出约定须为对象")
    if contract.get("json_schema") or contract.get("schema"):
        _check_json_schema(contract.get("json_schema") or contract["schema"])
    review_required = spec.get("review_required", True)
    if not isinstance(review_required, bool):
        raise ValueError("review_required 必须是布尔值")
    if not review_required and authority.source == "agent":
        raise ValueError("专家不能关闭人工核查；自动交付由已授权的执行模式决定")
    parent_id = str(spec.get("parent_task_id") or authority.parent_task_id or "")
    if authority.parent_task_id and parent_id != authority.parent_task_id:
        raise ValueError("子任务不能更换父任务")
    scope = dict(spec.get("scope") or {})
    flow, node = str(spec.get("workflow_run_id") or ""), str(spec.get("workflow_node_id") or "")
    if parent_id:
        parent = _task(db, parent_id)
        if parent["project_id"] != project_id:
            raise ValueError("父任务不属于当前项目")
        if parent["status"] in TERMINAL:
            raise ValueError("已结束的父任务不能派生新任务")
        inherited = parent["scope"]
        for field in ("allowed_binding_ids", "allowed_member_ids", "tools"):
            if field in inherited:
                if field in scope and inherited[field] is not None and (scope[field] is None or not set(scope[field]).issubset(inherited[field])):
                    raise ValueError("子任务不能扩大父任务授权范围")
                scope.setdefault(field, inherited[field])
        if parent["workflow_run_id"]:
            if flow and flow != parent["workflow_run_id"]:
                raise ValueError("子任务不能切换工作流")
            flow = parent["workflow_run_id"]
            if node and node != parent["workflow_node_id"]:
                raise ValueError("子任务不能切换工作流节点")
            node = parent["workflow_node_id"]
    if flow:
        if not parent_id and authority.source != "system":
            raise ValueError("工作流任务须由服务端解析当前实例范围")
        scope["kind"] = "workflow"
        if scope.get("allowed_binding_ids") is None:
            if authority.source != "system":
                raise ValueError("工作流任务须使用服务端解析的执行范围")
            scope["allowed_binding_ids"] = [binding_id]
    else:
        scope.setdefault("kind", "project")
    member_id = str(spec.get("member_id") or "")
    _scope_binding(scope, binding_id, member_id)
    executor = project_bindings.resolve(db, project_id, binding_id, member_id=member_id)
    if executor.get("team_id") and scope.get("allowed_member_ids") is not None and executor.get("member_id") not in scope["allowed_member_ids"]:
        raise ValueError("当前成员不在本节点执行范围内")
    dependencies = [str(x.get("task_id") if isinstance(x, dict) else x) for x in spec.get("dependencies") or []]
    for dependency in dependencies:
        if _task(db, dependency)["project_id"] != project_id:
            raise ValueError("依赖任务不属于当前项目")
    metadata = {k: v for k, v in (spec.get("metadata") or {}).items() if not k.startswith("_")}
    clean = {"project_id": project_id, "title": str(spec.get("title") or goal[:100]), "goal": goal,
             "executor_binding_id": binding_id, "member_id": member_id, "inputs": inputs,
             "output_contract": contract, "review_required": review_required, "parent_task_id": parent_id,
             "workflow_run_id": flow, "workflow_node_id": node, "scope": scope, "metadata": metadata,
             "dependencies": list(dict.fromkeys(dependencies))}
    digest = _digest(clean)
    with _lock, _atomic(db):
        if parent_id and _task(db, parent_id)["status"] in TERMINAL:
            raise ValueError("已结束的父任务不能派生新任务")
        old = db.db.execute("SELECT id,request_digest FROM project_tasks WHERE project_id=? AND request_key=?", (project_id, request_key)).fetchone() if request_key else None
        if old:
            if old["request_digest"] != digest:
                raise ValueError("该请求标识已用于另一份任务")
            return get(db, old["id"])
        task_id, stamp = _id("pt-"), _now()
        values = {**clean, "id": task_id, "status": "ready", "version": 1, "latest_run_id": "", "accepted_delivery_id": "",
                  "request_key": request_key, "request_digest": digest, "created_at": stamp, "updated_at": stamp}
        for key in _TASK_JSON:
            values[key] = _json(values[key])
        fields = list(values)
        db.db.execute("INSERT INTO project_tasks(" + ",".join(fields) + ") VALUES(" + ",".join("?" for _ in fields) + ")", [values[f] for f in fields])
        _event(db, task_id, "created", {"source": authority.source})
    _notify(db, task_id)
    return get(db, task_id)


def _root(db, project_id, *, writable=False):
    import project_notes
    return project_notes.project_root(db, project_id, writable=writable).resolve()


def _hash(path):
    import asset_service
    return asset_service.sha256_file(path)


def _snapshot_inputs(db, task, run_id):
    import asset_service
    root = _root(db, task["project_id"], writable=True)
    folder = root / ".tasks" / task["id"] / run_id / "inputs"
    assets = []
    for index, raw in enumerate(task["inputs"]):
        item = {"asset_id": raw} if isinstance(raw, str) else dict(raw)
        asset_id, relative = str(item.get("asset_id") or ""), str(item.get("relative") or "")
        asset = asset_service.get(db, asset_id) if asset_id else None
        if asset_id and (not asset or asset["project_id"] != task["project_id"]):
            raise ValueError("输入资产不属于当前项目")
        if asset and asset_service.is_instruction_asset(asset):
            raise ValueError("指令资产不能作为研究资料输入")
        if relative:
            if Path(relative).is_absolute() or PureWindowsPath(relative).drive:
                raise ValueError("输入文件须为项目相对路径")
            source = (root / relative).resolve()
        elif asset and asset.get("storage") == "file":
            source = Path(asset["canonical_uri"]).resolve()
        elif asset:
            digest = asset.get("content_hash", "")
            if item.get("content_hash") and item["content_hash"] != digest:
                raise ValueError("输入资产版本已变化")
            assets.append({"asset_id": asset_id, "content_hash": digest})
            continue
        else:
            raise ValueError("输入须引用项目资产或相对文件")
        if not source.is_file() or (relative and not source.is_relative_to(root)):
            raise ValueError("输入文件不存在或超出项目范围")
        digest = _hash(source)
        expected = item.get("content_hash") or item.get("sha256")
        if expected and expected != digest:
            raise ValueError("输入文件版本已变化，请重新准备任务")
        destination = folder / (str(index) + "-" + source.name)
        if not destination.resolve().is_relative_to(root):
            raise ValueError("任务输入目录超出项目范围")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if _hash(destination) != digest:
            raise ValueError("复制输入时文件发生变化")
        assets.append({"asset_id": asset_id, "path": str(destination), "original_path": str(source),
                       "relative": source.relative_to(root).as_posix() if source.is_relative_to(root) else "", "content_hash": digest, "sha256": digest})
    dependencies = []
    for key in task["dependencies"]:
        dependency = get(db, key)
        if dependency["status"] != "delivered" or not dependency["accepted_delivery_id"]:
            raise ValueError("等待依赖任务交付：" + dependency["title"])
        dependencies.append({"task_id": key, "delivery_id": dependency["accepted_delivery_id"], "outputs": dependency["outputs"]})
    rejected = db.db.execute("SELECT feedback FROM project_task_deliveries WHERE task_id=? AND decision='rejected' ORDER BY rowid DESC LIMIT 1", (task["id"],)).fetchone()
    children = []
    for child in get(db, task["id"])["children"]:
        if child["status"] == "delivered":
            detail = get(db, child["id"])
            children.append({"task_id": child["id"], "delivery_id": child["accepted_delivery_id"], "outputs": detail["outputs"]})
    return {"goal": task["goal"], "assets": assets, "dependencies": dependencies,
            "children": children, "feedback": rejected["feedback"] if rejected else "",
            "execution_context": {k: v for k, v in task["metadata"].items() if not k.startswith("_")}}


def _policy(db, task, authority, mode=None):
    import project_bindings
    if mode is not None and mode not in MODES:
        raise ValueError("未知的执行模式")
    parent_id = authority.parent_task_id or task["parent_task_id"]
    parent_mode = None
    if parent_id:
        parent = get(db, parent_id)
        if parent["latest_run"]:
            parent_mode = _effective_policy(parent, parent["latest_run"])["autonomy_mode"]
    default = project_bindings.settings(db, task["project_id"]).get("autonomy_mode", "supervised")
    override = task["metadata"].get("_autonomy_override")
    previous = get_run(db, task["latest_run_id"])["policy_snapshot"] if task["latest_run_id"] else {}
    inherited = (override or {}).get("autonomy_mode") or parent_mode or previous.get("autonomy_mode") or default
    selected = mode or inherited
    if authority.source != "ui" and selected == "full_access" and inherited != "full_access":
        raise ValueError("专家不能自行升级为 Full access")
    source = "user" if mode and authority.source == "ui" else (override or {}).get("source") or ("parent" if parent_mode else previous.get("source") or "project")
    return {"autonomy_mode": selected, "source": source,
            "actor_id": authority.actor_id if authority.source == "ui" and (mode or override) else previous.get("actor_id", ""), "review_required": task["review_required"]}


def _resume_identity(task, inputs, executor):
    assets = [{k: item.get(k) for k in ("asset_id", "content_hash", "relative")} for item in inputs.get("assets", [])]
    return _digest({"goal": inputs.get("goal"), "assets": assets, "dependencies": inputs.get("dependencies"),
        "children": inputs.get("children"), "feedback": inputs.get("feedback"), "context": inputs.get("execution_context"),
        "assistant_id": executor.get("assistant_id"), "member": executor.get("member"), "tools": executor.get("tools"),
        "runtime_config": executor.get("runtime_config"), "scope": task["scope"]})


def block_start(db, task_id, error, *, authority):
    """Record a rejected dispatch without inventing an execution attempt."""
    with _lock, _atomic(db):
        task = _task(db, task_id)
        _authority(authority, task["project_id"])
        if task["latest_run_id"] or task["status"] not in ("draft", "ready", "blocked"):
            return get(db, task_id)
        metadata = {**task["metadata"], "_start_error": str(error)[:5000]}
        if task["status"] == "blocked" and metadata == task["metadata"]:
            return get(db, task_id)
        db.db.execute("UPDATE project_tasks SET status='blocked',metadata=?,version=version+1,updated_at=? WHERE id=?",
                      (_json(metadata), _now(), task_id))
        _event(db, task_id, "start_blocked", {"error": metadata["_start_error"]})
    _notify(db, task_id)
    return get(db, task_id)


def start(db, task_id, *, authority, request_key="", autonomy_mode=None, restart=False):
    import project_bindings
    task = _task(db, task_id)
    if task['metadata'].get('deleted_at'):
        raise ValueError('任务已删除，请先恢复任务记录')
    _authority(authority, task["project_id"])
    import research_plans
    research_plans.check_dependencies(db, task_id)
    if restart and authority.source == "agent":
        raise ValueError("工具结果未确认时须由用户明确重启")
    if authority.parent_task_id and task["parent_task_id"] != authority.parent_task_id and task_id != authority.parent_task_id:
        raise ValueError("执行任务不属于当前父任务")
    with _lock, _atomic(db):
        if request_key:
            old = db.db.execute("SELECT id FROM project_task_runs WHERE task_id=? AND request_key=?", (task_id, request_key)).fetchone()
            if old:
                return get_run(db, old["id"])
        task = _task(db, task_id)
        if task["status"] == "delivered":
            raise ValueError("任务已交付，请新建任务处理新的要求")
        if task["status"] == "awaiting_review":
            raise ValueError("请先审查或退回当前产物")
        interrupted = get_run(db, task["latest_run_id"]) if task["latest_run_id"] else None
        phase = (interrupted or {}).get("checkpoint", {}).get("phase")
        if phase == "tool" and not restart:
            raise ValueError("工具结果未确认，请核查后明确重启当前节点")
        if interrupted and interrupted["status"] == "interrupted" and not restart:
            _validate_versions(db, task, interrupted, outputs=False)
            db.db.execute("UPDATE project_task_runs SET status='queued',error='',ended_at='' WHERE id=?", (interrupted["id"],))
            db.db.execute("UPDATE project_tasks SET status='queued',updated_at=? WHERE id=?", (_now(), task_id))
            _event(db, task_id, "resumed", {}, interrupted["id"])
            return get_run(db, interrupted["id"])
        if task["latest_run_id"] and get_run(db, task["latest_run_id"])["checkpoint"].get("waiting_children"):
            if any(child["status"] != "delivered" for child in get(db, task_id)["children"]):
                raise ValueError("请等待子任务交付后再汇总")
        active = db.db.execute("SELECT id FROM project_task_runs WHERE task_id=? AND status IN ('queued','running')", (task_id,)).fetchone()
        if active:
            return get_run(db, active["id"])
        executor = project_bindings.resolve(db, task["project_id"], task["executor_binding_id"], member_id=task["member_id"])
        import agent_settings
        executor["runtime_config"] = agent_settings.runtime_config(db, task["project_id"], executor["assistant_id"], executor.get("member") or None)
        if not executor["runtime_config"].get("model_id"):
            executor["runtime_config"]["model_id"] = (db.active_model() or {}).get("id", "")
        _scope_binding(task["scope"], task["executor_binding_id"], executor.get("member_id", ""))
        policy = _policy(db, task, authority, autonomy_mode)
        run_id = _id("ptr-")
        snapshot = _snapshot_inputs(db, task, run_id)
        checkpoint = {"restart_approved": True} if restart else {}
        if interrupted and interrupted["status"] in ("failed", "interrupted") and phase in ("model", "tools_ready") and not restart:
            if _resume_identity(task, snapshot, executor) == _resume_identity(task, interrupted["input_snapshot"], interrupted["executor_snapshot"]):
                _validate_versions(db, task, interrupted, outputs=False)
                checkpoint = dict(interrupted["checkpoint"])
                origin = next((o for o in get(db, task_id)["origins"] if o["kind"] == "workflow_node" and o["task_run_id"] == interrupted["id"]), None)
                checkpoint["resumed_from"] = origin["legacy_id"] if origin else interrupted["id"]
        row = db.db.execute("SELECT COALESCE(MAX(attempt),0)+1 AS attempt FROM project_task_runs WHERE task_id=?", (task_id,)).fetchone()
        db.db.execute("INSERT INTO project_task_runs(id,task_id,attempt,status,request_key,policy_snapshot,input_snapshot,executor_snapshot,checkpoint,started_at) VALUES (?,?,?,'queued',?,?,?,?,?,?)",
                      (run_id, task_id, row["attempt"], request_key or _id("request-"), _json(policy), _json(snapshot), _json(executor), _json(checkpoint), _now()))
        metadata = dict(task["metadata"])
        metadata.pop("_start_error", None)
        db.db.execute("UPDATE project_tasks SET status='queued',latest_run_id=?,metadata=?,version=version+1,updated_at=? WHERE id=?", (run_id, _json(metadata), _now(), task_id))
        _event(db, task_id, "queued", {"policy": policy}, run_id)
    _notify(db, task_id)
    return get_run(db, run_id)


def claim(db, run_id, worker_id="worker"):
    with _lock, _atomic(db):
        run = get_run(db, run_id)
        if run["status"] != "queued":
            return None
        if _task(db, run["task_id"])["status"] == "cancelled":
            return None
        changed = db.db.execute("UPDATE project_task_runs SET status='running' WHERE id=? AND status='queued'", (run_id,))
        if not changed.rowcount:
            return None
        db.db.execute("UPDATE project_tasks SET status='running',version=version+1,updated_at=? WHERE id=?", (_now(), run["task_id"]))
        _event(db, run["task_id"], "running", {"worker": worker_id}, run_id)
    _notify(db, run["task_id"])
    return {"task": get(db, run["task_id"]), "run": get_run(db, run_id)}


def save_checkpoint(db, run_id, **values):
    run = get_run(db, run_id)
    checkpoint = {**run["checkpoint"], **values}
    db.db.execute("UPDATE project_task_runs SET checkpoint=? WHERE id=?", (_json(checkpoint), run_id))
    db.db.commit()
    return checkpoint


def _effective_policy(task, run):
    policy = dict(run["policy_snapshot"])
    policy.update(task["metadata"].get("_autonomy_override") or {})
    return policy


def _validate_contract(contract, output):
    content = output.get("content", output.get("reply", ""))
    text = content if isinstance(content, str) else _json(content)
    if len(text.strip()) < int(contract.get("min_chars") or 0):
        raise ValueError("任务产物未达到约定的最小内容长度")
    for section in contract.get("sections") or []:
        label = section.get("title", "") if isinstance(section, dict) else str(section)
        if label and label not in text:
            raise ValueError("任务产物缺少约定章节：" + label)
    available = {item.get("relative") for item in output.get("files", [])}
    for item in contract.get("required_files") or []:
        relative = item.get("relative") or item.get("path") if isinstance(item, dict) else str(item)
        if relative not in available:
            raise ValueError("任务缺少约定文件：" + str(relative))
    schema = contract.get("json_schema") or contract.get("schema")
    if contract.get("format") == "json" or contract.get("json") or schema:
        try:
            value = json.loads(text) if isinstance(content, str) else content
        except (TypeError, ValueError) as exc:
            raise ValueError("任务产物必须为有效 JSON") from exc
        if schema:
            _validate_json(value, schema)


def _validate_json(value, schema, path="$"):
    kinds = {"object": dict, "array": list, "string": str, "number": (int, float), "integer": int, "boolean": bool, "null": type(None)}
    kind = schema.get("type")
    if kind in kinds and (not isinstance(value, kinds[kind]) or kind in ("integer", "number") and isinstance(value, bool)):
        raise ValueError("JSON 产物类型不符：" + path)
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("JSON 产物取值不符：" + path)
    if isinstance(value, dict):
        for key in schema.get("required") or []:
            if key not in value:
                raise ValueError("JSON 产物缺少字段：" + path + "." + key)
        for key, rule in (schema.get("properties") or {}).items():
            if key in value:
                _validate_json(value[key], rule, path + "." + key)
        if schema.get("additionalProperties") is False and set(value) - set(schema.get("properties") or {}):
            raise ValueError("JSON 产物包含未约定字段：" + path)
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems") or 0):
            raise ValueError("JSON 产物条目不足：" + path)
        for index, item in enumerate(value):
            if schema.get("items"):
                _validate_json(item, schema["items"], path + "[" + str(index) + "]")
    if isinstance(value, str) and len(value) < int(schema.get("minLength") or 0):
        raise ValueError("JSON 产物字段过短：" + path)


def _validate_versions(db, task, run, *, outputs=True):
    import asset_service
    import project_bindings
    frozen = run["executor_snapshot"]
    current = project_bindings.resolve(db, task["project_id"], task["executor_binding_id"], member_id=frozen.get("member_id", ""))
    if current["assistant_id"] != frozen["assistant_id"]:
        raise ValueError("任务执行者配置已变化，请重新准备任务")
    if any(current.get(key,'') != frozen.get(key,'') for key in ('team_id','member_id')):
        raise ValueError('任务执行团队、成员或项目归属已变化，请重新准备任务')
    if (current.get("assistant") or {}).get("rules", "") != (frozen.get("assistant") or {}).get("rules", ""):
        raise ValueError("任务专家规则已变化，请重新准备任务")
    if frozen.get("runtime_config"):
        import agent_settings
        live = agent_settings.runtime_config(db, task["project_id"], current["assistant_id"], current.get("member") or None)
        if live["permission"] != frozen["runtime_config"]["permission"]:
            raise ValueError("任务工具权限配置已变化，请重新准备任务")
    granted = current.get("tools")
    if granted is not None and (frozen.get("tools") is None or not set(frozen["tools"]).issubset(granted)):
        raise ValueError("任务工具授权已收窄，请重新准备任务")
    for item in run["input_snapshot"].get("assets", []):
        if item.get("path"):
            path = Path(item["path"])
            if not path.is_file() or _hash(path) != item["content_hash"]:
                raise ValueError("任务输入快照已变化")
        elif item.get("asset_id"):
            asset = asset_service.get(db, item["asset_id"])
            if not asset or asset.get("content_hash") != item["content_hash"]:
                raise ValueError("任务输入资产版本已变化")
    for dependency in [*run["input_snapshot"].get("dependencies", []), *run["input_snapshot"].get("children", [])]:
        current = _task(db, dependency["task_id"])
        if current["status"] != "delivered" or current["accepted_delivery_id"] != dependency["delivery_id"]:
            raise ValueError("依赖任务交付版本已变化")
        for item in dependency.get("outputs", []):
            path = Path(item["path"])
            if not path.is_file() or _hash(path) != item.get("content_hash", item.get("sha256")):
                raise ValueError("依赖产物已变化")
    if outputs:
        files = run["output_snapshot"].get("files", [])
        if not files:
            raise ValueError("任务尚无实际产物")
        root = _root(db, task["project_id"])
        for item in files:
            path = Path(item["path"]).resolve()
            if not path.is_relative_to(root) or not path.is_file() or _hash(path) != item.get("content_hash", item.get("sha256")):
                raise ValueError("待验收产物已变化或超出项目范围")
        _validate_contract(task["output_contract"], run["output_snapshot"])
        function = _adapter("validate")
        if function:
            function(db, task, run)


def _decision(db, task, run, *, approved, source, actor_id, feedback, request_key):
    decision_id = _id("ptd-")
    policy = _effective_policy(task, run)
    db.db.execute("INSERT INTO project_task_deliveries VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
        decision_id, task["id"], run["id"], "approved" if approved else "rejected", source, actor_id,
        _json(run["input_snapshot"]), _json(run["output_snapshot"]), _json(policy), feedback, request_key, _now()))
    db.db.execute("UPDATE project_tasks SET status=?,accepted_delivery_id=?,version=version+1,updated_at=? WHERE id=? AND latest_run_id=?",
                  ("delivered" if approved else "blocked", decision_id if approved else "", _now(), task["id"], run["id"]))
    _event(db, task["id"], "delivered" if approved else "rejected", {"source": source, "delivery_id": decision_id, "feedback": feedback}, run["id"])


def complete(db, run_id, result):
    run = get_run(db, run_id)
    task = _task(db, run["task_id"])
    if run["status"] not in ("queued", "running") or task["status"] == "cancelled":
        return get(db, task["id"])
    output = result.get("output_snapshot", result.get("output", {}))
    checkpoint = {**run["checkpoint"], **result.get("checkpoint", {}), "reply": result.get("reply", "")}
    checkpoint.setdefault("output", output)
    waiting = any(child["status"] != "delivered" for child in get(db, task["id"])["children"])
    checkpoint["waiting_children"] = waiting
    candidate = {**run, "output_snapshot": output, "checkpoint": checkpoint}
    _validate_versions(db, task, candidate)
    policy = _effective_policy(task, run)
    with _lock, _atomic(db):
        current = get_run(db, run_id)
        if current["status"] not in ("queued", "running") or _task(db, task["id"])["status"] == "cancelled":
            return get(db, task["id"])
        waiting = bool(db.db.execute("SELECT 1 FROM project_tasks WHERE parent_task_id=? AND status<>'delivered' LIMIT 1", (task["id"],)).fetchone())
        checkpoint["waiting_children"] = waiting
        db.db.execute("UPDATE project_task_runs SET status='succeeded',checkpoint=?,output_snapshot=?,ended_at=? WHERE id=?", (_json(checkpoint), _json(output), _now(), run_id))
        db.db.execute("UPDATE project_tasks SET status=?,version=version+1,updated_at=? WHERE id=?", ("blocked" if waiting else "awaiting_review", _now(), task["id"]))
        _event(db, task["id"], "waiting_children" if waiting else "output_ready", {}, run_id)
        if not waiting and (not task["review_required"] or policy["autonomy_mode"] == "full_access"):
            _decision(db, task, candidate, approved=True, source="policy", actor_id="", feedback="", request_key="policy:" + run_id)
    _notify(db, task["id"])
    value = get(db, task["id"])
    _wake_parent(db, value)
    return value


def review(db, task_id, *, approved, feedback="", authority, request_key="", reviewed_run_id="",reviewed_output_version=None):
    task = _task(db, task_id)
    _authority(authority, task["project_id"], human=True)
    if not isinstance(approved, bool):
        raise ValueError("请明确选择通过或退回")
    feedback = str(feedback).strip()[:20000]
    if not approved and not feedback:
        raise ValueError("退回时请填写修改意见")
    run = get_run(db, task["latest_run_id"]) if task["latest_run_id"] else None
    if reviewed_run_id and reviewed_run_id != (run or {}).get("id"):
        raise ValueError("任务已有新的运行，请打开当前产物后重新核查")
    revision=(run or {}).get('checkpoint',{}).get('output_revision',0)
    if (revision or reviewed_output_version is not None) and reviewed_output_version!=revision:
        raise ValueError('产物修订版本已变化，请重新查看当前产物后核查')
    request_key = request_key or ("human:" + (run or {}).get("id", "") + ":" + str(approved))
    old = db.db.execute("SELECT id FROM project_task_deliveries WHERE task_id=? AND request_key=?", (task_id, request_key)).fetchone()
    if old:
        return get(db, task_id)
    if task["status"] != "awaiting_review" or not run or run["status"] != "succeeded":
        raise ValueError("该任务当前不在待验收状态")
    if approved:
        _validate_versions(db, task, run)
    with _lock, _atomic(db):
        current = _task(db, task_id)
        current_revision=get_run(db,run['id'])['checkpoint'].get('output_revision',0)
        if current_revision!=revision:raise ValueError('产物修订版本已变化，请重新查看后核查')
        if current["status"] != "awaiting_review" or current["latest_run_id"] != run["id"]:
            old = db.db.execute("SELECT id FROM project_task_deliveries WHERE task_id=? AND request_key=?", (task_id, request_key)).fetchone()
            if old:
                return get(db, task_id)
            raise ValueError("任务状态已变化，请刷新")
        _decision(db, current, run, approved=approved, source="human", actor_id=authority.actor_id, feedback=feedback, request_key=request_key)
    _notify(db, task_id)
    value = get(db, task_id)
    _wake_parent(db, value)
    return value


def fail(db, run_id, error, *, cancelled=False):
    run = get_run(db, run_id)
    task = _task(db, run["task_id"])
    if task["status"] in TERMINAL or run["status"] not in ("queued", "running"):
        return get(db, task["id"])
    status = "cancelled" if cancelled else "failed"
    with _lock, _atomic(db):
        db.db.execute("UPDATE project_task_runs SET status=?,error=?,ended_at=? WHERE id=?", (status, str(error)[:5000], _now(), run_id))
        db.db.execute("UPDATE project_tasks SET status=?,version=version+1,updated_at=? WHERE id=? AND latest_run_id=?", (status, _now(), task["id"], run_id))
        _event(db, task["id"], status, {"error": str(error)}, run_id)
    _notify(db, task["id"])
    return get(db, task["id"])


def interrupt(db, run_id, reason="Service restarted; resume the saved attempt"):
    run = get_run(db, run_id)
    if run["status"] not in ("queued", "running"):
        return get(db, run["task_id"])
    with _lock, _atomic(db):
        db.db.execute("UPDATE project_task_runs SET status='interrupted',error=?,ended_at=? WHERE id=? AND status IN ('queued','running')", (reason, _now(), run_id))
        db.db.execute("UPDATE project_tasks SET status='blocked',updated_at=? WHERE id=? AND latest_run_id=? AND status IN ('queued','running')", (_now(), run["task_id"], run_id))
        _event(db, run["task_id"], "interrupted", {"reason": reason}, run_id)
    _notify(db, run["task_id"])
    return get(db, run["task_id"])


def cancel(db, task_id, *, authority):
    task = _task(db, task_id)
    _authority(authority, task["project_id"])
    if authority.parent_task_id and task_id != authority.parent_task_id and task["parent_task_id"] != authority.parent_task_id:
        raise ValueError("只能停止当前任务及其子任务")
    if task["status"] in TERMINAL:
        return get(db, task_id)
    with _lock, _atomic(db):
        db.db.execute("UPDATE project_tasks SET status='cancelled',version=version+1,updated_at=? WHERE id=?", (_now(), task_id))
        db.db.execute("UPDATE project_task_runs SET status='cancelled',error='用户停止任务',ended_at=? WHERE task_id=? AND status IN ('queued','running')", (_now(), task_id))
        _event(db, task_id, "cancelled", {"source": authority.source})
    try:
        import project_task_execution
        project_task_execution.request_cancel(task["latest_run_id"])
    except ImportError:
        pass
    for child in get(db, task_id)["children"]:
        cancel(db, child["id"], authority=CommandContext.system(task["project_id"], task_id))
    _notify(db, task_id)
    return get(db, task_id)


def set_mode(db, task_id, mode, *, authority):
    task = _task(db, task_id)
    _authority(authority, task["project_id"], human=True)
    if mode not in MODES:
        raise ValueError("未知的执行模式")
    metadata = {**task["metadata"], "_autonomy_override": {"autonomy_mode": mode, "source": "user", "actor_id": authority.actor_id}}
    with _lock, _atomic(db):
        db.db.execute("UPDATE project_tasks SET metadata=?,version=version+1,updated_at=? WHERE id=?", (_json(metadata), _now(), task_id))
        _event(db, task_id, "policy_changed", {"autonomy_mode": mode, "actor_id": authority.actor_id}, task["latest_run_id"])
    task = _task(db, task_id)
    if task["status"] == "awaiting_review" and mode == "full_access":
        run = get_run(db, task["latest_run_id"])
        _validate_versions(db, task, run)
        with _lock, _atomic(db):
            if _task(db, task_id)["status"] == "awaiting_review":
                _decision(db, task, run, approved=True, source="policy", actor_id="", feedback="", request_key="mode:" + run["id"])
    _notify(db, task_id)
    return get(db, task_id)
