"""Natural-language steward tool calling reaches real project workflow delivery."""
import http.server
import json
import threading
import unittest
from pathlib import Path

import workflow
from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace


class StewardModelFixture:
    def __enter__(self):
        self.requests=[];requests=self.requests
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*_args): pass
            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(payload)
                last=payload['messages'][-1]
                call=last.get('role')=='user' and 'SETUP_REQUEST' in str(last.get('content'))
                delta={'tool_calls':[{'index':0,'id':'create-research-project','type':'function','function':{
                    'name':'project_create_from_idea','arguments':json.dumps({'idea':'Controlled ion transport research','title':'Steward research project','request_key':'steward-browser-setup'})}}]} if call else {'content':'CHAT_SETUP_DONE' if last.get('role')=='tool' else '# SETUP_REAL_OUTPUT\nResearch evidence and limitations.'}
                self.send_response(200);self.send_header('Content-Type','text/event-stream' if payload.get('stream') else 'application/json');self.end_headers()
                value={'choices':[{'delta':delta,'finish_reason':'tool_calls' if call else 'stop'}]}
                wire=('data: '+json.dumps(value)+'\n\ndata: [DONE]\n\n') if payload.get('stream') else json.dumps({'choices':[{'message':{'role':'assistant',**delta},'finish_reason':'stop'}]})
                self.wfile.write(wire.encode());self.wfile.flush()
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.url=f'http://127.0.0.1:{self.server.server_address[1]}/v1'
        return self
    def __exit__(self,*_args):self.server.shutdown();self.server.server_close()


@unittest.skipUnless(web.sync_playwright and web.EDGE,'Requires Playwright and Edge')
class ResearchSetupRuntimeTest(unittest.TestCase):
    setUpClass=classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp=web.WebRuntimeTest.setUp
    load_page=web.WebRuntimeTest.load_page
    use_model=workspace.ProjectWorkspaceRuntimeTest.use_model
    poll_json=workspace.ProjectWorkspaceRuntimeTest.poll_json

    def test_global_steward_creates_project_and_real_reviewable_workflow_output(self):
        self.load_page()
        with StewardModelFixture() as model:
            with workflow.WorkflowDB(self.database_path) as db:
                db.add_model('Steward fixture',model.url,'steward-fixture','fixture-only-key',activate=True)
            self.page.evaluate('async()=>await refreshState(false)')
            self.page.evaluate("openTab('agent')")
            self.page.locator('#ah-new').click()
            self.page.wait_for_function("!document.querySelector('#ah-new').disabled&&!document.querySelector('#ask').disabled")
            self.page.locator('#ask').fill('SETUP_REQUEST')
            self.page.locator('#send').click()
            try:self.page.wait_for_function("document.querySelector('#msgs').textContent.includes('CHAT_SETUP_DONE')")
            except Exception:
                self.fail('Steward chat did not converge: '+self.page.locator('#msgs').inner_text()+' Last messages: '+json.dumps([request['messages'][-1] for request in model.requests],ensure_ascii=False)[:2500])
            button=self.page.locator('[data-steward-project]');button.wait_for()
            pid=button.get_attribute('data-steward-project')
            run=self.poll_json('/api/workflow/execution?task_id='+pid,lambda data:(data.get('run') or {}).get('status') in ('awaiting_review','failed'))['run']
            self.assertEqual(run['status'],'awaiting_review',json.dumps({'error':run.get('error'),'nodes':[{'status':node.get('status'),'error':node.get('error'),'checkpoint':str(node.get('checkpoint'))[-1000:]} for node in run['nodes']]},ensure_ascii=False))
            tasks=self.page.request.get(self.url+'api/project-tasks?project_id='+pid).json()['tasks']
            first=next(task for task in tasks if task['status']=='awaiting_review')
            self.assertIn('SETUP_REAL_OUTPUT',Path(first['outputs'][-1]['path']).read_text(encoding='utf-8'))
            self.assertEqual(len(tasks),4)
            self.assertIn('[Active skill: research-steward]',json.dumps(model.requests[0],ensure_ascii=False))
            button.click()
            self.page.locator('.project-workflow-graph').wait_for()
            self.assertEqual(self.page.locator('#pwProject').input_value(),pid)
            with workflow.WorkflowDB(self.database_path) as db:
                row=db.db.execute('SELECT result FROM research_setups WHERE project_id=?',(pid,)).fetchone()
                config=json.loads(row['result'])
                self.assertEqual(len(db.team_members(config['team_id'])),4)
        self.assertEqual(self.errors,[])

    def test_existing_project_full_access_runs_all_four_nodes_with_real_team_member(self):
        import project_bindings
        with workflow.WorkflowDB(self.database_path) as db:
            pid=db.create_task('Automatic project')
            project_bindings.save_settings(db,pid,'full_access')
        self.load_page()
        with StewardModelFixture() as model:
            with workflow.WorkflowDB(self.database_path) as db:
                db.add_model('Automatic fixture',model.url,'automatic-fixture','fixture-only-key',activate=True)
            response=self.page.request.post(self.url+'api/langgraph/create-from-idea',data={
                'idea':'Analyze transport and deliver the research proposal','project':pid,'request_key':'automatic-setup'})
            self.assertTrue(response.ok,response.text())
            setup=response.json()
            self.assertEqual(setup['project_id'],pid)
            run=self.poll_json('/api/workflow/execution?task_id='+pid,lambda data:(data.get('run') or {}).get('status') in ('succeeded','failed'))['run']
            self.assertEqual(run['status'],'succeeded',{'error':run.get('error'),'nodes':[(node['id'],node.get('error')) for node in run['nodes']]})
            tasks=self.page.request.get(self.url+'api/project-tasks?project_id='+pid).json()['tasks']
            self.assertEqual(len(tasks),4)
            self.assertTrue(all(task['status']=='delivered' and task['outputs'] for task in tasks))
            self.assertTrue(any(task.get('member_id') for task in tasks))
            for task in tasks:
                self.assertIn('SETUP_REAL_OUTPUT',Path(task['outputs'][-1]['path']).read_text(encoding='utf-8'))
            again=self.page.request.post(self.url+'api/langgraph/create-from-idea',data={
                'idea':'Analyze transport and deliver the research proposal','project':pid,'request_key':'automatic-setup'}).json()
            self.assertTrue(again['reused'])
            self.assertEqual(again['run_id'],setup['run_id'])
        self.assertEqual(self.errors,[])
