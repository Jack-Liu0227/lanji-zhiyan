"""Actual saved output visibility, interruption recovery, and context identity."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_task_orchestration as orchestration
from tests._pdf_fixture import write_text_pdf
import asset_service
import papertran
import project_tasks as tasks
import project_task_execution as worker
import workflow_execution as engine
import workflow_node_workbench as nodes
import node_output_editors
import workflow_state
import workflow_tasks
import workflow_context
import workflow
import project_task_routes


class WorkflowArtifactWorkspaceTest(unittest.TestCase):
    setUp = orchestration.TaskOrchestrationTest.setUp
    layout = orchestration.TaskOrchestrationTest.layout

    def test_each_research_stage_selects_its_typed_workbench_editor(self):
        expected = {
            'idea': 'idea', 'search': 'table', 'download': 'papers', 'notes': 'bilingual',
            'outline': 'text', 'map': 'evidence_graph', 'write': 'text', 'review': 'checklist',
            'entrans': 'bilingual', 'convert': 'files',
        }

        for stage, editor in expected.items():
            with self.subTest(stage=stage):
                self.assertEqual(node_output_editors.describe({'content': 'stage output'}, {'step': stage}), editor)

    def test_literature_revision_keeps_sources_read_only_and_exclusions(self):
        baseline = [
            {'id': 'paper-1', 'task_id': self.pid, 'source': 'crossref', 'title': 'First paper', 'doi': '10.1/1'},
            {'id': 'paper-2', 'task_id': self.pid, 'source': 'arxiv', 'title': 'Second paper', 'doi': '10.1/2'},
        ]
        reviewed = node_output_editors.parse_content('search', [
            {**baseline[0], 'included': True, 'reason': 'In scope'},
            {**baseline[1], 'included': False, 'reason': 'Wrong topic', 'annotation': 'Manual check'},
        ], baseline, keep_excluded=True)
        self.assertEqual([row['id'] for row in reviewed], ['paper-1', 'paper-2'])
        self.assertEqual(reviewed[1]['annotation'], 'Manual check')
        self.assertEqual([row['id'] for row in reviewed if row['included']], ['paper-1'])
        with self.assertRaisesRegex(ValueError, '只读'):
            node_output_editors.parse_content('search', [{**baseline[0], 'source': 'manual'}], baseline)
        with self.assertRaisesRegex(ValueError, '唯一的记录 ID'):
            node_output_editors.parse_content('search', [{'id': 'unrelated', 'title': 'New record'}], baseline)

    def test_search_checkpoint_falls_back_to_saved_rows_without_cross_project_leakage(self):
        current = {'id': 'current-paper', 'task_id': self.pid, 'title': 'Current search result'}
        foreign = {'id': 'foreign-paper', 'task_id': 'another-project', 'title': 'Foreign result'}
        checkpoint = {'done_tools': {'paper_search\0{}': {
            'matches': [], 'saved': [current, foreign]}}}

        self.assertEqual(workflow_state.search_rows(self.pid, checkpoint), [current])
        self.assertEqual(workflow_state.search_result_rows(self.pid, {
            'matches': [current], 'saved': [foreign]}), [current])

    def test_reading_capture_binds_each_pdf_and_note_to_its_paper(self):
        folder = self.db.pdir(self.pid, '文献库')
        expected = {}
        for name, pages in (('first', ['Source page one', 'Source page two']), ('second', ['Another source'])):
            source = write_text_pdf(folder / (name + '.pdf'), pages)
            paper = self.db.ingest_pdf(self.pid, source)
            note = self.db.add_note(self.pid, name + ' evidence', name + ' evidence text', paper['id'])
            markdown = self.db.save_md_note('', name + ' reading note', name + ' Markdown text', self.pid, paper['id'])
            dual, mono = papertran.out_paths(source, folder)
            write_text_pdf(mono, ['Translated page ' + str(index) for index in range(len(pages))])
            write_text_pdf(dual, ['Stale dual PDF'])
            papertran._save_state(folder, paper['id'], len(pages), len(pages), True,
                                  identity='active', completed_pages=list(range(1, len(pages) + 1)))
            expected[paper['id']] = (source, mono, note, markdown, len(pages))

        capture = workflow_state.capture(self.db, self.pid, 'notes')

        self.assertTrue(capture['valid'])
        self.assertEqual({row['id'] for row in capture['content']}, expected.keys())
        self.assertEqual({file['path'] for file in capture['file_versions']},
                         {str(path.resolve()) for source, mono, *_ in expected.values() for path in (source, mono)})
        for row in capture['content']:
            source, mono, note, markdown, count = expected[row['id']]
            self.assertEqual(row['source_path'], str(source.resolve()))
            self.assertEqual(row['translation_path'], str(mono.resolve()))
            self.assertEqual(row['dual_path'], '')
            self.assertEqual(row['page_map'], [{'source': page, 'translation': page} for page in range(1, count + 1)])
            self.assertEqual(row['reading_notes'], [
                {'id': note['id'], 'title': note['title'], 'markdown': note['markdown']},
                {'id': markdown['id'], 'title': markdown['title'], 'markdown': markdown['content']},
            ])

    def test_reading_revision_only_changes_markdown(self):
        baseline = [{'id': 'first', 'source_path': 'original.pdf', 'translation_path': 'translation.pdf',
                     'page_map': [{'source': 1, 'translation': 2}],
                     'reading_notes': [{'id': 'note-1', 'title': 'Evidence', 'markdown': 'Original note'}]},
                    {'id': 'second', 'reading_notes': [{'id': 'note-2', 'title': 'Reading note', 'markdown': ''}]}]
        edited = deepcopy(baseline)
        edited[0]['reading_notes'][0]['markdown'] = '# Revised note'
        self.assertEqual(node_output_editors.parse_content('notes', edited, baseline), edited)
        for field, value in (('id', 'foreign'), ('source_path', 'other.pdf'), ('translation_path', 'other.pdf'),
                             ('page_map', [{'source': 1, 'translation': 1}]), ('included', False)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                node_output_editors.parse_content('notes', [{**edited[0], field: value}, edited[1]], baseline)
        with self.assertRaises(ValueError):
            node_output_editors.parse_content('notes', edited[:1], baseline)
        edited[0]['reading_notes'][0]['id'] = 'note-2'
        with self.assertRaises(ValueError):
            node_output_editors.parse_content('notes', edited, baseline)

    def test_review_revision_preserves_issue_identity_and_original_location(self):
        baseline = {'summary': 'Check figures', 'issues': [
            {'id': 'figure', 'detail': 'Missing label', 'location': {'section': 'Results', 'page': 3}},
            {'id': 'citation', 'detail': 'Check DOI', 'location': {'section': 'References', 'page': 7}},
        ]}
        revised = deepcopy(baseline)
        revised['issues'][0].update(detail='Add panel labels', resolved=True, comment='Matching figure checked')
        self.assertEqual(node_output_editors.parse_content('review', revised, baseline), revised)
        for changes in ({'id': 'different'}, {'location': {'page': 9}}, {'resolved': 'yes'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                candidate = deepcopy(revised)
                candidate['issues'][0].update(changes)
                node_output_editors.parse_content('review', candidate, baseline)

    def test_translation_revision_preserves_source_and_segment_alignment(self):
        baseline = {'source': 'Original', 'result': 'Initial', 'segments': [
            {'id': 'intro', 'source': 'Original', 'target': 'Initial', 'location': {'paragraph': 1}},
        ]}
        revised = deepcopy(baseline)
        revised['result'] = revised['segments'][0]['target'] = 'Revised'
        self.assertEqual(node_output_editors.parse_content('entrans', revised, baseline), revised)
        with self.assertRaises(ValueError):
            node_output_editors.parse_content('entrans', {**revised, 'result': 'Out of sync'}, baseline)
        with self.assertRaises(ValueError):
            node_output_editors.parse_content('entrans', {**revised, 'source': 'Tampered'}, baseline)
        revised['segments'][0]['location']['paragraph'] = 2
        with self.assertRaises(ValueError):
            node_output_editors.parse_content('entrans', revised, baseline)

    def running(self):
        self.layout(['search', 'task'])
        flow = engine.create(self.db, self.pid)
        task = tasks.get(self.db, flow['settings']['task_ids']['n0'])
        run = tasks.start(self.db, task['id'], authority=tasks.CommandContext.ui(self.pid))
        tasks.claim(self.db, run['id'])
        workflow_tasks._record_attempt(self.db, flow, 'n0', task, run, {})
        return flow, tasks.get(self.db, task['id']), run

    def saved(self, task, run):
        root = tasks._root(self.db, self.pid)
        path = root / 'saved-report.md'
        path.write_text('# Actually saved evidence', encoding='utf-8')
        asset = asset_service.register_file(self.db, path, project_id=self.pid,
            metadata={'project_task_id': task['id'], 'task_run_id': run['id']})
        rid = self.db.cache_result(self.pid, {'title': 'Owned search result', 'source': 'manual'})
        self.db.cache_result(self.pid, {'title': 'Unrelated existing paper', 'source': 'manual'})
        row = next(r for r in self.db.results(self.pid) if r['id'] == rid)
        tasks.save_checkpoint(self.db, run['id'], phase='model', output_assets=[asset['id']],
            done_tools={'paper_search\0{}': {'saved': [row]}},
            input={'goal': 'Complete the research objective'}, messages=[{'role': 'user', 'content': 'full context ' * 10000}])
        return nodes.Selection(self.pid, task['id']), asset

    def test_prepared_node_without_run_has_a_provisional_workspace(self):
        self.layout(['search', 'task'])
        flow = engine.create(self.db, self.pid)
        task = tasks.get(self.db, flow['settings']['task_ids']['n1'])

        value = nodes.workspace(self.db, nodes.Selection(self.pid, task['id']),
                                authority=tasks.CommandContext.ui(self.pid))

        self.assertTrue(value['provisional'])
        self.assertFalse(value['editable'])
        self.assertEqual(value['task']['id'], task['id'])
        self.assertEqual(value['run']['id'], '')
        self.assertEqual(value['upstream'][0]['task_id'], flow['settings']['task_ids']['n0'])

    def test_running_and_failed_node_show_only_their_saved_results(self):
        _, task, run = self.running()
        selection, asset = self.saved(task, run)
        ui = tasks.CommandContext.ui(self.pid)
        for status in ('running', 'failed'):
            if status == 'failed':
                tasks.fail(self.db, run['id'], 'Provider disconnected')
            value = nodes.workspace(self.db, selection, authority=ui)
            self.assertTrue(value['provisional'])
            self.assertFalse(value['editable'])
            self.assertEqual([row['title'] for row in value['content']], ['Owned search result'])
            preview = nodes.workspace_preview(self.db, selection, 'asset:' + asset['id'], authority=ui)
            self.assertIn('Actually saved evidence', preview['text'])
            self.assertEqual(len(value['steps']), 2)
        with self.assertRaises(ValueError):
            nodes.workspace_preview(self.db, selection, 'asset:foreign', authority=ui)

    def test_interrupted_saved_output_can_be_fixed_edited_and_passed_forward(self):
        flow, task, run = self.running()
        selection, _ = self.saved(task, run)
        tasks.interrupt(self.db, run['id'])
        task = tasks.get(self.db, task['id'])
        ui = tasks.CommandContext.ui(self.pid)
        body = {'request_key': 'collect-1', 'expected_version': task['version'], 'task_run_id': run['id']}
        value = nodes.collect(self.db, task['id'], body, authority=ui)
        self.assertEqual(value['status'], 'awaiting_review')
        self.assertEqual(nodes.collect(self.db, task['id'], body, authority=ui)['latest_run_id'], value['latest_run_id'])
        result = nodes.read(self.db, selection, authority=ui)
        content = result['content']
        content[0]['reason'] = 'HUMAN_CHECKED_EVIDENCE'
        result = nodes.command(self.db, task['id'], {'action': 'revise-structured', 'identity': result['identity'],
            'request_key': 'revise-1', 'content': content}, authority=ui)['result']
        with patch.object(engine, 'kick'):
            nodes.command(self.db, task['id'], {'action': 'review', 'identity': result['identity'],
                'approved': True, 'request_key': 'review-1'}, authority=ui)
        with patch.object(workflow, 'run_agent', return_value={'reply': 'Final downstream report'}) as model:
            engine.advance(self.db, flow['id'])
        self.assertIn('HUMAN_CHECKED_EVIDENCE', model.call_args.kwargs['extra_context'])
        self.assertEqual(tasks.get_run(self.db, run['id'])['status'], 'interrupted')

    def test_task_and_attempt_ids_resolve_the_current_context(self):
        flow, task, run = self.running()
        self.saved(task, run)
        self.db.project_task_context = tasks.CommandContext.agent(self.pid, task['id'])
        for run_id in (flow['id'], task['id'], run['id']):
            page = workflow_context.read(self.db, self.pid, {'run_id': run_id, 'node_id': 'search', 'section': 'input'})
            self.assertIn('Complete the research objective', page['content'])
            self.assertEqual(page['run_id'], flow['id'])
        with self.assertRaises(ValueError):
            workflow_context.read(self.db, 'other-project', {'run_id': task['id'], 'section': 'input'})

    def test_polling_omits_full_transcript_but_preserves_durable_checkpoint(self):
        _, task, run = self.running()
        self.saved(task, run)
        data = project_task_routes.route('GET', '/api/project-tasks/detail', {},
            {'project_id': [self.pid], 'id': [task['id']]}, self.db)
        self.assertLess(len(json.dumps(data)), 40000)
        self.assertNotIn('messages', data['task']['latest_run']['checkpoint'])
        self.assertGreater(len(tasks.get_run(self.db, run['id'])['checkpoint']['messages'][0]['content']), 100000)

    def test_compaction_keeps_goal_and_real_context_identity(self):
        messages = [{'role': 'system', 'content': 'Research instructions'}, {'role': 'user', 'content': 'ORIGINAL_RESEARCH_GOAL'}]
        for i in range(15):
            messages.extend([{'role': 'assistant', 'tool_calls': [{'id': str(i), 'function': {'name': 'paper_read', 'arguments': '{}'}}]},
                             {'role': 'tool', 'tool_call_id': str(i), 'content': 'Long paper text ' * 1000}])
        compact = workflow_context.bounded_messages(messages, 8000, run_id='real-flow', node_id='real-node')
        text = json.dumps(compact)
        self.assertIn('ORIGINAL_RESEARCH_GOAL', text)
        self.assertIn('real-flow', text)
        calls = [call['id'] for m in compact for call in m.get('tool_calls', [])]
        self.assertEqual(calls, [m['tool_call_id'] for m in compact if m['role'] == 'tool'])

    def test_compaction_preserves_current_node_and_accepted_human_output(self):
        messages = [
            {'role': 'system', 'content': 'Instructions'},
            {'role': 'system', 'content': 'CURRENT_NODE_INPUT ' + 'filler ' * 8000},
            {'role': 'user', 'content': 'Continue from accepted evidence'},
        ]
        messages[1]['content'] += '\nACCEPTED_HUMAN_REVISION'
        compact = workflow_context.bounded_messages(messages, 8000, run_id='flow', node_id='node')
        self.assertIn('ACCEPTED_HUMAN_REVISION', json.dumps(compact))
        prompt = workflow_context.prompt({'goal': 'Goal', 'task_inputs': {'dependencies': []},
            'accepted_upstream': [{'outputs': [{'content': 'ACCEPTED_HUMAN_OUTPUT'}]}]}, 'flow', 'node')
        self.assertLess(prompt.index('ACCEPTED_HUMAN_OUTPUT'), prompt.index('task_inputs'))

    def test_missing_typed_output_cannot_succeed_using_model_narration(self):
        self.layout(['search'])
        run = engine.create(self.db, self.pid)
        with patch.object(workflow, 'run_agent', return_value={'reply': 'I completed the literature search'}):
            result = engine.advance(self.db, run['id'])
        task = tasks.get(self.db, result['settings']['task_ids']['n0'])
        self.assertEqual(task['status'], 'failed')
        self.assertFalse(task['outputs'])
