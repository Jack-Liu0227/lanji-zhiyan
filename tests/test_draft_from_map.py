"""Regression checks for source-bound manuscript generation."""
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
for _n in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(Path(_ROOT) / "packages" / _n))

import workflow  # noqa: E402
import workflow_runners  # noqa: E402


class DraftFromMapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = workflow.WorkflowDB(Path(self.tmp.name) / "t.sqlite3")
        base = Path(self.tmp.name) / "proj"
        (base / "论文正文").mkdir(parents=True)
        (base / "笔记").mkdir()
        (base / "论文正文" / "框架.md").write_text(
            "# 题目\n\n## 1 引言\n\n### 1.1 背景\n- 说明：x\n\n### 1.2 现状\n- 说明：y\n\n"
            "## 2 方法\n\n### 2.1 结构\n- 说明：z\n", encoding="utf-8")
        (base / "笔记" / "精读-A.md").write_text("结论：Cu 掺杂带隙收窄\n- 界面钝化压低暗电流\n", encoding="utf-8")
        self.tid = self.db.create_task("起稿项目", "q", "Na", str(base))
        self.base = base
        # 谱系图：两章 + 三个小节，1.2 挂一篇精读笔记，2.1 什么都不挂
        workflow.map_write(self.db, self.tid, {
            "nodes": [{"id": "c1", "type": "section", "label": "1 引言", "detail": ""},
                      {"id": "s1", "type": "section", "label": "1.1 背景", "detail": ""},
                      {"id": "s2", "type": "section", "label": "1.2 现状", "detail": ""},
                      {"id": "c2", "type": "section", "label": "2 方法", "detail": ""},
                      {"id": "s3", "type": "section", "label": "2.1 结构", "detail": ""},
                      {"id": "r1", "type": "note", "label": "精读-A", "ref": "精读-A.md"}],
            "edges": [{"from": "c1", "to": "s1", "label": "包含"},
                      {"from": "c1", "to": "s2", "label": "包含"},
                      {"from": "c1", "to": "c2", "label": "下一章"},
                      {"from": "c2", "to": "s3", "label": "包含"},
                      {"from": "s2", "to": "r1", "label": "支撑（背景）"}]})
    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _md(self):
        return workflow.doc_read(self.db, self.tid, "write")["markdown"]

    def test_chapters_and_source_are_compiled_without_model(self):
        with patch.object(workflow, "chat_raw") as model:
            result = workflow_runners.run_write(self.db, self.tid, "按谱系图生成")
        model.assert_not_called()
        md = self._md()
        heads = [line for line in md.splitlines() if line.startswith("#")]
        self.assertEqual(heads, ["## 1 引言", "### 1.1 背景", "### 1.2 现状", "## 2 方法", "### 2.1 结构"])
        first, remaining = md.split("### 1.2 现状", 1)
        selected, last = remaining.split("## 2 方法", 1)
        self.assertNotIn("带隙收窄", first + last)
        self.assertIn("带隙收窄", selected)
        self.assertIn("支撑（背景）", selected)
        self.assertTrue(result["gaps"])

    def test_saving_graph_and_draft_without_mime_registry_access(self):
        with patch("asset_service.mimetypes.guess_type", side_effect=PermissionError):
            graph = workflow.map_read(self.db, self.tid)
            saved = workflow.map_write(self.db, self.tid, graph, expected_revision=graph["edit_revision"])
            result = workflow_runners.run_write(self.db, self.tid, "按谱系图生成")
        self.assertEqual(saved["revision"], result["revision"])
        for path in (saved["path"], result["path"]):
            asset = self.db.db.execute("SELECT content_hash FROM assets WHERE canonical_uri=?", (path,)).fetchone()
            self.assertTrue(asset and asset["content_hash"])

    def test_changed_order_and_removed_evidence_invalidate_previous_draft(self):
        from lineage_draft import validate_draft
        workflow_runners.run_write(self.db, self.tid, "按谱系图生成")
        previous = self._md()
        graph = workflow.map_read(self.db, self.tid)
        graph["edges"] = [edge for edge in graph["edges"] if edge["to"] != "r1"]
        graph["edges"].append({"from": "s2", "to": "s1", "label": "下一节"})
        workflow.map_write(self.db, self.tid, graph)
        with self.assertRaisesRegex(ValueError, "重新生成"):
            validate_draft(self.db, self.tid, previous, require_complete=False)
        workflow_runners.run_write(self.db, self.tid, "按谱系图重新生成")
        self.assertLess(self._md().index("1.2 现状"), self._md().index("1.1 背景"))
        self.assertNotIn("带隙收窄", self._md())

    def test_stale_edit_revision_and_parent_cycle_are_rejected(self):
        graph = workflow.map_read(self.db, self.tid)
        stale = graph["edit_revision"]
        graph["nodes"][0]["label"] = "1 引言（已编辑）"
        changed = workflow.map_write(self.db, self.tid, graph, expected_revision=stale)
        with self.assertRaisesRegex(ValueError, "其他操作"):
            workflow.map_write(self.db, self.tid, graph, expected_revision=stale)
        changed["edges"].append({"from": "s1", "to": "c1", "label": "包含"})
        with self.assertRaisesRegex(ValueError, "循环"):
            workflow.map_write(self.db, self.tid, changed, expected_revision=changed["edit_revision"])

    def test_automated_write_rejects_missing_graph_and_model_text(self):
        workflow.map_write(self.db, self.tid, {"nodes": [], "edges": []})
        with self.assertRaisesRegex(ValueError, "谱系图"):
            workflow._tool_impl("draft_write", self.db, self.tid, {})
        with self.assertRaisesRegex(ValueError, "谱系图"):
            workflow._tool_impl("doc_write", self.db, self.tid, {"markdown": "Invented"}, scope="write")
        self.assertFalse(self._md())

    def test_desktop_write_job_uses_the_same_source_gate(self):
        import agent_jobs
        with patch.object(workflow, "chat_raw") as model:
            run = agent_jobs.start(self.db.path, {"task_id": self.tid, "message": "按谱系图生成"}, "write")
            deadline = time.monotonic() + 5
            while run.worker_started and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertFalse(run.worker_started)
            self.assertEqual(run.status, "done")
            model.assert_not_called()
        self.assertIn("<!-- lineage:", self._md())

    def test_delivery_checks_source_and_document(self):
        from lineage_draft import validate_draft
        import workflow_state
        graph = workflow.map_read(self.db, self.tid)
        graph["nodes"] = [node for node in graph["nodes"] if node["id"] in {"s2", "r1"}]
        graph["edges"] = [{"from": "s2", "to": "r1", "label": "反证"}]
        workflow.map_write(self.db, self.tid, graph)
        result = workflow_runners.draft_from_lineage(self.db, self.tid, require_complete=True)
        self.assertTrue(workflow_state.capture(self.db, self.tid, "write")["valid"])
        self.assertIn("反证", result["markdown"])
        with self.assertRaisesRegex(ValueError, "重新生成"):
            validate_draft(self.db, self.tid, result["markdown"] + "\n额外模型结论")
        (self.base / "笔记" / "精读-A.md").write_text("修订过的来源。", encoding="utf-8")
        self.assertFalse(workflow_state.capture(self.db, self.tid, "write")["valid"])

    def test_中译英这一步的system钉死了正文(self):
        """这一步的专家必须读「论文撰写」的正文.md、写同目录的 正文_en.md，不许另建零碎文件。"""
        import workflow_documents as docs
        txt = workflow_runners.entrans_system(self.db, self.tid)
        self.assertIn(str(docs.doc_path(self.db, self.tid, "write")), txt, "源路径要写死在 system 里")
        self.assertIn("正文_en.md", txt, "产物路径要说清")
        self.assertIn("read_attachment", txt, "要告诉它怎么把正文读进来")
        self.assertIn("不许另建零碎文件", txt)


if __name__ == "__main__":
    unittest.main()
