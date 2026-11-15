"""Project file tasks whose target is always a node of the active workflow."""
from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath
import shutil
import uuid

import asset_service
import project_notes
import run_service
import workflow
import workflow_execution as engine
import workflow_layout
import workflow_state


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def current_layout(db, project_id):
    workflow_layout._project(db, project_id)
    if not db.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_workflow_layouts'").fetchone():
        raise ValueError("项目尚未绑定工作流，请先打开科研平台并配置工作流")
    row = db.db.execute("SELECT layout_id FROM project_workflow_layouts WHERE task_id=?", (project_id,)).fetchone()
    if not row:
        raise ValueError("项目尚未绑定工作流，请先打开科研平台并配置工作流")
    import project_workflows
    layout = project_workflows.effective(db, project_id, workflow_layout._layout(db, row["layout_id"]))
    return {**layout, **workflow_layout.validate_definition(layout)}


def actors(db, project_id, node):
    """Read configured profiles without copying another project's runtime."""
    mode = node.get("execution_mode") or ("team" if node.get("team_id") else "assistant")
    aid, tid, mid = (node.get(k, "") for k in ("assistant_id", "team_id", "member_id"))
    if mode == "assistant":
        if tid or mid:
            raise ValueError("专家执行模式仍有团队绑定，请重新保存节点执行配置")
        if not aid:
            raise ValueError("尚未配置执行专家或团队")
        members = [None]
        team = None
    else:
        import shared_executors
        team = db.team(tid) if tid else None
        if not team or team.get("status") in ("archived", "disabled", "dissolved") or (team.get("project_id") != project_id and shared_executors.is_instance(db,tid)):
            raise ValueError("节点团队不存在、已停用或属于其他项目")
        roster = db.team_members(tid)
        members = [m for m in roster if (not mid or m["id"] == mid)
                   and ("eligible_member_ids" not in node or m["id"] in node["eligible_member_ids"])
                   and m.get("role") not in ("reviewer", "observer")
                   and m.get("status") not in ("disabled", "archived")
                   and (mid or m.get("step") in ("", node["step"]) or m.get("role") == "leader")]
        members.sort(key=lambda m: (m.get("step") != node["step"], m.get("role") != "leader", m["id"]))
        if not members:
            raise ValueError("节点团队没有符合职责的执行成员，或指定成员已失效")
    results = []
    errors = []
    for member in members:
        effective = member.get("assistant_id", "") if member else aid
        if member and aid and aid != effective:
            errors.append("专家与团队成员绑定冲突，请在节点设置中明确执行方式")
            continue
        assistant = db.assistant(effective) if effective else None
        if not assistant or not assistant.get("enabled"):
            errors.append("执行专家不存在或已停用")
            continue
        results.append({"assistant_id": effective, "assistant_name": assistant["name"],
                        "team_id": tid if member else "", "team_name": team["name"] if team else "",
                        "member_id": member["id"] if member else "", "member_name": member["name"] if member else "",
                        "execution_mode": mode, **({"binding_source": node["binding_source"]} if node.get("binding_source") else {})})
    if not results:
        raise ValueError(errors[0] if errors else "没有可用的执行专家")
    return results


