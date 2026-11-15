"""Versioned result edits retain originals and recover partial project-file writes."""
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from tests import test_project_task_service as service
import asset_service
import node_output_editors
import project_task_execution
import project_task_review as review
import project_tasks as tasks
import task_output_versions as versions
import workflow
import workflow_layout
import workflow_state


class WorkflowNodeResultEditingTest(unittest.TestCase):
    setUp = service.ProjectTaskServiceTest.setUp
    create = service.ProjectTaskServiceTest.create
    result = service.ProjectTaskServiceTest.result

    def pending(self, stage='task', content='Original result'):
        task = self.create(metadata={'stage': stage})
        run = tasks.start(self.db, task['id'], authority=self.ui)
        if stage in ('outline', 'write', 'review'):
            workflow.doc_write(self.db, self.pid, stage, content)
        elif stage == 'map':
            workflow.map_write(self.db, self.pid, content)
        capture = workflow_state.capture(self.db, self.pid, stage) if stage != 'task' else None
        output = project_task_execution._outputs(self.db, task, run, {'reply': 'Original agent reply'}, capture)
        return tasks.complete(self.db, run['id'], {'reply': 'Original agent reply', 'output_snapshot': output,
                                                  'checkpoint': {'output': capture or output}})

    def command(self, task, text='Human revision', index=0):
        current = review.read(self.db, task['id'], self.pid, index)
        return {'reviewed_run_id': current['run_id'], 'expected_version': current['task_version'],
                'expected_hash': current['content_hash'], 'output_index': index, 'text': text}

    def test_text_snapshot_write_failure_does_not_touch_working_document(self):
        task = self.pending('outline', '# Initial outline')
        path = workflow.doc_path(self.db, self.pid, 'outline')
        original = path.read_text(encoding='utf-8')
        command = self.command(task)
        write = asset_service.atomic_write_text
        def fail_candidate(target, text):
            if 'revisions' in Path(target).parts:
                raise OSError('simulated disk failure')
            return write(target, text)
        with patch.object(asset_service, 'atomic_write_text', side_effect=fail_candidate):
            with self.assertRaises(OSError):
                review.revise(self.db, task['id'], command, authority=self.ui)
        self.assertEqual(path.read_text(encoding='utf-8'), original)
        self.assertEqual(tasks.get(self.db, task['id'])['latest_run']['checkpoint'].get('output_revision', 0), 0)

    def test_database_failure_restores_working_document_and_asset(self):
        task = self.pending('outline', '# Initial outline')
        path = workflow.doc_path(self.db, self.pid, 'outline')
        original = path.read_text(encoding='utf-8')
        command = self.command(task)
        event = tasks._event
        def fail_revision(db, task_id, kind, *args, **kwargs):
            if kind == 'output_revised':
                raise sqlite3.OperationalError('simulated commit failure')
            return event(db, task_id, kind, *args, **kwargs)
        with patch.object(tasks, '_event', side_effect=fail_revision):
            with self.assertRaises(sqlite3.OperationalError):
                review.revise(self.db, task['id'], command, authority=self.ui)
        self.assertEqual(path.read_text(encoding='utf-8'), original)
        asset = self.db.db.execute('SELECT content_hash FROM assets WHERE canonical_uri=?', (str(path.resolve()),)).fetchone()
        self.assertEqual(asset['content_hash'], tasks._hash(path))
        self.assertFalse(tasks.get(self.db, task['id'])['output_revisions'])
        self.assertEqual(self.db.db.execute('SELECT state FROM project_task_file_operations').fetchone()['state'], 'rolled_back')
        self.assertIsNone(self.db.db.execute('SELECT 1 FROM project_task_output_versions WHERE task_run_id=? AND revision=1',
                                          (task['latest_run_id'],)).fetchone())

    def test_complete_text_snapshots_are_immutable_across_multiple_revisions(self):
        task = self.pending('outline', '# Initial outline')
        zero = task['latest_run']
        first = review.revise(self.db, task['id'], self.command(task, '# First review'), authority=self.ui)
        second = review.revise(self.db, task['id'], self.command(first, '# Second review'), authority=self.ui)
        old = versions.read(self.db, second, second['latest_run'], 0)
        one = versions.read(self.db, second, second['latest_run'], 1)
        self.assertEqual(old['output_snapshot'], zero['output_snapshot'])
        self.assertIn('Initial outline', Path(old['output_snapshot']['files'][0]['path']).read_text(encoding='utf-8'))
        self.assertIn('First review', Path(one['output_snapshot']['files'][0]['path']).read_text(encoding='utf-8'))
        self.assertIn('Second review', second['latest_run']['output_snapshot']['content'])
        self.assertEqual(one['checkpoint']['output_revision'], 1)

    def test_restart_recovers_applied_but_uncommitted_file_operation(self):
        task = self.pending('outline', '# Before interruption')
        run = task['latest_run']
        path = workflow.doc_path(self.db, self.pid, 'outline')
        original = path.read_text(encoding='utf-8')
        staged = path.parent / 'staged-review.md'
        staged.write_text('# Uncommitted', encoding='utf-8')
        operation = versions.begin(self.db, task, run, 1, [{'path': str(path), 'source': str(staged), 'old_hash': tasks._hash(path)}])
        versions.apply(self.db, task, operation)
        self.assertEqual(path.read_text(encoding='utf-8'), '# Uncommitted')
        with workflow.WorkflowDB(self.db.path) as reopened:
            self.assertEqual(versions.recover(reopened), 1)
            self.assertEqual(versions.recover(reopened), 0)
        self.assertEqual(path.read_text(encoding='utf-8'), original)

    def test_recovery_preserves_external_edit_instead_of_overwriting_it(self):
        task = self.pending('outline', '# Original')
        path = workflow.doc_path(self.db, self.pid, 'outline')
        staged = path.parent / 'candidate-review.md'
        staged.write_text('# Candidate', encoding='utf-8')
        operation = versions.begin(self.db, task, task['latest_run'], 1,
                                   [{'path': str(path), 'source': str(staged), 'old_hash': tasks._hash(path)}])
        versions.apply(self.db, task, operation)
        path.write_text('# Concurrent user edit', encoding='utf-8')
        self.assertEqual(versions.recover(self.db), 1)
        self.assertEqual(path.read_text(encoding='utf-8'), '# Concurrent user edit')
        self.assertEqual(self.db.db.execute('SELECT state FROM project_task_file_operations').fetchone()['state'], 'conflict')

    def test_recovery_restores_exact_bytes_including_windows_newlines(self):
        task = self.pending()
        path = self.root / 'windows-note.md'
        original = b'# Original\r\n\r\nWindows newlines\r\n'
        path.write_bytes(original)
        staged = self.root / 'candidate.md'
        staged.write_text('# Candidate\n', encoding='utf-8')
        operation = versions.begin(self.db, task, task['latest_run'], 1,
                                   [{'path': str(path), 'source': str(staged), 'old_hash': tasks._hash(path)}])
        versions.apply(self.db, task, operation)
        versions.recover(self.db)
        self.assertEqual(path.read_bytes(), original)

    def test_prepared_operation_does_not_restore_a_file_it_never_replaced(self):
        task = self.pending()
        path = self.root / 'shared.md'
        path.write_text('# Original', encoding='utf-8')
        staged = self.root / 'candidate.md'
        staged.write_text('# Same user edit', encoding='utf-8')
        versions.begin(self.db, task, task['latest_run'], 1,
                       [{'path': str(path), 'source': str(staged), 'old_hash': tasks._hash(path)}])
        path.write_bytes(staged.read_bytes())
        versions.recover(self.db)
        self.assertEqual(path.read_bytes(), staged.read_bytes())

    def test_graph_history_retains_original_and_human_version(self):
        graph = {'nodes': [{'id': 'proof', 'type': 'note', 'label': 'Original proof'}], 'edges': []}
        task = self.pending('map', graph)
        baseline = review.read_graph(self.db, task['id'], self.pid)
        edited = {'nodes': [{'id': 'proof', 'type': 'note', 'label': 'Checked proof'}], 'edges': []}
        updated = review.revise_graph(self.db, task['id'], {'graph': edited, 'reviewed_run_id': baseline['run_id'],
                                      'expected_version': baseline['task_version']}, authority=self.ui)
        old = versions.read(self.db, updated, updated['latest_run'], 0)
        new = versions.read(self.db, updated, updated['latest_run'], 1)
        self.assertEqual(old['checkpoint']['output']['content']['nodes'][0]['label'], 'Original proof')
        self.assertEqual(new['checkpoint']['output']['content']['nodes'][0]['label'], 'Checked proof')

    def test_structured_search_selection_changes_delivery_not_original_records(self):
        workflow_layout.ensure_schema(self.db)
        layout = workflow_layout.save_layout(self.db, {'name': 'Search only', 'nodes': [
            {'id': 'search', 'step': 'search', 'executor_binding_id': self.binding['id']}], 'edges': []})['layout']
        workflow_layout.select_layout(self.db, {'task_id': self.pid, 'layout_id': layout['id']})
        first = self.db.cache_result(self.pid, {'source': 'manual', 'title': 'Included paper'})
        second = self.db.cache_result(self.pid, {'source': 'manual', 'title': 'Excluded paper'})
        task = self.create(metadata={'stage': 'search', 'legacy_stage_compat': True, 'node_config': {'step': 'search'}})
        run = tasks.start(self.db, task['id'], authority=self.ui)
        capture = workflow_state.capture(self.db, self.pid, 'search')
        output = project_task_execution._outputs(self.db, task, run, {'reply': 'Found sources'}, capture)
        task = tasks.complete(self.db, run['id'], {'output_snapshot': output, 'checkpoint': {'output': capture}})
        changed = [{**row, 'included': row['id'] == first} for row in capture['content']]
        updated = review.revise_structured(self.db, task['id'], {'content': changed, 'reviewed_run_id': run['id'],
                                            'expected_version': task['version']}, authority=self.ui)
        self.assertEqual([row['id'] for row in updated['latest_run']['output_snapshot']['content']], [first])
        self.assertEqual({row['id'] for row in self.db.results(self.pid)}, {first, second})
        self.assertEqual(workflow_state.capture(self.db, self.pid, 'search')['content'], updated['latest_run']['output_snapshot']['content'])
        tasks.review(self.db, task['id'], approved=True, authority=self.ui, reviewed_output_version=1)

    def test_invalid_structured_records_and_pdf_path_changes_are_rejected(self):
        baseline = [{'id': 'paper', 'title': 'Source', 'source': 'manual'}]
        for value in ([{'id': 'foreign', 'title': 'Other'}], [{**baseline[0], 'included': 'yes'}],
                      [{**baseline[0], 'included': False}]):
            with self.assertRaises(ValueError):
                node_output_editors.parse_content('search', value, baseline)
        with self.assertRaises(ValueError):
            node_output_editors.parse_content('notes', [{'id': 'paper', 'files': ['foreign.pdf']}],
                                               [{'id': 'paper', 'files': ['original.pdf']}])
        with self.assertRaises(ValueError):
            node_output_editors.parse_content('entrans', {'source': 'Original', 'result': ''}, {})

    def test_large_structured_revision_payload_is_supported(self):
        payload = {'value': 'x' * (3 * 1024 * 1024)}
        self.assertEqual(node_output_editors.parse_content('task', payload, {}), payload)

    def test_repeated_structured_edits_keep_all_pdf_references(self):
        task = self.create(metadata={'stage': 'notes'})
        run = tasks.start(self.db, task['id'], authority=self.ui)
        report = self.result(run, 'Translation summary')
        pdf = self.root / 'original.pdf'
        pdf.write_bytes(b'original binary PDF data')
        frozen_pdf = Path(report['output_snapshot']['files'][0]['path']).parent / 'translated.pdf'
        frozen_pdf.write_bytes(b'fixed translated PDF data')
        report['output_snapshot']['files'].insert(0, {'name': 'translated.pdf', 'path': str(frozen_pdf),
                                                     'content_hash': tasks._hash(frozen_pdf)})
        report['output_snapshot']['content'] = [{'id': 'paper', 'files': [str(pdf)], 'translation': 'First wording'}]
        task = tasks.complete(self.db, run['id'], report)
        for translation in ('Reviewed wording', 'Final wording'):
            content = [{**task['latest_run']['output_snapshot']['content'][0], 'translation': translation}]
            task = review.revise_structured(self.db, task['id'], {'content': content,
                'reviewed_run_id': run['id'], 'expected_version': task['version']}, authority=self.ui)
        self.assertEqual(task['outputs'][0]['path'], str(frozen_pdf))
        self.assertEqual(frozen_pdf.read_bytes(), b'fixed translated PDF data')
        self.assertEqual(pdf.read_bytes(), b'original binary PDF data')
        self.assertEqual(task['latest_run']['output_snapshot']['content'][0]['translation'], 'Final wording')

    def test_revision_command_receipt_commits_with_output_version(self):
        task = self.pending()
        body = {**self.command(task), '_command_receipt': {'request_key': 'save-once',
                                                        'action': 'revise-text', 'request_digest': 'digest'}}
        revised = review.revise(self.db, task['id'], body, authority=self.ui)
        receipt = self.db.db.execute('SELECT * FROM workflow_node_commands WHERE task_id=? AND request_key=?',
                                     (task['id'], 'save-once')).fetchone()
        self.assertEqual(receipt['task_run_id'], revised['latest_run_id'])
        self.assertEqual(receipt['output_revision'], 1)

    def test_text_edit_of_structured_summary_updates_content_and_rejects_invalid_json(self):
        task = self.pending()
        task = review.revise_structured(self.db, task['id'], {'content': {'value': 1},
            'reviewed_run_id': task['latest_run_id'], 'expected_version': task['version']}, authority=self.ui)
        with self.assertRaisesRegex(ValueError, 'JSON'):
            review.revise(self.db, task['id'], self.command(task, 'invalid json'), authority=self.ui)
        task = review.revise(self.db, task['id'], self.command(task, '{"value": 2}'), authority=self.ui)
        self.assertEqual(task['latest_run']['output_snapshot']['content'], {'value': 2})

    def test_tool_output_cannot_be_rewritten_through_text_or_structured_api(self):
        task = self.pending()
        self.db.db.execute('UPDATE project_tasks SET metadata=? WHERE id=?',
                           (json.dumps({'stage': 'task', 'node_config': {'node_kind': 'tool'}}), task['id']))
        self.db.db.commit()
        body = {'reviewed_run_id': task['latest_run_id'], 'expected_version': task['version'], 'content': {'forged': True}}
        with self.assertRaisesRegex(ValueError, '固定工具'):
            review.revise_structured(self.db, task['id'], body, authority=self.ui)


if __name__ == '__main__':
    unittest.main()
