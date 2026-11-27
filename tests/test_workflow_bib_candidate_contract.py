import csv
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import artifact_quality
import project_task_execution as worker
import project_artifacts
import project_task_review
import project_tasks
import research_setup
from tests import test_project_artifacts as artifact_fixtures


def candidate_csv(count=11):
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(['title', 'doi_or_stable_url'])
    for index in range(count):
        writer.writerow([f'Candidate Study {index}', f'10.1234/paper{index}'])
    return output.getvalue()


def bibliography(count=8):
    return '\n'.join(f'@article{{ref{index}, title={{Candidate Study {index}}}, '
                     f'doi={{10.1234/paper{index}}}}}' for index in range(count))


class BibCandidateContractTest(unittest.TestCase):
    def test_eight_exact_candidates_pass_without_requiring_all_eleven(self):
        artifact_quality.validate_bib_candidates('references.bib', bibliography(), candidate_csv())
        artifact_quality.validate_bib_candidates(
            'references.bib', bibliography().replace('Candidate Study 0', '{Candidate} Study 0')
            .replace('10.1234/paper0', 'https://doi.org/10.1234/paper0'), candidate_csv())

    def test_outside_candidate_and_doi_conflicts_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'ref7.*not in the accepted candidate pool'):
            artifact_quality.validate_bib_candidates('references.bib', bibliography().replace('Candidate Study 7', 'Unrelated Study'), candidate_csv())
        with self.assertRaisesRegex(ValueError, 'ref7.*DOI differs'):
            artifact_quality.validate_bib_candidates('references.bib', bibliography().replace('10.1234/paper7', '10.1234/unrelated'), candidate_csv())
        with self.assertRaisesRegex(ValueError, 'duplicates an accepted candidate'):
            artifact_quality.validate_bib_candidates('references.bib', bibliography() + '\n@article{duplicate,title={Candidate Study 0},doi={10.1234/paper0}}', candidate_csv())

    def test_non_doi_candidate_requires_its_exact_stable_url(self):
        candidates = 'title,doi_or_stable_url\nKnown paper,https://example.org/paper/42\n'
        artifact_quality.validate_bib_candidates('references.bib', '@article{x,title={Known paper},url={http://example.org/paper/42/}}', candidates)
        with self.assertRaisesRegex(ValueError, 'DOI cannot be verified'):
            artifact_quality.validate_bib_candidates('references.bib', '@article{x,title={Known paper},doi={10.1234/unproven},url={https://example.org/paper/42}}', candidates)
        with self.assertRaisesRegex(ValueError, 'do not match'):
            artifact_quality.validate_bib_candidates('references.bib', '@article{x,title={Known paper},url={https://example.org/paper/43}}', candidates)

    def test_canonical_doi_or_url_columns_are_supported_without_inferred_locators(self):
        artifact_quality.validate_bib_candidates('references.bib', '@article{x,title={Known paper},doi={10.1234/known}}',
                                                 'title,doi\nKnown paper,10.1234/known\n')
        artifact_quality.validate_bib_candidates('references.bib', '@article{x,title={Known paper},url={https://example.org/paper}}',
                                                 'title,url\nKnown paper,https://example.org/paper\n')
        with self.assertRaisesRegex(ValueError, 'requires title and DOI or stable URL'):
            artifact_quality.candidate_sources('title,doi\nKnown paper,unverified\n')

    def test_machine_flag_is_explicit_and_bib_only(self):
        artifact_quality.validate_rules({'required_files': ['a.bib'], 'file_rules': {'a.bib': {'require_candidate_match': True}}})
        for contract in (
            {'required_files': ['a.bib'], 'file_rules': {'a.bib': {'require_candidate_match': 'true'}}},
            {'required_files': ['a.md'], 'file_rules': {'a.md': {'require_candidate_match': True}}},
        ):
            with self.assertRaises(ValueError):
                artifact_quality.validate_rules(contract)
        node = research_setup._literature_contract({'step': 'review', 'output_contract': {'required_files': ['references.bib']}}, candidate_match=True)
        self.assertTrue(node['output_contract']['file_rules']['references.bib']['require_candidate_match'])
        other = research_setup._literature_contract({'step': 'review', 'output_contract': {'required_files': ['references.bib']}})
        self.assertNotIn('require_candidate_match', other['output_contract']['file_rules']['references.bib'])

    def test_legacy_rule_is_narrow_and_explicit_false_is_preserved(self):
        node = {'step': 'review', 'executor_role_id': 'final_review'}
        contract = {'required_files': ['literature_review.md', 'references.bib'], 'file_rules': {'references.bib': {'min_entries': 8}}}
        task = {'output_contract': contract, 'metadata': {'node_config': node, 'layout_snapshot': {'id': 'builtin.langgraph.literature_study'}}}
        self.assertTrue(worker._effective_output_contract(task)['file_rules']['references.bib']['require_candidate_match'])
        self.assertNotIn('require_candidate_match', contract['file_rules']['references.bib'])
        task['metadata']['layout_snapshot']['id'] = 'custom'
        self.assertNotIn('require_candidate_match', worker._effective_output_contract(task)['file_rules']['references.bib'])
        task['metadata']['layout_snapshot']['id'] = 'builtin.langgraph.literature_study'
        contract['file_rules']['references.bib']['require_candidate_match'] = False
        self.assertFalse(worker._effective_output_contract(task)['file_rules']['references.bib']['require_candidate_match'])


class FrozenCandidateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.db = type('DB', (), {})()
        self.db.db = sqlite3.connect(':memory:')
        self.db.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.db.close)
        self.db.db.executescript('''
            CREATE TABLE project_tasks (id TEXT, project_id TEXT, workflow_run_id TEXT);
            CREATE TABLE project_task_runs (id TEXT, task_id TEXT, input_snapshot TEXT);
            CREATE TABLE project_task_deliveries (id TEXT, task_id TEXT, task_run_id TEXT,
                decision TEXT, input_versions TEXT, output_versions TEXT);
        ''')
        self.task = {'project_id': 'project', 'workflow_run_id': 'workflow'}

    def delivery(self, task, dependencies, content=None):
        outputs = []
        if content is not None:
            path = self.root / f'{task}.csv'
            path.write_text(content, encoding='utf-8')
            outputs.append({'asset_id': 'asset-' + task, 'name': 'candidate_papers.csv', 'path': str(path),
                            'content_hash': hashlib.sha256(path.read_bytes()).hexdigest()})
        inputs = {'dependencies': dependencies}
        self.db.db.execute('INSERT INTO project_tasks VALUES (?,?,?)', (task, 'project', 'workflow'))
        self.db.db.execute('INSERT INTO project_task_runs VALUES (?,?,?)', ('run-' + task, task, json.dumps(inputs)))
        self.db.db.execute('INSERT INTO project_task_deliveries VALUES (?,?,?,?,?,?)',
                           ('delivery-' + task, task, 'run-' + task, 'approved', json.dumps(inputs), json.dumps({'files': outputs})))
        return {'task_id': task, 'delivery_id': 'delivery-' + task, 'outputs': outputs}

    def read(self, dependencies):
        with patch.object(worker.tasks, '_root', return_value=self.root):
            return worker._frozen_candidate_csv(self.db, self.task, {'input_snapshot': {'dependencies': dependencies}})

    def test_traverses_frozen_diamond_and_detects_changed_or_ambiguous_sources(self):
        a = self.delivery('a', [], candidate_csv())
        b = self.delivery('b', [a])
        c = self.delivery('c', [a])
        d = self.delivery('d', [b, c])
        self.assertEqual(self.read([d]), candidate_csv())
        other = self.delivery('other', [], candidate_csv())
        with self.assertRaisesRegex(ValueError, 'unambiguous'):
            self.read([d, other])
        Path(a['outputs'][0]['path']).write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'changed or is outside'):
            self.read([d])

    def test_unapproved_delivery_and_mismatched_versions_fail_closed(self):
        a = self.delivery('a', [], candidate_csv())
        self.db.db.execute("UPDATE project_task_deliveries SET decision='rejected' WHERE id='delivery-a'")
        with self.assertRaisesRegex(ValueError, 'not an approved delivery'):
            self.read([a])
        self.db.db.execute("UPDATE project_task_deliveries SET decision='approved' WHERE id='delivery-a'")
        a['outputs'] = []
        with self.assertRaisesRegex(ValueError, 'differ from the frozen delivery'):
            self.read([a])

    def test_duplicate_diamond_edge_with_a_different_hash_is_rejected(self):
        a = self.delivery('a', [], candidate_csv())
        tampered = deepcopy(a)
        tampered['outputs'][0]['content_hash'] = 'bad-hash'
        b = self.delivery('b', [a])
        c = self.delivery('c', [tampered])
        with self.assertRaisesRegex(ValueError, 'differ from the frozen delivery|conflicting frozen delivery'):
            self.read([b, c])


