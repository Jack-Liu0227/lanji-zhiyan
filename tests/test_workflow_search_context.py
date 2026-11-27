"""Search model views keep complete current matches without replaying the project catalogue."""
from copy import deepcopy
import json
import sqlite3
import unittest
from types import SimpleNamespace

import model_context
import workflow_context
from workflow_compaction import WorkflowContext


def row(index, *, abstract='abstract text ' * 200):
    return {'id': 'result-' + str(index), 'task_id': 'project', 'source': 'crossref',
            'title': 'Complete bibliographic title ' + str(index), 'authors': 'Jane Author; John Author',
            'year': '2024', 'doi': '10.test/source-' + str(index),
            'url': 'https://doi.org/10.test/source-' + str(index),
            'pdf_url': 'https://example.org/full-source-' + str(index) + '.pdf',
            'abstract': abstract}


class WorkflowSearchContextTest(unittest.TestCase):
    def receipt(self):
        catalog = [row(index) for index in range(540)]
        matches = [catalog[4], catalog[13], catalog[26]]
        return {'query': 'Find the precise citation', 'found': 3, 'raw': 3, 'filtered': 0,
                'limit': 100, 'sort_by': 'composite', 'saved': [matches[0]],
                'matches': matches, 'enriched': 0, 'results': catalog}

    def test_current_matches_keep_bibliographic_metadata_and_raw_catalogue(self):
        receipt = self.receipt()
        original = deepcopy(receipt)
        visible = workflow_context._tool_value(receipt, 1)
        self.assertEqual(receipt, original)
        self.assertEqual(workflow_context._tool_value(receipt, None), original)
        self.assertEqual(visible['query'], receipt['query'])
        self.assertEqual(visible['results_scope'], 'current_query_matches')
        self.assertEqual([item['id'] for item in visible['results']], [item['id'] for item in receipt['matches']])
        for source, result in zip(receipt['matches'], visible['results']):
            for key in ('id', 'task_id', 'source', 'title', 'authors', 'year', 'doi', 'url', 'pdf_url'):
                self.assertEqual(result[key], source[key])
            self.assertLess(len(result['abstract']), len(source['abstract']))
        archive = visible['archived_project_results']
        self.assertEqual(archive['count'], 540)
        self.assertEqual(archive['read_with'], {'tool': 'workflow_context_read', 'section': 'agent'})
        self.assertEqual(archive['location']['query'], receipt['query'])

    def test_two_search_receipts_fit_a_workflow_window_without_losing_pairing(self):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.execute('CREATE TABLE service_settings(service TEXT, owner TEXT, data TEXT)')
        profile = {'model': 'search-budget-fixture', 'context_window': 65536,
                   'tokenizer': 'utf8_bytes_conservative'}
        messages = [{'role': 'system', 'content': 'Preserve source identity and write the citation check.'},
                    {'role': 'user', 'content': 'Check the eight citations and retain unresolved metadata.'},
                    {'role': 'assistant', 'content': '', 'tool_calls': [
                        {'id': 'search-one', 'function': {'name': 'paper_search', 'arguments': '{"query":"first"}'}},
                        {'id': 'search-two', 'function': {'name': 'paper_search', 'arguments': '{"query":"second"}'}}]},
                    {'role': 'tool', 'tool_call_id': 'search-one', 'name': 'paper_search',
                     'content': json.dumps(self.receipt())},
                    {'role': 'tool', 'tool_call_id': 'search-two', 'name': 'paper_search',
                     'content': json.dumps(self.receipt())}]
        original = deepcopy(messages)
        context = WorkflowContext(SimpleNamespace(db=connection), save_checkpoint=lambda **values: None,
                                  summarize=lambda *args: 'Earlier messages summarized.', run_id='flow', node_id='E')
        prepared = context.prepare(profile, messages, [], None)
        self.assertTrue(prepared.budget.fits)
        self.assertEqual(messages, original)
        calls = {call['id'] for message in prepared.messages for call in message.get('tool_calls', [])}
        outputs = {message['tool_call_id'] for message in prepared.messages if message['role'] == 'tool'}
        self.assertEqual(calls, {'search-one', 'search-two'})
        self.assertEqual(calls, outputs)
        for message in prepared.messages:
            if message['role'] != 'tool':
                continue
            result = json.loads(message['content'])
            self.assertEqual(result['results_scope'], 'current_query_matches')
            self.assertEqual(len(result['results']), 3)
            self.assertEqual(result['results'][0]['title'], 'Complete bibliographic title 4')
            self.assertEqual(result['results'][0]['year'], '2024')


if __name__ == '__main__':
    unittest.main()
