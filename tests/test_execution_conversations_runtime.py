"""Real dispatch, expert replies and workflow inspection through execution conversations."""
from pathlib import Path
import unittest
from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace
from tests.test_project_tasks_runtime import TaskModelFixture
import workflow
import workflow_layout
import project_bindings


@unittest.skipUnless(web.sync_playwright and web.EDGE,'Requires Playwright and Edge')
class ExecutionConversationsRuntimeTest(unittest.TestCase):
    setUpClass=classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp=web.WebRuntimeTest.setUp
    load_page=web.WebRuntimeTest.load_page
    seed=workspace.ProjectWorkspaceRuntimeTest.seed
    use_model=workspace.ProjectWorkspaceRuntimeTest.use_model
    poll_json=workspace.ProjectWorkspaceRuntimeTest.poll_json

    def tearDown(self):self.assertEqual(self.errors,[])

    def shot(self,name):
        directory=web.ROOT/'.codex-tasks/20261029-execution-conversations';directory.mkdir(parents=True,exist_ok=True)
        self.page.screenshot(path=str(directory/(name+'.png')))

    def test_dispatch_opens_conversation_and_board_keeps_one_card_after_followup(self):
        self.seed()
        with TaskModelFixture() as model:
            self.use_model(model.url)
            self.page.evaluate('args=>ProjectTasks.compose(args.project,{title:"派发任务预览",goal:"Analyze evidence",executor_binding_id:args.binding})',{'project':self.pid,'binding':self.binding})
            self.page.locator('[data-pt-create-start]').click()
            self.page.locator('[data-execution-id]').wait_for()
            self.page.wait_for_function("document.querySelector('#pwMessages')?.textContent.includes('PROJECT_TASK_REAL_OUTPUT')")
            data=self.page.request.get(self.url+'api/project-tasks?project_id='+self.pid).json()['tasks'][0]
            cid=data['chat_id']
            self.assertEqual(self.page.locator('#pwRecipient').input_value(),'binding:'+self.binding)
            self.assertEqual(self.page.locator('#pwMessages .msg-assistant .msg-meta strong').inner_text(),'课题分析专家')
            self.assertEqual(self.page.locator('#pwMessages .msg-assistant').count(),1)
            self.page.reload(wait_until='networkidle')
            self.page.locator('[data-execution-id]').wait_for()
            self.page.locator('#pwAsk').fill('Explain the saved result')
            self.page.locator('#pwSend').click()
            self.page.wait_for_function("document.querySelectorAll('#pwMessages .msg-assistant').length===2&&document.querySelector('#pwSend').getAttribute('aria-label')==='发送'")
            self.assertEqual(self.page.locator('#pwMessages .msg-assistant .msg-meta strong').all_text_contents(),['课题分析专家','课题分析专家'])
            web.workspace_section(self.page,'tasks')
            card=self.page.locator('[data-ec-task="'+data['id']+'"]');card.wait_for()
            self.assertEqual(self.page.locator('[data-ec-task]').count(),1)
            card.dblclick();self.page.locator('[data-execution-id="'+data['id']+'"]').wait_for()
            self.assertEqual(self.page.locator('#pwMessages').get_attribute('data-chat-id'),cid)
            self.shot('dispatched-task-conversation')

    def test_workflow_conversation_shows_real_tools_experts_and_review_progress(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            workflow_layout.ensure_schema(db)
            db.assistant_save('evidence-reviewer',name='证据审查专家',enabled=1,permission='full_access')
            other=project_bindings.register(db,self.pid,'assistant','evidence-reviewer')['id']
            layout=workflow_layout.save_layout(db,{'name':'对话预览验收流程','nodes':[
                {'id':'read','step':'task','label':'读取项目资料','executor_binding_id':self.binding,'node_kind':'tool','tool_name':'library_overview','tool_args':{},'review_required':True},
                {'id':'verify','step':'task','label':'复核项目证据','executor_binding_id':other,'node_kind':'tool','tool_name':'library_overview','tool_args':{},'review_required':True}],
                'edges':[{'source':'read','target':'verify'}]})['layout']
            workflow_layout.select_layout(db,{'task_id':self.pid,'layout_id':layout['id']})
        with TaskModelFixture() as model:
            self.use_model(model.url)
            response=self.page.request.post(self.url+'api/workflow/execution/prepare',data={'project_id':self.pid,'goal':'检查项目证据','template_id':layout['id']})
            self.assertTrue(response.ok,response.text());run=response.json()['run']
            self.assertTrue(run['chat_id'])
            self.page.evaluate('args=>ProjectWorkspace.openConversation(...args)',[run['chat_id'],self.pid])
            self.page.locator('[data-execution-id]').wait_for()
            self.assertEqual(self.page.locator('#pwScope').input_value(),'project')
            self.page.locator('#pwMessages [data-pw-start]').click()
            for index in range(2):
                current=self.poll_json('/api/project-tasks?project_id='+self.pid+'&workflow_run_id='+run['id'],lambda data:any(t['workflow_node_id']==('read' if index==0 else 'verify') and t['status']=='awaiting_review' for t in data['tasks']))
                task=next(t for t in current['tasks'] if t['workflow_node_id']==('read' if index==0 else 'verify'))
                self.page.wait_for_function('n=>document.querySelectorAll("#pwMessages .tool-card").length===n',arg=index+1)
                self.assertEqual(self.page.locator('#pwMessages .tool-card details[open]').count(),0)
                review=self.page.request.post(self.url+'api/project-tasks/review',data={'project_id':self.pid,'id':task['id'],'approved':True,'reviewed_run_id':task['latest_run']['id'],'reviewed_output_version':0})
                self.assertTrue(review.ok,review.text())
            self.page.wait_for_function("document.querySelector('.execution-chat-summary .pw-state').textContent==='已完成'")
            names=self.page.locator('#pwMessages .msg-assistant .msg-meta strong').all_text_contents()
            self.assertEqual(names,['课题分析专家','证据审查专家'])
            self.page.reload(wait_until='networkidle')
            self.page.wait_for_function("document.querySelectorAll('#pwMessages .tool-card').length===2")
            self.assertEqual(self.page.locator('#pwMessages .msg-assistant').count(),2)
            self.page.locator('#pwMessages .tool-card summary').first.click()
            self.assertIn('输入参数',self.page.locator('#pwMessages .tool-card').first.inner_text())
            self.shot('workflow-conversation-tools')
            self.page.emulate_media(reduced_motion='reduce');self.page.set_viewport_size({'width':900,'height':850});self.page.evaluate("setTheme('light')")
            self.page.locator('[data-execution-id]').wait_for();self.shot('workflow-conversation-light')
            self.assertTrue(self.page.locator('#main').evaluate('e=>e.scrollWidth<=e.clientWidth+1'))

    def test_direct_chat_preserves_each_expert_name_after_switch_and_reload(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            db.assistant_save('caption-expert',name='图注专家',enabled=1,permission='full_access')
            other=project_bindings.register(db,self.pid,'assistant','caption-expert')['id']
        self.page.evaluate('()=>ProjectWorkspace.refresh()')
        with TaskModelFixture() as model:
            self.use_model(model.url)
            for index,binding in enumerate([self.binding,other]):
                self.page.wait_for_function("b=>[...document.querySelector('#pwRecipient').options].some(o=>o.value==='binding:'+b)",arg=binding)
                self.page.locator('#pwRecipient').select_option('binding:'+binding)
                self.page.locator('#pwAsk').fill('Discuss result '+str(index))
                self.page.locator('#pwSend').click()
                self.page.wait_for_function("n=>document.querySelectorAll('#pwMessages .msg-assistant').length===n&&document.querySelector('#pwSend').getAttribute('aria-label')==='发送'",arg=index+1)
            self.page.reload(wait_until='networkidle')
            self.page.wait_for_function("document.querySelectorAll('#pwMessages .msg-assistant').length===2")
            self.assertEqual(self.page.locator('#pwMessages .msg-assistant .msg-meta strong').all_text_contents(),['课题分析专家','图注专家'])
            web.workspace_section(self.page,'tasks')
            self.page.locator('[data-ec-task]').wait_for()
            self.assertEqual(self.page.locator('[data-ec-task]').count(),1)
