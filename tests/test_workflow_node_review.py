"""Human revisions preserve original evidence and require review of the exact new version."""
from pathlib import Path
import unittest
from unittest.mock import patch

import project_task_review
import project_tasks as tasks
import workflow
import workflow_state
import workflow_layout
import workflow_tasks
from tests import test_project_task_service as service


class WorkflowNodeReviewTest(unittest.TestCase):
    setUp=service.ProjectTaskServiceTest.setUp
    create=service.ProjectTaskServiceTest.create
    result=service.ProjectTaskServiceTest.result

    def pending(self,**spec):
        task=self.create(**spec);run=tasks.start(self.db,task['id'],authority=self.ui)
        return tasks.complete(self.db,run['id'],self.result(run))

    def command(self,task,text='HUMAN_REVISED_EVIDENCE'):
        snapshot=project_task_review.read(self.db,task['id'],self.pid,0)
        return {'reviewed_run_id':snapshot['run_id'],'expected_version':snapshot['task_version'],
                'expected_hash':snapshot['content_hash'],'output_index':0,'text':text,'reason':'Checked against evidence'}

    def test_human_revision_keeps_original_and_stale_review_cannot_approve_new_output(self):
        task=self.pending();original=Path(task['outputs'][0]['path']);old=original.read_text(encoding='utf-8')
        revised=project_task_review.revise(self.db,task['id'],self.command(task),authority=self.ui)
        self.assertEqual(original.read_text(encoding='utf-8'),old)
        self.assertEqual(Path(revised['outputs'][0]['path']).read_text(encoding='utf-8'),'HUMAN_REVISED_EVIDENCE')
        self.assertEqual(revised['latest_run']['checkpoint']['output_revision'],1)
        self.assertEqual(len(revised['output_revisions']),1)
        with self.assertRaisesRegex(ValueError,'修订版本'):
            tasks.review(self.db,task['id'],approved=True,authority=self.ui,reviewed_run_id=task['latest_run_id'],reviewed_output_version=0)
        approved=tasks.review(self.db,task['id'],approved=True,authority=self.ui,reviewed_run_id=task['latest_run_id'],reviewed_output_version=1)
        self.assertEqual(approved['status'],'delivered')
        self.assertEqual(approved['deliveries'][-1]['output_versions']['files'][0]['path'],revised['outputs'][0]['path'])
        dependent=self.create(goal='Use reviewed evidence',dependencies=[task['id']])
        run=tasks.start(self.db,dependent['id'],authority=self.ui)
        self.assertEqual(run['input_snapshot']['dependencies'][0]['outputs'][0]['path'],revised['outputs'][0]['path'])

    def test_stale_task_hash_other_project_and_agent_edit_are_rejected(self):
        task=self.pending();body=self.command(task)
        with self.assertRaises(ValueError):project_task_review.revise(self.db,task['id'],body,authority=tasks.CommandContext.agent(self.pid))
        with self.assertRaises(ValueError):project_task_review.revise(self.db,task['id'],body,authority=tasks.CommandContext.ui(self.other))
        with self.assertRaises(ValueError):project_task_review.revise(self.db,task['id'],{**body,'expected_version':0},authority=self.ui)
        with self.assertRaises(ValueError):project_task_review.revise(self.db,task['id'],{**body,'expected_hash':'wrong'},authority=self.ui)
        self.assertEqual(tasks.get(self.db,task['id'])['output_revisions'],[])

    def test_delivered_outputs_remain_immutable(self):
        task=self.pending();body=self.command(task)
        tasks.review(self.db,task['id'],approved=True,authority=self.ui)
        with self.assertRaisesRegex(ValueError,'新运行'):project_task_review.revise(self.db,task['id'],body,authority=self.ui)

    def test_invalid_json_revision_does_not_change_valid_contract_output(self):
        task=self.create(output_contract={'format':'json'});run=tasks.start(self.db,task['id'],authority=self.ui)
        task=tasks.complete(self.db,run['id'],self.result(run,'{"valid":true}'))
        with self.assertRaisesRegex(ValueError,'JSON'):
            project_task_review.revise(self.db,task['id'],self.command(task,'not json'),authority=self.ui)
        self.assertEqual(Path(task['outputs'][0]['path']).read_text(encoding='utf-8'),'{"valid":true}')

    def test_document_stage_revision_updates_working_document_and_capture(self):
        task=self.create(metadata={'stage':'outline'})
        workflow.doc_write(self.db,self.pid,'outline','# ORIGINAL_OUTLINE')
        capture=workflow_state.capture(self.db,self.pid,'outline')
        source=Path(capture['file_versions'][0]['path'])
        run=tasks.start(self.db,task['id'],authority=self.ui)
        result=self.result(run,source.read_text(encoding='utf-8'),checkpoint={'output':capture})
        result['output_snapshot']['files'][0]['original_path']=str(source)
        result['output_snapshot']['original_versions']=[{'path':str(source),'sha256':tasks._hash(source)}]
        task=tasks.complete(self.db,run['id'],result)
        revised=project_task_review.revise(self.db,task['id'],self.command(task,'# HUMAN_CHECKED_OUTLINE'),authority=self.ui)
        self.assertIn('HUMAN_CHECKED_OUTLINE',workflow.doc_read(self.db,self.pid,'outline')['markdown'])
        self.assertEqual(revised['latest_run']['checkpoint']['output']['fingerprint'],workflow_state.capture(self.db,self.pid,'outline')['fingerprint'])

    def test_revised_workflow_document_can_be_reviewed_with_updated_stage_delivery(self):
        workflow_layout.ensure_schema(self.db)
        layout=workflow_layout.save_layout(self.db,{'name':'Human edited outline','nodes':[{'id':'outline','step':'outline','label':'Outline','executor_binding_id':self.binding['id']}],'edges':[]})['layout']
        workflow_layout.select_layout(self.db,{'task_id':self.pid,'layout_id':layout['id']})
        prepared=workflow_tasks.prepare(self.db,self.pid,'Human document review')
        def write(db,pid,*args,**kwargs):
            workflow.doc_write(db,pid,'outline','# ORIGINAL_WORKFLOW_DOCUMENT')
            return {'reply':'Written original document'}
        with patch.object(workflow,'run_agent',side_effect=write):workflow_tasks.advance(self.db,prepared['id'])
        task=tasks.get(self.db,prepared['settings']['task_ids']['outline'])
        self.assertEqual(task['status'],'awaiting_review',task['latest_run'].get('error'))
        revised=project_task_review.revise(self.db,task['id'],self.command(task,'# REVISED_WORKFLOW_DOCUMENT'),authority=self.ui)
        accepted=tasks.review(self.db,task['id'],approved=True,authority=self.ui,reviewed_output_version=1)
        self.assertEqual(accepted['status'],'delivered')
        self.assertEqual(workflow_state.states(self.db,self.pid)['outline']['status'],'delivered')


if __name__=='__main__':unittest.main()
