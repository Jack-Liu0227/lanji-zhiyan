"""Tests for packages/ai-runtime: RetryPolicy, Budget, Telemetry, and wire execution."""
from __future__ import annotations

import http.client
import json
import io
import ssl
import urllib.error
import unittest
from unittest.mock import MagicMock, patch

from runtime import (
    AIRuntimeError,
    Budget,
    BudgetExceededError,
    CancelledError,
    RetryPolicy,
    Telemetry,
    call,
)


def http_error(status, code, headers=None):
    return urllib.error.HTTPError('https://example.test/v1', status, 'failure', headers or {},
                                  io.BytesIO(json.dumps({'error': {'code': code}}).encode()))


class AIRuntimeTest(unittest.TestCase):
    def test_outbound_scrubbing_does_not_mutate_checkpoint_messages(self):
        profile = {'base_url': 'https://example.invalid/v1', 'model': 'fixture'}
        messages = [{'role': 'user', 'content': 'Return this private contact: a@b.co'}]
        with patch('runtime._call_wire_openai_completions', return_value={'content': 'ok'}) as wire:
            call(profile, messages)
        self.assertEqual(messages[0]['content'], 'Return this private contact: a@b.co')
        self.assertNotIn('a@b.co', wire.call_args.args[1][0]['content'])

    def test_unavailable_gateway_channel_has_actionable_error_without_blind_retries(self):
        raw = json.dumps({'error': {'code': 'get_channel_failed', 'message': 'no channel'}}).encode()
        error = urllib.error.HTTPError('https://example.test/v1/chat/completions', 500, 'failure', {}, io.BytesIO(raw))
        with patch('runtime.open_guarded_url', side_effect=error) as request, patch('runtime.time.sleep') as sleep:
            with self.assertRaisesRegex(AIRuntimeError, '可用渠道'):
                call({'base_url': 'https://example.test/v1', 'model': 'unavailable', 'api_key': 'test'}, [{'role': 'user', 'content': 'test'}])
        request.assert_called_once()
        sleep.assert_not_called()

    def test_retry_policy_delay_calculation(self):
        policy = RetryPolicy(base_delay=1.0, max_delay=10.0, backoff_factor=2.0)
        delay0 = policy.delay_for_attempt(0)
        self.assertGreaterEqual(delay0, 1.0)
        self.assertLessEqual(delay0, 1.5)

        delay5 = policy.delay_for_attempt(5)
        self.assertEqual(delay5, 10.0)

    def test_429_honors_retry_after_and_posts_once_per_attempt(self):
        profile = {'base_url': 'https://example.test/v1', 'model': 'model'}
        success = io.BytesIO(b'{"choices":[{"message":{"content":"Recovered"}}]}')
        with patch('workflow.open_url', side_effect=[http_error(429, 'rate_limit_exceeded', {'Retry-After': '35'}), success]) as request, \
                patch('runtime.time.sleep') as sleep:
            self.assertEqual(call(profile, [])['content'], 'Recovered')
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(35)

    def test_unaffordable_quota_and_long_cooldown_do_not_retry(self):
        profile = {'base_url': 'https://example.test/v1', 'model': 'model'}
        for code, headers, message in [('insufficient_quota', {}, '额度不足'),
                                       ('rate_limit_exceeded', {'Retry-After': '600'}, '至少等待 600 秒')]:
            with self.subTest(code=code), patch('workflow.open_url', side_effect=http_error(429, code, headers)) as request, \
                    patch('runtime.time.sleep') as sleep:
                with self.assertRaisesRegex(AIRuntimeError, message):
                    call(profile, [])
                request.assert_called_once()
                sleep.assert_not_called()

    def test_responses_rate_limit_retries_only_before_output(self):
        profile = {'base_url': 'https://example.test/v1', 'model': 'model', 'api': 'openai-responses'}
        limited = b'data: {"type":"error","code":"rate_limit_exceeded"}\n\n'
        delta = b'data: {"type":"response.output_text.delta","delta":"A"}\n\n'
        completed = b'data: {"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"A"}]}]}}\n\n'
        parts = []
        with patch('workflow.open_url', side_effect=[io.BytesIO(limited), io.BytesIO(delta + completed)]) as request, \
                patch('runtime.time.sleep'):
            self.assertEqual(call(profile, [], on_delta=parts.append)['content'], 'A')
            self.assertEqual(request.call_count, 2)
        self.assertEqual(parts, ['A'])
        with patch('workflow.open_url', return_value=io.BytesIO(delta + limited)) as request, patch('runtime.time.sleep') as sleep:
            with self.assertRaisesRegex(AIRuntimeError, '429'):
                call(profile, [], on_delta=parts.append)
            request.assert_called_once()
            sleep.assert_not_called()

    def test_budget_token_tracking_and_limit(self):
        b = Budget(max_tokens=100)
        b.add_usage(40, 20)
        self.assertEqual(b.used_tokens, 60)

        with self.assertRaises(BudgetExceededError):
            b.add_usage(30, 20)

    def test_telemetry_recording(self):
        tel = Telemetry()
        tel.record("openai-completions", "gpt-4o", 250, {"prompt": 100, "completion": 50, "total": 150}, "ok")
        tel.record("anthropic-messages", "claude-3-5", 300, {"prompt": 80, "completion": 40, "total": 120}, "error", "Rate limited")

        summ = tel.summary()
        self.assertEqual(summ["total_calls"], 2)
        self.assertEqual(summ["successful_calls"], 1)
        self.assertEqual(summ["failed_calls"], 1)
        self.assertEqual(summ["total_tokens"], 270)

    def test_call_honors_cancelled_check(self):
        profile = {"base_url": "https://api.openai.com/v1", "model": "gpt-4o"}
        with self.assertRaises(CancelledError):
            call(profile, [{"role": "user", "content": "hi"}], cancel_check=lambda: True)

    def test_call_openai_completions_mock(self):
        profile = {"base_url": "https://api.openai.com/v1", "model": "gpt-4o", "api_key": "test-key"}
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "Hello scientific world!"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = False

        with patch("runtime.open_guarded_url", return_value=mock_response):
            res = call(profile, [{"role": "user", "content": "Hi"}])

        self.assertEqual(res["content"], "Hello scientific world!")
        self.assertEqual(res["usage"]["total"], 15)

    def test_stream_response_exposes_finish_reason_and_incomplete_eof(self):
        profile = {"base_url": "https://example.test/v1", "model": "model"}
        complete = io.BytesIO(
            b'data: {"choices":[{"delta":{"content":"Answer"},"finish_reason":null}]}\n\n'
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        )
        incomplete = io.BytesIO(
            b'data: {"choices":[{"delta":{"content":"Partial"},"finish_reason":null}]}\n\n'
        )
        with patch("runtime.open_guarded_url", side_effect=[complete, incomplete]):
            first = call(profile, [], on_delta=lambda _piece: None)
            second = call(profile, [], on_delta=lambda _piece: None)

        self.assertEqual(first["finish_reason"], "stop")
        self.assertTrue(first["stream_complete"])
        self.assertIsNone(second["finish_reason"])
        self.assertFalse(second["stream_complete"])

    def test_mid_read_ssl_eof_and_incomplete_read_retry_before_output(self):
        profile = {"base_url": "https://example.test/v1", "model": "model", "api": "openai-responses"}
        completed = b'data: {"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"OK"}]}]}}\n\n'
        broken_stream = MagicMock()
        broken_stream.__enter__.return_value = broken_stream
        broken_stream.__exit__.return_value = False
        broken_stream.__iter__.side_effect = ssl.SSLEOFError(8, "EOF occurred in violation of protocol")
        with patch("workflow.open_url", side_effect=[
            broken_stream,
            http.client.IncompleteRead(b""),
            io.BytesIO(completed),
        ]) as request, patch("runtime.time.sleep") as sleep:
            res = call(profile, [], on_delta=lambda _piece: None)
        self.assertEqual(res["content"], "OK")
        self.assertEqual(request.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_workflow_open_url_falls_back_to_direct_on_proxy_tls_eof(self):
        import urllib.request
        import workflow

        req = urllib.request.Request("https://example.test/v1/models")
        proxy_opener = MagicMock()
        proxy_opener.open.side_effect = urllib.error.URLError(ssl.SSLEOFError(8, "UNEXPECTED_EOF_WHILE_READING"))
        direct_opener = MagicMock()
        direct_opener.open.return_value = io.BytesIO(b"ok")

        with patch.dict("os.environ", {"HTTPS_PROXY": "http://127.0.0.1:7897"}, clear=False), \
                patch("workflow.socket.create_connection", return_value=MagicMock(__enter__=lambda s: s, __exit__=lambda *a: False)), \
                patch("workflow.urllib.request.build_opener", side_effect=[direct_opener, proxy_opener]):
            res = workflow.open_url(req, timeout=5)
        self.assertEqual(res.read(), b"ok")
        proxy_opener.open.assert_called_once()
        direct_opener.open.assert_called_once()

    def test_reasoning_only_before_ssl_eof_still_retries(self):
        profile = {"base_url": "https://example.test/v1", "model": "model", "api": "openai-responses"}
        class BrokenReasoningStream:
            def __enter__(self): return self
            def __exit__(self, *_a): return False
            def __iter__(self):
                yield b'data: {"type":"response.reasoning_summary_text.delta","delta":"thinking"}\n'
                yield b'\n'
                raise urllib.error.URLError(ssl.SSLEOFError(8, "TLS/SSL connection has been closed (EOF)"))
        reasoning_then_drop = BrokenReasoningStream()
        completed = io.BytesIO(
            b'data: {"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"RECOVERED"}]}]}}\n\n'
        )
        seen_reasoning = []
        with patch("workflow.open_url", side_effect=[reasoning_then_drop, completed]) as opener, \
                patch("runtime.time.sleep") as sleep:
            res = call(profile, [], on_delta=lambda _p: None, on_reasoning=seen_reasoning.append)
        self.assertEqual(res["content"], "RECOVERED")
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        self.assertEqual(seen_reasoning, ["thinking"])


if __name__ == "__main__":
    unittest.main()
