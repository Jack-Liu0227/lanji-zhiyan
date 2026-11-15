"""Users manage templates directly from visible toolbox controls."""
from pathlib import Path
import unittest

from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace
from tests.test_toolbox_prompts_runtime import PromptModel


@unittest.skipUnless(web.sync_playwright and web.EDGE,'Requires Playwright and Edge')
class PromptLibraryRuntimeTest(unittest.TestCase):
    setUpClass=classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp=web.WebRuntimeTest.setUp
    load_page=web.WebRuntimeTest.load_page
    seed=workspace.ProjectWorkspaceRuntimeTest.seed
    use_model=workspace.ProjectWorkspaceRuntimeTest.use_model

    def manager(self,quick=False):
        if quick:self.page.locator('#paperToolbox').click()
        else:
            self.page.locator('#sidebar [data-nav="toolbox"]').click()
            self.page.locator('#paperToolboxPage').wait_for()
        self.page.get_by_role('button',name='管理模板',exact=True).click()
        manager=self.page.get_by_role('dialog',name='提示词模板库',exact=True)
        manager.locator('[data-pl-row]').first.wait_for()
        return manager

    def catalog(self,pid):
        response=self.page.request.post(self.url+'api/prompt-templates/library',data={'project_id':pid})
        self.assertTrue(response.ok,response.text());return response.json()

    def save(self,manager):
        with self.page.expect_response(lambda response:response.url.endswith('/api/prompt-templates/save')) as response:
            manager.get_by_role('button',name='保存模板',exact=True).click()
        self.assertTrue(response.value.ok,response.value.text())
        manager.locator('[data-pl-save-state]').filter(has_text='已保存').wait_for()
        return response.value.json()

    def test_visible_new_edit_disable_apply_execute_and_delete(self):
        self.load_page();manager=self.manager();pid=manager.locator('[data-pl-project]').input_value()
        self.assertTrue(pid)
        manager.get_by_role('button',name='新建模板',exact=True).click()
        manager.get_by_label('模板名称',exact=True).fill('可管理的工具模板')
        manager.get_by_label('提示词正文',exact=True).fill('LIBRARY_STYLE {{focus}}')
        manager.get_by_label('标签',exact=True).fill('材料，翻译')
        created=self.save(manager)
        self.assertIn(created['template_id'],[item['template_id'] for item in self.catalog(pid)['templates']])
        manager.get_by_label('模板名称',exact=True).fill('重命名的模板')
        manager.get_by_label('提示词正文',exact=True).fill('EDITED_LIBRARY_STYLE {{purpose}}')
        manager.get_by_label('状态',exact=True).select_option('disabled');disabled=self.save(manager)
        self.assertEqual(disabled['template_id'],created['template_id'])
        self.assertTrue(manager.get_by_role('button',name='用于当前工具',exact=True).is_disabled())
        manager.locator('[data-pl-row="builtin:translate_academic"]').click()
        manager.locator('[data-pl-row="project:'+created['template_id']+'"]').click()
        self.assertEqual(manager.get_by_label('状态',exact=True).input_value(),'disabled')
        manager.get_by_label('状态',exact=True).select_option('active');self.save(manager)
        manager.get_by_role('button',name='用于当前工具',exact=True).click()
        apply=self.page.get_by_role('dialog',name='应用提示词模板',exact=True)
        apply.get_by_label('purpose',exact=True).fill('evidence')
        apply.get_by_role('button',name='用于当前工具',exact=True).click();manager.wait_for(state='detached')
        self.assertEqual(self.page.locator('[name=project_id]').input_value(),pid)
        self.assertEqual(self.page.locator('[data-tb-prompt]').input_value(),'EDITED_LIBRARY_STYLE evidence')
        with PromptModel() as model:
            self.use_model(model.url)
            self.page.locator('[data-tb-text]').fill('Source $D$ [1]')
            self.page.locator('[data-tb-run]').click()
            self.page.wait_for_function("document.querySelector('[data-tb-output]').value.includes('TRANSLATED_RESULT')")
            self.page.wait_for_function("!document.querySelector('[data-tb-stop]')||document.querySelector('[data-tb-stop]').hidden")
            self.assertIn('EDITED_LIBRARY_STYLE evidence',model.requests[-1]['messages'][0]['content'])
        self.page.get_by_role('button',name='管理模板',exact=True).click()
        manager=self.page.get_by_role('dialog',name='提示词模板库',exact=True)
        manager.locator('[data-pl-row="project:'+created['template_id']+'"]').click()
        self.page.request.post(self.url+'api/prompt-templates/bind',data={'project_id':pid,'template_id':created['template_id'],'variables':{'purpose':'evidence'}})
        manager.get_by_role('button',name='删除模板',exact=True).click()
        confirm=self.page.get_by_role('dialog',name='确认操作',exact=True)
        confirm.get_by_role('button',name='删除模板',exact=True).click()
        manager.locator('[data-pl-row="project:'+created['template_id']+'"]').wait_for(state='detached')
        self.assertNotIn(created['template_id'],[item['template_id'] for item in self.catalog(pid)['templates']])
        bindings=self.page.request.post(self.url+'api/prompt-templates/bindings',data={'project_id':pid}).json()['bindings']
        self.assertEqual(bindings,[])
        self.assertNotEqual(self.page.locator('[data-tb-preset]').input_value(),'project')
        self.assertEqual(self.errors,[])

    def test_quick_panel_builtin_copy_category_crud_scope_and_conflict(self):
        self.seed();manager=self.manager(quick=True)
        manager.locator('[data-pl-project]').select_option(self.pid)
        manager.locator('[data-pl-row="builtin:translate_academic"]').click()
        manager.get_by_role('button',name='复制为自定义模板',exact=True).click()
        manager.get_by_label('模板名称',exact=True).fill('复制的学术风格')
        created=self.save(manager);original=created['body']
        manager.get_by_role('button',name='管理分类',exact=True).click()
        categories=self.page.get_by_role('dialog',name='模板分类管理',exact=True)
        categories.get_by_role('button',name='新建分类',exact=True).click()
        add=self.page.get_by_role('dialog',name='新建模板分类',exact=True)
        add.get_by_label('分类名称',exact=True).fill('专用风格')
        add.get_by_role('button',name='保存分类',exact=True).click();add.wait_for(state='detached')
        category=next(c for c in self.catalog(self.pid)['catalog']['categories'] if c['name']=='专用风格')
        categories.locator('[data-pl-edit-category="'+category['id']+'"]').click()
        edit=self.page.get_by_role('dialog',name='编辑模板分类',exact=True)
        edit.get_by_label('分类名称',exact=True).fill('论文专用风格')
        edit.get_by_role('button',name='保存分类',exact=True).click();edit.wait_for(state='detached')
        categories.get_by_role('button',name='关闭',exact=True).click()
        manager.get_by_label('分类',exact=True).select_option(category['id']);self.save(manager)
        manager.get_by_role('button',name='管理分类',exact=True).click()
        categories=self.page.get_by_role('dialog',name='模板分类管理',exact=True)
        categories.locator('[data-pl-delete-category="'+category['id']+'"]').click()
        delete=self.page.get_by_role('dialog',name='删除模板分类',exact=True)
        delete.get_by_role('button',name='迁移并删除分类',exact=True).click();delete.wait_for(state='detached')
        categories.get_by_role('button',name='关闭',exact=True).click()
        manager.get_by_label('提示词正文',exact=True).filter().wait_for()
        self.assertEqual(manager.get_by_label('提示词正文',exact=True).input_value(),original)
        self.assertNotEqual(manager.get_by_label('分类',exact=True).input_value(),category['id'])
        for theme in ('dark','light'):
            self.page.evaluate('theme=>setTheme(theme)',theme)
            path=Path(web.ROOT)/'.codex-tasks/20261028-template-library/screenshots';path.mkdir(parents=True,exist_ok=True)
            self.page.screenshot(path=str(path/('template-manager-'+theme+'.png')))
        manager.locator('[data-pl-project]').select_option(self.other)
        self.page.wait_for_function("!document.querySelector('[data-pl-row^=\"project:\"]')")
        self.assertFalse(any(item['template_id']==created['template_id'] for item in self.catalog(self.other)['templates']))
        manager.locator('[data-pl-project]').select_option(self.pid)
        manager.locator('[data-pl-row="project:'+created['template_id']+'"]').click()
        manager.get_by_label('提示词正文',exact=True).fill('LOCAL_UNSAVED_TEXT')
        current=self.page.request.post(self.url+'api/prompt-templates/inspect',data={'project_id':self.pid,'template_id':created['template_id']}).json()
        external=self.page.request.post(self.url+'api/prompt-templates/save',data={'project_id':self.pid,'template_id':created['template_id'],'expected_hash':current['content_hash'],'title':current['title'],'content':'EXTERNAL_NEW_TEXT'})
        self.assertTrue(external.ok,external.text())
        manager.get_by_role('button',name='保存模板',exact=True).click()
        manager.locator('.nb-error').filter(has_text='其他操作修改').wait_for()
        self.assertEqual(manager.get_by_label('提示词正文',exact=True).input_value(),'LOCAL_UNSAVED_TEXT')
        manager.get_by_role('button',name='关闭',exact=True).click()
        confirm=self.page.get_by_role('dialog',name='确认操作',exact=True)
        confirm.get_by_role('button',name='继续编辑',exact=True).click()
        self.assertTrue(manager.is_visible())
        manager.get_by_role('button',name='刷新',exact=True).click()
        self.page.get_by_role('dialog',name='确认操作',exact=True).get_by_role('button',name='丢弃修改',exact=True).click()
        self.page.wait_for_function("document.querySelector('.prompt-library [name=content]')?.value==='EXTERNAL_NEW_TEXT'")
        self.page.set_viewport_size({'width':720,'height':720})
        self.assertTrue(manager.get_by_role('button',name='新建模板',exact=True).is_visible())
        self.assertTrue(manager.evaluate('element=>element.scrollWidth<=element.clientWidth'))
        self.assertEqual(self.errors,[])

    def test_management_entry_exists_without_project_binding(self):
        self.load_page();before=len(self.page.request.get(self.url+'api/state').json()['tasks'])
        self.page.evaluate('()=>{S.tasks=[];curTask=null;}')
        manager=self.manager()
        self.assertEqual(manager.locator('[data-pl-row^="builtin:"]').count(),12)
        self.assertEqual(manager.locator('[data-pl-row="builtin:caption_academic"]').count(),1)
        self.assertTrue(manager.get_by_role('button',name='新建模板',exact=True).is_disabled())
        self.assertTrue(manager.get_by_label('提示词正文',exact=True).is_visible())
        self.assertEqual(len(self.page.request.get(self.url+'api/state').json()['tasks']),before)
        self.assertEqual(self.errors,[])

    def test_pending_template_read_does_not_replace_a_new_draft(self):
        self.load_page();manager=self.manager()
        manager.get_by_role('button',name='新建模板',exact=True).click()
        manager.get_by_label('模板名称',exact=True).fill('待加载的模板')
        manager.get_by_label('提示词正文',exact=True).fill('SAVED_TEMPLATE_BODY')
        created=self.save(manager)
        manager.locator('[data-pl-row="builtin:translate_academic"]').click()
        pending=[]
        self.page.route('**/api/prompt-templates/inspect',lambda route:pending.append(route))
        with self.page.expect_request(lambda request:request.url.endswith('/api/prompt-templates/inspect')):
            manager.locator('[data-pl-row="project:'+created['template_id']+'"]').click()
        manager.get_by_role('button',name='新建模板',exact=True).click()
        manager.get_by_label('模板名称',exact=True).fill('正在编辑的新模板')
        manager.get_by_label('提示词正文',exact=True).fill('UNSAVED_NEW_DRAFT')
        self.assertEqual(len(pending),1)
        with self.page.expect_response(lambda response:response.url.endswith('/api/prompt-templates/inspect')):
            pending[0].continue_()
        self.page.wait_for_timeout(100)
        self.assertEqual(manager.get_by_label('模板名称',exact=True).input_value(),'正在编辑的新模板')
        self.assertEqual(manager.get_by_label('提示词正文',exact=True).input_value(),'UNSAVED_NEW_DRAFT')
        self.assertEqual(self.errors,[])
