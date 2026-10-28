"""Selectors show each team/expert once while retaining exact member and node dispatch."""
from pathlib import Path
import unittest

import project_bindings
import workflow
import workflow_layout
from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace
from tests import test_project_tasks_runtime as tasks_runtime


@unittest.skipUnless(web.sync_playwright and web.EDGE,'Requires Playwright and Edge')
class ExecutorPickerRuntimeTest(unittest.TestCase):
    setUpClass=classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp=web.WebRuntimeTest.setUp
    load_page=web.WebRuntimeTest.load_page
    seed=workspace.ProjectWorkspaceRuntimeTest.seed
    use_model=workspace.ProjectWorkspaceRuntimeTest.use_model
    poll_json=workspace.ProjectWorkspaceRuntimeTest.poll_json

    def setup_catalog(self):
        self.seed()
        self.local={};self.foreign={}
        with workflow.WorkflowDB(self.database_path) as db:
            db.assistant_save('picker-analyst',name='选择器分析专家',enabled=1,permission='full_access')
            for pid in (self.pid,self.other):
                for name in ('文献抽取','文件撰写'):
                    team=db.team_add(name,'fixed',project_id=pid)
                    db.team_member_add(team['id'],'负责人',role='leader',assistant_id='workspace-writer')
                    member=db.team_member_add(team['id'],'证据分析员',role='member',assistant_id='picker-analyst')
                    alias=db.team_member_add(team['id'],'分析兼任角色',role='member',assistant_id='picker-analyst',prompt='EXACT_MEMBER_ROLE')
                    grant=project_bindings.register(db,self.pid,'team',team['id'])
                    (self.local if pid==self.pid else self.foreign)[name]={'team':team,'member':member,'alias':alias,'binding':grant['id']}
        self.page.evaluate('async()=>ProjectWorkspace.refresh()')
        self.page.wait_for_function('binding=>[...document.querySelector("#pwRecipient").options].some(option=>option.value==="binding:"+binding)',arg=self.local['文件撰写']['binding'])

    def screenshot(self,name):
        path=Path(web.ROOT)/'.codex-tasks/20261028-executor-picker/screenshots'
        path.mkdir(parents=True,exist_ok=True);self.page.screenshot(path=str(path/name))

    def test_project_picker_has_one_preset_and_one_assistant_without_roster_expansion(self):
        self.setup_catalog()
        options=self.page.locator('#pwRecipient option').evaluate_all('items=>items.map(item=>({value:item.value,text:item.textContent}))')
        labels=[item['text'] for item in options]
        self.assertEqual(labels.count('团队 · 文件撰写'),1)
        self.assertEqual(labels.count('团队 · 文献抽取'),1)
        self.assertEqual(labels.count('专家 · 选择器分析专家'),1)
        self.assertEqual(len(labels),len(set(labels)))
        self.assertFalse(any(item['value'].startswith('member:') for item in options))
        self.assertEqual(next(item['value'] for item in options if item['text']=='团队 · 文件撰写'),'binding:'+self.local['文件撰写']['binding'])
        self.page.locator('#pwRecipient').select_option('binding:'+self.local['文件撰写']['binding'])
        self.page.locator('#pwRecipientDetailControl').wait_for(state='visible')
        members=self.page.locator('#pwRecipientDetail option').all_text_contents()
        self.assertEqual(len(members),3)
        self.assertEqual(members.count('证据分析员'),1)
        self.assertNotIn('分析兼任角色',members)
        self.page.locator('#pwRecipient').focus();self.page.keyboard.press('ArrowDown')
        self.screenshot('project-executor-picker.png');self.page.keyboard.press('Escape')
        self.page.locator('#pwRecipient').select_option('binding:'+self.binding)
        self.assertTrue(self.page.locator('#pwRecipientDetailControl').is_hidden())
        self.assertEqual(self.errors,[])

    def test_member_target_and_legacy_foreign_selection_keep_exact_dispatch(self):
        self.setup_catalog()
        chosen=self.foreign['文件撰写']
        target='member:'+chosen['binding']+':'+chosen['alias']['id']
        self.page.evaluate('data=>{const key="rw-workspace:"+data.pid;const saved=JSON.parse(localStorage.getItem(key))||{};saved.target=data.target;saved.scope="project";saved.surface="chat";localStorage.setItem(key,JSON.stringify(saved));}',{'pid':self.pid,'target':target})
        self.page.reload(wait_until='networkidle')
        self.page.wait_for_function('value=>document.querySelector("#pwRecipientDetail")?.value===value',arg=target)
        self.assertEqual(self.page.locator('#pwRecipient').input_value(),'binding:'+chosen['binding'])
        self.assertEqual(self.page.locator('#pwRecipient option').all_text_contents().count('团队 · 文件撰写'),1)
        self.assertIn('分析兼任角色',self.page.locator('#pwRecipientDetail').inner_text())
        self.assertNotIn('证据分析员',self.page.locator('#pwRecipientDetail').inner_text())
        with tasks_runtime.TaskModelFixture() as model:
            self.use_model(model.url)
            self.page.locator('#pwAsk').fill('EXACT_PICKER_MEMBER_DISPATCH')
            self.page.locator('#pwPrepareTask').click()
            self.page.locator('[data-pw-start]').wait_for()
            task=self.page.request.get(self.url+'api/project-tasks?project_id='+self.pid).json()['tasks'][0]
            self.assertEqual(task['executor_binding_id'],chosen['binding'])
            self.assertEqual(task['member_id'],chosen['alias']['id'])
            with self.page.expect_response(lambda response:response.url.endswith('/api/project-tasks/start')) as response:
                self.page.locator('[data-pw-start]').click()
            self.assertTrue(response.value.ok,response.value.text())
            completed=self.poll_json('/api/project-tasks/detail?project_id='+self.pid+'&id='+task['id'],lambda data:data['task']['status'] in ('awaiting_review','failed','blocked'))['task']
            self.assertEqual(completed['status'],'awaiting_review',(completed.get('latest_run') or {}).get('error'))
            self.page.locator('#pwMessages [data-pw-output]').wait_for(timeout=10000)
            self.page.locator('[data-execution-detail]').click()
            self.page.locator('#pwApprove').wait_for(timeout=10000)
            detail=self.page.request.get(self.url+'api/project-tasks/detail?project_id='+self.pid+'&id='+task['id']).json()['task']
            actor=detail['latest_run']['executor_snapshot']
            self.assertEqual(actor['assistant_id'],'picker-analyst')
            self.assertEqual(actor['member']['prompt'],'EXACT_MEMBER_ROLE')
            self.assertEqual(actor['team']['project_id'],self.pid)
        self.assertEqual(self.errors,[])

    def test_task_and_graph_pickers_group_presets_and_disambiguate_custom_names(self):
        self.setup_catalog()
        chosen=self.foreign['文件撰写']
        with workflow.WorkflowDB(self.database_path) as db:
            workflow_layout.ensure_schema(db)
            layout=workflow_layout.save_layout(db,{'name':'Picker identity flow','nodes':[{'id':'evidence','step':'task','label':'证据分析','executor_binding_id':chosen['binding'],'member_id':chosen['alias']['id']}],'edges':[]})['layout']
            workflow_layout.select_layout(db,{'task_id':self.pid,'layout_id':layout['id']})
            for name in ('自定义同名团队','自定义同名团队'):
                team=db.team_add(name,'custom',project_id=self.pid)
                db.team_member_add(team['id'],'Leader',role='leader',assistant_id='workspace-writer')
        self.page.evaluate('data=>ProjectTasks.compose(data.pid,{executor_binding_id:data.binding,member_id:data.member})',{'pid':self.pid,'binding':chosen['binding'],'member':chosen['alias']['id']})
        self.page.wait_for_function('value=>document.querySelector("[data-pt-executor]")?.value===value',arg=chosen['binding'])
        labels=self.page.locator('[data-pt-executor] option').all_text_contents()
        self.assertEqual(labels.count('团队 · 文件撰写'),1)
        self.assertEqual(len(labels),len(set(labels)))
        self.assertEqual(len([label for label in labels if '自定义同名团队' in label]),2)
        self.assertEqual(self.page.locator('[data-pt-member]').input_value(),chosen['alias']['id'])
        self.page.locator('dialog.pt-dialog [data-close]').click()
        web.workspace_section(self.page,'workflow')
        self.page.locator('[data-pwg-binding]').wait_for()
        self.assertEqual(self.page.locator('[data-pwg-binding]').input_value(),chosen['binding'])
        self.assertEqual(self.page.locator('[data-pwg-binding] option').all_text_contents().count('团队 · 文件撰写'),1)
        self.assertEqual(self.page.locator('[data-pwg-member]').input_value(),chosen['alias']['id'])
        self.page.locator('[data-pwg-prompt]').fill('Retain this exact team and member assignment')
        self.page.locator('[data-pwg-save]').click()
        self.page.wait_for_function("document.querySelector('[data-pwg-notice]').textContent.includes('已保存')||document.querySelector('[data-pwg-error]').textContent")
        self.assertEqual(self.page.locator('[data-pwg-error]').inner_text(),'')
        current=self.page.request.get(self.url+'api/workflow/layouts/project?project_id='+self.pid).json()['layout']
        self.assertEqual(current['nodes'][0]['executor_binding_id'],chosen['binding'])
        self.assertEqual(current['nodes'][0]['member_id'],chosen['alias']['id'])
        self.assertEqual(self.errors,[])
