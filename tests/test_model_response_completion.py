"""Provider completion states survive normalization without losing partial output."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import agent_protocols


class ModelResponseCompletionTest(unittest.TestCase):
    def profile(self, api, **extra):
        return {"api": api, "base_url": "https://example.invalid/v1", "model": "fixture", **extra}

    def complete(self, api, raw):
        with patch.object(agent_protocols, "_request", return_value=raw):
            return agent_protocols.complete(self.profile(api), [{"role": "user", "content": "Continue"}])

    def stream(self, api, events):
        pieces = []
        with patch.object(agent_protocols, "_stream_events", return_value=iter(events)):
            result = agent_protocols.complete(self.profile(api), [], on_delta=pieces.append)
        return result, pieces

    def test_responses_incomplete_retains_text_tool_arguments_and_provider_reason(self):
        result = self.complete("openai-responses", {
            "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "Partial answer"}]},
                {"type": "function_call", "call_id": "call-1", "name": "save", "arguments": '{"content":"part'},
            ],
        })
        self.assertEqual(result["content"], "Partial answer")
        self.assertEqual(result["tool_calls"][0]["function"], {"name": "save", "arguments": '{"content":"part'})
        self.assertEqual(result["finish_reason"], "length")
        self.assertEqual(result["incomplete_details"], {"reason": "max_output_tokens"})
        self.assertFalse(result["stream_complete"])

    def test_responses_incomplete_stream_keeps_partial_message_and_tool_deltas(self):
        result, pieces = self.stream("openai-responses", [
            {"type": "response.output_item.added", "output_index": 0,
             "item": {"type": "message", "role": "assistant", "content": []}},
            {"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": "Partial answer"},
            {"type": "response.output_item.added", "output_index": 1,
             "item": {"type": "function_call", "call_id": "call-1", "name": "save", "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": '{"content":"part'},
            {"type": "response.incomplete", "response": {"status": "incomplete",
             "incomplete_details": {"reason": "max_output_tokens"}}},
        ])
        self.assertEqual(pieces, ["Partial answer"])
        self.assertEqual(result["content"], "Partial answer")
        self.assertEqual(result["tool_calls"][0]["function"]["arguments"], '{"content":"part')
        self.assertEqual(result["finish_reason"], "length")
        self.assertFalse(result["stream_complete"])

    def test_responses_missing_terminal_event_preserves_partial_content(self):
        for prefix in ([], [{"type": "response.output_item.added", "output_index": 0,
                            "item": {"type": "message", "role": "assistant", "content": []}}]):
            with self.subTest(prefix=bool(prefix)):
                result, pieces = self.stream("openai-responses", [*prefix,
                    {"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": "First "},
                    {"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": "last"},
                ])
                self.assertEqual(pieces, ["First ", "last"])
                self.assertEqual(result["content"], "First last")
                self.assertEqual(result["incomplete_details"], {"reason": "stream_terminated"})
                self.assertFalse(result["stream_complete"])

    def test_responses_completed_stream_uses_final_text_without_duplication(self):
        result, _ = self.stream("openai-responses", [
            {"type": "response.output_text.delta", "output_index": 0, "delta": "Answer"},
            {"type": "response.completed", "response": {"status": "completed", "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "Answer"}]},
                {"type": "function_call", "call_id": "call-1", "name": "save", "arguments": '{"content":"full"}'},
            ]}},
        ])
        self.assertEqual(result["content"], "Answer")
        self.assertEqual(result["tool_calls"][0]["function"]["arguments"], '{"content":"full"}')
        self.assertTrue(result["stream_complete"])
        self.assertEqual(result["finish_reason"], "stop")

    def test_responses_missing_terminal_keeps_each_message_in_order(self):
        result, _ = self.stream("openai-responses", [
            {"type": "response.output_text.delta", "output_index": 0, "delta": "First message"},
            {"type": "response.output_text.delta", "output_index": 1, "delta": "Second message"},
        ])
        self.assertEqual(result["content"], "First message\nSecond message")
        self.assertFalse(result["stream_complete"])

    def test_responses_refusal_is_preserved_as_text(self):
        result, _ = self.stream("openai-responses", [
            {"type": "response.refusal.delta", "output_index": 0, "delta": "Cannot provide that."},
            {"type": "response.completed", "response": {"status": "completed"}},
        ])
        self.assertEqual(result["content"], "Cannot provide that.")
        self.assertTrue(result["stream_complete"])

    def test_responses_noncompleted_status_is_not_success(self):
        for status in ("queued", "in_progress", "cancelled"):
            with self.subTest(status=status):
                result = self.complete("openai-responses", {"status": status, "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": "Partial"}]}]})
                self.assertEqual(result["content"], "Partial")
                self.assertEqual(result["status"], status)
                self.assertEqual(result["finish_reason"], "incomplete")
                self.assertFalse(result["stream_complete"])

    def test_anthropic_nonstream_output_limit_is_incomplete(self):
        result = self.complete("anthropic-messages", {
            "content": [{"type": "text", "text": "Partial answer"}], "stop_reason": "max_tokens"})
        self.assertEqual(result["content"], "Partial answer")
        self.assertEqual(result["finish_reason"], "max_tokens")
        self.assertFalse(result["stream_complete"])

    def test_anthropic_stream_retains_stop_reason(self):
        for reason, expected in (("max_tokens", False), ("model_context_window_exceeded", False),
                                 ("end_turn", True)):
            with self.subTest(reason=reason):
                result, pieces = self.stream("anthropic-messages", [
                    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Answer"}},
                    {"type": "message_delta", "delta": {"stop_reason": reason}},
                    {"type": "message_stop"},
                ])
                self.assertEqual(result["content"], "Answer")
                self.assertEqual(pieces, ["Answer"])
                self.assertEqual(result["finish_reason"], reason)
                self.assertEqual(result["stream_complete"], expected)

    def test_anthropic_stream_keeps_unfinished_tool_arguments(self):
        result, _ = self.stream("anthropic-messages", [
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "tool_use", "id": "call-1", "name": "save", "input": {}}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "input_json_delta", "partial_json": '{"content":"part'}},
            {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}},
            {"type": "message_stop"},
        ])
        self.assertEqual(result["tool_calls"][0]["function"]["arguments"], '{"content":"part')
        self.assertEqual(result["finish_reason"], "incomplete")
        self.assertFalse(result["stream_complete"])

    def test_anthropic_stream_without_stop_event_is_incomplete(self):
        result, _ = self.stream("anthropic-messages", [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Partial"}},
        ])
        self.assertEqual(result["content"], "Partial")
        self.assertEqual(result["finish_reason"], "incomplete")
        self.assertFalse(result["stream_complete"])

    def test_anthropic_thinking_stays_inside_actual_requested_output_cap(self):
        for effort in ("low", "high", "xhigh"):
            for requested in (512, 1024, 1025, 4096, 20000):
                with self.subTest(effort=effort, requested=requested), \
                        patch.object(agent_protocols, "_request", return_value={"content": [], "stop_reason": "end_turn"}) as request:
                    agent_protocols.complete(self.profile("anthropic-messages", reasoning=effort), [], max_tokens=requested)
                    payload = request.call_args.args[1]
                    self.assertEqual(payload["max_tokens"], requested)
                    if requested <= 1024:
                        self.assertEqual(payload["thinking"], {"type": "disabled"})
                    else:
                        self.assertGreaterEqual(payload["thinking"]["budget_tokens"], 1024)
                        self.assertLess(payload["thinking"]["budget_tokens"], requested)

    def test_gemini_nonstream_output_limit_is_incomplete(self):
        result = self.complete("google-generative-ai", {"candidates": [{
            "content": {"parts": [{"text": "Partial answer"}]}, "finishReason": "MAX_TOKENS"}]})
        self.assertEqual(result["content"], "Partial answer")
        self.assertEqual(result["finish_reason"], "length")
        self.assertFalse(result["stream_complete"])

    def test_gemini_stream_retains_finish_reason_and_text(self):
        for reason, expected, normalized in (("MAX_TOKENS", False, "length"), ("STOP", True, "STOP")):
            with self.subTest(reason=reason):
                result, pieces = self.stream("google-generative-ai", [
                    {"candidates": [{"content": {"parts": [{"text": "First "}]}}]},
                    {"candidates": [{"content": {"parts": [{"text": "last"}]}, "finishReason": reason}]},
                ])
                self.assertEqual(result["content"], "First last")
                self.assertEqual(pieces, ["First ", "last"])
                self.assertEqual(result["finish_reason"], normalized)
                self.assertEqual(result["stream_complete"], expected)

    def test_gemini_stream_without_finish_reason_is_incomplete(self):
        result, _ = self.stream("google-generative-ai", [
            {"candidates": [{"content": {"parts": [{"text": "Partial"}]}}]},
        ])
        self.assertEqual(result["content"], "Partial")
        self.assertEqual(result["finish_reason"], "incomplete")
        self.assertFalse(result["stream_complete"])

    def test_gemini_abnormal_stop_keeps_text_without_marking_success(self):
        for reason in ("SAFETY", "RECITATION", "MALFORMED_FUNCTION_CALL", "OTHER"):
            with self.subTest(reason=reason):
                result = self.complete("google-generative-ai", {"candidates": [{
                    "content": {"parts": [{"text": "Partial"}]}, "finishReason": reason}]})
                self.assertEqual(result["content"], "Partial")
                self.assertEqual(result["finish_reason"], reason)
                self.assertFalse(result["stream_complete"])


if __name__ == "__main__":
    unittest.main()
