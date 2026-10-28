"""Real browser paths for conversation choices, durable traces, editable graphs and human revisions."""
import http.server
import json
from pathlib import Path
import threading
import unittest

from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace
from tests import test_project_tasks_runtime as task_runtime
import workflow
import workflow_layout


class TrajectoryModelFixture:
    def __enter__(self):
        self.requests=[];requests=self.requests
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(payload)
                last=payload['messages'][-1]
                call=last.get('role')=='user' and 'TOOL_TRACE' in str(last.get('content'))
                delta={'tool_calls':[{'index':0,'id':'real-library-call','type':'function','function':{'name':'library_overview','arguments':'{}'}}]} if call else {'content':'TRACE_FINAL_FROM_HTTP_FIXTURE'}
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                self.wfile.write(('data: '+json.dumps({'choices':[{'delta':delta,'finish_reason':'tool_calls' if call else 'stop'}]})+'\n\ndata: [DONE]\n\n').encode());self.wfile.flush()
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.url=f'http://127.0.0.1:{self.server.server_address[1]}/v1'
        return self
    def __exit__(self,*args):self.server.shutdown();self.server.server_close()


@unittest.skipUnless(web.sync_playwright and web.EDGE,'Requires Playwright and Edge')
class WorkspaceGraphRuntimeTest(unittest.TestCase):
    setUpClass=classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp=web.WebRuntimeTest.setUp
    load_page=web.WebRuntimeTest.load_page
    seed=workspace.ProjectWorkspaceRuntimeTest.seed
    use_model=workspace.ProjectWorkspaceRuntimeTest.use_model
    poll_json=workspace.ProjectWorkspaceRuntimeTest.poll_json

    def flow(self):
        with workflow.WorkflowDB(self.database_path) as db:
            workflow_layout.ensure_schema(db)
            layout=workflow_layout.save_layout(db,{'name':'Editable project flow','nodes':[
                {'id':'analyze','step':'task','label':'Analyze evidence','prompt':'FIRST_GRAPH_TASK','executor_binding_id':self.binding,'review_required':True},
                {'id':'check','step':'task','label':'Check evidence','prompt':'SECOND_GRAPH_TASK','executor_binding_id':self.binding,'review_required':True}],
                'edges':[{'source':'analyze','target':'check'}]})['layout']
            workflow_layout.select_layout(db,{'task_id':self.pid,'layout_id':layout['id']})
        web.workspace_section(self.page,'workflow')
        self.page.locator('[data-pwg-label]').wait_for()
        return layout

    def screenshot(self,name):
        out=Path(web.ROOT)/'.codex-tasks/20261027-node-workflow-trajectory/screenshots'
        out.mkdir(parents=True,exist_ok=True);self.page.screenshot(path=str(out/name))

    def test_conversation_model_and_permission_save_without_changing_other_conversation(self):
        self.seed()
        with task_runtime.TaskModelFixture() as model:
            self.use_model(model.url)
            with workflow.WorkflowDB(self.database_path) as db:
                db.add_model('Second browser model',model.url,'second-browser-model','fixture-only')
                second=next(item['id'] for item in db.models() if item['name']=='Second browser model')
            self.page.evaluate('async()=>{await refreshState(false);await ProjectWorkspace.refresh();}')
            self.page.locator('#pwModel').select_option(second)
            self.page.wait_for_function("!document.querySelector('#pwModel').disabled")
            self.page.locator('#pwPermission').select_option('read_only')
            self.page.wait_for_function("!document.querySelector('#pwPermission').disabled")
            first=self.page.locator('[data-pw-chat][aria-current="true"]').get_attribute('data-pw-chat')
            self.page.locator('#pwNewChat').click()
            self.page.wait_for_function("!document.querySelector('#pwNewChat').disabled")
            self.assertEqual(self.page.locator('#pwModel').input_value(),'')
            self.assertEqual(self.page.locator('#pwPermission').input_value(),'auto')
            self.page.locator(f'[data-pw-chat="{first}"]').click()
            self.assertEqual(self.page.locator('#pwModel').input_value(),second)
            self.assertEqual(self.page.locator('#pwPermission').input_value(),'read_only')
            self.page.locator('#pwAsk').fill('CONVERSATION_WITH_LOCAL_MODEL')
            self.page.locator('#pwSend').click()
            self.page.wait_for_function("document.querySelector('#pwMessages').textContent.includes('PROJECT_TASK_REAL_OUTPUT')")
            self.assertEqual(model.requests[-1]['model'],'second-browser-model')
        self.assertEqual(self.errors,[])

    def test_actual_tool_trace_can_be_inspected_and_reloaded_without_reexecution(self):
        self.seed()
        with TrajectoryModelFixture() as model:
            self.use_model(model.url)
            self.page.locator('#pwAsk').fill('TOOL_TRACE inspect actual project library')
            self.page.locator('#pwSend').click()
            self.page.wait_for_function("document.querySelector('#pwMessages').textContent.includes('TRACE_FINAL_FROM_HTTP_FIXTURE')")
            self.page.locator('#pwTraceView').click()
            row=self.page.locator('[data-wt-record]').filter(has_text='library_overview').first
            row.wait_for();row.click()
            self.assertIn('输入参数',self.page.locator('[data-wt-inspector]').inner_text())
            self.assertIn('输出结果',self.page.locator('[data-wt-inspector]').inner_text())
            self.assertNotIn('未记录',row.locator('.wt-duration').inner_text())
            self.assertTrue(self.page.locator('#pwAsk').is_hidden())
            count=len(model.requests);self.screenshot('trajectory-real-tool.png')
            self.page.reload(wait_until='networkidle')
            self.page.locator('#pwTraceView').click()
            self.page.locator('[data-wt-record]').filter(has_text='library_overview').first.wait_for()
            self.assertEqual(len(model.requests),count)
        self.assertEqual(self.errors,[])

    def test_graph_node_editing_and_running_snapshot_are_independent(self):
        self.seed();layout=self.flow()
        self.page.locator('[data-wfl-node="check"]').click()
        self.page.locator('[data-pwg-prompt]').fill('GRAPH_CHANGED_INSTRUCTION')
        self.page.locator('[data-pwg-save]').click()
        self.page.wait_for_function("document.querySelector('[data-pwg-notice]').textContent.includes('已保存')")
        self.page.locator('[data-pwg-add]').click()
        self.page.locator('[data-pwg-label]').fill('Final human check')
        self.page.locator('[data-pwg-prompt]').fill('THIRD_GRAPH_TASK')
        self.page.locator('[data-pwg-binding]').select_option(self.binding)
        self.page.locator('[data-pwg-parent="check"]').check()
        self.page.locator('[data-pwg-save]').click()
        self.page.wait_for_function("document.querySelector('[data-pwg-notice]').textContent.includes('已保存')")
        self.assertEqual(self.page.locator('[data-wfl-node]').count(),3)
        targets=self.page.request.get(self.url+'api/handoff/targets?project_id='+self.pid)
        self.assertTrue(targets.ok,targets.text())
        self.assertEqual(len(targets.json()['nodes']),3)
        self.page.locator('#pwWorkflowRun').click()
        self.page.locator('[data-sequence-prepare]').click()
        run=self.poll_json('/api/workflow/execution?task_id='+self.pid,lambda data:bool((data.get('run') or {}).get('settings',{}).get('task_ids')))['run']
        self.page.locator('[data-sequence-close]').click()
        self.page.locator('[data-pwg-mode="run"]:not(:disabled)').wait_for()
        self.page.locator('[data-wfl-node="check"]').click()
        self.page.locator('[data-pwg-prompt]').fill('NEXT_RUN_ONLY_INSTRUCTION')
        self.page.locator('[data-pwg-save]').click()
        self.page.wait_for_function("document.querySelector('[data-pwg-notice]').textContent.includes('已保存')")
        frozen=self.page.request.get(self.url+'api/workflow/execution?id='+run['id']).json()['run']['layout']
        self.assertEqual(next(node for node in frozen['nodes'] if node['id']=='check')['prompt'],'GRAPH_CHANGED_INSTRUCTION')
        self.page.locator('[data-pwg-mode="run"]').click()
        self.page.locator('[data-wfl-node="check"]').click()
        self.assertIn('GRAPH_CHANGED_INSTRUCTION',self.page.locator('[data-pwg-inspector]').inner_text())
        self.assertEqual(self.page.locator('[data-pwg-prompt]').count(),0)
        with workflow.WorkflowDB(self.database_path) as db:
            self.assertEqual(len(workflow_layout._layout(db,layout['id'])['nodes']),2)
        self.screenshot('workflow-running-snapshot.png');self.assertEqual(self.errors,[])

    def test_graph_failed_save_blocks_view_change_and_keeps_draft(self):
        self.seed();self.flow()
        self.page.locator('[data-pwg-prompt]').fill('KEEP_UNSAVED_NODE_DRAFT')
        self.page.route('**/api/workflow/layouts/project',lambda route:route.fulfill(status=409,json={'error':'NODE_SAVE_FAILED'}))
        self.page.locator('#pwTab-library').click()
        self.page.wait_for_function("document.querySelector('[data-pwg-error]').textContent.includes('NODE_SAVE_FAILED')")
        self.assertEqual(self.page.locator('#pwTab-flow').get_attribute('aria-selected'),'true')
        self.assertEqual(self.page.locator('[data-pwg-prompt]').input_value(),'KEEP_UNSAVED_NODE_DRAFT')
        self.page.unroute('**/api/workflow/layouts/project')
        self.page.locator('#pwTab-library').click()
        self.page.locator('.pr-catalog').wait_for()
        self.assertEqual(self.errors,[])

    def test_graph_dependencies_can_reverse_without_a_cycle(self):
        self.seed();layout=self.flow()
        self.page.locator('[data-wfl-node="check"]').click()
        self.page.locator('[data-pwg-parent="analyze"]').uncheck()
        self.page.locator('[data-pwg-save]').click()
        self.page.wait_for_function("document.querySelector('[data-pwg-notice]').textContent.includes('已保存')")
        self.page.locator('[data-wfl-node="analyze"]').click()
        self.page.locator('[data-pwg-parent="check"]').check()
        self.page.locator('[data-pwg-save]').click()
        self.page.wait_for_function("document.querySelector('[data-pwg-notice]').textContent.includes('已保存')")
        current=self.page.request.get(self.url+'api/workflow/layouts/project?project_id='+self.pid).json()['layout']
        self.assertEqual(current['order'],['check','analyze'])
        self.assertEqual(current['edges'],[{'source':'check','target':'analyze'}])
        self.page.locator('#pwRoles').click()
        self.page.locator('[data-pt-role-template] option[value="'+layout['id']+'"]').wait_for(state='attached')
        self.assertEqual(self.page.locator('[data-pt-role-template]').input_value(),layout['id'])
        self.assertEqual(self.errors,[])

    def test_node_output_human_revision_review_and_downstream_execution(self):
        self.seed();self.flow()
        with task_runtime.TaskModelFixture() as model:
            self.use_model(model.url)
            self.page.locator('#pwWorkflowRun').click()
            self.page.locator('[data-sequence-start]').click()
            self.poll_json('/api/workflow/execution?task_id='+self.pid,lambda data:(data.get('run') or {}).get('status')=='awaiting_review')
            self.page.locator('[data-sequence-close]').click()
            self.page.locator('[data-pwg-task]').wait_for()
            self.page.locator('[data-pwg-task]').click()
            self.page.locator('[data-pw-output-edit]').last.wait_for()
            tasks=self.page.request.get(self.url+'api/project-tasks?project_id='+self.pid).json()['tasks']
            first=next(task for task in tasks if task['workflow_node_id']=='analyze')
            original=Path(first['outputs'][-1]['path']);original_text=original.read_text(encoding='utf-8')
            self.page.locator('[data-pw-output-edit]').last.click()
            self.page.locator('[data-por-text]:not(:disabled)').fill('# HUMAN_BROWSER_CORRECTION\nReviewed evidence for downstream node.')
            self.page.locator('[data-por-reason]').fill('Browser human verification')
            self.page.locator('[data-por-save]').click()
            self.page.locator('.output-revision-dialog').wait_for(state='detached')
            self.page.get_by_text('人工修订 v1 · 核查以当前产物为准',exact=True).wait_for()
            self.assertEqual(original.read_text(encoding='utf-8'),original_text)
            self.page.locator('#pwFeedback').fill('HUMAN_REVIEW_OF_REVISION_ONE')
            self.screenshot('node-human-revision-review.png')
            self.page.locator('#pwApprove').click()
            data=self.poll_json('/api/project-tasks?project_id='+self.pid,lambda data:any(task['workflow_node_id']=='check' and task['status']=='awaiting_review' for task in data['tasks']))
            first=next(task for task in data['tasks'] if task['workflow_node_id']=='analyze')
            self.assertEqual(first['status'],'delivered')
            self.assertEqual(first['deliveries'][-1]['source'],'human')
            self.assertEqual(first['latest_run']['checkpoint']['output_revision'],1)
            self.assertIn('HUMAN_BROWSER_CORRECTION',json.dumps(model.requests[-1],ensure_ascii=False))
        self.assertEqual(self.errors,[])

    def test_task_center_opens_the_same_project_task_without_duplicate_creation(self):
        self.seed()
        task=self.page.evaluate("async data=>(await api('/api/project-tasks/create',{project_id:data.pid,goal:'CANONICAL_CENTER_TASK',executor_binding_id:data.binding})).task",{'pid':self.pid,'binding':self.binding})
        self.page.evaluate("openTab('task-center')")
        self.page.locator('[data-ec-view="board"][aria-pressed="true"]').wait_for()
        self.page.locator('[data-ec-task="'+task['id']+'"]').dblclick()
        self.page.locator('[data-execution-detail]').click()
        self.page.locator('.pw-task-detail').wait_for()
        self.assertEqual(self.page.locator('#pwProject').input_value(),self.pid)
        self.assertIn('CANONICAL_CENTER_TASK',self.page.locator('#pwResource').inner_text())
        rows=self.page.request.get(self.url+'api/project-tasks?project_id='+self.pid).json()['tasks']
        self.assertEqual([row['id'] for row in rows],[task['id']])
        self.assertEqual(self.errors,[])

    def test_graph_and_trace_controls_fit_small_window(self):
        self.seed();self.flow()
        self.page.set_viewport_size({'width':760,'height':900})
        self.page.wait_for_function("document.querySelector('#projectWorkspace').classList.contains('pw-narrow')")
        self.page.locator('[data-pwg-zoom="fit"]').click()
        self.page.locator('[data-pwg-label]').fill('Small window node')
        self.page.locator('[data-pwg-save]').click()
        self.page.wait_for_function("document.querySelector('[data-pwg-notice]').textContent.includes('已保存')")
        widths=self.page.locator('#main').evaluate('e=>[e.clientWidth,e.scrollWidth]')
        self.assertLessEqual(widths[1],widths[0]+2)
        toolbar=self.page.locator('.pwg-toolbar').bounding_box()
        resource=self.page.locator('#pwResource').bounding_box()
        self.assertGreaterEqual(toolbar['y'],resource['y']-1)
        heights=self.page.locator('#pwResource').evaluate('e=>[e.clientHeight,e.scrollHeight]')
        self.assertLessEqual(heights[1],heights[0]+2)
        self.screenshot('workflow-narrow.png')
        self.assertEqual(self.errors,[])


if __name__=='__main__':unittest.main()
