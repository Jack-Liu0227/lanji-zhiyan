"""Team coordinators and bounded workers use the canonical project task lifecycle."""
from contextlib import contextmanager
import json
from pathlib import Path
import threading


LIMITS = {'max_parallel': 3}
_lock = threading.Lock()
_slots = {}
_global_slots = threading.BoundedSemaphore(6)
_CONTROL_TOOLS = {'task_create', 'task_start', 'task_status', 'task_cancel'}


def configure(db, executor, parent, scope, metadata):
    """Only server-resolved team identities can grant orchestration or worker roles."""
    if parent and parent['metadata'].get('_team_worker'):
        raise ValueError('团队成员不能继续派生任务，请将补充研究建议交给负责人')
    policy = (parent or {}).get('metadata', {}).get('_team_orchestration')
    if policy:
        if metadata.get('team_chat_scope'):
            return
        if executor['binding_id'] != policy['binding_id'] or executor['member_id'] not in policy['worker_ids']:
            raise ValueError('负责人只能将子任务交给本次授权的团队成员')
        metadata['_team_worker'] = {'coordinator_id': parent['id'], 'team_id': policy['team_id'],
                                    'max_parallel': policy['max_parallel']}
        return
    if parent or executor['kind'] != 'team' or executor.get('member_selected'):
        return
    import shared_executors
    roster = shared_executors.profile_members(db, executor['team_id'], executor['project_id'])
    allowed = scope.get('allowed_member_ids')
    roster = [m for m in roster if m['role'] in ('leader', 'member', 'teammate', 'executor')
              and m['status'] not in ('disabled', 'archived') and (allowed is None or m['id'] in allowed)]
    policy = {**LIMITS, 'team_id': executor['team_id'], 'binding_id': executor['binding_id'],
              'leader_id': executor['member_id'],
              'worker_ids': [m['id'] for m in roster if m['id'] != executor['member_id']],
              'members': [{k: m.get(k, '') for k in ('id', 'name', 'role', 'assistant_id', 'prompt')} for m in roster]}
    metadata['_team_orchestration'] = policy
    scope['allowed_binding_ids'] = [executor['binding_id']]
    scope['allowed_member_ids'] = [m['id'] for m in roster]


def read_tool(name, args=None):
    import workflow
    # Transform tools have writable actions; workers use the dedicated read tools.
    return name != 'paper_tool' and not (name == 'project_resources' and (args or {}).get('action') == 'index') and workflow.tool_risk(name) in ('read_only', 'network_read')


def tools(task, specs):
    if task['metadata'].get('_team_worker'):
        return [s for s in specs if (read_tool(s['function']['name']) and s['function']['name'] != 'task_status') or s['function']['name'] == 'task_handoff']
    if task['metadata'].get('_team_orchestration'):
        return [s for s in specs if s['function']['name'] not in {'team_assign', 'team_form'}]
    return specs


def enforce_tool(db, name, args=None):
    import project_tasks as tasks
    import workflow
    authority = getattr(db, 'project_task_context', None)
    if not authority or not authority.parent_task_id:
        return
    task = tasks.get(db, authority.parent_task_id)
    if task['metadata'].get('_team_worker') and name == 'task_handoff':
        return
    if task['metadata'].get('_team_worker') and not read_tool(name, args):
        raise PermissionError('并行成员只使用查询和阅读工具，分析结果会独立保存；共享文档由负责人汇总写入')
    if task['metadata'].get('_team_orchestration'):
        if name in {'team_assign', 'team_form'}:
            raise PermissionError('团队负责人请使用 task_create、task_start 和 workflow 工具调度本次任务')
        if name not in _CONTROL_TOOLS and workflow.tool_risk(name) not in ('read_only', 'network_read'):
            if any(child['status'] != 'delivered' for child in task['children']):
                raise PermissionError('请等待成员交付后再汇总写入共享资料')


def prompt(task, run):
    policy = task['metadata'].get('_team_orchestration')
    if policy:
        roster = json.dumps(policy['members'], ensure_ascii=False)
        return (f"\n你是本次团队任务的负责人。团队名册：{roster}\n"
                f"先明确目标和交付要求，再用 task_create 按 member_id 将独立研究分给专家；executor_binding_id 使用 {policy['binding_id']}。"
                '子任务会自动排队执行。需要先后交接时设置 dependencies，只有交付版本可供下游使用。'
                f"同时执行 {policy['max_parallel']} 个成员任务，其他任务排队继续；"
                '每份任务写清研究对象、来源范围、证据定位和预期产物。简单目标可以直接完成。'
                '派发后若仍有成员未交付，说明当前进度并结束本轮，不要轮询空等、冒充成员或声称研究完成。'
                '成员全部交付后系统会自动让你继续，task_inputs.children 含固定的交付版本和产物；'
                '需要补充研究时可追加任务。汇总时核查来源与证据，标明分歧、推断和局限，保存最终产物。'
                '成员的自动交付只用于内部汇总，不代表用户批准了最终结论。'
                '如果用户要求按工作流推进，先调用 workflow_templates 选择当前项目模板，再调用 workflow_prepare 和 workflow_start 启动工作流；'
                '之后用 workflow_status 查看节点和审查目标。每个工作流节点由团队负责人承接，负责人可以为节点创建成员子任务。'
                '节点结果必须等前端用户审查、编辑或修订并通过后，工作流才会进入下一个节点。')
    if task['metadata'].get('_team_worker'):
        return ('\n你是负责人派发的独立研究专家，只完成当前任务。你看不到负责人的完整对话。'
                '使用查询和阅读工具，返回发现、来源及页码或章节定位、事实与推断的区别、局限及后续建议。'
                '你的回复将保存为本次任务的独立版本化产物，不要改共享文档或代替用户核查。'
                '如果任务更适合另一位团队成员，可以使用 task_handoff 转移控制权，写清目标成员 ID、具体原因和已确认的证据；转移后不要继续执行当前任务。')
    return ''


