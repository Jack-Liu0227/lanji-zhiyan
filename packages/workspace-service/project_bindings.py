"""Project executor grants and template roles, independent of execution state."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import uuid


MODES = frozenset(("supervised", "full_access"))
_ROLE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


def _conn(db):
    return db.db if hasattr(db, "db") else db


def _now():
    return datetime.now(timezone.utc).isoformat()


def _project(db, project_id):
    if not project_id or not _conn(db).execute("SELECT 1 FROM research_tasks WHERE id=?", (project_id,)).fetchone():
        raise ValueError("请选择有效项目")


def _table(db, name):
    return bool(_conn(db).execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _record(db, table, record_id):
    # All table names here are internal constants, never request parameters.
    row = _conn(db).execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
    return dict(row) if row else None


def _tools(value):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 300 or any(not isinstance(x, str) or not x or len(x) > 200 for x in value):
        raise ValueError("工具范围必须是工具 ID 数组或 null")
    return list(dict.fromkeys(value))


def _binding(db, project_id, binding_id):
    row = _conn(db).execute("SELECT * FROM project_executor_bindings WHERE id=? AND project_id=?", (binding_id, project_id)).fetchone()
    if not row and binding_id.startswith('global:'):
        import shared_executors
        _,kind,target_id=binding_id.split(':',2)
        existing=_conn(db).execute('SELECT * FROM project_executor_bindings WHERE project_id=? AND kind=? AND target_id=?',(project_id,kind,target_id)).fetchone()
        return dict(existing) if existing else shared_executors.virtual(db,project_id,kind,target_id)
    if not row:
        raise ValueError("执行者绑定不存在或不属于当前项目")
    return dict(row)


def _public_binding(db, row):
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    result["tools"] = _tools(json.loads(result["tools"]) if result["tools"] is not None else None)
    target = _record(db, "assistants" if result["kind"] == "assistant" else "teams", result["target_id"])
    result["name"] = (target or {}).get("name") or result["target_id"]
    return result


def _remember_origin(db, binding_id, source, source_id):
    _conn(db).execute("INSERT OR IGNORE INTO project_binding_origins VALUES (?,?,?,?)",
                      (binding_id, source, source_id, _now()))


def register(db, project_id, kind, target_id, *, tools=None, enabled=True, source="user"):
    _project(db, project_id)
    if kind not in ("assistant", "team"):
        raise ValueError("执行者类型必须是 assistant 或 team")
    if not isinstance(enabled, (bool, int)) or enabled not in (True, False, 0, 1):
        raise ValueError("enabled 必须是布尔值")
    if not isinstance(source, str) or not source or len(source) > 200:
        raise ValueError("绑定来源无效")
    target_id = str(target_id or "")
    target = _record(db, "assistants" if kind == "assistant" else "teams", target_id)
    if not target:
        raise ValueError("专家不存在" if kind == "assistant" else "团队不存在")
    if kind == "team":
        import shared_executors
        if shared_executors.is_instance(db,target_id) and target.get('project_id')!=project_id:
            raise ValueError('团队运行实例不属于当前项目')
    allowed = _tools(tools)
    encoded = json.dumps(allowed, ensure_ascii=False) if allowed is not None else None
    conn = _conn(db)
    row = conn.execute("SELECT * FROM project_executor_bindings WHERE project_id=? AND kind=? AND target_id=?",
                       (project_id, kind, target_id)).fetchone()
    stamp = _now()
    with conn:
        if row:
            # Re-importing evidence is not authorization to undo a user's denial.
            if not source.startswith("legacy") and (bool(row["enabled"]) != bool(enabled) or row["tools"] != encoded or row["source"] != source):
                conn.execute("UPDATE project_executor_bindings SET enabled=?,tools=?,source=?,revision=revision+1,updated_at=? WHERE id=?",
                             (int(bool(enabled)), encoded, source, stamp, row["id"]))
            binding_id = row["id"]
        else:
            binding_id = "pex-" + uuid.uuid5(uuid.NAMESPACE_URL, project_id + ":" + kind + ":" + target_id).hex
            conn.execute("INSERT INTO project_executor_bindings VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (binding_id, project_id, kind, target_id, int(bool(enabled)), encoded, source, 1, stamp, stamp))
        _remember_origin(db, binding_id, source, target_id)
    return _public_binding(db, _binding(db, project_id, binding_id))


def _assistant(db, project_id, assistant_id):
    assistant = _record(db, "assistants", assistant_id)
    if not assistant or not assistant.get("enabled"):
        raise ValueError("执行专家不存在或已停用")
    grant = _conn(db).execute("SELECT enabled,tools FROM project_executor_bindings WHERE project_id=? AND kind='assistant' AND target_id=?",
                             (project_id, assistant_id)).fetchone()
    if grant and not grant["enabled"]:
        raise ValueError("该专家在当前项目中已停用")
    return assistant, _tools(json.loads(grant["tools"]) if grant and grant["tools"] is not None else None)


def _intersect(left, right):
    if left is None:
        return right
    if right is None:
        return left
    return [tool for tool in left if tool in set(right)]


def resolve(db, project_id, binding_id, *, member_id="", materialize=True):
    _project(db, project_id)
    grant = _public_binding(db, _binding(db, project_id, binding_id))
    if not grant["enabled"]:
        raise ValueError("该执行者绑定在当前项目中已停用")
    member = None
    team = None
    requested_member_id = str(member_id or "")
    if grant["kind"] == "assistant":
        if requested_member_id:
            raise ValueError("直接专家任务不能指定团队成员")
        assistant_id = grant["target_id"]
    else:
        import shared_executors
        team = shared_executors.team_instance(db,project_id,grant['target_id']) if materialize else db.team(grant['target_id'])
        requested_member_id=shared_executors.member_id(db,team['id'],requested_member_id)
        if not team or (team.get("project_id") != project_id and (materialize or shared_executors.is_instance(db,team['id']))) or team.get("status") in ("disabled", "archived", "dissolved"):
            raise ValueError("团队不存在、已停用或不属于当前项目")
        roster = shared_executors.profile_members(db, team['id'], project_id)
        if requested_member_id:
            member = next((row for row in roster if row["id"] == requested_member_id), None)
            if not member:
                raise ValueError("所选成员不属于该团队或已被移除")
        else:
            leaders = [row for row in roster if row["role"] == "leader"]
            if len(leaders) != 1:
                raise ValueError("团队必须配置唯一的执行负责人")
            member = leaders[0]
        if member.get("role") not in ("leader", "member", "teammate", "executor") or member.get("status") in ("disabled", "archived"):
            raise ValueError("所选成员没有执行资格或已停用")
        assistant_id = member.get("assistant_id") or ""
    assistant, assistant_tools = _assistant(db, project_id, assistant_id)
    return {"binding_id": binding_id, "project_id": project_id, "kind": grant["kind"],
            "assistant_id": assistant_id, "team_id": team["id"] if team else "", "member_id": member["id"] if member else "",
            "member": member, "assistant": assistant, "team": team,
            "tools": _intersect(grant["tools"], assistant_tools), "revision": grant["revision"],
            "requested_member_id": requested_member_id, "member_selected": bool(requested_member_id)}


def list_bindings(db, project_id):
    _project(db, project_id)
    result = []
    rows = _conn(db).execute("SELECT * FROM project_executor_bindings WHERE project_id=? ORDER BY created_at,id", (project_id,)).fetchall()
    import shared_executors
    rows=[dict(row) for row in rows if row['kind']!='team' or not shared_executors.is_instance(db,row['target_id'])]
    known={(row['kind'],row['target_id']) for row in rows}
    candidates=[('assistant',a['id']) for a in db.assistants() if a.get('enabled')]+[('team',t['id']) for t in shared_executors.templates(db)]
    rows.extend(shared_executors.virtual(db,project_id,kind,target) for kind,target in candidates if (kind,target) not in known)
    from team_configuration import PROFILES
    project_names={row['id']:row['title'] for row in _conn(db).execute('SELECT id,title FROM research_tasks')}
    for row in rows:
        binding = _public_binding(db, row)
        binding['selection_key']=binding['kind']+':'+binding['target_id']
        try:
            if binding['kind']=='team':
                team=_record(db,'teams',binding['target_id'])
                if team:
                    binding['owner_project_id']=team.get('project_id','')
                    binding['owner_project_title']=project_names.get(team.get('project_id',''),'')
                    if team['kind']=='fixed' and team['name'] in PROFILES:
                        binding['selection_key']='team-preset:'+PROFILES[team['name']]
                if not binding['enabled'] or not team or team['status'] in ('disabled','archived','dissolved'): raise ValueError('团队已停用')
                leaders=[member for member in db.team_members(team['id']) if member['role']=='leader' and member['status'] not in ('disabled','archived')]
                if len(leaders)!=1: raise ValueError('团队必须有唯一执行负责人')
                _assistant(db,project_id,leaders[0]['assistant_id'])
            else:
                resolve(db, project_id, binding["id"])
            binding.update(available=True, disabled_reason="")
        except ValueError as exc:
            binding.update(available=False, disabled_reason=str(exc))
        result.append(binding)
    return result


def settings(db, project_id):
    _project(db, project_id)
    row = _conn(db).execute("SELECT * FROM project_execution_settings WHERE project_id=?", (project_id,)).fetchone()
    return dict(row) if row else {"project_id": project_id, "autonomy_mode": "supervised", "revision": 0}


def save_settings(db, project_id, mode):
    _project(db, project_id)
    if mode not in MODES:
        raise ValueError("执行模式必须是 supervised 或 full_access")
    conn = _conn(db)
    current = settings(db, project_id)
    with conn:
        if current["revision"] == 0:
            conn.execute("INSERT INTO project_execution_settings VALUES (?,?,1,?)", (project_id, mode, _now()))
        elif current["autonomy_mode"] != mode:
            conn.execute("UPDATE project_execution_settings SET autonomy_mode=?,revision=revision+1,updated_at=? WHERE project_id=?", (mode, _now(), project_id))
    return settings(db, project_id)


def _template(db, template_id, project_id=''):
    if not _table(db, "workflow_layouts"):
        raise ValueError("工作流模板尚未保存")
    row = _conn(db).execute("SELECT * FROM workflow_layouts WHERE id=?", (template_id,)).fetchone()
    if not row:
        raise ValueError("工作流模板不存在")
    template={**dict(row),**json.loads(row['definition'])}
    if project_id:
        import project_workflows
        return project_workflows.effective(db,project_id,template)
    return template


def role_id(node):
    return str(node.get("executor_role_id") or node.get("executor_role") or node.get("role_id") or node.get("id") or "")


def _declared_roles(template):
    return dict.fromkeys(role_id(node) for node in template.get("nodes", []))


def _role(db, project_id, template_id, identifier):
    row = _conn(db).execute("SELECT * FROM project_workflow_roles WHERE project_id=? AND template_id=? AND role_id=?",
                           (project_id, template_id, identifier)).fetchone()
    if not row:
        raise ValueError("该工作流角色尚未绑定项目执行者")
    result = dict(row)
    result["executor_binding_id"] = result["binding_id"]
    return result


def _save_role(db, project_id, template_id, identifier, binding_id, member_id, source):
    _binding(db, project_id, binding_id)
    conn = _conn(db)
    old = conn.execute("SELECT * FROM project_workflow_roles WHERE project_id=? AND template_id=? AND role_id=?",
                       (project_id, template_id, identifier)).fetchone()
    with conn:
        if old:
            permitted = source != "legacy" or old["source"] == "legacy"
            changed = old["binding_id"] != binding_id or old["member_id"] != member_id or (source != "legacy" and old["source"] != source)
            if permitted and changed:
                conn.execute("UPDATE project_workflow_roles SET binding_id=?,member_id=?,revision=revision+1,source=?,updated_at=? WHERE project_id=? AND template_id=? AND role_id=?",
                             (binding_id, member_id, source, _now(), project_id, template_id, identifier))
        else:
            conn.execute("INSERT INTO project_workflow_roles VALUES (?,?,?,?,?,1,?,?)",
                         (project_id, template_id, identifier, binding_id, member_id, source, _now()))
    return _role(db, project_id, template_id, identifier)


def bind_role(db, project_id, template_id, role_id, binding_id, *, member_id=""):
    _project(db, project_id)
    if not isinstance(role_id, str) or not _ROLE.fullmatch(role_id):
        raise ValueError("执行角色 ID 无效")
    template = _template(db, template_id, project_id)
    declared = _declared_roles(template)
    if role_id not in declared:
        raise ValueError("该执行角色不属于工作流模板")
    resolve(db, project_id, binding_id, member_id=member_id)
    return _save_role(db, project_id, template_id, role_id, binding_id, str(member_id or ""), "user")


def resolve_role(db, project_id, template_id, role_id, *, materialize=True):
    _project(db, project_id)
    if role_id not in _declared_roles(_template(db, template_id, project_id)):
        raise ValueError("该执行角色已从工作流模板移除")
    role = _role(db, project_id, template_id, role_id)
    executor = resolve(db, project_id, role["binding_id"], member_id=role["member_id"], materialize=materialize)
    return {**executor, "role_id": role_id, "template_id": template_id, "role_revision": role["revision"]}


def list_roles(db, project_id, template_id):
    _project(db, project_id)
    template = _template(db, template_id, project_id)
    result = []
    for identifier in _declared_roles(template):
        row = _conn(db).execute("SELECT * FROM project_workflow_roles WHERE project_id=? AND template_id=? AND role_id=?", (project_id, template_id, identifier)).fetchone()
        role = _role(db, project_id, template_id, identifier) if row else {"project_id": project_id, "template_id": template_id, "role_id": identifier, "binding_id": "", "executor_binding_id": "", "member_id": "", "revision": 0}
        try:
            executor = resolve_role(db, project_id, template_id, identifier, materialize=False)
            role.update(available=True, disabled_reason="", executor=executor)
        except ValueError as exc:
            role.update(available=False, disabled_reason=str(exc), executor=None)
        result.append(role)
    return result


def import_legacy(db, project_id):
    """Import configured identities only. Never seed teams or revive denials."""
    _project(db, project_id)
    conn = _conn(db)

    def grant(kind, target_id, source, source_id):
        if not target_id:
            return None
        try:
            binding = register(db, project_id, kind, target_id, source="legacy")
        except ValueError:
            return None
        with conn:
            _remember_origin(db, binding["id"], source, source_id)
        return binding

    if _table(db, "teams"):
        for row in conn.execute("SELECT id FROM teams WHERE project_id=? ORDER BY created_at,id", (project_id,)).fetchall():
            tid = row["id"]
            grant("team", tid, "team", tid)
            for member in conn.execute("SELECT id,assistant_id FROM team_members WHERE task_id=?", (tid,)).fetchall():
                grant("assistant", member["assistant_id"], "team_member", member["id"])
    if _table(db, "team_members"):
        for member in conn.execute("SELECT id,assistant_id FROM team_members WHERE task_id=?", (project_id,)).fetchall():
            grant("assistant", member["assistant_id"], "legacy_project_member", member["id"])
    if _table(db, "project_todos"):
        for todo in conn.execute("SELECT id,assigned_assistant FROM project_todos WHERE task_id=? AND assigned_assistant!=''", (project_id,)).fetchall():
            aid = todo["assigned_assistant"]
            if not _record(db, "assistants", aid):
                matched = conn.execute("SELECT id FROM assistants WHERE name=?", (aid,)).fetchall()
                aid = matched[0]["id"] if len(matched) == 1 else ""
            grant("assistant", aid, "todo", todo["id"])
    if _table(db, "chats"):
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(chats)")}
        if {"task_id", "assistant_id"} <= columns:
            for chat in conn.execute("SELECT id,assistant_id FROM chats WHERE task_id=? AND assistant_id!=''", (project_id,)).fetchall():
                grant("assistant", chat["assistant_id"], "chat", chat["id"])
    if not (_table(db, "project_workflow_layouts") and _table(db, "workflow_layouts")):
        return
    saved = conn.execute("SELECT layout_id FROM project_workflow_layouts WHERE task_id=?", (project_id,)).fetchone()
    if not saved:
        return
    template_id = saved["layout_id"]
    try:
        nodes = _template(db, template_id).get("nodes", [])
    except (ValueError, TypeError):
        return
    for node in nodes:
        if not isinstance(node, dict):
            continue
        try:
            import workflow_layout
            effective = workflow_layout.resolve_node(db, project_id, template_id, node, materialize=False)
        except ValueError:
            continue
        binding = grant("team", effective.get("team_id"), "workflow_node", template_id + ":" + str(node.get("id", ""))) if effective.get("team_id") else grant("assistant", effective.get("assistant_id"), "workflow_node", template_id + ":" + str(node.get("id", "")))
        if binding:
            _save_role(db, project_id, template_id, role_id(node), binding["id"], str(effective.get("member_id") or ""), "legacy")
