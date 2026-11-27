"""Workflow compaction preserves constraints and observes only accepted views."""
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import model_context
import workflow_context
from workflow_compaction import WorkflowContext


def read_exchange(call_id, content="source text", offset=0, total=None):
    args = {"run_id": "run", "node_id": "A", "section": "artifact", "artifact": "source.md", "offset": offset, "limit": 16000}
    result = {**args, "content": content, "total": total or len(content), "next_offset": None,
              "delivery_id": "delivery", "content_hash": "version", "end_offset": offset + len(content)}
    return [
        {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "function": {"name": "workflow_context_read", "arguments": json.dumps(args)}}]},
        {"role": "tool", "tool_call_id": call_id, "name": "workflow_context_read", "content": json.dumps(result)},
    ]


class WorkflowCompactionTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("CREATE TABLE service_settings(service TEXT, owner TEXT, data TEXT)")
        self.addCleanup(self.connection.close)
        self.db = SimpleNamespace(db=self.connection)
        self.saved = []
        self.summarize = Mock(return_value="Completed earlier research; continue with the saved evidence and remaining draft.")
        self.profile = {"id": "model", "model": "test-model", "context_window": 32768,
                        "max_output": 8000, "tokenizer": "utf8_bytes_conservative"}
        self.counter = patch.object(model_context, "_offline_encoding", return_value=None)
        self.counter.start()
        self.addCleanup(self.counter.stop)

    def controller(self, state=None):
        return WorkflowContext(self.db, state=state, save_checkpoint=lambda **value: self.saved.append(deepcopy(value)),
                               summarize=self.summarize, run_id="run", node_id="D")

    def history(self, size=7000):
        return [{"role": "system", "content": "Use registered evidence and preserve versions."},
                {"role": "developer", "content": "Do not bypass output validation."},
                {"role": "user", "content": "Write the review using verified sources."},
                *read_exchange("old-1", "a" * size), *read_exchange("old-2", "b" * size),
                *read_exchange("old-3", "c" * size), *read_exchange("current", "d" * 1000)]

    def test_short_request_does_not_call_a_summary_model(self):
        context = self.controller()
        prepared = context.prepare(self.profile, self.history(10), [], 2048)
        self.summarize.assert_not_called()
        self.assertTrue(prepared.budget.fits)
        self.assertIn("context_budget", prepared.profile["_usage_context"])

    def test_pressure_creates_a_persisted_handoff_without_mutating_history(self):
        context = self.controller()
        messages = self.history(7800)
        original = deepcopy(messages)
        prepared = context.prepare(self.profile, messages, [], 2048)
        self.assertEqual(messages, original)
        self.summarize.assert_called_once()
        self.assertTrue(context.state["summary"]["complete_source"])
        self.assertEqual(context.state["summary"]["source_model"], "test-model")
        self.assertTrue(prepared.budget.fits)
        for message in original[:3]:
            self.assertIn(message, prepared.messages)
        calls = {c["id"] for m in prepared.messages for c in m.get("tool_calls", [])}
        results = {m["tool_call_id"] for m in prepared.messages if m.get("role") == "tool"}
        self.assertEqual(calls, results)
        self.assertIn("current", calls)
        self.assertEqual(self.summarize.call_args.args[0]["_usage_context"]["source"], "context_compaction")

    def test_existing_summary_is_reused_until_the_active_view_is_full_again(self):
        context = self.controller()
        messages = self.history(9000)
        context.prepare(self.profile, messages, [], 2048)
        resumed = self.controller(context.state)
        resumed.prepare(self.profile, [*messages, *read_exchange("next", "next page")], [], 2048)
        self.summarize.assert_called_once()

    def test_large_catalog_does_not_summarize_fitting_history_across_five_rounds(self):
        context = self.controller()
        profile = {"model": "catalog-model", "context_window": 65536,
                   "tokenizer": "utf8_bytes_conservative"}
        catalog = {"role": "system", "content": workflow_context.SKILL_CATALOG_PREFIX + json.dumps({
            "enabled_skills": [
                {"skill_id": "skill-" + str(index), "description": "description " * 500,
                 "read_with": {"tool": "skill_read", "skill_id": "skill-" + str(index)}}
                for index in range(30)
            ]
        }) + "\nRead the exact skill before applying it."}
        messages = [{"role": "system", "content": "Preserve every evidence version."}, catalog,
                    {"role": "user", "content": "Read the evidence."}]
        for index in range(8):
            messages.extend(read_exchange("old-" + str(index), "old evidence " * 250))
        original_catalog = deepcopy(catalog)

        for round_index in range(5):
            with self.subTest(round=round_index):
                messages.extend(read_exchange("new-" + str(round_index),
                                              ("round " + str(round_index) + " evidence ").ljust(1024, "x")))
                original = deepcopy(messages)
                prepared = context.prepare(profile, messages, [], None)
                self.summarize.assert_not_called()
                self.assertTrue(prepared.budget.fits)
                self.assertEqual(messages, original)
                history = [message for message in messages if message["role"] not in ("system", "developer")]
                visible_history = [message for message in prepared.messages
                                   if message["role"] not in ("system", "developer")]
                self.assertEqual(visible_history, history)
                self.assertLess(model_context.request_tokens(profile, history, []),
                                prepared.budget.trigger_tokens)
                visible_catalog = next(message for message in prepared.messages
                                       if str(message.get("content", "")).startswith(workflow_context.SKILL_CATALOG_PREFIX))
                self.assertLess(len(visible_catalog["content"]), len(original_catalog["content"]))
                self.assertTrue(visible_catalog["content"].endswith("Read the exact skill before applying it."))

        messages.extend(read_exchange("overflow", "overflow evidence " * 4000))
        latest_pair = read_exchange("latest", "Keep this complete final page. " * 40)
        messages.extend(latest_pair)
        original = deepcopy(messages)
        history = [message for message in messages if message["role"] not in ("system", "developer")]
        self.assertGreater(model_context.request_tokens(profile, history, []), profile["context_window"])
        prepared = context.prepare(profile, messages, [], None)
        self.summarize.assert_called_once()
        self.assertTrue(prepared.budget.fits)
        self.assertEqual(messages, original)
        for message in latest_pair:
            self.assertIn(message, prepared.messages)
        self.assertIn("WORKFLOW COMPACTION CHECKPOINT", " ".join(str(m.get("content")) for m in prepared.messages))
        next_pair = read_exchange("after-summary", "New evidence after the handoff.".ljust(1024, "y"))
        messages.extend(next_pair)
        resumed = self.controller(context.state)
        prepared = resumed.prepare(profile, messages, [], None)
        self.summarize.assert_called_once()
        self.assertTrue(prepared.budget.fits)
        for message in [*latest_pair, *next_pair]:
            self.assertIn(message, prepared.messages)
        self.assertEqual(catalog, original_catalog)

    def test_revised_system_constraints_invalidate_the_saved_summary(self):
        self.summarize.side_effect = ["OLD SUMMARY SENTINEL", "Revised constraints require page-level evidence."]
        context = self.controller()
        messages = self.history(7900)
        context.prepare(self.profile, messages, [], 2048)
        original_fingerprint = context.state["summary"]["source_fingerprint"]
        revised = deepcopy(messages)
        revised[0]["content"] += " Human feedback now requires page-level evidence."
        resumed = self.controller(context.state)
        prepared = resumed.prepare(self.profile, revised, [], 2048)
        self.assertEqual(self.summarize.call_count, 2)
        self.assertNotEqual(resumed.state["summary"]["source_fingerprint"], original_fingerprint)
        self.assertIn(revised[0], prepared.messages)
        self.assertNotIn("OLD SUMMARY SENTINEL", " ".join(str(m.get("content")) for m in prepared.messages))

    def test_developer_feedback_outside_the_old_summary_span_also_invalidates_it(self):
        self.summarize.side_effect = ["OLD SUMMARY SENTINEL", "The revised output contract requires a matrix."]
        context = self.controller()
        messages = self.history(7900)
        context.prepare(self.profile, messages, [], 2048)
        updated = [*messages, {"role": "developer", "content": "Revised output contract: save a comparison matrix."}]
        resumed = self.controller(context.state)
        prepared = resumed.prepare(self.profile, updated, [], 2048)
        self.assertEqual(self.summarize.call_count, 2)
        self.assertIn(updated[-1], prepared.messages)
        self.assertNotIn("OLD SUMMARY SENTINEL", " ".join(str(m.get("content")) for m in prepared.messages))

    def test_legacy_history_uses_a_bounded_summary_source_and_marks_omissions(self):
        context = self.controller()
        messages = self.history(500000)
        original = deepcopy(messages)
        prepared = context.prepare(self.profile, messages, [], 2048)
        self.assertEqual(messages, original)
        self.summarize.assert_called_once()
        summary_profile, source_view, output_limit = self.summarize.call_args.args
        source_budget = model_context.measure_request(summary_profile, source_view, [], output_limit)
        self.assertTrue(source_budget.fits)
        self.assertFalse(context.state["summary"]["complete_source"])
        self.assertTrue(context.state["summary"]["omitted"])
        self.assertIn("full_checkpoint", " ".join(str(m.get("content")) for m in prepared.messages))

    def test_failed_summary_is_not_retried_for_the_same_source_span(self):
        self.summarize.side_effect = RuntimeError("model unavailable")
        context = self.controller()
        messages = self.history(9000)
        original = deepcopy(messages)
        first = context.prepare(self.profile, messages, [], 2048)
        context.prepare(self.profile, messages, [], 2048)
        self.summarize.assert_called_once()
        self.assertEqual(messages, original)
        self.assertTrue(first.budget.fits)
        self.assertNotIn("summary", context.state)
        self.assertIn("Summary unavailable", context.state["fallback"])
        self.assertIn("WORKFLOW COMPACTION FALLBACK", " ".join(str(m.get("content")) for m in first.messages))

    def test_revised_feedback_allows_a_failed_source_span_to_be_summarized_again(self):
        self.summarize.side_effect = [RuntimeError("temporarily unavailable"), "Summary under the revised feedback."]
        context = self.controller()
        messages = self.history(7900)
        context.prepare(self.profile, messages, [], 2048)
        failed_fingerprint = context.state["summary_attempt"]["source_fingerprint"]
        revised = deepcopy(messages)
        revised[1]["content"] += " Human feedback: preserve the new output contract."
        resumed = self.controller(context.state)
        resumed.prepare(self.profile, revised, [], 2048)
        self.assertEqual(self.summarize.call_count, 2)
        self.assertNotEqual(resumed.state["summary_attempt"]["source_fingerprint"], failed_fingerprint)
        self.assertEqual(resumed.state["summary_attempt"]["status"], "succeeded")
        self.assertNotIn("fallback", resumed.state)

    def test_same_failed_span_is_retried_after_the_model_identity_changes(self):
        self.summarize.side_effect = [RuntimeError("old gateway failed"), "Recovered summary with a new route."]
        context = self.controller()
        messages = self.history(9000)
        context.prepare(self.profile, messages, [], 2048)
        changed = {**self.profile, "model": "new-gateway-model"}
        context.prepare(changed, messages, [], 2048)
        self.assertEqual(self.summarize.call_count, 2)
        self.assertEqual(context.state["summary_attempt"]["status"], "succeeded")

    def test_a_small_new_page_does_not_immediately_retry_a_failed_summary(self):
        self.summarize.side_effect = RuntimeError("model timed out")
        context = self.controller()
        messages = self.history(9000)
        context.prepare(self.profile, messages, [], 2048)
        resumed = self.controller(context.state)
        for index in range(3):
            messages.extend(read_exchange("next-" + str(index), "new page" * 60))
            prepared = resumed.prepare(self.profile, messages, [], 2048)
            self.assertTrue(prepared.budget.fits)
        self.summarize.assert_called_once()
        self.assertIn("model timed out", resumed.state["summary_attempt"]["detail"])

    def test_summary_source_excludes_the_preserved_execution_catalogs(self):
        context = self.controller()
        messages = self.history(9000)
        context.prepare(self.profile, messages, [], 2048)
        _, source, _ = self.summarize.call_args.args
        self.assertNotIn(messages[0], source)
        self.assertNotIn(messages[1], source)
        self.assertNotIn(messages[2], source)
        self.assertIn("concise workflow handoff summary", source[-1]["content"])

    def test_cancelled_summary_propagates_without_a_fallback(self):
        class Cancelled(Exception):
            pass

        self.summarize.side_effect = Cancelled()
        context = self.controller()
        with self.assertRaises(Cancelled):
            context.prepare(self.profile, self.history(9000), [], 2048)
        self.assertNotIn("fallback", context.state)

    def test_overlong_summary_is_rejected_instead_of_being_silently_truncated(self):
        self.summarize.return_value = "x" * 10000
        context = self.controller()
        prepared = context.prepare(self.profile, self.history(9000), [], 2048)
        self.assertTrue(prepared.budget.fits)
        self.assertNotIn("summary", context.state)
        self.assertEqual(context.state["summary_attempt"]["status"], "failed")

    def test_complete_unicode_summary_is_not_rejected_by_an_estimated_token_count(self):
        self.profile.pop('tokenizer')
        self.summarize.return_value = '\u5b8c\u6574\u6458\u8981\u4fdd\u7559\u8bc1\u636e\u4e0e\u8fdb\u5ea6\u3002' * 140
        context = self.controller()
        prepared = context.prepare(self.profile, self.history(28000), [], 2048)
        self.summarize.assert_called_once()
        candidate, _, summary_limit = self.summarize.call_args.args
        self.assertEqual(candidate['_usage_context']['context_budget']['count_method'], 'estimated_utf8_mixed')
        estimated = model_context.request_tokens(self.profile, [{'role': 'assistant', 'content': self.summarize.return_value}], [])
        empty = model_context.request_tokens(self.profile, [{'role': 'assistant', 'content': ''}], [])
        self.assertGreater(estimated - empty, summary_limit)
        self.assertEqual(context.state['summary_attempt']['status'], 'succeeded')
        self.assertEqual(context.state['summary']['content'], self.summarize.return_value)
        self.assertNotIn('fallback', context.state)
        self.assertTrue(prepared.budget.fits)

    def test_only_accepted_requests_mark_the_visible_text_as_read(self):
        context = self.controller()
        messages = [{"role": "user", "content": "Read"}, *read_exchange("page", "abcdefgh", total=8)]
        prepared = context.prepare(self.profile, messages, [], 2048)
        self.assertEqual(context.state["progress"], {})
        context.accepted(prepared)
        entry = next(iter(context.state["progress"]["reads"].values()))
        self.assertEqual(entry["coverage"], [[0, 8]])
        context.accepted(prepared)
        self.assertEqual(len(context.state["progress"]["observations"]), 1)

    def test_write_receipts_are_preserved_independently_of_summary_content(self):
        context = self.controller()
        context.result("project_artifact_write", {}, {"ok": True, "path": "draft.md", "content_hash": "current", "asset_id": "asset"})
        context.result("project_artifact_write", {}, {"ok": False, "error": "conflict", "path": "draft.md"})
        resumed = self.controller(context.state)
        prepared = resumed.prepare(self.profile, self.history(10), [], 2048)
        self.assertIn('"content_hash":"current"', " ".join(str(m.get("content")) for m in prepared.messages))
        self.assertEqual(len(resumed.state["progress"]["writes"]), 1)

    def test_accepted_evidence_survives_a_summary_that_omits_its_ids(self):
        context = self.controller()
        record = {"id": "registered-evidence", "paper_id": "verified-paper", "filename": "paper.pdf",
                  "page_no": 4, "field": "conductivity", "value": "2.1 mS/cm", "excerpt": "full source"}
        context.result("paper_evidence_record", {}, {"accepted": [record], "rejected": []})
        resumed = self.controller(context.state)
        prepared = resumed.prepare(self.profile, self.history(9000), [], 2048)
        progress = json.loads(next(m["content"].removeprefix("WORKFLOW PROGRESS\n")
                                   for m in prepared.messages if str(m.get("content", "")).startswith("WORKFLOW PROGRESS\n")))
        self.assertEqual(progress["accepted_evidence"], [{
            "id": "registered-evidence", "paper_id": "verified-paper", "filename": "paper.pdf",
            "page_no": 4, "field": "conductivity", "value": "2.1 mS/cm"}])
        self.assertIn("paper_evidence_get", progress["evidence_instruction"])
        self.assertNotIn("registered-evidence", self.summarize.return_value)

    def test_summary_instruction_keeps_next_actions_with_the_current_node(self):
        context = self.controller()
        messages = self.history(7800)
        context.prepare(self.profile, messages, [], 2048)
        instruction = self.summarize.call_args.args[1][-1]["content"]
        self.assertIn("only this node's current task", instruction)
        self.assertIn("latest concrete correction", instruction)

    def test_lookup_facts_survive_compaction_without_becoming_accepted_evidence(self):
        search_args = {"query": "electron blocking interface", "sources": ["crossref"]}
        search_result = {"query": search_args["query"], "found": 1, "matches": [
            {"id": "result-1", "task_id": "project", "source": "crossref", "title": "Electron blocking",
             "authors": "A", "year": 2024, "doi": "10.1000/one", "url": "https://doi.org/10.1000/one"}]}
        evidence_args = {"ids": ["ev-1"], "project": "project"}
        evidence_result = {"evidence": [{"id": "ev-1", "paper_id": "paper-1", "filename": "paper.pdf",
                                           "page_no": 2, "field": "observation", "value": "direct"}]}
        messages = [
            {"role": "system", "content": "Preserve lookup facts."},
            {"role": "user", "content": "Continue the current node."},
            {"role": "assistant", "tool_calls": [
                {"id": "search-call", "function": {"name": "paper_search", "arguments": json.dumps(search_args)}},
                {"id": "evidence-call", "function": {"name": "paper_evidence_get", "arguments": json.dumps(evidence_args)}},
            ]},
            {"role": "tool", "tool_call_id": "search-call", "name": "paper_search", "content": json.dumps(search_result)},
            {"role": "tool", "tool_call_id": "evidence-call", "name": "paper_evidence_get", "content": json.dumps(evidence_result)},
            *read_exchange("older-a", "a" * 7800), *read_exchange("older-b", "b" * 7800),
            *read_exchange("older-c", "c" * 7800), *read_exchange("current-page", "d" * 1000),
        ]
        original = deepcopy(messages)
        context = self.controller()
        context.prepare(self.profile, messages, [], 2048)
        self.summarize.assert_called_once()
        self.assertNotIn("ev-1", self.summarize.return_value)
        resumed = self.controller(context.state)
        prepared = resumed.prepare(self.profile, messages, [], 2048)
        progress = json.loads(next(m["content"].removeprefix("WORKFLOW PROGRESS\n")
                                   for m in prepared.messages if str(m.get("content", "")).startswith("WORKFLOW PROGRESS\n")))
        ref = progress["completed_lookups"][0]["record_refs"][0]
        self.assertEqual(progress["returned_bibliography"][ref]["doi"], "10.1000/one")
        self.assertEqual(progress["retrieved_evidence"][0]["id"], "ev-1")
        self.assertEqual(progress["accepted_evidence"], [])
        self.assertEqual(messages, original)
        self.assertTrue(prepared.budget.fits)

    def test_legacy_done_tool_lookup_is_restored_without_promoting_evidence(self):
        args = {"query": "legacy query", "sources": ["crossref"]}
        result = {"query": "legacy query", "found": 1, "matches": [
            {"id": "legacy-result", "task_id": "project", "source": "crossref",
             "title": "Legacy result", "year": 2020, "doi": "10.1000/legacy"}]}
        self.db.job_checkpoint = {"done_tools": {
            "paper_search\0" + json.dumps(args, sort_keys=True): result}}
        context = self.controller()
        prepared = context.prepare(self.profile, [{"role": "user", "content": "Continue."}], [], 2048)
        progress = json.loads(next(m["content"].removeprefix("WORKFLOW PROGRESS\n")
                                   for m in prepared.messages if str(m.get("content", "")).startswith("WORKFLOW PROGRESS\n")))
        ref = progress["completed_lookups"][0]["record_refs"][0]
        self.assertEqual(progress["returned_bibliography"][ref]["doi"], "10.1000/legacy")
        self.assertEqual(progress["accepted_evidence"], [])

    def test_summary_receives_the_current_E_contract_instead_of_upstream_D_outputs(self):
        contract = {
            "required_files": ["最终产物/literature_review.md", "最终产物/references.bib",
                               "最终产物/citation_check.md"],
            "file_rules": {"最终产物/literature_review.md": {"min_chars": 2500},
                           "最终产物/references.bib": {"min_entries": 8}},
            "acceptance": ["Keep unresolved citations explicit and await human final review."],
        }
        payload = {
            "task_id": "task-E", "current_node": "E", "task_goal": "Check citations and deliver the final review.",
            "output_contract": contract,
            "accepted_upstream": [{"node_id": "D", "output_contract": {"required_files": [
                "笔记/文献研究/comparison_matrix.md", "论文正文/review_draft.md"]}}],
        }
        for prefix, feedback in (("【当前节点输入与前置交付】\n", ""),
                                 ("CURRENT_NODE_INPUT ", ""),
                                 ("【当前节点输入与前置交付】\n", "【人工退回意见，须落实】\nFix the bibliography.\n\n")):
            with self.subTest(prefix=prefix, feedback=bool(feedback)):
                self.summarize.reset_mock()
                context = self.controller()
                messages = self.history(7800)
                messages.insert(2, {"role": "system", "content": prefix + feedback + json.dumps(payload, ensure_ascii=False) +
                                   "\n【指定技能 citation】\nKeep the source evidence unchanged."})
                original = deepcopy(messages)
                prepared = context.prepare(self.profile, messages, [], 2048)
                self.summarize.assert_called_once()
                source = self.summarize.call_args.args[1]
                marker = next(message["content"] for message in source
                              if str(message.get("content", "")).startswith("CURRENT_NODE_SUMMARY_TASK "))
                current = json.loads(marker.removeprefix("CURRENT_NODE_SUMMARY_TASK "))
                self.assertEqual(current["task_id"], "task-E")
                self.assertEqual(current["current_node"], "E")
                self.assertEqual(current["task_goal"], payload["task_goal"])
                self.assertEqual(current["output_contract"], contract)
                self.assertNotIn("comparison_matrix.md", marker)
                self.assertNotIn("review_draft.md", marker)
                self.assertEqual(messages, original)
                self.assertTrue(prepared.budget.fits)

    def test_legacy_evidence_receipts_are_restored_from_original_tool_history(self):
        context = self.controller()
        evidence = {"id": "evidence-old", "paper_id": "paper-old", "page_no": 2,
                    "field": "test conditions", "value": "200 MPa"}
        messages = [*self.history(9000),
                    {"role": "assistant", "tool_calls": [{"id": "record", "function": {
                        "name": "paper_evidence_record", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "record", "content": json.dumps({"accepted": [evidence]})}]
        context.prepare(self.profile, messages, [], 2048)
        self.assertEqual(context.state["progress"]["evidence"]["evidence-old"]["value"], "200 MPa")
        before = deepcopy(context.state)
        context.result("paper_evidence_record", {}, {"accepted": [evidence], "rejected": []})
        self.assertEqual(context.state, before)

    def test_actual_candidate_settings_are_read_once_without_schema_writes(self):
        self.connection.execute("INSERT INTO service_settings VALUES('model_context','fallback',?)", (json.dumps({"context_window": 32768, "max_output": 8000}),))
        statements = []
        self.connection.set_trace_callback(statements.append)
        context = self.controller()
        fallback = {"id": "fallback", "model": "fallback"}
        first = context.prepare(fallback, self.history(10), [], 2048)
        context.prepare(fallback, self.history(10), [], 4096)
        self.assertEqual(first.budget.window, 32768)
        self.assertIn("overrides.context_window", first.budget.source)
        self.assertEqual(len(statements), 1)
        self.assertTrue(statements[0].startswith("SELECT"))

    def test_legacy_write_receipts_are_restored_without_claiming_reads(self):
        context = self.controller()
        messages = [{"role": "user", "content": "Continue"},
                    {"role": "assistant", "tool_calls": [{"id": "saved", "function": {
                        "name": "project_artifact_write", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "saved", "name": "project_artifact_write", "content": json.dumps({
                        "ok": True, "path": "draft.md", "content_hash": "saved-version", "asset_id": "asset"})}]
        prepared = context.prepare(self.profile, messages, [], 2048)
        self.assertEqual(len(context.state["progress"]["writes"]), 1)
        self.assertEqual(context.state["progress"]["reads"], {})
        self.assertIn('"content_hash":"saved-version"', " ".join(str(m.get("content")) for m in prepared.messages))

    def test_history_cannot_move_a_persisted_write_receipt_back_to_an_old_version(self):
        context = self.controller()
        context.result("project_artifact_write", {}, {
            "ok": True, "path": "draft.md", "content_hash": "current-version", "asset_id": "current-asset"})
        old_receipt = {"ok": True, "path": "draft.md", "content_hash": "old-version", "asset_id": "old-asset"}
        messages = [{"role": "user", "content": "Continue"},
                    {"role": "assistant", "tool_calls": [{"id": "old", "function": {
                        "name": "project_artifact_write", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "old", "name": "project_artifact_write", "content": json.dumps(old_receipt)}]
        resumed = self.controller(context.state)
        prepared = resumed.prepare(self.profile, messages, [], 2048)
        self.assertEqual(resumed.state["progress"]["writes"], [{
            "path": "draft.md", "content_hash": "current-version", "asset_id": "current-asset"}])
        progress = next(m["content"] for m in prepared.messages if str(m.get("content", "")).startswith("WORKFLOW PROGRESS"))
        self.assertIn('"content_hash":"current-version"', progress)
        self.assertNotIn("old-version", progress)

    def test_saved_write_progress_directs_resume_to_current_node_submission(self):
        context = self.controller()
        context.result("project_artifact_write", {}, {
            "ok": True, "path": "draft.md", "content_hash": "current", "asset_id": "asset"})
        prepared = context.prepare(self.profile, self.history(10), [], 2048)
        progress = next(m["content"] for m in prepared.messages
                        if str(m.get("content", "")).startswith("WORKFLOW PROGRESS\n"))
        self.assertIn("current node only", progress)
        self.assertIn("correction already identified", progress)
        self.assertIn("call workflow_submit_outputs", progress)

    def test_output_retry_is_checked_against_the_candidate_output_cap(self):
        context = self.controller()
        context.prepare(self.profile, self.history(10), [], 4096)
        retry = context.prepare(self.profile, self.history(10), [], 16000)
        self.assertEqual(retry.budget.output_limit, 8000)
        self.assertTrue(retry.budget.fits)

    def test_required_instructions_are_preserved_when_the_soft_target_is_too_small(self):
        context = self.controller()
        profile = {"context_window": 32768, "max_output": 16000, "tokenizer": "utf8_bytes_conservative"}
        messages = [{"role": "system", "content": "s" * 10200}, {"role": "user", "content": "Current objective"},
                    *read_exchange("old", "a" * 2000), *read_exchange("current", "b" * 100)]
        prepared = context.prepare(profile, messages, [], 16000)
        self.assertIn(messages[0], prepared.messages)
        self.assertIn(messages[1], prepared.messages)
        self.assertGreater(prepared.budget.input_tokens, prepared.budget.target_tokens)
        self.assertTrue(prepared.budget.fits)

    def test_automatic_compaction_preserves_recent_body_above_the_soft_target(self):
        context = self.controller()
        profile = {"model": "large-fixed-fixture", "context_window": 65536,
                   "tokenizer": "utf8_bytes_conservative"}
        recent = read_exchange("draft", "Complete current draft body. " * 210)
        messages = [{"role": "system", "content": "s" * 34500},
                    {"role": "user", "content": "Revise the current draft and submit."},
                    *read_exchange("old", "old " * 10000), *recent]
        original = deepcopy(messages)
        prepared = context.prepare(profile, messages, [], None)
        self.assertTrue(prepared.budget.fits)
        self.assertGreater(prepared.budget.input_tokens, prepared.budget.target_tokens)
        for message in recent:
            self.assertIn(message, prepared.messages)
        self.assertEqual(messages, original)

    def test_unchanged_progress_and_budget_do_not_rewrite_the_checkpoint(self):
        context = self.controller()
        messages = [{"role": "user", "content": "Read"}, *read_exchange("page", "abcdefgh", total=8)]
        prepared = context.prepare(self.profile, messages, [], 2048)
        context.prepare(self.profile, messages, [], 2048)
        self.assertEqual(len(self.saved), 1)
        context.accepted(prepared)
        self.assertEqual(len(self.saved), 2)
        context.accepted(prepared)
        context.result("workflow_context_read", {}, {"content": "abcdefgh"})
        context.result("project_artifact_write", {}, {"ok": False, "error": "conflict"})
        self.assertEqual(len(self.saved), 2)
        receipt = {"ok": True, "path": "draft.md", "content_hash": "version", "asset_id": "asset"}
        context.result("project_artifact_write", {}, receipt)
        context.result("project_artifact_write", {}, receipt)
        self.assertEqual(len(self.saved), 3)


if __name__ == "__main__":
    unittest.main()
