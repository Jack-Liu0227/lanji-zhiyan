"""Freeze a conversation's effective runtime choices without storing credentials."""
import agent_settings


def snapshot(db, owner_id, executor=None):
    import workflow
    executor = executor or {}
    binding_id = str(executor.get('executor_binding_id') or '')
    import execution_conversations
    execution=execution_conversations.by_chat(db,owner_id)
    if execution and execution['kind']=='task' and not binding_id:
        task=db.db.execute('SELECT executor_binding_id,member_id FROM project_tasks WHERE id=?',(execution['entity_id'],)).fetchone()
        binding_id=task['executor_binding_id'];executor={**executor,'member_id':task['member_id']}
    resolved = None
    if binding_id:
        import project_bindings
        project_id = db.chat(owner_id).get('task_id')
        if not project_id:
            raise ValueError('专家对话需要绑定当前项目')
        resolved = project_bindings.resolve(db, project_id, binding_id, member_id=str(executor.get('member_id') or ''))
    config=dict(agent_settings.runtime_config(db,owner_id,
        (resolved or {}).get('assistant_id',''), (resolved or {}).get('member')))
    config['assistant_name']=(db.assistant(config['assistant_id']) or {}).get('name','执行专家')
    if resolved:
        config.update(executor_binding_id=binding_id, team_id=resolved['team_id'], member_id=resolved['member_id'],
                      tools=resolved['tools'], member_prompt=(resolved['member'] or {}).get('prompt',''))
    profile=db.model(config['model_id']) if config.get('model_id') else workflow.agent_profile(db,owner_id)
    if profile:
        config['model_id']=profile['id']
    if execution:
        context=execution_conversations.read(db,owner_id)
        config['execution_context']='关联任务：'+context['execution']['title']+'\n状态：'+context['execution']['status']+'\n任务要求：'+context['execution']['goal']+'\n近期结果：'+'\n'.join(str(message.get('content',''))[-4000:] for message in context['messages'][-3:])
    agent_settings.validate_runtime_fields(db,config)
    return config


def apply(db, config):
    if not config:
        return
    db.agent_runtime_snapshot=dict(config)
    if config.get('model_id'):
        db.runtime_model={**db.model(config['model_id']),
                          'reasoning':config['reasoning'],'permission':config['permission']}
