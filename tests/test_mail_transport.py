"""Exercise SMTP over real loopback TLS sockets, without a real email account."""
import base64
import email
from pathlib import Path
import shutil
import socketserver
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import mail


@unittest.skipUnless(shutil.which("openssl"), "Requires OpenSSL for a temporary TLS test certificate")
class MailTransportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix="rw-smtp-tls-")
        cls.addClassCleanup(temporary.cleanup)
        root = Path(temporary.name)
        key, cert = root / "key.pem", root / "cert.pem"
        try:
            subprocess.run([shutil.which("openssl"), "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=localhost"], check=True, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.CalledProcessError as exc:
            raise unittest.SkipTest(f"OpenSSL failed to generate certificate: {exc.stderr.decode('utf-8', errors='ignore')}")
        cls.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.tls.load_cert_chain(cert, key)

    def server(self, tls=True, deny_auth=False):
        records = {"auth": 0, "recipients": [], "messages": []}

        class Handler(socketserver.StreamRequestHandler):
            def handle(inner):
                def send(value):
                    inner.wfile.write((value + '\r\n').encode())
                    inner.wfile.flush()
                send('220 localhost test SMTP')
                while raw := inner.rfile.readline():
                    line = raw.decode().strip()
                    command = line.split(' ', 1)[0].upper()
                    if command in {'EHLO', 'HELO'}:
                        send('250-localhost\r\n250-AUTH PLAIN\r\n250 SIZE 1000000')
                    elif command == 'AUTH':
                        records['auth'] += 1
                        token = line.split(' ', 2)[-1]
                        good = base64.b64decode(token) == b'\0audit@example.com\0test-only'
                        send('235 Authentication successful' if good and not deny_auth else '535 Authentication rejected')
                    elif command == 'MAIL':
                        send('250 OK')
                    elif command == 'RCPT':
                        address = email.utils.parseaddr(line.split(':', 1)[1])[1]
                        records['recipients'].append(address)
                        send('550 No such recipient' if address == 'bad@example.com' else '250 OK')
                    elif command == 'DATA':
                        send('354 End with a dot')
                        payload = []
                        while (chunk := inner.rfile.readline()) not in {b'.\r\n', b''}:
                            payload.append(chunk)
                        records['messages'].append(email.message_from_bytes(b''.join(payload)))
                        send('250 Queued')
                    elif command == 'QUIT':
                        send('221 Bye')
                        break
                    elif command == 'STARTTLS':
                        send('502 STARTTLS unavailable')
                    else:
                        send('250 OK')

        context = self.tls
        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            def get_request(inner):
                conn, address = super().get_request()
                return (context.wrap_socket(conn, server_side=True) if tls else conn), address

        server = Server(('127.0.0.1', 0), Handler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        account = {'email': 'audit@example.com', 'password': 'test-only', 'smtpHost': '127.0.0.1', 'smtpPort': server.server_address[1], 'smtpSecure': tls, 'allowSelfSigned': True}
        return account, records

    def test_tls_delivery_parses_names_deduplicates_and_has_message_id(self):
        account, records = self.server()
        result = mail.send_mail(account, '"Research, Team" <ok@example.com>; second@example.com', 'ok@example.com', '协议测试', 'BODY_TEST')
        self.assertTrue(result['ok'])
        self.assertEqual(records['recipients'], ['ok@example.com', 'second@example.com'])
        self.assertEqual(result['acceptedCount'], 2)
        self.assertTrue(result['messageId'])
        self.assertEqual(len(records['messages']), 1)
        self.assertTrue(records['messages'][0]['Date'])

    def test_partial_recipient_rejection_is_not_reported_as_complete_success(self):
        account, records = self.server()
        result = mail.send_mail(account, 'ok@example.com, bad@example.com', text='body')
        self.assertFalse(result['ok'])
        self.assertTrue(result['partial'])
        self.assertEqual(result['acceptedCount'], 1)
        self.assertEqual(result['refusedRecipients'], ['bad@example.com'])
        self.assertEqual(len(records['messages']), 1)

    def test_connection_test_checks_smtp_without_delivering_a_message(self):
        account, records = self.server()
        imap = Mock()
        imap.select.return_value = ('OK', [b'2'])
        imap.list.return_value = ('OK', [b'(\\Inbox) "/" INBOX', b'() "/" Sent'])
        with patch('mail.open_client', return_value=imap):
            result = mail.test_account(account)
        self.assertTrue(result['imap']['ok'])
        self.assertTrue(result['smtp']['ok'])
        self.assertEqual(result['folderCount'], 2)
        self.assertEqual(records['auth'], 1)
        self.assertEqual(records['messages'], [])

    def test_smtp_auth_failure_is_separate_from_successful_imap(self):
        account, records = self.server(deny_auth=True)
        imap = Mock()
        imap.select.return_value = ('OK', [b'2'])
        imap.list.return_value = ('OK', [])
        with patch('mail.open_client', return_value=imap):
            result = mail.test_account(account)
        self.assertTrue(result['imap']['ok'])
        self.assertFalse(result['smtp']['ok'])
        self.assertFalse(result['ok'])
        self.assertEqual(records['messages'], [])

    def test_starttls_is_required_before_any_password_is_sent(self):
        account, records = self.server(tls=False)
        with self.assertRaisesRegex(ValueError, 'STARTTLS'):
            mail.send_mail(account, 'ok@example.com', text='body')
        self.assertEqual(records['auth'], 0)
        self.assertEqual(records['messages'], [])

    def test_invalid_recipient_is_rejected_before_connecting(self):
        account, records = self.server()
        with self.assertRaisesRegex(ValueError, '收件人'):
            mail.send_mail(account, 'not-an-address', text='body')
        self.assertEqual(records['auth'], 0)
