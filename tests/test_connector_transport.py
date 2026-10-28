"""HTTP and SDK integration boundaries, using loopback services and test identities."""
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import feishu
import service_settings
from tests.test_feishu import FakeDB


class ConnectorTransportTest(unittest.TestCase):
    def test_feishu_app_send_checks_authorization_payload_and_acknowledgement(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(inner):
                body = json.loads(inner.rfile.read(int(inner.headers['Content-Length'])))
                requests.append((inner.path, inner.headers.get('Authorization'), body))
                data = {'code': 0, 'tenant_access_token': 'loopback-token'} if inner.path.startswith('/auth/') else {'code': 0, 'data': {'message_id': 'loopback-message-id'}}
                raw = json.dumps(data).encode()
                inner.send_response(200)
                inner.send_header('Content-Type', 'application/json')
                inner.send_header('Content-Length', str(len(raw)))
                inner.end_headers()
                inner.wfile.write(raw)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        db = FakeDB()
        self.addCleanup(db.close)
        service_settings.write(db, 'feishu', {'enabled': True, 'app_id': 'fixture-app', 'app_secret': 'fixture-only-secret', 'receive_id': 'fixture-recipient', 'receive_id_type': 'open_id'}, 'assistant-a')
        with patch.object(feishu, 'API', f'http://127.0.0.1:{server.server_address[1]}'):
            result = feishu.send(db, 'assistant-a', 'ISOLATED_DELIVERY')
        self.assertEqual(result['message_id'], 'loopback-message-id')
        self.assertEqual(requests[1][1], 'Bearer loopback-token')
        self.assertEqual(requests[1][2]['receive_id'], 'fixture-recipient')
        self.assertEqual(json.loads(requests[1][2]['content']), {'text': 'ISOLATED_DELIVERY'})

    def test_feishu_does_not_claim_success_when_message_id_is_missing(self):
        with patch.object(feishu, 'config', return_value={'enabled': True, 'app_id': 'test', 'app_secret': 'test', 'receive_id': 'test'}), patch.object(feishu, 'request', side_effect=[{'tenant_access_token': 'test'}, {'code': 0, 'data': {}}]):
            with self.assertRaisesRegex(ValueError, '未确认'):
                feishu.send(None, 'test', 'test')

    def test_lark_inbound_bridge_uses_the_lark_domain(self):
        fake_db = Mock()
        fake_db.assistants.return_value = [{'id': 'test', 'enabled': 1}]
        client = Mock()
        lark = SimpleNamespace(ws=SimpleNamespace(Client=client), EventDispatcherHandler=Mock(), LARK_DOMAIN='https://open.larksuite.com', FEISHU_DOMAIN='https://open.feishu.cn')
        cfg = {'enabled': True, 'inbound_enabled': True, 'app_id': 'test', 'app_secret': 'test', 'registration_region': 'lark'}
        def run_thread(**kwargs):
            return SimpleNamespace(start=kwargs['target'])
        with patch.dict(feishu._BRIDGE, {'status': 'stopped', 'assistant_id': '', 'error': ''}), patch('workflow.WorkflowDB', return_value=nullcontext(fake_db)), patch.object(feishu, 'config', return_value=cfg), patch.dict('sys.modules', {'lark_oapi': lark}), patch.object(feishu.threading, 'Thread', side_effect=run_thread):
            feishu.start_bridge('temporary-only')
            client.assert_called_once()
            self.assertEqual(client.call_args.kwargs['domain'], lark.LARK_DOMAIN)
