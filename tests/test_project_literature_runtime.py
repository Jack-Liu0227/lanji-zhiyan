"""Project-space management against real application APIs and a simulated Zotero connector."""
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch
import os
import time
import unittest
from tests.test_web_runtime import workspace_section

import project_library
import workflow
import zotero_sync as sync
import zotero_sources
import zotero
from _pdf_fixture import write_text_pdf
from tests import test_connected_research_runtime as connected
from tests.test_zotero_collaboration import Remote


@unittest.skipUnless(connected.runtime.sync_playwright and connected.runtime.EDGE, 'Requires Playwright and Edge')
class ProjectLiteratureRuntimeTest(unittest.TestCase):
    setUpClass = classmethod(connected.runtime.WebRuntimeTest.setUpClass.__func__)
    setUp = connected.runtime.WebRuntimeTest.setUp
    load_page = connected.runtime.WebRuntimeTest.load_page

    def fixture(self):
        self.remote = Remote()
        self.cfg = {'library_type': 'users', 'library_id': str(time.time_ns()), 'api_key': 'fixture-only'}
        for target, replacement in [('config', lambda db: self.cfg), ('all_pages', self.remote.pages), ('request', self.remote.request)]:
            active = patch.object(sync, target, side_effect=replacement)
            active.start(); self.addCleanup(active.stop)
        def route(request):
            url = urlsplit(request.request.url)
            body = request.request.post_data_json if request.request.method == 'POST' else {}
            query = parse_qs(url.query)
            pid = body.get('task_id') or query.get('task_id', [''])[0]
            with workflow.WorkflowDB(self.database_path) as db:
                if url.path.endswith('/config'):
                    value = {'config': {'transport': 'local'}, 'binding': sync.binding(db, pid), 'local': {'readable': True, 'authorized': True}, 'docs': {}}
                elif url.path.endswith('/collections'):
                    value = {'collections': [{'key': key, 'data': {'name': name}} for key,name in [('COLLA001','Source A'),('COLLB001','Source B')]]}
                else:
                    value = sync.route(request.request.method, url.path, body, query, db)
            request.fulfill(json=value)
        self.page.route('**/api/zotero/web/**', route)

    def create_project(self, title):
        with workflow.WorkflowDB(self.database_path) as db:
            pid = db.create_task(title, base_dir=str(self.temporary_root / title))
            zotero_sources.configure(db, pid, [{'collection': 'COLLA001'}, {'collection': 'COLLB001'}])
        return pid

    def open_project(self, pid, manage=False):
        self.page.evaluate('async args => {await refreshState(false);await ProjectLiterature.open(args.pid,args.manage);}', {'pid':pid,'manage':manage})
        self.page.locator('#projectLiterature').wait_for()
        if manage: self.page.locator('#zs-browse').wait_for()

    def test_select_sync_remove_and_restore_without_leaving_project_space(self):
        self.fixture();self.load_page()
        self.remote.add('PAPER001',['COLLA001','COLLB001'],title='Shared source paper')
        self.remote.add('PAPER002',['COLLA001'],title='Leave unselected',DOI='10.123/unselected')
        self.remote.add('PAPER003',['COLLB001'],title='Chosen B paper',DOI='10.123/chosen-b')
        pid=self.create_project('Project literature membership')
        self.open_project(pid)
        self.page.locator('#spZoteroBtn').click()
        self.page.locator('#zs-browse').click()
        for key in ('PAPER001','PAPER003'):
            self.page.locator(f'[data-zs-member="{key}"]').check()
        self.page.locator('#zs-select').click()
        self.page.wait_for_function('!document.querySelector("#zs-apply").disabled')
        self.page.evaluate('() => {window.uiConfirm=async()=>true;}')
        self.page.locator('#zs-apply').click()
        self.page.locator('#plList').get_by_text('Chosen B paper',exact=True).wait_for()
        self.assertEqual(self.page.locator('#plList [data-pl-id]').count(),2)
        self.assertNotIn(sync.project_tag(pid),{t['tag'] for t in self.remote.rows['PAPER002']['data']['tags']})
        self.page.locator('#plManagement > summary').click()
        self.page.locator('[data-pl-key="PAPER001"]').check()
        self.page.locator('#plRemove').click()
        self.page.locator('[data-zs-unlink-key="PAPER001"]').wait_for()
        self.assertEqual(self.page.locator('[data-zs-unlink-key]').count(),1)
        self.page.locator('#zs-unlink-apply').click()
        self.page.wait_for_function('document.querySelectorAll("#plList [data-pl-id]").length===1')
        self.assertEqual(self.remote.rows['PAPER001']['data']['collections'],['COLLA001','COLLB001'])
        self.page.locator('[data-zs-restore]').first.click()
        self.page.wait_for_function('document.querySelectorAll("#plList [data-pl-id]").length===2')
        self.assertEqual(self.page.evaluate('route()'),'workspace')
        self.assertEqual(self.page.locator('#pwProject').input_value(),pid)
        screenshot_dir=Path(os.environ.get('LANJI_TEST_SCREENSHOT_DIR',str(self.temporary_root)))
        screenshot_dir.mkdir(parents=True,exist_ok=True)
        self.page.locator('#plManagement > summary').click()
        self.page.screenshot(path=str(screenshot_dir/'project-literature.png'),full_page=True)
        self.assertEqual(self.errors,[])

    def test_poll_preserves_draft_filter_selection_and_detects_edit_conflict(self):
        self.fixture();self.load_page()
        self.remote.add(title='Evidence before edit',collections=['COLLA001'])
        pid=self.create_project('Project literature editing')
        with workflow.WorkflowDB(self.database_path) as db:
            zotero_sources.select(db,pid,['PAPER001'])
            plan=sync.sync(db,pid)
            sync.sync(db,pid,dry_run=False,plan_token=plan['plan_token'])
            rid=project_library.items(db,pid)[0]['result_id']
        self.open_project(pid,True)
        self.page.locator('#zs-collection').select_option('COLLA001')
        self.page.locator('[data-zs-source-mode]').first.select_option('all')
        self.page.locator('#plSearch').fill('Evidence')
        self.page.locator('[data-pl-key="PAPER001"]').check()
        self.page.locator('[data-pl-edit]').click()
        self.page.locator('#plEditForm [name=title]').fill('My unsaved Evidence draft')
        self.page.evaluate("async x => api('/api/library/project',{task_id:x.pid,result_id:x.rid,fields:{title:'Evidence changed remotely'}})",{'pid':pid,'rid':rid})
        self.page.evaluate('() => ConnectedResearch.refreshLibrary()')
        self.assertEqual(self.page.locator('#plEditForm [name=title]').input_value(),'My unsaved Evidence draft')
        self.assertEqual(self.page.locator('#plSearch').input_value(),'Evidence')
        self.assertTrue(self.page.locator('[data-pl-key="PAPER001"]').is_checked())
        self.assertEqual(self.page.locator('[data-zs-source-mode]').first.input_value(),'all')
        self.page.locator('#plEditForm').get_by_role('button',name='保存题录').click()
        self.page.locator('#plError').get_by_text('编辑期间文献已更新',exact=False).wait_for()
        with workflow.WorkflowDB(self.database_path) as db:
            self.assertEqual(db.result(rid)['title'],'Evidence changed remotely')
        self.assertEqual(self.page.locator('#plEditForm [name=title]').input_value(),'My unsaved Evidence draft')
        self.page.evaluate('() => {window.uiConfirm=async()=>true;}')
        self.page.locator('#plEditLatest').click()
        self.page.wait_for_function('document.querySelector("#plEditForm [name=title]").value==="Evidence changed remotely"')
        self.page.locator('#plEditForm [name=title]').fill('Evidence saved in project')
        self.page.locator('#plEditForm').get_by_role('button',name='保存题录').click()
        self.page.wait_for_function('document.querySelector("#plEditor").hidden')
        self.page.locator('#plList').get_by_text('Evidence saved in project',exact=True).wait_for()
        self.page.evaluate("() => openTab('workflow')")
        workspace_section(self.page, 'papers')
        self.page.get_by_text('Evidence saved in project',exact=True).wait_for()
        self.assertEqual(self.errors,[])

    def test_late_candidate_response_does_not_leak_into_another_project(self):
        self.fixture();self.load_page()
        first=self.create_project('Project delayed A')
        second=self.create_project('Project delayed B')
        pending=[]
        self.page.route('**/api/zotero/web/candidates?**',lambda route:pending.append(route))
        self.open_project(first,True)
        self.page.locator('#zs-browse').click()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(pending),1)
        self.page.locator('#pwProject').select_option(second)
        workspace_section(self.page,'papers')
        self.page.locator('#spZoteroBtn').click()
        self.page.locator('#zs-browse').wait_for()
        pending[0].fulfill(json={'items':[{'key':'PAPER001','title':'A_ONLY_STALE_RESPONSE','collections':[],'selected':False,'status':''}]})
        self.page.wait_for_timeout(150)
        self.assertEqual(self.page.locator('#pwProject').input_value(),second)
        self.assertNotIn('A_ONLY_STALE_RESPONSE',self.page.locator('#projectLiterature').inner_text())
        self.assertEqual(self.page.locator('[data-zs-member]').count(),0)
        self.assertEqual(self.errors,[])

    def test_import_pdf_from_project_row_then_open_project_copy(self):
        self.fixture();self.load_page()
        remote=self.remote.add(collections=['COLLA001'])
        pid=self.create_project('Project literature PDF')
        with workflow.WorkflowDB(self.database_path) as db:
            zotero_sources.select(db,pid,['PAPER001'])
            plan=sync.sync(db,pid)
            sync.sync(db,pid,dry_run=False,plan_token=plan['plan_token'])
        original=write_text_pdf(self.temporary_root/'zotero-fixture.pdf',['Project PDF original attachment'])
        original_bytes=original.read_bytes()
        normalized={**sync._from_remote(remote),'key':'PAPER001','version':remote['version'],'creators':['Alice Chen'],'type':'journalArticle'}
        imports,opened=[],[]
        def import_pdf(request):
            body=request.request.post_data_json;imports.append(body)
            with workflow.WorkflowDB(self.database_path) as db, patch.object(zotero,'item',return_value=normalized), patch.object(zotero,'pdf_path',return_value=str(original)):
                result=workflow.stage_zotero_import(db,body['task_id'],keys=body['keys'])
            request.fulfill(json=result)
        self.page.route('**/api/workflow/zotero',import_pdf)
        self.page.route('**/api/paper/open?**',lambda request:(opened.append(request.request.url),request.fulfill(json={'opened':True})))
        self.open_project(pid)
        self.page.locator('[data-pl-import-pdf]').click()
        self.page.locator('[data-pl-read]').wait_for()
        self.page.locator('[data-pl-read]').click()
        self.assertEqual(imports,[{'task_id':pid,'keys':['PAPER001']}])
        with workflow.WorkflowDB(self.database_path) as db:
            rows=project_library.items(db,pid)
            self.assertEqual(len(rows),1)
            copied=Path(rows[0]['source_path'])
            self.assertNotEqual(copied,original)
            self.assertEqual(copied.read_bytes(),original_bytes)
            self.page.locator('#flStage iframe').wait_for()
            self.assertIn('pdf',self.page.locator('#flStage iframe').get_attribute('src'))
            self.assertEqual(opened,[])
        self.assertEqual(original.read_bytes(),original_bytes)
        self.assertEqual(self.page.evaluate('route()'),'workspace')
        self.assertEqual(self.errors,[])

    def test_source_panel_read_failure_can_retry_in_place(self):
        self.fixture();self.load_page()
        pid=self.create_project('Project literature retry')
        failures=[]
        def fail_once(request):
            if not failures:
                failures.append(True);request.fulfill(status=503,json={'error':'SIMULATED_TEMPORARY_FAILURE'})
            else: request.fallback()
        self.page.route('**/api/zotero/web/config?**',fail_once)
        self.open_project(pid)
        self.page.locator('#spZoteroBtn').click()
        self.page.locator('[data-zs-reload]').click()
        self.page.locator('#zs-save-sources').wait_for()
        self.assertEqual(self.page.locator('#pwProject').input_value(),pid)
        self.assertEqual(self.page.evaluate('route()'),'workspace')
        self.assertEqual(self.errors,[])
