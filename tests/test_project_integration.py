"""Project notebook, expert schedules, streaming and Feishu inbound behavior."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for package in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(ROOT / "packages" / package))

import feishu
import agent_protocols
import paths
import project_notes
import runtime
import schedule
import service_settings
import workflow


class ProjectIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "research.sqlite3"
        with workflow.WorkflowDB(self.db_path) as db:
            self.project_id = db.create_task("验证项目")
            self.root = db.base_dir(self.project_id)

    def test_existing_markdown_is_browsable_and_paths_stay_inside_project(self):
        path = self.root / "笔记" / "文献矩阵" / "已有.md"
        path.write_text("# 已有\n\n证据", encoding="utf-8")
        custom = self.root / "笔记" / "旧分类"
        custom.mkdir()
        (custom / "历史.md").write_text("# 历史", encoding="utf-8")
        with workflow.WorkflowDB(self.db_path) as db:
            board = project_notes.dashboard(db, self.project_id)
            group = next(g for g in board["groups"] if g["name"] == "文献矩阵")
            self.assertEqual(group["count"], 1)
            self.assertEqual(next(g for g in board["groups"] if g["name"] == "旧分类")["count"], 1)
            self.assertEqual(project_notes.read(db, self.project_id, group["files"][0]["path"])["content"], "# 已有\n\n证据")
            project_notes.write(db, self.project_id, "笔记/旧分类/历史.md", "# 更新")
            self.assertEqual((custom / "历史.md").read_text(encoding="utf-8"), "# 更新")
            with self.assertRaises(ValueError):
                project_notes.write(db, self.project_id, "笔记/../越界.md", "bad")
            with self.assertRaises(ValueError):
                project_notes.read(db, self.project_id, "../research.sqlite3")

    def test_project_init_preserves_existing_readmes_and_rejects_managed_roots(self):
        root_readme = self.root / "README.md"
        note_readme = self.root / "笔记" / "README.md"
        root_readme.write_text("我的项目说明", encoding="utf-8")
        note_readme.write_text("我的笔记说明", encoding="utf-8")
        workflow.make_project_tree(self.root)
        self.assertEqual(root_readme.read_text(encoding="utf-8"), "我的项目说明")
        self.assertEqual(note_readme.read_text(encoding="utf-8"), "我的笔记说明")
        with self.assertRaises(ValueError):
            paths.validate_project_dir(Path.home())
        with self.assertRaises(ValueError):
            paths.validate_project_dir(Path(self.root.anchor))
        with self.assertRaises(ValueError):
            paths.validate_project_dir(Path(self.tmp.name) / "Zotero" / "storage")

    def test_empty_notebook_exposes_a_usable_category_path(self):
        with workflow.WorkflowDB(self.db_path) as db:
            board = project_notes.dashboard(db, self.project_id)
            category = next(g for g in board["groups"] if g["name"] == "研究计划")
            self.assertEqual(category["count"], 0)
            relative = category["path"] + "/first.md"
            project_notes.write(db, self.project_id, relative, "created from empty dashboard")
            self.assertEqual(project_notes.read(db, self.project_id, relative)["content"],
                             "created from empty dashboard")
            with self.assertRaisesRegex(ValueError, "笔记/"):
                project_notes.write(db, self.project_id, "研究计划/second.md", "invalid prefix")

    def test_tool_limit_reply_preserves_actual_successes_and_failures(self):
        relative = "笔记/研究计划/last-step.md"
        calls = [{"id": str(i), "type": "function", "function": {
            "name": "project_note_write", "arguments": json.dumps({"path": path, "content": "test"})}}
            for i, path in enumerate(("研究计划/rejected.md", relative))]
        with workflow.WorkflowDB(self.db_path) as db, patch.object(workflow, "chat_raw", return_value={
                "content": "", "tool_calls": calls}):
            result = workflow.run_agent(db, self.project_id, "write test", max_steps=1,
                                        profile={"permission": "workspace_write"})
            self.assertIn(relative, result["reply"])
            self.assertIn("失败", result["reply"])
            self.assertIn("成功", result["reply"])
            self.assertIn("尚未完成最终核验", result["reply"])
            self.assertEqual(db.messages(self.project_id)[-1]["content"], result["reply"])
        self.assertTrue((self.root / relative).is_file())
        self.assertFalse((self.root / "研究计划/rejected.md").exists())

    def test_feishu_cannot_bind_a_legacy_zotero_project_for_writes(self):
        with workflow.WorkflowDB(self.db_path) as db:
            db.db.execute("UPDATE research_tasks SET base_dir=? WHERE id=?",
                          (str(Path(self.tmp.name) / "Zotero/library"), self.project_id))
            db.db.commit()
            with patch.object(feishu, "start_bridge") as bridge:
                with self.assertRaisesRegex(ValueError, "Zotero"):
                    feishu.save(db, {"assistant_id": "builtin-steward", "project_id": self.project_id,
                        "app_id": "cli_test", "app_secret": "test", "allowed_sender": "ou_test",
                        "enabled": True, "inbound_enabled": True})
                bridge.assert_not_called()
            self.assertFalse(feishu.config(db, "builtin-steward").get("inbound_enabled"))

    def test_schedule_runs_with_selected_expert_and_project_output(self):
        previous_db_path = schedule.DB_PATH
        self.addCleanup(setattr, schedule, "DB_PATH", previous_db_path)
        schedule.DB_PATH = self.db_path
        with workflow.WorkflowDB(self.db_path) as db:
            row = db.schedule_save({"name": "日报", "prompt": "汇总", "project_id": self.project_id,
                                    "assistant_id": "builtin-steward", "cron": "0 9 * * *",
                                    "output_dir": "笔记/每日推送", "enabled": True})
        with patch.object(workflow, "run_agent", return_value={"reply": "今天的摘要"}) as agent:
            result = schedule.run_once(row, trigger="manual")
        self.assertEqual(result["status"], "completed")
        self.assertTrue(Path(result["output_path"]).is_relative_to(self.root))
        self.assertIn("今天的摘要", Path(result["output_path"]).read_text(encoding="utf-8"))
        self.assertEqual(agent.call_args.kwargs["assistant_id"], "builtin-steward")

    def test_feishu_message_becomes_project_chat_and_duplicate_is_ignored(self):
        with workflow.WorkflowDB(self.db_path) as db:
            service_settings.write(db, "feishu", {"enabled": True, "inbound_enabled": True,
                "app_id": "cli_test", "app_secret": "secret", "project_id": self.project_id,
                "allowed_sender": "ou_allowed"}, "builtin-steward")
        event = {"event": {"sender": {"sender_id": {"open_id": "ou_allowed"}},
                 "message": {"message_id": "om_1", "chat_id": "oc_1", "message_type": "text",
                             "content": json.dumps({"text": "整理今天的文献"})}}}
        with patch.object(workflow, "run_agent", return_value={"reply": "完成"}) as agent, patch.object(feishu, "send") as sender, patch.object(feishu, "start_typing", return_value=""):
            result = feishu.handle_inbound(self.db_path, "builtin-steward", event)
            again = feishu.handle_inbound(self.db_path, "builtin-steward", event)
        self.assertTrue(result["accepted"])
        self.assertEqual(again["reason"], "duplicate")
        self.assertEqual(agent.call_count, 1)
        self.assertEqual(sender.call_args.kwargs["receive_id_type"], "chat_id")
        with workflow.WorkflowDB(self.db_path) as db:
            self.assertEqual(db.chat(result["local_chat_id"])["task_id"], self.project_id)
        event["event"]["message"]["message_id"] = "om_2"
        event["event"]["sender"]["sender_id"]["open_id"] = "ou_other"
        self.assertEqual(feishu.handle_inbound(self.db_path, "builtin-steward", event)["reason"], "sender_not_allowed")


class StreamingTest(unittest.TestCase):
    def test_model_supplied_confirm_cannot_authorize_external_write(self):
        with self.assertRaises(PermissionError):
            workflow.enforce_tool_policy("mail_send", {"confirm": True}, "workspace_write")
        workflow.enforce_tool_policy("mail_send", {}, "full_access")

    def test_content_and_tool_calls_arrive_incrementally(self):
        chunks = [
            {"choices": [{"delta": {"content": "研"}}]},
            {"choices": [{"delta": {"content": "究", "tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "project_note_read", "arguments": '{"path":'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"笔记/a.md"}'}}]}}], "usage": {"total_tokens": 8}},
        ]
        body = b"".join(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode() for chunk in chunks) + b"data: [DONE]\n\n"
        pieces = []
        profile = {"base_url": "https://example.test/v1", "model": "test", "api_key": "key"}
        with patch.object(runtime, "open_guarded_url", return_value=io.BytesIO(body)):
            result = runtime.call(profile, [{"role": "user", "content": "hi"}], on_delta=pieces.append)
        self.assertEqual(pieces, ["研", "究"])
        self.assertEqual(result["content"], "研究")
        self.assertEqual(result["tool_calls"][0]["function"]["arguments"], '{"path":"笔记/a.md"}')

    def test_native_protocols_stream_text_and_preserve_tool_calls(self):
        fixtures = {
            "openai-responses": [
                {"type": "response.output_text.delta", "delta": "研"},
                {"type": "response.output_text.delta", "delta": "究"},
                {"type": "response.completed", "response": {"output": [
                    {"type": "message", "content": [{"type": "output_text", "text": "研究"}]},
                    {"type": "function_call", "call_id": "call_1", "name": "project_note_read", "arguments": "{}"}],
                    "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}}}],
            "anthropic-messages": [
                {"type": "message_start", "message": {"usage": {"input_tokens": 2}}},
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "研"}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "究"}},
                {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "call_1", "name": "project_note_read", "input": {}}},
                {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{}"}},
                {"type": "message_delta", "usage": {"output_tokens": 3}}],
            "google-generative-ai": [
                {"candidates": [{"content": {"parts": [{"text": "研"}]}}]},
                {"candidates": [{"content": {"parts": [{"text": "究"}, {"functionCall": {"name": "project_note_read", "args": {}}}]}}],
                 "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 3, "totalTokenCount": 5}}],
        }
        for api, events in fixtures.items():
            with self.subTest(api=api):
                pieces = []
                profile = {"api": api, "base_url": "https://example.test/v1", "model": "test", "api_key": "key"}
                with patch.object(agent_protocols, "_stream_events", return_value=iter(events)) as stream:
                    result = agent_protocols.complete(profile, [{"role": "user", "content": "hi"}], on_delta=pieces.append)
                self.assertEqual(pieces, ["研", "究"])
                self.assertEqual(result["content"], "研究")
                self.assertEqual(result["tool_calls"][0]["function"]["name"], "project_note_read")
                self.assertEqual(result["usage"]["total"], 5)
                self.assertTrue(stream.called)


if __name__ == "__main__":
    unittest.main()
