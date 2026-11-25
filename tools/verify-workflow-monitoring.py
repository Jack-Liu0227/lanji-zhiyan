#!/usr/bin/env python3
"""Exercise a 12-node workflow through run_agent/runtime without patching either.

Fixture: .venv/Scripts/python.exe tools/verify-workflow-monitoring.py
         --report .codex-tasks/workflow-monitoring/fixture.json
Add --hold-seconds 125 for a simulated long HTTP wait, not a real-model test.

Explicit real mode (retains a NEW project and never approves S2):
  .venv/Scripts/python.exe tools/verify-workflow-monitoring.py --real
    --database EXISTING.sqlite3 --project-title "Synthetic acceptance"
    --report .codex-tasks/workflow-monitoring/real.json

Markdown is written by project_note_write. CSV exports come from model-produced
CSV blocks in immutable task outputs; the report records that provenance.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import quote
import uuid

REPO = Path(__file__).resolve().parents[1]
for package in ('database', 'workspace-core', 'workspace-service', 'connectors', 'ai-runtime'):
    sys.path.insert(0, str(REPO / 'packages' / package))
sys.path.insert(0, str(REPO))

NODES = ('S1', 'A1', 'A2', 'A3', 'N1', 'N2', 'N3', 'J1', 'A4', 'Q1', 'Q2', 'S2')
EDGES = (('S1', 'A1'), ('S1', 'A2'), ('S1', 'A3'), ('A1', 'N1'), ('A2', 'N2'),
         ('A3', 'N3'), ('N1', 'J1'), ('N2', 'J1'), ('N3', 'J1'), ('J1', 'A4'),
         ('A4', 'Q1'), ('A4', 'Q2'), ('Q1', 'S2'), ('Q2', 'S2'))
TOOLS = ('library_overview', 'project_note_list', 'project_note_read', 'workflow_context_read', 'project_note_write')
SYNTHETIC = 'SYNTHETIC ACCEPTANCE MATERIAL'
SAMPLES = (('T01', 2), ('T02', 3), ('T03', 5))


class VerificationFailure(RuntimeError):
    """Only verifier-owned, nonsecret facts may appear in these messages."""


def require(condition, message):
    if not condition:
        raise VerificationFailure(message)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def file_link(label, relative):
    return f'[{label}](<{quote(relative, safe="/")}>)'


def report_path(raw, token):
    target = Path(raw).expanduser().resolve()
    if target.suffix.lower() != '.json':
        target /= 'verification-' + token + '.json'
    require(target.is_relative_to(REPO), '--report must be inside an ignored repository directory')
    relative = target.relative_to(REPO)
    require(relative.parts[0].lower() not in ('plan', 'doc', 'docs'), 'Reports cannot live in Plan/doc/docs')
    result = subprocess.run(['git', 'check-ignore', '--quiet', '--', relative.as_posix()],
                            cwd=REPO, capture_output=True, check=False)
    require(result.returncode == 0, '--report must be Git-ignored')
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def model_configuration_digest(db):
    # Hash raw/encrypted settings; never report provider configuration or credentials.
    rows = {table: [list(row) for row in db.db.execute('SELECT * FROM ' + table + ' ORDER BY id')]
            for table in ('model_profiles', 'providers')}
    return digest(json.dumps(rows, sort_keys=True, default=str).encode())


def csv_text():
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(('sample', 'value', 'source'))
    writer.writerows((sample, value, 'synthetic') for sample, value in SAMPLES)
    return output.getvalue()


def fixture_answer(node):
    return (f'# {node}: {SYNTHETIC}\n\n'
            'Synthetic records were read and cross-checked. There are 3 records; the value sum is 10. '
            'These invented records are software test inputs, not scientific evidence.\n\n'
            '```csv\n' + csv_text() + '```\n')


class LocalSSEModel:
    """Serve streamed tool calls; tools and output freezing execute in the app."""
    def __init__(self, hold_seconds):
        self.hold_seconds = hold_seconds
        self.lock = threading.Lock()
        self.counts = Counter()
        self.requests = 0
        self.errors = 0
        self.held = False
        self.wait_started = threading.Event()
        self.wait_finished = threading.Event()
        self.fast_branch_delivered = threading.Event()
        self.wait_heartbeat_verified = threading.Event()
        self.stopping = threading.Event()

    def __enter__(self):
        fixture = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def packet(self, delta, finish=None):
                value = {'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
                self.wfile.write(('data: ' + json.dumps(value, ensure_ascii=False) + '\n\n').encode())
                self.wfile.flush()

            def do_POST(self):
                try:
                    require(self.path == '/v1/chat/completions', 'Fixture received an unexpected route')
                    size = int(self.headers.get('Content-Length', '0'))
                    require(0 < size <= 2_000_000, 'Fixture request size is invalid')
                    payload = json.loads(self.rfile.read(size))
                    require(payload.get('stream') is True, 'Runtime did not request OpenAI SSE')
                    users = [m['content'] for m in payload['messages'] if m.get('role') == 'user']
                    match = re.search(r'^ACCEPTANCE_SPEC=(\{[^\n]+\})', users[-1], re.M)
                    require(match is not None, 'Fixture cannot identify the current node')
                    spec = json.loads(match.group(1))
                    node = spec['node']
                    require(node in NODES, 'Fixture received an unknown node')
                    offered = {tool['function']['name'] for tool in payload.get('tools', [])}
                    require(offered <= set(TOOLS), 'Model was offered a tool outside the acceptance scope')
                    results = [m for m in payload['messages'] if m.get('role') == 'tool']
                    for message in results:
                        result = json.loads(message['content'])
                        require(isinstance(result, dict) and not result.get('error'), 'A workbench tool failed')
                    actions = [('library_overview', {}), ('project_note_read', {'path': spec['source']})]
                    if node == 'A2':
                        actions.extend(('project_note_read', {'path': path}) for path in spec['round_sources'])
                    actions.append(('project_note_write', {'path': spec['note'], 'content': fixture_answer(node),
                                                          'title': 'Synthetic acceptance ' + node}))
                    step = len(results)
                    with fixture.lock:
                        fixture.requests += 1
                        fixture.counts[node] += 1
                        hold = node == 'A2' and step == 0 and not fixture.held
                        if hold:
                            fixture.held = True
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                    self.send_header('Cache-Control', 'no-cache')
                    self.end_headers()
                    if hold:
                        fixture.wait_started.set()
                        started = time.monotonic()
                        deadline = started + fixture.hold_seconds + 30
                        while (time.monotonic() - started < fixture.hold_seconds or
                               not fixture.fast_branch_delivered.is_set() or not fixture.wait_heartbeat_verified.is_set()):
                            require(not fixture.stopping.is_set(), 'Fixture was stopped')
                            require(time.monotonic() < deadline, 'N1 did not deliver while A2 waited')
                            # Comments keep HTTP alive without producing model progress callbacks.
                            self.wfile.write(b': simulated fixture wait, no model output\n\n')
                            self.wfile.flush()
                            fixture.stopping.wait(.5)
                        fixture.wait_finished.set()
                        fixture.actual_wait_seconds = round(time.monotonic() - started, 3)
                    self.packet({'reasoning_content': 'Checking explicitly synthetic acceptance records. '})
                    if step < len(actions):
                        name, arguments = actions[step]
                        encoded = json.dumps(arguments, ensure_ascii=False)
                        middle = len(encoded) // 2
                        self.packet({'tool_calls': [{'index': 0, 'id': f'accept-{node}-{step}', 'type': 'function',
                            'function': {'name': name, 'arguments': encoded[:middle]}}]})
                        self.packet({'tool_calls': [{'index': 0, 'function': {'arguments': encoded[middle:]}}]}, 'tool_calls')
                    else:
                        answer = fixture_answer(node)
                        self.packet({'content': answer[:40]})
                        self.packet({'content': answer[40:]}, 'stop')
                    self.wfile.write(b'data: [DONE]\n\n')
                    self.wfile.flush()
                except Exception as exc:
                    with fixture.lock:
                        fixture.errors += 1
                        fixture.failure = str(exc) if isinstance(exc, VerificationFailure) else type(exc).__name__
                    try:
                        self.wfile.write(b'data: {"error":{"message":"Local acceptance fixture failed"}}\n\n')
                        self.wfile.flush()
                    except OSError:
                        pass

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name='acceptance-model-http')
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'
        return self

    def __exit__(self, *_args):
        self.stopping.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)


def prepare(db, root, args, token, model, report):
    import project_bindings
    import project_notes
    import workflow_layout
    import workflow_execution

    root.mkdir(parents=True, exist_ok=False)
    project_id = db.create_task(args.project_title + ' [' + token + ']', base_dir=str(root))
    report['project_id'] = project_id
    chat_id = db.create_chat('12节点工作流验收 · ' + token, task_id=project_id)
    report['origin_chat_id'] = chat_id
    unrelated = db.create_chat('Unrelated control conversation', task_id=project_id)
    require(db.db.execute('SELECT COUNT(*) FROM workflow_runs WHERE project_id=?', (project_id,)).fetchone()[0] == 0,
            'New acceptance project already contains workflow runs')
    notes = project_notes.ensure_categories(db, project_id).relative_to(root).as_posix()
    source = notes + '/acceptance/source.md'
    body = ('# ' + SYNTHETIC + '\n\nInvented records for software tests only. '
            'They are not publications, experiments, or research evidence.\n\n```csv\n' + csv_text() + '```\n')
    project_notes.write(db, project_id, source, body, create=True)
    round_sources = []
    for index in range(args.a2_rounds):
        relative = notes + f'/acceptance/round-{index + 1:02d}.md'
        project_notes.write(db, project_id, relative, body + f'\nCross-check pass {index + 1}; read each source separately.\n', create=True)
        round_sources.append(relative)
    assistant = 'monitor-accept-' + token
    db.assistant_save(assistant, name='Synthetic monitoring acceptance ' + token,
        enabled=1, model_id=model['id'], permission='workspace_write',
        rules='Use only supplied synthetic records and the granted project tools. Never access other projects, network, or credentials.')
    binding = project_bindings.register(db, project_id, 'assistant', assistant, tools=list(TOOLS))
    nodes = []
    for node in NODES:
        note = notes + '/acceptance/results/' + node + '.md'
        spec = {'node': node, 'source': source, 'round_sources': round_sources if node == 'A2' else [], 'note': note}
        prompt = ('ACCEPTANCE_SPEC=' + json.dumps(spec, ensure_ascii=False) + '\n'
            'This is a SYNTHETIC ACCEPTANCE MATERIAL software test. Use library_overview, then project_note_read '
            'on the source path above. Cross-check sample identifiers, values, count and sum; '
            'compare accepted upstream outputs when supplied. Do not invent research claims. '
            'Write a Markdown result with project_note_write at the exact note path above. '
            'Finish with a Markdown reply labelled SYNTHETIC ACCEPTANCE MATERIAL and exactly one ```csv block '
            'with header sample,value,source, all source rows, and source=synthetic. Preserve identifiers and values. '
            'State the sample count and sum outside the CSV. Use only this dedicated project and these note paths. '
            'Do not create tasks or workflows, run code, access the network, change settings, or approve reviews. ')
        if node == 'A2':
            prompt += (f'For A2 perform {args.a2_rounds} additional sequential model/tool rounds: read each distinct '
                       'round_sources path above with project_note_read in a SEPARATE round, compare each with the '
                       'source, and accumulate an audit checklist before writing. Do not batch those reads. ')
        nodes.append({'id': node, 'step': 'task', 'label': node + ' synthetic check', 'prompt': prompt,
            'executor_binding_id': binding['id'], 'tools': list(TOOLS), 'review_required': node == 'S2',
            'manual_review_required': node == 'S2', 'output_contract': {'min_chars': 80, 'required_files': [note]}})
    workflow_layout.ensure_schema(db)
    layout = workflow_layout.save_layout(db, {'name': '12节点合成材料验收 · ' + token,
        'nodes': nodes, 'edges': [{'source': start, 'target': end} for start, end in EDGES]})['layout']
    workflow_layout.select_layout(db, {'task_id': project_id, 'layout_id': layout['id']})
    body = {'project_id': project_id, 'chat_id': chat_id, 'template_id': layout['id'],
            'goal': 'Verify the synthetic 12-node workflow; S2 must await human review.',
            'idempotency_key': 'monitor-accept-' + token}
    run = workflow_execution.route('POST', '/api/workflow/execution/prepare', body, {}, db)['run']
    report['workflow_run_id'] = run['id']
    require(run['project_id'] == project_id and run['settings'].get('origin_chat_id') == chat_id,
            'Prepared run does not belong to the new project and origin chat')
    require(set(run['settings']['task_ids']) == set(NODES) and len(set(run['settings']['task_ids'].values())) == 12,
            'New workflow does not contain twelve distinct canonical node tasks')
    require(db.db.execute('SELECT COUNT(*) FROM workflow_runs WHERE project_id=?', (project_id,)).fetchone()[0] == 1,
            'Preparing the new acceptance workflow created unexpected runs')
    require(not db.messages(chat_id), 'New origin chat already contains messages')
    mode = ('真实模式：使用当前有效模型执行，A2 通过多轮材料读取进行交叉核对。' if args.real else
            f'本地 SSE 夹具模式：A2 模拟 HTTP 等待至少 {args.hold_seconds:g} 秒；这不是实际模型耗时。')
    introduction = (
        '# 本次12节点工作流验收\n\n'
        '本记录由验收脚本根据本次授权新建。目标是验证对话归属、分支调度、长任务心跳、'
        '只读轨迹、产物版本及重复执行的幂等性。此消息是验收说明，尚不代表验收通过。\n\n'
        f'{mode}\n\n'
        '流程：S1 → A1/A2/A3 → N1/N2/N3 → J1 → A4 → Q1/Q2 → S2。'
        '需要确认 A1 的后续节点 N1 能在 A2 完成前交付；S2 必须保留待人工核查状态。\n\n'
        f'运行标识：`{run["id"]}`。输入：{file_link("合成测试材料", source)}。'
        f'A2 将额外读取 {args.a2_rounds} 份独立测试材料。所有材料均明确标注为合成验收数据，不作为科研证据。\n\n'
        '执行工具限定为项目读取和 project_note_write。Markdown 由项目笔记工具写入；'
        'CSV 由验收脚本从实际模型回复中的 CSV 代码块导出，并记录对应任务尝试和产物修订。'
        '全部检查通过后，本记录才会追加实际结果和文件入口。'
    )
    message = db.add_message(chat_id, 'assistant', introduction, events=[], sender='验收说明')
    report['origin_messages'] = {'initial_message_id': message['id'], 'initial_content_sha256': digest(introduction.encode())}
    report['checks']['fresh_project_chat_and_workflow'] = True
    return {'project_id': project_id, 'chat_id': chat_id, 'unrelated': unrelated,
            'run_id': run['id'], 'task_ids': run['settings']['task_ids'], 'prepare_body': body}


def agent_get(db, action, **query):
    import agent_settings
    return agent_settings.route('GET', '/api/agent-settings/' + action, {},
                                {key: [value] for key, value in query.items()}, db)


def trajectory(db, state, chat_id, run_id, after=0, limit=500):
    return agent_get(db, 'trajectory/events', project_id=state['project_id'], chat_id=chat_id,
                     run_id=run_id, after=after, limit=limit)


def all_events(db, state, chat_id, run_id):
    events, after = [], 0
    while True:
        page = trajectory(db, state, chat_id, run_id, after=after, limit=7)
        events.extend(page['events'])
        if not page['has_more']:
            return events
        require(page['next_after'] > after, 'Trajectory pagination stopped advancing')
        after = page['next_after']


def drive(db, state, args, fixture, report):
    import project_tasks as tasks
    import workflow
    import workflow_execution

    stopped, finished = threading.Event(), threading.Event()
    failures = []
    def advance():
        try:
            with workflow.WorkflowDB(db.path) as worker_db:
                workflow_execution.advance(worker_db, state['run_id'], stopped.is_set)
        except BaseException as exc:
            failures.append(type(exc).__name__)
        finally:
            finished.set()
    thread = threading.Thread(target=advance, daemon=True, name='acceptance-workflow-driver')
    thread.start()
    samples = []
    deadline = time.monotonic() + args.timeout_seconds
    try:
        while not finished.is_set():
            require(time.monotonic() < deadline, 'Acceptance workflow timed out')
            first = tasks.get(db, state['task_ids']['N1'])
            if fixture and first['status'] == 'delivered':
                fixture.fast_branch_delivered.set()
            a2 = tasks.get(db, state['task_ids']['A2'])
            if a2['latest_run_id']:
                page = trajectory(db, state, a2['chat_id'], a2['latest_run_id'])
                value = page.get('monitoring') or {}
                if value and (not samples or value != samples[-1]['monitoring']):
                    samples.append({'observed_at': utc_now(), 'status': page['status'], 'monitoring': value,
                        'fixture_waiting': bool(fixture and fixture.wait_started.is_set() and not fixture.wait_finished.is_set())})
                    if fixture and samples[-1]['fixture_waiting'] and value.get('worker_alive'):
                        if any(old['fixture_waiting'] and old['monitoring'].get('worker_alive')
                               and old['monitoring']['heartbeat_at'] != value['heartbeat_at']
                               and old['monitoring']['last_progress_at'] == value['last_progress_at'] for old in samples[:-1]):
                            fixture.wait_heartbeat_verified.set()
            finished.wait(.2)
    finally:
        stopped.set()
        if fixture and not finished.is_set():
            fixture.stopping.set()
        thread.join(15)
        report['monitoring_samples'] = samples
    require(not thread.is_alive(), 'Acceptance worker did not drain after stopping')
    require(not failures, 'Acceptance driver failed')
    run = workflow_execution.detail(db, state['run_id'])
    report['workflow_status'] = run['status']
    report['node_statuses'] = {node: tasks.get(db, task_id)['status'] for node, task_id in state['task_ids'].items()}
    require(run['status'] == 'awaiting_review', 'Workflow did not stop at final human review')
    first = tasks.get(db, state['task_ids']['N1'])['latest_run']
    slow = tasks.get(db, state['task_ids']['A2'])['latest_run']
    require(first['ended_at'] < slow['ended_at'], 'N1 waited for the unrelated A2 branch')
    report['branch_order'] = {'N1_ended_at': first['ended_at'], 'A2_ended_at': slow['ended_at'], 'fast_branch_first': True}
    waiting = [sample['monitoring'] for sample in samples if sample['status'] == 'running'
               and (sample['fixture_waiting'] if fixture else True) and sample['monitoring'].get('worker_alive')]
    require(len({sample['heartbeat_at'] for sample in waiting}) >= 2, 'No advancing live heartbeat was observed')
    if fixture:
        require(any(a['heartbeat_at'] != b['heartbeat_at'] and a['last_progress_at'] == b['last_progress_at']
                    for a in waiting for b in waiting), 'HTTP waiting was confused with output progress')
        require(fixture.errors == 0, 'Local SSE fixture reported a failure')
    report['checks']['heartbeat'] = True
    report['checks']['heartbeat_independent_of_output'] = True if fixture else 'not asserted for real model output'


def execution_counts(db, state):
    ids = json.dumps(list(state['task_ids'].values()))
    queries = {
        'attempts': 'SELECT COUNT(*) FROM project_task_runs WHERE task_id IN (SELECT value FROM json_each(?))',
        'events': 'SELECT COUNT(*) FROM execution_conversation_events WHERE task_id IN (SELECT value FROM json_each(?))',
        'model_requests': "SELECT COUNT(*) FROM execution_conversation_events WHERE task_id IN (SELECT value FROM json_each(?)) AND json_extract(payload,'$.type')='model_request'",
        'output_versions': 'SELECT COUNT(*) FROM project_task_output_versions WHERE task_id IN (SELECT value FROM json_each(?))',
    }
    return {kind: db.db.execute(sql, (ids,)).fetchone()[0] for kind, sql in queries.items()}


def verify(db, root, state, fixture, args, report, evidence):
    import asset_service
    import project_task_execution
    import project_tasks as tasks
    import task_output_versions
    import workflow_execution
    import workflow_file_preview

    project_id, origin = state['project_id'], state['chat_id']
    run = workflow_execution.detail(db, state['run_id'])
    overview = run['chat_id']
    for chat in (origin, overview, tasks.get(db, state['task_ids']['A1'])['chat_id']):
        context = agent_get(db, 'workflow-context', project_id=project_id, chat_id=chat)
        require(context['run_ids'] == [state['run_id']], 'Context selected the wrong workflow')
        require(set(context['task_ids']) == set(state['task_ids'].values()), 'Context omitted canonical nodes')
    for chat in ('', state['unrelated']):
        context = agent_get(db, 'workflow-context', project_id=project_id, chat_id=chat)
        require(not context['run_ids'] and not context['task_ids'], 'Unrelated context fell back to a project workflow')
    report['checks']['conversation_context'] = True
    exports = root / 'acceptance-exports'
    exports.mkdir(exist_ok=False)
    evidence.mkdir(parents=True, exist_ok=False)
    report['artifacts'], report['nodes'] = [], []
    for node, task_id in state['task_ids'].items():
        task = tasks.get(db, task_id)
        attempt = task['latest_run']
        require(len(task['runs']) == 1, 'Duplicate task attempt at ' + node)
        require(task['status'] == ('awaiting_review' if node == 'S2' else 'delivered'), 'Unexpected task state at ' + node)
        require(set(task['scope']['tools']) == set(TOOLS), 'Task tool scope changed at ' + node)
        revision = attempt['checkpoint'].get('output_revision', 0)
        saved = db.db.execute('SELECT output_snapshot FROM project_task_output_versions WHERE task_run_id=? AND revision=?',
                              (attempt['id'], revision)).fetchone()
        require(saved is not None and json.loads(saved['output_snapshot']) == attempt['output_snapshot'], 'Output version not frozen at ' + node)
        require(task_output_versions.read(db, task, attempt, revision)['output_snapshot'] == attempt['output_snapshot'], 'Version lookup mismatch at ' + node)
        events = all_events(db, state, task['chat_id'], attempt['id'])
        require(events == all_events(db, state, origin, attempt['id']), 'Origin trajectory differs at ' + node)
        results = [item['payload'] for item in events if item['kind'] == 'tool_result']
        counts = Counter(item.get('name') for item in results)
        require(all(item.get('ok') for item in results), 'Workbench tool failed at ' + node)
        require(counts['library_overview'] and counts['project_note_read'] and counts['project_note_write'], 'Missing real workbench tool at ' + node)
        require(set(counts) <= set(TOOLS), 'Tool escaped the acceptance scope at ' + node)
        for item in events:
            payload = item['payload']
            require((payload.get('workflow_run_id'), payload.get('node_id'), payload.get('task_id'),
                     payload.get('task_run_id'), payload.get('attempt')) ==
                    (state['run_id'], node, task_id, attempt['id'], 1), 'Event lineage mismatch at ' + node)
            require(payload.get('agent_run_id'), 'Event has no runtime agent association at ' + node)
            if item['kind'] == 'model_request':
                require(not set(payload) & {'messages', 'request', 'request_body', 'body', 'headers'}, 'Request body leaked at ' + node)
            if item['kind'] == 'reasoning_done':
                require(payload.get('text') == payload.get('content'), 'Reasoning projection differs at ' + node)
        if node == 'A2':
            reads = {item['payload']['args'].get('path') for item in events
                     if item['kind'] == 'tool_call' and item['payload'].get('name') == 'project_note_read'}
            rounds = {item['payload'].get('round') for item in events if item['kind'] == 'model_request'}
            require(len(reads) >= args.a2_rounds + 1 and len(rounds) >= args.a2_rounds + 2,
                    'A2 did not perform distinct sequential reading rounds')
        page = trajectory(db, state, task['chat_id'], attempt['id'])
        require(not page['monitoring']['worker_alive'], 'Terminal attempt still reports a live worker at ' + node)
        for index, item in enumerate(attempt['output_snapshot']['files']):
            path = Path(item['path']).resolve()
            require(path.is_relative_to(root) and path.is_file(), 'Artifact escaped the new project at ' + node)
            require(digest(path.read_bytes()) == item['content_hash'], 'Frozen artifact hash differs at ' + node)
            if item.get('original_path'):
                require(digest(Path(item['original_path']).read_bytes()) == item['content_hash'], 'Original artifact changed at ' + node)
            preview = workflow_file_preview.preview(db, project_id, origin, task_id=task_id,
                task_run_id=attempt['id'], output_revision=revision, artifact_id='file:' + str(index))
            require(preview['kind'] == 'markdown', 'Markdown version preview failed at ' + node)
            destination = evidence / (node + '-' + str(index) + '.md')
            shutil.copyfile(path, destination)
            report['artifacts'].append({'node': node, 'kind': 'markdown', 'evidence_file': str(destination.relative_to(REPO)),
                'project_relative': path.relative_to(root).as_posix(),
                'sha256': item['content_hash'], 'task_run_id': attempt['id'], 'output_revision': revision})
        text = attempt['output_snapshot']['content']
        require(isinstance(text, str) and SYNTHETIC in text, 'Synthetic material label missing at ' + node)
        blocks = re.findall(r'```csv\s*\n(.*?)```', text, re.S | re.I)
        require(len(blocks) == 1, 'Expected one model-produced CSV block at ' + node)
        reader = csv.DictReader(io.StringIO(blocks[0].strip()))
        require(reader.fieldnames == ['sample', 'value', 'source'], 'CSV schema differs at ' + node)
        rows = list(reader)
        require(len(rows) == 3 and {(row['sample'], Decimal(row['value'])) for row in rows} ==
                {(sample, Decimal(value)) for sample, value in SAMPLES} and
                all(row['source'] == 'synthetic' for row in rows), 'Synthetic CSV cross-check failed at ' + node)
        require(sum(Decimal(row['value']) for row in rows) == 10, 'Synthetic CSV sum differs at ' + node)
        export = exports / (node + '.csv')
        export.write_text(blocks[0].strip() + '\n', encoding='utf-8', newline='')
        asset_service.register_file(db, export, project_id=project_id, kind='document', source='acceptance_csv_export',
            metadata={'task_id': task_id, 'task_run_id': attempt['id'], 'output_revision': revision,
                      'exporter': 'verify-workflow-monitoring', 'source_content_sha256': digest(text.encode())})
        preview = workflow_file_preview.preview(db, project_id, origin, relative=export.relative_to(root).as_posix())
        require(preview['kind'] == 'table' and preview['total_rows'] == 3, 'CSV preview failed at ' + node)
        destination = evidence / (node + '.csv')
        shutil.copyfile(export, destination)
        report['artifacts'].append({'node': node, 'kind': 'csv', 'evidence_file': str(destination.relative_to(REPO)),
            'project_relative': export.relative_to(root).as_posix(),
            'sha256': digest(export.read_bytes()), 'task_run_id': attempt['id'], 'output_revision': revision,
            'exporter': 'verifier exports model-produced CSV block', 'source_content_sha256': digest(text.encode())})
        report['nodes'].append({'node': node, 'task_id': task_id, 'task_run_id': attempt['id'], 'chat_id': task['chat_id'],
            'status': task['status'], 'attempts': len(task['runs']), 'output_revision': revision,
            'agent_run_id': attempt['checkpoint']['agent_run_id'], 'event_count': len(events), 'tool_results': dict(counts)})
    final = tasks.get(db, state['task_ids']['S2'])
    require(tasks.manual_review_required(final, final['latest_run']), 'S2 is not protected by manual review')
    require(not final['accepted_delivery_id'] and not final['deliveries'], 'S2 was accepted without a human')
    report['review_target'] = {'task_id': final['id'], 'task_run_id': final['latest_run_id'], 'chat_id': final['chat_id'],
                               'output_revision': final['latest_run']['checkpoint'].get('output_revision', 0), 'approved': False}
    report['checks'].update(trajectory_lineage=True, reasoning_projection=True, artifact_versions=True,
                            csv_values_and_preview=True, final_human_review=True)
    before = execution_counts(db, state)
    request_count = fixture.requests if fixture else before['model_requests']
    changes = db.db.total_changes
    db.db.execute('PRAGMA query_only=ON')
    try:
        for _ in range(3):
            for node in report['nodes']:
                all_events(db, state, node['chat_id'], node['task_run_id'])
            for chat in (origin, overview):
                agent_get(db, 'trajectory', project_id=project_id, chat_id=chat, limit=50)
                agent_get(db, 'workflow-context', project_id=project_id, chat_id=chat)
    finally:
        db.db.execute('PRAGMA query_only=OFF')
    require(db.db.total_changes == changes, 'Read-only trajectory modified the database')
    require(execution_counts(db, state) == before, 'Read-only trajectory changed execution counts')
    repeated = workflow_execution.route('POST', '/api/workflow/execution/prepare', state['prepare_body'], {}, db)['run']
    require(repeated['id'] == state['run_id'], 'Repeated prepare created a second workflow')
    require(workflow_execution.advance(db, state['run_id'])['status'] == 'awaiting_review', 'Repeated advance bypassed S2')
    for task_id in state['task_ids'].values():
        attempt = tasks.get(db, task_id)['latest_run']
        replay = tasks.start(db, task_id, authority=tasks.CommandContext.ui(project_id), request_key=attempt['request_key'])
        require(replay['id'] == attempt['id'], 'Repeated task start created an attempt')
        project_task_execution.execute(db, attempt['id'])
    require(execution_counts(db, state) == before, 'Idempotent replay changed execution counts')
    require((fixture.requests if fixture else execution_counts(db, state)['model_requests']) == request_count,
            'Read-only/replay checks issued another model request')
    report['counts'] = before
    report['checks'].update(read_only_queries_no_writes=True, read_only_queries_no_model_requests=True, idempotency=True)


def publish_result(db, root, state, report):
    """Append a verifier summary to this run's new origin, without event fabrication."""
    import project_tasks as tasks
    import workflow_execution
    import workflow_file_preview

    chat_id, project_id = state['chat_id'], state['project_id']
    chat = db.chat(chat_id)
    require(chat['task_id'] == project_id, 'Result message chat is outside the new acceptance project')
    messages = db.messages(chat_id)
    require(len(messages) == 1 and messages[0]['id'] == report['origin_messages']['initial_message_id'],
            'Origin chat changed before writing the acceptance result')
    run = workflow_execution.detail(db, state['run_id'])
    counts = execution_counts(db, state)
    require(counts == report['counts'], 'Execution counts changed before publishing the result')
    require(run['status'] == 'awaiting_review', 'Final workflow is no longer awaiting human review')
    final = tasks.get(db, state['task_ids']['S2'])
    require(final['status'] == 'awaiting_review' and not final['accepted_delivery_id'], 'S2 is no longer pending human review')
    rows, links = [], []
    labels = {'delivered': '已交付', 'awaiting_review': '待人工核查'}
    for node in report['nodes']:
        artifacts = [item for item in report['artifacts'] if item['node'] == node['node']]
        markdown = next(item for item in artifacts if item['kind'] == 'markdown')
        table = next(item for item in artifacts if item['kind'] == 'csv')
        for item in (markdown, table):
            relative = item['project_relative']
            path = (root / relative).resolve()
            require(path.is_relative_to(root) and path.is_file() and digest(path.read_bytes()) == item['sha256'],
                    'Published file differs from the verified artifact')
            preview = workflow_file_preview.preview(db, project_id, chat_id, relative=relative)
            require(preview['kind'] == ('markdown' if item['kind'] == 'markdown' else 'table'), 'Published link cannot be previewed')
            links.append(relative)
        current = tasks.get(db, node['task_id'])
        require(current['status'] == node['status'], 'Node status changed before publishing the result')
        rows.append(f'| {node["node"]} | {labels.get(current["status"], current["status"])} | '
                    f'{file_link("Markdown", markdown["project_relative"])} | {file_link("CSV", table["project_relative"])} |')
    mode = ('本次使用真实模型执行。' if report['mode'] == 'real' else
            '本次使用本地 HTTP SSE 模型夹具；A2 的 HTTP 等待为夹具模拟，不代表实际模型延迟。')
    content = (
        '# 验收检查通过，S2 等待人工核查\n\n'
        f'{mode}此结果由验收脚本读取已落库的运行、任务、事件和产物生成。\n\n'
        f'工作流运行：`{run["id"]}`。当前状态：`{run["status"]}`。'
        f'共有 {len(report["nodes"])} 个节点、{counts["attempts"]} 次任务尝试、'
        f'{counts["events"]} 条执行事件、{counts["model_requests"]} 次模型请求和 '
        f'{counts["output_versions"]} 个产物版本。\n\n'
        '已检查 N1 先于 A2 完成、心跳更新、对话归属、轨迹关联、版本哈希、只读查询和幂等重放。\n\n'
        '下表链接均指向本次项目中已生成并核对哈希的文件。Markdown 为冻结的笔记产物；'
        'CSV 从对应任务的实际最终回复导出，不是模型直接写入的 CSV 文件。\n\n'
        '| 节点 | 当前状态 | Markdown 产物 | CSV 产物 |\n| --- | --- | --- | --- |\n'
        + '\n'.join(rows) + '\n\n'
        f'最终 S2 尚未批准。请在本项目任务列表打开 S2 节点，核对 Markdown、CSV 和执行轨迹后决定是否通过。'
        f'任务标识：`{final["id"]}`；尝试：`{final["latest_run_id"]}`；'
        f'修订：v{report["review_target"]["output_revision"]}。\n\n'
        '本说明没有新增或改写模型、工具执行轨迹。'
    )
    message = db.add_message(chat_id, 'assistant', content, events=[], sender='验收结果')
    messages = db.messages(chat_id)
    require(len(messages) == 2 and messages[-1]['id'] == message['id'] and all(not item['events'] for item in messages),
            'Origin acceptance messages contain unexpected events')
    require(execution_counts(db, state) == counts, 'Publishing the result changed execution history')
    report['origin_messages'].update(result_message_id=message['id'], result_content_sha256=digest(content.encode()),
                                     message_count=len(messages), verified_project_links=len(links))
    report['checks']['origin_messages_and_project_links'] = True


