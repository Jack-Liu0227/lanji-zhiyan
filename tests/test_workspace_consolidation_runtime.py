"""User paths through the consolidated workspace and native Agent controls."""
from pathlib import Path
import unittest

import workflow
from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace
from tests import test_project_tasks_runtime as task_runtime
from tests.test_workspace_graph_runtime import TrajectoryModelFixture


@unittest.skipUnless(web.sync_playwright and web.EDGE,'Requires Playwright and Edge')
class WorkspaceConsolidationRuntimeTest(unittest.TestCase):
    setUpClass=classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp=web.WebRuntimeTest.setUp
    load_page=web.WebRuntimeTest.load_page
    seed=workspace.ProjectWorkspaceRuntimeTest.seed
    use_model=workspace.ProjectWorkspaceRuntimeTest.use_model
    poll_json=workspace.ProjectWorkspaceRuntimeTest.poll_json

    def test_project_manager_edits_metadata_and_preserves_directory(self):
        self.seed()
        self.assertEqual(self.page.locator('#sidebar [data-nav="tasks"],#sidebar [data-nav="schedules"]').count(),0)
        original=self.page.request.get(self.url+'api/task?id='+self.pid).json()['task']
        self.page.locator('#pwProjectSettings > summary').click()
        self.page.locator('#pwProjectEdit').click()
        self.page.locator('#t-title').fill('Renamed inside workspace')
        self.page.locator('#t-q').fill('Updated research question')
        self.page.locator('#t-go').click()
        self.page.locator('dialog.project-manager').wait_for(state='detached')
        updated=self.page.request.get(self.url+'api/task?id='+self.pid).json()['task']
        self.assertEqual(updated['base_dir'],original['base_dir'])
        self.assertEqual(updated['title'],'Renamed inside workspace')
        self.assertEqual(updated['question'],'Updated research question')
        self.assertTrue((Path(updated['base_dir'])/'evidence.md').is_file())
        self.assertEqual(self.errors,[])

    def test_legacy_automation_links_open_global_center_and_edit_the_same_rule(self):
        self.seed()
        web.workspace_section(self.page,'automation')
        self.page.locator('#schNew').click()
        self.page.locator('#schName').fill('ONE_SHARED_AUTOMATION')
        self.page.locator('#schFreq').select_option('manual')
        self.page.locator('#schAssistant').select_option('workspace-writer')
        self.page.locator('#schPrompt').fill('Prepare a project report')
        self.page.locator('#schSave').click()
        self.page.get_by_text('ONE_SHARED_AUTOMATION',exact=True).wait_for()
        data=self.page.request.get(self.url+'api/schedules').json()
        rule=next(row for row in data['schedules'] if row['name']=='ONE_SHARED_AUTOMATION')
        self.assertEqual(self.page.locator('#sidebar [data-nav="task-center"]').count(),1)
        self.page.evaluate("openTab('schedules')")
        self.page.locator('[data-ec-view="automation"][aria-pressed="true"]').wait_for()
        self.assertEqual(self.page.evaluate('route()'),'task-center')
        self._finish_automation_edit(rule)

    def test_small_create_button_opens_empty_form_and_catalog_opens_real_manuscript(self):
        self.seed()
        self.page.locator('#pwProjectCreate').click()
        self.assertEqual(self.page.locator('#t-title').input_value(),'')
        self.assertEqual(self.page.locator('.pm-projects').count(),0)
        self.page.keyboard.press('Escape')
        for kind in ('manuscript','experiment'):
            response=self.page.request.post(self.url+'api/project/resources/write',data={'project_id':self.pid,'kind':kind,'title':'CATALOG_'+kind,'content':'# REAL_UNIFIED_'+kind})
            self.assertTrue(response.ok,response.text())
        web.workspace_section(self.page,'catalog')
        self.page.locator('[data-pr-kind="manuscript"]').click()
        self.page.locator('[data-pr-item]').filter(has_text='CATALOG_manuscript').click()
        self.page.wait_for_function("document.querySelector('#flStage').textContent.includes('REAL_UNIFIED_manuscript')")
        web.workspace_section(self.page,'experiments')
        self.page.locator('[data-pr-item]').filter(has_text='CATALOG_experiment').wait_for()
        self.assertEqual(self.page.locator('[data-pr-kind="experiment"]').get_attribute('aria-pressed'),'true')
        self.assertEqual(self.errors,[])

    def _finish_automation_edit(self,rule):
        self.page.locator(f'[data-sch-edit="{rule["id"]}"]').click()
        self.assertEqual(self.page.locator('#schProj').input_value(),self.pid)
        self.page.locator('#schName').fill('UPDATED_SHARED_AUTOMATION')
        self.page.locator('#schSave').click()
        self.page.get_by_text('UPDATED_SHARED_AUTOMATION',exact=True).wait_for()
        data=self.page.request.get(self.url+'api/schedules').json()
        self.assertEqual(len([row for row in data['schedules'] if row['id']==rule['id']]),1)
        self.assertEqual(next(row for row in data['schedules'] if row['id']==rule['id'])['name'],'UPDATED_SHARED_AUTOMATION')
        self.assertEqual(self.errors,[])

    def test_only_one_workflow_canvas_and_no_stage_tool_entry(self):
        self.seed()
        web.workspace_section(self.page,'workflow')
        self.page.locator('[data-pwg-label]').wait_for()
        self.assertEqual(self.page.locator('.project-workflow-graph').count(),1)
        self.assertEqual(self.page.locator('#pwLegacyGraphHost,#pwLgCompactBar,[data-pw-view="tools"],#pwWorkflowTool').count(),0)
        self.page.evaluate("ProjectWorkspace.showResource('tools')")
        self.page.locator('[data-pwg-label]').wait_for()
        self.assertEqual(self.page.locator('#pwToolChoice,.wf-stage').count(),0)
        self.assertEqual(self.errors,[])

    def test_agent_graph_entry_starts_the_same_project_workflow(self):
        self.seed()
        with task_runtime.TaskModelFixture() as model:
            self.use_model(model.url)
            response=self.page.request.post(self.url+'api/langgraph/start',data={
                'project_id':self.pid,'graph_type':'literature_study','goal':'Read evidence and deliver a report'})
            self.assertTrue(response.ok,response.text())
            started=response.json()['run']
            saved=self.poll_json('/api/workflow/execution?task_id='+self.pid,
                lambda data:(data.get('run') or {}).get('status') in ('awaiting_review','failed'))['run']
            self.assertEqual(saved['id'],started['id'])
            self.assertEqual(saved['status'],'awaiting_review',saved)
            self.page.evaluate("ProjectWorkspace.showResource('workflow')")
            self.page.locator('#pwWorkflowChoice').wait_for()
            self.assertEqual(self.page.locator('#pwWorkflowChoice').input_value(),saved['layout']['id'])
            tasks=self.page.request.get(self.url+'api/project-tasks?project_id='+self.pid).json()['tasks']
            self.assertEqual(len(tasks),4)
            self.assertTrue(all(task['workflow_run_id']==saved['id'] for task in tasks))
        self.assertEqual(self.errors,[])

    def test_global_chat_runtime_is_per_conversation_and_used_by_model(self):
        self.seed()
        with task_runtime.TaskModelFixture() as model:
            self.use_model(model.url)
            with workflow.WorkflowDB(self.database_path) as db:
                db.add_model('Independent chat model',model.url,'independent-chat-model','fixture-only')
                selected=next(row['id'] for row in db.models() if row['name']=='Independent chat model')
            self.page.evaluate("async()=>{await refreshState(false);openTab('agent');}")
            self.page.locator('#ah-new').click()
            self.page.locator('#modpick').select_option(selected)
            self.page.wait_for_function("!document.querySelector('#modpick').disabled")
            self.page.locator('#atReasoning').select_option('high')
            self.page.wait_for_function("!document.querySelector('#atReasoning').disabled")
            self.page.locator('#atPermission').select_option('read_only')
            self.page.wait_for_function("!document.querySelector('#atPermission').disabled")
            chat=self.page.evaluate('curChat')
            self.assertNotEqual(self.page.evaluate('S.active.id'),selected)
            self.page.locator('#ask').fill('GLOBAL_NATIVE_CONFIGURATION')
            self.page.locator('#send').click()
            self.page.wait_for_function("document.querySelector('#msgs').textContent.includes('PROJECT_TASK_REAL_OUTPUT')")
            self.assertEqual(model.requests[-1]['model'],'independent-chat-model')
            with workflow.WorkflowDB(self.database_path) as db:
                saved=db.chat(chat)
                self.assertEqual((saved['reasoning'],saved['permission']),('high','read_only'))
                self.assertFalse(saved['task_id'])
            self.page.locator('#ah-new').click()
            self.page.wait_for_function("!document.querySelector('#ah-new').disabled")
            self.assertEqual(self.page.locator('#modpick').input_value(),'')
            self.assertEqual(self.page.locator('#atPermission').input_value(),'auto')
        self.assertEqual(self.errors,[])

    def test_global_timeline_uses_durable_real_events_and_expands_inline(self):
        self.seed()
        with TrajectoryModelFixture() as model:
            self.use_model(model.url)
            self.page.evaluate("openTab('agent')")
            self.page.locator('#ah-new').click()
            self.page.locator('#ask').fill('TOOL_TRACE inspect actual library')
            self.page.locator('#send').click()
            self.page.wait_for_function("document.querySelector('#msgs').textContent.includes('TRACE_FINAL_FROM_HTTP_FIXTURE')")
            self.page.locator('[data-v="trajectory"]').click()
            row=self.page.locator('[data-wt-record]').filter(has_text='library_overview').first
            row.wait_for();row.click()
            self.assertTrue(self.page.locator('.wt-event.is-expanded [data-wt-inspector]').is_visible())
            self.assertIn('输出结果',self.page.locator('[data-wt-inspector]').inner_text())
            self.page.locator('[data-wt-search]').fill('library_overview')
            self.assertEqual(self.page.locator('[data-wt-record]').count(),1)
            self.assertTrue(self.page.locator('#composer').is_hidden())
            requests=len(model.requests)
            out=Path(web.ROOT)/'.codex-tasks/20261028-workspace-consolidation'
            out.mkdir(parents=True,exist_ok=True)
            self.page.screenshot(path=str(out/'timeline-dark.png'))
            self.page.set_viewport_size({'width':760,'height':800})
            self.page.screenshot(path=str(out/'timeline-narrow.png'))
            self.assertLessEqual(self.page.locator('#main').evaluate('e=>e.scrollWidth-e.clientWidth'),2)
            self.page.reload(wait_until='networkidle')
            self.page.locator('[data-v="trajectory"]').click()
            self.page.locator('[data-wt-record]').filter(has_text='library_overview').first.wait_for()
            self.assertEqual(len(model.requests),requests)
        self.assertEqual(self.errors,[])
