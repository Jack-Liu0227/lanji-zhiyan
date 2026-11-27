"""Long source text reaches the model in complete, capacity-checked batches."""
from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import model_context
import workflow
import workflow_stages


class WorkflowLongDocumentsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.db = workflow.WorkflowDB(Path(self.temp.name) / 'app.sqlite3')
        self.addCleanup(self.db.close)
        self.project = self.db.create_task('Long documents', base_dir=str(Path(self.temp.name) / 'project'))
        self.profile = {'model': 'local-long-document-fixture', 'base_url': 'https://invalid.local/v1',
                        'context_window': 32768, 'max_output': 4096}
        self.calls = []
        self.counter = patch.object(model_context, '_offline_encoding', return_value=None)
        self.counter.start()
        self.addCleanup(self.counter.stop)
        self.active = patch.object(self.db, 'active_model', return_value=self.profile)
        self.active.start()
        self.addCleanup(self.active.stop)

    def model(self, profile, messages, **kwargs):
        budget = model_context.measure_request(profile, messages, kwargs.get('tools') or [], kwargs['max_tokens'])
        self.assertTrue(budget.fits, budget)
        self.calls.append(deepcopy(messages))
        return {'content': 'A complete source reading note.', 'stream_complete': True}

    def paper(self, pages):
        return {'id': 'paper', 'task_id': self.project, 'filename': 'full-source.pdf',
                'text_chars': sum(len(text) for text in pages),
                'pages': [{'page_no': index, 'text': text} for index, text in enumerate(pages, 1)]}

    def test_long_pdf_page_tail_and_reference_pages_reach_the_actual_model(self):
        pages = ['原文可核对的材料与实验条件。' * 7000 + '\nUNIQUE_PAGE_ONE_TAIL',
                 'Second source page with full evidence. ' * 3000 + '\nUNIQUE_PAGE_TWO_TAIL',
                 'References\n' + 'Reference source detail. ' * 30 + '\nUNIQUE_REFERENCE_TAIL']
        with patch.object(self.db, 'paper', return_value=self.paper(pages)), \
                patch.object(workflow, 'chat_raw', side_effect=self.model):
            result = workflow_stages.stage_read(self.db, self.project, 'paper', per_page=1)
        self.assertGreater(result['chunks'], 1)
        self.assertLess(result['chunks'], 20)
        recovered = {page: [] for page in range(1, 4)}
        pattern = re.compile(r'\[PAGE (\d+)\]\n\[CHARS (\d+):(\d+) OF (\d+)\]\n')
        for messages in self.calls:
            text = messages[-1]['content']
            matches = list(pattern.finditer(text))
            for index, match in enumerate(matches):
                number, start, end, total = map(int, match.groups())
                body_end = matches[index + 1].start() - 2 if index + 1 < len(matches) else len(text)
                content = text[match.end():body_end]
                self.assertEqual(content, pages[number - 1][start:end])
                self.assertEqual(total, len(pages[number - 1]))
                recovered[number].append((start, content))
        for number, source in enumerate(pages, 1):
            self.assertEqual(''.join(text for _, text in sorted(recovered[number])), source)
        self.assertEqual(len(self.db.notes(self.project)), 1)

    def test_larger_actual_window_reduces_read_batches(self):
        paper = self.paper(['Verifiable source paragraph. ' * 6500 + 'FINAL_WORD'])
        with patch.object(self.db, 'paper', return_value=paper), patch.object(workflow, 'chat_raw', side_effect=self.model):
            small = workflow_stages.stage_read(self.db, self.project, 'paper')['chunks']
            self.profile['context_window'] = 262144
            large = workflow_stages.stage_read(self.db, self.project, 'paper')['chunks']
        self.assertGreater(small, large)
        self.assertEqual(large, 1)

    def test_failed_read_batch_does_not_save_a_completed_note(self):
        def fail_second(profile, messages, **kwargs):
            if self.calls:
                raise RuntimeError('source batch unavailable')
            return self.model(profile, messages, **kwargs)
        with patch.object(self.db, 'paper', return_value=self.paper(['Source sentence. ' * 18000])), \
                patch.object(workflow, 'chat_raw', side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, 'source batch unavailable'):
                workflow_stages.stage_read(self.db, self.project, 'paper')
        self.assertEqual(self.db.notes(self.project), [])
        self.assertEqual(self.db.db.execute("SELECT COUNT(*) FROM steps WHERE stage='download' AND status='done'").fetchone()[0], 0)

    def review_model(self, profile, messages, **kwargs):
        self.model(profile, messages, **kwargs)
        tail = 'UNIQUE_MANUSCRIPT_TAIL' in messages[-1]['content']
        response = {'verdict': '大修' if tail else '小修', 'scores': {'证据': 2 if tail else 4},
            'issues': [{'severity': '高', 'quote': 'UNIQUE_MANUSCRIPT_TAIL',
                        'problem': 'Tail statement needs a source.', 'fix': 'Cite the source.'}] if tail else [],
            'missing_evidence': ['UNIQUE_MANUSCRIPT_TAIL'] if tail else []}
        return {'content': json.dumps(response, ensure_ascii=False), 'stream_complete': True}

    def test_complete_manuscript_and_evidence_beyond_the_sixtieth_record_are_reviewed(self):
        draft = 'Full manuscript source paragraph. ' * 7000 + '\nUNIQUE_MANUSCRIPT_TAIL'
        evidence = [{'id': f'evidence-{index}', 'filename': 'source.pdf', 'page_no': index + 1,
                     'field': 'measurement', 'value': f'VALUE_{index}', 'excerpt': f'Complete evidence source {index}'}
                    for index in range(90)]
        with patch.object(self.db, 'evidence', return_value=evidence), \
                patch.object(workflow, 'chat_raw', side_effect=self.review_model):
            result = workflow_stages.stage_review(self.db, self.project, draft)
        self.assertGreater(len(self.calls), 1)
        self.assertLess(len(self.calls), 20)
        self.assertIn('VALUE_89', '\n'.join(messages[-1]['content'] for messages in self.calls))
        ranges = {}
        pattern = re.compile(r'^\[MANUSCRIPT CHARS (\d+):(\d+) OF (\d+)\]\n')
        for messages in self.calls:
            user = messages[-1]['content']
            match = pattern.match(user)
            start, end, total = map(int, match.groups())
            text = user[match.end():user.index('\n\n[REGISTERED EVIDENCE CHARS ')]
            self.assertEqual(text, draft[start:end])
            self.assertEqual(total, len(draft))
            ranges[start] = text
        self.assertEqual(''.join(ranges[start] for start in sorted(ranges)), draft)
        self.assertEqual(result['review']['verdict'], '大修')
        self.assertEqual(result['review']['scores']['证据'], 2)
        self.assertIn('UNIQUE_MANUSCRIPT_TAIL', result['review']['missing_evidence'])
        self.assertIn('Tail statement needs a source.', result['markdown'])
        self.assertEqual(result['review']['evidence_count'], 90)

    def test_missing_evidence_in_one_partition_is_not_declared_absent_everywhere(self):
        draft = 'An explicit claim needing evidence.'
        evidence = [{'id': str(index), 'filename': 'source.pdf', 'page_no': index + 1,
                     'field': 'fact', 'value': 'Complete registered fact. ' * 250}
                    for index in range(30)]
        def model(profile, messages, **kwargs):
            self.model(profile, messages, **kwargs)
            return {'content': json.dumps({'verdict': '小修', 'scores': {}, 'issues': [],
                'missing_evidence': [draft] if len(self.calls) == 1 else []}), 'stream_complete': True}
        with patch.object(self.db, 'evidence', return_value=evidence), patch.object(workflow, 'chat_raw', side_effect=model):
            result = workflow_stages.stage_review(self.db, self.project, draft)
        self.assertGreater(len(self.calls), 1)
        self.assertEqual(result['review']['missing_evidence'], [])
        self.assertIn(draft, result['review']['evidence_followup'])
        self.assertIn(draft, result['markdown'])

    def test_failed_or_truncated_review_never_writes_a_completed_report(self):
        for response in ({'content': '{}', 'stream_complete': True},
                         {'content': '{"issues":[],"missing_evidence":[]}', 'finish_reason': 'length'}):
            with self.subTest(response=response), patch.object(self.db, 'evidence', return_value=[]), \
                    patch.object(workflow, 'chat_raw', return_value=response):
                with self.assertRaises(RuntimeError):
                    workflow_stages.stage_review(self.db, self.project, 'A manuscript requiring review.')
                self.assertEqual(self.db.notes(self.project), [])
                self.assertEqual(self.db.db.execute("SELECT COUNT(*) FROM steps WHERE stage='review' AND status='done'").fetchone()[0], 0)

    def test_invalid_review_structure_never_saves_a_note_or_completed_step(self):
        valid = {'verdict': '\u5c0f\u4fee', 'scores': {'\u8bc1\u636e': 3}, 'issues': [], 'missing_evidence': []}
        invalid = [
            {'issues': [], 'missing_evidence': []},
            {**valid, 'verdict': ''},
            {**valid, 'verdict': 'unknown'},
            {**valid, 'verdict': []},
            {**valid, 'scores': None},
            {**valid, 'scores': []},
            *[{**valid, 'scores': {'\u8bc1\u636e': value}} for value in (-1, 6, True, '3', None)],
            *[{**valid, 'issues': [{**dict.fromkeys(('severity', 'quote', 'problem', 'fix'), 'text'), field: value}]}
              for field, value in (('severity', None), ('quote', []), ('problem', 2), ('fix', False))],
            {**valid, 'issues': [{'severity': '\u9ad8', 'quote': 'claim', 'problem': 'Missing fix'}]},
        ]
        for response in invalid:
            with self.subTest(response=response), patch.object(self.db, 'evidence', return_value=[]), \
                    patch.object(workflow, 'chat_raw', return_value={
                        'content': json.dumps(response), 'stream_complete': True}):
                with self.assertRaisesRegex(RuntimeError, 'invalid review JSON'):
                    workflow_stages.stage_review(self.db, self.project, 'A manuscript requiring review.')
                self.assertEqual(self.db.notes(self.project), [])
                self.assertEqual(self.db.db.execute("SELECT COUNT(*) FROM steps WHERE stage='review' AND status='done'").fetchone()[0], 0)

    def test_valid_review_allows_partial_score_dimensions_and_numeric_boundaries(self):
        review = {'verdict': '\u63a5\u53d7', 'scores': {'\u8bc1\u636e': 0, '\u903b\u8f91': 5.0},
                  'issues': [], 'missing_evidence': []}
        with patch.object(self.db, 'evidence', return_value=[]), patch.object(workflow, 'chat_raw', return_value={
                'content': json.dumps(review), 'stream_complete': True}):
            result = workflow_stages.stage_review(self.db, self.project, 'A supported manuscript.')
        self.assertEqual(result['review']['verdict'], review['verdict'])
        self.assertEqual(result['review']['scores'], review['scores'])
        self.assertEqual(len(self.db.notes(self.project)), 1)


if __name__ == '__main__':
    unittest.main()