def arguments():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--report', required=True, help='Git-ignored JSON path or directory inside this repository')
    parser.add_argument('--hold-seconds', type=float, default=8, help='Fixture-only simulated A2 HTTP wait, minimum 6 (default 8)')
    parser.add_argument('--a2-rounds', type=int, default=8, help='Distinct extra A2 source-reading rounds (default 8)')
    parser.add_argument('--timeout-seconds', type=float, default=None, help='Workflow deadline (default: fixture hold+180; real 1200)')
    parser.add_argument('--real', action='store_true', help='Explicitly call the current model in an existing database')
    parser.add_argument('--database', type=Path, help='Existing database, required with --real; never used by fixture mode')
    parser.add_argument('--project-title', default='Synthetic workflow monitoring acceptance', help='New project title; a unique suffix is always added')
    args = parser.parse_args()
    require(args.hold_seconds >= 6 and args.hold_seconds <= 3600, '--hold-seconds must be between 6 and 3600')
    require(4 <= args.a2_rounds <= 30, '--a2-rounds must be between 4 and 30')
    require(0 < len(args.project_title.strip()) <= 100, '--project-title must contain 1 to 100 characters')
    require(args.real == (args.database is not None), '--database is only allowed, and required, with --real')
    if args.real:
        args.database = args.database.expanduser().resolve()
        require(args.database.is_file(), '--database must name an existing database')
    args.timeout_seconds = args.timeout_seconds or (1200 if args.real else args.hold_seconds + 180)
    require(args.timeout_seconds > 0, '--timeout-seconds must be positive')
    return args


