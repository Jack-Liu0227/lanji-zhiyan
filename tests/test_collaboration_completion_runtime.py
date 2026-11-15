"""Browser acceptance for local Zotero controls and scheduled reviewed workflows."""
import json
import os
from pathlib import Path
import unittest
from tests.test_web_runtime import workspace_section
from tests import test_connected_research_runtime as connected


@unittest.skipUnless(connected.runtime.sync_playwright and connected.runtime.EDGE, 'Requires Playwright and Edge')
class CollaborationCompletionRuntimeTest(unittest.TestCase):
    setUpClass = classmethod(connected.runtime.WebRuntimeTest.setUpClass.__func__)
    setUp = connected.runtime.WebRuntimeTest.setUp
    load_page = connected.runtime.WebRuntimeTest.load_page
    project = connected.ConnectedResearchRuntimeTest.project
    use_model = connected.ConnectedResearchRuntimeTest.use_model
    poll_json = connected.ConnectedResearchRuntimeTest.poll_json

    def screenshot(self, name):
        folder=os.environ.get('LANJI_TEST_SCREENSHOT_DIR')
        if folder:
            Path(folder).mkdir(parents=True,exist_ok=True)
            self.page.screenshot(path=str(Path(folder)/name))

    def test_scheduled_workflow_review_inbox_snooze_and_final_note(self):
        self.load_page()
        pid = self.project('Scheduled reviewed workflow')
        with connected.ModelFixture() as model:
            self.use_model(model.url)
            layout = self.page.evaluate("""async () => (await api('/api/workflow/layouts/save',{name:'Scheduled sequence',
                nodes:[{id:'o',step:'outline',label:'REVIEW_INBOX_NODE',assistant_id:'builtin-steward',prompt:'BROWSER_OUTLINE'},
                       {id:'w',step:'write',label:'Final write',assistant_id:'builtin-steward',prompt:'BROWSER_WRITE',review_required:false}],
                edges:[{source:'o',target:'w'}],order:['o','w']})).layout.id""")
            self.page.evaluate("() => openTab('schedules')")
            self.page.locator('#schNew').click()
            self.page.locator('#schWorkflow').select_option(layout)
            self.assertTrue(self.page.locator('#schAssistant').is_disabled())
            self.page.locator('#schName').fill('REVIEWED_SCHEDULE')
            self.page.locator('#schFreq').select_option('manual')
            self.page.locator('#schSave').click()
            self.page.locator('.sch-card').filter(has_text='REVIEWED_SCHEDULE').locator('[data-sch-run]').click()
            data = self.poll_json('/api/workflow/reviews', lambda d: any(r['project_id']==pid for r in d['items']))
            item = next(r for r in data['items'] if r['project_id']==pid)
            schedule = self.page.request.get(self.url.rstrip('/')+'/api/schedules').json()
            run = next(r for r in schedule['runs'] if r.get('workflow_run_id')==item['run_id'])
            self.assertEqual(run['status'], 'awaiting_review')
            self.assertFalse(run.get('note_id'))
            self.page.evaluate('() => ConnectedResearch.refreshReviews()')
            self.page.locator('#workflowReviewInbox').click()
            self.page.locator('[data-review-snooze]').wait_for()
            self.screenshot('collaboration-review-inbox.png')
            self.page.locator('[data-review-snooze]').click()
            self.poll_json('/api/workflow/reviews', lambda d: d['items'] and not d['items'][0]['notify'])
            self.page.locator('[data-review-open]').click()
            self.page.locator('[data-pt-feedback]').fill('INBOX_FEEDBACK_PRESERVED')
            self.page.locator('[data-pt-action="approve"]').click()
            result = self.poll_json('/api/schedules', lambda d:any(r.get('workflow_run_id')==item['run_id'] and r.get('note_id') and r['status']=='completed' for r in d['runs']))
            completed=next(r for r in result['runs'] if r.get('workflow_run_id')==item['run_id'])
            self.assertEqual(completed['status'],'completed')
            self.page.locator('dialog.pt-dialog [data-close]').click()
            self.page.evaluate("() => openTab('schedules')")
            self.page.locator('[data-run-note]').first.click()
            self.page.wait_for_function("document.querySelector('#mdSource')?.value.includes('INBOX_FEEDBACK_PRESERVED')")
            self.assertIn('BROWSER_ARTIFACT_write', self.page.locator('#mdSource').input_value())
        self.assertEqual(self.errors, [])

    def test_local_zotero_project_unlink_restore_controls_use_backend_contract(self):
        batch = {'id':'zlink-browser', 'created_at':'1793000000', 'status':'preview', 'data':{'items':[
            {'key':'TEST0001','title':'Synthetic browser paper','shared_projects':['Project B'], 'references':{'notes':1,'runs':[]}}]}}
        writes=[]
        def route(request):
            url=request.request.url.split('/api/zotero/web/')[1].split('?')[0]
            body=request.request.post_data_json if request.request.method=='POST' else {}
            if url=='config':
                value={'config':{'transport':'local'},'binding':{'collection':'COLL0001','library':'local:fixture/users/0'},'local':{'readable':True,'authorized':True},'docs':{}}
            elif url=='auto': value={'auto_sync':{}}
            elif url=='links': value={'batches':[] if batch['status']=='preview' else [batch]}
            elif url=='links/preview': value={'batch':batch}
            elif url in ('links/apply','links/restore'):
                writes.append((url,body));batch['status']='restored' if url.endswith('restore') else 'applied';value={'batch':batch}
            else: value={}
            request.fulfill(json=value)
        self.page.route('**/api/zotero/web/**',route)
        self.load_page()
        pid=self.project('Local Zotero controls')
        self.page.evaluate("pid => ProjectLiterature.open(pid,true)",pid)
        self.page.locator('#zs-unlink-preview').click()
        self.page.locator('[data-zs-unlink-key]').wait_for()
        self.assertIn('Project B',self.page.locator('#zs-unlink').inner_text())
        self.assertIn('笔记 1',self.page.locator('#zs-unlink').inner_text())
        self.assertEqual(self.page.locator('#zs-key').count(),0)
        self.assertEqual(self.page.evaluate('route()'),'workspace')
        self.screenshot('collaboration-local-zotero.png')
        self.page.evaluate('() => {window.uiConfirm=async()=>true;}')
        self.page.locator('#zs-unlink-apply').click()
        self.page.locator('[data-zs-restore]').first.click()
        self.page.get_by_text('已恢复',exact=False).first.wait_for()
        self.assertEqual([p for p,_ in writes],['links/apply','links/restore'])
        self.assertTrue(all(b['confirm'] and b['batch_id']==batch['id'] for _,b in writes))
        self.assertEqual(self.errors, [])

    def test_workflow_zotero_entry_uses_current_project_and_old_writes_are_removed(self):
        self.load_page()
        first=self.project('Previously viewed sync project')
        second=self.project('Current workflow sync project')
        self.page.evaluate('pid => IntegrationSettings.openZotero(pid)',first)
        self.page.wait_for_function('pid => document.querySelector("#zs-project")?.value===pid',arg=first)
        self.page.evaluate("pid => {curTask=pid;openTab('workflow');}",second)
        workspace_section(self.page, 'papers')
        self.page.locator('#plManagement > summary').click()
        self.page.wait_for_function('pid => document.querySelector("#pwProject")?.value===pid',arg=second)
        self.page.locator('#zs-load').wait_for()
        self.assertIn('rw:project:'+second,self.page.locator('#projectLiterature').inner_text())
        # These payloads cannot create a Zotero item even if a legacy route regresses.
        for path,payload in (('/api/zotero/import_project_papers',{'task_id':second}),('/api/zotero/collection/create',{})):
            response=self.page.request.post(self.url.rstrip('/')+path,data=payload)
            self.assertEqual(response.status,404,response.text())
            self.assertEqual(response.json()['error'],'not found')
        self.assertEqual(self.errors, [])

    def test_multiple_collection_sources_and_explicit_item_selection(self):
        binding, writes = {}, []
        chosen = []
        def route(request):
            path = request.request.url.split('/api/zotero/web/')[1].split('?')[0]
            body = request.request.post_data_json if request.request.method == 'POST' else {}
            if path == 'config':
                value = {'config': {'transport': 'local'}, 'binding': binding, 'local': {'readable': True, 'authorized': True}, 'docs': {}}
            elif path == 'auto': value = {'auto_sync': {}}
            elif path == 'links': value = {'batches': []}
            elif path == 'collections':
                value = {'collections': [{'key': key, 'data': {'name': name}} for key, name in [('COLLA001', 'Collection A'), ('COLLB001', 'Collection B')]]}
            elif path == 'sources':
                writes.append((path, body))
                binding.update(library='local:fixture/users/0', collection='', sources=body['sources'])
                value = {'binding': binding}
            elif path == 'candidates':
                value = {'items': [{'key': key, 'title': name, 'collections': cols, 'doi': '', 'selected': False, 'status': ''}
                                   for key, name, cols in [('PAPER001', 'Shared source item', ['COLLA001', 'COLLB001']),
                                                          ('PAPER002', 'Leave in A', ['COLLA001']),
                                                          ('PAPER003', 'Choose from B', ['COLLB001'])]]}
            elif path == 'select':
                writes.append((path, body)); chosen.extend(body['keys']); value = {'ok': True}
            elif path == 'sync':
                writes.append((path, body))
                value = {'actions': [{'key': key, 'kind': 'pull'} for key in chosen], 'conflicts': [], 'mode': 'merge', 'plan_token': 'browser-selection'} if body['dry_run'] else {'ok': True}
            else: value = {}
            request.fulfill(json=value)
        self.page.route('**/api/zotero/web/**', route)
        self.load_page()
        pid = self.project('Subset across Collections')
        self.page.evaluate('pid => ProjectLiterature.open(pid,true)', pid)
        self.page.locator('#zs-load').click()
        for key in ('COLLA001', 'COLLB001'):
            self.page.locator('#zs-collection').select_option(key)
            self.page.locator('#zs-add-source').click()
        self.assertEqual(self.page.locator('[data-zs-source-mode]').count(), 2)
        self.assertEqual(self.page.locator('[data-zs-source-mode]').evaluate_all('(els)=>els.map(e=>e.value)'), ['selected', 'selected'])
        self.page.locator('#zs-save-sources').click()
        self.page.locator('#zs-browse').click()
        self.page.locator('[data-zs-member="PAPER001"]').wait_for()
        self.assertEqual(self.page.locator('[data-zs-member]:checked').count(), 0)
        self.page.locator('[data-zs-member="PAPER001"]').check()
        self.page.locator('[data-zs-member="PAPER003"]').check()
        self.page.locator('#zs-select').click()
        self.page.wait_for_function('!document.querySelector("#zs-apply").disabled')
        self.page.evaluate('() => {window.uiConfirm=async()=>true;}')
        self.page.locator('#zs-apply').click()
        self.page.wait_for_function('document.querySelector("#integration-result").textContent.includes("同步完成")')
        self.assertEqual(chosen, ['PAPER001', 'PAPER003'])
        self.assertEqual(writes[0], ('sources', {'task_id': pid, 'sources': [{'collection': 'COLLA001', 'mode': 'selected'}, {'collection': 'COLLB001', 'mode': 'selected'}], 'export_collection': ''}))
        self.assertEqual(writes[-1], ('sync', {'task_id': pid, 'mode': 'merge', 'dry_run': False, 'plan_token': 'browser-selection', 'confirm': True}))
        self.assertEqual(self.page.evaluate('route()'),'workspace')
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
