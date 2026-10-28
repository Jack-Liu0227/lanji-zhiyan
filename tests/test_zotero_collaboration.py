from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import project_library
import service_settings
import workflow
import zotero
import zotero_sync as sync
import zotero_links
import zotero_sources


class Remote:
    def __init__(self):
        self.version = 1
        self.rows = {}
        self.reads = []
        self.writes = []

    def add(self, key="PAPER001", collections=None, **fields):
        data = {"key": key, "version": self.version, "itemType": "journalArticle", "title": "Evidence",
                "DOI": "10.123/evidence", "date": "2024-06-12", "collections": collections or [], "tags": [],
                "creators": [{"creatorType": "author", "firstName": "Alice", "lastName": "Chen"},
                             {"creatorType": "editor", "firstName": "Bob", "lastName": "Smith"}], **fields}
        self.rows[key] = {"key": key, "version": self.version, "data": data}
        return deepcopy(self.rows[key])

    def pages(self, config, path):
        self.reads.append(path)
        since = int(path.partition('since=')[2] or 0)
        return sync.Page([deepcopy(r) for r in self.rows.values() if r['version'] > since], self.version)

    def request(self, config, path, method="GET", payload=None):
        if method == "GET":
            if path.startswith('/deleted'):
                return {'items': []}
            if path.startswith('/items/'):
                key = path.split('/')[-1]
                if key not in self.rows:
                    raise ValueError('404 item removed')
                return deepcopy(self.rows[key])
            if path.startswith('/collections/'):
                return {'key': path.split('/')[-1]}
        self.writes.append((path, deepcopy(payload)))
        if path == '/items' and method == 'POST':
            item = deepcopy(payload[0])
            key = item.get('key') or f'NEW{len(self.rows):05d}'
            if key in self.rows and item['version'] != self.rows[key]['version']:
                raise ValueError('412 stale version')
            self.version += 1
            item.update(key=key, version=self.version)
            self.rows[key] = {'key': key, 'version': self.version, 'data': item}
            return {'successful': {'0': deepcopy(self.rows[key])}}
        raise AssertionError((path, method))


class ZoteroCollaborationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = workflow.WorkflowDB(Path(temporary.name) / 'test.sqlite3')
        self.addCleanup(self.db.close)
        self.pid = self.db.create_task('Project A', base_dir=str(Path(temporary.name) / 'A'))
        self.remote = Remote()
        self.cfg = {'library_type': 'users', 'library_id': '123', 'api_key': 'test'}
        for target, replacement in [('config', lambda db: self.cfg), ('all_pages', self.remote.pages), ('request', self.remote.request)]:
            active = patch.object(sync, target, side_effect=replacement)
            active.start()
            self.addCleanup(active.stop)
        sync.bind(self.db, self.pid, 'COLLA001')

    def apply(self, pid=None, mode='merge'):
        pid = pid or self.pid
        plan = sync.sync(self.db, pid, mode)
        self.assertEqual(plan['conflicts'], [], plan)
        result = sync.sync(self.db, pid, mode, False, plan['plan_token'])
        self.assertTrue(result['ok'], result)
        return result

    def test_title_edit_preserves_structured_creators_full_date_and_foreign_tags(self):
        original = self.remote.add(collections=['COLLA001'], tags=[{'tag': 'manual-topic', 'type': 0}])
        self.apply()
        row = project_library.records(self.db, self.pid)[0]
        project_library.update_record(self.db, self.pid, row['id'], {'title': 'Corrected title'})
        self.apply()
        updated = self.remote.rows['PAPER001']['data']
        self.assertEqual(updated['creators'], original['data']['creators'])
        self.assertEqual(updated['date'], '2024-06-12')
        self.assertEqual(updated['title'], 'Corrected title')
        self.assertEqual({t['tag'] for t in updated['tags']}, {'manual-topic', sync.project_tag(self.pid)})

    def test_second_project_reuses_library_item_without_creating_duplicate(self):
        self.remote.add(collections=['COLLA001'])
        self.apply()
        other = self.db.create_task('Project B')
        sync.bind(self.db, other, 'COLLB001')
        self.db.cache_result(other, {'source': 'crossref', 'title': 'Evidence', 'doi': 'https://doi.org/10.123/EVIDENCE'})
        self.apply(other)
        self.assertEqual(len(self.remote.rows), 1)
        item = self.remote.rows['PAPER001']['data']
        self.assertEqual(set(item['collections']), {'COLLA001'})
        self.assertEqual({t['tag'] for t in item['tags']}, {sync.project_tag(self.pid), sync.project_tag(other)})
        self.assertEqual(project_library.items(self.db, other)[0]['zotero_key'], 'PAPER001')
        record = self.db.results(self.pid)[0]
        project_library.update_record(self.db, self.pid, record['id'], {'title': 'Edited after project B added its tag'})
        self.apply()
        updated = self.remote.rows['PAPER001']['data']
        self.assertEqual(updated['title'], 'Edited after project B added its tag')
        self.assertEqual(set(updated['collections']), {'COLLA001'})

    def test_incremental_reads_keep_unchanged_objects(self):
        self.remote.add(collections=['COLLA001'])
        self.remote.add('PAPER002', ['COLLA001'], title='Other', DOI='10.123/other')
        self.apply(mode='pull')
        self.remote.version += 1
        self.remote.add(collections=['COLLA001'], title='Updated remotely')
        self.apply(mode='pull')
        self.assertTrue(any('since=' in p for p in self.remote.reads))
        self.assertEqual({r['title'] for r in self.db.results(self.pid)}, {'Updated remotely', 'Other'})

    def test_project_collection_can_be_reused_by_other_project(self):
        other = self.db.create_task('Other')
        sync.bind(self.db, other, 'COLLA001')
        self.assertEqual(sync.binding(self.db, other)['sources'], [{'collection': 'COLLA001', 'mode': 'all'}])

    def test_local_transport_uses_existing_local_authorization_without_web_key(self):
        local = sync.LocalConfig({'transport': 'local', 'local_profile': 'fixture', 'base_url': 'http://127.0.0.1:23119'}, self.db)
        # The public request function was patched for this fixture; call its saved original below.
        with patch.object(zotero, '_write', return_value={'successful': {}}) as write:
            ORIGINAL_REQUEST(local, '/items', 'POST', [{'itemType': 'journalArticle', 'title': 'Local'}])
            self.assertEqual(write.call_args.args[1], '/users/0/items')
            self.assertIs(write.call_args.args[0], self.db)

    def test_batch_unlink_and_restore_preserve_shared_item_and_do_not_reimport(self):
        self.remote.add(collections=['COLLA001'], tags=[{'tag': 'manual'}])
        self.apply()
        rid = self.db.results(self.pid)[0]['id']
        other = self.db.create_task('Project B')
        sync.bind(self.db, other, 'COLLB001')
        self.db.cache_result(other, {'source': 'crossref', 'title': 'Evidence', 'doi': '10.123/evidence'})
        self.apply(other)
        batch = zotero_links.preview(self.db, self.pid)
        self.assertEqual(batch['data']['items'][0]['shared_projects'], ['Project B'])
        result = zotero_links.apply(self.db, batch['id'])
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(self.db.results(self.pid), [])
        self.assertEqual(self.db.result(rid)['title'], 'Evidence')
        self.assertEqual(len(self.db.results(other)), 1)
        item = self.remote.rows['PAPER001']['data']
        self.assertEqual(item['collections'], ['COLLA001'])
        self.assertEqual({t['tag'] for t in item['tags']}, {'manual', sync.project_tag(other)})
        self.apply()
        self.assertEqual(self.db.results(self.pid), [])
        self.db.cache_result(self.pid, {'source': 'new-search', 'title': 'Evidence again', 'doi': '10.123/evidence'})
        self.assertEqual(self.db.results(self.pid), [])
        self.assertEqual(zotero_links.apply(self.db, batch['id'])['status'], 'applied')
        self.assertEqual(zotero_links.apply(self.db, batch['id'], restore=True)['status'], 'restored')
        self.assertEqual(set(self.remote.rows['PAPER001']['data']['collections']), {'COLLA001'})
        self.assertTrue(self.db.results(self.pid))
        self.assertEqual(len(self.remote.rows), 1)

    def test_partial_batch_can_retry_after_remote_write_without_repeating_it(self):
        self.remote.add(collections=['COLLA001'])
        self.apply()
        batch = zotero_links.preview(self.db, self.pid)
        original = self.remote.request
        failed = []
        def lost_response(cfg, path, method='GET', payload=None):
            result = original(cfg, path, method, payload)
            if method == 'POST' and not failed:
                failed.append(True)
                raise ConnectionError('Response lost after server applied update')
            return result
        with patch.object(sync, 'request', side_effect=lost_response):
            self.assertEqual(zotero_links.apply(self.db, batch['id'])['status'], 'partial')
        count = len(self.remote.writes)
        self.assertEqual(zotero_links.apply(self.db, batch['id'])['status'], 'applied')
        self.assertEqual(len(self.remote.writes), count)

    def test_partial_restore_can_only_retry_restore_and_preserves_new_tags(self):
        self.remote.add(collections=['COLLA001'])
        self.apply()
        batch = zotero_links.preview(self.db, self.pid)
        zotero_links.apply(self.db, batch['id'])
        original = self.remote.request
        failed = []
        def interrupted(cfg, path, method='GET', payload=None):
            result = original(cfg,path,method,payload)
            if method=='POST' and not failed:
                failed.append(True)
                raise ConnectionError('Lost restore response')
            return result
        with patch.object(sync,'request',side_effect=interrupted):
            result=zotero_links.apply(self.db,batch['id'],restore=True)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['data']['operation'],'restore')
        with self.assertRaisesRegex(ValueError,'已恢复'):
            zotero_links.apply(self.db,batch['id'])
        self.remote.rows['PAPER001']['data']['tags'].append({'tag':'added-after-restore'})
        self.assertEqual(zotero_links.apply(self.db,batch['id'],restore=True)['status'],'restored')
        self.assertIn('added-after-restore',{t['tag'] for t in self.remote.rows['PAPER001']['data']['tags']})

    def test_conflict_resolution_checks_both_versions_and_preserves_other_items(self):
        self.remote.add(collections=['COLLA001'])
        self.apply()
        rid = self.db.results(self.pid)[0]['id']
        project_library.update_record(self.db, self.pid, rid, {'title': 'Local edit'})
        self.remote.version += 1
        self.remote.add(collections=['COLLA001'], title='Remote edit', tags=[{'tag': sync.project_tag(self.pid)}])
        self.remote.add('PAPER002', ['COLLA001'], title='Independent addition', DOI='10.123/second')
        plan = sync.sync(self.db, self.pid)
        self.assertEqual(len(plan['conflicts']), 1)
        result = sync.sync(self.db, self.pid, dry_run=False, plan_token=plan['plan_token'], skip_conflicts=True)
        self.assertTrue(result['ok'])
        self.assertIn('Independent addition', [r['title'] for r in self.db.results(self.pid)])
        comparison = zotero_links.comparison(self.db, self.pid, rid, 'PAPER001')
        body = {**comparison, 'result_id': rid, 'decision': 'zotero'}
        with self.assertRaisesRegex(ValueError, '已变化'):
            zotero_links.resolve(self.db, self.pid, {**body, 'version': 0})
        self.assertTrue(zotero_links.resolve(self.db, self.pid, body)['ok'])
        self.assertEqual(self.db.result(rid)['title'], 'Remote edit')

    def test_source_change_does_not_invalidate_membership_removal(self):
        self.remote.add(collections=['COLLA001'])
        self.apply()
        batch = zotero_links.preview(self.db, self.pid)
        sync.bind(self.db, self.pid, 'NEWCOL01')
        self.assertEqual(zotero_links.apply(self.db, batch['id'])['status'], 'applied')
        self.assertEqual(self.remote.rows['PAPER001']['data']['collections'], ['COLLA001'])

    def test_multiple_sources_import_only_selected_keys_and_deduplicate_overlap(self):
        self.remote.add('PAPER001', ['COLLA001', 'COLLB001'])
        self.remote.add('PAPER002', ['COLLA001'], title='Unselected A', DOI='10.123/a2')
        self.remote.add('PAPER003', ['COLLB001'], title='Selected B', DOI='10.123/b1')
        self.remote.add('PAPER004', ['COLLB001'], title='Unselected B', DOI='10.123/b2')
        zotero_sources.configure(self.db, self.pid, [{'collection': 'COLLA001'}, {'collection': 'COLLB001'}])
        self.assertEqual(len(zotero_sources.candidates(self.db, self.pid)['items']), 4)
        self.apply()
        self.assertEqual(project_library.items(self.db, self.pid), [])
        zotero_sources.select(self.db, self.pid, ['PAPER001', 'PAPER003', 'PAPER001'])
        self.apply()
        self.assertEqual({r['zotero_key'] for r in project_library.items(self.db, self.pid)}, {'PAPER001', 'PAPER003'})
        for key, item in self.remote.rows.items():
            self.assertEqual(any(t['tag'] == sync.project_tag(self.pid) for t in item['data']['tags']), key in ('PAPER001', 'PAPER003'))
        self.assertEqual(self.remote.rows['PAPER003']['data']['collections'], ['COLLB001'])
        self.remote.version += 1
        self.remote.add('PAPER005', ['COLLA001'], title='Later addition', DOI='10.123/later')
        self.apply()
        self.assertEqual(len(project_library.items(self.db, self.pid)), 2)

    def test_projects_choose_different_subsets_from_same_collection_and_remove_independently(self):
        self.remote.add('PAPER001', ['COLLA001', 'COLLB001'])
        self.remote.add('PAPER002', ['COLLA001'], title='For B', DOI='10.123/b')
        other = self.db.create_task('Project B')
        for pid, keys in [(self.pid, ['PAPER001']), (other, ['PAPER001', 'PAPER002'])]:
            zotero_sources.configure(self.db, pid, [{'collection': 'COLLA001'}])
            zotero_sources.select(self.db, pid, keys)
            self.apply(pid)
        batch = zotero_links.preview(self.db, self.pid)
        zotero_links.apply(self.db, batch['id'])
        self.apply()
        self.assertEqual(project_library.items(self.db, self.pid), [])
        self.assertEqual(len(project_library.items(self.db, other)), 2)
        self.assertEqual(self.remote.rows['PAPER001']['data']['collections'], ['COLLA001', 'COLLB001'])
        self.assertEqual({t['tag'] for t in self.remote.rows['PAPER001']['data']['tags']}, {sync.project_tag(other)})
        zotero_links.apply(self.db, batch['id'], restore=True)
        self.apply()
        self.assertEqual(len(project_library.items(self.db, self.pid)), 1)

    def test_item_moved_out_of_sources_and_source_removal_preserve_membership(self):
        self.remote.add(collections=['COLLA001'])
        self.apply()
        self.remote.version += 1
        self.remote.add(collections=['UNUSED01'], title='Moved item')
        self.apply()
        zotero_sources.configure(self.db, self.pid, [{'collection': 'COLLB001'}])
        self.apply()
        self.assertEqual(project_library.items(self.db, self.pid)[0]['title'], 'Moved item')
        self.assertEqual(self.remote.rows['PAPER001']['data']['collections'], ['UNUSED01'])

    def test_selection_is_atomic_and_invalidates_preview_and_old_restore(self):
        self.remote.add(collections=['COLLA001'])
        zotero_sources.configure(self.db, self.pid, [{'collection': 'COLLA001'}])
        plan = sync.sync(self.db, self.pid)
        with self.assertRaisesRegex(ValueError, '移出来源'):
            zotero_sources.select(self.db, self.pid, ['PAPER001', 'MISSING1'])
        self.assertFalse(self.db.db.execute('SELECT 1 FROM zotero_project_memberships').fetchone())
        zotero_sources.select(self.db, self.pid, ['PAPER001'])
        with self.assertRaisesRegex(ValueError, '预览已过期'):
            sync.sync(self.db, self.pid, dry_run=False, plan_token=plan['plan_token'])
        self.apply()
        batch = zotero_links.preview(self.db, self.pid)
        zotero_links.apply(self.db, batch['id'])
        zotero_sources.select(self.db, self.pid, ['PAPER001'])
        self.assertEqual(zotero_links.apply(self.db, batch['id'], restore=True)['status'], 'partial')
        self.apply()
        self.assertEqual(len(project_library.items(self.db, self.pid)), 1)

    def test_no_doi_same_title_distinct_keys_are_not_collapsed(self):
        self.remote.add('PAPER001', ['COLLA001'], DOI='')
        self.remote.add('PAPER002', ['COLLB001'], DOI='')
        zotero_sources.configure(self.db, self.pid, [{'collection': 'COLLA001'}, {'collection': 'COLLB001'}])
        zotero_sources.select(self.db, self.pid, ['PAPER001', 'PAPER002'])
        self.apply()
        self.apply()
        self.assertEqual({r['zotero_key'] for r in project_library.items(self.db, self.pid)}, {'PAPER001', 'PAPER002'})

    def test_duplicate_doi_selected_keys_require_a_choice_before_import(self):
        self.remote.add('PAPER001', ['COLLA001'])
        self.remote.add('PAPER002', ['COLLB001'])
        zotero_sources.configure(self.db, self.pid, [{'collection': 'COLLA001'}, {'collection': 'COLLB001'}])
        zotero_sources.select(self.db, self.pid, ['PAPER001', 'PAPER002'])
        plan = sync.sync(self.db, self.pid)
        self.assertEqual(len(plan['conflicts']), 2)
        self.assertEqual(plan['actions'], [])
        batch = zotero_links.preview(self.db, self.pid, ['PAPER002'])
        zotero_links.apply(self.db, batch['id'])
        # Excluding the duplicate must not hide the explicitly retained same-DOI member.
        self.apply()
        self.assertEqual({r['zotero_key'] for r in project_library.items(self.db, self.pid)}, {'PAPER001'})

    def test_all_mode_is_explicit_and_exclusion_survives_source_changes(self):
        self.remote.add(collections=['COLLA001'])
        zotero_sources.configure(self.db, self.pid, [{'collection': 'COLLA001', 'mode': 'all'}])
        self.apply()
        batch = zotero_links.preview(self.db, self.pid)
        zotero_links.apply(self.db, batch['id'])
        zotero_sources.configure(self.db, self.pid, [{'collection': 'COLLA001', 'mode': 'all'}, {'collection': 'COLLB001'}])
        self.remote.version += 1
        self.remote.add('PAPER002', ['COLLA001'], title='New A', DOI='10.123/new-a')
        self.remote.add('PAPER003', ['COLLB001'], title='New B', DOI='10.123/new-b')
        self.apply()
        self.assertEqual({r['zotero_key'] for r in project_library.items(self.db, self.pid)}, {'PAPER002'})

    def test_legacy_migration_preserves_explicit_whole_collection_scope(self):
        self.db.db.execute('DELETE FROM zotero_project_sources')
        self.db.db.executescript(Path('migrations/017_zotero_project_sources.sql').read_text(encoding='utf-8'))
        self.assertEqual(sync.binding(self.db, self.pid)['sources'], [{'collection': 'COLLA001', 'mode': 'all'}])
        self.remote.add(collections=['COLLA001'])
        self.apply()
        self.assertEqual(len(project_library.items(self.db, self.pid)), 1)

    def test_pending_selection_deleted_before_first_sync_is_reported_and_can_detach(self):
        self.remote.add(collections=['COLLA001'])
        zotero_sources.configure(self.db, self.pid, [{'collection': 'COLLA001'}])
        zotero_sources.select(self.db, self.pid, ['PAPER001'])
        self.remote.rows.clear()
        with patch.object(sync, 'library_items', return_value=[]):
            plan = sync.sync(self.db, self.pid)
            self.assertEqual([r['key'] for r in plan['conflicts']], ['PAPER001'])
            batch = zotero_links.preview(self.db, self.pid, ['PAPER001'])
            self.assertEqual(zotero_links.apply(self.db, batch['id'])['status'], 'applied')
            self.apply()
        self.assertFalse(project_library.items(self.db, self.pid))

    def test_workflow_selected_import_outside_sources_uses_same_sync_mapping(self):
        remote = self.remote.add(collections=['COLLB001'])
        zotero_sources.configure(self.db, self.pid, [{'collection': 'COLLA001'}])
        normalized = {**sync._from_remote(remote), 'key': 'PAPER001', 'version': remote['version'],
                      'creators': ['Alice Chen'], 'type': 'journalArticle'}
        with patch.object(zotero, 'item', return_value=normalized), patch.object(zotero, 'pdf_path', return_value=''):
            workflow.stage_zotero_import(self.db, self.pid, keys=['PAPER001'])
        before = project_library.items(self.db, self.pid)[0]
        self.apply()
        after = project_library.items(self.db, self.pid)
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]['id'], before['id'])
        self.assertEqual(after[0]['zotero_key'], 'PAPER001')
        self.assertEqual(self.remote.rows['PAPER001']['data']['collections'], ['COLLB001'])
        self.assertIn(sync.project_tag(self.pid), {t['tag'] for t in self.remote.rows['PAPER001']['data']['tags']})

    def test_pull_only_members_can_be_removed_without_tags_or_collection_changes(self):
        self.remote.add(collections=['COLLA001'])
        self.apply(mode='pull')
        batch = zotero_links.preview(self.db, self.pid)
        self.assertEqual([r['key'] for r in batch['data']['items']], ['PAPER001'])
        zotero_links.apply(self.db, batch['id'])
        self.assertFalse(project_library.items(self.db, self.pid))
        self.assertEqual(self.remote.rows['PAPER001']['data']['collections'], ['COLLA001'])
        self.assertEqual(self.remote.writes, [])

    def test_importing_attachment_does_not_reset_pending_local_metadata_edit(self):
        remote = self.remote.add(collections=['COLLA001'])
        self.apply()
        rid = project_library.items(self.db,self.pid)[0]['result_id']
        project_library.update_record(self.db,self.pid,rid,{'title':'Local edit before PDF import'})
        normalized = {**sync._from_remote(remote),'key':'PAPER001','version':remote['version'],'creators':['Alice Chen'],'type':'journalArticle'}
        with patch.object(zotero,'item',return_value=normalized), patch.object(zotero,'pdf_path',return_value=''):
            workflow.stage_zotero_import(self.db,self.pid,keys=['PAPER001'])
        self.apply()
        self.assertEqual(self.remote.rows['PAPER001']['data']['title'],'Local edit before PDF import')
        self.assertEqual(self.db.result(rid)['title'],'Local edit before PDF import')


ORIGINAL_REQUEST = sync.request
