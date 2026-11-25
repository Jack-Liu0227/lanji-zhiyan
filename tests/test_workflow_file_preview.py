"""File preview boundaries and pinned node versions, using disposable projects."""
import csv
import io
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import workflow
import workflow_execution as engine
import workflow_node_workbench as nodes
import project_tasks as tasks
import workflow_file_preview as files
from tests import test_task_orchestration as orchestration


class FilePreviewTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        self.db = workflow.WorkflowDB(self.folder / 'test.sqlite3')
        self.addCleanup(self.db.close)
        self.pid = self.db.create_task('Preview', base_dir=str(self.folder / 'project'))
        self.chat = self.db.create_chat('Preview chat', task_id=self.pid)
        self.root = self.db.base_dir(self.pid).resolve()

    def write(self, relative, content):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8', newline='')
        return target

    def preview(self, **options):
        return files.preview(self.db, self.pid, self.chat, **options)

    def test_markdown_bom_relative_project_root_and_absolute_paths(self):
        target = self.write('资料/证据.md', '\ufeff# 中文证据\n[数据](table.csv)')
        for reference in ({'relative': '资料/证据.md'}, {'path': '/资料/证据.md'}, {'path': str(target)}):
            with self.subTest(reference=reference):
                result = self.preview(**reference)
                self.assertEqual(result['text'], '# 中文证据\n[数据](table.csv)')
                self.assertEqual(result['kind'], 'markdown')
                self.assertEqual(result['source'], {'kind': 'project', 'project_id': self.pid,
                                                   'chat_id': self.chat, 'relative': '资料/证据.md'})
                self.assertFalse(result['truncated'])

    def test_csv_reader_preserves_bom_duplicate_headers_quotes_newlines_and_all_columns(self):
        columns = ['标题', '标题', *[f'列{i}' for i in range(2, 14)]]
        rows = [['中文，逗号', '引号 "原文", 内容\r\n第二行', *map(str, range(2, 14))],
                ['末行', '“中文引号”', *[''] * 12]]
        text = io.StringIO(newline='')
        writer = csv.writer(text)
        writer.writerows([columns, *rows])
        self.write('数据.csv', '\ufeff' + text.getvalue())
        result = self.preview(relative='数据.csv', limit='1')
        self.assertEqual(result['columns'], columns)
        self.assertEqual(result['rows'], rows[:1])
        self.assertEqual(result['total_rows'], 2)
        self.assertTrue(result['truncated'])
        result = self.preview(relative='数据.csv', offset=1, limit=1)
        self.assertEqual(result['rows'], rows[1:])
        self.assertFalse(result['truncated'])
        self.assertEqual(result['offset'], 1)
        self.assertEqual(self.preview(relative='数据.csv', offset=2)['rows'], [])
        with self.assertRaisesRegex(ValueError, 'offset'):
            self.preview(relative='数据.csv', offset=3)

    def test_csv_empty_and_ragged_rows_keep_extra_cells(self):
        self.write('empty.csv', '')
        self.assertEqual(self.preview(relative='empty.csv')['columns'], [])
        self.write('ragged.csv', 'a,b\n1,2,3\n4\n')
        result = self.preview(relative='ragged.csv')
        self.assertEqual(result['columns'], ['a', 'b', '第 3 列'])
        self.assertEqual(result['rows'], [['1', '2', '3'], ['4', '', '']])
        first_page = self.preview(relative='ragged.csv', offset=1, limit=1)
        self.assertEqual(first_page['columns'], ['a', 'b', '第 3 列'])
        self.assertEqual(first_page['rows'], [['4', '', '']])
        self.assertEqual(first_page['total_rows'], 2)

    def test_plain_project_reference_is_allowed_from_another_project_conversation(self):
        self.write('shared.md', '# Explicit project material')
        another = self.db.create_chat('No associated workflow', task_id=self.pid)
        self.assertEqual(files.preview(self.db, self.pid, another, relative='shared.md')['text'],
                         '# Explicit project material')
        other_pid = self.db.create_task('Foreign', base_dir=str(self.folder / 'foreign'))
        foreign = self.db.create_chat('Foreign chat', task_id=other_pid)
        for chat in ('', 'missing-chat', foreign):
            with self.subTest(chat=chat), self.assertRaises(ValueError):
                files.preview(self.db, self.pid, chat, relative='shared.md')

    def test_missing_nonregular_unsupported_and_escaping_paths_are_rejected(self):
        (self.root / 'folder.md').mkdir()
        self.write('sample.txt', 'not supported')
        (self.folder / 'secret.md').write_text('outside project', encoding='utf-8')
        for relative in ('missing.md', 'folder.md', 'sample.txt', '../secret.md', 'https://host/file.md',
                         '//host/share/file.md', 'C:relative.md', 'sample.md\0', 'sample.md:stream.md'):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                self.preview(relative=relative)
        with self.assertRaisesRegex(ValueError, '不存在'):
            self.preview(path='missing.md')

    def test_symlink_cannot_escape_project_directory(self):
        outside = self.folder / 'secret.md'
        outside.write_text('outside', encoding='utf-8')
        link = self.root / 'link.md'
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f'Symlinks unavailable: {exc}')
        with self.assertRaisesRegex(ValueError, '越过'):
            self.preview(relative='link.md')

    def test_actual_file_size_and_growth_are_bounded_before_reading(self):
        for relative, maximum in (('large.md', files.MAX_MARKDOWN_BYTES), ('large.csv', files.MAX_FILE_BYTES)):
            target = self.root / relative
            with target.open('wb') as stream:
                stream.truncate(maximum + 1)
            with self.subTest(relative=relative), self.assertRaisesRegex(ValueError, '过大'):
                self.preview(relative=relative)
        self.write('growing.md', 'x' * 20)
        with patch.object(files, 'MAX_MARKDOWN_BYTES', 16), patch.object(Path, 'stat',
                return_value=SimpleNamespace(st_mode=stat.S_IFREG, st_size=1)):
            with self.assertRaisesRegex(ValueError, '过大'):
                files._read_file(self.root / 'growing.md')

    def test_parameter_bounds_and_invalid_encoding(self):
        self.write('ok.csv', 'header\nvalue\n')
        for name, value in (('offset', -1), ('offset', True), ('offset', '1.5'), ('offset', 2**31),
                            ('limit', 0), ('limit', 501), ('limit', 1.5), ('limit', 'x'),
                            ('limit', '9' * 5000), ('output_revision', False), ('output_revision', -1)):
            with self.subTest(name=name, value=str(value)[:40]), self.assertRaises(ValueError):
                self.preview(relative='ok.csv', **{name: value})
        (self.root / 'bad.md').write_bytes(b'\xff\xfe')
        with self.assertRaisesRegex(ValueError, 'UTF-8'):
            self.preview(relative='bad.md')
        self.write('broken.csv', 'header\n"unterminated')
        with self.assertRaisesRegex(ValueError, 'CSV'):
            self.preview(relative='broken.csv')
        self.write('ok.md', 'one line')
        with self.assertRaisesRegex(ValueError, 'offset'):
            self.preview(relative='ok.md', offset=1)

    def test_route_supports_query_lists_scalars_and_file_path(self):
        target = self.write('report.md', '# Report')
        query = {'project_id': [self.pid], 'chat_id': [self.chat], 'path': [str(target)]}
        self.assertEqual(files.route('GET', '/api/project-tasks/file-preview', {}, query, self.db)['preview']['text'], '# Report')
        scalar = {key: value[0] for key, value in query.items()}
        self.assertEqual(files.route('GET', '/api/project-tasks/file-preview', {}, scalar, self.db)['preview']['name'], 'report.md')
        self.assertIsNone(files.route('POST', '/api/project-tasks/file-preview', {}, query, self.db))
        self.assertIsNone(files.route('GET', '/api/project-tasks/node-preview', {}, query, self.db))
        with self.assertRaises(ValueError):
            files.route('GET', '/api/project-tasks/file-preview', {}, {**query, 'path': ['a.md', 'b.md']}, self.db)
        with self.assertRaises(ValueError):
            self.preview(relative='report.md', path='different.md')


