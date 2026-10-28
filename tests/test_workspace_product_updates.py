import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

import workflow
import image_tools
import paper_toolbox
import project_bindings
import project_tasks
import task_management
import usage_stats
import message_channels
import prompt_templates
import workspace_chat


class WorkspaceProductUpdatesTest(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.db=workflow.WorkflowDB(self.root/'app.sqlite3');self.addCleanup(self.db.close)
        self.project=self.root/'project';self.project.mkdir()
        self.pid=self.db.create_task('Image project',base_dir=str(self.project))
        self.model=self.db.add_model('Fixture','http://127.0.0.1:9999/v1','vision','test',activate=True)
        self.db.assistant_save('test-expert',name='Direct expert',enabled=1,rules='EXPERT_DIRECT_RULE',permission='read_only')
        self.binding=project_bindings.register(self.db,self.pid,'assistant','test-expert')

    def image(self,name='figure.png'):
        image=Image.new('RGBA',(32,24),(255,0,0,128));buffer=io.BytesIO();image.save(buffer,format='PNG');image.close()
        return image_tools.upload(self.db,{'project_id':self.pid,'name':name,'data':base64.b64encode(buffer.getvalue()).decode()})

    def test_direct_chat_freezes_selected_expert_without_creating_tasks(self):
        cid=self.db.create_chat('Hello',task_id=self.pid)
        snapshot=workspace_chat.snapshot(self.db,cid,{'executor_binding_id':self.binding['id']})
        self.assertEqual(snapshot['assistant_id'],'test-expert')
        self.assertEqual(self.db.db.execute('SELECT COUNT(*) FROM project_tasks').fetchone()[0],0)
        workspace_chat.apply(self.db,snapshot)
        self.assertEqual(self.db.agent_runtime_snapshot['assistant_id'],'test-expert')

    def test_builtin_templates_seed_once_and_are_project_scoped(self):
        first=prompt_templates.search(self.db,self.pid)['templates']
        second=prompt_templates.search(self.db,self.pid)['templates']
        self.assertEqual(len(first),2);self.assertEqual({v['template_id'] for v in first},{v['template_id'] for v in second})
        self.assertTrue(any('图注' in v['title'] for v in first))

    def test_image_roundtrip_and_batch_caption_have_real_image_content(self):
        first=self.image();second=self.image('second.png');ids=[first['asset_id'],second['asset_id']]
        for fmt in ('jpg','jpeg','png','webp','tiff','pdf'):
            result=paper_toolbox.transform(self.db,self.pid,{'tool':'image_convert','image_ids':ids,'format':fmt})
            self.assertEqual(len(result['files']),2)
            self.assertTrue(all(Path(f['path']).is_file() for f in result['files']))
        pdf=Path(result['files'][0]['path']);raw=base64.b64encode(pdf.read_bytes()).decode()
        uploaded=image_tools.upload(self.db,{'project_id':self.pid,'name':'roundtrip.pdf','data':raw})
        result=paper_toolbox.transform(self.db,self.pid,{'tool':'image_convert','image_ids':[uploaded['asset_id']],'format':'png'})
        with Image.open(result['files'][0]['path']) as converted:self.assertGreater(converted.width,0)
        with patch.object(workflow,'chat_raw',return_value={'content':'A red image.'}) as model:
            captions=paper_toolbox.transform(self.db,self.pid,{'tool':'caption','image_ids':ids})
        self.assertEqual(model.call_count,2)
        self.assertEqual(model.call_args.args[1][-1]['content'][1]['type'],'image_url')
        self.assertTrue(Path(captions['files'][0]['path']).is_file())

    def test_image_validation_and_partial_failure(self):
        image=self.image()
        with self.assertRaises(ValueError):image_tools.inputs(self.db,'other',[image['asset_id']])
        with self.assertRaises(Exception):image_tools.upload(self.db,{'name':'fake.png','data':base64.b64encode(b'not an image').decode()})
        second=self.image('two.png')
        with patch.object(workflow,'chat_raw',side_effect=[ValueError('first failed'),{'content':'Second caption'}]):
            result=paper_toolbox.transform(self.db,self.pid,{'tool':'caption','image_ids':[image['asset_id'],second['asset_id']]})
        self.assertEqual(len(result['errors']),1);self.assertIn('Second caption',result['content'])

    def test_task_delete_restore_preserves_outputs_and_rejects_start(self):
        authority=project_tasks.CommandContext.ui(self.pid)
        task=project_tasks.create(self.db,{'project_id':self.pid,'goal':'Draft','executor_binding_id':self.binding['id']},authority=authority)
        output=self.project/'keep.md';output.write_text('keep','utf-8')
        task_management.command(self.db,self.pid,task['id'],'organize',{'priority':'P0','pinned':True,'title':'Renamed'})
        deleted=task_management.command(self.db,self.pid,task['id'],'delete',{})['task']
        self.assertTrue(deleted['metadata']['deleted_at']);self.assertTrue(output.is_file())
        with self.assertRaises(ValueError):project_tasks.start(self.db,task['id'],authority=authority)
        restored=task_management.command(self.db,self.pid,task['id'],'restore',{})['task']
        self.assertNotIn('deleted_at',restored['metadata']);self.assertEqual(restored['title'],'Renamed')
        duplicate=task_management.command(self.db,self.pid,task['id'],'duplicate',{})['task']
        self.assertNotEqual(duplicate['id'],task['id'])

    def test_metering_persists_and_excludes_prompt_secrets(self):
        self.db.usage_context={'project_id':self.pid,'owner_id':self.db.create_chat('Metered',task_id=self.pid)}
        profile=self.db.model(self.model['id'])
        normalized=usage_stats.normalize({'input_tokens':100,'output_tokens':20,'input_tokens_details':{'cached_tokens':80}},'openai-responses')
        usage_stats.record(profile,[{'role':'system','content':'SENSITIVE_PROMPT'}],[],250,normalized,'ok',0,25)
        usage_stats.record(profile,[],[],500,{},'error',1)
        report=usage_stats.report(self.db,{})
        self.assertEqual(report['totals']['input'],100);self.assertEqual(report['totals']['cached'],80)
        self.assertEqual(report['totals']['metered'],1);self.assertEqual(report['totals']['failed'],1)
        self.assertNotIn('SENSITIVE_PROMPT',json.dumps(report));self.assertIsNone(report['requests'][0]['prompt_tokens'])
        filtered=usage_stats.report(self.db,{'q':['VISION ok']})
        self.assertEqual(filtered['totals']['requests'],1)
        self.assertEqual(filtered['totals']['input'],100)
        self.assertEqual(filtered['requests'][0]['status'],'ok')
        self.assertEqual(usage_stats.report(self.db,{'q':['NO_MATCH']})['totals']['requests'],0)
        usage_stats.route('POST','/api/usage/prices',{'profile_id':self.model['id'],'input':2,'output':8,'cached':.2},{},self.db)
        self.assertAlmostEqual(usage_stats.report(self.db,{})['totals']['cost'],.000216)
        anthropic=usage_stats.normalize({'input_tokens':10,'output_tokens':2,'cache_read_input_tokens':90},'anthropic-messages')
        self.assertEqual(anthropic['prompt'],100)

    def test_channels_encrypt_mask_validate_and_only_send_explicitly(self):
        body={'enabled':True,'webhook':'https://hooks.slack.com/services/TEST/ONLY/SECRET'}
        with patch('urllib.request.build_opener') as http:
            public=message_channels.route('POST','/api/message-channels/slack/save',body,{},self.db)
            http.assert_not_called()
        self.assertTrue(public['webhook_set']);self.assertNotIn('SECRET',json.dumps(public))
        self.assertNotIn('SECRET',json.dumps(message_channels.read(self.db,'slack')))
        self.assertEqual(message_channels.reveal(message_channels.read(self.db,'slack'))['webhook'],body['webhook'])
        with self.assertRaises(ValueError):message_channels.validate('slack',{'webhook':'https://evil.example/services/secret'})
        with patch('urllib.request.build_opener') as http:
            http.return_value.open.return_value.__enter__.return_value.read.return_value=b'ok'
            self.assertTrue(message_channels.send(self.db,'slack','Test')['ok'])
        self.assertEqual(message_channels.public(self.db,'slack')['last_status'],'ok')

    def test_multimodal_protocol_shapes(self):
        from agent_protocols import image_content
        parts=[{'type':'text','text':'Caption'},{'type':'image_url','image_url':{'url':'data:image/png;base64,AAAA'}}]
        self.assertEqual(image_content(parts,'openai-responses')[1]['type'],'input_image')
        self.assertEqual(image_content(parts,'anthropic-messages')[1]['source']['media_type'],'image/png')
        self.assertEqual(image_content(parts,'google-generative-ai')[1]['inlineData']['data'],'AAAA')

    def test_project_upload_never_overwrites_or_escapes(self):
        import project_files
        first=project_files.upload(self.db,self.pid,'evidence.txt',b'first')
        second=project_files.upload(self.db,self.pid,'evidence.txt',b'second')
        self.assertNotEqual(first['relative'],second['relative'])
        self.assertEqual((self.project/first['relative']).read_bytes(),b'first')
        with self.assertRaises(ValueError):project_files.upload(self.db,self.pid,'../outside.txt',b'x')
        with self.assertRaises(ValueError):project_files.upload(self.db,self.pid,'NUL.txt',b'x')

    def test_todo_deadlines_reject_impossible_dates_without_changing_saved_value(self):
        import project_todos
        todo=project_todos.add_todo(self.db,self.pid,'Review deadline',due_date='2026-11-01')
        for invalid in ('2026-02-30','2026-13-01','20261101'):
            with self.assertRaises(ValueError):
                project_todos.update_todo(self.db,todo['id'],due_date=invalid)
        self.assertEqual(project_todos.list_todos(self.db,self.pid)[0]['due_date'],'2026-11-01')
        project_todos.update_todo(self.db,todo['id'],due_date='')
        self.assertEqual(project_todos.list_todos(self.db,self.pid)[0]['due_date'],'')

    def test_scheduler_default_and_live_team_status(self):
        import team_runtime
        scheduled=self.db.schedule_save({'name':'Dedicated schedule','project_id':self.pid,'prompt':'Research'})
        self.assertEqual(scheduled['assistant_id'],'builtin-scheduler')
        team=self.db.team_add('Live team','custom',project_id=self.pid)
        member=self.db.team_member_add(team['id'],'Researcher','leader',assistant_id='test-expert')
        binding=project_bindings.register(self.db,self.pid,'team',team['id'])
        cid=self.db.create_chat('Team chat',task_id=self.pid)
        frozen=workspace_chat.snapshot(self.db,cid,{'executor_binding_id':binding['id'],'member_id':member['id']})
        import uuid
        key=uuid.uuid4().hex
        run=self.db.create_async_task(kind='agent_run',owner_id=cid,idempotency_key=key)
        self.db.update_async_task(run['id'],status='running',checkpoint={'request':{'_runtime_snapshot':frozen}})
        live=team_runtime.snapshot(self.db,team['id'])
        self.assertEqual(live['members'][0]['status'],'running')
        self.db.update_async_task(run['id'],status='done')
        self.assertEqual(team_runtime.snapshot(self.db,team['id'])['members'][0]['status'],'idle')
