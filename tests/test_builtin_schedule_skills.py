"""Run each shipped schedule through the Responses wire, a real tool and archiving."""
from datetime import date, datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import project_tasks
import rss
import schedule
import workflow


CASES = (
    ('last30days', 'scheduled-news-digest', 'rss_recent', {'days': 30}, 'Fixture research announcement'),
    ('literature-matrix', 'scheduled-literature-matrix', 'paper_discover', {'query': 'retrieval', 'days': 30}, 'Fixture paper'),
    ('literature-review-push', 'scheduled-literature-review', 'paper_discover', {'query': 'retrieval', 'days': 30}, 'Fixture paper'),
    ('feishu-daily-msg', 'scheduled-feishu-draft', 'rss_recent', {'days': 1}, 'Fixture research announcement'),
)


class BuiltinScheduleSkillsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix='rw-schedule-skills-')
        self.addCleanup(temp.cleanup)
        self.db = workflow.WorkflowDB(Path(temp.name) / 'app.sqlite3')
        self.addCleanup(self.db.close)
        self.pid = self.db.create_task('Fixture research', base_dir=str(Path(temp.name) / 'project'))
        model = self.db.add_model('Test model', 'https://example.test/v1', 'test', activate=True)
        self.db.db.execute("UPDATE model_profiles SET api='openai-responses' WHERE id=?", (model['id'],))
        self.db.db.commit()
        patcher = patch.object(schedule, 'DB_PATH', self.db.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def configure(self, suffix, frequency='manual'):
        item = self.db.schedule('builtin.schedule.' + suffix)
        return self.db.schedule_save({**item, 'project_id': self.pid, 'frequency': frequency, 'enabled': True})

    def test_all_four_skills_use_their_tools_and_archive_the_result(self):
        source = self.db.add_rss_source('https://rss.example.test/feed', 'Fixture feed')
        now = datetime.now(timezone.utc)
        self.db.save_rss_items(source, [
            {'title': 'Expired fixture', 'link': 'https://rss.example.test/old',
             'published_at': (now - timedelta(days=45)).strftime('%Y-%m-%d %H:%M')},
        ])
        def fetch_feed(url):
            self.assertEqual(url, source['url'])
            return [{'title': 'Fixture research announcement', 'link': 'https://rss.example.test/recent',
                     'published_at': (now - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M')}]

        patcher = patch.object(rss, 'fetch', side_effect=fetch_feed)
        fetcher = patcher.start()
        self.addCleanup(patcher.stop)
        for suffix, skill, tool, args, evidence in CASES:
            with self.subTest(rule=suffix):
                rule = self.configure(suffix)
                report = '# Fixture report\n\n' + evidence
                turns = []

                def send(req, timeout):
                    payload = json.loads(req.data)
                    turns.append(payload)
                    self.assertTrue(payload['stream'])
                    if len(turns) == 1:
                        self.assertIn(skill, json.dumps(payload['input'], ensure_ascii=False))
                        self.assertIn(tool, {spec['name'] for spec in payload['tools']})
                        output = {'type': 'function_call', 'name': tool, 'arguments': json.dumps(args), 'call_id': 'read-evidence'}
                    else:
                        citation = next(entry['output'] for entry in payload['input'] if entry.get('type') == 'function_call_output')
                        self.assertIn(evidence, citation)
                        self.assertNotIn('Expired fixture', citation)
                        output = {'type': 'message', 'content': [{'type': 'output_text', 'text': report}]}
                    event = {'type': 'response.completed', 'response': {'output': [output]}}
                    return io.BytesIO(('data: ' + json.dumps(event) + '\n\n').encode())

                def source_result(url):
                    query = parse_qs(urlsplit(url).query)
                    self.assertIn((date.today() - timedelta(days=30)).isoformat(), query['filter'][0])
                    if 'openalex' in url:
                        return json.dumps({'results': [
                            {'title': 'Fixture paper', 'publication_date': date.today().isoformat(),
                             'doi': 'https://doi.org/10.1234/fixture', 'id': 'https://openalex.org/W1234',
                             'authorships': [{'author': {'display_name': 'Fixture Author'}}]},
                            {'title': 'Expired fixture', 'publication_date': '2001-01-01'}]}).encode()
                    return b'{"message":{"items":[]}}'

                with patch.object(workflow, 'open_url', side_effect=send), patch('workflow_search._get', side_effect=source_result):
                    attempt = schedule.run_once(rule, 'manual')
                self.assertEqual(attempt['status'], 'completed', attempt.get('reason'))
                self.assertEqual(len(turns), 2)
                task = project_tasks.find_origin(self.db, 'schedule_run', attempt['id'])
                skills = task['latest_run']['checkpoint']['input']['skills']
                self.assertEqual([entry['id'] for entry in skills], [skill])
                self.assertIn(tool, skills[0]['content'])
                self.assertIn(report, Path(attempt['output_path']).read_text(encoding='utf-8'))
                self.assertIn(report, self.db.md_note(attempt['note_id'])['content'])
        fetcher.assert_called_once_with(source['url'])

    def test_disabled_skill_stops_before_the_model_and_names_the_skill(self):
        rule = self.configure('literature-matrix')
        self.db.db.execute("INSERT INTO capability_bindings VALUES('builtin-scheduler','skill','scheduled-literature-matrix',0)")
        self.db.db.commit()
        with patch.object(workflow, 'open_url') as request:
            attempt = schedule.run_once(rule, 'manual')
        request.assert_not_called()
        self.assertEqual(attempt['status'], 'failed')
        self.assertIn('scheduled-literature-matrix', attempt['reason'])
        self.assertFalse(attempt['output_path'])

    def test_previous_deliveries_do_not_skip_a_due_schedule(self):
        rule = self.configure('last30days', 'daily')
        with patch.object(workflow, 'run_agent', return_value={'reply': 'A delivered report'}):
            first = schedule.run_once(rule, 'manual')
            self.assertEqual(first['status'], 'completed')
            now = datetime.now()
            self.db.schedule_mark_run(rule['id'], first['ended_at'], now.isoformat(timespec='seconds'))
            self.assertEqual(schedule.tick(now), 1)
            import time
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and sum(row['status'] == 'completed' for row in self.db.schedule_runs()) < 2:
                time.sleep(0.02)
        self.assertEqual(sum(row['status'] == 'completed' for row in self.db.schedule_runs()), 2)

    def test_due_rules_run_concurrently_without_duplicate_triggers(self):
        from threading import Barrier, Event
        import time
        ids = {self.configure(suffix, 'daily')['id'] for suffix in ('last30days', 'literature-matrix')}
        now = datetime.now()
        for identifier in ids:
            self.db.schedule_mark_run(identifier, '', now.isoformat(timespec='seconds'))
        running, release = Barrier(3, timeout=30), Event()

        def model(*args, **kwargs):
            running.wait()
            release.wait(6)
            return {'reply': 'Independent scheduled report'}

        with patch.object(workflow, 'run_agent', side_effect=model):
            try:
                self.assertEqual(schedule.tick(now), 2)
                running.wait()
                self.assertEqual(schedule.tick(now), 0)
            finally:
                release.set()
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline and len([row for row in self.db.schedule_runs() if row['status'] == 'completed']) < 2:
                time.sleep(0.02)
        records = [row for row in self.db.schedule_runs() if row['schedule_id'] in ids]
        self.assertEqual(sorted((row['schedule_id'], row['status']) for row in records),
                         sorted((identifier, 'completed') for identifier in ids))
