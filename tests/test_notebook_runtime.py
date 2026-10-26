import unittest
from tests import test_web_runtime as web


@unittest.skipUnless(web.sync_playwright and web.EDGE, "Requires Playwright and Edge")
class NotebookRuntimeTest(unittest.TestCase):
    setUpClass = classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp = web.WebRuntimeTest.setUp
    load_page = web.WebRuntimeTest.load_page

    def notes(self):
        self.load_page()
        self.page.evaluate("openTab('notes')")
        self.page.locator('[data-nb-view="category"]').click()
        self.page.locator('[data-category="planning"]').wait_for()

    def test_create_custom_two_levels_and_rename(self):
        self.notes()
        self.assertEqual(self.page.locator('.nb-sidebar [data-category]').count(), 7)
        self.page.locator('[data-manage]').click()
        self.page.get_by_role('button', name='＋ 新建分类', exact=True).click()
        modal = self.page.get_by_role('dialog', name='新建分类', exact=True)
        modal.get_by_label('分类名称').fill('浏览器复现分类')
        modal.get_by_label('上级分类').select_option('experiment_analysis')
        modal.get_by_role('button', name='保存分类', exact=True).click()
        modal.wait_for(state='detached')
        manager = self.page.get_by_role('dialog', name='管理分类', exact=True)
        row = manager.locator('.nb-category-row').filter(has_text='浏览器复现分类')
        row.get_by_role('button', name='编辑').click()
        editor = self.page.get_by_role('dialog', name='编辑分类', exact=True)
        editor.get_by_label('分类名称').fill('浏览器计算复现')
        editor.get_by_role('button', name='保存分类').click()
        editor.wait_for(state='detached')
        manager.get_by_role('button', name='关闭', exact=True).click()
        self.page.get_by_role('button', name='浏览器计算复现', exact=False).wait_for()
        self.assertEqual(self.errors, [])

    def test_multiselect_tags_save_and_reopen(self):
        self.notes()
        self.page.locator('[data-category="literature"]').click()
        self.page.locator('#nbTreeNewFile, #pnBtnSideNewFile').first.click()
        self.page.locator('#mdNoteTitle').fill('标签下拉验证')
        self.page.locator('#mdSource').fill('Evidence with portable metadata')
        properties = self.page.locator('.nb-note-properties')
        properties.get_by_role('button', name='标签（可选）').click()
        popup = properties.locator('.nb-tag-popup')
        popup.get_by_label('精读', exact=True).check()
        popup.get_by_label('文献矩阵', exact=True).check()
        self.assertTrue(popup.is_visible())
        popup.get_by_role('searchbox').fill('自定义重点')
        popup.get_by_role('button', name='新建标签：自定义重点').click()
        self.page.locator('#mdDashSave').click()
        self.page.wait_for_function("document.querySelector('#mdSaveState')?.textContent==='已保存'")
        path = self.page.evaluate('mdProj.path')
        data = self.page.evaluate("async path=>api('/api/project/note?project_id='+mdDashProject+'&path='+encodeURIComponent(path))", path)
        self.assertEqual(data['category_id'], 'literature')
        self.assertEqual(data['tags'], ['精读', '文献矩阵', '自定义重点'])
        self.assertTrue(data['content'].startswith('---\n'))
        self.page.locator('#mdDashBackFiles').click()
        self.page.locator('[data-pn-file]').filter(has_text='标签下拉验证').click()
        self.page.locator('.nb-note-properties').get_by_role('button', name='移除标签 精读').click()
        self.page.locator('#mdDashSave').click()
        self.page.wait_for_function("document.querySelector('#mdSaveState')?.textContent==='已保存'")
        self.assertEqual(self.errors, [])

    def test_batch_tags_and_full_body_search(self):
        self.notes()
        self.page.evaluate("""async()=>{
          for(let i=0;i<2;i++)await api('/api/project/note/save',{project_id:mdDashProject,path:'',title:'批量标签'+i,
            content:'A'.repeat(800)+'DEEP_SEARCH_MARKER',metadata:{category_id:'planning',tags:[]}});
          await mdDashLoad();
        }""")
        self.page.locator('[data-category="planning"]').click()
        self.page.locator('#mdDashSearch').fill('DEEP_SEARCH_MARKER')
        self.page.locator('[data-select-note]').first.wait_for()
        self.page.locator('[data-select-note]').first.check()
        self.page.locator('[data-select-note]').nth(1).check()
        self.page.get_by_role('button', name='批量整理（2）').click()
        dialog = self.page.get_by_role('dialog', name='批量整理', exact=True)
        dialog.get_by_role('button', name='添加标签（可选）').click()
        dialog.get_by_role('searchbox').fill('批量重点')
        dialog.get_by_role('button', name='新建标签：批量重点').click()
        dialog.get_by_role('button', name='完成', exact=True).click()
        dialog.get_by_role('button', name='应用到所选笔记').click()
        dialog.wait_for(state='detached')
        self.page.wait_for_function("[...document.querySelectorAll('.nb-row-tags')].filter(e=>e.textContent.includes('批量重点')).length===2")
        self.assertEqual(self.page.locator('.nb-row-tags').filter(has_text='批量重点').count(), 2)
        self.assertEqual(self.errors, [])
