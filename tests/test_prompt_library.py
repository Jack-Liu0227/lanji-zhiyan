"""Template management edits the existing files with scope and version checks."""
from pathlib import Path
import tempfile
import unittest

import asset_service
import project_notes
import prompt_templates
import workflow


class PromptLibraryTest(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.db=workflow.WorkflowDB(self.root/'db.sqlite3');self.addCleanup(self.db.close)
        project=self.root/'project';project.mkdir();self.pid=self.db.create_task('Library',base_dir=str(project))
        other=self.root/'other';other.mkdir();self.other=self.db.create_task('Other',base_dir=str(other))

    def call(self,action,**body):
        return prompt_templates.route(self.db,'/api/prompt-templates/'+action,{'project_id':self.pid,**body})

    def test_crud_disabled_templates_and_variables_share_existing_note_library(self):
        created=self.call('save',title='研究风格',content='Use {{focus}}',metadata={'tags':['写作'],'variables':{}})
        self.assertIn(created['template_id'],[item['template_id'] for item in prompt_templates.search(self.db,self.pid)['templates']])
        self.assertEqual(created['variables']['focus']['type'],'text')
        original=prompt_templates.render(self.db,self.pid,created['template_id'],{'focus':'evidence'})
        edited=self.call('save',template_id=created['template_id'],expected_hash=created['content_hash'],title='改名后的模板',content='Only {{method}}',metadata={'template_status':'disabled','variables':created['variables']})
        self.assertEqual(edited['path'],created['path']);self.assertEqual(edited['template_id'],created['template_id'])
        self.assertEqual(set(edited['variables']),{'method'})
        self.assertNotIn(created['template_id'],[item['template_id'] for item in prompt_templates.search(self.db,self.pid)['templates']])
        self.assertEqual(self.call('inspect',template_id=created['template_id'])['body'],'Only {{method}}')
        enabled=self.call('save',template_id=created['template_id'],expected_hash=edited['content_hash'],title=edited['title'],content=edited['body'],metadata={'template_status':'active'})
        self.assertEqual(prompt_templates.render(self.db,self.pid,created['template_id'],{'focus':'evidence'},original['revision_id'])['rendered'],'Use evidence')
        prompt_templates.save_binding(self.db,self.pid,{'template_id':enabled['template_id'],'variables':{'method':'comparison'}})
        self.assertEqual(next(item for item in self.call('library')['templates'] if item['template_id']==enabled['template_id'])['binding_count'],1)
        self.call('delete',template_id=enabled['template_id'],expected_hash=enabled['content_hash'])
        self.assertNotIn(enabled['template_id'],[item['template_id'] for item in self.call('library')['templates']]);self.assertEqual(prompt_templates.bindings(self.db,self.pid),[])
        self.assertFalse((self.db.base_dir(self.pid)/enabled['path']).exists())
        revision=self.db.db.execute('SELECT content_uri FROM asset_revisions WHERE id=?',(original['revision_id'],)).fetchone()
        self.assertIsNotNone(revision);self.assertIn('Use {{focus}}',Path(revision['content_uri']).read_text(encoding='utf-8'))

    def test_edit_and_delete_conflicts_do_not_remove_newer_text_or_other_notes(self):
        created=self.call('save',title='Template',content='First')
        edited=self.call('save',template_id=created['template_id'],expected_hash=created['content_hash'],title='Template',content='Second')
        with self.assertRaises(ValueError):self.call('save',template_id=created['template_id'],expected_hash=created['content_hash'],title='Stale',content='Lost update')
        with self.assertRaises(ValueError):self.call('delete',template_id=created['template_id'],expected_hash=created['content_hash'])
        self.assertEqual(self.call('inspect',template_id=created['template_id'])['body'],'Second')
        with self.assertRaises(ValueError):prompt_templates.route(self.db,'/api/prompt-templates/delete',{'project_id':self.other,'template_id':created['template_id'],'expected_hash':edited['content_hash']})
        plain=project_notes.write(self.db,self.pid,'','NORMAL_NOTE',title='Normal')
        with self.assertRaises(ValueError):self.call('delete',template_id=plain.get('asset_id'),expected_hash=plain['content_hash'])
        self.assertTrue((self.db.base_dir(self.pid)/plain['path']).is_file())
        self.assertIn('caption_academic',[item['id'] for item in self.call('library')['builtins']])
        global_catalog=prompt_templates.route(self.db,'/api/prompt-templates/library',{})
        self.assertEqual(global_catalog['templates'],[]);self.assertTrue(global_catalog['read_only'])