def targets(db, project_id):
    # Intentionally use direct reads: opening this picker must not seed teams,
    # bind a default workflow, create sessions or register output assets.
    layout = current_layout(db, project_id)
    import team_configuration
    from workflow_catalog import LEGACY_TEAM_WORKFLOWS
    legacy = team_configuration.legacy_plan(db, project_id) if layout["id"] in LEGACY_TEAM_WORKFLOWS else []
    nodes = []
    fingerprints = []
    import project_bindings
    roles = {r["role_id"]: r for r in project_bindings.list_roles(db, project_id, layout["id"])}
    for node in layout["nodes"]:
        reason = ""
        try:
            explicit = any(node.get(key) for key in ('executor_binding_id','assistant_id','team_id','execution_mode'))
            node = workflow_layout.resolve_node(db, project_id, layout["id"], node, materialize=False)
            role = roles.get(project_bindings.role_id(node), {})
            if role.get("source") == "user" and not explicit:
                executor = project_bindings.resolve_role(db, project_id, layout["id"], project_bindings.role_id(node), materialize=False)
                node = {**node, "execution_mode": "team" if executor.get("team_id") else "assistant",
                        "team_id": executor.get("team_id", ""), "member_id": executor.get("requested_member_id", ""),
                        "assistant_id": executor["assistant_id"] if not executor.get("team_id") else ""}
            choices = actors(db, project_id, node)
            for choice in choices:
                kind, target_id = ("team", choice["team_id"]) if choice["team_id"] else ("assistant", choice["assistant_id"])
                grant = next((b for b in project_bindings.list_bindings(db, project_id) if b["kind"] == kind and b["target_id"] == target_id), None)
                if grant:
                    project_bindings.resolve(db, project_id, grant["id"], member_id=choice["member_id"], materialize=False)
        except ValueError as exc:
            choices, reason = [], str(exc)
        for actor in choices:
            assistant = db.assistant(actor["assistant_id"])
            member = db.team_member(actor["member_id"]) if actor["member_id"] else None
            fingerprints.append([assistant, member, db.team(actor["team_id"]) if actor["team_id"] else None])
        nodes.append({**node, "actors": choices, "disabled_reason": reason,
                      "dependencies": [e["source"] for e in layout["edges"] if e["target"] == node["id"]]})
    # Member busy/idle changes are runtime state, not routing changes.
    for group in fingerprints:
        for item in group:
            if item:
                item.pop("updated_at", None)
                if item.get("status") not in ("disabled", "archived"):
                    item.pop("status", None)
    team_count = db.db.execute("SELECT COUNT(*) FROM teams WHERE project_id=?", (project_id,)).fetchone()[0]
    return {"project_id": project_id, "layout_id": layout["id"], "layout_name": layout["name"],
            "revision": workflow_state.digest([layout, nodes, fingerprints]), "nodes": nodes,
            "configuration": {"legacy_leaders": len(legacy), "team_count": team_count}}


def repair_configuration(db, body):
    project_id = str(body.get("project_id") or "")
    layout = current_layout(db, project_id)
    if body.get("layout_id") != layout["id"]:
        raise ValueError("工作流已切换，请刷新后再修复")
    from workflow_catalog import LEGACY_TEAM_WORKFLOWS
    if layout["id"] not in LEGACY_TEAM_WORKFLOWS:
        raise ValueError("自定义工作流请在节点中明确配置执行者")
    import team_configuration
    count = team_configuration.migrate(db, project_id, once=False)
    return {"repaired_leaders": count, "targets": targets(db, project_id)}


def _target(db, body):
    data = targets(db, body["project_id"])
    if body.get("layout_id") != data["layout_id"] or body.get("revision") != data["revision"]:
        raise ValueError("工作流或执行配置已变化，请刷新目标并重新选择；任务说明会保留")
    node = next((n for n in data["nodes"] if n["id"] == body.get("node_id")), None)
    if not node or not node["actors"]:
        raise ValueError(node["disabled_reason"] if node else "目标节点不在当前工作流中")
    actor = next((a for a in node["actors"] if all(a[k] == body.get(k, "")
                 for k in ("assistant_id", "team_id", "member_id"))), None)
    if not actor:
        raise ValueError("该专家或成员不能执行当前节点，请重新选择工作流内的目标")
    return node, actor


def _file(db, project_id, relative):
    root = project_notes.project_root(db, project_id)
    if not relative or Path(relative).is_absolute() or PureWindowsPath(relative).drive:
        raise ValueError("请选择当前项目中的文件")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("文件不存在或不属于当前项目")
    return path


def detail(db, handoff_id):
    row = db.db.execute("SELECT * FROM file_handoffs WHERE id=?", (handoff_id,)).fetchone()
    if not row:
        raise ValueError("文件交接记录不存在")
    result = dict(row)
    result["payload"] = json.loads(result["payload"])
    result["run"] = engine.detail(db, result["run_id"]) if result["run_id"] else None
    result["status"] = result["run"]["status"] if result["run"] else "draft"
    return result


def history(db, project_id, relative="", limit=50, summary=False):
    workflow_layout._project(db, project_id)
    sql = "SELECT id FROM file_handoffs WHERE project_id=?"
    args = [project_id]
    if relative:
        sql += " AND json_extract(payload,'$.relative')=?"
        args.append(relative)
    args.append(max(1, min(int(limit), 50)))
    rows = db.db.execute(sql + " ORDER BY rowid DESC LIMIT ?", args).fetchall()
    if not summary:
        return [detail(db, r["id"]) for r in rows]
    results = []
    for row in rows:
        item = dict(db.db.execute("SELECT * FROM file_handoffs WHERE id=?", (row["id"],)).fetchone())
        item["payload"] = json.loads(item["payload"])
        item["status"] = (run_service.get(db, item["run_id"]) or {}).get("status", "draft")
        results.append(item)
    return results


