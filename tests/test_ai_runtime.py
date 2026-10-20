"""Tests for packages/ai-runtime: RetryPolicy, Budget, Telemetry, and wire execution."""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from runtime import (
    Budget,
    BudgetExceededError,
    CancelledError,
    RetryPolicy,
    Telemetry,
    call,
)


class AIRuntimeTest(unittest.TestCase):
    def test_retry_policy_delay_calculation(self):
        policy = RetryPolicy(base_delay=1.0, max_delay=10.0, backoff_factor=2.0)
        delay0 = policy.delay_for_attempt(0)
        self.assertGreaterEqual(delay0, 1.0)
        self.assertLessEqual(delay0, 1.5)

        delay5 = policy.delay_for_attempt(5)
        self.assertEqual(delay5, 10.0)

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


if __name__ == "__main__":
    unittest.main()
