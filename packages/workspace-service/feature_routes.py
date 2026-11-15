"""业务扩展路由：工作流运行、项目待办与项目空间搜索。"""
from __future__ import annotations

from typing import Any

import project_todos
import space_service
import asset_service
import run_service


def route(method: str, path: str, body: dict[str, Any], query: dict[str, list[str]], db: Any) -> Any | None:
    # ---------------- 论文工具箱（图片图注/转换、翻译、润色、引文、格式转换、PDF 翻译） ----------------
    if path.startswith("/api/paper-toolbox"):
        import paper_toolbox
        response = paper_toolbox.route(method, path, body, query, db)
        if response is not None:
            return response

    # ---------------- 0. Canonical assets and resumable workflow runs ----------------
    if path == "/api/assets" and method == "GET":
        project_id = (query.get("project_id") or [""])[0]
        kind = (query.get("kind") or [""])[0]
        return {"assets": asset_service.list_assets(db, project_id, kind)}

    if path == "/api/assets/search" and method == "GET":
        return {"query": (query.get("q") or [""])[0],
                "assets": asset_service.search(db, (query.get("q") or [""])[0],
                                                project_id=(query.get("project_id") or [""])[0])}

    if path == "/api/assets/relations" and method == "GET":
        return {"asset_id": (query.get("id") or [""])[0],
                "relations": asset_service.relations(db, (query.get("id") or [""])[0])}

    if path == "/api/assets/diagnostics" and method == "GET":
        return asset_service.project_diagnostics(db, (query.get("project_id") or [""])[0])

    if path == "/api/assets/rebuild" and method == "POST":
        return asset_service.rebuild_project_index(
            db, str(body.get("project_id") or ""),
            include_files=bool(body.get("include_files", True)),
        )

    if path == "/api/workflow/runs" and method == "GET":
        runs = run_service.list_runs(db, (query.get("project_id") or [""])[0])
        return {"runs": [{**run, "nodes": run_service.nodes(db, run["id"])} for run in runs]}

    if path == "/api/workflow/run" and method == "GET":
        run = run_service.detail(db, (query.get("id") or [""])[0])
        if not run:
            raise KeyError((query.get("id") or [""])[0])
        return {"run": run}

    if path == "/api/workflow/run" and method == "POST":
        return {"run": run_service.start(db, str(body.get("project_id") or ""),
                                          str(body.get("workflow") or "research"),
                                          idempotency_key=str(body.get("idempotency_key") or ""))}

    if path == "/api/workflow/recover" and method == "POST":
        import agent_jobs
        return agent_jobs.recover(db.path)

    # ---------------- 2. 项目待办 (TODO) 与 看板概览 (Kanban) ----------------
    if path == "/api/kanban/overview" and method == "GET":
        task_id = (query.get("task_id") or [""])[0]
        return project_todos.kanban_overview(db, task_id)

    if path == "/api/todos" and method == "GET":
        task_id = (query.get("task_id") or [""])[0]
        return {"todos": project_todos.list_todos(db, task_id)}

    if path == "/api/todos/add" and method == "POST":
        return project_todos.add_todo(
            db,
            task_id=str(body.get("task_id", "")),
            title=str(body.get("title", "")),
            description=str(body.get("description", "")),
            priority=str(body.get("priority", "P1")),
            assigned_assistant=str(body.get("assigned_assistant", "")),
            due_date=str(body.get("due_date", "")),
            stage=str(body.get("stage", "")),
        )

    if path == "/api/todos/update" and method == "POST":
        return project_todos.update_todo(db, str(body.get("id", "")), **body)

    if path == "/api/todos/toggle" and method == "POST":
        return project_todos.toggle_todo(db, str(body.get("id", "")))

    if path == "/api/todos/delete" and method == "POST":
        return project_todos.delete_todo(db, str(body.get("id", "")))

    if path == "/api/todos/import_workflow" and method == "POST":
        return {"created": project_todos.import_workflow_stages_as_todos(db, str(body.get("task_id", "")))}

    if path == "/api/todos/dispatch" and method == "POST":
        return {"task": project_todos.dispatch(db, str(body.get("id") or ""),
                                               reviewer_id=str(body.get("reviewer_id") or ""))}

    # ---------------- 4. 项目空间搜索与笔记预览 ----------------
    if path == "/api/space/search" and method == "GET":
        task_id = (query.get("task_id") or [""])[0]
        q = (query.get("q") or [""])[0]
        return space_service.search_project_space(db, task_id, q)

    if path == "/api/space/notes" and method == "GET":
        task_id = (query.get("task_id") or [""])[0]
        return {"notes": space_service.list_project_notes(db, task_id)}

    if path == "/api/space/note" and method == "GET":
        note_id = (query.get("id") or [""])[0]
        note_data = space_service.get_project_note_detail(db, note_id)
        return {"note": note_data, **note_data}

    return None
