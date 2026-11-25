"""User-visible conversation navigation, live updates and execution presentation."""
from pathlib import Path
import unittest

from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace
import workflow


@unittest.skipUnless(web.sync_playwright and web.EDGE, 'Requires Playwright and Edge')
class ChatReadingRuntimeTest(unittest.TestCase):
    setUpClass = classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp = web.WebRuntimeTest.setUp
    load_page = web.WebRuntimeTest.load_page
    seed = workspace.ProjectWorkspaceRuntimeTest.seed

    def open_history(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            self.chat = db.create_chat('对话阅读与导航验收', task_id=self.pid)
            for index in range(12):
                question = '请核对论文中的证据' if index in (0, 2) else f'第 {index + 1} 次提问：补充实验与文献依据'
                db.add_message(self.chat, 'user', question)
                events = [
                    {'type': 'tool_call', 'id': f'tool-{index}', 'name': 'library_overview', 'args': {}},
                    {'type': 'tool_result', 'id': f'tool-{index}', 'name': 'library_overview',
                     'ok': True, 'duration_ms': 97300, 'summary': '完成', 'result': {'evidence': index + 1}},
                ]
                db.add_message(self.chat, 'assistant', '\n\n'.join([
                    f'第 {index + 1} 轮的证据与实验结果。已核对原始文献，保留研究方法、样本范围和结论限制。'
                ] * 5), events, sender='课题分析专家')
        self.page.evaluate('args=>ProjectWorkspace.openConversation(...args)', [self.chat, self.pid])
        self.page.wait_for_function('document.querySelectorAll(".chat-turn-node").length===12')
        self.page.emulate_media(reduced_motion='reduce')

    def shot(self, name):
        folder = Path(web.ROOT) / '.codex-tasks/chat-presentation-20261124/visual'
        folder.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(folder / (name + '.png')))

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def test_live_card_follows_active_run_and_keeps_explicit_history_selection(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            chat = db.create_chat('Live workflow selection', task_id=self.pid)
        def run(run_id, status):
            return {'id': run_id, 'status': status, 'layout_name': run_id, 'live': {
                'id': run_id, 'name': run_id, 'status': status, 'started_at': '2026-01-01T00:00:00Z',
                'observed_at': '2026-01-01T00:02:00Z', 'total_count': 5, 'delivered_count': 0,
                'nodes': [{'node_id': 'A', 'label': 'A 检索筛选', 'status': 'running',
                           'monitoring': {'phase': 'model', 'worker_alive': True,
                                          'phase_started_at': '2026-01-01T00:01:00Z'}}]
                    if status == 'running' else []}}
        context = {'chat_id': chat, 'run_ids': ['old'], 'task_ids': [], 'runs': [run('old', 'cancelled')]}
        self.page.route('**/api/agent-settings/workflow-context?**', lambda route: route.fulfill(json=context))
        self.page.evaluate('args=>ProjectWorkspace.openConversation(...args)', [chat, self.pid])
        self.page.wait_for_function('document.querySelector("#pwWorkflowLive").innerText.includes("old")')
        context['runs'].insert(0, run('active', 'running'))
        context['run_ids'].insert(0, 'active')
        self.page.evaluate('ProjectWorkspace.refresh()')
        self.page.wait_for_function('document.querySelector("#pwWorkflowLive strong").textContent==="active"')
        self.assertTrue(self.page.locator('.pw-live-glance').is_visible())
        self.assertIn('等待模型响应', self.page.locator('.pw-live-glance').inner_text())
        self.assertIn('本阶段已用', self.page.locator('[data-live-glance-clock]').inner_text())
        self.assertIn('后台心跳正常', self.page.locator('.pw-live-glance').inner_text())
        self.page.locator('#pwWorkflowLive summary').click()
        self.page.locator('[data-live-select]').select_option('old')
        self.page.wait_for_function('document.querySelector("#pwWorkflowLive strong").textContent==="old"')
        self.page.evaluate('ProjectWorkspace.refresh()')
        self.assertEqual(self.page.locator('#pwWorkflowLive strong').first.inner_text(), 'old')
        self.assertIn('1 条运行需关注', self.page.locator('[data-live-active]').inner_text())
        self.page.reload(wait_until='networkidle')
        self.page.wait_for_function('document.querySelector("#pwWorkflowLive strong").textContent==="old"')
        self.page.locator('[data-live-active]').click()
        self.page.wait_for_function('document.querySelector("#pwWorkflowLive strong").textContent==="active"')

    def test_live_card_does_not_label_failed_nodes_as_running(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            chat = db.create_chat('Interrupted workflow recovery', task_id=self.pid)
        run = {'id': 'interrupted-run', 'status': 'interrupted', 'layout_name': 'Interrupted review', 'live': {
            'id': 'interrupted-run', 'name': 'Interrupted review', 'status': 'interrupted',
            'started_at': '2026-01-01T00:00:00Z', 'observed_at': '2026-01-01T00:03:00Z',
            'total_count': 5, 'delivered_count': 1, 'review_count': 0,
            'nodes': [
                {'node_id': 'B', 'label': 'Step 2.1: 机制证据', 'status': 'failed', 'error': 'artifact mismatch'},
                {'node_id': 'C', 'label': 'Step 2.2: 方法比较', 'status': 'failed', 'error': 'CSV contract'},
            ]}}
        context = {'chat_id': chat, 'run_ids': [run['id']], 'task_ids': [], 'runs': [run]}
        self.page.route('**/api/agent-settings/workflow-context?**', lambda route: route.fulfill(json=context))
        self.page.evaluate('args=>ProjectWorkspace.openConversation(...args)', [chat, self.pid])
        self.page.wait_for_function('document.querySelector("#pwWorkflowLive").dataset.status==="interrupted"')
        glance = self.page.locator('.pw-live-glance')
        self.assertIn('artifact mismatch', glance.inner_text())
        self.assertIn('另有 1 个节点待恢复', glance.inner_text())
        self.assertNotIn('节点进行中', glance.inner_text())
        self.page.locator('#pwWorkflowLive summary').click()
        self.assertEqual(self.page.locator('.pw-live-node[data-status="failed"]').count(), 2)
        self.assertEqual(self.errors, [])

    def test_user_nodes_jump_without_streams_stealing_position_and_survive_reload(self):
        self.open_history()
        self.assertEqual(self.page.locator('#pwMessages .msg-user').count(), 12)
        self.assertEqual(self.page.locator('#pwMessages .chat-process:not([open])').count(), 12)
        self.page.locator('#pwAsk').fill('保留这份尚未发送的草稿')
        node = self.page.locator('.chat-turn-node').nth(2)
        node.focus()
        self.assertIn('第 3 次提问', self.page.locator('.chat-turn-preview').inner_text())
        node.press('Enter')
        self.page.wait_for_function('document.querySelectorAll("#pwMessages .msg-user")[2].classList.contains("chat-jump-highlight")')
        self.assertEqual(self.page.locator('.chat-turn-node[aria-current]').get_attribute('data-chat-turn'),
                         node.get_attribute('data-chat-turn'))
        top = self.page.locator('#pwMessages').evaluate('e=>e.scrollTop')
        self.page.evaluate("""()=>{
          const box=document.querySelector('#pwMessages');
          appendOne(box,{type:'assistant_delta',message_id:'live-reading',content:'新增的回复内容。\\n'.repeat(80)});
        }""")
        self.page.wait_for_function("document.querySelector('[data-chat-latest]').textContent.includes('有新回复')")
        self.assertAlmostEqual(self.page.locator('#pwMessages').evaluate('e=>e.scrollTop'), top, delta=2)
        self.assertEqual(self.page.locator('#pwAsk').input_value(), '保留这份尚未发送的草稿')
        self.shot('history-navigation-dark')
        self.page.locator('[data-chat-latest]').click()
        self.page.wait_for_function('(()=>{const e=document.querySelector("#pwMessages");return e.scrollHeight-e.clientHeight-e.scrollTop<3})()')
        self.page.locator('.chat-turn-node').first.focus()
        self.page.keyboard.press('ArrowDown')
        self.assertEqual(self.page.locator('.chat-turn-node:focus').get_attribute('data-chat-turn'),
                         self.page.locator('.chat-turn-node').nth(1).get_attribute('data-chat-turn'))
        self.page.reload(wait_until='networkidle')
        self.page.wait_for_function('document.querySelectorAll(".chat-turn-node").length===12')
        self.assertEqual(self.page.locator('#pwAsk').input_value(), '保留这份尚未发送的草稿')
        self.page.locator('#pwNewChat').click()
        self.page.wait_for_function('document.querySelectorAll(".chat-turn-node").length===0')
        self.page.wait_for_function('!document.querySelector("#pwNewChat").disabled')
        self.page.locator('#pwSessions [data-pw-chat="' + self.chat + '"]').click()
        self.page.wait_for_function('document.querySelectorAll(".chat-turn-node").length===12')
        self.page.set_viewport_size({'width': 900, 'height': 850})
        self.page.evaluate("setTheme('light')")
        self.assertTrue(self.page.locator('#main').evaluate('e=>e.scrollWidth<=e.clientWidth+1'))
        self.shot('history-navigation-light-compact')

    def test_parallel_tools_failures_retries_and_stopped_clocks(self):
        self.open_history()
        self.page.evaluate("""()=>{
          const box=document.querySelector('#pwMessages');
          ChatPresentation.replace(box,'tool-fixture',()=>{
            box.replaceChildren();
            appendOne(box,{type:'user',id:'question',content:'检索并核对资料',at:new Date(Date.now()-98000).toISOString()});
            appendOne(box,{type:'chat_waiting'});
            appendOne(box,{type:'tool_call',id:'same-id',sub:'a',name:'paper_download',args:{url:'a'}});
            appendOne(box,{type:'tool_call',id:'same-id',sub:'b',name:'paper_download',args:{url:'b'}});
            appendOne(box,{type:'tool_result',id:'same-id',sub:'a',name:'paper_download',ok:false,summary:'下载失败，服务返回 403',duration_ms:200});
            appendOne(box,{type:'tool_result',id:'same-id',sub:'b',name:'paper_download',ok:true,summary:'完成',result:'B_RESULT',duration_ms:97300});
          });
        }""")
        self.assertEqual(self.page.locator('.tool-card.bad[data-sub-id="a"]').count(), 1)
        self.assertEqual(self.page.locator('.tool-card[data-sub-id="b"] .tool-output pre').text_content(), 'B_RESULT')
        self.assertEqual(self.page.locator('.tool-card[data-sub-id="b"] .tool-dur').text_content(), '已完成 · 1 分 37 秒')
        self.assertEqual(self.page.locator('.tool-card[data-sub-id="b"] .tool-detail').text_content(), '')
        self.assertTrue(self.page.locator('.chat-process').evaluate('e=>e.open'))
        self.page.evaluate("""()=>{
          const box=document.querySelector('#pwMessages');
          appendOne(box,{type:'tool_call',id:'retry',sub:'a',name:'paper_download',args:{url:'a'}});
          appendOne(box,{type:'tool_result',id:'retry',sub:'a',name:'paper_download',ok:true,summary:'完成',result:'A_RESULT'});
          appendOne(box,{type:'tool_call',id:'pending',name:'file_list',args:{}});
        }""")
        self.assertEqual(self.page.locator('.tool-card.recovered .tool-dur').text_content(), '先前失败 · 重试成功')
        self.page.wait_for_function("document.querySelector('.chat-activity-time').textContent.includes('1 分')")
        self.page.evaluate("appendOne(document.querySelector('#pwMessages'),{type:'cancelled'})")
        self.assertEqual(self.page.locator('#pwMessages [data-chat-clock-start]').count(), 0)
        self.assertEqual(self.page.locator('.chat-activity').count(), 0)
        self.assertIn('已停止', self.page.locator('.chat-process > summary').inner_text())
        self.assertIn('重试成功', self.page.locator('.chat-process > summary').inner_text())
        self.assertEqual(self.page.locator('.tool-card[data-call-id="pending"] .tool-dur').text_content(), '已停止')
        self.shot('tools-failed-recovered-stopped')

    def test_execution_refresh_keeps_anchor_details_and_server_elapsed_time(self):
        self.open_history()
        self.page.evaluate("""()=>{
          const box=document.querySelector('#pwMessages'), start=new Date(Date.now()-125000).toISOString();
          window.readingExecution={execution:{chat_id:'execution-reading',entity_id:'reading',kind:'workflow',project_id:curTask,title:'长任务执行验收',status:'running',children:[]},messages:[
            {id:'goal',role:'user',content:'按计划逐项检索文献',created_at:start},
            {id:'step',role:'assistant',content:'',status:'running',created_at:start,started_at:start,task_title:'检索文献',attempt:1,events:[
              {type:'tool_call',id:'saved-tool',name:'library_overview',args:{},at:start},
              {type:'tool_result',id:'saved-tool',name:'library_overview',ok:true,duration_ms:100,summary:'完成',result:'SAVED_EVIDENCE'}
            ]},
            ...Array.from({length:10},(_,i)=>({id:'saved-'+i,role:i%2?'assistant':'user',content:('已保存的研究记录 '+i+'。\\n').repeat(12),created_at:start}))
          ]};
          ExecutionConversation.render(box,readingExecution,()=>{});
        }""")
        self.page.wait_for_function('document.querySelectorAll(".chat-turn-node").length===6')
        self.page.wait_for_function("document.querySelector('.chat-activity-time').textContent.includes('2 分')")
        self.page.locator('.chat-turn-node').first.click()
        self.page.locator('.chat-process > summary').click()
        self.page.locator('.tool-card summary').click()
        self.page.locator('.tool-output pre').wait_for(state='visible')
        top = self.page.locator('#pwMessages').evaluate('e=>e.scrollTop')
        self.page.evaluate("""()=>{
          readingExecution.messages.push({id:'new-output',role:'assistant',content:'新增输出。'.repeat(60)});
          ExecutionConversation.render(document.querySelector('#pwMessages'),readingExecution,()=>{});
        }""")
        self.assertTrue(self.page.locator('.chat-process').evaluate('e=>e.open'))
        self.assertTrue(self.page.locator('.tool-card details').evaluate('e=>e.open'))
        self.assertAlmostEqual(self.page.locator('#pwMessages').evaluate('e=>e.scrollTop'), top, delta=2)
        self.assertEqual(self.page.locator('#pwMessages .msg-assistant').filter(has_text='进行中').count(), 0)
        self.page.evaluate("""()=>{
          readingExecution.execution.status='awaiting_review';
          readingExecution.messages[1].status='awaiting_review';
          readingExecution.messages[1].ended_at=new Date(Date.parse(readingExecution.messages[1].started_at)+124000).toISOString();
          readingExecution.messages[1].content='已完成检索，等待核查资料。';
          ExecutionConversation.render(document.querySelector('#pwMessages'),readingExecution,()=>{});
        }""")
        self.assertEqual(self.page.locator('#pwMessages [data-chat-clock-start]').count(), 0)
        self.assertEqual(self.page.locator('.chat-action-state').text_content(), '等待人工核查')
        self.assertIn('用时 2 分 4 秒', self.page.locator('.chat-process > summary').inner_text())
        self.assertEqual(self.page.locator('.execution-chat-turn[data-execution-message="step"] .msg-elapsed').text_content(), '用时 2 分 4 秒')
        self.shot('execution-waiting-review')
        self.page.evaluate("""()=>{
          const box=document.querySelector('#pwMessages');box.replaceChildren();
          readingExecution.execution.chat_id='execution-failure';
          readingExecution.execution.status='failed';
          readingExecution.messages=readingExecution.messages.slice(0,2);
          readingExecution.messages[1].status='failed';
          readingExecution.messages[1].events[1].ok=false;
          readingExecution.messages[1].events[1].summary='外部资料服务暂不可用';
          ExecutionConversation.render(box,readingExecution,()=>{});
        }""")
        self.assertTrue(self.page.locator('.chat-process').evaluate('e=>e.open'))
        self.assertIn('外部资料服务暂不可用', self.page.locator('.tool-card').inner_text())
        self.assertEqual(self.page.locator('#pwMessages [data-chat-clock-start]').count(), 0)
