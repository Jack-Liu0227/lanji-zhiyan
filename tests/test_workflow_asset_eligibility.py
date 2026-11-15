"""Automatic inputs use accepted artifacts and freeze real virtual-source content."""
import base64
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import asset_service
import paths
import project_library
import project_resources
import project_task_review
import project_tasks as tasks
import space_service
import workflow
import workflow_context
import workflow_execution
import workflow_layout
import task_tools
from tests import test_task_orchestration as orchestration
from tests import test_project_task_service as service


class WorkflowAssetEligibilityTest(unittest.TestCase):
    create = service.ProjectTaskServiceTest.create
    result = service.ProjectTaskServiceTest.result

    def setUp(self):
        service.ProjectTaskServiceTest.setUp(self)
        data = patch.object(paths, 'DATA', Path(self.temp.name) / 'asset-history')
        data.start()
        self.addCleanup(data.stop)

    def pending(self, text='Unreviewed evidence', **fields):
        task = self.create(**fields)
        run = tasks.start(self.db, task['id'], authority=self.ui)
        result = self.result(run, text)
        file = result['output_snapshot']['files'][0]
        asset = asset_service.register_file(self.db, file['path'], project_id=self.pid, kind='task_output',
            body=text, metadata={'project_task_id': task['id'], 'task_run_id': run['id']})
        file['asset_id'] = asset['id']
        return tasks.complete(self.db, run['id'], result), asset

    def test_virtual_source_hash_and_history_include_metadata_and_actual_body(self):
        uri = 'research://' + self.pid + '/literature/synthetic'
        original = asset_service.register(self.db, project_id=self.pid, kind='paper', title='Original title',
            canonical_uri=uri, storage='virtual', metadata={'doi': '10.test/one', 'authors': 'A'}, body='Original abstract')
        frozen = asset_service.virtual_snapshot(self.db, original)
        self.assertTrue(frozen['content_hash'])
        self.assertTrue(frozen['revision_id'])
        self.assertEqual(frozen['body'], 'Original abstract')
        changed = asset_service.register(self.db, project_id=self.pid, kind='paper', title='Edited title',
            canonical_uri=uri, storage='virtual', metadata={'doi': '10.test/two', 'authors': 'A'}, body='Edited abstract')
        current = asset_service.virtual_snapshot(self.db, changed)
        self.assertEqual(original['id'], changed['id'])
        self.assertNotEqual(frozen['content_hash'], current['content_hash'])
        self.assertNotEqual(frozen['revision_id'], current['revision_id'])
        old = self.db.db.execute('SELECT content_uri FROM asset_revisions WHERE id=?', (frozen['revision_id'],)).fetchone()
        old_content = json.loads(base64.b64decode(old['content_uri'].split(',', 1)[1]))
        self.assertEqual(old_content['metadata']['doi'], '10.test/one')
        self.assertEqual(old_content['body'], 'Original abstract')
        repeated = asset_service.register(self.db, project_id=self.pid, kind='paper', title='Edited title',
            canonical_uri=uri, storage='virtual', metadata={'authors': 'A', 'doi': '10.test/two'}, body='Edited abstract')
        self.assertEqual(current['revision_id'], repeated['current_revision_id'])

    def test_legacy_empty_hash_snapshot_is_read_only_and_does_not_commit_its_caller(self):
        asset = asset_service.register(self.db, project_id=self.pid, kind='paper', title='Legacy paper',
            canonical_uri='research://legacy/paper', storage='virtual', body='Stored full abstract')
        self.db.db.execute("UPDATE assets SET content_hash='',current_revision_id='' WHERE id=?", (asset['id'],))
        self.db.db.commit()
        self.db.db.execute("UPDATE research_tasks SET title='Uncommitted rename' WHERE id=?", (self.pid,))
        snapshot = asset_service.virtual_snapshot(self.db, asset_service.get(self.db, asset['id']))
        self.assertTrue(snapshot['content_hash'])
        self.assertEqual(snapshot['body'], 'Stored full abstract')
        self.assertTrue(self.db.db.in_transaction)
        self.assertEqual(asset_service.get(self.db, asset['id'])['content_hash'], '')
        self.db.db.rollback()
        self.assertNotEqual(self.db.summary(self.pid)['task']['title'], 'Uncommitted rename')

    def test_bibliography_updates_change_canonical_virtual_revision(self):
        result_id = self.db.cache_result(self.pid, {'title': 'First title', 'doi': '10.test/source', 'abstract': 'Before edit'})
        before = project_library.index_record(self.db, self.pid, result_id)
        frozen = asset_service.virtual_snapshot(self.db, before)
        project_library.update_record(self.db, self.pid, result_id, {'title': 'Updated title', 'abstract': 'New evidence'})
        after = asset_service.get(self.db, before['id'])
        self.assertNotEqual(frozen['content_hash'], after['content_hash'])
        self.assertNotEqual(frozen['revision_id'], after['current_revision_id'])
        self.assertEqual(frozen['metadata']['title'], 'First title')
        self.assertEqual(asset_service.virtual_snapshot(self.db, after)['metadata']['title'], 'Updated title')

    def test_pending_rejected_and_replaced_outputs_are_not_automatic_inputs(self):
        task, asset = self.pending()
        self.assertEqual(asset_service.eligibility(self.db, asset)['review_status'], 'awaiting_review')
        self.assertNotIn(asset['id'], {a['id'] for a in asset_service.research_assets(self.db, self.pid)})
        with self.assertRaisesRegex(ValueError, '尚未交付'):
            asset_service.require_input(self.db, asset)
        self.assertTrue(asset_service.require_input(self.db, asset, allow_draft=True)['draft_selected'])
        tasks.review(self.db, task['id'], approved=False, feedback='Check sources', authority=self.ui)
        self.assertEqual(asset_service.eligibility(self.db, asset)['review_status'], 'rejected')
        retry = tasks.start(self.db, task['id'], authority=self.ui)
        result = self.result(retry, 'Checked evidence')
        file = result['output_snapshot']['files'][0]
        accepted_asset = asset_service.register_file(self.db, file['path'], project_id=self.pid, kind='task_output',
            metadata={'project_task_id': task['id'], 'task_run_id': retry['id']})
        file['asset_id'] = accepted_asset['id']
        tasks.complete(self.db, retry['id'], result)
        tasks.review(self.db, task['id'], approved=True, authority=self.ui)
        self.assertEqual(asset_service.eligibility(self.db, asset)['review_status'], 'superseded')
        self.assertTrue(asset_service.require_input(self.db, accepted_asset)['consumable'])
        automatic = {a['id'] for a in asset_service.research_assets(self.db, self.pid)}
        self.assertNotIn(asset['id'], automatic)
        self.assertIn(accepted_asset['id'], automatic)

    def test_working_document_is_controlled_even_without_task_output_kind(self):
        task = self.create()
        run = tasks.start(self.db, task['id'], authority=self.ui)
        working = self.root / 'outline.md'
        working.write_text('Working evidence', encoding='utf-8')
        self.db.project_task_context = tasks.CommandContext.agent(self.pid, task['id'])
        try:
            asset = asset_service.register_file(self.db, working, project_id=self.pid, kind='document')
        finally:
            del self.db.project_task_context
        self.assertTrue(asset_service.eligibility(self.db, asset)['controlled'])
        with self.assertRaises(ValueError):
            asset_service.require_input(self.db, asset)
        result = self.result(run, 'Working evidence')
        result['output_snapshot']['files'][0]['original_path'] = str(working)
        tasks.complete(self.db, run['id'], result)
        tasks.review(self.db, task['id'], approved=True, authority=self.ui)
        self.assertTrue(asset_service.require_input(self.db, asset)['consumable'])
        working.write_text('Unreviewed external edit', encoding='utf-8')
        with self.assertRaises(ValueError):
            asset_service.require_input(self.db, asset)
        self.assertNotIn(asset['id'], {a['id'] for a in asset_service.research_assets(self.db, self.pid)})
        updated = asset_service.register_file(self.db, working, project_id=self.pid, kind='document')
        self.assertEqual(asset_service.eligibility(self.db, updated)['review_status'], 'modified')

    def test_output_view_defaults_to_accepted_and_explicitly_lists_drafts(self):
        task, asset = self.pending()
        self.assertEqual(project_resources.catalog(self.db, self.pid, 'output')['items'], [])
        drafts = project_resources.catalog(self.db, self.pid, 'output', review_status='all')['items']
        self.assertEqual([item['asset_id'] for item in drafts], [asset['id']])
        self.assertEqual(drafts[0]['review_status'], 'awaiting_review')
        tasks.review(self.db, task['id'], approved=True, authority=self.ui)
        delivered = project_resources.catalog(self.db, self.pid, 'output')['items']
        self.assertEqual([item['asset_id'] for item in delivered], [asset['id']])
        self.assertEqual(delivered[0]['task_run_id'], task['latest_run_id'])
        self.assertTrue(delivered[0]['delivery_id'])

    def test_original_user_material_remains_available_without_task_approval(self):
        original = self.root / 'source.md'
        original.write_text('User supplied evidence', encoding='utf-8')
        asset = asset_service.register_file(self.db, original, project_id=self.pid, kind='document')
        self.assertFalse(asset_service.eligibility(self.db, asset)['controlled'])
        self.assertTrue(asset_service.require_input(self.db, asset)['consumable'])
        self.assertIn(asset['id'], {a['id'] for a in asset_service.research_assets(self.db, self.pid)})

    def test_other_agent_cannot_read_pending_output_but_its_own_node_can(self):
        task, asset = self.pending(text='PRIVATE_CANDIDATE_CONTENT')
        with self.assertRaises(ValueError):
            workflow._tool_impl('project_resources', self.db, self.pid, {'asset_id': asset['id']})
        self.assertEqual(workflow._tool_impl('project_resources', self.db, self.pid, {'kind': 'output'})['items'], [])
        self.db.project_task_context = tasks.CommandContext.agent(self.pid, task['id'])
        try:
            own = workflow._tool_impl('project_resources', self.db, self.pid, {'asset_id': asset['id']})
            self.assertIn('PRIVATE_CANDIDATE_CONTENT', own['content'])
            self.assertEqual(asset_service.require_context(self.db, asset)['context_basis'], 'own_attempt')
        finally:
            del self.db.project_task_context
        tasks.review(self.db, task['id'], approved=True, authority=self.ui)
        published = workflow._tool_impl('project_resources', self.db, self.pid, {'asset_id': asset['id']})
        self.assertIn('PRIVATE_CANDIDATE_CONTENT', published['content'])

    def test_explicit_draft_input_is_bound_to_the_exact_asset_hash(self):
        producer, asset = self.pending()
        consumer = self.create(goal='Check chosen draft', inputs=[{'asset_id': asset['id'], 'allow_draft': True}])
        run = tasks.start(self.db, consumer['id'], authority=self.ui)
        self.assertTrue(run['input_snapshot']['assets'][0]['allow_draft'])
        self.db.project_task_context = tasks.CommandContext.agent(self.pid, consumer['id'])
        try:
            self.assertEqual(asset_service.require_context(self.db, asset)['context_basis'], 'frozen_input')
            copy = run['input_snapshot']['assets'][0]['path']
            self.assertEqual(asset_service.require_path_context(self.db, self.pid, copy)['context_basis'], 'frozen_input')
            Path(asset['canonical_uri']).write_text('Different unreviewed version', encoding='utf-8')
            with self.assertRaises(ValueError):
                asset_service.require_context(self.db, asset)
        finally:
            del self.db.project_task_context

    def test_note_tools_and_search_do_not_expose_another_tasks_draft(self):
        task = self.create()
        tasks.start(self.db, task['id'], authority=self.ui)
        self.db.project_task_context = tasks.CommandContext.agent(self.pid, task['id'])
        try:
            note = project_resources.write(self.db, self.pid, {'kind': 'note', 'title': 'Candidate note', 'content': 'DRAFT_NOTE_NEEDLE'})
            own = workflow._tool_impl('project_note_read', self.db, self.pid, {'path': note['path']})
            self.assertIn('DRAFT_NOTE_NEEDLE', own['content'])
            self.assertTrue(workflow._tool_impl('project_note_list', self.db, self.pid, {})['items'])
        finally:
            del self.db.project_task_context
        with self.assertRaises(ValueError):
            workflow._tool_impl('project_note_read', self.db, self.pid, {'path': note['path']})
        self.assertEqual(workflow._tool_impl('project_note_list', self.db, self.pid, {})['items'], [])
        self.assertEqual(space_service.search_project_space(self.db, self.pid, 'DRAFT_NOTE_NEEDLE')['notes'], [])
        self.assertEqual(workflow._knowledge_query_literal(self.db, 'DRAFT_NOTE_NEEDLE', [(self.pid, 'Project')], True)['hits'], [])
        project_resources.write(self.db, self.pid, {'kind': 'note', 'title': 'User note', 'content': 'DRAFT_NOTE_NEEDLE'})
        found = space_service.search_project_space(self.db, self.pid, 'DRAFT_NOTE_NEEDLE')['notes']
        self.assertEqual([row['title'] for row in found], ['User note'])

    def test_cross_node_output_context_requires_canonical_delivery(self):
        self.grant = self.binding
        workflow_layout.ensure_schema(self.db)
        orchestration.TaskOrchestrationTest.layout(self, ['task', 'task'])
        flow = workflow_execution.create(self.db, self.pid)
        with patch.object(workflow, 'run_agent', return_value={'reply': 'NODE_OUTPUT_CONTEXT'}):
            pending = workflow_execution.advance(self.db, flow['id'])
        producer_id = pending['settings']['task_ids']['n0']
        args = {'run_id': flow['id'], 'node_id': pending['current_node'], 'section': 'output'}
        with self.assertRaisesRegex(ValueError, '交付'):
            workflow_context.read(self.db, self.pid, args)
        self.db.project_task_context = tasks.CommandContext.agent(self.pid, producer_id)
        try:
            self.assertIn('NODE_OUTPUT_CONTEXT', workflow_context.read(self.db, self.pid, args)['content'])
        finally:
            del self.db.project_task_context
        tasks.review(self.db, producer_id, approved=True, authority=self.ui)
        self.assertIn('NODE_OUTPUT_CONTEXT', workflow_context.read(self.db, self.pid, args)['content'])

    def test_task_freezes_virtual_metadata_and_rejects_changed_source(self):
        source = asset_service.register(self.db, project_id=self.pid, kind='paper', title='Frozen paper',
            canonical_uri='research://' + self.pid + '/literature/input', storage='virtual',
            metadata={'doi': '10.test/input'}, body='Original input abstract')
        task = self.create(inputs=[source['id']])
        run = tasks.start(self.db, task['id'], authority=self.ui)
        frozen = run['input_snapshot']['assets'][0]
        self.assertEqual(frozen['metadata']['doi'], '10.test/input')
        self.assertEqual(frozen['body'], 'Original input abstract')
        asset_service.register(self.db, project_id=self.pid, kind='paper', title='Changed paper',
            canonical_uri=source['canonical_uri'], storage='virtual', metadata={'doi': '10.test/edited'}, body='Edited abstract')
        with self.assertRaisesRegex(ValueError, '输入资产版本'):
            tasks.complete(self.db, run['id'], self.result(run))

    def test_relative_unindexed_working_path_cannot_bypass_pending_output_policy(self):
        task = self.create()
        run = tasks.start(self.db, task['id'], authority=self.ui)
        source = self.root / 'unindexed.md'
        source.write_text('Pending working file', encoding='utf-8')
        result = self.result(run, 'Pending working file')
        result['output_snapshot']['files'][0]['original_path'] = str(source)
        tasks.complete(self.db, run['id'], result)
        self.assertIsNone(self.db.db.execute('SELECT id FROM assets WHERE canonical_uri=?', (str(source),)).fetchone())
        consumer = self.create(goal='Use selected relative file', inputs=[{'relative': 'unindexed.md'}])
        with self.assertRaisesRegex(ValueError, '尚未交付'):
            tasks.start(self.db, consumer['id'], authority=self.ui)

    def test_resource_result_target_is_a_completed_mapped_agent_node(self):
        self.grant = self.binding
        workflow_layout.ensure_schema(self.db)
        orchestration.TaskOrchestrationTest.layout(self, ['task'])
        flow = workflow_execution.create(self.db, self.pid)
        with patch.object(workflow_execution, 'kick'):
            task_tools.call(self.db, self.pid, 'workflow_start', {'id': flow['id']})
        with patch.object(workflow, 'run_agent', return_value={'reply': 'NODE_RESULT_TARGET'}):
            completed = workflow_execution.advance(self.db, flow['id'])
        task_id = completed['settings']['task_ids']['n0']
        task = tasks.get(self.db, task_id)
        asset = asset_service.get(self.db, task['outputs'][0]['asset_id'])
        target = asset_service.eligibility(self.db, asset)['node_result']
        self.assertTrue(target['available'])
        self.assertEqual(target['task_id'], task_id)
        self.assertEqual(target['task_run_id'], task['latest_run_id'])
        self.assertEqual(target['workflow_run_id'], flow['id'])
        self.assertEqual(target['node_id'], 'n0')
        snapshot = project_task_review.read(self.db, task_id, self.pid, 0)
        revised = project_task_review.revise(self.db, task_id, {'reviewed_run_id': snapshot['run_id'],
            'expected_version': snapshot['task_version'], 'expected_hash': snapshot['content_hash'],
            'output_index': 0, 'text': 'REVISED_NODE_RESULT'}, authority=self.ui)
        old_target = asset_service.eligibility(self.db, asset)['node_result']
        current_asset = asset_service.get(self.db, revised['outputs'][0]['asset_id'])
        new_target = asset_service.eligibility(self.db, current_asset)['node_result']
        self.assertEqual(old_target['output_revision'], 0)
        self.assertEqual(new_target['output_revision'], 1)
        child, child_asset = self.pending(parent_task_id=task_id)
        self.assertEqual(child['workflow_node_id'], 'n0')
        self.assertIsNone(asset_service.eligibility(self.db, child_asset)['node_result'])
        settings = completed['settings']
        settings['task_ids']['n0'] = child['id']
        self.db.db.execute('UPDATE workflow_executions SET settings=? WHERE run_id=?', (json.dumps(settings), flow['id']))
        self.db.db.commit()
        self.assertIsNone(asset_service.eligibility(self.db, asset)['node_result'])


if __name__ == '__main__':
    unittest.main()
