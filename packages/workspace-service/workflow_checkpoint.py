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


def drive(db, run, execute_node):
    graph = StateGraph(Cursor)
    nodes = list(run['settings']['task_ids'].items())
    for index, (nid, task_id) in enumerate(nodes):
        execute, review = 'execute_' + str(index), 'review_' + str(index)
        following = 'execute_' + str(index + 1) if index + 1 < len(nodes) else END

        def work(state, node_id=nid, project_task_id=task_id):
            return {**execute_node(node_id, project_task_id), 'node_id': node_id, 'task_id': project_task_id}

        def gate(state, project_task_id=task_id):
            # Resuming replays this function. All execution and file writes live in work().
            if state['status'] != 'delivered':
                interrupt(review_target(tasks.get(db, project_task_id)))
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