class BibWorkerContractTest(unittest.TestCase):
    setUp = artifact_fixtures.ProjectArtifactTest.setUp
    queue = artifact_fixtures.ProjectArtifactTest.queue

    def output(self, flag):
        task, run = self.queue(output_contract={
            'required_files': ['references.bib'],
            'file_rules': {'references.bib': {'min_entries': 8, 'require_candidate_match': flag}},
        })
        claimed = project_tasks.claim(self.db, run['id'])
        self.db.project_task_context = project_tasks.CommandContext.agent(self.project, task['id'])
        receipt = project_artifacts.write(self.db, self.project, {
            'path': 'references.bib', 'content': bibliography().replace('Candidate Study 7', 'Unrelated Study'),
        })
        return worker._outputs(self.db, claimed['task'], claimed['run'],
                               {'reply': 'Submitted references', 'output_assets': receipt['output_assets']}, None,
                               candidate_sources_content=candidate_csv())

    def test_worker_rejects_foreign_references_with_the_explicit_rule(self):
        with self.assertRaisesRegex(ValueError, 'ref7.*not in the accepted candidate pool'):
            self.output(True)

    def test_worker_does_not_restrict_references_without_the_rule(self):
        output = self.output(False)
        self.assertTrue(output['files'])

    def old_invalid_result(self):
        task, run = self.queue(output_contract={
            'required_files': ['references.bib'],
            'file_rules': {'references.bib': {'min_entries': 8, 'require_candidate_match': True}},
        }, review_required=True)
        claimed = project_tasks.claim(self.db, run['id'])
        self.db.project_task_context = project_tasks.CommandContext.agent(self.project, task['id'])
        receipt = project_artifacts.write(self.db, self.project, {
            'path': 'references.bib', 'content': bibliography().replace('Candidate Study 7', 'Unrelated Study'),
        })
        legacy = {**claimed['task'], 'output_contract': {'required_files': ['references.bib'],
                  'file_rules': {'references.bib': {'min_entries': 8}}}}
        output = worker._outputs(self.db, legacy, claimed['run'],
                                 {'reply': 'Old result', 'output_assets': receipt['output_assets']}, None)
        result = project_tasks.complete(self.db, run['id'], {'reply': 'Old result', 'output_snapshot': output})
        self.assertEqual(result['status'], 'awaiting_review')
        return result

    def test_old_invalid_result_can_be_edited_then_approved(self):
        task = self.old_invalid_result()
        with patch.object(worker, '_frozen_candidate_csv', return_value=candidate_csv()):
            with self.assertRaisesRegex(ValueError, 'ref7.*not in the accepted candidate pool'):
                project_tasks.review(self.db, task['id'], approved=True,
                                     authority=project_tasks.CommandContext.ui(self.project))
            current = project_tasks.get(self.db, task['id'])
            self.assertEqual(current['status'], 'awaiting_review')
            self.assertEqual(current['accepted_delivery_id'], '')
            item = current['latest_run']['output_snapshot']['files'][0]
            revised = project_task_review.revise(self.db, task['id'], {
                'reviewed_run_id': current['latest_run_id'], 'expected_version': current['version'],
                'output_index': 0, 'expected_hash': item['content_hash'], 'text': bibliography(),
            }, authority=project_tasks.CommandContext.ui(self.project))
            self.assertEqual(revised['status'], 'awaiting_review')
            result = project_tasks.review(self.db, task['id'], approved=True,
                                          reviewed_output_version=1,
                                          authority=project_tasks.CommandContext.ui(self.project))
            self.assertEqual(result['status'], 'delivered')

    def test_old_invalid_result_can_be_rejected(self):
        task = self.old_invalid_result()
        with patch.object(worker, '_frozen_candidate_csv', return_value=candidate_csv()):
            result = project_tasks.review(self.db, task['id'], approved=False, feedback='Revise the references.',
                                          authority=project_tasks.CommandContext.ui(self.project))
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['accepted_delivery_id'], '')


if __name__ == '__main__':
    unittest.main()
