"""Model-visible workflow pages retain their cursor and fixed source version."""
import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import workflow_context
import capabilities


class WorkflowContextCompactionTest(unittest.TestCase):
    def page_messages(self, page, arguments=None):
        return [
            {"role": "system", "content": "Keep the approved source version."},
            {"role": "user", "content": "Finish using verified evidence."},
            {"role": "assistant", "tool_calls": [{"id": "page", "function": {
                "name": "workflow_context_read", "arguments": json.dumps(arguments or {})}}]},
            {"role": "tool", "name": "workflow_context_read", "tool_call_id": "page",
             "content": json.dumps(page)},
        ]

    def visible_page(self, messages):
        return json.loads(next(m["content"] for m in messages if m.get("tool_call_id") == "page"))

    def test_skill_catalog_filters_disabled_skills_and_keeps_all_read_locators(self):
        skills = [{"id": f"skill-{index}", "description": "Long discovery description. " * 300}
                  for index in range(30)]
        enabled = {item["id"] for index, item in enumerate(skills) if index % 5}
        with patch.object(capabilities, "list_skills", return_value=skills), \
                patch.object(capabilities, "enabled", side_effect=lambda db, assistant, kind, item, *, item_scope="": item in enabled):
            catalog = workflow_context.skill_catalog(None, "assistant", "project")
        payload = catalog[len(workflow_context.SKILL_CATALOG_PREFIX):]
        original, end = json.JSONDecoder().raw_decode(payload)
        instruction = payload[end:]
        messages = [{"role": "system", "content": "Keep the permission policy."},
                    {"role": "system", "content": catalog},
                    {"role": "user", "content": "Discover the relevant skill."}]
        bounded = workflow_context.bounded_messages(messages, budget=7000)
        supplied = next(message["content"] for message in bounded
                        if message.get("content", "").startswith(workflow_context.SKILL_CATALOG_PREFIX))
        visible, end = json.JSONDecoder().raw_decode(supplied[len(workflow_context.SKILL_CATALOG_PREFIX):])
        self.assertEqual({item["skill_id"] for item in visible["enabled_skills"]}, enabled)
        self.assertEqual(len(visible["enabled_skills"]), 24)
        for item in visible["enabled_skills"]:
            self.assertNotIn("read_with", item)
        self.assertEqual(visible["read_tool"], "skill_read")
        self.assertEqual(visible["read_argument"], "skill_id")
        self.assertTrue(all(len(item["description"]) < len(original["enabled_skills"][0]["description"])
                            for item in visible["enabled_skills"]))
        self.assertEqual(supplied[len(workflow_context.SKILL_CATALOG_PREFIX):][end:], instruction)
        self.assertEqual(bounded[0], messages[0])
        self.assertLessEqual(len(json.dumps(bounded, ensure_ascii=False)), 7000)

    def test_source_view_removes_only_redundant_versioned_metadata(self):
        contract = {"required_files": [{"relative": "review.md", "min_chars": 2500}],
                    "acceptance": ["Cite each accepted evidence ID."]}
        output = {"name": "review.md", "relative": "notes/review.md", "path": "C:/project/notes/review.md",
                  "asset_id": "asset-1", "content_hash": "a" * 64, "sha256": "a" * 64,
                  "read_with": {"section": "artifact", "artifact": "review.md", "content_hash": "a" * 64}}
        snapshot = {"goal": "Finish the review.", "task_goal": "Finish the review.",
                    "output_contract": contract, "task_id": "task-current",
                    "task_inputs": {"goal": "Finish the review.", "handoffs": [{"source": "B"}],
                                    "dependencies": [{"task_id": "task-B", "delivery_id": "delivery-B",
                                                      "outputs": [deepcopy(output)]}]},
                    "handoffs": [{"source": "B"}],
                    "history": [{"task_id": "task-B", "delivery_id": "delivery-B",
                                 "outputs": [deepcopy(output)]}],
                    "accepted_upstream": [{"task_id": "task-B", "delivery_id": "delivery-B",
                                           "outputs": [deepcopy(output)]}]}
        snapshot["upstream_manifest"] = workflow_context._upstream_manifest(snapshot)
        messages = [{"role": "system", "content": "CURRENT_NODE_INPUT " +
                    json.dumps(snapshot, ensure_ascii=False)}]
        original = deepcopy(messages)
        view = [workflow_context._source_system(messages[0], 100000, "run", "D")]
        content = view[0]["content"]
        visible, _ = json.JSONDecoder().raw_decode(content[len("CURRENT_NODE_INPUT "):])
        self.assertEqual(messages, original)
        self.assertEqual(visible["task_goal"], "Finish the review.")
        self.assertEqual(visible["output_contract"], contract)
        self.assertEqual(visible["accepted_upstream"][0]["outputs"][0]["content_hash"], "a" * 64)
        self.assertEqual(visible["accepted_upstream"][0]["outputs"][0]["asset_id"], "asset-1")
        self.assertEqual(visible["accepted_upstream"][0]["outputs"][0]["read_with"]["artifact"], "review.md")
        dependency = visible["task_inputs"]["dependencies"][0]
        self.assertEqual((dependency["task_id"], dependency["delivery_id"]), ("task-B", "delivery-B"))
        self.assertNotIn("outputs", dependency)
        self.assertEqual(dependency["outputs_ref"], "history: same task_id and delivery_id")
        self.assertNotIn("goal", visible["task_inputs"])
        self.assertNotIn("handoffs", visible["task_inputs"])
        self.assertNotIn("upstream_manifest", visible)
        self.assertEqual(visible["upstream_manifest_ref"], "accepted_upstream.outputs")
        asset = visible["accepted_upstream"][0]["outputs"][0]
        self.assertNotIn("path", asset)
        self.assertNotIn("sha256", asset)
        self.assertNotIn("name", asset)
        self.assertEqual(asset["relative"], "notes/review.md")
        self.assertEqual(asset["content_hash"], "a" * 64)
        minimal = workflow_context._source_system(messages[0], 1, "run", "D")["content"]
        minimal_view, _ = json.JSONDecoder().raw_decode(minimal[len("CURRENT_NODE_INPUT "):])
        self.assertEqual(minimal_view["task_inputs"]["dependencies"][0]["outputs_ref"],
                         "history: same task_id and delivery_id")
        self.assertEqual(minimal_view["upstream_manifest_ref"], "accepted_upstream.outputs")

    def test_source_metadata_with_different_versions_paths_or_feedback_is_retained(self):
        old = {"path": "C:/project/notes/source.md", "relative": "notes/source.md",
               "asset_id": "asset", "content_hash": "old"}
        new = {**old, "content_hash": "new", "sha256": "different"}
        value = {"task_goal": "Current goal", "handoffs": [{"source": "B", "feedback": "Keep feedback"}],
                 "task_inputs": {"goal": "Different goal", "handoffs": [{"source": "C"}],
                                 "dependencies": [{"task_id": "B", "delivery_id": "delivery-new", "outputs": [new]}]},
                 "history": [{"task_id": "B", "delivery_id": "delivery-old", "outputs": [old]}],
                 "input_assets": [{"relative": "notes/source.md", "path": "C:/other/different.md",
                                   "content_hash": "version", "sha256": "other-version"}]}
        message = {"role": "system", "content": "CURRENT_NODE_INPUT " + json.dumps(value)}
        view = workflow_context._source_system(message, 100000, "run", "D")
        visible = json.loads(view["content"].removeprefix("CURRENT_NODE_INPUT "))
        self.assertEqual(visible["task_inputs"]["goal"], "Different goal")
        self.assertEqual(visible["task_inputs"]["handoffs"], [{"source": "C"}])
        self.assertEqual(visible["task_inputs"]["dependencies"][0]["outputs"][0]["content_hash"], "new")
        self.assertEqual(visible["task_inputs"]["dependencies"][0]["outputs"][0]["sha256"], "different")
        self.assertEqual(visible["handoffs"][0]["feedback"], "Keep feedback")
        self.assertEqual(visible["input_assets"][0]["path"], "C:/other/different.md")
        self.assertEqual(visible["input_assets"][0]["sha256"], "other-version")

    def test_same_delivery_and_hash_does_not_send_duplicate_upstream_bodies(self):
        body = "Complete approved source text. " * 1000
        sources = {"task_id": "task-B", "delivery_id": "delivery-B", "outputs": [
            {"name": "evidence.md", "relative": "notes/evidence.md", "content": body,
             "total_chars": len(body), "content_hash": "a" * 64,
             "read_with": {"tool": "workflow_context_read", "node_id": "B", "section": "artifact",
                           "artifact": "notes/evidence.md"}},
            {"name": "result.md", "relative": ".tasks/B/result.md", "content": body,
             "total_chars": len(body), "content_hash": "a" * 64,
             "read_with": {"tool": "workflow_context_read", "node_id": "B", "section": "artifact",
                           "artifact": ".tasks/B/result.md"}},
        ]}
        original = deepcopy(sources)
        view = workflow_context._node_source_view({"accepted_upstream": [sources]})
        outputs = view["accepted_upstream"][0]["outputs"]
        self.assertEqual(outputs[0]["content"], body)
        self.assertNotIn("content", outputs[1])
        self.assertEqual(outputs[1]["content_ref"], "notes/evidence.md")
        self.assertEqual(outputs[1]["content_hash"], "a" * 64)
        self.assertEqual(outputs[1]["read_with"]["artifact"], ".tasks/B/result.md")
        self.assertEqual(sources, original)

    def test_large_ordinary_tool_result_is_valid_json_with_source_identity(self):
        result = {"source_id": "source", "rows": [{"id": "row", "content_hash": "f" * 64,
                  "description": "long source description " * 5000}], "ok": True}
        serialized = workflow_context.serialize_tool_result(result, budget=40000)
        self.assertLessEqual(len(serialized), 40000)
        visible = json.loads(serialized)
        self.assertEqual(visible["source_id"], "source")
        self.assertEqual(visible["rows"][0]["id"], "row")
        self.assertEqual(visible["rows"][0]["content_hash"], "f" * 64)
        self.assertTrue(result["rows"][0]["description"].startswith(visible["rows"][0]["description"]))

    def test_serialized_page_keeps_hash_and_next_offset_after_body_reduction(self):
        page = {"content": "large source " * 4000, "total": 80000, "next_offset": 68000,
                "content_hash": "g" * 64, "delivery_id": "approved"}
        serialized = workflow_context.serialize_tool_result(page, {"offset": 16000}, budget=4000)
        visible = json.loads(serialized)
        self.assertLessEqual(len(serialized), 4000)
        self.assertEqual(visible["content_hash"], "g" * 64)
        self.assertEqual(visible["delivery_id"], "approved")
        self.assertEqual(visible["offset"], 16000)
        self.assertEqual(visible["end_offset"], 16000 + len(visible["content"]))
        self.assertEqual(visible["next_offset"], visible["end_offset"])
        self.assertTrue(visible["truncated"])

    def test_tool_metadata_over_budget_is_an_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "metadata"):
            workflow_context.serialize_tool_result({"content_hash": "h" * 10000}, budget=4000)

    def test_long_page_keeps_version_and_resumes_after_visible_content(self):
        page = {
            "run_id": "run", "node_id": "source", "section": "artifact",
            "content": "evidence " * 2000,
            "offset": 16000, "end_offset": 34000, "total": 50000,
            "next_offset": 34000, "artifact": "notes/evidence.md",
            "content_hash": "a" * 64, "delivery_id": "approved-source",
        }
        messages = [
            {"role": "system", "content": "Keep the approved source version."},
            {"role": "user", "content": "Finish using verified evidence."},
            {"role": "assistant", "tool_calls": [{"id": "page", "function": {
                "name": "workflow_context_read", "arguments": json.dumps({
                    "section": "artifact", "offset": 16000, "limit": 16000})}}]},
            {"role": "tool", "name": "workflow_context_read", "tool_call_id": "page",
             "content": json.dumps(page)},
        ]
        bounded = workflow_context.bounded_messages(messages, budget=7000)
        result = json.loads(next(m["content"] for m in bounded if m.get("tool_call_id") == "page"))
        self.assertEqual(result["content_hash"], "a" * 64)
        self.assertEqual(result["delivery_id"], "approved-source")
        self.assertEqual(result["total"], 50000)
        self.assertEqual(result["offset"], 16000)
        self.assertTrue(page["content"].startswith(result["content"]))
        self.assertGreater(len(result["content"]), 0)
        self.assertEqual(result["end_offset"], 16000 + len(result["content"]))
        self.assertEqual(result["next_offset"], result["end_offset"])
        self.assertLessEqual(len(json.dumps(bounded, ensure_ascii=False)), 7000)
        self.assertEqual(messages[-1]["content"], json.dumps(page))

    def test_last_page_becomes_a_continuation_when_only_its_prefix_fits(self):
        page = {"content": "remaining source " * 1000, "offset": 12000,
                "total": 29000, "next_offset": None, "content_hash": "fixed-version"}
        bounded = workflow_context.bounded_messages(self.page_messages(page), budget=4500)
        visible = self.visible_page(bounded)
        self.assertTrue(visible["truncated"])
        self.assertEqual(visible["next_offset"], 12000 + len(visible["content"]))
        self.assertLess(visible["next_offset"], 29000)

    def test_legacy_page_uses_original_call_offset(self):
        page = {"content": "source " * 3000, "total": 60000, "next_offset": 37000,
                "content_hash": "fixed-version"}
        bounded = workflow_context.bounded_messages(
            self.page_messages(page, {"offset": 16000, "limit": 30000}), budget=4000)
        visible = self.visible_page(bounded)
        self.assertEqual(visible["offset"], 16000)
        self.assertEqual(visible["next_offset"], 16000 + len(visible["content"]))

    def test_working_file_list_retains_every_version_and_locator(self):
        files = [{"path": f"notes/source-{i}.md", "relative": f"notes/source-{i}.md",
                  "asset_id": f"asset-{i}", "content_hash": str(i) * 64,
                  "exists": True, "content": "working text " * 1000} for i in range(20)]
        bounded = workflow_context.bounded_messages(self.page_messages(files), budget=7000)
        visible = self.visible_page(bounded)
        self.assertEqual(len(visible), 20)
        for original, result in zip(files, visible):
            for key in ("path", "relative", "asset_id", "content_hash", "exists"):
                self.assertEqual(result[key], original[key])
            self.assertTrue(original["content"].startswith(result["content"]))
        self.assertLessEqual(len(json.dumps(bounded, ensure_ascii=False)), 7000)

    def test_latest_multi_tool_exchange_keeps_original_arguments_and_pairs(self):
        messages = self.page_messages({"content": "body " * 2000, "total": 10000,
                                       "offset": 0, "next_offset": None})
        large_arguments = json.dumps({"markdown": "saved text " * 90})
        messages[-2]["tool_calls"].append({"id": "write", "function": {
            "name": "project_artifact_write", "arguments": large_arguments}})
        messages.append({"role": "tool", "tool_call_id": "write",
                         "name": "project_artifact_write", "content": json.dumps({
                             "ok": True, "asset_id": "saved", "content_hash": "b" * 64})})
        bounded = workflow_context.bounded_messages(messages, budget=4000)
        assistant = next(m for m in bounded if m.get("tool_calls"))
        self.assertEqual(assistant["tool_calls"][1]["function"]["arguments"], large_arguments)
        self.assertEqual([c["id"] for c in assistant["tool_calls"]], ["page", "write"])
        self.assertEqual([m["tool_call_id"] for m in bounded if m["role"] == "tool"], ["page", "write"])
        self.assertEqual(bounded[0], messages[0])
        self.assertIn(messages[1], bounded)

    def test_complete_request_measure_controls_the_budget(self):
        page = {"content": "evidence " * 3000, "total": 27000, "offset": 0,
                "next_offset": None, "content_hash": "c" * 64}
        calls = []
        def measure(messages):
            calls.append([m["role"] for m in messages])
            return (len(json.dumps(messages, ensure_ascii=False)) + 1) // 2
        bounded = workflow_context.bounded_messages(self.page_messages(page), budget=3000, measure=measure)
        self.assertLessEqual(measure(bounded), 3000)
        self.assertGreater(len(json.dumps(bounded, ensure_ascii=False)), 3000)
        self.assertTrue(all("system" in roles and "user" in roles and "tool" in roles for roles in calls))

    def test_successive_compacted_pages_cover_unicode_source_without_gaps(self):
        source = "\u6765\u6e90\u8bc1\u636e " * 2000
        offset, received = 0, []
        measure = lambda messages: len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
        for _ in range(40):
            content = source[offset:offset + 16000]
            page = {"content": content, "offset": offset, "end_offset": offset + len(content),
                    "total": len(source), "next_offset": offset + len(content)
                    if offset + len(content) < len(source) else None, "content_hash": "fixed"}
            visible = self.visible_page(workflow_context.bounded_messages(
                self.page_messages(page, {"offset": offset}), budget=3000, measure=measure))
            self.assertEqual(visible["offset"], offset)
            received.append(visible["content"])
            if visible["next_offset"] is None:
                break
            self.assertGreater(visible["next_offset"], offset)
            offset = visible["next_offset"]
        self.assertEqual("".join(received), source)

    def test_large_history_does_not_shorten_a_recent_page_that_fits(self):
        messages = self.page_messages({"content": "last source " * 600,
                                       "offset": 100, "total": 100000,
                                       "next_offset": 7300, "content_hash": "fixed"})
        messages[2:2] = [{"role": "assistant", "content": "old history " * 20000}]
        bounded = workflow_context.bounded_messages(messages, budget=12000)
        visible = self.visible_page(bounded)
        self.assertEqual(visible["content"], "last source " * 600)
        self.assertFalse(visible["truncated"])
        self.assertEqual(visible["next_offset"], 7300)

    def test_immutable_system_and_arguments_over_budget_raise(self):
        messages = self.page_messages({"ok": True})
        messages[0]["content"] = "Keep this permission rule. " * 500
        with self.assertRaisesRegex(ValueError, "system instructions"):
            workflow_context.bounded_messages(messages, budget=2000)
        messages[0]["content"] = "Keep the permission rule."
        messages[-2]["tool_calls"][0]["function"]["arguments"] = json.dumps({"body": "x" * 10000})
        with self.assertRaisesRegex(ValueError, "tool arguments"):
            workflow_context.bounded_messages(messages, budget=2000)

    def test_prompt_keeps_contract_and_more_than_sixteen_source_versions(self):
        contract = {"required_files": ["report.md"], "file_rules": {"report.md": {
            "min_chars": 2500}}, "acceptance": ["Verify the source."]}
        sources = [{"node_id": "upstream", "delivery_id": "approved", "outputs": [{
            "relative": f"notes/source-{i}.md", "asset_id": f"asset-{i}",
            "content_hash": "d" * 64, "total_chars": 20000, "content": "source " * 3000,
            "read_with": {"section": "artifact", "artifact": f"notes/source-{i}.md"}}]} for i in range(20)]
        snapshot = {"goal": "Use the approved evidence.", "output_contract": contract,
                    "accepted_upstream": sources, "history": [{"reply": "old " * 100000}]}
        original = deepcopy(snapshot)
        visible = json.loads(workflow_context.prompt(snapshot, "run", "node", budget=24000))
        self.assertEqual(visible["goal"], "Use the approved evidence.")
        self.assertEqual(visible["output_contract"], contract)
        self.assertEqual(len(visible["upstream_manifest"]), 20)
        self.assertEqual(len(visible["accepted_upstream"]), 20)
        self.assertEqual(visible["upstream_manifest"][-1]["content_hash"], "d" * 64)
        self.assertEqual(visible["upstream_manifest"][-1]["asset_id"], "asset-19")
        self.assertEqual(snapshot, original)
        self.assertLessEqual(len(json.dumps(visible, ensure_ascii=False)), 24000)

    def test_prompt_does_not_silently_truncate_a_required_contract(self):
        snapshot = {"goal": "Keep this goal.", "output_contract": {"required_files": [
            "report.md"], "acceptance": ["Required condition. " * 5000]}}
        with self.assertRaisesRegex(ValueError, "output contracts"):
            workflow_context.prompt(snapshot, "run", "node", budget=2000)

    def test_many_short_source_descriptions_shrink_without_repeating_long_notices(self):
        contract = {"required_files": ["report.md"], "acceptance": ["Use the approved source versions."]}
        source = {"goal": "Use these sources.", "output_contract": contract, "input_assets": [
            {"asset_id": f"asset-{index}", "description": "A short source description."}
            for index in range(40)]}
        messages = [{"role": "system", "content": "CURRENT_NODE_INPUT " + json.dumps(source)},
                    {"role": "user", "content": "Finish the report."}]
        original = deepcopy(messages)
        bounded = workflow_context.bounded_messages(messages, budget=3400, run_id="run", node_id="node")
        visible = json.loads(bounded[0]["content"].removeprefix("CURRENT_NODE_INPUT "))
        self.assertLessEqual(len(json.dumps(bounded, ensure_ascii=False)), 3400)
        self.assertEqual(visible["output_contract"], contract)
        self.assertEqual([item["asset_id"] for item in visible["input_assets"]],
                         [f"asset-{index}" for index in range(40)])
        self.assertEqual(visible["full_context"]["tool"], "workflow_context_read")
        self.assertEqual(messages, original)

    def test_read_reports_the_actual_range_of_a_short_final_page(self):
        no_row = SimpleNamespace(fetchone=lambda: None)
        db = SimpleNamespace(db=SimpleNamespace(execute=lambda *args: no_row),
                             workflow_context_run_id="run", workflow_context_node_id="node")
        run = {"project_id": "project", "settings": {}, "layout": {"nodes": []},
               "nodes": [{"id": "node", "checkpoint": {"input": {"body": "x" * 12000}}}]}
        engine = SimpleNamespace(detail=lambda *args: run)
        with patch.dict("sys.modules", {"workflow_execution": engine}):
            page = workflow_context.read(db, "project", {"run_id": "run", "node_id": "node",
                                                       "section": "input", "offset": 11000, "limit": 16000})
            self.assertEqual(page["offset"], 11000)
            self.assertEqual(page["end_offset"], page["total"])
            self.assertEqual(page["end_offset"] - page["offset"], len(page["content"]))
            self.assertIsNone(page["next_offset"])
            empty = workflow_context.read(db, "project", {"run_id": "run", "node_id": "node",
                                                        "section": "input", "offset": 90000})
            self.assertEqual(empty["offset"], empty["total"])
            self.assertEqual(empty["end_offset"], empty["total"])
            self.assertEqual(empty["content"], "")

    def test_read_keeps_working_file_manifest_outside_the_body_page(self):
        files = [{"path": "notes/source.md", "asset_id": "asset", "exists": True,
                  "content_hash": "e" * 64, "content": "large working document " * 1000}]
        no_row = SimpleNamespace(fetchone=lambda: None)
        db = SimpleNamespace(db=SimpleNamespace(execute=lambda *args: no_row),
                             workflow_context_run_id="run", workflow_context_node_id="node")
        run = {"project_id": "project", "settings": {}, "layout": {"nodes": []},
               "nodes": [{"id": "node", "checkpoint": {}}]}
        engine = SimpleNamespace(detail=lambda *args: run)
        artifacts = SimpleNamespace(current=lambda *args: deepcopy(files))
        with patch.dict("sys.modules", {"workflow_execution": engine, "project_artifacts": artifacts}):
            page = workflow_context.read(db, "project", {"run_id": "run", "node_id": "node",
                                                       "section": "artifacts", "limit": 8000})
        bounded = workflow_context.bounded_messages(self.page_messages(page), budget=4000)
        visible = self.visible_page(bounded)
        self.assertEqual(visible["files"], [{"path": "notes/source.md", "relative": "notes/source.md",
                                           "asset_id": "asset", "content_hash": "e" * 64, "exists": True}])


if __name__ == "__main__":
    unittest.main()
