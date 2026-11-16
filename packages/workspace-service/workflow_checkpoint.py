"""Durable orchestration cursors; only canonical task commands can accept work."""
from contextlib import closing
import sqlite3
from typing import TypedDict

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import project_tasks as tasks


class Cursor(TypedDict, total=False):
    status: str
    node_id: str
    task_id: str
    delivery_id: str
    output_revision: int


def review_target(task):
    run = task.get('latest_run') or {}
    return {'project_id': task['project_id'], 'task_id': task['id'],
            'workflow_run_id': task['workflow_run_id'], 'node_id': task['workflow_node_id'],
            'task_run_id': run.get('id', ''), 'title': task['title'], 'status': task['status'],
            'output_revision': run.get('checkpoint', {}).get('output_revision', 0),
            'editor': 'evidence_graph' if task['metadata'].get('stage') == 'map' else 'files'}


def _linear_graph(db, nodes, execute_node):
    graph = StateGraph(Cursor)
    for index, (nid, task_id) in enumerate(nodes):
        execute, review = 'execute_' + str(index), 'review_' + str(index)
        following = 'execute_' + str(index + 1) if index + 1 < len(nodes) else END

        def work(state, node_id=nid, project_task_id=task_id):
            return {**execute_node(node_id, project_task_id), 'node_id': node_id, 'task_id': project_task_id}

        def gate(state, project_task_id=task_id):
            # Resuming replays this function. All execution and file writes live in work().
            task = tasks.get(db, project_task_id)
            if task['status'] == 'delivered':
                tasks._validate_versions(db, task, task['latest_run'])
                return {'status': 'delivered', 'delivery_id': task['accepted_delivery_id'],
                        'output_revision': task['latest_run']['checkpoint'].get('output_revision', 0)}
            if task['status'] == 'cancelled':
                return {'status': 'cancelled'}
            # A resume replays this node from its beginning. Check the canonical
            # task before interrupting so an approval can advance to the next
            # node instead of opening the same review gate again.
            interrupt(review_target(task))
            # Some LangGraph versions continue after interrupt() when a caller
            # supplies a resume value in the same invocation. Re-read the task
            # so both behaviours use the canonical delivery decision.
            task = tasks.get(db, project_task_id)
            if task['status'] == 'delivered':
                tasks._validate_versions(db, task, task['latest_run'])
                return {'status': 'delivered', 'delivery_id': task['accepted_delivery_id'],
                        'output_revision': task['latest_run']['checkpoint'].get('output_revision', 0)}
            return {'status': 'cancelled' if task['status'] == 'cancelled' else 'retry'}

        graph.add_node(execute, work)
        graph.add_node(review, gate)
        graph.add_conditional_edges(execute, lambda state: 'stop' if state['status'] == 'cancelled' else 'review',
                                    {'stop': END, 'review': review})
        graph.add_conditional_edges(review, lambda state: state['status'],
                                    {'delivered': following, 'retry': execute, 'cancelled': END})
    graph.add_edge(START, 'execute_0' if nodes else END)
    return graph


class DagCursor(Cursor, total=False):
    attempted: list[str]
    errors: dict[str, str]


