"""Behavioral coverage for offline context budgeting across model candidates."""
import tempfile
import unittest
from unittest.mock import patch

import model_context as context


class ModelContextTest(unittest.TestCase):
    def setUp(self):
        self.offline = patch.object(context, "_offline_encoding", return_value=None)
        self.offline.start()
        self.addCleanup(self.offline.stop)

    def test_unknown_model_uses_an_explicit_estimate_and_ignores_price_ranges(self):
        budget = context.measure_request({"model": "gpt-6.1-sol", "max_input": 1_000_000}, [], [], 8_000)
        self.assertEqual(budget.window, 65_536)
        self.assertTrue(budget.estimated_window)
        self.assertIn("estimated", budget.source)
        self.assertEqual(budget.count_method, "estimated_utf8_mixed")
        self.assertEqual((budget.trigger_tokens, budget.target_tokens, budget.safety), (53_440, 40_080, 4_096))

    def test_explicit_overrides_take_precedence_over_the_actual_profile(self):
        budget = context.measure_request(
            {"context_window": 100_000, "max_output": 32_000}, [], [], 8_000,
            {"context_window": 32_768, "max_output_tokens": 8_192},
        )
        self.assertEqual((budget.window, budget.max_output), (32_768, 8_192))
        self.assertFalse(budget.estimated_window)
        self.assertIn("overrides.context_window", budget.source)
        self.assertEqual(budget.trigger_tokens, 20_672)

    def test_retry_with_double_output_rechecks_the_candidate_limit(self):
        profile = {"model": "configured-model", "context_window": 65_536, "max_output": 12_000}
        first = context.measure_request(profile, [], [], 8_000)
        self.assertTrue(first.fits)
        with self.assertRaisesRegex(ValueError, "16000.*12000"):
            context.measure_request(profile, [], [], 16_000)

    def test_retry_output_reduces_the_available_input_budget(self):
        first = context.measure_request({"context_window": 65_536}, [], [], 8_000)
        retry = context.measure_request({"context_window": 65_536}, [], [], 16_000)
        self.assertEqual((first.trigger_tokens, retry.trigger_tokens), (53_440, 45_440))

    def test_small_fallback_window_is_validated_without_reusing_primary_budget(self):
        primary = context.measure_request({"context_window": 100_000}, [], [], 8_000)
        self.assertEqual(primary.trigger_tokens, 87_000)
        with self.assertRaisesRegex(ValueError, "8192.*8000"):
            context.measure_request({"model": "small-fallback", "context_window": 8_192}, [], [], 8_000)

    def test_long_history_is_marked_for_compaction_instead_of_being_rejected(self):
        budget = context.measure_request({}, [{"role": "user", "content": "a" * 200_000}], [], 8_000)
        self.assertTrue(budget.needs_compaction)
        self.assertFalse(budget.fits)

    def test_fallback_estimate_reserves_more_for_unicode_and_punctuation(self):
        empty = context.request_tokens({}, [{"role": "user", "content": ""}], [])
        english = context.request_tokens({}, [{"role": "user", "content": "a" * 63}], [])
        chinese = context.request_tokens({}, [{"role": "user", "content": "中" * 64}], [])
        punctuation = context.request_tokens({}, [{"role": "user", "content": "?" * 64}], [])
        self.assertEqual(english - empty, 21)
        self.assertEqual(chinese - empty, 96)
        self.assertEqual(punctuation - empty, 64)

    def test_byte_upper_bound_can_still_be_requested_explicitly(self):
        profile = {"tokenizer": "utf8_bytes_conservative"}
        empty = context.request_tokens(profile, [{"role": "user", "content": ""}], [])
        chinese = context.request_tokens(profile, [{"role": "user", "content": "中" * 64}], [])
        budget = context.measure_request(profile, [], [], 8000)
        self.assertEqual(chinese - empty, 192)
        self.assertEqual(budget.count_method, "utf8_bytes_conservative")

    def test_tool_schemas_consume_input_budget(self):
        messages = [{"role": "user", "content": "read"}]
        bare = context.request_tokens({}, messages, [])
        declared = context.request_tokens({}, messages, [{"type": "function", "function": {"name": "read", "description": "x" * 500}}])
        self.assertGreater(declared - bare, 180)

    def test_images_reserve_tokens_without_counting_base64_as_text(self):
        def image_request(size):
            return [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + "x" * size}}]}]
        short = context.request_tokens({}, image_request(10), [])
        long = context.request_tokens({}, image_request(100_000), [])
        self.assertEqual(short, long)
        self.assertGreater(short, 4_096)

    def test_internal_message_annotations_are_not_counted_as_wire_content(self):
        base = {"role": "user", "content": "read"}
        self.assertEqual(context.request_tokens({}, [base], []), context.request_tokens({}, [{**base, "_checkpoint": "x" * 10_000}], []))

    def test_invalid_window_and_output_configuration_fail_clearly(self):
        for value in (False, 0, -1, "unknown", 4.5):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "context_window"):
                context.resolve_spec({"context_window": value})
        with self.assertRaisesRegex(ValueError, "output_limit"):
            context.measure_request({}, [], [], 0)

    def test_compaction_reserves_summary_and_recent_context_under_the_trigger(self):
        budget = context.measure_request({"context_window": 32_768}, [], [], 16_000)
        self.assertLess(budget.target_tokens, budget.trigger_tokens)
        self.assertEqual(budget.summary_tokens, 1024)
        self.assertGreater(budget.retained_tokens, 0)
        self.assertLess(budget.summary_tokens + budget.retained_tokens, budget.target_tokens)

    def test_cached_tiktoken_encoding_is_preferred(self):
        self.offline.stop()
        try:
            from tiktoken import registry
        except ImportError:
            self.skipTest("optional tiktoken is not installed")

        class Encoding:
            name = "test-offline-encoding"

            def encode(self, text, disallowed_special=()):
                return [0] * 5

        with patch.dict(registry.ENCODINGS, {"test-offline-encoding": Encoding()}):
            budget = context.measure_request({"tokenizer": "test-offline-encoding"}, [], [], 8_000)
        self.assertEqual(budget.count_method, "tiktoken:test-offline-encoding")
        self.assertEqual(budget.input_tokens, 37)

    def test_small_windows_adapt_the_summary_margin_to_available_capacity(self):
        budget = context.measure_request({"context_window": 8_192, "max_output": 2_048}, [], [], 2_048)
        self.assertLess(budget.target_tokens, budget.trigger_tokens)
        self.assertTrue(budget.fits)
        self.assertGreaterEqual(budget.summary_tokens, 512)

    def test_missing_tokenizer_cache_does_not_attempt_a_download(self):
        self.offline.stop()
        try:
            import tiktoken
            from tiktoken import registry
        except ImportError:
            self.skipTest("optional tiktoken is not installed")
        with tempfile.TemporaryDirectory() as cache, patch.dict(context.os.environ, {"TIKTOKEN_CACHE_DIR": cache}), \
                patch.dict(registry.ENCODINGS, {}, clear=True), patch.object(tiktoken, "get_encoding", side_effect=AssertionError("download attempted")):
            budget = context.measure_request({"model": "gpt-4o"}, [], [], 8_000)
        self.assertEqual(budget.count_method, "estimated_utf8_mixed")


if __name__ == "__main__":
    unittest.main()
