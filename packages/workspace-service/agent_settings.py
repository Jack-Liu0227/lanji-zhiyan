"""Persisted model registry, assistant policies and editable team configuration."""
from __future__ import annotations

import json
import hashlib
import uuid
import account_providers
from urllib.parse import urlsplit

REASONING = ("auto", "none", "minimal", "low", "medium", "high", "xhigh")
PERMISSIONS = ("auto", "read_only", "workspace_write", "full_access")
MODEL_POLICIES = ("fallback", "strict")
READ_TOOLS = frozenset(("library_overview", "knowledge_query", "calendar_list", "outline_read", "map_read", "map_sources", "doc_read", "cite_read", "read_attachment", "skill_read", "mail_accounts", "mail_list", "mail_read", "zotero_collections", "zotero_staged_list", "zotero_staged_import", "project_note_list", "project_note_read"))
READ_TOOLS = READ_TOOLS | {"prompt_template_search", "prompt_template_read", "prompt_template_render", "workflow_context_read", "graph_workflow_status", "workflow_templates", "workflow_status", "task_status"}
READ_TOOLS = READ_TOOLS | {"paper_excerpt", "research_data_profile", "project_resources"}
READ_TOOLS = READ_TOOLS | {"paper_abstracts", "paper_focus", "paper_evidence_get", "rss_recent"}


def _normalize_fallback_ids(value):
    if value is None or value == "":
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                value = parsed
            else:
                value = [stripped]
        except ValueError:
            value = [part.strip() for part in stripped.split(",")]
    if not isinstance(value, (list, tuple)):
        raise ValueError("备用模型列表格式无效")
    return list(dict.fromkeys(str(item).strip() for item in value if str(item or "").strip()))


