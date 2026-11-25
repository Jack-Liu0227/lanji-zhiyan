"""Template identity, concurrency, empty catalogs and audited reference migration."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from workflow import WorkflowDB, now
import workflow_layout as layouts
import workflow_template_catalog as catalog
import project_workflows


class TemplateCatalogTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.sqlite3'
        self.db = WorkflowDB(self.path)
        self.addCleanup(self.db.close)
        layouts.ensure_schema(self.db)
        self.pid = self.db.create_task('Catalog test')

    def definition(self):
        return {'name': 'Parallel workflow', 'nodes': [
            {'id': 'a', 'step': 'task', 'label': 'Pool', 'prompt': 'Find candidates'},
            {'id': 'b', 'step': 'task', 'label': 'Review', 'prompt': 'Check evidence'},
            {'id': 'c', 'step': 'task', 'label': 'Review', 'prompt': 'Check evidence'}],
            'edges': [{'source': 'a', 'target': 'b'}, {'source': 'a', 'target': 'c'}], 'order': ['a', 'b', 'c']}

    def test_reordered_ids_positions_and_whitespace_reuse_identity(self):
        original = layouts.save_layout(self.db, self.definition())['layout']
        changed = self.definition()
        changed['name'] = '  Parallel   workflow  '
        mapping = {'a': 'random-1', 'b': 'random-3', 'c': 'random-2'}
        for node in changed['nodes']:
            node['id'] = mapping[node['id']]; node['x'] = 500
        for edge in changed['edges']:
            edge.update(source=mapping[edge['source']], target=mapping[edge['target']])
        changed['nodes'].reverse(); changed['edges'].reverse()
        changed['order'] = ['random-1', 'random-2', 'random-3']
        saved = layouts.save_layout(self.db, changed)
        self.assertTrue(saved['reused'])
        self.assertEqual(saved['layout']['id'], original['id'])

    def test_business_changes_do_not_merge(self):
        original = self.definition()
        saved = layouts.save_layout(self.db, original)['layout']
        for field, value in [('prompt', 'Different evidence'), ('tools', ['library_overview']),
                             ('review_required', False), ('output_contract', {'required_files': ['result.md']}),
                             ('executor_role_id', 'independent-role')]:
            changed = copy.deepcopy(original); changed['nodes'][0][field] = value
            self.assertNotEqual(layouts.save_layout(self.db, changed)['layout']['id'], saved['id'])

    def test_concurrent_missing_id_requests_converge(self):
        def save(_):
            with WorkflowDB(self.path) as db:
                return layouts.save_layout(db, self.definition())['layout']['id']
        with ThreadPoolExecutor(max_workers=3) as executor:
            ids = list(executor.map(save, range(6)))
        self.assertEqual(len(set(ids)), 1)

    def test_edit_keeps_id_and_collision_is_explicit(self):
        saved = layouts.save_layout(self.db, self.definition())['layout']
        changed = {**saved, 'name': 'Other name'}
        other = layouts.save_layout(self.db, {**changed, 'id': ''})['layout']
        with self.assertRaisesRegex(ValueError, '完全重复'):
            layouts.save_layout(self.db, changed)
        self.assertEqual(layouts._layout(self.db, saved['id'])['name'], saved['name'])
        self.assertNotEqual(other['id'], saved['id'])

    def test_delete_all_and_reopen_does_not_seed_or_switch_selection(self):
        selected = layouts.list_layouts(self.db)['layouts'][0]
        layouts.select_layout(self.db, {'task_id': self.pid, 'layout_id': selected['id']})
        for item in layouts.list_layouts(self.db)['layouts']:
            layouts.delete_layout(self.db, item['id'])
        with WorkflowDB(self.path) as reopened:
            self.assertEqual(layouts.list_layouts(reopened)['layouts'], [])
            self.assertIsNone(layouts.state(reopened, self.pid)['layout'])
            with self.assertRaisesRegex(ValueError, '未选择'):
                project_workflows.current(reopened, self.pid)
            self.assertTrue(layouts.save_layout(reopened, self.definition())['layout']['id'])
            self.assertIsNone(layouts.state(reopened, self.pid)['layout'])

    def test_deleted_evoflow_presets_do_not_reappear_on_list(self):
        import evoflow_sdk
        evoflow_sdk.ensure_presets(self.db)
        for item in layouts.list_layouts(self.db)['layouts']:
            layouts.delete_layout(self.db, item['id'])
        listed = evoflow_sdk.platform_call(self.db, self.pid, 'workflow.list')['result']['layouts']
        self.assertEqual(listed, [])

    def insert_duplicate(self, source, wid='duplicate'):
        self.db.db.execute('INSERT INTO workflow_layouts VALUES (?,?,?,?,?,?)',
                           (wid, source['name'], '', json.dumps({k: source[k] for k in ('nodes', 'edges', 'order')}),
                            '2000-01-01', '2000-01-01'))
        self.db.db.commit()
        return wid

    def test_explicit_cleanup_redirects_selection_roles_and_schedule_and_archives(self):
        saved = layouts.save_layout(self.db, self.definition())['layout']
        duplicate = self.insert_duplicate(saved)
        layouts.select_layout(self.db, {'task_id': self.pid, 'layout_id': duplicate, 'node_id': 'b'})
        self.db.db.execute('INSERT INTO project_workflow_roles VALUES (?,?,?,?,?,?,?,?)',
                           (self.pid, duplicate, 'b', 'binding', '', 1, 'user', now()))
        self.db.db.execute('INSERT INTO schedule_workflows VALUES (?,?)', ('schedule', duplicate))
        self.db.db.commit()
        preview = catalog.deduplicate(self.db)
        self.assertEqual(len(preview['groups']), 1)
        self.assertEqual(preview['removed'], 0)
        result = catalog.deduplicate(self.db, apply=True)
        self.assertEqual(result['removed'], 1)
        self.assertEqual(layouts.state(self.db, self.pid)['layout']['id'], saved['id'])
        self.assertEqual(self.db.db.execute('SELECT template_id FROM project_workflow_roles WHERE project_id=?', (self.pid,)).fetchone()[0], saved['id'])
        self.assertEqual(self.db.db.execute('SELECT layout_id FROM schedule_workflows WHERE schedule_id=?', ('schedule',)).fetchone()[0], saved['id'])
        self.assertEqual(self.db.db.execute("SELECT canonical_id FROM workflow_template_archive WHERE template_id=?", (duplicate,)).fetchone()[0], saved['id'])
        self.assertEqual(catalog.deduplicate(self.db, apply=True)['removed'], 0)

    def test_conflicting_project_overrides_preserve_whole_group(self):
        saved = layouts.save_layout(self.db, self.definition())['layout']
        duplicate = self.insert_duplicate(saved)
        for wid, prompt in [(saved['id'], 'One override'), (duplicate, 'Another override')]:
            value = self.definition(); value['nodes'][0]['prompt'] = prompt
            self.db.db.execute('INSERT INTO project_workflow_definitions VALUES (?,?,?,?,?,?)',
                               (self.pid, wid, 1, json.dumps(value), '', now()))
        self.db.db.commit()
        result = catalog.deduplicate(self.db, apply=True)
        self.assertEqual(result['removed'], 0)
        self.assertIn('冲突', result['groups'][0]['skipped_reason'])
        self.assertEqual(len(list(self.db.db.execute('SELECT * FROM project_workflow_definitions'))), 2)


if __name__ == '__main__':
    unittest.main()
