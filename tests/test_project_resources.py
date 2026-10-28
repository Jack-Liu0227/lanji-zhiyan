"""Resource views retain canonical paths, revisions and project boundaries."""
import tempfile
from pathlib import Path
import unittest

import asset_service
import project_resources as resources
import workflow


class ProjectResourcesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.db = workflow.WorkflowDB(Path(tmp.name) / 'app.sqlite3');self.addCleanup(self.db.close)
        self.pid = self.db.create_task('Resource project')

    def test_typed_views_share_one_file_and_revision(self):
        for kind in ('note', 'experiment', 'manuscript'):
            written = resources.write(self.db, self.pid, {'kind': kind, 'title': kind, 'content': '# Actual content'})
            read = resources.read(self.db, self.pid, written['asset_id'])
            updated = resources.write(self.db, self.pid, {'kind': kind, 'asset_id': written['asset_id'], 'expected_hash': read['content_hash'], 'content': '# Revised'})
            self.assertEqual(updated['asset_id'], written['asset_id'])
            self.assertEqual(updated['path'], written['path'])
            self.assertNotEqual(updated['content_hash'], written['content_hash'])
            listed = resources.catalog(self.db, self.pid, kind)
            self.assertEqual(len(listed['items']), 1)
            self.assertEqual(listed['items'][0]['asset_id'], written['asset_id'])
            with self.assertRaises(ValueError):
                resources.write(self.db, self.pid, {'kind': kind, 'asset_id': written['asset_id'], 'expected_hash': read['content_hash'], 'content': 'Stale'})
        self.assertEqual(resources.catalog(self.db, self.pid)['total'], 3)

    def test_index_finds_existing_files_without_moving_or_reclassifying_assets(self):
        root = self.db.base_dir(self.pid)
        manuscript = root / '论文正文' / 'existing.md';manuscript.parent.mkdir(exist_ok=True)
        manuscript.write_text('# Existing manuscript', encoding='utf-8')
        experiment = root / '实验记录' / 'measurements.csv';experiment.parent.mkdir(exist_ok=True)
        experiment.write_text('sample,value\nA,10', encoding='utf-8')
        before = manuscript.read_bytes(), experiment.read_bytes()
        resources.index(self.db, self.pid)
        first = resources.catalog(self.db, self.pid)
        resources.index(self.db, self.pid)
        self.assertEqual((manuscript.read_bytes(), experiment.read_bytes()), before)
        self.assertEqual({item['id'] for item in first['items']}, {item['id'] for item in resources.catalog(self.db, self.pid)['items']})
        self.assertEqual(resources.catalog(self.db, self.pid, 'manuscript')['items'][0]['path'], str(manuscript))
        self.assertEqual(resources.catalog(self.db, self.pid, 'experiment')['items'][0]['path'], str(experiment))

    def test_cross_project_and_delivery_snapshot_writes_are_rejected(self):
        written = resources.write(self.db, self.pid, {'kind': 'note', 'content': 'Owned'})
        other = self.db.create_task('Other')
        with self.assertRaises(PermissionError): resources.read(self.db, other, written['asset_id'])
        with self.assertRaises(PermissionError): resources.write(self.db, other, {'asset_id': written['asset_id'], 'content': 'Overwrite'})
        snapshot = self.db.base_dir(self.pid) / '.tasks/run/output.md';snapshot.parent.mkdir(parents=True)
        snapshot.write_text('Immutable delivery', encoding='utf-8')
        asset = asset_service.register_file(self.db, snapshot, project_id=self.pid, kind='report')
        with self.assertRaises(PermissionError): resources.write(self.db, self.pid, {'asset_id': asset['id'], 'content': 'Overwrite', 'expected_hash': asset['content_hash']})
        workflow.enforce_tool_policy('project_resources', {}, 'read_only')
        with self.assertRaises(PermissionError): workflow.enforce_tool_policy('project_resources', {'action':'index'}, 'read_only')
        with self.assertRaises(PermissionError): workflow.enforce_tool_policy('project_resource_write', {}, 'read_only')
