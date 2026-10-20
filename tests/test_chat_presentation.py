"""Reasoning presentation and Feishu activity lifecycle; no external requests."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for package in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(ROOT / "packages" / package))
import agent_protocols
import feishu
import runtime
import service_settings
import workflow


class ReasoningPresentationTest(unittest.TestCase):
    def test_completions_keeps_reasoning_separate_from_answer(self):
        data = [{"choices": [{"delta": {"reasoning_content": "Plan summary"}}]},
                {"choices": [{"delta": {"content": "Answer"}}]}]
        body = b"".join(("data: " + json.dumps(d) + "\n\n").encode() for d in data) + b"data: [DONE]\n\n"
        thoughts, answers = [], []
        with patch.object(runtime, "open_guarded_url", return_value=io.BytesIO(body)):
            result = runtime.call({"base_url": "https://example.test", "model": "test"}, [],
                                  on_delta=answers.append, on_reasoning=thoughts.append)
        self.assertEqual(thoughts, ["Plan summary"])
        self.assertEqual(answers, ["Answer"])
        self.assertEqual(result["reasoning"], "Plan summary")
        self.assertEqual(result["content"], "Answer")

    def test_native_protocols_expose_public_summary_not_signatures(self):
        fixtures = {
            "openai-responses": [
                {"type": "response.reasoning_summary_text.delta", "delta": "Summary"},
                {"type": "response.output_text.delta", "delta": "Answer"},
                {"type": "response.completed", "response": {"output": [
                    {"type": "reasoning", "encrypted_content": "SECRET", "summary": [{"type": "summary_text", "text": "Summary"}]},
                    {"type": "message", "content": [{"type": "output_text", "text": "Answer"}]}]}}],
            "anthropic-messages": [
                {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Summary"}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "SECRET"}},
                {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Answer"}}],
            "google-generative-ai": [{"candidates": [{"content": {"parts": [
                {"text": "Summary", "thought": True, "thoughtSignature": "SECRET"}, {"text": "Answer"}]}}]}],
        }
        for api, events in fixtures.items():
            with self.subTest(api=api), patch.object(agent_protocols, "_stream_events", return_value=iter(events)) as stream:
                thoughts, answers = [], []
                result = agent_protocols.complete({"api": api, "base_url": "https://example.test", "model": "test"}, [],
                                                 on_delta=answers.append, on_reasoning=thoughts.append)
                self.assertEqual(thoughts, ["Summary"])
                self.assertEqual(answers, ["Answer"])
                self.assertEqual(result["reasoning"], "Summary")
                self.assertNotIn("SECRET", result["reasoning"])
                payload = stream.call_args.args[1]
                if api == "google-generative-ai":
                    self.assertTrue(payload["generationConfig"]["thinkingConfig"]["includeThoughts"])
                elif api == "openai-responses":
                    self.assertEqual(payload["reasoning"]["summary"], "auto")

    def test_agent_persists_summary_and_emits_incrementally(self):
        def model(*args, **kwargs):
            kwargs["on_reasoning"]("Summary")
            kwargs["on_delta"]("Answer")
            return {"content": "Answer", "reasoning": "Summary"}
        with tempfile.TemporaryDirectory() as folder, workflow.WorkflowDB(Path(folder) / "test.sqlite3") as db:
            pid = db.create_task("Test")
            emitted = []
            with patch.object(workflow, "chat_raw", side_effect=model):
                workflow.run_agent(db, pid, "test", emit=emitted.append, profile={})
            self.assertIn("reasoning_delta", [e["type"] for e in emitted])
            saved = next(e for e in db.messages(pid)[-1]["events"] if e["type"] == "reasoning_done")
            self.assertEqual(saved["content"], "Summary")
            self.assertGreaterEqual(saved["duration_ms"], 0)


class FeishuTypingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.sqlite3"
        with workflow.WorkflowDB(self.path) as db:
            pid = db.create_task("Test")
            service_settings.write(db, "feishu", {"enabled": True, "inbound_enabled": True,
                "app_id": "fake", "app_secret": "fake", "allowed_sender": "allowed", "project_id": pid}, "builtin-steward")
        self.event = {"event": {"sender": {"sender_id": {"open_id": "allowed"}}, "message": {
            "message_id": "msg", "chat_id": "chat", "message_type": "text", "content": '{"text":"task"}'}}}

    def test_typing_added_before_work_and_removed_after_success_or_failure(self):
        for failing in (False, True):
            with self.subTest(failing=failing):
                self.event["event"]["message"]["message_id"] = "failure" if failing else "success"
                order = []
                def request(path, payload=None, **kwargs):
                    if "tenant_access_token" in path:
                        return {"tenant_access_token": "fake-token"}
                    if kwargs.get("method") == "DELETE":
                        order.append("stop")
                        return {"code": 0}
                    self.assertEqual(payload, {"reaction_type": {"emoji_type": "Typing"}})
                    order.append("start")
                    return {"data": {"reaction_id": "reaction"}}
                def agent(*args, **kwargs):
                    order.append("work")
                    if failing:
                        raise RuntimeError("private failure details")
                    return {"reply": "done"}
                def send(*args, **kwargs):
                    order.append("reply")
                    self.assertNotIn("private failure details", args[2])
                    return {"message_id": "reply"}
                with patch.object(feishu, "request", side_effect=request), patch.object(workflow, "run_agent", side_effect=agent), patch.object(feishu, "send", side_effect=send):
                    if failing:
                        with self.assertRaises(RuntimeError):
                            feishu.handle_inbound(self.path, "builtin-steward", self.event)
                    else:
                        feishu.handle_inbound(self.path, "builtin-steward", self.event)
                    self.assertEqual(feishu.handle_inbound(self.path, "builtin-steward", self.event)["reason"], "duplicate")
                self.assertEqual(order, ["start", "work", "reply", "stop"])
                with workflow.WorkflowDB(self.path) as db:
                    row = db.db.execute("SELECT * FROM feishu_inbox WHERE message_id=?", (self.event["event"]["message"]["message_id"],)).fetchone()
                    self.assertEqual(row["typing_reaction_id"], "")
                    self.assertEqual(row["status"], "failed" if failing else "completed")

    def test_reaction_permission_failure_does_not_block_task(self):
        with patch.object(feishu, "request", side_effect=ValueError("reaction permission missing")), patch.object(workflow, "run_agent", return_value={"reply": "done"}) as agent, patch.object(feishu, "send", return_value={}):
            self.assertTrue(feishu.handle_inbound(self.path, "builtin-steward", self.event)["accepted"])
            agent.assert_called_once()
        with workflow.WorkflowDB(self.path) as db:
            row = db.db.execute("SELECT * FROM feishu_inbox WHERE message_id='msg'").fetchone()
            self.assertEqual(row["typing_error"], "reaction permission missing")


if __name__ == "__main__":
    unittest.main()
