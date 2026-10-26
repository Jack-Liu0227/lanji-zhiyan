import unittest
from tests import test_web_runtime as web


@unittest.skipUnless(web.sync_playwright and web.EDGE, "Requires Playwright and Edge")
class PromptUITest(unittest.TestCase):
    setUpClass = classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp = web.WebRuntimeTest.setUp
    load_page = web.WebRuntimeTest.load_page

    def create_template(self):
        self.load_page()
        self.page.evaluate("openTab('notes')")
        self.page.get_by_role('button', name='新建模板', exact=True).click()
        self.page.locator('#mdNoteTitle').fill('浏览器模板')
        self.page.locator('#mdSource').fill('Review {{focus}} for {{project.title}}')
        self.page.locator('#mdDashSave').click()
        self.page.wait_for_function("mdProj?.metadata?.template_id && document.querySelector('#mdSaveState')?.textContent==='已保存'")
        return self.page.evaluate('({project:mdProj.pid,template:mdProj.metadata.template_id,path:mdProj.path})')

    def test_note_to_agent_preview_and_structured_request(self):
        created = self.create_template()
        self.page.get_by_role('button', name='应用到 Agent', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='应用提示词模板', exact=True)
        dialog.get_by_role('button', name='预览效果').click()
        self.page.wait_for_function("document.querySelector('dialog .nb-error')?.textContent.includes('focus')")
        dialog.get_by_label('focus', exact=True).fill('experimental controls')
        dialog.get_by_role('button', name='预览效果').click()
        self.page.wait_for_function("document.querySelector('[data-result]')?.textContent.includes('experimental controls')")
        dialog.get_by_role('button', name='应用到本轮任务').click()
        dialog.wait_for(state='detached')
        self.page.locator('#ask').wait_for()
        self.assertIn('浏览器模板', self.page.locator('.pt-composer').inner_text())
        self.page.evaluate("""()=>{
            requireModelKey=async()=>true;
            followChatRun=async run=>{window.capturedTemplateRequest=run.request;};
        }""")
        self.page.locator('#ask').fill('Perform review')
        self.page.locator('#send').click()
        self.page.wait_for_function('window.capturedTemplateRequest')
        request = self.page.evaluate('window.capturedTemplateRequest')
        self.assertEqual(request['template_ref']['template_id'], created['template'])
        self.assertEqual(request['template_ref']['variables'], {'focus': 'experimental controls'})
        self.assertTrue(request['template_ref']['revision_id'])
        self.page.evaluate("appendOne(document.querySelector('#msgs'),{type:'template_used',title:'浏览器模板',revision:1,revision_id:'revision-test'})")
        self.assertIn('v1', self.page.locator('.pt-used').inner_text())
        self.assertEqual(self.errors, [])

    def test_template_settings_binding_and_copy(self):
        created = self.create_template()
        self.page.get_by_role('button', name='模板设置', exact=True).click()
        settings = self.page.get_by_role('dialog', name='模板设置', exact=True)
        field = settings.locator('fieldset').filter(has=self.page.locator('legend', has_text='focus'))
        field.get_by_role('combobox', name='类型', exact=True).select_option('enum')
        field.get_by_label('下拉选项（每行一项）').fill('controls\nresults')
        field.get_by_label('默认值', exact=True).fill('controls')
        settings.get_by_role('button', name='保存模板设置').click()
        settings.wait_for(state='detached')
        self.page.get_by_role('button', name='应用到 Agent', exact=True).click()
        apply = self.page.get_by_role('dialog', name='应用提示词模板', exact=True)
        self.assertEqual(apply.get_by_label('focus', exact=True).input_value(), 'controls')
        apply.get_by_role('button', name='默认绑定…', exact=True).click()
        binding = self.page.get_by_role('dialog', name='默认模板绑定', exact=True)
        binding.get_by_label('绑定范围').select_option('step')
        binding.get_by_label('绑定对象').select_option('review')
        binding.get_by_role('button', name='保存默认绑定').click()
        self.page.wait_for_function("document.querySelector('dialog[aria-label=默认模板绑定] .nb-error')?.textContent==='默认绑定已保存。'")
        data = self.page.evaluate("async p=>api('/api/prompt-templates/bindings',{project_id:p})", created['project'])
        self.assertEqual(data['bindings'][0]['scope_id'], 'review')
        self.assertTrue(data['bindings'][0]['revision_id'])
        binding.get_by_role('button', name='关闭', exact=True).click()
        apply.get_by_role('button', name='关闭', exact=True).click()
        self.page.get_by_role('button', name='复制模板', exact=True).click()
        self.page.wait_for_function('old=>mdProj?.metadata?.template_id && mdProj.metadata.template_id!==old', arg=created['template'])
        self.assertEqual(self.errors, [])

    def test_workflow_template_selection_reaches_request(self):
        created = self.create_template()
        self.page.evaluate("openTab('workflow')")
        self.page.locator('#wv-ask').wait_for()
        self.page.locator('.pt-composer').get_by_role('button', name='提示词模板', exact=True).click()
        picker = self.page.get_by_role('dialog', name='选择提示词模板', exact=True)
        picker.locator(f'[data-template="{created["template"]}"]').click()
        dialog = self.page.get_by_role('dialog', name='应用提示词模板', exact=True)
        dialog.get_by_label('focus', exact=True).fill('workflow verification')
        dialog.get_by_role('button', name='应用到本轮任务').click()
        dialog.wait_for(state='detached')
        self.page.evaluate("""()=>{
            requireModelKey=async()=>true;
            wfFollowRun=async run=>{window.capturedWorkflowRequest=run.request;};
        }""")
        self.page.locator('#wv-ask').fill('Review this project')
        self.page.locator('#wv-send').click()
        self.page.wait_for_function('window.capturedWorkflowRequest')
        self.assertEqual(self.page.evaluate('capturedWorkflowRequest.template_ref.template_id'), created['template'])
        self.assertEqual(self.errors, [])
