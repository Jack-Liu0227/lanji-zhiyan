"""Invalid provider continuation state has one bounded, lossless recovery path."""
from __future__ import annotations

from copy import deepcopy
import io
import json
import unittest
import urllib.error
from unittest.mock import Mock, patch

import model_context
import runtime
import workflow
from workflow_compaction import WorkflowContext


def rejected(status=400, message="The encrypted content for item rs_old could not be verified.",
             code="invalid_request_error"):
    body = json.dumps({"error": {"type": "invalid_request_error", "code": code, "message": message}}).encode()
    return urllib.error.HTTPError("https://example.invalid/v1/responses", status, "rejected", {}, io.BytesIO(body))


def assistant(text="Visible prior answer", encrypted="opaque-old-state"):
    return {"role": "assistant", "content": text,
            "tool_calls": [{"id": "saved-call", "type": "function", "function": {
                "name": "external_write", "arguments": '{"path":"saved.md","content":"Already saved"}'}}],
            "_protocol": {"api": "openai-responses", "output": [
                {"type": "reasoning", "id": "rs_old", "encrypted_content": encrypted, "summary": []},
                {"type": "message", "content": [{"type": "output_text", "text": text}]},
                {"type": "function_call", "call_id": "saved-call", "name": "external_write",
                 "arguments": '{"path":"saved.md","content":"Already saved"}'},
            ]}}


class WorkflowProtocolRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.profile = {"api": "openai-responses", "base_url": "https://example.invalid/v1",
                        "model": "fixture-model", "context_window": 65536, "max_output": 4096}
        self.saved = Mock()
        self.context = self.controller()
        self.messages = [
            {"role": "system", "content": "Preserve accepted outputs and evidence."},
            {"role": "user", "content": "Save the evidence."}, assistant(),
            {"role": "tool", "tool_call_id": "saved-call", "name": "external_write",
             "content": '{"path":"saved.md","content_hash":"accepted-version","saved":true}'},
            {"role": "user", "content": "Continue using the saved result."},
        ]
        self.offline = patch.object(model_context, "_offline_encoding", return_value=None)
        self.offline.start()
        self.addCleanup(self.offline.stop)

    def controller(self, state=None):
        return WorkflowContext(None, state=state, save_checkpoint=self.saved,
                               summarize=Mock(return_value="Keep the accepted evidence and saved.md."))

    def invoke(self, messages=None, context=None, **kwargs):
        return workflow.call_with_fallback(None, [self.profile], messages or self.messages,
                                          max_tokens=4096, request_context=context or self.context, **kwargs)

    def test_only_confirmed_responses_http400_gets_the_dedicated_error(self):
        cases = [
            (400, "unrelated message", "invalid_encrypted_content", "openai-responses", True),
            (400, "Encrypted content could not be verified", "invalid_request_error", "openai-responses", True),
            (400, "Encrypted content could not be decrypted or parsed", "invalid_request_error", "openai-responses", True),
            (400, "Encrypted content is required", "invalid_request_error", "openai-responses", False),
            (400, "The signature could not be verified", "invalid_request_error", "openai-responses", False),
            (401, "Encrypted content could not be verified", "invalid_encrypted_content", "openai-responses", False),
            (403, "Encrypted content could not be verified", "invalid_encrypted_content", "openai-responses", False),
            (400, "Encrypted content could not be verified", "invalid_encrypted_content", "openai-completions", False),
        ]
        for status, message, code, api, expected in cases:
            with self.subTest(status=status, message=message, api=api), \
                    patch.object(workflow, "open_url", side_effect=rejected(status, message, code)) as request, \
                    patch.object(runtime.time, "sleep") as sleep:
                with self.assertRaises(runtime.AIRuntimeError) as caught:
                    runtime.call({**self.profile, "api": api}, [], retry_policy=runtime.RetryPolicy(max_retries=0))
                self.assertEqual(isinstance(caught.exception, runtime.InvalidEncryptedContentError), expected)
                if expected:
                    self.assertEqual(caught.exception.error_kind, "invalid_encrypted_content")
                    self.assertEqual(caught.exception.status_code, 400)
                    self.assertFalse(runtime._is_fallback_eligible_error(caught.exception))
                request.assert_called_once()
                sleep.assert_not_called()

    def test_protocol_recovery_changes_only_a_copy_and_remeasures_budget(self):
        self.messages.insert(1, {"role": "assistant", "content": "Other provider history",
                                 "_protocol": {"api": "anthropic-messages", "content": []}})
        original = deepcopy(self.messages)
        prepared = self.context.prepare(self.profile, self.messages, [], 4096)
        prepared_original = deepcopy(prepared)
        state_original = deepcopy(self.context.state)
        self.saved.reset_mock()
        recovered = self.context.recover_protocol(prepared, [])
        self.assertIsNotNone(recovered)
        expected = deepcopy(prepared.messages)
        next(item for item in expected if item.get("_protocol", {}).get("api") == "openai-responses").pop("_protocol")
        self.assertEqual(recovered.messages, expected)
        self.assertEqual(prepared, prepared_original)
        self.assertEqual(self.messages, original)
        self.assertEqual(self.context.state, state_original)
        self.saved.assert_not_called()
        measured = model_context.measure_request(recovered.profile, recovered.messages, [], 4096)
        self.assertEqual(recovered.budget, measured)
        self.assertTrue(recovered.budget.fits)
        self.assertEqual(recovered.profile["_context_budget"]["protocol_recovery"], 1)
        self.assertEqual(recovered.profile["_usage_context"]["context_budget"]["protocol_recovery"], 1)

    def test_missing_provider_state_has_no_recovery(self):
        prepared = self.context.prepare(self.profile, [{"role": "user", "content": "Continue"}], [], 4096)
        self.saved.reset_mock()
        self.assertIsNone(self.context.recover_protocol(prepared, []))
        self.saved.assert_not_called()

    def test_real_wrapper_retries_once_preserves_tool_pairs_and_remembers_endpoint_compatibility(self):
        requests = []
        original = deepcopy(self.messages)

        def provider(request, **_kwargs):
            payload = json.loads(request.data)
            requests.append(payload)
            if any(item.get("encrypted_content") for item in payload["input"]):
                raise rejected()
            return io.BytesIO(json.dumps({"status": "completed", "output": [
                {"type": "reasoning", "id": "rs_new", "encrypted_content": "opaque-new-state", "summary": []},
                {"type": "message", "content": [{"type": "output_text", "text": "Complete answer"}]},
            ]}).encode())

        with patch.object(workflow, "open_url", side_effect=provider), \
                patch.object(workflow, "_tool_impl") as external_tool, patch.object(runtime.time, "sleep") as sleep:
            first = self.invoke()
            self.assertEqual(first["content"], "Complete answer")
            second_history = [*self.messages, {"role": "assistant", "content": first["content"],
                              "_protocol": first["_protocol"]}, {"role": "user", "content": "Continue again"}]
            second = self.invoke(second_history)
        self.assertEqual(second["content"], "Complete answer")
        self.assertEqual(len(requests), 3)
        for payload in requests[1:]:
            self.assertFalse(any(item.get("encrypted_content") for item in payload["input"]))
            call = next(item for item in payload["input"] if item.get("type") == "function_call")
            result = next(item for item in payload["input"] if item.get("type") == "function_call_output")
            self.assertEqual(call["call_id"], "saved-call")
            self.assertEqual(result["call_id"], "saved-call")
            self.assertEqual(result["output"], original[3]["content"])
            self.assertTrue(any(item.get("content") == "Visible prior answer" for item in payload["input"]))
        self.assertEqual(self.messages, original)
        self.assertEqual(self.context.state["plain_responses"], [model_context.endpoint_identity(self.profile)])
        external_tool.assert_not_called()
        sleep.assert_not_called()

    def test_compatibility_survives_context_restore_and_stays_scoped_to_endpoint_and_model(self):
        prepared = self.context.prepare(self.profile, self.messages, [], 4096)
        self.context.accepted(self.context.recover_protocol(prepared, []))
        restored = self.controller(self.context.state)
        for candidate, remove in [
            (dict(self.profile), True),
            ({**self.profile, "id": "another-profile-same-route"}, True),
            ({**self.profile, "base_url": "https://other.invalid/v1"}, False),
            ({**self.profile, "model": "another-model"}, False),
            ({**self.profile, "api": "anthropic-messages"}, False),
        ]:
            with self.subTest(candidate=candidate):
                view = restored.prepare(candidate, self.messages, [], 4096).messages
                self.assertEqual(any(item.get("_protocol", {}).get("api") == "openai-responses" for item in view), not remove)

    def test_failed_recovery_does_not_enable_compatibility_or_repeat_requests(self):
        with patch.object(workflow, "open_url", side_effect=lambda *_a, **_k: (_ for _ in ()).throw(rejected())) as request:
            with self.assertRaises(runtime.InvalidEncryptedContentError):
                self.invoke()
        self.assertEqual(request.call_count, 2)
        self.assertNotIn("plain_responses", self.context.state)

    def test_unrelated_http400_is_not_recovered(self):
        with patch.object(workflow, "open_url", side_effect=rejected(message="Invalid tool schema")) as request:
            with self.assertRaises(runtime.AIRuntimeError) as caught:
                self.invoke()
        self.assertNotIsInstance(caught.exception, runtime.InvalidEncryptedContentError)
        request.assert_called_once()

    def test_streamed_text_prevents_protocol_recovery(self):
        for callback_name in ("on_delta",):
            pieces = []

            def model(_profile, _messages, **kwargs):
                kwargs[callback_name]("Already visible")
                raise runtime.InvalidEncryptedContentError("Rejected state", status_code=400)

            with self.subTest(callback=callback_name), patch.object(workflow, "chat_raw", side_effect=model) as request, \
                    patch.object(self.context, "recover_protocol", wraps=self.context.recover_protocol) as recover:
                with self.assertRaises(runtime.InvalidEncryptedContentError):
                    self.invoke(**{callback_name: pieces.append})
                request.assert_called_once()
                recover.assert_not_called()
                self.assertEqual(pieces, ["Already visible"])

    def test_reasoning_only_does_not_block_plain_protocol_recovery(self):
        pieces = []
        def model(_profile, _messages, **kwargs):
            if kwargs.get('on_reasoning'):
                kwargs['on_reasoning']('private reasoning')
            raise runtime.InvalidEncryptedContentError('Rejected state', status_code=400)
        with patch.object(workflow, 'chat_raw', side_effect=[runtime.InvalidEncryptedContentError('Rejected state', status_code=400), {'content': 'Recovered'}]) as request:
            result = self.invoke(on_reasoning=pieces.append)
        self.assertEqual(result['content'], 'Recovered')
        self.assertEqual(request.call_count, 2)
        self.assertEqual(pieces, [])

    def test_cancellation_is_checked_before_protocol_recovery(self):
        cancelled = False

        def model(*_args, **_kwargs):
            nonlocal cancelled
            cancelled = True
            raise runtime.InvalidEncryptedContentError("Rejected state", status_code=400)

        with patch.object(workflow, "chat_raw", side_effect=model) as request, \
                patch.object(self.context, "recover_protocol", wraps=self.context.recover_protocol) as recover:
            with self.assertRaises(runtime.CancelledError):
                self.invoke(cancel_check=lambda: cancelled)
            request.assert_called_once()
            recover.assert_not_called()

    def test_overflow_and_protocol_recovery_share_one_attempt(self):
        for errors in ((runtime.InvalidEncryptedContentError("Rejected"), runtime.ContextOverflowError("Too large")),
                       (runtime.ContextOverflowError("Too large"), runtime.InvalidEncryptedContentError("Rejected"))):
            context = self.controller()
            prepared = context.prepare(self.profile, self.messages, [], 4096)
            with self.subTest(errors=[type(error).__name__ for error in errors]), \
                    patch.object(workflow, "chat_raw", side_effect=errors) as request, \
                    patch.object(context, "recover_overflow", return_value=prepared) as overflow, \
                    patch.object(context, "recover_protocol", wraps=context.recover_protocol) as protocol:
                with self.assertRaises(type(errors[1])):
                    self.invoke(context=context)
                self.assertEqual(request.call_count, 2)
                self.assertEqual(overflow.call_count + protocol.call_count, 1)
                self.assertNotIn("plain_responses", context.state)

    def test_summary_sources_do_not_replay_provider_protocol(self):
        self.profile.update(context_window=32768, max_output=4096, tokenizer="utf8_bytes_conservative")
        history = self.messages[:2]
        for index in range(6):
            message = assistant("Historical source " + "x" * 4000, encrypted="cipher-" + str(index))
            history.extend([message, {"role": "tool", "tool_call_id": "saved-call", "content": "source " * 200}])
        history.append({"role": "user", "content": "Continue the review"})
        original = deepcopy(history)
        self.context.prepare(self.profile, history, [], 4096)
        self.context.summarize.assert_called_once()
        summary_messages = self.context.summarize.call_args.args[1]
        self.assertFalse(any("_protocol" in message for message in summary_messages))
        self.assertEqual(history, original)


if __name__ == "__main__":
    unittest.main()
