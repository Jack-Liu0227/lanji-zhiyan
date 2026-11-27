import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "workspace-service"))
import workflow_progress as progress


def page(offset=0, content="abcdefgh", total=16, **fields):
    return {"run_id": "run", "node_id": "source", "section": "artifact", "artifact": "evidence.csv",
            "delivery_id": "delivery-1", "content_hash": "hash-1", "offset": offset,
            "end_offset": offset + len(content), "content": content, "total": total,
            "next_offset": offset + len(content) if offset + len(content) < total else None, **fields}


def messages(result, args=None, call_id="read-1"):
    args = {"section": "artifact", "artifact": "evidence.csv", "offset": 0, "limit": 100, **(args or {})}
    return [{"role": "assistant", "tool_calls": [{"id": call_id, "function": {
                "name": "workflow_context_read", "arguments": json.dumps(args)}}]},
            {"role": "tool", "tool_call_id": call_id, "name": "workflow_context_read",
             "content": json.dumps(result)}]


class TestWorkflowProgress(unittest.TestCase):
    def test_successful_lookup_facts_are_keyed_by_exact_arguments(self):
        first_args = {"query": "interface engineering", "sources": ["crossref"]}
        first = {"query": first_args["query"], "found": 1, "matches": [
            {"id": "result-1", "task_id": "project", "source": "crossref",
             "title": "Interface engineering", "authors": "A", "year": 2024,
             "doi": "10.1000/one", "url": "https://doi.org/10.1000/one"}]}
        state = progress.observe_result(None, "paper_search", first_args, first)
        other_args = {"query": "other interface engineering", "sources": ["crossref"]}
        state = progress.observe_result(state, "paper_search", other_args,
                                       {**first, "query": other_args["query"]})
        failed = progress.observe_result(state, "paper_search", {"query": "different"}, {"error": "offline"})
        changed = progress.observe_result(failed, "paper_search", first_args,
                                           {**first, "matches": [{**first["matches"][0], "doi": "10.1000/two"}]})
        view = json.loads(progress.prompt(changed))
        self.assertEqual(len(view["completed_lookups"]), 2)
        self.assertEqual({item["args"].get("query") for item in view["completed_lookups"]},
                         {"interface engineering", "other interface engineering"})
        latest_id = view["completed_lookups"][-1]["lookup_id"]
        latest_refs = view["completed_lookups"][-1]["record_refs"]
        latest_records = [view["returned_bibliography"][ref] for ref in latest_refs
                          if latest_id in view["returned_bibliography"][ref]["lookup_refs"]]
        self.assertEqual([item["doi"] for item in latest_records], ["10.1000/two"])
        self.assertNotIn("different", json.dumps(view))
        self.assertEqual(view["accepted_evidence"], [])

    def test_retrieved_evidence_facts_are_not_accepted_evidence(self):
        result = {"evidence": [{"id": "ev-1", "paper_id": "paper-1", "filename": "p.pdf",
                                 "page_no": 3, "field": "observation", "value": "direct"}]}
        state = progress.observe_result(None, "paper_evidence_get", {"ids": ["ev-1"], "project": "project"}, result)
        view = json.loads(progress.prompt(state))
        self.assertEqual(view["retrieved_evidence"], [{"id": "ev-1", "paper_id": "paper-1", "filename": "p.pdf",
                                                         "page_no": 3, "field": "observation", "value": "direct"}])
        self.assertEqual(view["accepted_evidence"], [])

    def test_lookup_prompt_deduplicates_identical_bibliography_and_keeps_conflicts(self):
        record = {"id": "r1", "task_id": "project", "source": "crossref",
                  "title": "Very detailed source title " * 20, "authors": "Author " * 20,
                  "year": 2024, "doi": "10.1000/one", "url": "https://doi.org/10.1000/one"}
        state = None
        args = [{"query": "source title", "sources": ["crossref"]},
                {"query": '"source title"', "sources": ["crossref"]}]
        for item in args:
            state = progress.observe_result(state, "paper_search", item,
                                           {"query": item["query"], "found": 1, "matches": [record]})
        view = json.loads(progress.prompt(state))
        self.assertEqual(len(view["returned_bibliography"]), 1)
        self.assertEqual(len(view["completed_lookups"]), 2)
        refs = {entry["record_refs"][0] for entry in view["completed_lookups"]}
        self.assertEqual(len(refs), 1)
        shared = view["returned_bibliography"][next(iter(refs))]
        self.assertEqual(len(shared["lookup_refs"]), 2)
        # Compare the same lookup section before and after metadata sharing.
        duplicated = [{**entry, "records": [record]} for entry in view["completed_lookups"]]
        compact = {"completed_lookups": view["completed_lookups"],
                   "returned_bibliography": view["returned_bibliography"]}
        self.assertLess(len(json.dumps(compact)), len(json.dumps(duplicated)))
        state = progress.observe_result(state, "paper_search", {"query": "source title", "sources": ["openalex"]},
                                       {"query": "source title", "found": 1,
                                        "matches": [{**record, "source": "openalex", "doi": "10.1000/two"}]})
        conflicting = json.loads(progress.prompt(state))
        self.assertEqual({item["doi"] for item in conflicting["returned_bibliography"].values()},
                         {"10.1000/one", "10.1000/two"})

    def test_abstract_lookup_keeps_bibliography_without_abstract_body(self):
        state = progress.observe_result(None, "paper_abstracts", {"result_ids": ["r1"]},
            {"papers": [{"result_id": "r1", "paper_id": "p1", "title": "A paper", "year": 2024,
                          "doi": "10.1000/a", "abstract": "private body"}], "next_offset": None})
        view = json.loads(progress.prompt(state))
        ref = view["completed_lookups"][0]["record_refs"][0]
        self.assertEqual(view["returned_bibliography"][ref]["doi"], "10.1000/a")
        self.assertNotIn("private body", json.dumps(view))

    def test_complete_artifact_coverage_adds_a_completion_hint_without_approval(self):
        result = page(content="complete", total=8, end_offset=8)
        state = progress.observe(None, messages(result, {"limit": 100}))
        state = progress.observe_result(state, "paper_search", {"query": "known"},
                                        {"query": "known", "matches": [{"id": "r1", "task_id": "p",
                                                                            "title": "Known", "doi": "10.1/known"}]})
        view = json.loads(progress.prompt(state))
        self.assertIn("completion_instruction", view)
        self.assertIn("Do not repeat already-resolved", view["completion_instruction"])
        self.assertIn("does not verify evidence", view["completion_instruction"])
    def test_only_visible_text_advances_cursor(self):
        raw = page(content="a" * 8000, total=16000)
        visible = {**raw, "content": "a" * 6000, "end_offset": 6000, "next_offset": 6000, "truncated": True}
        state = progress.observe(None, messages(visible, {"limit": 8000}))
        self.assertEqual(json.loads(progress.prompt(state))["reads"][0]["visible_ranges"], [[0, 6000]])
        self.assertEqual(json.loads(progress.prompt(state))["reads"][0]["next_unread_offset"], 6000)
        self.assertIsNone(progress.repeated_read(state, {"section": "artifact", "artifact": "evidence.csv", "limit": 8000}, raw))

    def test_complete_repeat_keeps_metadata_and_omits_body(self):
        result = page()
        args = {"section": "artifact", "artifact": "evidence.csv", "offset": 0, "limit": 100}
        state = progress.observe(None, messages(result, args))
        repeated = progress.repeated_read(state, args, result)
        self.assertEqual(repeated["content"], "")
        self.assertTrue(repeated["repeat"])
        self.assertEqual(repeated["total"], 16)
        self.assertEqual(repeated["offset"], 0)
        self.assertEqual(repeated["end_offset"], 8)
        self.assertEqual(repeated["content_hash"], "hash-1")
        self.assertEqual(repeated["delivery_id"], "delivery-1")
        self.assertEqual(repeated["read_hint"]["offset"], 8)
        self.assertIsNone(progress.repeated_read(state, {**args, "reread": True}, result))
        self.assertEqual(result["content"], "abcdefgh")

    def test_same_parameters_are_required(self):
        state = progress.observe(None, messages(page()))
        self.assertIsNone(progress.repeated_read(state, {"section": "artifact", "limit": 200}, page()))

    def test_ranges_merge_and_resume_from_first_gap(self):
        state = progress.observe(None, messages(page(offset=8), {"offset": 8}, "second"))
        self.assertEqual(json.loads(progress.prompt(state))["reads"][0]["next_unread_offset"], 0)
        state = progress.observe(state, messages(page(), call_id="first"))
        entry = json.loads(progress.prompt(state))["reads"][0]
        self.assertEqual(entry["visible_ranges"], [[0, 16]])
        self.assertIsNone(entry["next_unread_offset"])
        self.assertIsNone(progress.repeated_read(state, {"section": "artifact", "artifact": "evidence.csv", "limit": 100}, page())["read_hint"])

    def test_observation_is_idempotent_and_does_not_mutate_input(self):
        state = progress.observe(None, messages(page()))
        before = copy.deepcopy(state)
        result = progress.observe(state, messages(page()))
        self.assertEqual(state, before)
        self.assertEqual(result, before)

    def test_new_version_invalidates_old_coverage(self):
        state = progress.observe(None, messages(page()))
        new_page = page(offset=8, content_hash="hash-2", delivery_id="delivery-2")
        state = progress.observe(state, messages(new_page, {"offset": 8}, "changed"))
        entry = json.loads(progress.prompt(state))["reads"][0]
        self.assertEqual(entry["visible_ranges"], [[8, 16]])
        self.assertEqual(entry["content_hash"], "hash-2")
        self.assertIsNone(progress.repeated_read(state, {"section": "artifact", "limit": 100}, page()))
        self.assertEqual(progress.observe(state, messages(page())), state)

    def test_reclipped_old_history_cannot_restore_a_retired_version(self):
        state = progress.observe(None, messages(page()))
        current = messages(page(offset=8, content_hash="hash-2", delivery_id="delivery-2"),
                           {"offset": 8}, "changed")
        state = progress.observe(state, current)
        replay = messages(page(content="abc", end_offset=3, truncated=True)) + current
        self.assertEqual(progress.observe(state, replay), state)

    def test_legacy_offset_comes_from_paired_call(self):
        result = page(offset=8)
        del result["offset"]
        del result["end_offset"]
        state = progress.observe(None, messages(result, {"offset": 8}))
        self.assertEqual(json.loads(progress.prompt(state))["reads"][0]["visible_ranges"], [[8, 16]])

    def test_invalid_json_and_unversioned_results_do_not_prove_coverage(self):
        broken = messages(page())
        broken[1]["content"] = broken[1]["content"][:35]
        self.assertEqual(progress.observe(None, broken)["reads"], {})
        self.assertEqual(progress.observe(None, messages(page(content_hash="")))["reads"], {})

    def test_current_artifacts_are_not_elided(self):
        current = page(delivery_id="")
        state = progress.observe(None, messages(current))
        self.assertIsNone(progress.repeated_read(state, {"section": "artifact", "limit": 100}, current))
        self.assertEqual(progress.classify("workflow_context_read", {"section": "artifacts"}), "mutable")
        self.assertEqual(progress.classify("workflow_context_read", current), "mutable")
        self.assertEqual(progress.classify("workflow_context_read", page()), "immutable")
        self.assertEqual(progress.classify("project_artifact_write", {}), "side_effect")
        self.assertEqual(progress.classify("unknown_tool", {}), "side_effect")

    def test_saved_writes_survive_dropped_messages_but_failures_are_not_receipts(self):
        receipt = {"path": "draft.md", "content_hash": "hash-1", "asset_id": "asset-1"}
        state = progress.observe_result(None, "project_artifact_write", {}, receipt)
        state = progress.observe(state, [])
        self.assertEqual(json.loads(progress.prompt(state))["saved_writes"], [receipt])
        self.assertEqual(progress.observe_result(state, "project_artifact_write", {}, receipt), state)
        self.assertEqual(progress.observe_result(state, "project_artifact_write", {}, {**receipt, "error": "conflict"}), state)
        self.assertEqual(progress.observe_result(state, "project_artifact_write", {}, {**receipt, "ok": False}), state)
        self.assertEqual(progress.observe_result(state, "project_artifact_write", {}, {"path": "missing.md"}), state)

    def test_prompt_shows_latest_actual_write_without_claiming_acceptance(self):
        state = progress.observe_result(None, "project_artifact_write", {},
            {"path": "draft.md", "content_hash": "old", "asset_id": "a1"})
        state = progress.observe_result(state, "project_artifact_write", {},
            {"path": "draft.md", "content_hash": "new", "asset_id": "a2"})
        result = json.loads(progress.prompt(state))
        self.assertEqual(result["saved_writes"], [{"path": "draft.md", "content_hash": "new", "asset_id": "a2"}])
        self.assertIn("not evidence verification", result["meaning"])
        self.assertEqual(len(state["writes"]), 2)

    def test_old_model_write_history_cannot_replace_execution_receipts(self):
        state = progress.observe_result(None, "project_artifact_write", {},
            {"path": "draft.md", "content_hash": "new", "asset_id": "a2"})
        history = [{"role": "tool", "name": "project_artifact_write", "content": json.dumps(
            {"path": "draft.md", "content_hash": "old", "asset_id": "a1"})}]
        self.assertEqual(progress.observe(state, history), state)

    def test_complete_coverage_survives_a_real_reread_page_being_excerpted_for_the_model(self):
        import model_context
        import workflow_context
        original = page(content='Full source for verification. ' * 4000)
        original['total'] = len(original['content'])
        original['end_offset'] = original['total']
        original['next_offset'] = None
        state = progress.observe(None, messages(original, {'limit': original['total']}))
        before_state, before_result = copy.deepcopy(state), copy.deepcopy(original)
        args = {'section': 'artifact', 'artifact': 'evidence.csv', 'offset': 0,
                'limit': original['total'] + 100, 'reread': True}
        annotated = progress.annotate_read(state, args, original)
        self.assertEqual(annotated['content'], original['content'])
        self.assertNotIn('repeat', annotated)
        self.assertIsNone(progress.repeated_read(state, args, original))
        profile = {'model': 'reread-fixture', 'context_window': 32768, 'max_output': 4096,
                   'tokenizer': 'utf8_bytes_conservative'}
        request = [{'role': 'user', 'content': 'Recheck the evidence page.'}, *messages(annotated, args)]
        view = workflow_context.bounded_messages(request, budget=5000, run_id='run', node_id='current',
            measure=lambda candidate: model_context.request_tokens(profile, candidate, []))
        visible = json.loads(next(message['content'] for message in view if message.get('role') == 'tool'))
        self.assertTrue(model_context.measure_request(profile, view, [], 4096).fits)
        self.assertLess(len(visible['content']), len(original['content']))
        self.assertTrue(visible['content'])
        self.assertEqual(visible['next_offset'], len(visible['content']))
        self.assertTrue(visible['locally_excerpted'])
        self.assertEqual({key: item for key, item in visible['read_progress'].items() if key != 'continuation'}, {
            'visible_ranges': [[0, original['total']]], 'next_unread_offset': None, 'coverage_complete': True,
            'meaning': 'Coverage of visible text only; not evidence verification or delivery approval.'})
        self.assertIn('do not restart', visible['read_progress']['continuation'])
        observed = next(iter(progress.observe(state, view)['reads'].values()))
        self.assertEqual(observed['coverage'], [[0, original['total']]])
        self.assertEqual(observed['identity'], next(iter(state['reads'].values()))['identity'])
        self.assertEqual(state, before_state)
        self.assertEqual(original, before_result)

    def test_different_limit_recheck_retains_body_and_prior_full_coverage(self):
        source = page(content='abcdefgh' * 100, total=800)
        state = progress.observe(None, messages(source, {'limit': 800}))
        recheck = page(offset=20, content=source['content'][20:120], total=800)
        args = {'section': 'artifact', 'artifact': 'evidence.csv', 'offset': 20, 'limit': 100}
        self.assertIsNone(progress.repeated_read(state, args, recheck))
        annotated = progress.annotate_read(state, args, recheck)
        self.assertEqual(annotated['content'], source['content'][20:120])
        self.assertEqual(annotated['next_offset'], 120)
        self.assertIsNone(annotated['read_progress']['next_unread_offset'])
        self.assertTrue(annotated['read_progress']['coverage_complete'])
        self.assertNotIn('repeat', annotated)

    def test_unread_large_page_still_counts_only_the_visible_excerpt(self):
        import workflow_context
        result = page(content='a' * 16000, total=16000)
        annotated = progress.annotate_read(None, {'offset': 0, 'limit': 16000}, result)
        self.assertEqual(annotated['read_progress']['visible_ranges'], [])
        self.assertEqual(annotated['read_progress']['next_unread_offset'], 0)
        self.assertFalse(annotated['read_progress']['coverage_complete'])
        visible = workflow_context._tool_value(annotated, 6000)
        plain = workflow_context._tool_value(result, 6000)
        self.assertEqual({key: value for key, value in visible.items() if key != 'read_progress'}, plain)
        state = progress.observe(None, messages(visible, {'limit': 16000}))
        read = json.loads(progress.prompt(state))['reads'][0]
        self.assertEqual(read['visible_ranges'], [[0, 6000]])
        self.assertEqual(read['next_unread_offset'], 6000)

    def test_read_annotation_never_reuses_coverage_for_a_changed_hash_delivery_or_total(self):
        result = page(content='abcdefgh', total=8)
        state = progress.observe(None, messages(result))
        for changed in ({'content_hash': 'hash-2'}, {'delivery_id': 'delivery-2'}, {'total': 9},
                        {'run_id': 'another-run'}, {'node_id': 'another-node'}):
            with self.subTest(changed=changed):
                annotated = progress.annotate_read(state, {'reread': True}, {**result, **changed})
                self.assertEqual(annotated['read_progress']['visible_ranges'], [])
                self.assertEqual(annotated['read_progress']['next_unread_offset'], 0)
                self.assertFalse(annotated['read_progress']['coverage_complete'])

    def test_error_or_unversioned_page_does_not_claim_read_progress(self):
        result = page(content='abcdefgh', total=8)
        state = progress.observe(None, messages(result))
        for invalid in ({**result, 'error': 'version conflict'}, {**result, 'content_hash': ''},
                        {**result, 'run_id': ''}, {'content': 'unversioned text'}):
            with self.subTest(invalid=invalid):
                self.assertEqual(progress.annotate_read(state, {}, invalid), invalid)

    def test_read_progress_is_not_pruned_with_nested_tool_metadata(self):
        import workflow_context
        result = page(content='abcdefgh', total=8)
        state = progress.observe(None, messages(result))
        annotated = progress.annotate_read(state, {}, result)
        serialized = workflow_context.serialize_tool_result({'page': annotated}, budget=1000)
        visible = json.loads(serialized)['page']
        self.assertEqual(visible['read_progress'], annotated['read_progress'])
        generic = json.loads(workflow_context.serialize_tool_result({
            'read_progress': annotated['read_progress'], 'note': 'ordinary description ' * 1000}, budget=600))
        self.assertEqual(generic['read_progress'], annotated['read_progress'])


if __name__ == "__main__":
    unittest.main()
