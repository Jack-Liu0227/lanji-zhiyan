"""Resumable steward setup using the existing project, binding and workflow services."""
import hashlib
import json
import threading

import capabilities
import langgraph_workflow
import project_bindings
import project_notes
import project_workflows
import workflow_layout

_lock = threading.RLock()
DEFAULT_SKILLS = {
    'research_proposal': ('research-topic-finder', 'nature-academic-search', 'brainstorming-research-ideas'),
    'literature_study': ('nature-academic-search', 'literature-review', 'nature-reader'),
    'paper_writing': ('nature-writing', 'nature-reviewer', 'academic-writing'),
}
NAMES = {
    'research_proposal': ('研究问题顾问', '文献证据专家', '实验方法专家', '科研写作专家'),
    'literature_study': ('文献检索专家', '文献证据专家', '文献比较专家', '综述写作专家'),
    'paper_writing': ('论文结构专家', '科研写作专家', '同行评审专家', '论文修订专家'),
}


def _permit(db, actor_id, name):
    if not capabilities.enabled(db, actor_id, 'tool', name):
        raise PermissionError('管家当前未启用工具：' + name)


def configure(db, args, *, project_id='', actor_id='builtin-steward', default_model_id=''):
    """Same plan/key resumes setup; never recreate a queued or completed run."""
    import workflow
    with _lock:
        idea = str(args.get('idea') or '').strip()
        if not idea or len(idea) > 20000: raise ValueError('研究目标须为 1 至 20000 字符')
        if 'auto_start_graph' in args and not isinstance(args['auto_start_graph'], bool): raise ValueError('auto_start_graph 必须是布尔值')
        graph_type = str(args.get('graph_type') or 'research_proposal')
        if graph_type not in langgraph_workflow.GRAPH_REGISTRY: raise ValueError('科研流程模板不存在')
        graph = langgraph_workflow.GRAPH_REGISTRY[graph_type]()
        experts = args.get('experts')
        if experts is None:
            experts = [{'role_id': node.name, 'name': name} for node, name in zip(graph.nodes.values(), NAMES[graph_type])]
        if not isinstance(experts, list) or not 1 <= len(experts) <= 12: raise ValueError('专家配置须为 1 至 12 个角色')
        roles = [str(expert.get('role_id') or '') for expert in experts if isinstance(expert, dict)]
        if len(roles) != len(experts) or len(set(roles)) != len(roles) or any(not workflow_layout._ID.fullmatch(role) for role in roles):
            raise ValueError('专家角色 ID 必须有效且唯一')
        experts = [dict(expert) for expert in experts]
        for expert in experts:
            if default_model_id and not expert.get('assistant_id') and not expert.get('model_id'): expert['model_id'] = default_model_id
        definition = args.get('workflow')
        if definition is not None:
            definition = workflow_layout.validate_definition(definition)
            if any(node['executor_role_id'] not in roles for node in definition['nodes']): raise ValueError('每个节点的 executor_role_id 必须对应一个专家角色')
        elif set(roles) != set(graph.nodes):
            raise ValueError('自定义专家角色须同时提供对应的工作流节点')
        if project_id:
            project_notes.project_root(db, project_id, writable=True)
        _permit(db, actor_id, 'project_create_from_idea')
        for name in ('assistant_manage', 'team_create', 'workflow_create', 'project_note_write'):
            _permit(db, actor_id, name)
        if not project_id: _permit(db, actor_id, 'project_create')
        if args.get('auto_start_graph', True): _permit(db, actor_id, 'workflow_start')
        installed = {item['id']: item for item in capabilities.list_skills(str(db.base_dir(project_id) or '') if project_id else '')}
        requested = args.get('skills')
        selected = requested if requested is not None else [name for name in DEFAULT_SKILLS[graph_type] if name in installed and capabilities.enabled(db, actor_id, 'skill', name)]
        if not isinstance(selected, list) or any(not isinstance(name, str) or name not in installed or not capabilities.enabled(db, actor_id, 'skill', name) for name in selected):
            raise ValueError('请选择实际已安装且已启用的 Skill ID')
        for expert in experts:
            if expert.get('assistant_id'):
                assistant = db.require_assistant(str(expert['assistant_id']))
                if assistant['id'] == 'builtin-steward': raise ValueError('请选择专业专家作为执行者，管家负责装配')
            elif not str(expert.get('name') or '').strip(): raise ValueError('新专家需要名称')
            if expert.get('model_id'):
                import agent_settings
                agent_settings.validate_runtime_fields(db, {'model_id': expert['model_id']})
        plan = {**args, 'project': project_id, 'experts': experts, 'skills': selected}
        plan.pop('request_key', None)
        digest = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        key = str(args.get('request_key') or 'idea:' + digest)
        if len(key) > 200: raise ValueError('request_key 过长')
        row = db.db.execute('SELECT * FROM research_setups WHERE request_key=?', (key,)).fetchone()
        if row and row['plan_hash'] != digest: raise ValueError('request_key 已用于不同配置，请沿用原配置或更换 key')
        result = json.loads(row['result']) if row else {'experts': [], 'skills': selected}
        if row and row['project_id'] and project_id and row['project_id'] != project_id: raise PermissionError('装配记录属于其他项目')
        def save(status='configuring', error=''):
            stamp = workflow.now()
            db.db.execute('INSERT INTO research_setups VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(request_key) DO UPDATE SET project_id=excluded.project_id,result=excluded.result,status=excluded.status,error=excluded.error,updated_at=excluded.updated_at',
                          (key, result.get('project_id', ''), digest, json.dumps(result, ensure_ascii=False), status, error, stamp, stamp))
            db.db.commit()
        if row and row['status'] == 'configured':
            return _public(db, result, reused=True)
        if project_id or result.get('project_id'):
            import workflow_execution
            active = workflow_execution.active(db, project_id or result['project_id'])
            if active and active['id'] != result.get('run_id'): raise ValueError('项目已有活动工作流，请先完成或停止再装配')
        save()
        try:
            if not result.get('project_id'):
                title = str(args.get('title') or idea.splitlines()[0][:60]).strip()
                pid = project_id or db.create_task(title, str(args.get('goal') or idea), str(args.get('material_system') or ''))
                result.update(project_id=pid, title=db.summary(pid)['task']['title'], base_dir=str(db.base_dir(pid)))
                save()
            pid = result['project_id']
            project_notes.project_root(db, pid, writable=True)
            for expert in experts:
                role = expert['role_id']
                if any(item['role_id'] == role for item in result['experts']): continue
                assistant = db.require_assistant(expert['assistant_id']) if expert.get('assistant_id') else next((a for a in db.assistants() if a['name'] == expert['name'] and a.get('enabled') and a['id'] != 'builtin-steward'), None)
                if not assistant:
                    aid = 'setup-' + hashlib.sha256((key + ':' + role).encode()).hexdigest()[:32]
                    assistant = db.assistant(aid) or db.assistant_save(aid, name=expert['name'],
                        description=str(expert.get('description') or role),
                        rules=str(expert.get('rules') or '完成分配的科研工作，先读取相关 Skill 和项目资料，使用工具保存产物，引用可验证证据。区分研究建议、实际数据和未验证假设，不编造文献或实验结果。'),
                        model_id=str(expert.get('model_id') or ''))
                for skill in selected:
                    if not capabilities.enabled(db, assistant['id'], 'skill', skill): raise PermissionError('执行专家已停用 Skill：' + skill)
                grant = _grant(db, pid, 'assistant', assistant['id'])
                result['experts'].append({'role_id': role, 'assistant_id': assistant['id'], 'name': assistant['name'], 'binding_id': grant['id']})
                save()
            if not result.get('team_id'):
                team_name = str(args.get('team_name') or result['title'] + '研究团队')[:120]
                team = next((team for team in db.teams() if team.get('project_id') == pid and team['name'] == team_name and team['status'] != 'archived'), None)
                if not team: team = db.team_add(team_name, kind='custom', project_id=pid, purpose=idea)
                result['team_id'] = team['id'];save()
            members = db.team_members(result['team_id'])
            for i, expert in enumerate(result['experts']):
                member = next((member for member in members if member['assistant_id'] == expert['assistant_id']), None)
                if not member:
                    member = db.team_member_add(result['team_id'], expert['name'], role='leader' if not members else 'member', assistant_id=expert['assistant_id'], prompt='负责角色：' + expert['role_id'])
                    members.append(member)
                expert['member_id'] = member['id']
            save()
            team_grant = _grant(db, pid, 'team', result['team_id'])
            if not result.get('layout_id'):
                template_id = langgraph_workflow._template(db, graph_type)
                template = project_workflows.current(db, pid, template_id)
                nodes = definition['nodes'] if definition else template['nodes']
                configured = []
                for node in nodes:
                    expert = next(expert for expert in result['experts'] if expert['role_id'] == node['executor_role_id'])
                    team_node = graph.nodes.get(node['id']) and graph.nodes[node['id']].executor_hint == 'team'
                    configured.append({**node, 'executor_binding_id': team_grant['id'] if team_node else expert['binding_id'],
                        'member_id': expert['member_id'] if team_node else '', 'skills': selected})
                override = project_workflows.save(db, pid, {**(definition or template), 'layout_id': template_id,
                    'name': str(args.get('workflow_name') or result['title'] + '科研流程')[:120],
                    'nodes': configured, 'expected_revision': template['revision'], 'expected_fingerprint': template['fingerprint']})
                workflow_layout.select_layout(db, {'task_id': pid, 'layout_id': template_id})
                for node in configured:
                    project_bindings.bind_role(db, pid, template_id, node['executor_role_id'], node['executor_binding_id'], member_id=node.get('member_id', ''))
                result['layout_id'] = override['id'];save()
            if not result.get('plan_path'):
                plan_path = '笔记/研究规划/研究目标-' + digest[:16] + '.md'
                plan_note = project_notes.read(db, pid, plan_path) if (db.base_dir(pid) / plan_path).is_file() else project_notes.write(db, pid, plan_path, '# ' + result['title'] + '\n\n' + idea + '\n\n## 执行配置\n\n' + '\n'.join('- ' + expert['role_id'] + '：' + expert['name'] for expert in result['experts']) + '\n\nSkill：' + ('、'.join(selected) or '未指定；使用当前可用工具') + '\n', title='研究目标与执行计划', metadata={'category_id': 'planning'})
                result['plan_path'] = plan_note['path'];save()
            if args.get('auto_start_graph', True):
                import workflow_tasks
                import workflow_execution
                if not result.get('run_id'):
                    run = workflow_tasks.prepare(db, pid, str(args.get('goal') or idea), key, result['layout_id'])
                    result['run_id'] = run['id'];save()
                else:
                    run = workflow_execution.detail(db, result['run_id'])
                if run['status'] == 'queued': workflow_execution.kick(db.path, run['id'])
            save('configured')
            return _public(db, result)
        except Exception as exc:
            save('failed', str(exc))
            raise


def _grant(db, project_id, kind, target_id):
    previous = next((binding for binding in project_bindings.list_bindings(db, project_id) if binding['kind'] == kind and binding['target_id'] == target_id), None)
    if previous:
        if not previous['enabled'] or not previous.get('available', True): raise PermissionError('执行者在本项目已停用，不能自动重新启用')
        return previous
    return project_bindings.register(db, project_id, kind, target_id, source='steward_setup')


def _public(db, result, reused=False):
    import workflow_execution
    run = workflow_execution.detail(db, result['run_id']) if result.get('run_id') else None
    return {**result, 'ok': True, 'reused': reused, 'status': run['status'] if run else 'configured',
            'langgraph_run_id': result.get('run_id'), 'workspace': {'project_id': result['project_id'], 'section': 'workflow'},
            'message': '项目、专家、团队和流程已配置。' + ('研究执行状态：' + run['status'] if run else '尚未启动研究执行。')}
