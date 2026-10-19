"""Deterministic checks for the canonical asset and resumable run layer."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parents[1]
for name in ("database", "workspace-core", "workspace-service"):
    sys.path.insert(0, str(ROOT / "packages" / name))

import asset_service
import v130_routes as feature_routes
import run_service
import workflow


class AssetGraphTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rw-assets-")
        self.db = workflow.WorkflowDB(Path(self.tmp.name) / "research.sqlite3")
        self.project = self.db.create_task("资产项目", "问题", "关键词")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_note_is_atomic_file_and_asset(self):
        note = self.db.add_note(self.project, "精读", "# 结论\n\n原文 p.1")
        path = Path(note["canonical_path"])
        self.assertEqual(path.read_text(encoding="utf-8"), "# 结论\n\n原文 p.1")
        assets = asset_service.list_assets(self.db, self.project, "note")
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["title"], "精读")
        self.assertTrue(assets[0]["current_revision_id"])

        self.db.save_md_note("", "编辑器笔记", "第一版", self.project)
        saved = self.db.md_notes()[0]
        self.db.save_md_note(saved["id"], "编辑器笔记", "第二版", None)
        asset = asset_service.list_assets(self.db, self.project, "note")[0]
        revisions = self.db.db.execute(
            "SELECT COUNT(*) AS n FROM asset_revisions WHERE asset_id=?", (asset["id"],)
        ).fetchone()["n"]
        self.assertEqual(revisions, 2)

    def test_fts_search_and_relations(self):
        a = asset_service.register(self.db, project_id=self.project, kind="evidence",
                                   title="性能证据", canonical_uri="db://evidence/1",
                                   storage="sqlite", body="容量提升到 180 mAh g-1")
        b = asset_service.register(self.db, project_id=self.project, kind="document",
                                   title="正文", canonical_uri="db://document/1",
                                   storage="sqlite", body="结果与讨论")
        asset_service.link(self.db, b["id"], "supports", a["id"])
        hits = asset_service.search(self.db, "容量提升", project_id=self.project)
        self.assertEqual([item["id"] for item in hits], [a["id"]])
        self.assertEqual(asset_service.relations(self.db, b["id"])[0]["to_asset_id"], a["id"])

    def test_note_paper_binding_creates_asset_edge(self):
        paper = asset_service.register(
            self.db, project_id=self.project, kind="paper", title="论文",
            canonical_uri="db://paper/p1", storage="sqlite", body="source",
            metadata={"paper_id": "p1"})
        self.db.add_note(self.project, "精读笔记", "结论", "p1")
        note_asset = asset_service.list_assets(self.db, self.project, "note")[0]
        relations = asset_service.relations(self.db, note_asset["id"])
        self.assertEqual(relations[0]["to_asset_id"], paper["id"])
        self.assertEqual(relations[0]["relation"], "derived_from")

        editable = self.db.save_md_note("", "编辑笔记", "正文", self.project, "p1")
        editable_asset = next(item for item in asset_service.list_assets(self.db, self.project, "note")
                              if item["canonical_uri"].endswith(editable["id"] + ".md"))
        self.assertEqual(asset_service.relations(self.db, editable_asset["id"])[0]["to_asset_id"], paper["id"])
        self.db.save_md_note(editable["id"], "编辑笔记", "解绑", None, "")
        self.assertEqual(asset_service.relations(self.db, editable_asset["id"]), [])

        moved_project = self.db.create_task(
            "第二项目", "question", "material", str(Path(self.tmp.name) / "second")
        )
        moved = self.db.save_md_note("", "迁移笔记", "迁移前", self.project)
        old_path = Path(moved["canonical_path"])
        moved = self.db.save_md_note(moved["id"], "迁移笔记", "迁移后", moved_project)
        new_path = Path(moved["canonical_path"])
        self.assertNotEqual(old_path, new_path)
        self.assertFalse(old_path.exists())
        self.assertTrue(new_path.exists())
        self.assertEqual(self.db.db.execute(
            "SELECT COUNT(*) AS n FROM assets WHERE json_extract(metadata, '$.legacy_id')=?",
            (moved["id"],)).fetchone()["n"], 1)

        deleted_path = Path(self.db.save_md_note("", "待删", "删除我", self.project)["canonical_path"])
        deleted_id = deleted_path.stem
        self.db.delete_md_note(deleted_id)
        self.assertFalse(deleted_path.exists())
        deleted_asset = next(item for item in asset_service.list_assets(self.db, self.project, "note")
                             if item["canonical_uri"] == str(deleted_path.resolve()))
        self.assertEqual(deleted_asset["status"], "archived")
        self.assertEqual(asset_service.search(self.db, "删除我", project_id=self.project), [])

    def test_workflow_run_idempotency_and_node(self):
        first = run_service.start(self.db, self.project, "paper_read", idempotency_key="paper-1")
        again = run_service.start(self.db, self.project, "paper_read", idempotency_key="paper-1")
        self.assertEqual(first["id"], again["id"])
        node = run_service.node_start(self.db, first["id"], "extract", inputs=["paper-1"])
        done = run_service.finish_node(self.db, node["id"], "succeeded", outputs=["note-1"])
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(run_service.get(self.db, first["id"])["status"], "succeeded")

        failed = run_service.start(self.db, self.project, "paper_read", idempotency_key="paper-failed")
        run_service.finish(self.db, failed["id"], "failed", error="provider timeout")
        same_failed = run_service.start(self.db, self.project, "paper_read", idempotency_key="paper-failed")
        self.assertEqual(same_failed["id"], failed["id"])
        self.assertEqual(same_failed["status"], "failed")

    def test_recover_stale_run(self):
        self.db.db.execute(
            "INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,?,?,?)",
            ("wrun-stale", self.project, "paper_read", "running", "", "", "2000-01-01T00:00:00+00:00", "", ""),
        )
        self.db.db.execute(
            "INSERT INTO workflow_nodes (id,run_id,stage,status,started_at) VALUES(?,?,?,?,?)",
            ("wnode-stale", "wrun-stale", "extract", "running", "2000-01-01T00:00:00+00:00"),
        )
        self.db.db.commit()
        self.assertEqual(run_service.recover_stale(self.db, 1), 1)
        self.assertEqual(run_service.get(self.db, "wrun-stale")["status"], "queued")
        self.assertEqual(self.db.db.execute("SELECT status FROM workflow_nodes WHERE id='wnode-stale'").fetchone()[0], "queued")

    def test_asset_routes(self):
        asset_service.register(self.db, project_id=self.project, kind="note",
                               title="路由笔记", canonical_uri="db://note/route", storage="sqlite", body="可检索内容")
        out = feature_routes.route("GET", "/api/assets/search", {},
                                   {"project_id": [self.project], "q": ["可检索"]}, self.db)
        self.assertEqual(len(out["assets"]), 1)

    def test_rebuild_legacy_rows_is_idempotent(self):
        self.db.db.execute(
            "INSERT INTO md_notes (id,title,content,task_id,paper_id,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("legacy-note", "旧笔记", "容量提升 20%", self.project, "", "2026-01-01", "2026-01-01"),
        )
        self.db.db.commit()
        first = asset_service.rebuild_project_index(self.db, self.project, include_files=False)
        second = asset_service.rebuild_project_index(self.db, self.project, include_files=False)
        self.assertEqual(first["indexed"]["notes"], 1)
        self.assertEqual(second["indexed"]["notes"], 1)
        assets = asset_service.list_assets(self.db, self.project, "note")
        self.assertEqual(len(assets), 1)
        self.assertEqual(asset_service.search(self.db, "容量提升", project_id=self.project)[0]["title"], "旧笔记")
        route = feature_routes.route("POST", "/api/assets/rebuild",
                                     {"project_id": self.project, "include_files": False}, {}, self.db)
        self.assertEqual(route["project_id"], self.project)

    def test_delete_project_removes_project_runs(self):
        run = run_service.start(self.db, self.project, "cleanup-check")
        self.assertIsNotNone(run_service.get(self.db, run["id"]))
        self.db.delete_task(self.project)
        self.assertIsNone(run_service.get(self.db, run["id"]))

    def test_workflow_run_detail_route_includes_nodes(self):
        run = run_service.start(self.db, self.project, "detail-check")
        node = run_service.node_start(self.db, run["id"], "outline")
        out = feature_routes.route("GET", "/api/workflow/run", {}, {"id": [run["id"]]}, self.db)
        self.assertEqual(out["run"]["nodes"][0]["id"], node["id"])


if __name__ == "__main__":
    unittest.main()
