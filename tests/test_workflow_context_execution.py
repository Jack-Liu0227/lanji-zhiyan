"""Exercise context visibility and cache boundaries through the real agent loop."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
for _package in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(_ROOT / "packages" / _package))

import project_artifacts
import capabilities
import project_bindings
import project_tasks
import workflow
import workflow_context
import workflow_execution
import workflow_layout
from workflow_compaction import WorkflowContext


def read_call(call_id, **args):
    return {"content": "", "tool_calls": [{"id": call_id, "type": "function", "function": {
        "name": "workflow_context_read", "arguments": json.dumps({
            "run_id": "flow", "node_id": "upstream", "section": "artifact",
            "artifact": "evidence.md", "offset": 0, "limit": 8000, **args})}}]}


def page(content="abcdefgh", total=16, **fields):
    return {"run_id": "flow", "node_id": "upstream", "section": "artifact", "artifact": "evidence.md",
            "offset": 0, "end_offset": len(content), "content": content, "total": total,
            "next_offset": len(content) if len(content) < total else None,
            "content_hash": "source-version", "delivery_id": "source-delivery", **fields}


def tool_result(messages, call_id):
    message = next(m for m in messages if m.get("role") == "tool" and m.get("tool_call_id") == call_id)
    return json.loads(message["content"])


class WorkflowContextExecutionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.db = workflow.WorkflowDB(Path(self.temp.name) / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.pid = self.db.create_task("Context integration", base_dir=str(self.root))
        self.profile = {"id": "fake-profile", "model": "fake-model", "base_url": "https://invalid.local/v1",
                        "api": "openai-completions", "context_window": 256000, "permission": "auto"}
        self.db.runtime_model_candidates = [self.profile]
        self.db.workflow_end_to_end = True
        self.db.workflow_context_run_id = "flow"
        self.db.workflow_context_node_id = "current"
        self.db.agent_resume = {"messages": [{"role": "system", "content": "Use visible evidence."},
                                            {"role": "user", "content": "Continue reading."}]}
        signature = sha256(json.dumps([[self.pid, "Read the next evidence page"], ""]).encode()).hexdigest()
        self.checkpoint = {**deepcopy(self.db.agent_resume), "signature": signature}
        self.db.job_checkpoint = self.checkpoint
        self.db.save_agent_checkpoint = lambda **values: self.checkpoint.update(deepcopy(values))
        self.specs = [{"type": "function", "function": {"name": "workflow_context_read", "description": "Read evidence",
                       "parameters": {"type": "object", "properties": {}}}}]
        self.views = []
        self.request_options = []

    def run_script(self, responses, implementation, **options):
        sequence = iter(responses)

        def model(profile, messages, **kwargs):
            self.views.append(deepcopy(messages))
            self.request_options.append(kwargs)
            response = next(sequence)
            if isinstance(response, Exception):
                raise response
            return deepcopy(response)

        with patch.object(workflow, "chat_raw", side_effect=model), \
                patch.object(workflow_context, "validate_cached_read", return_value=True), \
                patch.object(workflow, "_tool_impl", side_effect=implementation) as tool:
            result = workflow.run_agent(self.db, self.pid, "Read the next evidence page", max_steps=8,
                system="Use visible evidence.", tools=self.specs, profile=self.profile, publish=False,
                tool_allowlist=[spec["function"]["name"] for spec in self.specs], **options)
        return result, tool

    def test_nonempty_token_limited_reply_is_retried_before_completion(self):
        result, tool = self.run_script([
            {'content': 'Partial reply.', 'finish_reason': 'length', 'stream_complete': True},
            {'content': 'Complete reply.', 'finish_reason': 'stop', 'stream_complete': True},
        ], lambda *args, **kwargs: self.fail('No tools should execute.'))
        self.assertEqual(result['reply'], 'Complete reply.')
        self.assertEqual(len(self.views), 2)
        tool.assert_not_called()

    def test_repeated_nonempty_token_limited_reply_fails_after_one_retry(self):
        with self.assertRaisesRegex(RuntimeError, 'finish_reason=length'):
            self.run_script([
                {'content': 'Partial one.', 'finish_reason': 'length', 'stream_complete': True},
                {'content': 'Partial two.', 'finish_reason': 'length', 'stream_complete': True},
            ], lambda *args, **kwargs: self.fail('No tools should execute.'))
        self.assertEqual(len(self.views), 2)

    def test_a_truncated_summary_does_not_retire_raw_history(self):
        self.profile.update(context_window=32768, max_output=4096, tokenizer='utf8_bytes_conservative')
        history = [{'role': 'system', 'content': 'Keep source versions.'},
                   {'role': 'user', 'content': 'Read the next evidence page'}]
        for index in range(6):
            history.extend([
                {'role': 'assistant', 'content': '', 'tool_calls': [{'id': str(index), 'type': 'function',
                    'function': {'name': 'workflow_context_read', 'arguments': '{}'}}]},
                {'role': 'tool', 'tool_call_id': str(index), 'content': 'source ' * 1200}])
        self.checkpoint.update(messages=history)
        result, _ = self.run_script([
            {'content': 'Truncated summary.', 'finish_reason': 'length', 'stream_complete': True},
            {'content': 'Continue with bounded source.', 'finish_reason': 'stop'},
        ], lambda *args, **kwargs: self.fail('No tools should execute.'))
        self.assertEqual(result['reply'], 'Continue with bounded source.')
        state = self.checkpoint['context_state']
        self.assertEqual(state['summary_attempt']['status'], 'failed')
        self.assertEqual(self.request_options[0]['retry_policy'].max_retries, 0)
        self.assertNotIn('summary', state)
        self.assertTrue(any('source ' * 1200 in item.get('content', '') for item in self.checkpoint['messages']))

    def test_first_request_fits_three_hundred_long_skill_descriptions_and_archives_them(self):
        self.profile.pop("context_window")
        self.checkpoint = {}
        self.db.job_checkpoint = {}
        description = "A detailed research skill discovery description. " * 120
        skills = [{"id": f"research-skill-{index}", "name": f"research-skill-{index}",
                   "description": description, "desc": description, "scope": "project",
                   "path": str(self.root / ".agents" / "skills" / f"skill-{index}" / "SKILL.md")}
                  for index in range(300)]
        with patch.object(capabilities, "list_skills", return_value=skills), \
                patch.object(capabilities, "enabled", return_value=True):
            result, tool = self.run_script([{"content": "Skills discovered within the request budget."}],
                                          lambda *args, **kwargs: page())
        tool.assert_not_called()
        self.assertEqual(len(self.views), 1)
        supplied = next(message["content"] for message in self.views[0]
                        if message.get("content", "").startswith(workflow_context.SKILL_CATALOG_PREFIX))
        visible, end = json.JSONDecoder().raw_decode(supplied[len(workflow_context.SKILL_CATALOG_PREFIX):])
        self.assertEqual([item["skill_id"] for item in visible["enabled_skills"]], [item["id"] for item in skills])
        self.assertEqual(visible["read_tool"], "skill_read")
        self.assertEqual(visible["read_argument"], "skill_id")
        self.assertTrue(all("read_with" not in item for item in visible["enabled_skills"]))
        self.assertTrue(all(len(item["description"]) < len(description) for item in visible["enabled_skills"]))
        self.assertIn("Use skill_read with the exact skill_id", supplied[len(workflow_context.SKILL_CATALOG_PREFIX):][end:])
        raw_message = next(message["content"] for message in self.checkpoint["messages"]
                           if message.get("content", "").startswith(workflow_context.SKILL_CATALOG_PREFIX))
        raw, _ = json.JSONDecoder().raw_decode(raw_message[len(workflow_context.SKILL_CATALOG_PREFIX):])
        self.assertEqual(len(raw["enabled_skills"]), 300)
        self.assertTrue(all(item["description"] == description for item in raw["enabled_skills"]))
        self.assertTrue(all(item["read_with"] == {"tool": "skill_read", "skill_id": item["skill_id"]}
                            for item in raw["enabled_skills"]))
        budget = self.checkpoint["context_state"]["last_budget"]
        self.assertEqual(budget["window"], 65536)
        self.assertTrue(budget["estimated_window"])
        self.assertTrue(budget["fits"])
        self.assertEqual(result["reply"], "Skills discovered within the request budget.")

    def test_resume_refreshes_runtime_and_review_context_without_mutating_old_messages(self):
        old_messages = [
            {"role": "system", "content": "OLD_IDENTITY"},
            {"role": "system", "content": "OLD_RUNTIME_SOURCE\n" + "x" * 75000},
            {"role": "system", "content": "【当前节点输入与前置交付】\nOLD_REVIEW_REQUIREMENT"},
            {"role": "user", "content": "Continue reading."},
        ]
        original = deepcopy(old_messages)
        self.db.job_checkpoint = {"signature": self.checkpoint["signature"], "messages": old_messages}
        self.db.db.execute("UPDATE research_tasks SET question=? WHERE id=?", ("FRESH_RUNTIME_CONTEXT", self.pid))
        self.db.db.commit()
        self.run_script([{"content": "Use the latest review requirement."}], lambda *args, **kwargs: page(),
                        extra_context="FRESH_REVIEW_REQUIREMENT", refresh_system_prompt=True)
        supplied = json.dumps(self.views[0], ensure_ascii=False)
        self.assertIn("FRESH_RUNTIME_CONTEXT", supplied)
        self.assertIn("FRESH_REVIEW_REQUIREMENT", supplied)
        self.assertNotIn("OLD_IDENTITY", supplied)
        self.assertNotIn("OLD_RUNTIME_SOURCE", supplied)
        self.assertNotIn("OLD_REVIEW_REQUIREMENT", supplied)
        self.assertEqual(old_messages, original)
        self.assertEqual(self.checkpoint["initial_context_archive"], original[:3])

    def test_repeat_after_success_keeps_page_bounds_and_points_to_next_page(self):
        result, tool = self.run_script([read_call("first"), read_call("second"), read_call("third"),
                                       {"content": "Evidence read."}], lambda *args, **kwargs: page())
        self.assertEqual(tool.call_count, 1)
        for call_id, view in (("second", self.views[2]), ("third", self.views[3])):
            repeated = tool_result(view, call_id)
            self.assertTrue(repeated["repeat"])
            self.assertEqual(repeated["content"], "")
            self.assertEqual(repeated["offset"], 0)
            self.assertEqual(repeated["end_offset"], 8)
            self.assertEqual(repeated["total"], 16)
            self.assertEqual(repeated["content_hash"], "source-version")
            self.assertEqual(repeated["delivery_id"], "source-delivery")
            self.assertEqual(repeated["read_hint"]["offset"], 8)
        self.assertEqual(result["reply"], "Evidence read.")

    def test_failed_model_request_does_not_mark_its_input_page_visible(self):
        with self.assertRaisesRegex(RuntimeError, "model unavailable"):
            self.run_script([read_call("first"), RuntimeError("model unavailable")], lambda *args, **kwargs: page())
        self.assertEqual(self.checkpoint["context_state"]["progress"].get("reads", {}), {})
        self.db.agent_resume = deepcopy(self.checkpoint)
        self.db.job_checkpoint = deepcopy(self.checkpoint)
        self.views.clear()
        self.run_script([read_call("second"), {"content": "Read after recovery."}], lambda *args, **kwargs: page())
        entry = next(iter(self.checkpoint["context_state"]["progress"]["reads"].values()))
        self.assertEqual(entry["coverage"], [[0, 8]])
        self.assertTrue(tool_result(self.views[1], "second")["repeat"])

    def test_non_object_tool_arguments_return_feedback_without_stopping_the_loop(self):
        malformed = read_call("invalid")
        malformed["tool_calls"][0]["function"]["arguments"] = "[]"
        result, tool = self.run_script([malformed, {"content": "Correct the arguments."}],
                                      lambda *args, **kwargs: page())
        tool.assert_not_called()
        self.assertIn("JSON object", tool_result(self.views[1], "invalid")["error"])
        self.assertEqual(result["reply"], "Correct the arguments.")

    def test_invalid_write_path_returns_feedback_without_stopping_the_loop(self):
        self.specs.append({"type": "function", "function": {"name": "project_artifact_write",
            "description": "Save declared output", "parameters": {"type": "object", "properties": {}}}})
        invalid = read_call("invalid-path")
        invalid["tool_calls"][0]["function"] = {"name": "project_artifact_write", "arguments": json.dumps(
            {"path": "../outside.md", "content": "Do not save outside the project."})}
        result, tool = self.run_script([invalid, {"content": "Use a declared path."}],
                                      lambda *args, **kwargs: page())
        tool.assert_not_called()
        self.assertIn("error", tool_result(self.views[1], "invalid-path"))
        self.assertFalse((Path(self.temp.name) / "outside.md").exists())
        self.assertEqual(result["reply"], "Use a declared path.")

    def test_each_explicit_reread_executes_the_reader_again(self):
        _, tool = self.run_script([read_call("first"), read_call("check-1", reread=True),
                                  read_call("check-2", reread=True), {"content": "Checked again."}],
                                 lambda *args, **kwargs: page())
        self.assertEqual(tool.call_count, 3)
        for call_id, view in (("check-1", self.views[2]), ("check-2", self.views[3])):
            self.assertFalse(tool_result(view, call_id).get("repeat", False))
            self.assertEqual(tool_result(view, call_id)["content"], "abcdefgh")

    def test_current_artifact_reads_refresh_their_contents_and_hash(self):
        contents = iter(("first current version", "human updated version"))

        def read(*args, **kwargs):
            text = next(contents)
            return page(text, total=len(text), section="artifacts", artifact="", delivery_id="", content_hash=text)

        _, tool = self.run_script([read_call("first", section="artifacts"), read_call("second", section="artifacts"),
                                  {"content": "Current content inspected."}], read)
        self.assertEqual(tool.call_count, 2)
        self.assertEqual(tool_result(self.views[2], "second")["content"], "human updated version")
        self.assertEqual(tool_result(self.views[2], "second")["content_hash"], "human updated version")

    def test_partially_visible_page_is_not_mistaken_for_the_complete_cached_page(self):
        original_view = WorkflowContext._view

        def view(context, messages, summary):
            prepared = original_view(context, messages, summary)
            for message in prepared:
                if message.get("role") == "tool" and message.get("tool_call_id") == "first":
                    result = json.loads(message["content"])
                    result.update(content=result["content"][:6000], end_offset=6000, next_offset=6000, truncated=True)
                    message["content"] = json.dumps(result)
            return prepared

        raw = page("a" * 8000, total=16000)
        with patch.object(WorkflowContext, "_view", new=view):
            self.run_script([read_call("first"), read_call("second"), {"content": "Continue reading."}],
                            lambda *args, **kwargs: deepcopy(raw))
        self.assertFalse(tool_result(self.views[2], "second").get("repeat", False))
        self.assertEqual(tool_result(self.views[1], "first")["end_offset"], 6000)
        progress = next(m for m in self.views[2] if m.get("content", "").startswith("WORKFLOW PROGRESS\n"))
        state = json.loads(progress["content"].split("\n", 1)[1])
        self.assertEqual(state["reads"][0]["visible_ranges"], [[0, 6000]])
        self.assertEqual(state["reads"][0]["next_unread_offset"], 6000)

    def test_human_edit_invalidates_actual_saved_write_cache_and_version_binding(self):
        binding = project_bindings.register(self.db, self.pid, "assistant", "builtin-steward")
        authority = project_tasks.CommandContext.ui(self.pid)
        task = project_tasks.create(self.db, {"goal": "Save report", "executor_binding_id": binding["id"],
            "output_contract": {"required_files": ["outputs/report.md"]}}, authority=authority)
        run = project_tasks.start(self.db, task["id"], authority=authority)
        project_tasks.claim(self.db, run["id"])
        self.db.project_task_context = project_tasks.CommandContext.agent(self.pid, task["id"])
        args = {"path": "outputs/report.md", "content": "# Report\nSaved research evidence."}
        receipt = project_artifacts.write(self.db, self.pid, args)
        self.assertTrue(project_artifacts.cached_write_current(self.db, self.pid, args, receipt))
        self.assertEqual(project_artifacts.bind_write_version(self.db, self.pid, args, [receipt])["expected_hash"],
                         receipt["content_hash"])
        (self.root / "outputs/report.md").write_text("# Human revision\nPreserve this edit.", encoding="utf-8")
        self.assertFalse(project_artifacts.cached_write_current(self.db, self.pid, args, receipt))
        self.assertEqual(project_artifacts.bind_write_version(self.db, self.pid, args, [receipt]), args)
        self.assertEqual((self.root / "outputs/report.md").read_text(encoding="utf-8"),
                         "# Human revision\nPreserve this edit.")

    def test_accepted_file_cache_rechecks_delivery_scope_and_human_edit(self):
        workflow_layout.ensure_schema(self.db)
        project_bindings.register(self.db, self.pid, "assistant", "builtin-steward")
        layout = workflow_layout.save_layout(self.db, {"name": "Accepted file cache", "nodes": [
            {"id": "A", "step": "task", "assistant_id": "builtin-steward",
             "output_contract": {"required_files": ["source.md"]}},
            {"id": "B", "step": "task", "assistant_id": "builtin-steward",
             "output_contract": {"required_files": ["summary.md"]}},
        ], "edges": [{"source": "A", "target": "B"}]})["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.pid, "layout_id": layout["id"]})
        flow = workflow_execution.create(self.db, self.pid)

        def write_source(db, project_id, *args, **kwargs):
            saved = workflow._tool_impl("project_artifact_write", db, project_id,
                {"path": "source.md", "content": "# Source\nExact accepted evidence."})
            return {"reply": "Saved source.", "output_assets": saved["output_assets"]}

        with patch.object(workflow, "run_agent", side_effect=write_source), patch.object(workflow_execution, "kick"):
            flow = workflow_execution.advance(self.db, flow["id"])
            task = project_tasks.get(self.db, flow["settings"]["task_ids"]["A"])
            project_tasks.review(self.db, task["id"], approved=True,
                                 authority=project_tasks.CommandContext.ui(self.pid))
        self.db.workflow_context_run_id = flow["id"]
        self.db.workflow_context_node_id = "B"
        args = {"run_id": flow["id"], "node_id": "A", "section": "artifact", "artifact": "source.md", "limit": 8000}
        cached = workflow_context.read(self.db, self.pid, args)
        self.assertTrue(workflow_context.validate_cached_read(self.db, self.pid, args, cached))
        with self.assertRaises(ValueError):
            workflow_context.validate_cached_read(self.db, "foreign-project", args, cached)
        Path(task["outputs"][0]["path"]).write_text("Human changed accepted source", encoding="utf-8")
        with self.assertRaises(ValueError):
            workflow_context.validate_cached_read(self.db, self.pid, args, cached)


if __name__ == "__main__":
    unittest.main()
