"""Tool styles use the existing template library and survive actual model execution."""
import http.server
import json
from pathlib import Path
import threading
import unittest

import project_notes
import prompt_templates
import workflow
from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace


class PromptModel:
    def __enter__(self):
        self.requests=[];self.started=threading.Event();self.release=threading.Event();self.release.set()
        fixture=self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                fixture.requests.append(body);fixture.started.set();fixture.release.wait(20)
                content='TRANSLATED_RESULT '+body['messages'][-1]['content']
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
                self.wfile.write(json.dumps({'choices':[{'message':{'role':'assistant','content':content},'finish_reason':'stop'}]}).encode())
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.url=f'http://127.0.0.1:{self.server.server_address[1]}/v1'
        return self
    def __exit__(self,*args):
        self.release.set();self.server.shutdown();self.server.server_close()


@unittest.skipUnless(web.sync_playwright and web.EDGE,'Requires Playwright and Edge')
class ToolboxPromptsRuntimeTest(unittest.TestCase):
    setUpClass=classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp=web.WebRuntimeTest.setUp
    load_page=web.WebRuntimeTest.load_page
    seed=workspace.ProjectWorkspaceRuntimeTest.seed
    use_model=workspace.ProjectWorkspaceRuntimeTest.use_model
    poll_json=workspace.ProjectWorkspaceRuntimeTest.poll_json

    def open_toolbox(self):
        self.page.locator('#sidebar [data-nav="toolbox"]').click()
        self.page.locator('#paperToolboxPage').wait_for()

    def test_global_styles_edits_and_per_tool_drafts_reach_model_and_quick_panel(self):
        self.load_page();self.open_toolbox()
        self.assertTrue(self.page.locator('[data-tb-pick-template]').is_disabled())
        self.assertEqual(self.page.locator('[data-tb-preset] optgroup').evaluate_all('groups=>groups.map(group=>group.label)'),['学术写作','对照与术语','阅读与交流'])
        self.page.locator('[data-tb-preset]').select_option('translate_abstract')
        self.assertIn('摘要',self.page.locator('[data-tb-prompt-purpose]').inner_text())
        self.assertIn('不强行补齐',self.page.locator('[data-tb-prompt]').input_value())
        self.page.locator('[data-tb-preset]').select_option('translate_natural')
        self.page.locator('[data-tb-prompts] summary').click()
        self.page.locator('[data-tb-prompt]').fill('GLOBAL_TRANSLATE_STYLE')
        self.page.locator('[data-tb-tool="polish"]').click()
        self.assertNotEqual(self.page.locator('[data-tb-prompt]').input_value(),'GLOBAL_TRANSLATE_STYLE')
        self.assertEqual(self.page.locator('[data-tb-preset] optgroup').evaluate_all('groups=>groups.map(group=>group.label)'),['学术表达','清晰与简洁','投稿与沟通'])
        self.page.locator('[data-tb-preset]').select_option('polish_response')
        self.assertIn('审稿回复',self.page.locator('[data-tb-prompt-purpose]').inner_text())
        self.assertIn('不声称已经完成',self.page.locator('[data-tb-prompt]').input_value())
        self.page.locator('[data-tb-preset]').select_option('polish_concise')
        self.page.locator('[data-tb-prompt]').fill('GLOBAL_POLISH_STYLE')
        self.page.locator('[data-tb-tool="translate"]').click()
        self.assertEqual(self.page.locator('[data-tb-prompt]').input_value(),'GLOBAL_TRANSLATE_STYLE')
        with PromptModel() as model:
            self.use_model(model.url)
            self.page.locator('[data-tb-text]').fill('Source $x=2$ [1]')
            self.page.locator('[data-tb-run]').click()
            self.page.wait_for_function("document.querySelector('[data-tb-output]').value.includes('TRANSLATED_RESULT')")
            self.assertIn('GLOBAL_TRANSLATE_STYLE',model.requests[-1]['messages'][0]['content'])
            self.assertNotIn('GLOBAL_POLISH_STYLE',model.requests[-1]['messages'][0]['content'])
            self.assertIn('$x=2$ [1]',self.page.locator('[data-tb-output]').input_value())
        self.page.locator('[data-tb-tool="references"]').click()
        self.assertTrue(self.page.locator('[data-tb-prompts]').is_hidden())
        self.page.locator('[data-tb-tool="translate"]').click()
        for theme in ('dark','light'):
            self.page.evaluate('theme=>setTheme(theme)',theme)
            self.page.locator('.ptb-body').evaluate('element=>element.scrollTop=0')
            path=Path(web.ROOT)/'.codex-tasks/20261028-toolbox-prompts/screenshots';path.mkdir(parents=True,exist_ok=True)
            self.page.screenshot(path=str(path/('toolbox-prompts-'+theme+'.png')))
        self.page.evaluate('''()=>{const key='rw-paper-toolbox',saved=JSON.parse(localStorage.getItem(key));saved.prompt_configs.translate.original_prompt='OLD_BUILTIN_TEXT';saved.prompt_configs.polish.prompt='OLD_BUILTIN_TEXT';saved.prompt_configs.polish.original_prompt='OLD_BUILTIN_TEXT';localStorage.setItem(key,JSON.stringify(saved));}''')
        self.page.reload(wait_until='networkidle');self.page.locator('#paperToolboxPage').wait_for()
        self.assertEqual(self.page.locator('[data-tb-prompt]').input_value(),'GLOBAL_TRANSLATE_STYLE')
        self.page.locator('[data-tb-tool="polish"]').click()
        self.assertIn('以简洁、清晰为目标',self.page.locator('[data-tb-prompt]').input_value())
        self.page.locator('[data-tb-tool="translate"]').click()
        self.page.locator('#sidebar [data-nav="agent"]').click();self.page.locator('#paperToolbox').click()
        self.page.locator('.paper-toolbox:not(.ptb-page)').wait_for()
        self.assertEqual(self.page.locator('[data-tb-prompt]').input_value(),'GLOBAL_TRANSLATE_STYLE')
        self.page.set_viewport_size({'width':720,'height':700})
        self.assertTrue(self.page.locator('[data-tb-run]').is_visible())
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        self.assertEqual(self.errors,[])

    def test_project_variables_save_and_immutable_prompt_history_with_actual_dispatch(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            note=project_notes.write(db,self.pid,'','PROJECT_STYLE {{focus}} for {{project.title}}',title='工具箱共享模板',metadata={'note_type':'prompt_template','category_id':'prompt_templates','tags':['翻译','领域专用']})
        self.open_toolbox();self.page.locator('[name=project_id]').select_option(self.pid)
        self.page.locator('[data-tb-pick-template]').click()
        self.page.locator('[data-template="'+note['template_id']+'"]').click()
        dialog=self.page.get_by_role('dialog',name='应用提示词模板',exact=True)
        dialog.get_by_label('focus',exact=True).fill('materials')
        dialog.get_by_role('button',name='用于当前工具',exact=True).click();dialog.wait_for(state='detached')
        self.page.locator('[data-tb-prompts] summary').click()
        prompt=self.page.locator('[data-tb-prompt]').input_value()
        self.assertIn('PROJECT_STYLE materials',prompt);self.assertNotIn('{{project.title}}',prompt)
        edited=prompt+' USER_EDITED_STYLE';self.page.locator('[data-tb-prompt]').fill(edited)
        self.page.locator('[data-tb-save-template]').click()
        save=self.page.get_by_role('dialog',name='保存为提示词模板',exact=True)
        save.get_by_label('模板名称').fill('工具箱保存的风格')
        save.get_by_role('button',name='保存模板',exact=True).click();save.wait_for(state='detached')
        catalog=self.page.request.post(self.url+'api/prompt-templates/search',data={'project_id':self.pid}).json()['templates']
        saved=next(item for item in catalog if item['title']=='工具箱保存的风格')
        self.assertIn('领域专用',saved['tags']);self.assertIn('翻译',saved['tags'])
        with PromptModel() as model:
            model.release.clear();self.use_model(model.url)
            self.page.locator('[data-tb-text]').fill('Original $D$ [3]')
            with self.page.expect_response(lambda response:response.url.endswith('/api/paper-toolbox/start')) as response:
                self.page.locator('[data-tb-run]').click()
            started=response.value.json();self.assertTrue(response.value.ok,response.value.text())
            self.assertTrue(model.started.wait(10))
            self.assertTrue(self.page.locator('[data-tb-prompt]').is_disabled())
            with workflow.WorkflowDB(self.database_path) as db:
                current=project_notes.read(db,self.pid,note['path'])
                project_notes.write(db,self.pid,note['path'],'LATER_TEMPLATE_CONTENT',expected_hash=current['content_hash'])
            retry=self.page.request.post(self.url+'api/paper-toolbox/start',data={'tool':'translate','text':'Original $D$ [3]','project_id':self.pid,'request_key':started['request_key'],'prompt_preset':'translate_academic','prompt':edited,'prompt_template':self.page.evaluate("JSON.parse(localStorage.getItem('rw-paper-toolbox')).prompt_configs.translate.reference")})
            self.assertTrue(retry.ok,retry.text());self.assertEqual(retry.json()['run_id'],started['run_id'])
            model.release.set()
            result=self.poll_json('/api/paper-toolbox/result?run_id='+started['run_id']+'&project_id='+self.pid,lambda data:data['status'] in ('done','failed'))
            self.assertEqual(result['status'],'done',result)
            self.assertEqual(result['prompt']['text'],edited);self.assertTrue(result['prompt']['modified'])
            instruction=model.requests[-1]['messages'][0]['content']
            self.assertIn(edited,instruction);self.assertNotIn('LATER_TEMPLATE_CONTENT',instruction)
            with workflow.WorkflowDB(self.database_path) as db:
                history=prompt_templates.history(db,self.pid,note['template_id'])
                self.assertEqual(history[0]['input_snapshot']['applied_prompt'],edited)
                self.assertEqual(history[0]['input_snapshot']['revision_id'],result['prompt']['revision_id'])
            self.page.evaluate('(data)=>PaperToolbox.openResult(data.run,data.pid)',{'run':started['run_id'],'pid':self.pid})
            record=self.page.get_by_role('dialog',name='论文工具处理记录',exact=True)
            record.locator('summary').click();self.assertIn(edited,record.inner_text())
            record.get_by_role('button',name='关闭',exact=True).click()
        self.page.locator('[name=project_id]').select_option(self.other)
        self.assertEqual(self.page.locator('[data-tb-preset]').input_value(),'translate_academic')
        self.assertNotIn('PROJECT_STYLE',self.page.locator('[data-tb-prompt]').input_value())
        self.page.evaluate("pid=>{curTask=pid;openTab('notes');}",self.pid)
        self.page.locator('#mdDashboardBody').wait_for()
        self.page.wait_for_function('pid=>mdDashProject===pid',arg=self.pid)
        self.page.evaluate('path=>Notebook.open(path)',note['path'])
        self.page.locator('#mdSource').wait_for()
        self.page.get_by_role('button',name='使用记录',exact=True).click()
        history_dialog=self.page.get_by_role('dialog',name='模板使用记录',exact=True)
        history_dialog.locator('summary').click()
        self.assertIn(edited,history_dialog.inner_text())
        self.assertIn('本次已修改',history_dialog.inner_text())
        self.assertEqual(self.errors,[])