def main():
    started = time.monotonic()
    token = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]
    report = {'schema_version': 1, 'status': 'failed', 'started_at': utc_now(), 'checks': {}, 'stage': 'arguments'}
    destination = None
    fixture = None
    # App/provider diagnostics can include request details. Suppress them, never copy them to the report.
    diagnostics = io.StringIO()
    try:
        args = arguments()
        destination = report_path(args.report, token)
        report.update(mode='real' if args.real else 'local_http_sse_fixture',
            real_model_called=False, fixture_simulated_wait=not args.real,
            wait_description='Simulated local HTTP wait; not real-model latency' if not args.real else 'Real model/tool rounds; no artificial sleep',
            hold_seconds=args.hold_seconds if not args.real else None, a2_extra_reading_rounds=args.a2_rounds,
            tool_scope=list(TOOLS), project_retained=args.real, stage='initialization')
        with redirect_stdout(diagnostics), redirect_stderr(diagnostics), ExitStack() as stack:
            if args.real:
                database = args.database
                os.environ['LANJI_ZHIYAN_DB'] = str(database)
                # Keep a unique persistent project beside the explicit DB, including on failure.
                # mkdir(exist_ok=False) in prepare refuses to reuse any previous directory.
                root = database.parent / 'projects' / ('workflow-monitoring-acceptance-' + token)
                report['project_directory'] = str(root)
            else:
                temporary = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='lanji-monitor-acceptance-', ignore_cleanup_errors=True)))
                database = temporary / 'acceptance.sqlite3'
                os.environ['LANJI_ZHIYAN_HOME'] = str(temporary / 'home')
                os.environ['LANJI_ZHIYAN_DATA'] = str(temporary / 'home' / 'data')
                os.environ['LANJI_ZHIYAN_DB'] = str(database)
                root = temporary / 'project'
                fixture = stack.enter_context(LocalSSEModel(args.hold_seconds))
            import workflow
            with workflow.WorkflowDB(database) as db:
                if fixture:
                    model = db.add_model('Local SSE acceptance fixture', fixture.url, 'acceptance-fixture', 'local-fixture-only', activate=True)
                else:
                    report['stage'] = 'model_selection'
                    model = db.active_model()
                    require(model is not None, 'No usable current model exists in --database; configure one in the app first')
                    require(model.get('api', 'openai-completions') in workflow.USABLE_WIRES, 'Current model protocol is not executable')
                configuration = model_configuration_digest(db)
                report['model'] = {'id': model['id'], 'wire': model.get('api', 'openai-completions')}
                report['stage'] = 'prepare'
                state = prepare(db, root.resolve(), args, token, model, report)
                report.update(project_id=state['project_id'], origin_chat_id=state['chat_id'], workflow_run_id=state['run_id'])
                if args.real:
                    report['project_directory'] = str(root)
                report['stage'] = 'execute_and_monitor'
                try:
                    drive(db, state, args, fixture, report)
                finally:
                    report['real_model_called'] = bool(args.real and execution_counts(db, state)['model_requests'])
                report['stage'] = 'verify_artifacts_and_replay'
                evidence = destination.parent / (destination.stem + '-artifacts-' + token)
                verify(db, root.resolve(), state, fixture, args, report, evidence)
                require(model_configuration_digest(db) == configuration, 'Existing model/provider configuration changed')
                report['checks']['model_configuration_unchanged'] = True
                report['stage'] = 'publish_origin_result'
                publish_result(db, root.resolve(), state, report)
                if fixture:
                    report['fixture'] = {'http_requests': fixture.requests, 'requests_by_node': dict(fixture.counts),
                        'streaming': True, 'errors': fixture.errors, 'synthetic_wait': True,
                        'actual_wait_seconds': fixture.actual_wait_seconds}
            report.update(status='passed', stage='complete')
    except (Exception, KeyboardInterrupt) as exc:
        report['failure_type'] = type(exc).__name__
        report['failure'] = str(exc) if isinstance(exc, VerificationFailure) else 'Execution failed; raw diagnostics suppressed to protect request/configuration data'
        if fixture and fixture.errors:
            report['fixture_failure'] = getattr(fixture, 'failure', 'Local fixture failed')
    report.update(finished_at=utc_now(), elapsed_seconds=round(time.monotonic() - started, 3))
    if destination:
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary = {key: report[key] for key in ('status', 'stage', 'elapsed_seconds')}
    if destination:
        summary['report'] = str(destination.relative_to(REPO))
    if report['status'] != 'passed':
        summary['failure'] = report['failure']
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