class NodeFilePreviewTest(unittest.TestCase):
    setUp = orchestration.TaskOrchestrationTest.setUp
    layout = orchestration.TaskOrchestrationTest.layout

    def finish(self):
        self.layout(['task'])
        run = engine.create(self.db, self.pid)
        with patch.object(workflow, 'run_agent', return_value={'reply': '# Original node version'}):
            run = engine.advance(self.db, run['id'])
        self.chat = run['chat_id']
        self.task = tasks.get(self.db, run['settings']['task_ids']['n0'])
        self.run_id = self.task['latest_run_id']
        self.root = self.db.base_dir(self.pid).resolve()
        self.path = self.task['outputs'][0]['path']

    def preview(self, **options):
        defaults = {'task_id': self.task['id'], 'task_run_id': self.run_id, 'output_revision': 0}
        return files.preview(self.db, self.pid, self.chat, **{**defaults, **options})

    def test_path_resolves_exact_artifact_and_revision_history_remains_pinned(self):
        self.finish()
        original = self.preview(path=self.path)
        self.assertEqual(original['text'], '# Original node version')
        self.assertEqual(original['source']['artifact_id'], 'file:0')
        selection = nodes.Selection(self.pid, self.task['id'], self.run_id, 0)
        result = nodes.read(self.db, selection, authority=tasks.CommandContext.ui(self.pid))
        nodes.command(self.db, self.task['id'], {'action': 'revise', 'identity': result['identity'],
                      'request_key': 'file-preview-revision', 'artifact_id': 'file:0', 'text': '# Revision one'},
                      authority=tasks.CommandContext.ui(self.pid))
        self.assertEqual(self.preview(artifact_id='file:0')['text'], '# Original node version')
        latest = self.preview(artifact_id='file:0', output_revision=1)
        self.assertEqual(latest['text'], '# Revision one')
        self.assertEqual(latest['source']['output_revision'], 1)
        with self.assertRaises(ValueError):
            self.preview(artifact_id='file:0', output_revision=99)

    def test_unknown_or_mismatching_paths_never_select_the_first_file(self):
        self.finish()
        other = self.root / 'other.md'
        other.write_text('# Current project material', encoding='utf-8')
        for options in ({'path': str(other)}, {'path': 'report.md'}, {'artifact_id': 'file:0', 'path': str(other)},
                        {'artifact_id': 'file:9'}, {}, {'artifact_id': 'asset:foreign'},
                        {'artifact_id': 'file:0', 'task_run_id': ''},
                        {'artifact_id': 'file:0', 'output_revision': None}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.preview(**options)
        unrelated = self.db.create_chat('Unrelated same-project chat', task_id=self.pid)
        with self.assertRaisesRegex(ValueError, '当前对话'):
            files.preview(self.db, self.pid, unrelated, task_id=self.task['id'], task_run_id=self.run_id,
                          output_revision=0, path=self.path)
        self.assertEqual(files.preview(self.db, self.pid, unrelated, path=str(other))['text'], '# Current project material')

    def replace_output(self, output, **run_changes):
        changes = {'output_snapshot': json.dumps(output, ensure_ascii=False), **run_changes}
        self.db.db.execute('UPDATE project_task_runs SET ' + ','.join(key + '=?' for key in changes) + ' WHERE id=?',
                           [*changes.values(), self.run_id])
        self.db.db.commit()

    def test_csv_node_path_maps_second_file_without_losing_any_columns(self):
        self.finish()
        path = self.root / '节点数据.csv'
        path.write_text('\ufeffa,a,c,d,e,f,g,h,i,j,k,l\n"中文,字段","多行\n第二行",2,3,4,5,6,7,8,9,10,11\n',
                        encoding='utf-8', newline='')
        output = self.task['latest_run']['output_snapshot']
        output['files'].append({'path': str(path), 'name': path.name, 'content_hash': tasks._hash(path)})
        self.replace_output(output)
        result = self.preview(path='/节点数据.csv')
        self.assertEqual(result['source']['artifact_id'], 'file:1')
        self.assertEqual(result['columns'], ['a', 'a', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l'])
        self.assertEqual(result['rows'], [['中文,字段', '多行\n第二行', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11']])
        output['files'].append(dict(output['files'][1]))
        self.replace_output(output)
        with self.assertRaisesRegex(ValueError, '唯一匹配'):
            self.preview(path=str(path))

    def test_table_node_normalizes_object_rows_and_keeps_all_keys(self):
        self.finish()
        output = self.task['latest_run']['output_snapshot']
        output['content'] = [{'title': '论文甲', 'extra': {'doi': '10/test'}}, {'title': '论文乙', 'later': 7}]
        inputs = self.task['latest_run']['input_snapshot']
        inputs.setdefault('execution_context', {})['node_config'] = {'step': 'search'}
        self.replace_output(output, input_snapshot=json.dumps(inputs))
        result = self.preview(artifact_id='structured', offset=1, limit=1)
        self.assertEqual(result['kind'], 'table')
        self.assertEqual(result['columns'], ['title', 'extra', 'later'])
        self.assertEqual(result['rows'], [['论文乙', '', 7]])
        self.assertEqual(result['total_rows'], 2)
        self.assertEqual(self.preview(artifact_id='structured', limit=1)['rows'],
                         [['论文甲', '{"doi": "10/test"}', '']])

    def test_corrupted_missing_and_oversized_node_files_fail_explicitly(self):
        self.finish()
        target = Path(self.path)
        original = target.read_bytes()
        target.write_text('changed', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.preview(path=self.path)
        target.write_bytes(original)
        with patch.object(files, 'MAX_MARKDOWN_BYTES', 4), patch.object(nodes, 'workspace_preview') as read:
            with self.assertRaisesRegex(ValueError, '过大'):
                self.preview(path=self.path)
            read.assert_not_called()
        target.unlink()
        with self.assertRaisesRegex(ValueError, '不存在'):
            self.preview(path=self.path)


if __name__ == '__main__':
    unittest.main()
