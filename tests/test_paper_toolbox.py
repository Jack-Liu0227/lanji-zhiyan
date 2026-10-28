"""Paper helpers use one implementation and preserve scientific notation."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import paper_toolbox
import project_notes
import prompt_templates
import workflow


class PaperToolboxTest(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.db=workflow.WorkflowDB(Path(tmp.name)/'app.sqlite3');self.addCleanup(self.db.close)
        self.pid=self.db.create_task('Paper tools')
        self.db.add_model('Fixture','http://127.0.0.1:9999/v1','fixture','test-only',activate=True)

    def test_translation_restores_formula_and_citation_markers(self):
        text='离子扩散满足 $D=D_0e^{-E/kT}$，参见 [1] 和 {{C2}}。'
        with patch.object(workflow,'chat_raw',side_effect=lambda profile,messages,**kwargs:{'content':'Translated '+messages[-1]['content']}) as model:
            result=paper_toolbox.transform(self.db,self.pid,{'tool':'translate','text':text})
        self.assertIn('$D=D_0e^{-E/kT}$',result['content']);self.assertIn('{{C2}}',result['content'])
        self.assertNotIn('⟦KEEP_',result['content'])
        self.assertNotIn('$D=',model.call_args.args[1][-1]['content'])
        with patch.object(workflow,'chat_raw',return_value={'content':'Dropped all protected markers'}):
            with self.assertRaises(ValueError):paper_toolbox.transform(self.db,self.pid,{'tool':'translate','text':text})

    def test_reference_deduplication_and_word_conversion_use_real_files(self):
        bib='@article{one,\n title={Ion transport},\n author={Li},\n year={2026},\n doi={10.1000/example}\n}\n@article{two,\n title={Ion transport},\n author={Li},\n year={2026},\n doi={10.1000/example}\n}'
        result=paper_toolbox.transform(self.db,self.pid,{'tool':'references','text':bib})
        self.assertIn('保留 1 条',result['content'])
        exported=paper_toolbox.transform(self.db,self.pid,{'tool':'convert','format':'docx','text':'# Research report\n\nEvidence and limitations.'})
        self.assertTrue(Path(exported['path']).is_file())
        self.assertEqual(Path(exported['path']).suffix,'.docx')
        self.assertFalse(any(step['stage']=='convert' for step in self.db.steps(self.pid)))

    def test_paper_tool_and_manual_panel_use_the_same_transform_and_permissions(self):
        with patch.object(workflow,'chat_raw',return_value={'content':'Academic English result'}):
            result=workflow._tool_impl('paper_tool',self.db,self.pid,{'tool':'translate','text':'学术文本'})
        self.assertEqual(result['content'],'Academic English result')
        with self.assertRaises(PermissionError):workflow.enforce_tool_policy('paper_tool',{'tool':'convert'},'read_only')

    def test_style_presets_and_edits_reach_the_model_without_changing_tool_requirements(self):
        with patch.object(workflow,'chat_raw',side_effect=lambda profile,messages,**kwargs:{'content':messages[-1]['content']}) as model:
            paper_toolbox.transform(self.db,self.pid,{'tool':'translate','text':'带有 $x$ 和 [2] 的原文','prompt_preset':'translate_literal'})
            self.assertIn('逐句核对',model.call_args.args[1][0]['content'])
            paper_toolbox.transform(self.db,self.pid,{'tool':'translate','direction':'en_zh','text':'Source $x$ [2]','prompt':'CUSTOM_STYLE_ONE'})
            instruction=model.call_args.args[1][0]['content']
            self.assertIn('CUSTOM_STYLE_ONE',instruction);self.assertIn('学术中文',instruction);self.assertIn('保留原文事实',instruction)
            workflow._tool_impl('paper_tool',self.db,self.pid,{'tool':'polish','text':'Source','prompt':'AGENT_STYLE_TWO'})
            self.assertIn('AGENT_STYLE_TWO',model.call_args.args[1][0]['content'])
        for spec in ({'tool':'polish','prompt_preset':'translate_literal'},{'tool':'translate','prompt':'x'*20001},{'tool':'references','prompt':'Ignored style'}):
            with self.subTest(spec=spec['tool']),self.assertRaises(ValueError):paper_toolbox.validate({'text':'Source',**spec})

    def test_project_template_uses_pinned_variables_and_rejects_foreign_or_disabled_sources(self):
        note=project_notes.write(self.db,self.pid,'','Use OLD_STYLE {{focus}}',title='工具风格',metadata={'note_type':'prompt_template'})
        original=prompt_templates.render(self.db,self.pid,note['template_id'],{'focus':'methods'})
        project_notes.write(self.db,self.pid,note['path'],'Use NEW_STYLE {{focus}}',expected_hash=note['content_hash'])
        reference={'project_id':self.pid,'template_id':note['template_id'],'revision_id':original['revision_id'],'variables':{'focus':'methods'}}
        with patch.object(workflow,'chat_raw',return_value={'content':'Result'}) as model:
            paper_toolbox.transform(self.db,self.pid,{'tool':'polish','text':'Source','prompt_template':reference})
            instruction=model.call_args.args[1][0]['content'];self.assertIn('OLD_STYLE methods',instruction);self.assertNotIn('NEW_STYLE',instruction)
            paper_toolbox.transform(self.db,self.pid,{'tool':'polish','text':'Source','prompt_template':reference,'prompt':'Edited style'})
            self.assertIn('Edited style',model.call_args.args[1][0]['content'])
        foreign=self.db.create_task('Other')
        with self.assertRaises(ValueError):paper_toolbox.transform(self.db,foreign,{'tool':'polish','text':'Source','prompt_template':reference,'prompt':'Edited style'})
        current=project_notes.read(self.db,self.pid,note['path'])
        project_notes.write(self.db,self.pid,note['path'],current['content'],metadata={'template_status':'disabled'})
        with self.assertRaises(ValueError):paper_toolbox.transform(self.db,self.pid,{'tool':'polish','text':'Source','prompt_template':reference})

    def test_mermaid_is_a_local_browser_tool_and_never_starts_a_model_job(self):
        catalog=paper_toolbox.route('GET','/api/paper-toolbox',{}, {},self.db)
        self.assertEqual(next(tool for tool in catalog['tools'] if tool['id']=='mermaid')['runtime'],'browser')
        with patch('agent_jobs.start') as jobs,patch.object(workflow,'chat_raw') as model:
            with self.assertRaisesRegex(ValueError,'本地预览和导出'):
                paper_toolbox.start(self.db,{'tool':'mermaid','text':'flowchart LR\n A --> B'})
            jobs.assert_not_called();model.assert_not_called()
