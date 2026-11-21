"""A real streamed reply keeps the speaker's icon from waiting through history."""
import http.server
import json
import threading
import unittest

from tests import test_web_runtime as web


@unittest.skipUnless(web.sync_playwright and web.EDGE, 'Requires Playwright and Edge')
class ChatAvatarRuntimeTest(unittest.TestCase):
    setUpClass = classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp = web.WebRuntimeTest.setUp
    load_page = web.WebRuntimeTest.load_page

    def test_bound_expert_icon_survives_stream_completion_and_assistant_switch(self):
        import workflow

        allow_stream, allow_finish = threading.Event(), threading.Event()

        class Model(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if not payload.get('stream'):
                    self.send_error(400, 'Expected a streamed chat request')
                    return
                allow_stream.wait(20)
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"An avatar test reply."}}]}\n\n')
                self.wfile.flush()
                allow_finish.wait(20)
                self.wfile.write(b'data: [DONE]\n\n')

        model = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Model)
        threading.Thread(target=model.serve_forever, daemon=True).start()
        self.addCleanup(model.server_close)
        self.addCleanup(model.shutdown)
        self.addCleanup(allow_finish.set)
        self.addCleanup(allow_stream.set)
        with workflow.WorkflowDB(self.database_path) as db:
            db.add_model('Avatar fixture', f'http://127.0.0.1:{model.server_port}/v1', 'avatar-test', 'fixture-key', activate=True)
            db.assistant_save('avatar-expert-a', name='Orbit expert', avatar='🪐', enabled=1)
            db.assistant_save('avatar-expert-b', name='Leaf expert', avatar='🍃', enabled=1)
        response = self.page.request.post(self.url + 'api/chat/create', data={'title': 'Avatar test'})
        self.assertTrue(response.ok, response.text())
        chat = response.json()['chat']['id']
        response = self.page.request.post(self.url + 'api/chat/assistant', data={'id': chat, 'assistant_id': 'avatar-expert-a'})
        self.assertTrue(response.ok, response.text())
        self.page.add_init_script('localStorage.setItem("rw-chat",' + json.dumps(chat) + ')')
        self.load_page()
        self.page.locator('#ask').fill('Reply for the avatar test.')
        self.page.locator('#send').click()
        waiting = self.page.locator('#msgs .chat-activity .msg-ava')
        waiting.wait_for(state='visible')
        self.assertEqual(waiting.text_content(), '🪐')
        allow_stream.set()
        live = self.page.locator('#msgs .msg-assistant.streaming .msg-ava')
        live.wait_for(state='visible')
        self.assertEqual(live.text_content(), '🪐')
        allow_finish.set()
        final = self.page.locator('#msgs .msg-assistant:not(.streaming)').filter(has_text='An avatar test reply.')
        final.wait_for(state='visible')
        self.assertEqual(final.locator('.msg-ava').text_content(), '🪐')
        self.page.wait_for_function('!activeAgentRun')
        self.page.locator('#assist').select_option('avatar-expert-b')
        self.page.wait_for_function("asBound() === 'avatar-expert-b'")
        self.page.reload(wait_until='networkidle')
        final.wait_for(state='visible')
        self.assertEqual(final.locator('.msg-ava').text_content(), '🪐')
        self.page.locator('#ask').fill('Reply from the second expert.')
        self.page.locator('#send').click()
        second = self.page.locator('#msgs .msg-assistant').filter(has_text='Leaf expert')
        second.wait_for(state='visible')
        self.assertEqual(second.locator('.msg-ava').text_content(), '🍃')
        self.assertEqual(self.errors, [])
