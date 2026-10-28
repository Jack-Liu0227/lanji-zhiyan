"""Local UI boundary for project task commands and explicit project grants."""
from __future__ import annotations


def route(method, path, body, query, db):
    if not path.startswith("/api/project-tasks"):
        return None
    import project_bindings as bindings
    import project_tasks as tasks
    import project_task_execution as worker
    import workflow_layout

    if method=='GET' and path=='/api/project-tasks/all':
        def value(name,default=''):
            raw=query.get(name,[default]);return raw[0] if isinstance(raw,list) else raw
        project=str(value('project_id'));status=str(value('status'));search=str(value('q')).strip()[:200]
        if project:workflow_layout._project(db,project)
        where=['1=1'];params=[]
        for field,item in [('t.project_id',project),('t.status',status)]:
            if item:where.append(field+'=?');params.append(item)
        if search:where.append('(t.title LIKE ? OR t.goal LIKE ?)');params.extend(['%'+search+'%']*2)
        clause=' AND '.join(where)
        limit=max(1,min(int(value('limit',100)),200));offset=max(0,int(value('offset',0)))
        total=db.db.execute('SELECT COUNT(*) FROM project_tasks t WHERE '+clause,params).fetchone()[0]
        rows=db.db.execute("SELECT t.id,t.project_id,t.title,t.goal,t.status,t.workflow_run_id,t.workflow_node_id,t.parent_task_id,t.updated_at,p.title AS project_title,(SELECT o.kind FROM project_task_origins o WHERE o.task_id=t.id AND o.kind='schedule_run' LIMIT 1) AS source_kind FROM project_tasks t JOIN research_tasks p ON p.id=t.project_id WHERE "+clause+' ORDER BY t.updated_at DESC,t.rowid DESC LIMIT ? OFFSET ?',[*params,limit,offset]).fetchall()
        return {'tasks':[dict(row) for row in rows],'total':total,'limit':limit,'offset':offset}

    def q(name):
        return (query.get(name) or [""])[0]

    project_id = str(body.get("project_id") or q("project_id") or "")
    task_id = str(body.get("id") or q("id") or "")
    action = path.removeprefix("/api/project-tasks").strip("/")
    if task_id:
        task = tasks.get(db, task_id, project_id=project_id)
        project_id = task["project_id"]
    workflow_layout._project(db, project_id)
    authority = tasks.CommandContext.ui(project_id)
    if method == 'POST' and action in ('delete', 'restore', 'organize', 'duplicate', 'location'):
        import task_management
        return task_management.command(db, project_id, task_id, action, body)
    if method == "GET":
        if action in ("", "list"):
            return {"tasks": tasks.list_tasks(db, project_id,
                workflow_run_id=q("workflow_run_id"), parent_task_id=q("parent_task_id"))}
        if action == "detail":
            return {"task": tasks.get(db, task_id, project_id=project_id)}
        if action=='output':
            import project_task_review
            return project_task_review.read(db,task_id,project_id,int(q('output_index') or 0))
        if action == 'graph-output':
            import project_task_review
            return project_task_review.read_graph(db, task_id, project_id)
        if action == "executors":
            import shared_executors
            grants=bindings.list_bindings(db,project_id)
            teams=[]
            for profile in shared_executors.templates(db):
                teams.append({**profile,'members':shared_executors.profile_members(db,profile['id'],project_id)})
            return {"bindings":grants,"available_assistants":db.assistants(),"teams":teams}
        if action == "policy":
            return {"policy": bindings.settings(db, project_id)}
        if action == "workflow-roles":
            import project_workflows
            template = project_workflows.effective(db,project_id,workflow_layout._layout(db, q("template_id")))
            rows = db.db.execute("SELECT * FROM project_workflow_roles WHERE project_id=? AND template_id=?",
                                 (project_id, template["id"])).fetchall()
            declared={bindings.role_id(node) for node in template['nodes']}
            roles = [{**dict(r), "executor_binding_id": r["binding_id"]} for r in rows if r['role_id'] in declared]
            return {"roles": roles, "template": template, "bindings": bindings.list_bindings(db, project_id)}
    elif method == "POST":
        if action == "bindings":
            binding = bindings.register(db, project_id, str(body.get("kind") or "assistant"),
                str(body.get("target_id") or body.get("assistant_id") or body.get("team_id") or ""),
                tools=body.get("tools"), enabled=body.get("enabled", True), source="user")
            return {"binding": binding, "bindings": bindings.list_bindings(db, project_id)}
        if action == "policy":
            return {"policy": bindings.save_settings(db, project_id, str(body.get("autonomy_mode") or ""))}
        if action == "workflow-roles":
            role = bindings.bind_role(db, project_id, str(body.get("template_id") or ""),
                str(body.get("role_id") or ""), str(body.get("executor_binding_id") or ""),
                member_id=str(body.get("member_id") or ""))
            return {"role": role}
        if action == "create":
            plan_id = str(body.get('plan_id') or '')
            plan = db.db.execute('SELECT * FROM project_todos WHERE id=? AND task_id=?',
                                 (plan_id, project_id)).fetchone() if plan_id else None
            if plan_id and not plan:
                raise ValueError('计划不属于当前项目')
            # Legacy execution identifiers are supplied by adapters, never by a form.
            allowed = ("title", "goal", "inputs", "executor_binding_id", "member_id", "output_contract",
                       "review_required", "parent_task_id", "dependencies")
            spec = {k: body[k] for k in allowed if k in body}
            if plan and plan['acceptance']:
                spec['goal'] = str(spec.get('goal') or plan['description'] or plan['title']) + '\n\n计划验收要求：\n' + plan['acceptance']
            if body.get("template_ref"):
                spec["metadata"] = {"template_ref": body["template_ref"]}
            task = tasks.create(db, {**spec, "project_id": project_id}, authority=authority,
                                request_key=str(body.get("request_key") or ""))
            if plan_id:
                import execution_board
                execution_board.link_plan(db,project_id,plan_id,task['id'])
        elif action == "start":
            run = tasks.start(db, task_id, authority=authority,
                request_key=str(body.get("request_key") or ""), autonomy_mode=body.get("autonomy_mode"), restart=body.get("restart") is True)
            if task.get("workflow_run_id") and not task.get("parent_task_id"):
                import workflow_execution
                workflow_execution.kick(db.path, task["workflow_run_id"])
            else:
                worker.kick(db.path, run["id"])
            return {"task": tasks.get(db, task_id, project_id=project_id), "run": run}
        elif action == "review":
            task = tasks.review(db, task_id, approved=body.get("approved"), feedback=body.get("feedback", ""),
                                authority=authority, request_key=str(body.get("request_key") or ""),
                                reviewed_run_id=str(body.get("reviewed_run_id") or ""),reviewed_output_version=body.get('reviewed_output_version'))
            if body.get('retry') is True and body.get('approved') is False:
                attempt = tasks.start(db, task_id, authority=authority, request_key='review-retry:' + task['latest_run_id'])
                if task.get('workflow_run_id') and not task.get('parent_task_id'):
                    import workflow_execution
                    workflow_execution.kick(db.path, task['workflow_run_id'])
                else:
                    worker.kick(db.path, attempt['id'])
                task = tasks.get(db, task_id, project_id=project_id)
        elif action=='revise-output':
            import project_task_review
            task=project_task_review.revise(db,task_id,body,authority=authority)
        elif action == 'revise-graph':
            import project_task_review
            task = project_task_review.revise_graph(db, task_id, body, authority=authority)
        elif action == "cancel":
            task = tasks.cancel(db, task_id, authority=authority)
        elif action == "mode":
            task = tasks.set_mode(db, task_id, str(body.get("autonomy_mode") or ""), authority=authority)
        else:
            raise ValueError("不支持的项目任务操作")
        if task.get("workflow_run_id") and task["status"] == "delivered" and not task.get("parent_task_id"):
            import workflow_execution
            workflow_execution.kick(db.path, task["workflow_run_id"])
        return {"task": task}
    raise ValueError("不支持的项目任务请求")