def migrate(db):
    import model_health
    for table, fields in {
        "assistants": {"reasoning": "TEXT NOT NULL DEFAULT 'auto'", "permission": "TEXT NOT NULL DEFAULT 'auto'",
                       "fallback_model_ids": "TEXT NOT NULL DEFAULT '[]'"},
        "team_members": {"model_id": "TEXT NOT NULL DEFAULT ''", "reasoning": "TEXT NOT NULL DEFAULT 'auto'",
                         "permission": "TEXT NOT NULL DEFAULT 'auto'", "fallback_model_ids": "TEXT NOT NULL DEFAULT '[]'"},
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
    model_health.ensure_schema(db)
    # 别加 teams → chats 的迁移：每次开库都跑，会把固定团队反复塞回对话列表；团队走独立的 team_sessions。
    for p in db.providers():
        account = account_providers.CATALOG.get(p["id"])
        if account and p["source"] == "builtin" and p["api"] not in account_providers.WIRES:
            db.db.execute("UPDATE providers SET api=?,base_url=?,api_key='',key_host='' WHERE id=?",
                          (account["wire"], "account://" + p["id"], p["id"]))
            db.db.commit()
            sync_provider_models(db, p["id"])
        adopted = {r["model"] for r in db.db.execute("SELECT model FROM model_profiles WHERE provider_id=? AND retired=0", (p["id"],))}
        if adopted != set(p["models"]):
            sync_provider_models(db, p["id"])


def validate_runtime_fields(db, fields):
    if "reasoning" in fields and fields["reasoning"] not in REASONING:
        raise ValueError("不支持的推理强度")
    if "permission" in fields and fields["permission"] not in PERMISSIONS:
        raise ValueError("不支持的权限模式")
    if "model_policy" in fields and fields["model_policy"] not in ("", *MODEL_POLICIES):
        raise ValueError("不支持的模型容错策略")
    if fields.get("model_id"):
        try:
            model = db.model(fields["model_id"])
        except KeyError as exc:
            raise ValueError("所选模型不存在或已移除：" + str(fields["model_id"])) from exc
        if model.get("api", "openai-completions") not in __import__("workflow").USABLE_WIRES:
            raise ValueError("该模型的协议尚不支持运行")
    if "fallback_model_ids" in fields and fields["fallback_model_ids"] is not None:
        fallbacks = _normalize_fallback_ids(fields["fallback_model_ids"])
        if len(fallbacks) > 20:
            raise ValueError("备用模型数量不能超过 20 个")
        usable = __import__("workflow").USABLE_WIRES
        for mid in fallbacks:
            try:
                model = db.model(mid)
            except KeyError as exc:
                raise ValueError("备用模型不存在或已移除：" + mid) from exc
            if model.get("api", "openai-completions") not in usable:
                raise ValueError("备用模型的协议尚不支持运行：" + mid)


def public(row):
    return {**{k: v for k, v in row.items() if k != "api_key"},
            "key_present": bool(row.get("api_key")),
            "key_invalid": bool(row.get("key_invalid")),
            "key_error": row.get("key_error") or ""}


def sync_provider_models(db, pid, *, commit=True):
    """Every adopted model is selectable; credentials remain linked to its provider."""
    import workflow
    p = db.provider(pid)
    host = p.get("key_host") or workflow._host_of(p["base_url"]) if p.get("api_key") else ""
    stored = db.db.execute("SELECT api_key FROM providers WHERE id=?", (pid,)).fetchone()[0]
    keep = []
    for model in p["models"]:
        old = db.db.execute("SELECT id FROM model_profiles WHERE provider_id=? AND model=?", (pid, model)).fetchone()
        if not old:
            old = db.db.execute("SELECT id FROM model_profiles WHERE provider_id='' AND retired=0 AND base_url=? AND model=?", (p["base_url"], model)).fetchone()
        mid = old["id"] if old else uuid.uuid4().hex
        keep.append(mid)
        if not old:
            db.db.execute("INSERT INTO model_profiles (id,name,base_url,api_key,model,is_active,created_at,api,provider_id,key_host) VALUES (?,?,?,?,?,0,?,?,?,?)",
                          (mid, p["name"] + " / " + model, p["base_url"], stored, model, workflow.now(), p["api"], pid, host))
        else:
            db.db.execute("UPDATE model_profiles SET name=?,base_url=?,api_key=?,api=?,provider_id=?,key_host=?,retired=0 WHERE id=?",
                          (p["name"] + " / " + model, p["base_url"], stored, p["api"], pid, host, mid))
    for r in db.db.execute("SELECT id FROM model_profiles WHERE provider_id=? AND retired=0", (pid,)).fetchall():
        if r["id"] not in keep:
            db.db.execute("UPDATE model_profiles SET retired=1,api_key='',key_host='',is_active=0 WHERE id=?", (r["id"],))
    if commit:
        db.db.commit()


def session(db, team_id, sid="", create=False, title=""):
    import workflow
    if sid:
        row = db.db.execute("SELECT * FROM team_sessions WHERE id=? AND team_id=?", (sid, team_id)).fetchone()
        if not row:
            raise ValueError("会话不属于此团队")
        flags=db.db.execute("SELECT * FROM conversation_flags WHERE kind='team' AND id=?",(sid,)).fetchone()
        if flags and (flags['deleted_at'] or flags['archived_at']):raise ValueError('请先恢复或取消归档此会话')
    else:
        row = db.db.execute("SELECT * FROM team_sessions s WHERE team_id=? AND active=1 AND NOT EXISTS(SELECT 1 FROM conversation_flags f WHERE f.kind='team' AND f.id=s.id AND (f.deleted_at<>'' OR f.archived_at<>'')) ORDER BY updated_at DESC LIMIT 1", (team_id,)).fetchone()
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


def runtime_config(db, task_id, assistant_id="", member=None, node=None, task=None):
    aid = assistant_id or db.assistant_of_chat(task_id)
    if not aid or aid == "builtin-steward":
        steward = db.assistant("builtin-steward") or {}
        cfg = {"model_id": steward.get("model_id", ""), "reasoning": steward.get("reasoning", "auto"),
               "permission": steward.get("permission", "auto"), "assistant_id": aid or "builtin-steward",
               "fallback_model_ids": _normalize_fallback_ids(steward.get("fallback_model_ids"))}
    else:
        a = db.require_assistant(aid)
        cfg = {"model_id": a.get("model_id", ""), "reasoning": a.get("reasoning", "auto"),
               "permission": a.get("permission", "auto"), "assistant_id": aid,
               "fallback_model_ids": _normalize_fallback_ids(a.get("fallback_model_ids"))}
    cfg["model_source"] = "assistant" if cfg["model_id"] else "default"
    cfg["model_policy"] = "fallback"
    if member is None and node is None:
        row = db.db.execute("SELECT model_id,reasoning,permission FROM chats WHERE id=?", (task_id,)).fetchone()
        if row:
            for key in ("model_id", "reasoning", "permission"):
                if row[key] and row[key] != "auto":
                    cfg[key] = row[key]
                    if key == "model_id":
                        cfg["model_source"] = "conversation"
    for key in ("model_id", "reasoning", "permission"):
        val = (member or {}).get(key)
        if val and val != "auto":
            cfg[key] = val
            if key == "model_id":
                cfg["model_source"] = "team_member"
    if member and member.get("fallback_model_ids"):
        cfg["fallback_model_ids"] = list(dict.fromkeys([
            *_normalize_fallback_ids(member.get("fallback_model_ids")),
            *cfg["fallback_model_ids"],
        ]))
    if node:
        if node.get("model_id"):
            cfg["model_id"] = str(node["model_id"]).strip()
            cfg["model_source"] = "workflow_node"
        if node.get("reasoning") and node["reasoning"] != "auto":
            cfg["reasoning"] = node["reasoning"]
        if node.get("fallback_model_ids"):
            cfg["fallback_model_ids"] = list(dict.fromkeys([
                *_normalize_fallback_ids(node.get("fallback_model_ids")),
                *cfg["fallback_model_ids"],
            ]))
        if node.get("model_policy") in MODEL_POLICIES:
            cfg["model_policy"] = node["model_policy"]
    task_override = ((task or {}).get("metadata") or {}).get("_model_override") or {}
    if isinstance(task_override, dict):
        if task_override.get("model_id"):
            cfg["model_id"] = str(task_override["model_id"]).strip()
            cfg["model_source"] = "task_override"
        if task_override.get("reasoning") and task_override["reasoning"] != "auto":
            cfg["reasoning"] = task_override["reasoning"]
        if task_override.get("model_policy") in MODEL_POLICIES:
            cfg["model_policy"] = task_override["model_policy"]
    return cfg


def model_identity(profile):
    if not profile:
        return None
    return {"id": profile["id"], "model": profile["model"], "api": profile.get("api", "openai-completions"),
            "endpoint_hash": hashlib.sha256(profile["base_url"].encode()).hexdigest(),
            "key_host": profile.get("key_host", ""), "revision": profile["runtime_revision"]}


def freeze_model(db, config):
    profile = None
    if config.get("model_id"):
        try:
            profile = db.model(config["model_id"])
        except (KeyError, ValueError):
            if config.get("model_policy") == "strict":
                raise
            chain = resolve_model_chain(db, config)
            profile = chain[0] if chain else None
            if not profile:
                raise
    else:
        profile = db.active_model()
        if not profile and config.get("fallback_model_ids"):
            chain = resolve_model_chain(db, config)
            profile = chain[0] if chain else None
    return {**config, "model_id": profile["id"] if profile else "", "model_identity": model_identity(profile)}


def _same_endpoint_identity(expected, actual):
    if not expected or not actual:
        return False
    return all(expected.get(k) == actual.get(k) for k in ("id", "model", "api", "endpoint_hash", "key_host"))


def load_frozen_model(db, config, *, allow_credential_refresh=False):
    try:
        profile = db.model(config["model_id"]) if config.get("model_id") else None
    except KeyError as exc:
        raise ValueError("原运行使用的模型已移除，请重新选择模型后启动新运行") from exc
    if "model_identity" in config and config["model_identity"] != model_identity(profile):
        if not (allow_credential_refresh and _same_endpoint_identity(config["model_identity"], model_identity(profile))):
            raise ValueError("本次运行的模型配置或凭据已变更，请核对后重新发送或重新开始任务")
    if not profile and "model_identity" not in config:
        profile = db.active_model()
    return {**profile, "reasoning": config["reasoning"], "permission": config["permission"]} if profile else None


def resolve_model_chain(db, config, *, required_capabilities=(), include_cooldown=True):
    """Resolve primary + fallback runnable model profiles, ordered by health and capabilities."""
    import model_health
    import workflow
    routing = model_health.routing_policy(db)
    strict = (config or {}).get("model_policy") == "strict"
    auto_fallback = routing["auto_fallback"] and not strict

    candidate_ids = []
    if (config or {}).get("model_id"):
        candidate_ids.append(config["model_id"])
    else:
        active = db.active_model()
        if active:
            candidate_ids.append(active["id"])

    if not strict:
        for mid in _normalize_fallback_ids((config or {}).get("fallback_model_ids")):
            if mid not in candidate_ids:
                candidate_ids.append(mid)
        if auto_fallback:
            for mid in routing["fallback_model_ids"]:
                if mid not in candidate_ids:
                    candidate_ids.append(mid)
            active = db.active_model()
            if active and active["id"] not in candidate_ids:
                candidate_ids.append(active["id"])

    health_map = model_health.list_states(db)
    req_caps = set(required_capabilities or ())
    healthy, cooled = [], []
    for mid in candidate_ids:
        try:
            prof = db.model(mid)
        except (KeyError, ValueError):
            continue
        if not prof or not prof.get("base_url") or not prof.get("model"):
            continue
        if prof.get("api", "openai-completions") not in workflow.USABLE_WIRES:
            continue
        if "vision" in req_caps and prof.get("api") in account_providers.WIRES:
            continue
        state = health_map.get(mid) or {}
        caps = state.get("capabilities") or {}
        if any(caps.get(cap) is False for cap in req_caps):
            continue
        enriched = {
            **prof,
            "reasoning": (config or {}).get("reasoning", "auto"),
            "permission": (config or {}).get("permission", "auto"),
        }
        if state.get("in_cooldown"):
            if include_cooldown:
                cooled.append((state.get("cooldown_remaining_seconds", 0), enriched))
        else:
            healthy.append(enriched)
    cooled.sort(key=lambda pair: pair[0])
    return [*healthy, *(item for _, item in cooled)]


def preflight_layout(db, project_id, layout, *, executors=None):
    """Validate model bindings and runtime availability for every runnable workflow node."""
    import model_health
    import project_bindings
    import workflow

    has_any_models = bool(db.db.execute("SELECT 1 FROM model_profiles WHERE retired=0").fetchone())
    health_map = model_health.list_states(db)
    nodes_report = []
    errors = []
    warnings = []

    for node in (layout or {}).get("nodes", []):
        if node.get("node_kind", "executor") != "executor" or node.get("manual_execution"):
            continue
        label = node.get("label") or node.get("id") or "节点"
        executor = (executors or {}).get(node["id"])
        if executor is None:
            if node.get("executor_binding_id"):
                executor = project_bindings.resolve(db, project_id, node["executor_binding_id"], member_id=node.get("member_id", ""), materialize=False)
            elif any(node.get(k) for k in ("assistant_id", "team_id", "member_id")):
                aid = node.get("assistant_id") or ""
                if not aid and node.get("team_id"):
                    roster = db.team_members(node["team_id"])
                    leader = next((m for m in roster if m.get("role") == "leader"), None)
                    aid = (leader or {}).get("assistant_id", "")
                executor = {"assistant_id": aid, "member": None}
            else:
                role = project_bindings.role_id(node)
                try:
                    executor = project_bindings.resolve_role(db, project_id, layout["id"], role, materialize=False)
                except ValueError:
                    executor = {"assistant_id": "", "member": None}

        aid = (executor or {}).get("assistant_id") or ""
        member = (executor or {}).get("member") or None
        cfg = runtime_config(db, project_id, aid, member, node=node)
        explicit_id = str(node.get("model_id") or (member or {}).get("model_id") or (db.assistant(aid) or {}).get("model_id") or "").strip()
        chain = resolve_model_chain(db, cfg)
        primary_health = health_map.get(cfg.get("model_id") or "", {})
        entry = {
            "node_id": node["id"],
            "label": label,
            "model_id": cfg.get("model_id") or "",
            "model_source": cfg.get("model_source", "default"),
            "model_policy": cfg.get("model_policy", "fallback"),
            "candidate_count": len(chain),
            "candidates": [{"id": m["id"], "model": m["model"], "name": m.get("name", m["model"])} for m in chain],
            "primary_health": primary_health.get("status", "unknown"),
            "ok": True,
            "warnings": [],
        }

        if explicit_id:
            explicit_ok = any(m["id"] == explicit_id for m in chain)
            if not explicit_ok:
                if cfg.get("model_policy") == "strict" or not chain:
                    msg = f"节点「{label}」指定的模型不可用或已停用，且无可用备用模型，请重新配置"
                    errors.append(msg)
                    entry["ok"] = False
                    entry["error"] = msg
                else:
                    msg = f"节点「{label}」主模型当前不可用，将自动切换备用模型（{chain[0]['model']}）"
                    warnings.append(msg)
                    entry["warnings"].append(msg)
        elif has_any_models and not chain:
            msg = f"节点「{label}」没有可用的模型（所有已配置的 Provider 均已停用或模型已下线）"
            errors.append(msg)
            entry["ok"] = False
            entry["error"] = msg
        elif not has_any_models and not chain:
            msg = f"节点「{label}」尚未配置可用模型"
            warnings.append(msg)
            entry["warnings"].append(msg)

        if primary_health.get("in_cooldown") and chain and chain[0]["id"] != (cfg.get("model_id") or ""):
            msg = f"节点「{label}」主模型处于冷却期，将优先使用备用模型（{chain[0]['model']}）"
            warnings.append(msg)
            entry["warnings"].append(msg)

        if explicit_id and cfg.get("model_policy") == "strict" and primary_health.get("status") in ("unavailable", "auth_error", "quota_exceeded"):
            msg = f"节点「{label}」主模型状态异常（{primary_health.get('status')}）且已禁用自动切换"
            warnings.append(msg)
            entry["warnings"].append(msg)

        # Validate wire compatibility if active/selected model exists
        if chain and chain[0].get("api", "openai-completions") not in workflow.USABLE_WIRES:
            msg = f"节点「{label}」所选模型协议不支持运行"
            errors.append(msg)
            entry["ok"] = False
            entry["error"] = msg

        nodes_report.append(entry)

    return {"ok": not errors, "errors": errors, "warnings": warnings, "nodes": nodes_report}


def route(method, path, body, query, db):
    import workflow_conversation_context
    conversation_context = workflow_conversation_context.route(method, path, body, query, db)
    if conversation_context is not None:
        return conversation_context
    import workspace_trajectory
    trajectory=workspace_trajectory.route(method,path,body,query,db)
    if trajectory is not None:return trajectory
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
        import model_health
        import model_context
        health_map = model_health.list_states(db)
        models = [{**public(m), "context": model_context.describe(db, m),
                   "health": health_map.get(m["id"], model_health.get(db, m["id"]))} for m in db.models()]
        return {"providers": [public(p) for p in db.providers()], "models": models,
                "health": health_map, "routing": model_health.routing_policy(db),
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
        key = b.get("api_key")
        if key is not None and not isinstance(key, str):
            raise ValueError("API Key 必须为文本")
        configured = pid and db.db.execute("SELECT 1 FROM providers WHERE id=?", (pid,)).fetchone()
        enabled = bool(b["enabled"]) if "enabled" in b else None if configured else True
        provider = db.save_provider(name, url, wire, "" if wire in account_providers.WIRES else key,
                                    pid=pid, enabled=enabled)
        import model_health
        model_health.clear(db, provider_id=provider["id"])
        return {"provider": public(provider)}
    if method == "POST" and action == "provider-discover":
        p = db.provider(b["id"])
        from agent_protocols import discover
        return {"candidates": discover(p)}
    if method == "POST" and action == "model-test":
        import model_checks
        import model_health
        mid = str(b.get("id") or "")
        result = model_checks.generate(db.model(mid), str(b.get("capability") or "text"), db=db)
        return {**result, "health": model_health.get(db, mid)}
    if action == "model-context" and method in ("GET", "POST"):
        import model_context
        profile = db.model(str(b.get("id") or q("id")))
        context = (model_context.save_overrides(db, profile, b) if method == "POST"
                   else model_context.describe(db, profile))
        return {"context": context}
    if method == "POST" and action == "model-batch-test":
        import model_health
        ids = b.get("ids")
        if ids is not None and not isinstance(ids, list):
            raise ValueError("ids 必须是数组")
        return {"results": model_health.batch_check(db, ids, capability=str(b.get("capability") or "text"))}
    if action == "model-routing" and method in ("GET", "POST"):
        import model_health
        if method == "POST":
            return {"routing": model_health.save_routing_policy(db, b)}
        return {"routing": model_health.routing_policy(db)}
    if method == "POST" and action == "model-health-reset":
        import model_health
        model_health.clear(db, profile_id=str(b.get("id") or ""), provider_id=str(b.get("provider_id") or ""))
        return {"ok": True, "health": model_health.list_states(db)}
    if method == "GET" and action == "runtime-effective":
        import workspace_chat
        import model_health
        owner = str(q("id"))
        db.chat(owner)
        config = workspace_chat.snapshot(db, owner, {"executor_binding_id": str(q("executor_binding_id")),
                                                     "member_id": str(q("member_id"))}, materialize=False)
        model = config["model_identity"] or {}
        mid = model.get("id", "")
        health = model_health.get(db, mid) if mid else {}
        return {"model": model.get("model", ""), "model_id": mid, "api": model.get("api", ""), "source": config["model_source"],
                "health_status": health.get("status", "unknown"),
                "reasoning": "auto" if model.get("api") in account_providers.WIRES else config["reasoning"],
                "reasoning_control": model.get("api") not in account_providers.WIRES}
    if action == "translation-model" and method in ("GET", "POST"):
        import papertran
        import service_settings
        if method == "POST":
            identifier = str(b.get("model_id") or "")
            if identifier:
                papertran.model_config(db.model(identifier))
            service_settings.write(db, "pdf_translation_model", {"model_id": identifier})
        return papertran.model_choices(db)
    if action in ("embedding", "embedding-test", "embedding-rebuild"):
        import semantic
        import securestore
        import service_settings
        if method == "POST" and action == "embedding-test":
            try:
                matrix = semantic.embed(["Embedding health check"], semantic.config(db), timeout=10)
                return {"status": "online", "dim": int(matrix.shape[1])}
            except semantic.EmbedError as exc:
                return {"status": "offline", "reason": str(exc)[:200]}
        if method == "POST" and action == "embedding-rebuild":
            return {"started": semantic.warm_async(str(db.path), force=True)}
        if method == "POST" and action == "embedding":
            base, model = str(b.get("base") or "").strip().rstrip("/"), str(b.get("model") or "").strip()
            address = urlsplit(base)
            if address.scheme not in ("http", "https") or not address.hostname or address.username or address.query or address.fragment or not model:
                raise ValueError("请输入有效的嵌入接口 HTTP(S) 地址和模型名")
            previous = service_settings.read(db, "semantic")
            key = b.get("key")
            if key is None and previous.get("key") and urlsplit(previous.get("base", "")).hostname != address.hostname:
                raise ValueError("更换嵌入服务域名时，请重新输入 Key 或明确清空")
            secret = previous.get("key", "") if key is None else securestore.protect(str(key).strip())
            service_settings.write(db, "semantic", {"base": base, "model": model, "key": secret})
        if action == "embedding" and method in ("GET", "POST"):
            return semantic.status(db)
    if method == "POST" and action == "provider-models":
        p = db.provider(b["id"])
        models = list(dict.fromkeys(str(x).strip() for x in b.get("models", []) if str(x).strip()))
        db.db.execute("UPDATE providers SET models=? WHERE id=?", (json.dumps(models), p["id"]))
        db.db.commit()
        sync_provider_models(db, p["id"])
        if models and p["enabled"] and (b.get("auto") or not db.active_model()):
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
            fields = {k: v for k, v in b.items() if k in ("name", "prompt", "assistant_id", "model_id", "fallback_model_ids", "reasoning", "permission")}
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
                raise ValueError("这个专家已在当前团队中")
            m = db.team_member_add(tid, fields.get("name") or a.get("name", "成员"), "member",
                                   prompt=fields.get("prompt", a.get("rules", "")), assistant_id=aid,
                                   color=len(db.team_members(tid)),
                                   **{k: fields[k] for k in ("model_id", "fallback_model_ids", "reasoning", "permission") if k in fields})
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
