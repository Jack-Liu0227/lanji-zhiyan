"""Full sources reach the budget boundary, including former cutoff tails."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import asset_service
import chat_context
import model_context
import project_artifacts
import project_tasks
import service_settings
import workflow
import workflow_context
from workflow_compaction import WorkflowContext
from tests import test_project_artifacts as artifact_tests


class FullTextBudgetTest(unittest.TestCase):
    def controller(self):
        return WorkflowContext(None, save_checkpoint=lambda **kw: None, summarize=Mock(return_value="Earlier work summarized."))

    def test_full_node_and_tool_sources_reach_a_large_model_with_their_tail(self):
        tail = "LITERAL SOURCE TAIL AFTER FORMER CUTOFF"
        content = "source material " * 5000 + tail
        snapshot = {"goal": "Use the complete source", "task_inputs": {"document": content}}
        node = workflow_context.prompt(snapshot, "run", "node")
        tool = workflow_context.serialize_tool_result({"content": content})
        self.assertEqual(json.loads(node)["task_inputs"]["document"], content)
        self.assertEqual(json.loads(tool)["content"], content)
        messages = [{"role": "system", "content": "CURRENT_NODE_INPUT " + node},
                    {"role": "user", "content": "Check the tail."},
                    {"role": "assistant", "tool_calls": [{"id": "read", "type": "function", "function": {
                        "name": "read_attachment", "arguments": '{"path":"source.md"}'}}]},
                    {"role": "tool", "tool_call_id": "read", "content": tool}]
        original = deepcopy(messages)
        profile = {"context_window": 500000, "max_output": 128000, "tokenizer": "utf8_bytes_conservative"}
        with patch.object(workflow, "chat_raw", return_value={"content": "Checked"}) as model:
            answer = workflow.call_with_fallback(None, [profile], messages,
                max_tokens=None, request_context=self.controller())
        self.assertEqual(answer["content"], "Checked")
        self.assertEqual(model.call_args.kwargs["max_tokens"], 128000)
        sent = model.call_args.args[1]
        self.assertTrue(any(tail in item.get("content", "") for item in sent if item["role"] == "tool"))
        self.assertTrue(any(tail in item.get("content", "") for item in sent if item["role"] == "system"))
        self.assertEqual(messages, original)

    def test_fallback_recomputes_automatic_output_for_each_model(self):
        profiles = [{"context_window": 500000, "max_output": 128000},
                    {"context_window": 32768, "max_output": 4096}]
        import runtime
        with patch.object(workflow, "chat_raw", side_effect=[runtime.ModelUnavailableError("offline"), {"content": "ok"}]) as model:
            workflow.call_with_fallback(None, profiles, [{"role": "user", "content": "continue"}],
                                       max_tokens=None, request_context=self.controller())
        self.assertEqual([call.kwargs["max_tokens"] for call in model.call_args_list], [128000, 4096])

    def test_failed_summary_rebudgets_output_after_adding_fallback_instructions(self):
        profile = {'context_window': 65536, 'tokenizer': 'utf8_bytes_conservative'}
        messages = [{'role': 'system', 'content': 'Required rule. ' * 3650},
                    {'role': 'user', 'content': 'Keep the instructions and continue.'},
                    *[{'role': 'assistant', 'content': 'Historical data. ' * 1000} for _ in range(4)]]
        controller = self.controller()
        controller.summarize.side_effect = RuntimeError('summary unavailable')
        prepared = controller.prepare(profile, messages, [], None)
        self.assertTrue(prepared.budget.fits)
        self.assertLess(prepared.budget.output_limit, 16384)

    def test_automatic_context_keeps_outbound_scrub_headroom(self):
        profile = {'context_window': 65536, 'tokenizer': 'utf8_bytes_conservative'}
        messages = [{'role': 'system', 'content': 'Rule. ' * 3000},
                    {'role': 'user', 'content': 'Continue.'}]
        prepared = self.controller().prepare(profile, messages, [], None)
        self.assertLessEqual(prepared.budget.input_tokens, prepared.budget.available_input_tokens - 200)

    def test_unknown_output_is_estimated_from_configured_window(self):
        self.assertEqual(model_context.automatic_output_limit({"context_window": 65536}), 16384)
        self.assertEqual(model_context.automatic_output_limit({"context_window": 262144}), 65536)
        self.assertTrue(model_context.resolve_spec({"model": "unknown"}).estimated_window)

    def test_automatic_output_yields_space_to_required_instructions(self):
        profile = {'context_window': 65536, 'tokenizer': 'utf8_bytes_conservative'}
        rules = 'Required rule. ' * 3250
        messages = [{'role': 'system', 'content': rules}, {'role': 'user', 'content': 'Continue.'}]
        prepared = self.controller().prepare(profile, messages, [], None)
        self.assertTrue(prepared.budget.fits)
        self.assertLess(prepared.budget.output_limit, 16384)
        self.assertGreaterEqual(prepared.budget.output_limit, 1024)
        self.assertIn(messages[0], prepared.messages)
        self.assertEqual(prepared.budget.window, 65536)

    def test_pressure_on_a_small_model_preserves_raw_source_and_page_alias(self):
        content = "large source " * 15000 + "FINAL PAGE TAIL"
        result = {"content": content, "text": content, "offset": 0, "total": len(content),
                  "end_offset": len(content), "next_offset": None, "content_hash": "v1"}
        messages = [{"role": "user", "content": "Finish using the source."},
                    {"role": "assistant", "tool_calls": [{"id": "read", "function": {
                        "name": "read_attachment", "arguments": '{}'}}]},
                    {"role": "tool", "tool_call_id": "read", "content": json.dumps(result)}]
        prepared = self.controller().prepare({"context_window": 32768, "max_output": 4096}, messages, [], None)
        visible = json.loads(next(item["content"] for item in prepared.messages if item.get("role") == "tool"))
        self.assertEqual(visible["text"], visible["content"])
        self.assertEqual(visible["next_offset"], len(visible["content"]))
        self.assertIn("FINAL PAGE TAIL", messages[-1]["content"])
        self.assertTrue(prepared.budget.fits)

    def test_default_chat_history_does_not_cut_long_messages(self):
        messages = [{"role": "user", "content": "A" * 50000 + "USER TAIL"},
                    {"role": "assistant", "content": "B" * 50000 + "ASSISTANT TAIL"}]
        self.assertEqual(workflow.context_budget(), None)
        self.assertEqual(workflow.recent_dialogue(messages), messages)
        self.assertEqual(chat_context.select(messages, "tail", None), messages)

    def test_attachment_wire_view_sends_the_body_once_and_reloads_mutable_files(self):
        import workflow_progress
        result = {"section": "attachment", "content": "FULL ATTACHMENT", "text": "FULL ATTACHMENT",
                  "offset": 0, "end_offset": 15, "total": 15, "next_offset": None}
        visible = json.loads(workflow_context.serialize_tool_result(result))
        self.assertEqual(visible["content"], "FULL ATTACHMENT")
        self.assertNotIn("text", visible)
        self.assertEqual(result["text"], "FULL ATTACHMENT")
        self.assertEqual(workflow_progress.classify("read_attachment", result), "mutable")

    def test_a_compacted_assistant_cannot_replay_its_unabridged_wire_payload(self):
        text = 'Earlier draft content. ' * 10000 + 'UNABRIDGED WIRE TAIL'
        original = [{'role': 'user', 'content': 'Review the current source.'},
                    {'role': 'assistant', 'content': text, '_protocol': {'api': 'openai-responses',
                        'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]}}]
        visible = workflow_context.bounded_messages(original, budget=3000)
        assistant = next(item for item in visible if item['role'] == 'assistant')
        self.assertNotIn('_protocol', assistant)
        self.assertLess(len(assistant['content']), 3000)
        self.assertEqual(original[-1]['_protocol']['output'][0]['content'][0]['text'], text)

    def test_automatic_budget_leaves_readable_page_space_beyond_required_prefix(self):
        profile = {'context_window': 65536, 'tokenizer': 'utf8_bytes_conservative'}
        body = 'Full source paragraph. ' * 3000
        messages = [{'role': 'system', 'content': 'Required rule. ' * 2950},
                    {'role': 'user', 'content': 'Read this complete source.'},
                    {'role': 'assistant', 'tool_calls': [{'id': 'page', 'function': {
                        'name': 'workflow_context_read', 'arguments': '{}'}}]},
                    {'role': 'tool', 'tool_call_id': 'page', 'content': json.dumps({
                        'content': body, 'offset': 0, 'total': len(body), 'next_offset': None})}]
        prepared = self.controller().prepare(profile, messages, [], None)
        page = json.loads(next(item['content'] for item in prepared.messages if item.get('role') == 'tool'))
        self.assertGreater(len(page['content']), 4000)
        self.assertEqual(page['next_offset'], len(page['content']))
        self.assertTrue(prepared.budget.fits)
        self.assertGreaterEqual(prepared.budget.output_limit, 8192)
        self.assertLess(prepared.budget.output_limit, 16384)

    def test_skill_descriptions_do_not_take_budget_from_the_latest_read_page(self):
        catalog = {'enabled_skills': [{'skill_id': 'skill-' + str(index),
            'description': 'Extensive discovery description. ' * 1000,
            'read_with': {'tool': 'skill_read', 'skill_id': 'skill-' + str(index)}} for index in range(200)]}
        page = 'SOURCE PAGE CONTENT ' * 800
        messages = [{'role': 'system', 'content': workflow_context.SKILL_CATALOG_PREFIX + json.dumps(catalog)},
                    {'role': 'user', 'content': 'Read the supplied page.'},
                    {'role': 'assistant', 'tool_calls': [{'id': 'page', 'function': {
                        'name': 'workflow_context_read', 'arguments': '{}'}}]},
                    {'role': 'tool', 'tool_call_id': 'page', 'content': json.dumps({
                        'content': page, 'total': len(page), 'offset': 0, 'next_offset': None})}]
        visible = workflow_context.bounded_messages(messages, budget=50000)
        delivered = json.loads(next(item['content'] for item in visible if item.get('role') == 'tool'))
        self.assertEqual(delivered['content'], page)
        supplied_catalog = next(item['content'] for item in visible
                                if item.get('content', '').startswith(workflow_context.SKILL_CATALOG_PREFIX))
        entries, _ = json.JSONDecoder().raw_decode(supplied_catalog[len(workflow_context.SKILL_CATALOG_PREFIX):])
        self.assertEqual(len(entries['enabled_skills']), 200)

    def test_summary_capacity_grows_with_the_source_and_leaves_new_turn_headroom(self):
        profile = {"context_window": 1000000, "max_output": 128000}
        budget = model_context.measure_request(profile, [{"role": "assistant", "content": "x" * 2000000}], [], 128000)
        self.assertGreater(budget.summary_tokens, 4096)
        self.assertLessEqual(budget.summary_tokens, 128000)
        self.assertLessEqual(budget.target_tokens, budget.available_input_tokens * .75)


class LargeArtifactTest(unittest.TestCase):
    setUp = artifact_tests.ProjectArtifactTest.setUp
    queue = artifact_tests.ProjectArtifactTest.queue

    def test_workflow_writes_reads_and_submits_a_text_artifact_over_five_mib(self):
        task, run = self.queue(output_contract={"required_files": ["large.md"]})
        project_tasks.claim(self.db, run["id"])
        self.db.project_task_context = project_tasks.CommandContext.agent(self.project, task["id"])
        text = "# Complete large artifact\n" + "full text\n" * 550000 + "LITERAL LARGE FILE TAIL"
        self.assertGreater(len(text.encode("utf-8")), 5 * 1024 * 1024)
        saved = project_artifacts.write(self.db, self.project, {"path": "large.md", "content": text})
        target = Path(self.db.base_dir(self.project)) / "large.md"
        self.assertEqual(target.read_text(encoding="utf-8-sig"), text)
        self.assertEqual(asset_service.sha256_file(target), saved["content_hash"])
        self.assertEqual(project_artifacts.current(self.db, self.project)[0]["content"], text)
        result = project_artifacts.submission(self.db, self.project)
        self.assertTrue(result["submitted"])
        self.assertFalse(result["approved"])

    def test_explicit_chat_history_preference_can_exceed_former_maximum(self):
        service_settings.write(self.db, "security", {"history_chars": 900000})
        self.assertEqual(workflow.context_budget(self.db), 900000)


class OrdinaryChatCatalogTest(unittest.TestCase):
    def test_unbound_chat_only_declares_executable_project_commands_and_keeps_steward_rules(self):
        import tempfile
        import capabilities
        import task_tools
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder, \
                workflow.WorkflowDB(Path(folder) / 'chat.sqlite3') as db:
            project = db.create_task('Bound scope', base_dir=str(Path(folder) / 'project'))
            db.add_model('Unknown route fixture', 'http://127.0.0.1:1/v1', 'unknown-route', '', activate=True)
            chat = db.create_chat('Unbound steward')
            steward = Path(folder) / 'research-steward' / 'SKILL.md'
            steward.parent.mkdir()
            rule = 'Preserve the complete user steward instruction. ' * 60 + 'USER_STEWARD_RULE_TAIL'
            steward.write_text(rule, encoding='utf-8')
            skills = [{'id': 'research-steward', 'description': 'Configure research projects.',
                       'path': str(steward), 'dir': str(steward.parent), 'scope': 'user'}]
            skills += [{'id': 'specialist-' + str(index), 'description': 'Full specialist description. ' * 200}
                       for index in range(300)]
            requests = []

            def model(candidate, messages, **kwargs):
                budget = model_context.measure_request(candidate, messages, kwargs.get('tools') or [],
                                                       kwargs['max_tokens'])
                self.assertTrue(budget.fits)
                requests.append((deepcopy(messages), {spec['function']['name'] for spec in kwargs['tools']}))
                return {'content': 'The current scope is available.', 'stream_complete': True}

            with patch.object(capabilities, 'list_skills', return_value=skills), \
                    patch.object(capabilities, 'enabled', return_value=True), \
                    patch.object(model_context, '_offline_encoding', return_value=None), \
                    patch.object(workflow, 'chat_raw', side_effect=model):
                unbound = workflow.run_agent(db, chat, 'hi', publish=False, max_steps=1)
                db.bind_chat(chat, project)
                bound = workflow.run_agent(db, chat, 'Check this project.', publish=False, max_steps=1)

            self.assertEqual(unbound['reply'], 'The current scope is available.')
            self.assertEqual(bound['reply'], 'The current scope is available.')
            systems = '\n'.join(message['content'] for message in requests[0][0] if message['role'] == 'system')
            self.assertIn(rule, systems)
            self.assertIn('graph_workflow_run', requests[0][1])
            self.assertIn('project_create_from_idea', requests[0][1])
            self.assertIn('skill_read', requests[0][1])
            self.assertFalse(requests[0][1].intersection(task_tools.NAMES | {'workflow_context_read', 'project_artifact_write'}))
            self.assertTrue(task_tools.NAMES.issubset(requests[1][1]))
            self.assertIn('project_artifact_write', requests[1][1])
            catalog = next(message['content'] for message in requests[0][0]
                           if message.get('content', '').startswith(workflow_context.SKILL_CATALOG_PREFIX))
            entries, _ = json.JSONDecoder().raw_decode(catalog[len(workflow_context.SKILL_CATALOG_PREFIX):])
            self.assertEqual({item['skill_id'] for item in entries['enabled_skills']}, {item['id'] for item in skills})

    def test_short_ordinary_chat_compacts_three_hundred_skill_descriptions_once(self):
        import tempfile
        import capabilities
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder, \
                workflow.WorkflowDB(Path(folder) / 'chat.sqlite3') as db:
            project = db.create_task('Ordinary catalog', base_dir=str(Path(folder) / 'project'))
            profile = {'model': 'ordinary-catalog-fixture', 'base_url': 'https://invalid.local/v1',
                       'context_window': 65536, 'max_output': 4096}
            skills = [{'id': 'skill-' + str(index), 'description': 'Complete specialist description. ' * 200}
                      for index in range(300)]
            views = []

            def model(candidate, messages, **kwargs):
                self.assertTrue(model_context.measure_request(candidate, messages, kwargs.get('tools') or [],
                                                             kwargs['max_tokens']).fits)
                views.append(deepcopy(messages))
                return {'content': 'A short ordinary reply.', 'stream_complete': True}

            with patch.object(capabilities, 'list_skills', return_value=skills), \
                    patch.object(capabilities, 'enabled', return_value=True), \
                    patch.object(workflow, 'chat_raw', side_effect=model), \
                    patch.object(model_context, '_offline_encoding', return_value=None):
                result = workflow.run_agent(db, project, 'Give a short reply.', profile=profile,
                                            publish=False, max_steps=1)
            self.assertEqual(result['reply'], 'A short ordinary reply.')
            self.assertEqual(len(views), 1)
            catalogs = [message['content'] for message in views[0]
                        if str(message.get('content', '')).startswith(workflow_context.SKILL_CATALOG_PREFIX)]
            self.assertEqual(len(catalogs), 1)
            catalog, _ = json.JSONDecoder().raw_decode(catalogs[0][len(workflow_context.SKILL_CATALOG_PREFIX):])
            self.assertEqual({item['skill_id'] for item in catalog['enabled_skills']},
                             {item['id'] for item in skills})
            self.assertTrue(catalog['descriptions_excerpted'])


if __name__ == "__main__":
    unittest.main()
