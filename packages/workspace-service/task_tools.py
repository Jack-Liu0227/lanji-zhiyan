"""Agent command boundary. Human review and permission elevation are not tools."""
from __future__ import annotations

import project_tasks as tasks


NAMES = {"task_create", "task_start", "task_status", "task_cancel", "workflow_templates", "workflow_prepare", "workflow_start", "workflow_status", "workflow_cancel"}


def workflow_result(db, run):
    from workflow_checkpoint import review_target
    if not run:
        return {'run': None}
    pending = [review_target(task) for task in tasks.list_tasks(db, run['project_id'], workflow_run_id=run['id'])
               if task['status'] == 'awaiting_review']
    return {'navigation': {'project_id': run['project_id'], 'workflow_run_id': run['id']},
            'review_targets': pending, 'run': run}


def call(db, project_id, name, args):
    import project_task_execution as worker
    import workflow_execution as engine
    import workflow_tasks
    authority = getattr(db, "project_task_context", None) or tasks.CommandContext.agent(project_id)
    if authority.project_id != project_id:
        raise ValueError("任务工具只能操作当前项目")
    if "autonomy_mode" in args or "approved" in args or "authority" in args:
        raise ValueError("专家工具不能设置执行模式或代替人工核查")
    parent = tasks.get(db, authority.parent_task_id, project_id=project_id) if authority.parent_task_id else None
    if name == 'workflow_templates':
        import workflow_layout
        import project_workflows
        workflow_layout.ensure_schema(db)
        return {'templates': [project_workflows.current(db, project_id, row['id'])
                              for row in db.db.execute('SELECT id FROM workflow_layouts ORDER BY name')]}
    if name == "task_create":
        plan_id = str(args.get('plan_id') or '')
        plan = db.db.execute('SELECT * FROM project_todos WHERE id=? AND task_id=?', (plan_id, project_id)).fetchone() if plan_id else None
        if plan_id and not plan: raise ValueError('计划不属于当前项目')
        fields = ("title", "goal", "executor_binding_id", "member_id", "inputs", "output_contract", "dependencies")
        spec = {k: args[k] for k in fields if k in args}
        if plan and plan['acceptance']: spec['goal'] = str(spec.get('goal') or plan['description'] or plan['title'])+'\n\n计划验收要求：\n'+plan['acceptance']
        task = tasks.create(db, {**spec, "project_id": project_id},
                            authority=authority, request_key=str(args.get("request_key") or ""))
        if plan_id:
            import execution_board
            execution_board.link_plan(db, project_id, plan_id, task['id'])
        return {"task": task}
    if name.startswith("task_"):
        task_id = str(args.get("id") or "")
        if not task_id and name == "task_status":
            rows = tasks.list_tasks(db, project_id, workflow_run_id=(parent or {}).get("workflow_run_id", ""),
                                    parent_task_id=(parent or {}).get("id", ""))
            import project_bindings
            executors = project_bindings.list_bindings(db, project_id)
            if parent and parent["scope"].get("allowed_binding_ids") is not None:
                executors = [e for e in executors if e["id"] in parent["scope"]["allowed_binding_ids"]]
            for executor in executors:
                if executor["kind"] == "team":
                    allowed_members = parent["scope"].get("allowed_member_ids") if parent else None
                    executor["members"] = [{k: m[k] for k in ("id", "name", "role", "assistant_id", "step")}
                        for m in db.team_members(executor["target_id"])
                        if m["role"] not in ("reviewer", "observer") and (allowed_members is None or m["id"] in allowed_members)]
            return {"tasks": rows, "executors": executors}
        task = tasks.get(db, task_id, project_id=project_id)
        if parent and task["id"] != parent["id"] and task["parent_task_id"] != parent["id"]:
            raise ValueError("只能操作当前任务及其直接子任务")
        if name == "task_start":
            run = tasks.start(db, task_id, authority=authority, request_key=str(args.get("request_key") or ""))
            if task["workflow_run_id"] and not task["parent_task_id"]:
                engine.kick(db.path, task["workflow_run_id"])
            else:
                worker.kick(db.path, run["id"])
            return {"task": tasks.get(db, task_id), "run": run}
        if name == "task_cancel":
            task = tasks.cancel(db, task_id, authority=authority)
        return {"task": task}
    run_id = str(args.get("id") or "")
    if parent:
        if name == "workflow_prepare" or not parent["workflow_run_id"] or (run_id and run_id != parent["workflow_run_id"]):
            raise ValueError("当前任务只能查询或停止所属工作流，不能创建其他实例")
        run_id = parent["workflow_run_id"]
        if name == "workflow_start":
            raise ValueError("节点执行不能自行重启整个工作流")
    if name in ("workflow_prepare", "workflow_start"):
        run = engine.detail(db, run_id) if run_id else workflow_tasks.prepare(db, project_id,
            str(args.get("goal") or ""), str(args.get("request_key") or ""), str(args.get("template_id") or ""), inputs=args.get('inputs'))
        if run["project_id"] != project_id:
            raise ValueError("工作流不属于当前项目")
        if name == "workflow_start":
            engine.kick(db.path, run["id"])
        return workflow_result(db, engine.detail(db, run["id"]))
    run = engine.detail(db, run_id) if run_id else engine.latest(db, project_id)
    if run and run["project_id"] != project_id:
        raise ValueError("工作流不属于当前项目")
    if name == "workflow_cancel" and run:
        run = workflow_tasks.cancel(db, run["id"], authority=authority)
    return workflow_result(db, run)
