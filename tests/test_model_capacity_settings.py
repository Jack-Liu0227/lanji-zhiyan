"""Model metadata and optional overrides drive the real request preparation."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import agent_settings
import model_catalog
import model_context
import runtime
import workflow
from workflow_compaction import WorkflowContext


class ModelCatalogTest(unittest.TestCase):
    def test_official_deepseek_route_has_a_sourced_capacity(self):
        profile = {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-v4-pro"}
        spec = model_context.resolve_spec(profile)
        self.assertEqual(spec.window, 1048576)
        self.assertFalse(spec.estimated_window)
        self.assertIn("catalog.context_window", spec.source)
        self.assertIn("deepseek-harness", spec.capacity_source)

    def test_codex_contract_does_not_leak_into_an_api_or_custom_gateway(self):
        profile = {"base_url": "account://openai-codex", "api": "codex-cli", "model": "gpt-6.1-sol"}
        self.assertEqual(model_context.resolve_spec(profile).window, 500000)
        self.assertEqual(model_context.resolve_spec(profile, {"context_window": 1000000}).window, 872000)
        api_spec = model_context.resolve_spec({**profile, "base_url": "https://api.openai.com/v1", "api": "openai-responses"})
        self.assertEqual(api_spec.window, 1050000)
        self.assertNotEqual(api_spec.capacity_source, model_catalog.CODEX_SOURCE)
        self.assertIsNone(model_catalog.lookup({**profile, "base_url": "https://gateway.example/v1", "api": "openai-responses"}))

    def test_unknown_alias_endpoint_and_price_range_do_not_claim_a_capacity(self):
        for fields in ({"model": "deepseek-v4-pro-future"}, {"base_url": "https://proxy.example/v1"},
                       {"base_url": "https://api.deepseek.com/unknown"}, {"base_url": "http://api.deepseek.com/v1"}):
            profile = {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-v4-pro", **fields}
            spec = model_context.resolve_spec({**profile, "max_input": 1000000})
            self.assertEqual(spec.window, 65536)
            self.assertTrue(spec.estimated_window)

    def test_public_api_input_limit_and_output_cap_are_separate_from_codex_defaults(self):
        profile = {"base_url": "https://api.openai.com/v1", "model": "gpt-6-sol", "api": "openai-responses"}
        budget = model_context.measure_request(profile, [], [], 8000)
        self.assertEqual(budget.window, 1050000)
        self.assertEqual(budget.available_input_tokens, 922000)
        self.assertEqual(budget.trigger_tokens, 922000)
        self.assertEqual(budget.max_output, 128000)
        self.assertIn("developers.openai.com", budget.capacity_source)


class ModelCapacitySettingsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.db = workflow.WorkflowDB(Path(self.temp.name) / "test.sqlite3")
        self.addCleanup(self.db.close)
        self.model = self.db.add_model("Gateway", "https://gateway.example/v1", "custom-model", "fixture")

    def route(self, method, body=None):
        return agent_settings.route(method, "/api/agent-settings/model-context", body or {},
                                    {"id": [self.model["id"]]}, self.db)["context"]

    def test_save_clear_and_state_use_the_same_effective_capacity(self):
        self.assertTrue(self.route("GET")["estimated_window"])
        saved = self.route("POST", {"id": self.model["id"], "context_window": 128000, "max_output": 4096})
        self.assertEqual((saved["context_window"], saved["output_limit"]), (128000, 4096))
        self.assertFalse(saved["estimated_window"])
        state = agent_settings.route("GET", "/api/agent-settings/state", {}, {}, self.db)
        self.assertEqual(next(m["context"] for m in state["models"] if m["id"] == self.model["id"]), saved)
        context = WorkflowContext(self.db, save_checkpoint=lambda **kw: None, summarize=Mock())
        prepared = context.prepare(self.db.model(self.model["id"]), [{"role": "user", "content": "Continue"}], [], 16000)
        self.assertEqual((prepared.budget.window, prepared.budget.output_limit), (128000, 4096))
        cleared = self.route("POST", {"id": self.model["id"], "context_window": None, "max_output": None})
        self.assertTrue(cleared["estimated_window"])
        self.assertEqual(cleared["overrides"], {})

    def test_endpoint_changes_invalidate_a_saved_override_even_inside_one_controller(self):
        self.route("POST", {"id": self.model["id"], "context_window": 200000})
        context = WorkflowContext(self.db, save_checkpoint=lambda **kw: None, summarize=Mock())
        messages = [{"role": "user", "content": "Continue"}]
        first = context.prepare(self.model, messages, [], 8000)
        changed = {**self.model, "base_url": "https://other.example/v1"}
        second = context.prepare(changed, messages, [], 8000)
        self.assertEqual(first.budget.window, 200000)
        self.assertEqual(second.budget.window, 65536)
        self.assertTrue(model_context.describe(self.db, changed)["stale"])

    def test_invalid_overrides_do_not_replace_the_saved_configuration(self):
        original = self.route("POST", {"id": self.model["id"], "context_window": 128000})
        for bad in ({"context_window": True}, {"context_window": -1}, {"context_window": 2.5},
                    {"context_window": 8192, "max_output": 8192}, {"thresholdRatio": .5}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.route("POST", {"id": self.model["id"], **bad})
            self.assertEqual(self.route("GET"), original)

    def test_an_unknown_model_id_cannot_write_configuration(self):
        with self.assertRaises((KeyError, ValueError)):
            self.route("POST", {"id": "missing", "context_window": 128000})
        self.assertEqual(self.db.db.execute("SELECT COUNT(*) FROM service_settings WHERE service='model_context'").fetchone()[0], 0)


class ContextOverflowRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.profile = {"model": "fixture", "context_window": 65536, "max_output": 4096,
                        "tokenizer": "utf8_bytes_conservative"}
        self.messages = [{"role": "system", "content": "Preserve permissions and current file versions."},
                         {"role": "user", "content": "Finish the same research task."},
                         {"role": "assistant", "content": "previous source text " * 1600}]
        self.saved = []
        self.context = WorkflowContext(None, save_checkpoint=lambda **kw: self.saved.append(deepcopy(kw)), summarize=Mock())

    def invoke(self, responses, messages=None, **kwargs):
        with patch.object(workflow, "chat_raw", side_effect=responses) as calls:
            result = workflow.call_with_fallback(None, [self.profile], messages or self.messages,
                max_tokens=16000, request_context=self.context, **kwargs)
        return result, calls

    def test_confirmed_overflow_retries_once_with_a_smaller_view_and_actual_output_cap(self):
        original = deepcopy(self.messages)
        result, calls = self.invoke([runtime.ContextOverflowError("too long"), {"content": "Recovered"}])
        self.assertEqual(result["content"], "Recovered")
        self.assertEqual(calls.call_count, 2)
        first, second = calls.call_args_list
        self.assertEqual(first.kwargs["max_tokens"], 4096)
        self.assertEqual(second.kwargs["max_tokens"], 4096)
        self.assertLess(len(json.dumps(second.args[1])), len(json.dumps(first.args[1])))
        self.assertIn(self.messages[0], second.args[1])
        self.assertIn(self.messages[1], second.args[1])
        self.assertEqual(self.messages, original)
        self.assertEqual(second.args[0]["_context_budget"]["overflow_recovery"], 1)

    def test_a_second_overflow_stops_without_looping(self):
        with patch.object(workflow, "chat_raw", side_effect=runtime.ContextOverflowError("too long")) as calls:
            with self.assertRaises(runtime.ContextOverflowError):
                workflow.call_with_fallback(None, [self.profile], self.messages, request_context=self.context)
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(self.context.state["progress"].get("reads", {}), {})

    def test_required_user_input_cannot_be_cut_to_authorize_a_retry(self):
        messages = [{"role": "user", "content": "Keep the exact required protocol. " * 1000}]
        with patch.object(workflow, "chat_raw", side_effect=runtime.ContextOverflowError("too long")) as calls:
            with self.assertRaises(runtime.ContextOverflowError):
                workflow.call_with_fallback(None, [self.profile], messages, request_context=self.context)
        self.assertEqual(calls.call_count, 1)

    def test_auth_errors_do_not_trigger_context_recovery(self):
        with patch.object(workflow, "chat_raw", side_effect=runtime.QuotaOrAuthError("invalid key")) as calls:
            with self.assertRaises(runtime.QuotaOrAuthError):
                workflow.call_with_fallback(None, [self.profile], self.messages, request_context=self.context)
        self.assertEqual(calls.call_count, 1)

    def test_cancelled_recovery_does_not_send_another_request(self):
        check = Mock(side_effect=[False, True])
        with patch.object(workflow, "chat_raw", side_effect=runtime.ContextOverflowError("too long")) as calls:
            with self.assertRaises(runtime.CancelledError):
                workflow.call_with_fallback(None, [self.profile], self.messages,
                    request_context=self.context, cancel_check=check)
        self.assertEqual(calls.call_count, 1)

    def test_an_overflow_after_streamed_content_does_not_replay_the_request(self):
        received = []
        def partial(profile, messages, **kwargs):
            kwargs["on_delta"]("Partial output")
            raise runtime.ContextOverflowError("too long")
        with patch.object(workflow, "chat_raw", side_effect=partial) as calls:
            with self.assertRaises(runtime.ContextOverflowError):
                workflow.call_with_fallback(None, [self.profile], self.messages,
                    request_context=self.context, on_delta=received.append)
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(received, ["Partial output"])

    def test_each_fallback_uses_its_own_capacity_and_output_limit(self):
        backup = {**self.profile, "id": "backup", "model": "backup", "context_window": 32768, "max_output": 2048}
        with patch.object(workflow, "chat_raw", side_effect=[runtime.ModelUnavailableError("offline"), {"content": "Backup"}]) as calls:
            result = workflow.call_with_fallback(None, [self.profile, backup], [{"role": "user", "content": "Continue"}],
                                                max_tokens=16000, request_context=self.context)
        self.assertEqual(result["content"], "Backup")
        self.assertEqual([call.kwargs["max_tokens"] for call in calls.call_args_list], [4096, 2048])
        self.assertEqual([call.args[0]["_context_budget"]["window"] for call in calls.call_args_list], [65536, 32768])


if __name__ == "__main__":
    unittest.main()
