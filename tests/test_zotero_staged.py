from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import agent_integrations
import project_library
import workflow
import workflow_stages
import zotero
import zotero_staged
import zotero_sync
from _pdf_fixture import write_text_pdf


class ZoteroStagedImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="zotero-staged-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = workflow.WorkflowDB(self.root / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.task = self.db.create_task("Staged Zotero", base_dir=str(self.root / "project"))
        self.result_id = self.db.cache_result(self.task, {
            "source": "openalex", "title": "A staged paper", "authors": "Ada Lovelace; Grace Hopper",
            "year": "2025", "doi": "10.1234/STAGED", "url": "https://example.test/paper",
            "abstract": "Abstract",
        })
        self.pdf = Path(self.db.base_dir(self.task)) / "文献" / "staged.pdf"
        self.pdf.parent.mkdir(parents=True, exist_ok=True)
        self.pdf.write_bytes(b"%PDF-1.4 staged")
        self.db.set_queue(self.task, self.result_id, "downloaded", str(self.pdf), "")
        config = {'transport': 'local', 'local_profile': 'fixture'}
        local = patch.object(zotero_sync, 'config', return_value=config)
        request = patch.object(zotero_sync, 'request', return_value={'data': {'name': 'Project papers'}})
        local.start(); self.addCleanup(local.stop)
        request.start(); self.addCleanup(request.stop)
        zotero_sync.bind(self.db, self.task, 'COLLA001')

    def test_plan_apply_keeps_staged_file_and_records_import(self):
        with patch.object(zotero, "items", return_value={"items": []}), \
             patch.object(zotero, "add_item", return_value={"key": "ITEM0001"}) as add_item, \
             patch.object(zotero, "import_pdf", return_value={"key": "ATTCH001"}) as import_pdf, \
             patch.object(zotero, "item", return_value={"key": "ITEM0001", "version": 3, "collections": ['COLLA001']}):
            preview = zotero_staged.plan(self.db, self.task, [self.result_id], attachment_mode="plugin")
            self.assertEqual(preview["attachment_mode"], "imported_file")
            self.assertEqual(preview["actions"][0]["kind"], "create")
            self.assertFalse(preview["conflicts"])

            result = zotero_staged.apply(self.db, self.task, preview["plan_token"], confirm=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["applied"][0]["key"], "ITEM0001")
        add_item.assert_called_once()
        import_pdf.assert_called_once_with(self.db, "ITEM0001", str(self.pdf), "A staged paper")
        self.assertTrue(self.pdf.is_file())
        queue = self.db.queue(self.task)[0]
        self.assertEqual(queue["status"], "downloaded")
        self.assertEqual(queue["saved_path"], str(self.pdf))
        self.assertEqual(queue["zotero_status"], "imported")
        self.assertEqual(queue["zotero_key"], "ITEM0001")
        self.assertEqual(queue["zotero_attachment_key"], "ATTCH001")
        self.assertEqual(project_library.items(self.db, self.task)[0]["zotero_key"], "ITEM0001")
        self.assertEqual(zotero_staged.list_staged(self.db, self.task)["items"], [])

    def test_existing_mapping_skips_remote_match_search(self):
        import zotero_sync
        zotero_sync.ensure(self.db)
        self.db.db.execute(
            "INSERT INTO zotero_item_links VALUES (?,?,?,?,?)",
            (self.task, self.result_id, "ITEM0001", 2, "hash"),
        )
        self.db.db.commit()
        with patch.object(zotero, "items") as search, patch.object(zotero, 'item', return_value={'version': 2}):
            preview = zotero_staged.plan(self.db, self.task, [self.result_id])
        search.assert_not_called()
        self.assertEqual(preview["actions"][0]["kind"], "match")
        self.assertEqual(preview["actions"][0]["key"], "ITEM0001")

    def test_agent_write_requires_full_access_and_preview_is_allowed(self):
        with patch.object(zotero, "items", return_value={"items": []}):
            preview = agent_integrations.invoke(
                "zotero_staged_import", self.db, self.task,
                {"result_ids": [self.result_id], "dry_run": True}, permission="auto",
            )
        self.assertEqual(len(preview['actions']), 1)
        self.assertNotIn('plan_token', preview)
        with patch.object(zotero, 'items', return_value={'items': []}):
            writable = agent_integrations.invoke('zotero_staged_import', self.db, self.task,
                                                 {'result_ids': [self.result_id]}, permission='full_access')
        self.assertTrue(writable['plan_token'])
        with self.assertRaises(PermissionError):
            agent_integrations.invoke(
                "zotero_staged_import", self.db, self.task,
                {"result_ids": [self.result_id], "dry_run": False,
                 "plan_token": writable["plan_token"], "confirm": True}, permission="full_access",
            )

    def test_http_route_lists_staged_items(self):
        result = zotero_sync.route("GET", "/api/zotero/web/staged", {}, {"task_id": [self.task]}, self.db)
        self.assertEqual(result["task_id"], self.task)
        self.assertEqual([row["id"] for row in result["items"]], [self.result_id])

    def test_empty_selection_and_expired_preview_never_write(self):
        with patch.object(zotero, 'items', return_value={'items': []}), patch.object(zotero, 'add_item') as write:
            for ids in (None, []):
                with self.assertRaises(ValueError):
                    zotero_staged.plan(self.db, self.task, ids)
            preview = zotero_staged.plan(self.db, self.task, [self.result_id])
            self.db.db.execute('UPDATE zotero_save_plans SET expires=0')
            self.db.db.commit()
            with self.assertRaises(ValueError):
                zotero_staged.apply(self.db, self.task, preview['plan_token'], confirm=True)
            write.assert_not_called()

    def test_lost_create_response_reuses_item_and_preserves_original_collection(self):
        self.db.set_queue(self.task, self.result_id, 'manual')
        state = {}

        def create(_db, item):
            state.update(key='ITEM0001', version=3, collections=['COLLB001'], doi=item['DOI'])
            raise TimeoutError('response lost after commit')

        def update(_cfg, _path, method='GET', payload=None):
            if method == 'PATCH':
                self.assertEqual(payload['version'], 3)
                state.update(collections=payload['collections'], version=4)
            return {'data': {'name': 'Project papers'}}

        with patch.object(zotero, 'items', side_effect=lambda **_: {'items': [state] if state else []}), \
             patch.object(zotero, 'add_item', side_effect=create) as write, \
             patch.object(zotero, 'item', side_effect=lambda _: state), patch.object(zotero_sync, 'request', side_effect=update):
            first = zotero_staged.plan(self.db, self.task, [self.result_id])
            self.assertFalse(zotero_staged.apply(self.db, self.task, first['plan_token'], confirm=True)['ok'])
            second = zotero_staged.plan(self.db, self.task, [self.result_id])
            self.assertTrue(zotero_staged.apply(self.db, self.task, second['plan_token'], confirm=True)['ok'])
        write.assert_called_once()
        self.assertEqual(state['collections'], ['COLLB001', 'COLLA001'])

    def test_temporary_pdf_removed_only_after_reference_can_read_original(self):
        import project_sources
        import paths
        from asset_service import sha256_file
        app_cache = self.root / 'cache'
        (app_cache / 'zotero-downloads').mkdir(parents=True)
        (self.root / 'zotero').mkdir()
        staged = write_text_pdf(app_cache / 'zotero-downloads' / 'temp.pdf', ['one physical Zotero attachment'])
        original = write_text_pdf(self.root / 'zotero' / 'original.pdf', ['fixture'])
        original.write_bytes(staged.read_bytes())
        self.db.db.execute('INSERT INTO zotero_downloads VALUES (?,?,?,?)',
                           (self.task, self.result_id, str(staged), sha256_file(staged)))
        self.db.db.commit()
        with patch.object(paths, 'CACHE', app_cache), patch.object(zotero, 'items', return_value={'items': []}), \
             patch.object(zotero, 'add_item', return_value={'key': 'ITEM0001'}), \
             patch.object(zotero, 'item', return_value={'collections': ['COLLA001'], 'version': 3}), \
             patch.object(zotero, 'import_pdf', return_value={'key': 'ATTCH001'}), \
             patch.object(zotero, 'pdf_attachment', return_value={'key': 'ATTCH001', 'path': str(original)}):
            preview = zotero_staged.plan(self.db, self.task, [self.result_id])
            out = zotero_staged.apply(self.db, self.task, preview['plan_token'], confirm=True)
            self.assertTrue(out['ok'], out)
            self.assertFalse(staged.exists())
            self.assertTrue(self.pdf.is_file())
            row = project_library.items(self.db, self.task)[0]
            self.assertEqual(row['source_path'], '')
            self.assertEqual(project_sources.resolve_pdf_for_read(self.db, self.task, row['paper_id']), original)

    def test_workflow_policy_treats_preview_as_read_only(self):
        workflow.enforce_tool_policy("zotero_staged_import", {"dry_run": True}, "auto")
        with self.assertRaises(PermissionError):
            workflow.enforce_tool_policy("zotero_staged_import", {"dry_run": False}, "auto")

    def test_switch_matching_copy_and_recheck_evidence_after_attachment_changes(self):
        import project_sources

        write_text_pdf(self.pdf, ['Original evidence on page one.'])
        original = self.root / 'original.pdf'
        original.write_bytes(self.pdf.read_bytes())
        paper_id = self.db.ingest_pdf(self.task, self.pdf)['id']
        project_library.link_pdf(self.db, self.task, self.result_id, paper_id)
        zotero_sync.record_membership(self.db, self.task, 'local:fixture/users/0', self.result_id, 'ITEM0001', 3)
        evidence = self.db.add_evidence(paper_id, {
            'page_no': 1, 'field': 'finding', 'value': 'original', 'excerpt': 'Original evidence on page one.',
        })
        with patch.object(zotero, 'pdf_attachment', return_value={'key': 'ATTCH001', 'path': str(original)}):
            linked = project_sources.use_zotero_reference(self.db, self.task, paper_id)
            self.assertEqual(linked['paper_id'], paper_id)
            self.assertEqual(self.pdf.read_bytes(), original.read_bytes())
            self.assertEqual(self.db.evidence(self.task)[0]['needs_recheck'], 0)
            write_text_pdf(original, ['Updated evidence on page one.'])
            self.assertIn('Updated evidence', self.db.paper(paper_id)['pages'][0]['text'])
            row = self.db.evidence(self.task)[0]
            self.assertEqual((row['id'], row['needs_recheck']), (evidence['id'], 1))
            self.assertNotEqual(self.pdf.read_bytes(), original.read_bytes())

    def test_rebuilding_download_queue_clears_old_zotero_state(self):
        self.db.set_zotero_stage(self.task, self.result_id, "imported", "ITEM0001", "ATTCH001")
        self.db.enqueue(self.task, [self.result_id])
        row = self.db.queue(self.task)[0]
        self.assertEqual(row["zotero_status"], "staged")
        self.assertEqual(row["zotero_key"], "")

    def test_download_stage_only_writes_project_staging(self):
        source = Path(self.db.base_dir(self.task)) / "文献" / "downloaded.pdf"
        write_text_pdf(source, ["staged"])
        with patch.object(workflow_stages, "download_pdf", return_value=source), \
             patch.object(zotero, "add_item") as add_item, \
             patch.object(zotero, "import_pdf") as import_pdf, \
             patch.object(zotero, "link_pdf") as link_pdf:
            result = workflow_stages.stage_download(self.db, self.task, self.result_id)
        self.assertEqual(result["pdf_path"], str(source))
        add_item.assert_not_called()
        import_pdf.assert_not_called()
        link_pdf.assert_not_called()
        self.assertEqual(self.db.queue(self.task)[0]["zotero_status"], "staged")


if __name__ == "__main__":
    unittest.main()
