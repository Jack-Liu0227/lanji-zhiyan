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
        self.tmp = tempfile.TemporaryDirectory(prefix="zotero-staged-")
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

    def test_plan_apply_keeps_staged_file_and_records_import(self):
        with patch.object(zotero, "items", return_value={"items": []}), \
             patch.object(zotero, "add_item", return_value={"key": "ITEM0001"}) as add_item, \
             patch.object(zotero, "import_pdf", return_value={"key": "ATTCH001"}) as import_pdf, \
             patch.object(zotero, "item", return_value={"key": "ITEM0001", "version": 3}):
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
        with patch.object(zotero, "items") as search:
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
        self.assertTrue(preview["plan_token"])
        with self.assertRaises(PermissionError):
            agent_integrations.invoke(
                "zotero_staged_import", self.db, self.task,
                {"result_ids": [self.result_id], "dry_run": False,
                 "plan_token": preview["plan_token"], "confirm": True}, permission="auto",
            )

    def test_http_route_lists_staged_items(self):
        result = zotero_sync.route("GET", "/api/zotero/web/staged", {}, {"task_id": [self.task]}, self.db)
        self.assertEqual(result["task_id"], self.task)
        self.assertEqual([row["id"] for row in result["items"]], [self.result_id])

    def test_workflow_policy_treats_preview_as_read_only(self):
        workflow.enforce_tool_policy("zotero_staged_import", {"dry_run": True}, "auto")
        with self.assertRaises(PermissionError):
            workflow.enforce_tool_policy("zotero_staged_import", {"dry_run": False}, "auto")

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
