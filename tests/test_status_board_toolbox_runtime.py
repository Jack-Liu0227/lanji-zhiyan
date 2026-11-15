"""Global tools and status colors follow actual project execution in the browser."""
import http.server
import json
from pathlib import Path
import threading
import unittest

from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace


class GatedModel:
    def __enter__(self):
        self.release=threading.Event();self.started=threading.Event();self.requests=[]
        fixture=self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                fixture.requests.append(body)
                if 'WAIT_ONLY_FOR_BOARD' in json.dumps(body):
                    fixture.started.set();fixture.release.wait(25)
                reply='# Evidence report\nSTATUS_BOARD_REAL_OUTPUT\nEvidence and limitations.'
                self.send_response(200);self.send_header('Content-Type','text/event-stream' if body.get('stream') else 'application/json');self.end_headers()
                value=('data: '+json.dumps({'choices':[{'delta':{'content':reply},'finish_reason':'stop'}]})+'\n\ndata: [DONE]\n\n') if body.get('stream') else json.dumps({'choices':[{'message':{'role':'assistant','content':reply},'finish_reason':'stop'}]})
                try:self.wfile.write(value.encode());self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError):pass
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.url=f'http://127.0.0.1:{self.server.server_address[1]}/v1'
        return self
    def __exit__(self,*args):
        self.release.set();self.server.shutdown();self.server.server_close()


