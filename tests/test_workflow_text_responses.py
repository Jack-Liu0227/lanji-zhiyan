"""Plain text and JSON helpers enforce complete responses and one retry total."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import workflow


class WorkflowTextResponsesTest(unittest.TestCase):
    def setUp(self):
        self.profile = {"model": "text-fixture", "base_url": "https://invalid.local/v1",
                        "context_window": 1000000, "max_output": 128000}
        self.messages = [{"role": "user", "content": "Return the complete result."}]

    def test_default_text_and_json_budgets_use_declared_model_capacity(self):
        for function, expected in ((workflow.chat, '{"ok":1}'), (workflow.chat_json, {"ok": 1})):
            with self.subTest(function=function.__name__), \
                    patch.object(workflow, "chat_raw", return_value={"content": '{"ok":1}'}) as model:
                result = function(self.profile, self.messages)
            self.assertEqual(result, expected)
            self.assertEqual(model.call_count, 1)
            self.assertEqual(model.call_args.kwargs["max_tokens"], 128000)

    def test_unknown_model_uses_window_estimate_without_an_eight_thousand_token_ceiling(self):
        profile = {"model": "unknown", "base_url": "https://invalid.local/v1"}
        for function in (workflow.chat, workflow.chat_json):
            with self.subTest(function=function.__name__), \
                    patch.object(workflow, "chat_raw", return_value={"content": '{"ok":1}'}) as model:
                function(profile, self.messages)
            self.assertEqual(model.call_args.kwargs["max_tokens"], 16384)

    def test_explicit_output_is_capped_by_actual_capacity_on_both_attempts(self):
        profile = {"model": "gpt-6.1-sol", "base_url": "https://api.openai.com/v1",
                   "api": "openai-responses"}
        for function, expected in ((workflow.chat, '{"ok":1}'), (workflow.chat_json, {"ok": 1})):
            with self.subTest(function=function.__name__), patch.object(workflow, "chat_raw", side_effect=[
                    {"content": "", "finish_reason": "length"}, {"content": '{"ok":1}'}]) as model:
                result = function(profile, self.messages, max_tokens=200000)
            self.assertEqual(result, expected)
            self.assertEqual([call.kwargs["max_tokens"] for call in model.call_args_list], [128000, 128000])

    def test_explicit_smaller_budget_and_call_options_are_preserved_without_doubling(self):
        with patch.object(workflow, "chat_raw", side_effect=[{"content": ""}, {"content": "Complete text."}]) as model:
            result = workflow.chat(self.profile, self.messages, 0.4, 25, 1500)
        self.assertEqual(result, "Complete text.")
        self.assertEqual([call.kwargs["max_tokens"] for call in model.call_args_list], [1500, 1500])
        self.assertTrue(all(call.kwargs["temperature"] == 0.4 and call.kwargs["timeout"] == 25
                            for call in model.call_args_list))

    def test_nonempty_incomplete_text_retries_instead_of_succeeding(self):
        failures = [{"finish_reason": "length"}, {"finish_reason": "max_tokens"},
                    {"finish_reason": "max_output_tokens"}, {"finish_reason": "content_filter"},
                    {"stream_complete": False}, {"status": "incomplete"},
                    {"status": "failed"}, {"status": "cancelled"}]
        for fields in failures:
            with self.subTest(fields=fields), patch.object(workflow, "chat_raw", side_effect=[
                    {"content": "Partial text.", **fields}, {"content": "Complete text."}]) as model:
                result = workflow.chat(self.profile, self.messages)
            self.assertEqual(result, "Complete text.")
            self.assertEqual(model.call_count, 2)

    def test_second_empty_or_incomplete_text_fails_explicitly(self):
        for response, reason in (({"content": "  "}, "正文为空"),
                                 ({"content": "Partial text.", "finish_reason": "length"}, "finish_reason=length")):
            with self.subTest(response=response), patch.object(workflow, "chat_raw", return_value=response) as model:
                with self.assertRaisesRegex(RuntimeError, reason):
                    workflow.chat(self.profile, self.messages)
            self.assertEqual(model.call_count, 2)

    def test_json_format_retry_keeps_original_messages_and_unwraps_an_object_array(self):
        original = deepcopy(self.messages)
        with patch.object(workflow, "chat_raw", side_effect=[
                {"content": "This is explanatory prose."}, {"content": '[{"verdict":"accepted"}]'}]) as model:
            result = workflow.chat_json(self.profile, self.messages)
        self.assertEqual(result, {"verdict": "accepted"})
        self.assertEqual(model.call_count, 2)
        self.assertEqual(model.call_args_list[0].args[1], original)
        self.assertEqual(model.call_args_list[1].args[1][:-1], original)
        self.assertIn("只输出 JSON 本体", model.call_args_list[1].args[1][-1]["content"])
        self.assertEqual(self.messages, original)

    def test_empty_text_and_json_format_failures_share_one_retry(self):
        for first in ({"content": ""}, {"content": '{"ok":1}', "finish_reason": "length"}):
            with self.subTest(first=first), patch.object(workflow, "chat_raw", side_effect=[
                    first, {"content": "Still not JSON."}, {"content": '{"ok":1}'}]) as model:
                with self.assertRaisesRegex(RuntimeError, "没有返回 JSON"):
                    workflow.chat_json(self.profile, self.messages)
            self.assertEqual(model.call_count, 2)

    def test_valid_looking_json_with_incomplete_response_is_never_accepted(self):
        with patch.object(workflow, "chat_raw", side_effect=[
                {"content": "Not JSON."}, {"content": '{"ok":1}', "finish_reason": "length"}]) as model:
            with self.assertRaisesRegex(RuntimeError, "完整 JSON.*finish_reason=length"):
                workflow.chat_json(self.profile, self.messages)
        self.assertEqual(model.call_count, 2)

    def test_second_empty_json_response_fails_explicitly(self):
        with patch.object(workflow, "chat_raw", return_value={"content": ""}) as model:
            with self.assertRaisesRegex(RuntimeError, "完整 JSON.*正文为空"):
                workflow.chat_json(self.profile, self.messages)
        self.assertEqual(model.call_count, 2)

    def test_transport_errors_propagate_without_spending_a_format_retry(self):
        for function in (workflow.chat, workflow.chat_json):
            with self.subTest(function=function.__name__), \
                    patch.object(workflow, "chat_raw", side_effect=RuntimeError("Provider is unavailable")) as model:
                with self.assertRaisesRegex(RuntimeError, "Provider is unavailable"):
                    function(self.profile, self.messages)
            self.assertEqual(model.call_count, 1)

    def test_summary_transport_can_disable_hidden_network_retries(self):
        import runtime
        policy = runtime.RetryPolicy(max_retries=0)
        with patch.object(workflow.ModelInvoker, 'invoke', return_value=None), \
                patch.object(runtime, 'call', return_value={'content': 'Summary.'}) as model:
            result = workflow.chat_raw(self.profile, self.messages, retry_policy=policy)
        self.assertEqual(result['content'], 'Summary.')
        self.assertIs(model.call_args.kwargs['retry_policy'], policy)


if __name__ == "__main__":
    unittest.main()