def prepare(db, body):
    project_id = str(body.get("project_id") or "")
    key = str(body.get("request_key") or "")
    if not key or len(key) > 120:
        raise ValueError("缺少有效的请求标识")
    request = {k: str(body.get(k) or "") for k in
               ("project_id", "layout_id", "revision", "node_id", "assistant_id", "team_id", "member_id", "relative", "instruction", "parent_id")}
    if len(request["instruction"]) > 20000:
        raise ValueError("任务说明不能超过 20000 字符")
    digest = workflow_state.digest(request)
    with engine._lock:
        old = db.db.execute("SELECT id,request_hash FROM file_handoffs WHERE project_id=? AND request_key=?", (project_id, key)).fetchone()
        if old:
            if old["request_hash"] != digest:
                raise ValueError("请求标识已用于另一份任务，请重新准备")
            return detail(db, old["id"])
        node, actor = _target(db, request)
        if request["parent_id"]:
            parent = detail(db, request["parent_id"])
            if any(parent["payload"].get(k, "") != request[k] for k in
                   ("project_id", "layout_id", "revision", "node_id", "assistant_id", "team_id", "member_id")):
                raise ValueError("只能补充同一项目、工作流版本、节点和执行者的任务")
            chain = parent["payload"].get("related_ids", [])
            if len(chain) >= 9:
                raise ValueError("连续补充已达到 10 份材料，请新建节点任务")
            request["related_ids"] = [*chain, parent["id"]]
        path = _file(db, project_id, request["relative"])
        existing = db.db.execute("SELECT * FROM assets WHERE canonical_uri=?", (str(path),)).fetchone()
        if existing and existing["project_id"] not in ("", project_id):
            raise ValueError("此文件已归属其他项目，请检查项目文件夹是否重叠")
        asset = asset_service.register_file(db, path, project_id=project_id,
            kind=existing["kind"] if existing else "document", title=existing["title"] if existing else "",
            source=existing["source"] if existing else "file-handoff")
        payload = {**request, "relative": path.relative_to(project_notes.project_root(db, project_id).resolve()).as_posix(),
                   "path": str(path), "sha256": asset["content_hash"], "asset_id": asset["id"],
                   "actor": actor, "node_label": node["label"], "step": node["step"]}
        hid = "handoff-" + uuid.uuid4().hex
        db.db.execute("INSERT INTO file_handoffs VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (hid, project_id, request["layout_id"], request["revision"], node["id"], key, digest, _json(payload), "", workflow.now()))
        db.db.commit()
        return detail(db, hid)


def validate_run(db, run):
    hid = run["settings"].get("handoff_id")
    if not hid:
        return
    record = detail(db, hid)
    if 'revision' in run['layout']:
        node=next((node for node in run['layout']['nodes'] if node['id']==record['payload']['node_id']),None)
        if not node:raise ValueError('文件任务的运行快照缺少目标节点')
        actor=run['settings']['actor']
        engine._actor(db,run['project_id'],{**node,**actor})
        import project_bindings
        kind='team' if actor.get('team_id') else 'assistant'
        target=actor.get('team_id') or actor['assistant_id']
        if kind=='team':
            profile=db.db.execute('SELECT template_team_id FROM team_execution_instances WHERE instance_team_id=?',(target,)).fetchone()
            if profile:target=profile[0]
        grant=next((binding for binding in project_bindings.list_bindings(db,run['project_id'])
                    if binding['kind']==kind and binding['target_id']==target),None)
        if not grant:raise ValueError('文件任务的执行者已不在当前项目范围内')
        project_bindings.resolve(db,run['project_id'],grant['id'],member_id=actor.get('member_id',''))
    else:
        _target(db, record["payload"])
    snapshot = run["settings"]["file_input"]
    for item in [snapshot, *snapshot.get("related_inputs", [])]:
        path = Path(item["path"])
        if not path.is_file() or asset_service.sha256_file(path) != item["sha256"]:
            raise ValueError("交接输入快照丢失或已变化，请重新准备任务")
    other = db.db.execute("SELECT id FROM workflow_runs WHERE project_id=? AND id<>? "
                          "AND status IN ('queued','running','awaiting_review','blocked','interrupted') "
                          "AND workflow IN ('research-sequence','file-handoff') LIMIT 1", (run["project_id"], run["id"])).fetchone()
    if other:
        raise ValueError("项目已有执行或待审查任务，请先处理现有运行；文件任务已保留")


def start(db, handoff_id):
    with engine._lock:
        record = detail(db, handoff_id)
        if record["run_id"]:
            return record
        payload = record["payload"]
        node, actor = _target(db, payload)
        project_id = record["project_id"]
        active = engine.active(db, project_id)
        if active:
            return {**record, "waiting_reason": "项目已有执行或待审查任务。材料已保存，请在现有运行结束后开始本任务。"}
        layout = current_layout(db, project_id)
        stages = [n["step"] for n in layout["nodes"]]
        state = (workflow_state.node_states(db, project_id, layout)[node["id"]] if "task" in stages or len(stages) != len(set(stages))
                 else workflow_state.states(db, project_id)[node["step"]])
        if state["status"] == "running":
            return {**record, "waiting_reason": "此节点已有任务正在执行，材料已保留，完成后可再次开始。"}
        if state["blocked_by"]:
            return {**record, "waiting_reason": "等待前置节点验收：" + "、".join(state["blocked_by"])}
        source = _file(db, project_id, payload["relative"])
        if asset_service.sha256_file(source) != payload["sha256"]:
            raise ValueError("原文件已变化，请用最新保存的文件重新准备任务")
        layout = current_layout(db, project_id)
        configured = next(n for n in layout["nodes"] if n["id"] == node["id"])
        effective = {**configured, **{k: actor[k] for k in ("execution_mode", "assistant_id", "team_id", "member_id")}}
        engine._actor(db, project_id, effective)
        root = project_notes.project_root(db, project_id, writable=True)
        snapshot = root / ".handoffs" / handoff_id / source.name
        if not snapshot.resolve().is_relative_to(root.resolve()):
            raise ValueError("交接快照目录必须位于当前项目内")
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, snapshot)
        if asset_service.sha256_file(snapshot) != payload["sha256"]:
            raise ValueError("保存交接快照时文件发生变化，请重新准备任务")
        related = []
        for related_id in payload.get("related_ids", []):
            prior = detail(db, related_id)
            old = prior["payload"]
            original = Path(prior["run"]["settings"]["file_input"]["path"]) if prior["run"] else _file(db, project_id, old["relative"])
            if not original.is_file() or asset_service.sha256_file(original) != old["sha256"]:
                raise ValueError("关联任务的输入文件已变化，请先重新准备该任务")
            dest = snapshot.parent / (related_id + original.suffix)
            if not dest.resolve().is_relative_to(root.resolve()):
                raise ValueError("交接快照目录必须位于当前项目内")
            shutil.copyfile(original, dest)
            if asset_service.sha256_file(dest) != old["sha256"]:
                raise ValueError("关联材料复制时发生变化，请重新准备任务")
            records = prior["run"]["nodes"] if prior["run"] else []
            related.append({"handoff_id": related_id, "path": str(dest), "sha256": old["sha256"],
                            "asset_id": old["asset_id"], "instruction": old["instruction"],
                            "previous_reply": records[-1]["checkpoint"].get("reply", "") if records else ""})
        run = run_service.start(db, project_id, "file-handoff", idempotency_key=handoff_id)
        settings = {"handoff_id": handoff_id, "node_ids": [node["id"]], "actor": actor,
                    "goal": payload["instruction"], "file_input": {"path": str(snapshot), "original_path": str(source),
                    "sha256": payload["sha256"], "asset_id": payload["asset_id"], "related_inputs": related}}
        db.db.execute("INSERT INTO workflow_executions VALUES (?,?,?,?)", (run["id"], _json(layout), _json(settings), workflow.now()))
        db.db.execute("UPDATE file_handoffs SET run_id=? WHERE id=?", (run["id"], handoff_id))
        engine._status(db, run["id"], "queued")
        return detail(db, handoff_id)


def route(method, path, body, query, db):
    if not path.startswith("/api/handoff/"):
        return None
    action = path.rsplit("/", 1)[-1]
    if method == "GET":
        get = lambda name: (query.get(name) or [""])[0]
        if action == "targets":
            return targets(db, get("project_id"))
        if action == "history":
            return {"items": history(db, get("project_id"), get("relative"), get("limit") or 50, get("summary") == "1")}
        if action == "detail":
            return {"handoff": detail(db, get("id"))}
    elif method == "POST":
        if action == "repair-configuration":
            return repair_configuration(db, body)
        if action == "prepare":
            return {"handoff": prepare(db, body)}
        if action == "start":
            result = start(db, str(body.get("id") or ""))
            if result["run_id"] and result["status"] == "queued":
                engine.kick(db.path, result["run_id"])
            return {"handoff": result}
    raise ValueError("不支持的文件交接操作")