@unittest.skipUnless(web.sync_playwright and web.EDGE,'Requires Playwright and Edge')
class StatusBoardToolboxRuntimeTest(unittest.TestCase):
    setUpClass=classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp=web.WebRuntimeTest.setUp
    load_page=web.WebRuntimeTest.load_page
    seed=workspace.ProjectWorkspaceRuntimeTest.seed
    use_model=workspace.ProjectWorkspaceRuntimeTest.use_model
    poll_json=workspace.ProjectWorkspaceRuntimeTest.poll_json

    def screenshot(self,name):
        path=Path(web.ROOT)/'.codex-tasks/20261028-toolbox-status-ui/screenshots'
        path.mkdir(parents=True,exist_ok=True);self.page.screenshot(path=str(path/name))

    def test_outer_toolbox_opens_without_project_and_preserves_tools_and_draft(self):
        self.load_page()
        before=len(self.page.request.get(self.url+'api/state').json()['tasks'])
        self.page.evaluate('()=>{curTask=null;S.tasks=[];}')
        self.page.locator('#sidebar [data-nav="toolbox"]').click()
        self.page.locator('#paperToolboxPage').wait_for()
        self.assertEqual(self.page.locator('#sidebar [data-nav="toolbox"]').get_attribute('aria-current'),'page')
        self.assertEqual(self.page.locator('#projectWorkspace,dialog.project-manager').count(),0)
        self.assertEqual(self.page.locator('.ptb-tools [data-tb-tool]').count(),8)
        self.page.locator('[data-tb-text]').fill('Global toolbox draft')
        self.page.locator('#sidebar [data-nav="agent"]').click()
        self.page.locator('#sidebar [data-nav="toolbox"]').click()
        self.page.locator('#paperToolboxPage').wait_for()
        self.assertEqual(self.page.locator('[data-tb-text]').input_value(),'Global toolbox draft')
        self.page.locator('[data-tb-tool="references"]').click()
        self.page.locator('[data-tb-text]').fill('@article{ion,\n title={Ion transport},\n author={Li},\n year={2026}\n}')
        self.page.locator('[data-tb-run]').click()
        self.page.wait_for_function("document.querySelector('[data-tb-output]').value.includes('保留 1 条')")
        self.page.locator('[data-tb-tool="convert"]').click()
        self.page.locator('[data-tb-format-row]').wait_for(state='visible')
        self.page.locator('[data-tb-tool="pdf_translate"]').click()
        self.assertTrue(self.page.locator('[data-tb-paper-row]').is_visible())
        self.page.locator('[data-tb-tool="references"]').click()
        for theme in ('dark','light'):
            self.page.evaluate('theme=>setTheme(theme)',theme)
            self.screenshot('toolbox-page-'+theme+'.png')
        self.assertEqual(len(self.page.request.get(self.url+'api/state').json()['tasks']),before)
        self.page.set_viewport_size({'width':900,'height':700})
        self.assertTrue(self.page.locator('[data-tb-run]').is_visible())
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        self.page.reload(wait_until='networkidle')
        self.page.locator('#paperToolboxPage').wait_for()
        self.assertEqual(self.errors,[])

    def create_task(self,title,**fields):
        response=self.page.request.post(self.url+'api/project-tasks/create',data={'project_id':self.pid,'title':title,'goal':title,'executor_binding_id':self.binding,**fields})
        self.assertTrue(response.ok,response.text());return response.json()['task']

    def start_task(self,task):
        response=self.page.request.post(self.url+'api/project-tasks/start',data={'project_id':self.pid,'id':task['id']})
        self.assertTrue(response.ok,response.text())
        return response.json()

    def test_live_board_uses_distinct_status_colors_and_moves_after_actual_review(self):
        self.seed()
        with GatedModel() as model:
            self.use_model(model.url)
            ready=self.create_task('待办 · 对比实验条件')
            review=self.create_task('核查 · 提取文献证据');self.start_task(review)
            self.poll_json('/api/project-tasks?project_id='+self.pid,lambda data:any(t['id']==review['id'] and t['status']=='awaiting_review' for t in data['tasks']))
            delivered=self.create_task('完成 · 整理研究笔记',review_required=False);self.start_task(delivered)
            failed=self.create_task('异常 · 缺少交付材料',output_contract={'required_files':['missing-evidence.md']});self.start_task(failed)
            stopped=self.create_task('停止 · 暂缓补充分析')
            self.page.request.post(self.url+'api/project-tasks/cancel',data={'project_id':self.pid,'id':stopped['id']})
            self.poll_json('/api/project-tasks?project_id='+self.pid,lambda data:{t['id']:t['status'] for t in data['tasks']}.get(delivered['id'])=='delivered' and {t['id']:t['status'] for t in data['tasks']}.get(failed['id'])=='failed')
            running=self.create_task('执行 · 对比离子传输机制',goal='WAIT_ONLY_FOR_BOARD 对比离子传输证据');self.start_task(running)
            self.assertTrue(model.started.wait(10))
            web.workspace_section(self.page,'tasks')
            expected={ready['id']:'pending',running['id']:'running',review['id']:'review',delivered['id']:'done',failed['id']:'attention',stopped['id']:'stopped'}
            for identifier,lane in expected.items():self.page.locator('[data-ec-lane="'+lane+'"] [data-ec-task="'+identifier+'"]').wait_for()
            self.page.locator('[data-ec-summary-filter="attention"]').click()
            self.assertEqual(self.page.locator('[data-ec-task]').count(),1)
            self.assertTrue(self.page.locator('[data-ec-task="'+failed['id']+'"]').is_visible())
            self.page.locator('[data-ec-summary-filter="attention"]').click()
            self.page.locator('[data-ec-task="'+ready['id']+'"]').wait_for()
            for theme in ('dark','light'):
                self.page.evaluate('theme=>setTheme(theme)',theme)
                colors=self.page.locator('[data-ec-task] .ec-status').evaluate_all('rows=>rows.map(row=>getComputedStyle(row).color)')
                self.assertEqual(len(set(colors)),6)
                contrasts=self.page.locator('[data-ec-task] .ec-status').evaluate_all('''rows=>rows.map(row=>{const c=document.createElement('canvas');c.width=c.height=1;const ctx=c.getContext('2d');const read=color=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=color;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data].slice(0,3)};const lum=rgb=>rgb.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((n,v,i)=>n+v*[.2126,.7152,.0722][i],0);const s=getComputedStyle(row),a=lum(read(s.color)),b=lum(read(s.backgroundColor));return (Math.max(a,b)+.05)/(Math.min(a,b)+.05)})''')
                self.assertTrue(all(value>=4.5 for value in contrasts),contrasts)
                self.screenshot('execution-status-'+theme+'.png')
            self.page.set_viewport_size({'width':900,'height':700})
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            self.screenshot('execution-status-small.png')
            model.release.set()
            self.page.locator('[data-ec-lane="review"] [data-ec-task="'+running['id']+'"]').wait_for(timeout=30000)
            self.page.locator('[data-ec-lane="review"] [data-ec-task="'+review['id']+'"]').dblclick()
            self.page.locator('[data-execution-detail]').click();self.page.locator('#pwApprove').click()
            web.workspace_section(self.page,'tasks')
            self.page.locator('[data-ec-lane="done"] [data-ec-task="'+review['id']+'"]').wait_for()
            self.assertTrue(model.requests)
        self.assertEqual(self.errors,[])

    def test_todo_only_shows_pending_goals_and_keeps_old_plans_in_history(self):
        self.seed()
        for status,title in [('TODO','文献检索'),('IN_PROGRESS','证据对比'),('REVIEW','核查研究假设'),('DONE','整理阶段结论')]:
            result=self.page.request.post(self.url+'api/plans/save',data={'project_id':self.pid,'title':title,'description':'按研究目标整理可复查的证据与结论。','status':status})
            self.assertTrue(result.ok,result.text())
        web.workspace_section(self.page,'plan')
        self.page.locator('[data-plan-id]').first.wait_for()
        self.assertEqual(self.page.locator('[data-ec-layout]').input_value(),'list')
        self.assertEqual(self.page.locator('[data-plan-id]').count(),1)
        self.assertIn('文献检索',self.page.locator('[data-plan-id]').inner_text())
        self.assertEqual(self.page.locator('[data-ec-column]').count(),0)
        self.page.locator('[data-ec-new]').click()
        self.assertEqual(self.page.locator('dialog [name=status]').count(),0)
        self.page.locator('dialog [data-plan-close]').click()
        self.page.locator('[data-ec-status]').select_option('history')
        self.assertEqual(self.page.locator('[data-plan-id]').count(),3)
        self.assertEqual(len(self.page.request.get(self.url+'api/plans?project_id='+self.pid).json()['plans']),4)
        self.page.locator('[data-ec-new]').click()
        self.page.locator('dialog [name=title]').fill('从历史视图新增的待安排目标')
        self.page.locator('dialog button[type=submit]').click()
        self.page.locator('[data-plan-id]').filter(has_text='从历史视图新增的待安排目标').wait_for()
        self.assertEqual(self.page.locator('[data-ec-status]').input_value(),'pending')
        for theme in ('dark','light'):
            self.page.evaluate('theme=>setTheme(theme)',theme);self.screenshot('plan-status-'+theme+'.png')
        self.assertEqual(self.errors,[])
