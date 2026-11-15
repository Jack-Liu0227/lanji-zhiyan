"""Regressions from the full frontend audit, using only disposable projects."""
from pathlib import Path
import unittest
from tests.test_web_runtime import workspace_section
from urllib.parse import urlsplit,parse_qs
from tests import test_web_runtime as web
import workflow
import project_bindings


@unittest.skipUnless(web.sync_playwright and web.EDGE,'Requires Playwright and Edge')
class FrontendAuditRuntimeTest(unittest.TestCase):
    setUpClass=classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp=web.WebRuntimeTest.setUp
    load_page=web.WebRuntimeTest.load_page

    def seed(self):
        with workflow.WorkflowDB(self.database_path) as db:
            self.pid=db.create_task('目录与前端验收',base_dir=str(self.temporary_root/self._testMethodName))
            root=db.base_dir(self.pid)
            (root/'nested/child').mkdir(parents=True,exist_ok=True)
            (root/'nested/child/example.md').write_text('# FOLDER_FILE',encoding='utf-8')
        self.load_page()
        self.page.evaluate('async pid=>{curTask=pid;await openTab("workspace");}',self.pid)
        workspace_section(self.page, 'files')
        self.page.locator('#flList [data-rel="nested"]').wait_for()

    def test_expert_and_team_navigation_remain_available(self):
        self.load_page()
        for route,ready in (('assistants','#ag-assistant-new'),('team','#tofNew')):
            self.page.locator(f'#sidebar [data-nav="{route}"]').click()
            self.page.wait_for_function('r=>route()===r',arg=route)
            self.page.locator(ready).wait_for()
            self.assertGreater(len(self.page.locator('#main').inner_text()),20)
        self.assertEqual(self.errors,[])

    def test_file_tree_is_available_in_small_window_and_reading_focus(self):
        self.seed()
        name=self.page.locator('#flList [data-rel="nested"] .fl-name')
        self.assertTrue(name.evaluate('e=>e.scrollWidth<=e.clientWidth'),'Short folder names should fit in the desktop directory')
        for width in (900,760,1440):
            self.page.set_viewport_size({'width':width,'height':800})
            self.page.wait_for_function("(()=>{const e=document.querySelector('#projectWorkspace');return e.classList.contains('pw-narrow')===(e.clientWidth<900)})()")
            if not self.page.locator('#flList').is_visible():
                self.page.locator('#pwToggleTree').click()
            self.page.locator('#flList').wait_for(state='visible',timeout=2500)
            self.page.locator('#flList [data-rel="nested"] .fl-chevron').click()
            self.page.locator('#flList [data-rel="nested/child"]').wait_for()
            self.page.locator('#flList [data-rel="nested"] .fl-chevron').click()
            if self.page.locator('#pwNavShade').is_visible():
                self.page.locator('#pwNavClose').click()
        self.page.locator('#pwFocus').click()
        self.assertTrue(self.page.locator('#flList').is_hidden())
        self.page.locator('#pwToggleTree').click()
        self.page.locator('#flList').wait_for(state='visible',timeout=2500)
        self.assertEqual(self.errors,[])

    def test_folder_reopen_uses_cache_and_can_collapse_during_loading(self):
        self.seed()
        requests=[]
        self.page.on('request',lambda r:requests.append(r.url) if '/api/tree?' in r.url and 'rel=nested' in r.url else None)
        for _ in range(4):
            self.page.locator('#flList [data-rel="nested"] .fl-chevron').click()
            self.page.locator('#flList [data-rel="nested/child"]').wait_for()
            self.page.locator('#flList [data-rel="nested"] .fl-chevron').click()
            self.page.locator('#flList [data-rel="nested/child"]').wait_for(state='hidden')
        self.assertEqual(len(requests),1,'Reopening a cached folder should not require another request')
        self.assertEqual(self.errors,[])

    def test_workspace_panels_toggle_without_losing_selected_file(self):
        self.seed()
        self.page.locator('#flList [data-rel="nested"] .fl-chevron').click()
        self.page.locator('#flList [data-rel="nested/child"] .fl-chevron').click()
        self.page.locator('#flList [data-rel="nested/child/example.md"] .fl-name').click()
        self.page.locator('[data-fl-mode="edit"]').click()
        self.page.locator('#flEditText').wait_for()
        for _ in range(3):
            self.page.locator('#pwToggleTree').click()
            self.assertTrue(self.page.locator('#flList').is_hidden())
            self.page.locator('#pwToggleTree').click()
            self.assertTrue(self.page.locator('#flList').is_visible())
            self.page.locator('#pwToggleNav').click()
            self.assertTrue(self.page.locator('.pw-sidebar').is_hidden())
            self.assertTrue(self.page.locator('#flList').is_hidden())
            self.page.locator('#pwToggleNav').click()
            self.page.locator('#pwTab-chat').click()
            self.page.locator('#pwShowDoc').click()
            self.assertIn('FOLDER_FILE',self.page.locator('#flEditText').input_value())
        self.assertEqual(self.errors,[])

    def test_team_edit_and_discussion_use_the_selected_team(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            db.assistant_save('audit-team-expert',name='审查专家',enabled=1)
            teams=[]
            for name in ('团队甲','团队乙'):
                team=db.team_add(name,'custom',project_id=self.pid)
                db.team_member_add(team['id'],name+'负责人','leader',assistant_id='audit-team-expert')
                teams.append(team)
        self.page.locator('#sidebar [data-nav="team"]').click()
        self.page.locator(f'.tof-room[data-office="{teams[1]["id"]}"]').click()
        self.page.locator('#tofEdit').click()
        self.page.locator('#ag-team-edit').wait_for()
        self.assertEqual(self.page.evaluate('teamScope'),teams[1]['id'])
        self.page.locator('#ag-team-back').click()
        self.page.locator('#tofEnter').click()
        self.page.locator('#tcTeamSelect').wait_for()
        self.assertEqual(self.page.locator('#tcTeamSelect').input_value(),teams[1]['id'])
        self.assertEqual(self.page.evaluate('route()'),'team')
        self.assertEqual(self.errors,[])

    def test_late_team_response_does_not_replace_another_module(self):
        self.seed()
        pending=[]
        self.page.route('**/api/teams?project=*',lambda route:pending.append((route,route.fetch())))
        self.page.locator('#sidebar [data-nav="team"]').click()
        self.page.wait_for_timeout(150)
        self.assertTrue(pending)
        self.page.locator('#sidebar [data-nav="calendar"]').click()
        pending[0][0].fulfill(response=pending[0][1])
        self.page.wait_for_timeout(150)
        self.assertEqual(self.page.evaluate('route()'),'calendar')
        self.assertEqual(self.page.locator('#tofProj').count(),0)
        self.assertEqual(self.errors,[])

    def test_knowledge_graph_zotero_note_is_saved_to_current_project(self):
        self.seed()
        self.page.route('**/api/zotero/note?*',lambda route:route.fulfill(json={'title':'星图保存笔记','content':'# GRAPH_NOTE_CONTENT'}))
        writes=[]
        self.page.on('request',lambda r:writes.append(r.post_data_json) if r.method=='POST' and r.url.endswith('/api/project/note/save') else None)
        self.page.locator('#sidebar [data-nav="library"]').click()
        self.page.evaluate("window.postMessage({rw:'saveNoteFromZotero',key:'TEST_NOTE'},location.origin)")
        self.page.wait_for_timeout(250)
        self.assertTrue(writes)
        self.assertEqual(writes[0]['project_id'],self.pid)
        self.assertIn('GRAPH_NOTE_CONTENT',writes[0]['content'])
        self.assertEqual(self.errors,[])

    def test_inline_directory_survives_opening_task_details(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            db.assistant_save('audit-task-expert',name='任务专家',enabled=1)
            binding=project_bindings.register(db,self.pid,'assistant','audit-task-expert')
        self.page.locator('#pwFocus').click()
        self.page.evaluate("""async d=>{const made=await api('/api/project-tasks/create',{project_id:d.pid,goal:'查看目录保存',executor_binding_id:d.binding});await ProjectWorkspace.openTask(made.task.id,d.pid);}""",{'pid':self.pid,'binding':binding['id']})
        self.page.locator('#pwTab-library').click()
        workspace_section(self.page, 'files')
        self.page.locator('#flList [data-rel="nested"]').wait_for(state='visible')
        self.assertEqual(self.errors,[])

    def test_new_chat_and_new_task_buttons_open_actual_objects(self):
        self.seed()
        self.page.locator('#pwTab-chat').click()
        self.page.locator('#pwAsk').fill('保留首个草稿')
        self.page.locator('#pwNewChat').click()
        self.page.wait_for_function("document.querySelector('#pwAsk').value===''&&!document.querySelector('#pwNewChat').disabled")
        self.assertEqual(self.page.locator('[data-pw-chat]').count(),2)
        self.page.locator('[data-pw-chat]').filter(has_text='保留首个草稿').click()
        self.assertEqual(self.page.locator('#pwAsk').input_value(),'保留首个草稿')
        self.page.locator('#pwCreateTask').click()
        self.page.locator('dialog [data-pt-goal]').wait_for()
        self.assertEqual(self.page.locator('[data-pt-goal]').input_value(),'保留首个草稿')
        self.assertEqual(self.errors,[])

    def test_small_window_modules_do_not_cut_off_primary_controls(self):
        self.seed()
        self.page.set_viewport_size({'width':760,'height':760})
        for name,ready in [('assistants','#ag-assistant-new'),('team','#tofNew'),('mail','#btnMailManage'),('settings','[data-set="workflows"]')]:
            self.page.locator(f'#sidebar [data-nav="{name}"]').click()
            self.page.locator(ready).wait_for()
            widths=self.page.locator('#main').evaluate('e=>[e.clientWidth,e.scrollWidth]')
            self.assertLessEqual(widths[1],widths[0]+2,f'{name} content overflows the window')
        self.page.locator('[data-set="workflows"]').click()
        self.page.locator('[data-wfl-save]').wait_for()
        widths=self.page.locator('#main').evaluate('e=>[e.clientWidth,e.scrollWidth]')
        self.assertLessEqual(widths[1],widths[0]+2,'Workflow settings overflow the window')
        self.assertEqual(self.errors,[])

    def test_folder_can_be_closed_while_its_first_request_is_pending(self):
        self.seed()
        pending=[]
        def hold(route):
            if parse_qs(urlsplit(route.request.url).query).get('rel')==['nested']:
                pending.append((route,route.fetch()))
            else:route.continue_()
        self.page.route('**/api/tree?**',hold)
        row=self.page.locator('#flList [data-rel="nested"]')
        row.locator('.fl-chevron').click()
        self.page.wait_for_timeout(150)
        self.assertEqual(len(pending),1)
        self.assertEqual(row.get_attribute('aria-expanded'),'true')
        row.locator('.fl-chevron').click()
        self.assertEqual(row.get_attribute('aria-expanded'),'false')
        pending[0][0].fulfill(response=pending[0][1])
        self.page.wait_for_timeout(150)
        self.assertEqual(self.page.locator('#flList [data-rel="nested/child"]').count(),0)
        row.locator('.fl-chevron').click()
        self.page.locator('#flList [data-rel="nested/child"]').wait_for()
        self.assertEqual(len(pending),1)
        self.assertEqual(self.errors,[])

    def test_team_draft_survives_refresh_failure_and_session_switch(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            db.assistant_save('draft-expert',name='草稿专家',enabled=1)
            team=db.team_add('草稿核查团队','custom',project_id=self.pid)
            db.team_member_add(team['id'],'负责人','leader',assistant_id='draft-expert')
            db.team_member_add(team['id'],'执行成员','member',assistant_id='draft-expert')
        self.page.locator('#sidebar [data-nav="team"]').click()
        self.page.locator(f'.tof-room[data-office="{team["id"]}"]').click()
        self.page.locator('#tofEnter').click()
        self.page.locator('#tcSay').fill('TEAM_DRAFT_TO_KEEP')
        old=self.page.evaluate('teamData.session.id')
        self.page.locator('#tcRefresh').click()
        self.page.wait_for_function("document.querySelector('#tcSay')?.value==='TEAM_DRAFT_TO_KEEP'")
        self.page.route('**/api/team/chat/say',lambda r:r.fulfill(status=500,json={'error':'UI_SEND_FAILED'}))
        self.page.locator('#tcSend').click()
        self.page.wait_for_function("document.body.textContent.includes('UI_SEND_FAILED')")
        self.assertEqual(self.page.locator('#tcSay').input_value(),'TEAM_DRAFT_TO_KEEP')
        self.page.locator('#tcNewSession').click()
        self.page.wait_for_function("id=>teamData?.session?.id&&teamData.session.id!==id&&document.querySelector('#tcSay')?.value===''",arg=old)
        self.page.locator(f'[data-tc-sess="{old}"]').click()
        self.page.wait_for_function("document.querySelector('#tcSay')?.value==='TEAM_DRAFT_TO_KEEP'")
        self.assertEqual(self.errors,[])