def needs_aggregation(task, run):
    if not task['metadata'].get('_team_orchestration'):
        return False
    seen = {c['task_id'] for c in run['input_snapshot'].get('children', [])}
    return any(c['id'] not in seen for c in task['children'])


def dispatch(db, parent_id):
    import project_tasks as tasks
    import project_task_execution as worker
    parent = tasks.get(db, parent_id)
    if not parent['metadata'].get('_team_orchestration') or parent['status'] in tasks.TERMINAL:
        return
    for child in parent['children']:
        if not child['metadata'].get('_team_worker') or child['status'] != 'ready':
            continue
        dependencies = [tasks.get(db, key) for key in child['dependencies']]
        if any(t['status'] != 'delivered' for t in dependencies):
            continue
        try:
            run = tasks.start(db, child['id'], authority=tasks.CommandContext.system(parent['project_id'], parent_id))
            worker.kick(db.path, run['id'])
        except (ValueError, PermissionError) as exc:
            tasks.block_start(db, child['id'], str(exc), authority=tasks.CommandContext.system(parent['project_id'], parent_id))


def changed(db, task):
    import project_tasks as tasks
    if task['metadata'].get('_team_orchestration'):
        if task['status'] == 'blocked' and (task.get('latest_run') or {}).get('checkpoint', {}).get('waiting_children'):
            dispatch(db, task['id'])
            tasks._wake_parent(db, {'parent_task_id': task['id'], 'status': 'delivered'})
    elif task['metadata'].get('_team_worker') and task['status'] == 'delivered':
        dispatch(db, task['parent_task_id'])


@contextmanager
def slot(db_path, task, cancel_check):
    policy = task['metadata'].get('_team_worker')
    if not policy:
        yield
        return
    key = (str(Path(db_path).resolve()), policy['team_id'])
    with _lock:
        local = _slots.setdefault(key, threading.BoundedSemaphore(policy['max_parallel']))
    acquired = []
    try:
        for semaphore in (local, _global_slots):
            while not semaphore.acquire(timeout=0.1):
                if cancel_check():
                    import execution
                    raise execution.Cancelled('团队任务已停止')
            acquired.append(semaphore)
        yield
    finally:
        for semaphore in reversed(acquired):
            semaphore.release()


def state(db, team_ids, session=''):
    import project_tasks as tasks
    slots = ','.join('?' for _ in team_ids)
    query = "SELECT id FROM project_tasks WHERE json_extract(metadata,'$._team_orchestration.team_id') IN (" + slots + ')'
    args = list(team_ids)
    if session:
        query += " AND json_extract(metadata,'$.team_chat_session')=?"
        args.append(session)
    row = db.db.execute(query + ' ORDER BY rowid DESC LIMIT 1', args).fetchone()
    if not row:
        return None
    task = tasks.get(db, row['id'])
    children = task['children']
    counts = {}
    for child in children:
        counts[child['status']] = counts.get(child['status'], 0) + 1
    failures = [c['title'] for c in children if c['status'] in ('failed', 'cancelled', 'interrupted', 'blocked')]
    phase = {'queued': '负责人排队中', 'running': '负责人拆解或汇总', 'awaiting_review': '团队产物等待核查',
             'delivered': '团队产物已交付', 'cancelled': '团队任务已停止', 'failed': '负责人执行失败',
             'interrupted': '执行已中断，请恢复任务', 'ready': '团队任务待开始'}.get(task['status'], '等待成员交付')
    if failures and task['status'] == 'blocked':
        phase = '成员任务需要处理：' + '、'.join(failures)
    if task['status'] == 'blocked' and not failures:
        problem = db.db.execute("SELECT data FROM project_task_events WHERE task_id=? AND kind='aggregation_blocked' ORDER BY rowid DESC LIMIT 1", (task['id'],)).fetchone()
        if problem:
            phase = json.loads(problem['data']).get('error') or phase
    handoffs = [dict(row) for row in db.db.execute(
        'SELECT id,source_member_id,target_member_id,reason,status,created_at,target_run_id FROM agent_handoffs WHERE task_id=? ORDER BY created_at',
        (task['id'],)).fetchall()]
    workflow_info = None
    workflow_run_id = task['metadata'].get('_team_workflow_run_id') or task.get('workflow_run_id')
    if workflow_run_id:
        try:
            import workflow_execution
            import task_tools
            workflow_run = workflow_execution.detail(db, workflow_run_id)
            workflow_view = task_tools.workflow_result(db, workflow_run)
            workflow_info = {'id': workflow_run['id'], 'status': workflow_run['status'],
                             'current_node': workflow_run.get('current_node', ''),
                             'chat_id': workflow_run.get('chat_id', ''),
                             'review_targets': workflow_view.get('review_targets', [])}
        except Exception as exc:
            workflow_info = {'id': workflow_run_id, 'status': 'error', 'error': str(exc)}
    return {'task_id': task['id'], 'chat_id': task.get('chat_id', ''), 'status': task['status'], 'phase': phase,
            'counts': counts, 'limits': task['metadata']['_team_orchestration'],
            'children': [{'id': c['id'], 'title': c['title'], 'status': c['status']} for c in children],
            'handoffs': handoffs, 'workflow': workflow_info}
