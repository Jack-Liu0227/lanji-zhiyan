"""RESTful API routes implementing the Section 5 HTTP contract."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from run_manager import RunManager


def route(method: str, path: str, body: dict[str, Any], query: dict[str, Any], db: Any) -> Any:
    # 5.1 项目与论文
    if method == "GET" and path == "/api/projects":
        return {"projects": [dict(t) for t in db.tasks()]}

    if method == "POST" and path == "/api/projects":
        title = str(body.get("title") or "").strip()
        if not title:
            raise ValueError("title is required")
        q = str(body.get("question") or "").strip()
        mat = str(body.get("material_system") or body.get("material") or "").strip()
        base = str(body.get("base_dir") or "").strip()
        tid = db.create_task(title, q, mat, base)
        import workflow
        made = workflow.make_project_tree(base, db.summary(tid)["task"]) if base else []
        return {"project": db.summary(tid)["task"], "task_id": tid, "dirs": made}

    m = re.match(r"^/api/projects/([^/]+)/summary$", path)
    if method == "GET" and m:
        pid = m.group(1)
        return db.summary(pid)

    m = re.match(r"^/api/projects/([^/]+)/papers/ingest$", path)
    if method == "POST" and m:
        pid = m.group(1)
        src = body.get("path") or body.get("source_path")
        if not src:
            raise ValueError("path is required to ingest paper")
        paper = db.ingest_pdf(pid, src)
        return {"paper": paper}

    m = re.match(r"^/api/projects/([^/]+)/papers$", path)
    if method == "GET" and m:
        pid = m.group(1)
        return {"papers": db.papers_of(pid)}

    m = re.match(r"^/api/papers/([^/]+)/evidence$", path)
    if method == "POST" and m:
        paper_id = m.group(1)
        evidence_item = {
            "page_no": int(body.get("page_no") or 1),
            "field": str(body.get("field") or "fact"),
            "value": str(body.get("value") or ""),
            "excerpt": str(body.get("excerpt") or ""),
            "kind": str(body.get("kind") or "fact"),
        }
        res = db.add_evidence(paper_id, evidence_item)
        return {"evidence": res}

    m = re.match(r"^/api/projects/([^/]+)/evidence$", path)
    if method == "GET" and m:
        pid = m.group(1)
        return {"evidence": [dict(r) for r in db.evidence(pid)]}

    m = re.match(r"^/api/projects/([^/]+)/export$", path)
    if method == "POST" and m:
        pid = m.group(1)
        kind = str(body.get("kind") or "evidence")
        target = str(body.get("target") or "export.csv")
        pdir = db.out_dir(pid)
        dest = pdir / target
        if kind == "report":
            out_file = db.export_report(pid, dest)
        else:
            out_file = db.export_evidence(pid, dest)
        return {"ok": True, "path": str(out_file)}

    # 5.2 Agent、助手和团队
    if method == "GET" and path == "/api/chats":
        return {"chats": db.chats()}

    if method == "POST" and path == "/api/chats":
        cid = db.create_chat(str(body.get("title") or ""))
        tid = str(body.get("task_id") or "").strip()
        aid = str(body.get("assistant_id") or "").strip()
        if tid:
            db.bind_chat(cid, tid)
        if aid:
            db.set_chat_assistant(cid, aid)
        return {"chat": db.chat(cid)}

    m = re.match(r"^/api/chats/([^/]+)/bind$", path)
    if method == "POST" and m:
        cid = m.group(1)
        tid = body.get("task_id")
        aid = body.get("assistant_id")
        if tid is not None:
            db.bind_chat(cid, str(tid))
        if aid is not None:
            db.set_chat_assistant(cid, str(aid))
        return {"chat": db.chat(cid)}

    if method == "GET" and path == "/api/assistants":
        return {"assistants": db.assistants()}

    if method == "POST" and path == "/api/assistants":
        aid = str(body.get("id") or "").strip() or ("a-" + Path(__file__).stat().st_mtime_ns.to_bytes(8, 'big').hex()[:8])
        saved = db.assistant_save(
            aid,
            name=body.get("name"),
            rules=body.get("rules"),
            model_id=body.get("model_id"),
            enabled=body.get("enabled", True),
            avatar=body.get("avatar"),
            description=body.get("description"),
        )
        return {"assistant": saved}

    m = re.match(r"^/api/teams/([^/]+)/members$", path)
    if method == "POST" and m:
        tid = m.group(1)
        import team
        return {"member": team.add_member(tid, body.get("name", ""), body.get("step", ""), body.get("prompt", ""))}

    m = re.match(r"^/api/teams/([^/]+)/leader$", path)
    if method == "POST" and m:
        tid = m.group(1)
        leader_id = str(body.get("leader_id") or body.get("member_id") or "")
        return {"ok": True, "team_id": tid, "leader_id": leader_id}

    m = re.match(r"^/api/teams/([^/]+)/tasks$", path)
    if method == "POST" and m:
        tid = m.group(1)
        import team
        return {"task": team.assign(tid, body.get("member", ""), body.get("prompt", ""),
                                    body.get("title", ""), body.get("trigger", "leader"))}

    m = re.match(r"^/api/runs/([^/]+)/cancel$", path)
    if method == "POST" and m:
        run_id = m.group(1)
        ok = RunManager.get_instance().cancel_run(run_id, db=db)
        return {"cancelled": ok, "run_id": run_id}

    m = re.match(r"^/api/runs/([^/]+)/events$", path)
    if method == "GET" and m:
        run_id = m.group(1)
        after_val = query.get("after", [0])
        after_id = int(after_val[0] if isinstance(after_val, list) else after_val)
        return {"run_id": run_id, "events": db.get_run_events(run_id, after_id)}

    # 5.3 Provider、Capability 和 MCP
    if method == "GET" and path == "/api/providers":
        return {"providers": db.providers()}

    if method == "POST" and path == "/api/providers":
        return {"provider": db.save_provider(body)}

    m = re.match(r"^/api/providers/([^/]+)/discover$", path)
    if method == "POST" and m:
        pid = m.group(1)
        import workflow
        prov = db.provider(pid)
        if not prov:
            raise KeyError(pid)
        models = workflow.discover_models(prov["base_url"], prov.get("api_key", ""))
        return {"models": models}

    m = re.match(r"^/api/providers/([^/]+)/models$", path)
    if method == "POST" and m:
        pid = m.group(1)
        models = body.get("models") or []
        db.db.execute("UPDATE providers SET models=? WHERE id=?", (json.dumps(models, ensure_ascii=False), pid))
        db.db.commit()
        return {"ok": True, "provider_id": pid}

    if method == "GET" and path == "/api/capabilities":
        import capabilities
        aid = str((query.get("assistant_id") or [""])[0] if isinstance(query.get("assistant_id"), list) else query.get("assistant_id") or "")
        return {"capabilities": capabilities.capabilities(db, aid)}

    if method == "POST" and path == "/api/capabilities/bind":
        import capabilities
        aid = str(body.get("assistant_id") or "")
        kind = str(body.get("kind") or "")
        item_id = str(body.get("item_id") or "")
        enabled = bool(body.get("enabled", True))
        return capabilities.bind(db, aid, kind, item_id, enabled)

    if method == "POST" and path == "/api/mcp/config":
        import capabilities
        return capabilities.mcp_save(db, body)

    if method == "POST" and path == "/api/mcp/discover":
        import capabilities
        sid = str(body.get("server_id") or body.get("id") or "")
        return capabilities.mcp_discover(db, sid)

    if method == "POST" and path == "/api/mcp/reveal":
        import capabilities
        sid = str(body.get("server_id") or body.get("id") or "")
        return capabilities.mcp_reveal(db, sid)

    if method == "POST" and path == "/api/mcp/revoke":
        import capabilities
        server = str(body.get("server") or body.get("server_id") or "")
        tool = str(body.get("tool") or "")
        return capabilities.mcp_revoke(db, server, tool)

    # 审计
    if method == "GET" and path == "/api/audit/events":
        proj = str((query.get("project_id") or [""])[0] if isinstance(query.get("project_id"), list) else query.get("project_id") or "")
        asst = str((query.get("assistant_id") or [""])[0] if isinstance(query.get("assistant_id"), list) else query.get("assistant_id") or "")
        lim_val = query.get("limit", [100])
        limit = int(lim_val[0] if isinstance(lim_val, list) else lim_val)
        return {"audit_events": db.get_audit_events(project_id=proj, assistant_id=asst, limit=limit)}

    return None
