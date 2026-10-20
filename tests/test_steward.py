"""Steward skill discovery, project isolation and Zotero copy behavior."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import agent_integrations
import asset_service
import capabilities
import fileguard
import paths
import project_sources
import workflow
import zotero
from _pdf_fixture import write_text_pdf


class StewardTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="steward-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = workflow.WorkflowDB(self.root / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.a = self.db.create_task("Project A")
        self.b = self.db.create_task("Project B")

    def test_existing_project_selection_and_rebinding_keep_outputs_scoped(self):
        chat = self.db.create_chat("Research")
        self.db.bind_chat(chat, self.a)
        self.assertTrue(self.db.out_dir(chat).is_relative_to(self.db.base_dir(self.a)))
        self.db.bind_chat(chat, self.b)
        self.assertTrue(self.db.out_dir(chat).is_relative_to(self.db.base_dir(self.b)))
        projects = workflow._tool_impl("project_list", self.db, self.b, {})
        self.assertEqual(projects["current_project_id"], self.b)
        self.assertEqual({p["id"] for p in projects["projects"]}, {self.a, self.b})
        for args in ({"task_id": self.b}, {"project_id": self.b}, {"project": self.b}, {"path": "../outside.md"}):
            with self.subTest(args=args), self.assertRaises(PermissionError):
                workflow.enforce_project_scope(self.db, self.a, "zotero_sync", args)

    def test_skill_is_loaded_without_project_and_project_override_is_preserved(self):
        chat = self.db.create_chat("Global")
        with patch.object(workflow, "chat_raw", return_value={"content": "ok"}) as model:
            workflow.run_agent(self.db, chat, "Organize my work", profile={})
        self.assertIn("[Active skill: research-steward]", model.call_args.args[1][1]["content"])
        project = self.db.base_dir(self.a)
        path = project / ".agents/skills/research-steward/SKILL.md"
        path.write_text("---\nname: research-steward\ndescription: Local override\n---\nLocal instructions", encoding="utf-8")
        capabilities.install_steward_skill(project)
        self.assertIn("Local instructions", capabilities.steward_context(self.db, "builtin-steward", str(project)))
        capabilities.route("POST", "/api/capabilities/binding", {
            "assistant_id": "builtin-steward", "kind": "skill", "item_id": "research-steward", "enabled": False}, {}, self.db)
        self.assertEqual(capabilities.steward_context(self.db, "builtin-steward", str(project)), "")

    def test_skill_references_are_readable_but_cannot_escape(self):
        project = str(self.db.base_dir(self.a))
        args = {"skill_id": "research-steward", "resource": "references/actions.md"}
        content = capabilities.invoke_tool(self.db, "builtin-steward", "skill_read", args, project)
        self.assertIn("workflow_create", content["content"])
        for relative in ("../secret.txt", "references/../../secret.txt", "C:/secret.txt", "/secret.txt"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                capabilities.invoke_tool(self.db, "builtin-steward", "skill_read", {**args, "resource": relative}, project)

    def test_zotero_copies_are_independent_reusable_and_project_owned(self):
        library = self.root / "Zotero/storage/ABCD1234"
        library.mkdir(parents=True)
        source = write_text_pdf(library / "paper.pdf", ["Evidence in original source"])
        original = source.read_bytes()
        modified = source.stat().st_mtime_ns
        with patch.object(zotero, "item", return_value={"title": "Paper", "version": 3}), patch.object(zotero, "pdf_path", return_value=str(source)):
            first = agent_integrations.invoke("zotero_import_pdf", self.db, self.a, {"key": "ABCD1234"})
            second = project_sources.import_zotero_pdf(self.db, self.a, "ABCD1234")
            other = project_sources.import_zotero_pdf(self.db, self.b, "ABCD1234")
            self.assertTrue(second["reused"])
            self.assertEqual(first["paper_id"], second["paper_id"])
            self.assertNotEqual(first["paper_id"], other["paper_id"])
            for result, project in ((first, self.a), (other, self.b)):
                dest = Path(result["path"])
                self.assertTrue(dest.is_relative_to(self.db.base_dir(project)))
                self.assertFalse(dest.samefile(source))
                self.assertEqual(self.db.paper(result["paper_id"])["task_id"], project)
                metadata = json.loads(dest.with_suffix(".source.json").read_text(encoding="utf-8"))
                self.assertEqual(metadata["source_sha256"], asset_service.sha256_file(source))
            Path(first["path"]).write_bytes(original + b"\n% local annotation")
            fresh = project_sources.import_zotero_pdf(self.db, self.a, "ABCD1234")
            self.assertFalse(fresh["reused"])
            self.assertNotEqual(first["path"], fresh["path"])
            self.assertTrue(Path(first["path"]).read_bytes().endswith(b"% local annotation"))
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(source.stat().st_mtime_ns, modified)

    def test_read_only_agent_can_list_and_read_mail_but_cannot_create(self):
        for name in ("mail_accounts", "mail_list", "mail_read", "skill_read"):
            workflow.enforce_tool_policy(name, {}, "read_only")
        for name in ("assistant_manage", "project_task_manage", "schedule_manage"):
            workflow.enforce_tool_policy(name, {"action": "list"}, "read_only")
            with self.assertRaises(PermissionError):
                workflow.enforce_tool_policy(name, {"action": "create"}, "read_only")
        with self.assertRaises(PermissionError), patch.object(zotero, "pdf_path") as read:
            agent_integrations.invoke("zotero_import_pdf", self.db, self.a, {"key": "ABCD1234"}, permission="read_only")
        read.assert_not_called()

    def test_invalid_expert_does_not_leave_partial_team(self):
        count = self.db.db.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
        with self.assertRaises((ValueError, KeyError)):
            workflow._tool_impl("team_create", self.db, self.a, {"name": "Team", "assistant_ids": ["builtin-steward", "missing"]})
        self.assertEqual(self.db.db.execute("SELECT COUNT(*) FROM teams").fetchone()[0], count)

    def test_new_team_can_receive_work_and_other_project_team_is_blocked(self):
        import team
        created = workflow._tool_impl("team_create", self.db, self.a, {"name": "Research team"})
        tid = created["team"]["id"]
        with patch.object(team, "DB_PATH", self.db.path):
            board = workflow._tool_impl("team_board", self.db, self.a, {"team_id": tid})
            self.assertEqual(board["members"][0]["id"], created["members"][0]["id"])
            assigned = workflow._tool_impl("team_assign", self.db, self.a, {
                "team_id": tid, "member": board["members"][0]["id"], "prompt": "Prepare a literature matrix"})
            self.assertEqual(assigned["status"], "queued")
            self.assertEqual(assigned["task_id"], tid)
            with self.assertRaises(PermissionError):
                workflow._tool_impl("team_board", self.db, self.b, {"team_id": tid})

    def test_legacy_managed_project_and_linked_attachment_writes_are_rejected(self):
        linked = self.root / "linked-papers"
        linked.mkdir()
        source = linked / "note.md"
        source.write_text("original", encoding="utf-8")
        with patch.object(zotero, "linked_attachment_roots", return_value=[linked]):
            for call in (lambda: paths.validate_project_dir(linked),
                         lambda: fileguard.before_write(source),
                         lambda: asset_service.atomic_write_text(source, "changed")):
                with self.assertRaisesRegex(ValueError, "Zotero"):
                    call()
            self.db.db.execute("UPDATE research_tasks SET base_dir=? WHERE id=?", (str(linked), self.a))
            self.db.db.commit()
            chat = self.db.create_chat("Legacy")
            with self.assertRaisesRegex(ValueError, "Zotero"):
                self.db.bind_chat(chat, self.a)
            self.assertEqual(self.db.chat(chat)["task_id"], "")
        self.assertEqual(source.read_text(encoding="utf-8"), "original")

    def test_linked_relative_attachment_uses_configured_base_only(self):
        linked = self.root / "linked-papers"
        linked.mkdir()
        pdf = write_text_pdf(linked / "paper.pdf", ["Linked PDF"])
        child = {"data": {"itemType": "attachment", "contentType": "application/pdf", "path": "attachments:paper.pdf"}}
        with patch.object(zotero, "_get", return_value=(200, {}, [child])), patch.object(zotero, "linked_attachment_roots", return_value=[linked]):
            self.assertEqual(zotero.pdf_path("ABCD1234"), str(pdf.resolve()))
            child["data"]["path"] = "attachments:../outside.pdf"
            write_text_pdf(self.root / "outside.pdf", ["Not this document"])
            self.assertEqual(zotero.pdf_path("ABCD1234"), "")

    def test_known_linked_source_remains_read_only_after_reopening_and_through_hardlink(self):
        source = write_text_pdf(self.root / "linked.pdf", ["Linked source"])
        with patch.object(zotero, "item", return_value={"title": "Linked"}), patch.object(zotero, "pdf_path", return_value=str(source)):
            project_sources.import_zotero_pdf(self.db, self.a, "ABCD1234")
        with patch.object(fileguard, "_READ_ONLY_SOURCES", set()):
            with workflow.WorkflowDB(self.db.path):
                with self.assertRaisesRegex(ValueError, "原附件"):
                    fileguard.before_write(source)
                alias = self.db.base_dir(self.a) / "linked-alias.pdf"
                os.link(source, alias)
                with self.assertRaisesRegex(ValueError, "硬链接"):
                    asset_service.atomic_write_text(alias, "changed")

    def test_zotero_copy_rejects_a_symlink_outside_project(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.db.base_dir(self.a) / "文献库/Zotero副本"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlink privilege unavailable")
        source = write_text_pdf(self.root / "source.pdf", ["Original"])
        with patch.object(zotero, "item", return_value={"title": "Paper"}), patch.object(zotero, "pdf_path", return_value=str(source)):
            with self.assertRaisesRegex(ValueError, "越过"):
                project_sources.import_zotero_pdf(self.db, self.a, "ABCD1234")
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
