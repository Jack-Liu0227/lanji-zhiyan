"""User paths across the four workspace views, including save and review boundaries."""
import unittest
import workflow

from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace
from tests import test_project_tasks_runtime as tasks


@unittest.skipUnless(web.sync_playwright and web.EDGE, 'Requires Playwright and Edge')
class WorkspaceNavigationRuntimeTest(unittest.TestCase):
    setUpClass=classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp=web.WebRuntimeTest.setUp
    load_page=web.WebRuntimeTest.load_page
    seed=workspace.ProjectWorkspaceRuntimeTest.seed
    use_model=workspace.ProjectWorkspaceRuntimeTest.use_model
    poll_json=workspace.ProjectWorkspaceRuntimeTest.poll_json

    def open_evidence(self):
        web.workspace_section(self.page,'files')
        self.page.locator('#flList [data-rel="evidence.md"]').click()
        self.page.locator('#flStage .md-render').wait_for()

    def test_default_chat_and_file_read_edit_cite_return_path(self):
        self.seed()
        self.assertTrue(self.page.locator('#pwAsk').is_visible())
        self.assertTrue(self.page.locator('#pwDocumentPane').is_hidden())
        self.open_evidence()
        self.assertTrue(self.page.locator('#pwAsk').is_hidden())
        self.assertEqual(self.page.locator('#flEditText').count(),0)
        self.assertTrue(self.page.locator('#flSaveBtn').is_hidden())
        self.page.locator('[data-fl-mode="edit"]').click()
        self.page.locator('#flEditText').fill('# SAVED_BEFORE_CITATION')
        self.page.locator('#pwUseMaterial').click()
        self.page.locator('#pwAsk').wait_for(state='visible')
        self.assertTrue(self.page.locator('#pwAsk').is_visible())
        self.assertTrue(self.page.locator('#pwDocumentPane').is_hidden())
        self.assertIn('evidence.md',self.page.locator('#pwRefs').inner_text())
        self.page.locator('[data-pw-ref]').click()
        self.page.wait_for_function("document.querySelector('#flStage').textContent.includes('SAVED_BEFORE_CITATION')")
        self.assertTrue(self.page.locator('#pwDocumentPane').is_visible())
        self.page.locator('#pwCloseDoc').click()
        self.assertTrue(self.page.locator('#pwDocumentPane').is_hidden())
        self.page.locator('#pwTab-library').click()
        self.page.locator('#flStage').wait_for(state='visible')
        self.page.locator('[data-fl-mode="preview"]').click()
        self.page.wait_for_function("document.querySelector('#flStage').textContent.includes('SAVED_BEFORE_CITATION')")
        self.assertIn('SAVED_BEFORE_CITATION',self.page.locator('#flStage').inner_text())
        self.assertEqual(self.errors,[])

    def test_review_belongs_to_task_and_fixed_output_and_returns_to_parent(self):
        self.seed()
        self.open_evidence()
        self.page.locator('#pwTab-chat').click()
        with tasks.TaskModelFixture() as model:
            self.use_model(model.url)
            self.page.wait_for_function("b=>[...document.querySelector('#pwRecipient').options].some(o=>o.value==='binding:'+b)",arg=self.binding)
            self.page.locator('#pwRecipient').select_option('binding:'+self.binding)
            self.page.locator('#pwAsk').fill('CHECK_REVIEW_CONTEXT')
            self.page.locator('#pwPrepareTask').click()
            self.page.locator('[data-pw-start]').click()
            self.page.locator('#pwMessages [data-pw-output]').wait_for(timeout=30000)
            task=self.page.request.get(self.url+'api/project-tasks?project_id='+self.pid).json()['tasks'][0]
            self.page.locator('#pwMessages [data-pw-output]').last.click()
            self.page.wait_for_function("document.querySelector('#flStage').textContent.includes('PROJECT_TASK_REAL_OUTPUT')")
            self.assertEqual(self.page.locator('#pwTab-tasks').get_attribute('aria-selected'),'true')
            self.assertTrue(self.page.locator('#pwApprove').is_visible())
            self.assertTrue(self.page.locator('#flModeTabs').is_hidden())
            self.page.locator('#pwBack').click()
            self.page.locator('.pw-task-detail').wait_for()
            self.page.locator('#pwTab-library').click()
            self.page.wait_for_function("document.querySelector('#flStage').textContent.includes('SAVED_PROJECT_MATERIAL')")
            self.assertTrue(self.page.locator('#pwReview').is_hidden())
            self.page.locator('#pwTab-chat').click()
            self.assertTrue(self.page.locator('#pwReview').is_hidden())
            self.page.evaluate('d=>ProjectWorkspace.openTask(d.id,d.project_id)',task)
            self.page.locator('#pwFeedback').fill('RETURN_FOR_MORE_EVIDENCE')
            self.page.locator('#pwReject').click()
            data=self.poll_json('/api/project-tasks?project_id='+self.pid,lambda d:d['tasks'][0]['status']=='blocked')
            self.assertIn('RETURN_FOR_MORE_EVIDENCE',str(data['tasks'][0]['deliveries']))
        self.assertEqual(self.errors,[])

    def test_narrow_directory_and_reference_have_focus_and_no_stacked_pages(self):
        self.seed()
        self.open_evidence()
        self.page.set_viewport_size({'width':760,'height':900})
        self.page.wait_for_function("document.querySelector('#projectWorkspace').classList.contains('pw-narrow')")
        self.assertTrue(self.page.locator('#flList').is_hidden())
        self.page.locator('#pwToggleTree').click()
        self.assertTrue(self.page.locator('#flList').is_visible())
        self.assertTrue(self.page.locator('#pwWorkArea').evaluate('e=>e.inert'))
        self.page.keyboard.press('Escape')
        self.assertTrue(self.page.locator('#flList').is_hidden())
        self.assertEqual(self.page.evaluate('document.activeElement.id'),'pwToggleNav')
        self.page.locator('#pwUseMaterial').click()
        self.page.locator('#pwAsk').wait_for(state='visible')
        self.page.locator('[data-pw-ref]').click()
        self.page.locator('#pwDocumentPane').wait_for(state='visible')
        self.assertEqual(self.page.locator('#pwDocumentPane').get_attribute('role'),'dialog')
        self.assertTrue(self.page.locator('.pw-chat').evaluate('e=>e.inert'))
        self.page.locator('#pwCloseDoc').focus()
        self.page.keyboard.press('Escape')
        self.assertTrue(self.page.locator('#pwDocumentPane').is_hidden())
        self.assertEqual(self.page.evaluate('document.activeElement.id'),'pwAsk')
        widths=self.page.locator('#main').evaluate('e=>[e.clientWidth,e.scrollWidth]')
        self.assertLessEqual(widths[1],widths[0]+2)
        self.assertEqual(self.errors,[])

    def test_file_tree_creates_in_parent_and_rename_delete_keep_preview_in_sync(self):
        self.seed()
        self.open_evidence()
        self.page.locator('#flList [data-rel="evidence.md"]').click(button='right')
        self.page.get_by_role('menuitem',name='新建文件夹',exact=True).click()
        self.page.locator('dialog input').fill('materials')
        self.page.locator('dialog [data-ok]').click()
        self.page.wait_for_function("document.querySelector('#flList [data-rel=materials]')||document.querySelector('#pwError').textContent")
        self.assertEqual(self.page.locator('#pwError').inner_text(),'')
        self.page.locator('#flList [data-rel="materials"]').wait_for()
        self.page.locator('#flList [data-rel="materials"]').click(button='right')
        self.page.get_by_role('menuitem',name='新文件',exact=True).click()
        self.page.locator('dialog input').fill('new.md')
        self.page.locator('dialog [data-ok]').click()
        self.page.locator('#flEditText').fill('# SAVED_BEFORE_RENAME')
        self.page.locator('#flList [data-rel="materials/new.md"]').click(button='right')
        self.page.once('dialog',lambda dialog:dialog.accept('renamed.md'))
        self.page.get_by_role('menuitem',name='重命名',exact=True).click()
        self.page.wait_for_function("document.querySelector('#pwResourceTitle').textContent==='renamed.md'")
        self.page.locator('[data-fl-mode="edit"]').click()
        self.page.locator('#flEditText').fill('# EDITED_AFTER_RENAME')
        self.page.locator('#pwTab-chat').click()
        self.page.locator('#pwAsk').wait_for(state='visible')
        with workflow.WorkflowDB(self.database_path) as db:
            root=db.base_dir(self.pid)
            self.assertFalse((root/'materials/new.md').exists())
            self.assertEqual((root/'materials/renamed.md').read_text(encoding='utf-8'),'# EDITED_AFTER_RENAME')
        self.page.locator('#pwTab-library').click()
        self.page.locator('#flList [data-rel="materials/renamed.md"]').click(button='right')
        self.page.get_by_role('menuitem',name='删除',exact=True).click()
        self.page.locator('dialog [data-ok]').click()
        self.page.locator('#flList [data-rel="materials/renamed.md"]').wait_for(state='detached')
        self.assertEqual(self.page.locator('#flEditText').count(),0)
        self.page.locator('#pwTab-chat').click()
        with workflow.WorkflowDB(self.database_path) as db:
            self.assertFalse((db.base_dir(self.pid)/'materials/renamed.md').exists())
        self.assertEqual(self.errors,[])

    def test_failed_save_blocks_file_creation_rename_and_delete(self):
        self.seed()
        self.open_evidence()
        self.page.locator('[data-fl-mode="edit"]').click()
        self.page.locator('#flEditText').fill('# UNSAVED_FILE_MUTATION')
        self.page.route('**/api/conv/save',lambda route:route.fulfill(status=500,json={'error':'SAVE_BARRIER'}))
        mutations=[]
        self.page.on('request',lambda request:mutations.append(request.url) if any(p in request.url for p in ('/api/conv/rename','/api/conv/delete','/api/project/files/create')) else None)
        for action in ('新文件','重命名','删除'):
            self.page.locator('#flList [data-rel="evidence.md"]').click(button='right')
            self.page.get_by_role('menuitem',name=action,exact=True).click()
            self.page.wait_for_function("document.querySelector('#pwError').textContent.includes('保存失败')")
            self.assertEqual(self.page.locator('dialog[open]').count(),0)
            self.assertEqual(self.page.locator('#flEditText').input_value(),'# UNSAVED_FILE_MUTATION')
        self.assertEqual(mutations,[])
        self.assertEqual(self.errors,[])

    def test_library_search_uses_current_project_and_rebuild_remains_accessible(self):
        self.seed()
        self.page.evaluate("async pid=>api('/api/project/note/save',{project_id:pid,path:'笔记/研究规划/SEARCHABLE_MATERIAL.md',content:'# SEARCHABLE_MATERIAL\\nCURRENT_PROJECT_ONLY'})",self.pid)
        self.page.locator('#pwProjectSettings summary').click()
        self.page.locator('#pwRebuildAssets').click()
        self.page.wait_for_function("document.querySelector('#pwNotice').textContent.includes('资料索引已整理')")
        self.page.locator('#pwTab-library').click()
        self.page.locator('#pwSearchInput').fill('SEARCHABLE_MATERIAL')
        self.page.locator('#pwSearchInput').press('Enter')
        self.page.locator('[data-pw-search-group="notes"]').first.wait_for()
        self.assertIn('SEARCHABLE_MATERIAL',self.page.locator('#pwResource').inner_text())
        self.page.locator('[data-pw-search-group="notes"]').first.click()
        self.page.locator('#mdDashboard').wait_for()
        self.assertTrue(self.page.locator('#pwAsk').is_hidden())
        self.page.locator('#pwProject').select_option(self.other)
        self.page.locator('#pwTab-library').click()
        self.page.locator('#pwSearchInput').fill('SEARCHABLE_MATERIAL')
        self.page.locator('#pwSearchInput').press('Enter')
        self.page.get_by_text('当前项目中没有匹配的资料。',exact=True).wait_for()
        self.assertEqual(self.errors,[])

    def test_star_map_mounts_inside_its_own_library_view(self):
        self.seed()
        def graph(route):
            data=route.fetch().json()
            data['paper_graph']={'built_at':'2026-10-27','nodes':[{'id':'p1','title':'FIRST_PAPER','year':2025},{'id':'p2','title':'SECOND_PAPER','year':2026}],
                                 'edges':[{'source':'p2','target':'p1','kind':'cite'}],'stats':{}}
            route.fulfill(json=data)
        self.page.route('**/api/task?id='+self.pid,graph)
        web.workspace_section(self.page,'stars')
        self.page.locator('#spStars svg[role="img"]').wait_for()
        self.assertTrue(self.page.locator('#pwAsk').is_hidden())
        self.assertIn('2 篇论文',self.page.locator('#spStars').inner_text())
        self.assertEqual(self.page.locator('#spStars [data-node-id="p1"]').get_attribute('aria-label'),'FIRST_PAPER')
        self.assertEqual(self.errors,[])

    def test_primary_tabs_keyboard_and_section_memory(self):
        self.seed()
        self.page.locator('#pwTab-chat').focus()
        self.page.keyboard.press('ArrowRight')
        self.assertEqual(self.page.evaluate('document.activeElement.id'),'pwTab-library')
        self.page.keyboard.press('Enter')
        web.workspace_section(self.page,'notes')
        self.page.locator('#mdDashboard').wait_for()
        self.page.locator('#pwTab-flow').click()
        self.page.locator('#pwWorkflowChoice').wait_for()
        self.assertTrue(self.page.locator('#pwAsk').is_hidden())
        self.page.locator('#pwTab-library').click()
        self.page.locator('#mdDashboard').wait_for()
        self.assertEqual(self.page.locator('[data-pw-view="notes"]').get_attribute('aria-current'),'page')
        self.page.reload(wait_until='networkidle')
        self.page.locator('#mdDashboard').wait_for()
        self.assertEqual(self.page.locator('#pwTab-library').get_attribute('aria-selected'),'true')
        self.assertEqual(self.errors,[])


if __name__=='__main__':
    unittest.main()
