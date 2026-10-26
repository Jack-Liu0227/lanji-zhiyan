"""Persisted model registry, assistant policies and editable team configuration."""
from __future__ import annotations

import json
import securestore              # 凭据加密（DPAPI），model_profiles 里也只存密文
import uuid
import account_providers
from urllib.parse import urlsplit

REASONING = ("auto", "none", "minimal", "low", "medium", "high", "xhigh")
PERMISSIONS = ("auto", "read_only", "workspace_write", "full_access")
READ_TOOLS = frozenset(("library_overview", "knowledge_query", "calendar_list", "outline_read", "map_read", "map_sources", "doc_read", "cite_read", "read_attachment", "skill_read", "mail_accounts", "mail_list", "mail_read", "zotero_collections", "project_note_list", "project_note_read"))
READ_TOOLS = READ_TOOLS | {"prompt_template_search", "prompt_template_read", "prompt_template_render", "workflow_context_read"}


def migrate(db):
    for table, fields in {
        "assistants": {"reasoning": "TEXT NOT NULL DEFAULT 'auto'", "permission": "TEXT NOT NULL DEFAULT 'auto'"},
        "team_members": {"model_id": "TEXT NOT NULL DEFAULT ''", "reasoning": "TEXT NOT NULL DEFAULT 'auto'", "permission": "TEXT NOT NULL DEFAULT 'auto'"},
        "model_profiles": {"api": "TEXT NOT NULL DEFAULT 'openai-completions'", "provider_id": "TEXT NOT NULL DEFAULT ''"},
        "teams": {"description": "TEXT NOT NULL DEFAULT ''", "roster_initialized": "INTEGER NOT NULL DEFAULT 0"},
        "chats": {"model_id": "TEXT NOT NULL DEFAULT ''", "reasoning": "TEXT NOT NULL DEFAULT 'auto'", "permission": "TEXT NOT NULL DEFAULT 'auto'"},
    }.items():
        cols = {r["name"] for r in db.db.execute("PRAGMA table_info(%s)" % table)}
        for name, declaration in fields.items():
            if name not in cols:
                db.db.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, declaration))
                if table == "teams" and name == "roster_initialized":
                    db.db.execute("UPDATE teams SET roster_initialized=1")
    db.db.execute("""CREATE TABLE IF NOT EXISTS team_sessions (
      id TEXT PRIMARY KEY, team_id TEXT NOT NULL, title TEXT NOT NULL,
      active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    # 别加 teams → chats 的迁移：每次开库都跑，会把固定团队反复塞回对话列表；团队走独立的 team_sessions。
    for p in db.providers():
        account = account_providers.CATALOG.get(p["id"])
        if account and p["source"] == "builtin" and p["api"] not in account_providers.WIRES:
            db.db.execute("UPDATE providers SET api=?,base_url=?,api_key='',key_host='' WHERE id=?",
                          (account["wire"], "account://" + p["id"], p["id"]))
            db.db.commit()
            sync_provider_models(db, p["id"])
        adopted = {r["model"] for r in db.db.execute("SELECT model FROM model_profiles WHERE provider_id=?", (p["id"],))}
        if adopted != set(p["models"]):
            sync_provider_models(db, p["id"])


def validate_runtime_fields(db, fields):
    if "reasoning" in fields and fields["reasoning"] not in REASONING:
        raise ValueError("不支持的推理强度")
    if "permission" in fields and fields["permission"] not in PERMISSIONS:
        raise ValueError("不支持的权限模式")
    if fields.get("model_id"):
        model = db.model(fields["model_id"])
        if model.get("api", "openai-completions") not in __import__("workflow").USABLE_WIRES:
            raise ValueError("该模型的协议尚不支持运行")


def public(row):
    return {**{k: v for k, v in row.items() if k != "api_key"},
            "key_present": bool(row.get("api_key")),
            "key_invalid": bool(row.get("key_invalid")),
            "key_error": row.get("key_error") or ""}


def sync_provider_models(db, pid):
    """Every adopted model is selectable; credentials remain linked to its provider."""
    import workflow
    p = db.provider(pid)
    host = p.get("key_host") or workflow._host_of(p["base_url"]) if p.get("api_key") else ""
    keep = []
    for model in p["models"]:
        old = db.db.execute("SELECT id FROM model_profiles WHERE provider_id=? AND model=?", (pid, model)).fetchone()
        if not old:
            old = db.db.execute("SELECT id FROM model_profiles WHERE provider_id='' AND base_url=? AND model=?", (p["base_url"], model)).fetchone()
        mid = old["id"] if old else uuid.uuid4().hex
        keep.append(mid)
        if not old:
            db.db.execute("INSERT INTO model_profiles (id,name,base_url,api_key,model,is_active,created_at,api,provider_id,key_host) VALUES (?,?,?,?,?,0,?,?,?,?)",
                          (mid, p["name"] + " / " + model, p["base_url"], securestore.protect(p["api_key"]), model, workflow.now(), p["api"], pid, host))
        else:
            db.db.execute("UPDATE model_profiles SET name=?,base_url=?,api_key=?,api=?,provider_id=?,key_host=? WHERE id=?",
                          (p["name"] + " / " + model, p["base_url"], securestore.protect(p["api_key"]), p["api"], pid, host, mid))
    for r in db.db.execute("SELECT id FROM model_profiles WHERE provider_id=?", (pid,)).fetchall():
        if r["id"] not in keep:
            db.db.execute("DELETE FROM model_profiles WHERE id=?", (r["id"],))
    db.db.commit()


def session(db, team_id, sid="", create=False, title=""):
    import workflow
    if sid:
        row = db.db.execute("SELECT * FROM team_sessions WHERE id=? AND team_id=?", (sid, team_id)).fetchone()
        if not row:
            raise ValueError("会话不属于此团队")
    else:
        row = db.db.execute("SELECT * FROM team_sessions WHERE team_id=? AND active=1 ORDER BY updated_at DESC LIMIT 1", (team_id,)).fetchone()
    if row and not create:
        return dict(row)
    sid = uuid.uuid4().hex
    db.db.execute("UPDATE team_sessions SET active=0 WHERE team_id=?", (team_id,))
    db.db.execute("INSERT INTO team_sessions VALUES (?,?,?,?,?,?)", (sid, team_id, title.strip() or "新会话", 1, workflow.now(), workflow.now()))
    # Existing transcripts remain available as a named legacy session.
    if not row:
        db.db.execute("UPDATE chat SET scope=? WHERE task_id=? AND scope='team'", ("team:" + sid, team_id))
    db.db.commit()
    return dict(db.db.execute("SELECT * FROM team_sessions WHERE id=?", (sid,)).fetchone())


def set_leader(db, team_id, member_id):
    m = db.team_member(member_id)
    if not m or m["task_id"] != team_id:
        raise ValueError("Leader 必须属于当前团队")
    if m.get("assistant_id") == "builtin-steward":
        raise ValueError("智研管家负责项目与团队配置，请选择专业负责人担任 Leader")
    if m.get("assistant_id"):
        db.require_assistant(m["assistant_id"])
    with db.db:
        db.db.execute("UPDATE team_members SET role='member' WHERE task_id=?", (team_id,))
        db.db.execute("UPDATE team_members SET role='leader' WHERE id=?", (member_id,))
    return db.team_member(member_id)


def runtime_config(db, task_id, assistant_id="", member=None):
    aid = assistant_id or db.assistant_of_chat(task_id)
    if not aid or aid == "builtin-steward":
        steward = db.assistant("builtin-steward") or {}
        cfg = {"model_id": steward.get("model_id", ""), "reasoning": steward.get("reasoning", "auto"), "permission": steward.get("permission", "auto"), "assistant_id": aid or "builtin-steward"}
    else:
        a = db.require_assistant(aid)
        cfg = {"model_id": a.get("model_id", ""), "reasoning": a.get("reasoning", "auto"), "permission": a.get("permission", "auto"), "assistant_id": aid}
    if member is None:
        row = db.db.execute("SELECT model_id,reasoning,permission FROM chats WHERE id=?", (task_id,)).fetchone()
        if row:
            for key in ("model_id", "reasoning", "permission"):
                if row[key] and row[key] != "auto":
                    cfg[key] = row[key]
    for key in ("model_id", "reasoning", "permission"):
        val = (member or {}).get(key)
        if val and val != "auto":
            cfg[key] = val
    return cfg


def route(method, path, body, query, db):
    if not path.startswith("/api/agent-settings/"):
        return None
    import workflow
    import team
    b = body or {}
    def q(key, default=""):
        value = (query or {}).get(key, default)
        return value[0] if isinstance(value, list) and value else value
    action = path.rsplit("/", 1)[-1]
    if method == "GET" and action == "state":
        return {"providers": [public(p) for p in db.providers()], "models": [public(m) for m in db.models()],
                "catalog": [{"id": k, "name": c["name"], "base_url": c.get("base_url", ""), "api": c.get("wire", "openai-completions"), "supported": c.get("wire") in workflow.USABLE_WIRES,
                             "auth": c.get("auth", ["apiKey"]), "docs": c.get("docs", "")} for k, c in workflow.PROVIDER_CATALOG.items()],
                "wires": [{"id": w, "label": workflow.WIRE_LABEL.get(w, w)} for w in workflow.CUSTOM_WIRES],
                "assistants": db.assistants(), "selectable": db.selectable_assistants(), "reasoning": REASONING, "permissions": PERMISSIONS}
    if method == "GET" and action == "provider-secret":
        return {"api_key": db.provider(q("id"))["api_key"]}
    if method == "POST" and action in ("provider-account-status", "provider-account-login"):
        p = db.provider(str(b.get("id") or ""))
        return account_providers.status(p["api"]) if action.endswith("status") else account_providers.login(p["api"])
    if method == "POST" and action == "provider-save":
        pid = str(b.get("id") or "")
        name, url = str(b.get("name") or "").strip(), str(b.get("base_url") or "").strip().rstrip("/")
        wire = b.get("api", "openai-completions")
        if wire in account_providers.WIRES:
            account = account_providers.entry(wire)
            if pid and pid != account["id"]:
                raise ValueError("账号 Provider 需使用对应的官方入口")
            pid, url = account["id"], "account://" + account["id"]
            if b.get("api_key"):
                raise ValueError("账号认证由官方客户端管理，无需填写 API Key")
        if not name or (wire not in account_providers.WIRES and (urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc)):
            raise ValueError("请输入名称与有效的 HTTP(S) 接口地址")
        if wire not in workflow.CUSTOM_WIRES:
            raise ValueError("请选择已支持的协议")
        if pid:
            db.ensure_provider(pid)
            fields = {"name": name, "base_url": url, "api": wire, "enabled": int(bool(b.get("enabled", True)))}
            if b.get("api_key") is not None:
                fields["api_key"] = str(b["api_key"]).strip()
            if wire in account_providers.WIRES:
                fields.update(api_key="", key_host="")
            db.db.execute("UPDATE providers SET %s WHERE id=?" % ",".join(k + "=?" for k in fields), (*fields.values(), pid))
            db.db.commit()
        else:
            pid = db.add_provider(name, url, wire, str(b.get("api_key") or ""))["id"]
            db.db.execute("UPDATE providers SET enabled=1 WHERE id=?", (pid,))
            db.db.commit()
        sync_provider_models(db, pid)
        return {"provider": public(db.provider(pid))}
    if method == "POST" and action == "provider-discover":
        p = db.provider(b["id"])
        from agent_protocols import discover
        return {"candidates": discover(p)}
    if method == "POST" and action == "provider-models":
        p = db.provider(b["id"])
        models = list(dict.fromkeys(str(x).strip() for x in b.get("models", []) if str(x).strip()))
        db.db.execute("UPDATE providers SET models=? WHERE id=?", (json.dumps(models), p["id"]))
        db.db.commit()
        sync_provider_models(db, p["id"])
        if models and (b.get("auto") or not db.active_model()):
            db.set_default(p["id"], models[0])
        return {"provider": public(db.provider(p["id"])), "models": [public(m) for m in db.models()]}
    if method == "POST" and action == "provider-delete":
        db.delete_provider(b["id"])
        return {"ok": True}
    if method == "POST" and action == "assistant-save":
        aid = b.get("id") or "a-" + uuid.uuid4().hex
        return {"assistant": db.assistant_save(aid, **{k: v for k, v in b.items() if k != "id"})}
    if method == "POST" and action == "runtime-save":
        db.chat(b["id"])
        fields = {k: b[k] for k in ("model_id", "reasoning", "permission") if k in b}
        validate_runtime_fields(db, fields)
        if fields:
            db.db.execute("UPDATE chats SET %s WHERE id=?" % ",".join(k + "=?" for k in fields), (*fields.values(), b["id"]))
            db.db.commit()
        return {"chat": db.chat(b["id"])}
    if method == "GET" and action == "assistant-history":
        aid = q("id")
        return {"sessions": [c for c in db.chats() if c.get("assistant_id") == aid]}
    if method == "GET" and action == "team-detail":
        tid = q("id")
        if not db.team(tid):
            raise ValueError("团队不存在")
        team.ensure_leader(tid, db)
        sid = q("session_id")
        selected = session(db, tid, sid)
        return {"team": db.team(tid), "members": db.team_members(tid), "session": selected,
                "sessions": [dict(r) for r in db.db.execute("SELECT * FROM team_sessions WHERE team_id=? ORDER BY updated_at DESC", (tid,))],
                "messages": db.messages(tid, "team:" + selected["id"]), "running": team.TEAM_RUNS.get(tid)}
    if method == "POST" and action == "team-save":
        tid = b.get("id")
        if not str(b.get("name") or "").strip():
            raise ValueError("团队名称不能为空")
        t = db.team_update(tid, name=b["name"]) if tid else db.team_add(b["name"], "custom", project_id=str(b.get("project") or ""))
        if not t:
            raise ValueError("团队不存在")
        db.db.execute("UPDATE teams SET description=? WHERE id=?", (str(b.get("description") or ""), t["id"]))
        db.db.commit()
        team.ensure_leader(t["id"], db)
        return {"team": db.team(t["id"])}
    if method == "POST" and action == "team-delete":
        tid = b["id"]
        if tid in team.TEAM_RUNS:
            raise ValueError("请先停止团队运行")
        t = db.team(tid)
        if not t or t["kind"] != "custom":
            raise ValueError("仅自定义持久团队可删除；预设团队可以编辑")
        # Preserve its title and transcript as an archived team, keeping sessions selectable.
        db.team_update(tid, status="archived")
        return {"ok": True}
    if method == "POST" and action in ("member-save", "leader", "team-session", "team-say"):
        tid = b.get("team_id") or b.get("id")
        if not db.team(tid):
            raise ValueError("团队不存在")
        if tid in team.TEAM_RUNS and action != "team-say":
            raise ValueError("请先停止当前团队运行，再编辑配置或切换会话")
        if action == "leader":
            return {"member": set_leader(db, tid, b["member_id"])}
        if action == "member-save":
            fields = {k: v for k, v in b.items() if k in ("name", "prompt", "assistant_id", "model_id", "reasoning", "permission")}
            validate_runtime_fields(db, fields)
            if "name" in fields:
                fields["name"] = str(fields["name"] or "").strip()
                if not fields["name"]:
                    raise ValueError("角色名称不能为空")
            mid = b.get("member_id")
            if mid:
                m = db.team_member(mid)
                if not m or m["task_id"] != tid:
                    raise ValueError("成员不属于此团队")
                if m["role"] == "leader" and fields.get("assistant_id") == "builtin-steward":
                    raise ValueError("智研管家负责项目与团队配置，请选择专业负责人担任 Leader")
                return {"member": db.team_member_update(mid, **fields)}
            aid = fields.get("assistant_id", "")
            a = db.require_assistant(aid) if aid else {}
            if not aid and not str(fields.get("name") or "").strip():
                raise ValueError("自定义角色需要填写名称")
            if aid and any(m["assistant_id"] == aid for m in db.team_members(tid)):
                raise ValueError("这个助手已在当前团队中")
            m = db.team_member_add(tid, fields.get("name") or a.get("name", "成员"), "member",
                                   prompt=fields.get("prompt", a.get("rules", "")), assistant_id=aid,
                                   color=len(db.team_members(tid)),
                                   **{k: fields[k] for k in ("model_id", "reasoning", "permission") if k in fields})
            return {"member": m}
        if action == "team-session":
            selected = session(db, tid, b.get("session_id", ""), create=bool(b.get("create")), title=str(b.get("title") or ""))
            db.db.execute("UPDATE team_sessions SET active=(id=?) WHERE team_id=?", (selected["id"], tid))
            db.db.commit()
            return {"session": selected}
        if action == "team-say":
            session(db, tid)
            return team.say(tid, str(b.get("text") or ""))
    return None
