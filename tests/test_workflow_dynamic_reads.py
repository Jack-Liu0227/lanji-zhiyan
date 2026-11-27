"""Explicit read sizes remain unlimited and every page has a recoverable cursor."""
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import workflow
import workflow_context
import workflow_documents
import workflow_execution
import workflow_progress


class WorkflowDynamicReadsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = workflow.WorkflowDB(self.root / 'app.sqlite3')
        self.addCleanup(self.db.close)
        self.project = self.db.create_task('Dynamic reads', base_dir=str(self.root / 'project'))
        self.text = '可核对的来源内容。' * 18000 + '\nUNIQUE_FINAL_SOURCE_LINE'
        self.attachment = self.root / 'source.md'
        self.attachment.write_text(self.text, encoding='utf-8')

    def attachment_read(self, **kwargs):
        with patch.object(workflow_documents.paths, 'resolve_allowed', return_value=self.attachment) as allowed:
            result = workflow_documents.read_attachment(str(self.attachment), **kwargs)
        allowed.assert_called_once_with(str(self.attachment), must_be_file=True, what='附件')
        return result

    def context_read(self, **kwargs):
        snapshot = {'source': self.text}
        run = {'id': 'flow', 'project_id': self.project, 'current_node': 'node', 'settings': {},
               'layout': {'nodes': [{'id': 'node'}]}, 'nodes': [{'id': 'node', 'checkpoint': {'input': snapshot}}]}
        with patch.object(workflow_execution, 'detail', return_value=run):
            return workflow_context.read(self.db, self.project, {'run_id': 'flow', 'node_id': 'node',
                                                                 'section': 'input', **kwargs})

    def test_attachment_explicit_large_limit_has_no_old_48000_ceiling(self):
        result = self.attachment_read(limit=len(self.text) + 1)
        self.assertEqual(result['content'], self.text)
        self.assertEqual(result['text'], self.text)
        self.assertEqual(result['total'], len(self.text))
        self.assertEqual(result['end_offset'], len(self.text))
        self.assertIsNone(result['next_offset'])
        self.assertFalse(result['truncated'])
        self.assertEqual(result['content_hash'], sha256(self.attachment.read_bytes()).hexdigest())

    def test_attachment_legacy_chars_can_request_the_entire_source(self):
        self.assertEqual(self.attachment_read(chars=len(self.text))['text'], self.text)

    def test_attachment_pages_reconstruct_the_source_and_keep_one_version(self):
        pieces, hashes, offset = [], set(), 0
        while offset is not None:
            result = self.attachment_read(offset=offset, limit=53001)
            self.assertEqual(result['offset'], sum(map(len, pieces)))
            self.assertEqual(result['end_offset'], result['offset'] + len(result['content']))
            pieces.append(result['content'])
            hashes.add(result['content_hash'])
            offset = result['next_offset']
        self.assertEqual(''.join(pieces), self.text)
        self.assertEqual(len(hashes), 1)

    def test_attachment_default_is_only_a_page_size_and_small_pages_are_exact(self):
        first = self.attachment_read()
        self.assertEqual(first['content'], self.text[:20000])
        self.assertEqual(first['next_offset'], 20000)
        self.assertEqual(self.attachment_read(offset=2, limit=3)['content'], self.text[2:5])
        end = self.attachment_read(offset=len(self.text) + 500)
        self.assertEqual(end['offset'], len(self.text))
        self.assertEqual(end['content'], '')
        self.assertIsNone(end['next_offset'])

    def test_pdf_attachment_uses_the_same_complete_paging_contract(self):
        with patch.object(workflow_documents.paths, 'resolve_allowed', return_value=self.attachment.with_suffix('.pdf')), \
                patch.object(workflow_documents.pdfio, 'text', return_value=self.text), \
                patch.object(Path, 'read_bytes', return_value=b'PDF original bytes'):
            result = workflow_documents.read_attachment('source.pdf', offset=120000, limit=100000)
        self.assertEqual(result['text'], self.text[120000:220000])
        self.assertEqual(result['content_hash'], sha256(b'PDF original bytes').hexdigest())
        self.assertIn('UNIQUE_FINAL_SOURCE_LINE', result['content'])

    def test_context_explicit_large_page_retains_tail_beyond_old_16000_ceiling(self):
        expected = json.dumps({'source': self.text}, ensure_ascii=False)
        result = self.context_read(limit=len(expected) + 1)
        self.assertEqual(result['content'], expected)
        self.assertEqual(result['total'], len(expected))
        self.assertEqual(result['end_offset'], len(expected))
        self.assertEqual(result['content_hash'], sha256(expected.encode('utf-8')).hexdigest())
        self.assertIsNone(result['next_offset'])

    def test_context_pages_reconstruct_source_and_actual_visibility_is_trackable(self):
        pieces, offset, state = [], 0, None
        while offset is not None:
            args = {'section': 'input', 'offset': offset, 'limit': 53001}
            result = self.context_read(**args)
            call_id = 'page-' + str(offset)
            messages = [{'role': 'assistant', 'tool_calls': [{'id': call_id, 'function': {
                'name': 'workflow_context_read', 'arguments': json.dumps(args)}}]},
                {'role': 'tool', 'tool_call_id': call_id, 'name': 'workflow_context_read',
                 'content': json.dumps(result, ensure_ascii=False)}]
            state = workflow_progress.observe(state, messages)
            pieces.append(result['content'])
            offset = result['next_offset']
        expected = json.dumps({'source': self.text}, ensure_ascii=False)
        self.assertEqual(''.join(pieces), expected)
        progress = json.loads(workflow_progress.prompt(state))['reads'][0]
        self.assertEqual(progress['visible_ranges'], [[0, len(expected)]])
        self.assertIsNone(progress['next_unread_offset'])

    def test_context_default_and_small_pages_keep_exact_offsets(self):
        expected = json.dumps({'source': self.text}, ensure_ascii=False)
        self.assertEqual(self.context_read()['content'], expected[:8000])
        small = self.context_read(offset=12, limit=3)
        self.assertEqual(small['content'], expected[12:15])
        self.assertEqual(small['end_offset'], 15)
        self.assertEqual(small['next_offset'], 15)
        end = self.context_read(offset=len(expected) + 10)
        self.assertEqual(end['content'], '')
        self.assertEqual(end['offset'], len(expected))
        self.assertIsNone(end['next_offset'])

    def test_nonpositive_page_sizes_are_rejected(self):
        for size in (0, -1):
            with self.subTest(size=size):
                with self.assertRaises(ValueError):
                    self.attachment_read(limit=size)
                with self.assertRaises(ValueError):
                    self.context_read(limit=size)


if __name__ == '__main__':
    unittest.main()
