import io
import unittest
from PIL import Image
from tests import test_web_runtime as runtime
from tests import test_project_workspace_runtime as workspace
from tests import test_project_tasks_runtime as tasks
from tests import test_connected_research_runtime as connected


@unittest.skipUnless(runtime.sync_playwright and runtime.EDGE,'Requires Playwright and Edge')
class WorkspaceProductRuntimeTest(unittest.TestCase):
    setUpClass=classmethod(runtime.WebRuntimeTest.setUpClass.__func__)
    setUp=runtime.WebRuntimeTest.setUp
    load_page=runtime.WebRuntimeTest.load_page
    seed=workspace.ProjectWorkspaceRuntimeTest.seed
    use_model=connected.ConnectedResearchRuntimeTest.use_model
    poll_json=tasks.ProjectTasksRuntimeTest.poll_json

    def test_direct_expert_chat_template_image_and_usage(self):
        self.seed()
        with tasks.TaskModelFixture() as model:
            self.use_model(model.url)
            self.page.wait_for_function("b=>[...document.querySelector('#pwRecipient').options].some(o=>o.value==='binding:'+b)",arg=self.binding)
            self.page.locator('#pwRecipient').select_option('binding:'+self.binding)
            self.assertEqual(self.page.locator('#pwSend').get_attribute('aria-label'),'发送')
            self.assertTrue(self.page.locator('#pwPrepareTask').is_visible())
            self.page.locator('.pw-compose [data-pick]').click()
            self.page.get_by_text('图片图注与润色',exact=True).first.wait_for()
            self.page.keyboard.press('Escape')
            buffer=io.BytesIO();Image.new('RGB',(48,36),'red').save(buffer,format='PNG')
            self.page.locator('#pwImageInput').set_input_files({'name':'chat-figure.png','mimeType':'image/png','buffer':buffer.getvalue()})
            self.page.get_by_text('chat-figure.png',exact=False).first.wait_for()
            self.page.locator('#pwAsk').fill('Hello expert, describe this image')
            with self.page.expect_response(lambda response:response.url.endswith('/api/agent')) as sent:self.page.locator('#pwSend').click()
            self.assertTrue(sent.value.ok,str(sent.value.status))
            self.page.wait_for_function("document.querySelector('#pwMessages')?.textContent.includes('PROJECT_TASK_REAL_OUTPUT')",timeout=30000)
            self.assertEqual(self.page.request.get(self.url+'api/project-tasks?project_id='+self.pid).json()['tasks'],[])
            self.assertEqual(model.requests[-1]['messages'][-1]['content'][1]['type'],'image_url')
            self.page.locator('#usageStats').click()
            self.page.locator('.usage-kpis').wait_for()
            self.page.locator('[data-view=requests]').click()
            self.page.locator('.usage-table td').filter(has_text='成功').first.wait_for()
            self.page.locator('[data-view=context]').click()
            self.page.locator('.usage-card summary').first.click()
            self.assertIn('图片：1',self.page.locator('.usage-legend').first.inner_text())
        self.assertEqual(self.errors,[])

    def test_batch_images_caption_conversion_download_and_channels(self):
        self.seed()
        with tasks.TaskModelFixture() as model:
            self.use_model(model.url)
            self.page.evaluate("()=>openTab('toolbox')")
            self.page.locator('[data-tb-tool=caption]').click()
            self.page.locator('[name=project_id]').select_option(self.pid)
            buffer=io.BytesIO();Image.new('RGB',(48,36),'blue').save(buffer,format='PNG')
            self.page.locator('[data-tb-image-input]').set_input_files([{'name':name,'mimeType':'image/png','buffer':buffer.getvalue()} for name in ('a.png','b.png')])
            self.page.wait_for_function("document.querySelectorAll('[data-tb-image-list] li').length===2&&!document.querySelector('[data-tb-run]').disabled")
            self.page.locator('[data-tb-run]').click()
            self.page.wait_for_function("document.querySelector('[data-tb-output]').value.includes('PROJECT_TASK_REAL_OUTPUT')&&!document.querySelector('[data-tb-run]').disabled",timeout=30000)
            self.assertEqual(len(model.requests),2)
            self.page.locator('[data-tb-tool=image_convert]').click()
            self.page.locator('[name=format]').select_option('jpeg')
            self.page.locator('[data-tb-run]').click()
            self.page.wait_for_function("document.querySelectorAll('[data-tb-downloads] a').length===2&&!document.querySelector('[data-tb-run]').disabled",timeout=30000)
            href=self.page.locator('[data-tb-downloads] a').first.get_attribute('href')
            response=self.page.request.get(self.url.rstrip('/')+href)
            self.assertTrue(response.ok);self.assertTrue(response.body().startswith(b'\xff\xd8'))
        self.page.evaluate("()=>{setSec='channels';openTab('settings');}")
        self.page.locator('[data-channel=telegram]').click()
        self.page.locator('[name=token]').fill('123456:ABCDEFGHIJKLMNO123456789')
        self.page.locator('[name=chat_id]').fill('123456')
        self.page.locator('[name=enabled]').check()
        self.page.locator('.channel-form [type=submit]').click()
        self.page.get_by_text('配置已保存；可发送测试消息验证。',exact=True).wait_for()
        self.assertEqual(self.errors,[])

    def test_task_context_menu_delete_and_restore(self):
        self.seed()
        self.page.wait_for_function("b=>[...document.querySelector('#pwRecipient').options].some(o=>o.value==='binding:'+b)",arg=self.binding)
        self.page.locator('#pwRecipient').select_option('binding:'+self.binding)
        self.page.locator('#pwAsk').fill('Task for context menu')
        self.page.locator('#pwPrepareTask').click()
        self.page.locator('[data-pw-start]').wait_for()
        runtime.workspace_section(self.page,'tasks')
        self.page.locator('[data-ec-view=board]').click()
        card=self.page.locator('[data-ec-task]').first;card.wait_for();card.click(button='right')
        self.page.get_by_role('menuitem',name='从看板删除',exact=True).click()
        self.page.get_by_role('button',name='从看板删除',exact=True).click()
        self.page.wait_for_function("document.querySelectorAll('[data-ec-task]').length===0")
        self.page.locator('[data-ec-trash]').click()
        self.page.locator('[data-ec-task]').first.click(button='right')
        self.page.get_by_role('menuitem',name='恢复到看板',exact=True).click()
        self.page.wait_for_function("document.querySelectorAll('[data-ec-task]').length===0")
        self.page.locator('[data-ec-trash]').click()
        self.page.locator('[data-ec-task]').first.wait_for()
        self.assertEqual(self.errors,[])

    def drop(self,selector,files):
        self.page.locator(selector).evaluate('''(host,files)=>{
            const transfer=new DataTransfer();for(const file of files)transfer.items.add(new File([new Uint8Array(file.data)],file.name,{type:file.type}));
            for(const type of ['dragenter','dragover','drop'])host.dispatchEvent(new DragEvent(type,{bubbles:true,cancelable:true,dataTransfer:transfer}));
        }''',files)

    def test_drag_upload_chat_library_and_toolbox(self):
        self.seed()
        self.drop('.pw-chat',[{'name':'drag-note.txt','type':'text/plain','data':list(b'DRAG_FILE_EVIDENCE')}])
        self.page.locator('#pwRefs').get_by_text('drag-note.txt',exact=True).wait_for()
        runtime.workspace_section(self.page,'files')
        self.drop('#pwDocumentPane',[{'name':'drag-note.txt','type':'text/plain','data':list(b'KEEP_ORIGINAL')}])
        self.page.wait_for_function("document.querySelector('#pwNotice')?.textContent.includes('已上传 1/1')")
        self.page.evaluate("()=>openTab('toolbox')")
        self.page.locator('.paper-toolbox').wait_for()
        self.page.locator('[data-tb-tool=caption]').click()
        buffer=io.BytesIO();Image.new('RGB',(20,20),'green').save(buffer,format='PNG')
        self.drop('.paper-toolbox',[{'name':'dropped.png','type':'image/png','data':list(buffer.getvalue())}])
        self.page.locator('[data-tb-image-list]').get_by_text('dropped.png',exact=False).wait_for()
        self.assertEqual(self.page.locator('[data-tb-tool=caption]').get_attribute('aria-pressed'),'true')
        self.assertEqual(self.errors,[])