def _dag_graph(db, run, nodes, execute_node):
    import execution
    import workflow_execution as engine
    import workflow_tasks

    graph = StateGraph(DagCursor)

    def reset(state):
        settings = engine.detail(db, run['id'])['settings']
        return {'status': 'running', 'attempted': [], 'errors': settings.get('dag_errors', {})}

    def dispatch(state):
        rows = {item['id']: item for item in tasks.list_tasks(db, run['project_id'], workflow_run_id=run['id'])}
        ordered = [(nid, rows[tid]) for nid, tid in nodes]
        attempted = state['attempted']
        errors = state['errors']
        ready = [(nid, task) for nid, task in ordered
                 if task['status'] in ('ready', 'queued', 'blocked', 'failed', 'interrupted')
                 and all(rows[parent]['status'] == 'delivered' for parent in task['dependencies'])]
        for nid, task in ready:
            if nid in attempted or nid in errors or task['metadata']['node_config'].get('manual_execution'):
                continue
            try:
                outcome = execute_node(nid, task['id'])
                if outcome['status'] == 'cancelled':
                    return {'status': 'cancelled'}
                if outcome['status'] == 'failed':
                    failed = tasks.get(db, task['id'])
                    errors = {**errors, nid: (failed.get('latest_run') or {}).get('error') or '节点执行失败'}
            except execution.Cancelled:
                raise
            except (ValueError, OSError) as exc:
                errors = {**errors, nid: str(exc)}
            return {'status': 'running', 'attempted': [*attempted, nid], 'errors': errors}
        latest = engine.detail(db, run['id'])
        latest['settings']['dag_errors'] = errors
        manual = next(((nid, task) for nid, task in ready
                       if task['metadata']['node_config'].get('manual_execution')), None)
        if manual:
            nid, task = manual
            latest['settings']['manual_pending'] = {'node_id': nid, 'task_id': task['id'], 'label': task['title']}
        else:
            latest['settings'].pop('manual_pending', None)
        workflow_tasks._save(db, latest)
        for nid, task in ordered:
            if task['status'] == 'awaiting_review':
                workflow_tasks._sync_workflow_node(db, latest, task)
                engine._status(db, run['id'], 'awaiting_review')
                return {'status': 'awaiting_review', 'task_id': task['id']}
        if all(task['status'] == 'delivered' for _, task in ordered):
            for _, task in ordered:
                tasks._validate_versions(db, task, task['latest_run'])
            return {'status': 'delivered'}
        if manual:
            message = '等待提交人工产物：' + manual[1]['title']
        else:
            problem = next(((nid, task) for nid, task in ordered
                            if nid in errors or task['status'] in ('failed', 'blocked', 'interrupted')), None)
            if problem:
                nid, task = problem
                workflow_tasks._sync_workflow_node(db, latest, task)
                message = errors.get(nid) or (task.get('latest_run') or {}).get('error') or '请检查节点状态：' + task['title']
            else:
                message = '工作流尚有节点未交付，请检查依赖状态'
        engine._status(db, run['id'], 'blocked', message)
        return {'status': 'blocked', 'task_id': manual[1]['id'] if manual else ''}

    def pause(state):
        task_id = state.get('task_id')
        target = review_target(tasks.get(db, task_id)) if task_id else {'status': state['status']}
        interrupt(target)
        return {}

    graph.add_node('reset', reset)
    graph.add_node('dispatch', dispatch)
    graph.add_node('pause', pause)
    graph.add_edge(START, 'reset')
    graph.add_edge('reset', 'dispatch')
    graph.add_conditional_edges('dispatch', lambda state: state['status'], {
        'running': 'dispatch', 'awaiting_review': 'pause', 'blocked': 'pause',
        'delivered': END, 'cancelled': END,
    })
    graph.add_edge('pause', 'reset')
    return graph


def drive(db, run, execute_node):
    nodes = list(run['settings']['task_ids'].items())
    graph = _dag_graph(db, run, nodes, execute_node) if run['settings'].get('dag_scheduler') else _linear_graph(db, nodes, execute_node)
    config = {'configurable': {'thread_id': run['id']}, 'recursion_limit': max(50, len(nodes) * 4 + 10)}
    # LangGraph persists in background threads; never share the UI's SQLite connection.
    with closing(sqlite3.connect(db.path, check_same_thread=False, timeout=60)) as connection:
        saver = SqliteSaver(connection, serde=JsonPlusSerializer(allowed_msgpack_modules=None))
        compiled = graph.compile(checkpointer=saver)
        saved = compiled.get_state(config)
        pending = any(task.interrupts for task in saved.tasks)
        value = Command(resume=True) if pending else None if saved.next else {'status': 'delivered'}
        result = compiled.invoke(value, config)
        snapshot = compiled.get_state(config)
        run['settings']['checkpoint'] = {
            'thread_id': run['id'], 'checkpoint_id': snapshot.config['configurable']['checkpoint_id'],
            'next': list(snapshot.next), 'values': dict(snapshot.values),
            'interrupts': [item.value for task in snapshot.tasks for item in task.interrupts]}
        import workflow_tasks
        import workflow_execution
        latest = workflow_execution.detail(db, run['id'])
        latest['settings']['checkpoint'] = run['settings']['checkpoint']
        workflow_tasks._save(db, latest)
        return result
