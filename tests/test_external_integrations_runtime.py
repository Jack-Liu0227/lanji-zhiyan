"""Browser regressions for mail and connector settings, using disposable accounts only."""
import json
import unittest
from urllib.parse import urlsplit

from tests import test_web_runtime as web


@unittest.skipUnless(web.sync_playwright and web.EDGE, "Requires Playwright and Edge")
class ExternalIntegrationsRuntimeTest(unittest.TestCase):
    setUpClass = classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp = web.WebRuntimeTest.setUp
    load_page = web.WebRuntimeTest.load_page

    def seed_mail(self):
        self.accounts = [{"id": aid, "email": aid + "@example.com", "label": aid, "hasPassword": True} for aid in ("alpha", "beta")]
        self.page.route('**/api/mail/**', self.mail_route)
        self.load_page()
        self.page.locator('#sidebar [data-nav="mail"]').click()
        self.page.locator('[data-mail="1"]').wait_for()

    def mail_route(self, route):
        path = urlsplit(route.request.url).path
        if path.endswith('/accounts'):
            data = self.accounts
        elif path.endswith('/providers'):
            data = {}
        elif path.endswith('/folders'):
            data = [{"path": "INBOX", "name": "Inbox", "specialUse": "\\Inbox"}]
        elif path.endswith('/messages'):
            aid = path.split('/')[4]
            data = {"total": 2, "messages": [{"uid": i, "subject": aid + "-message-" + str(i), "fromAddress": "fixture@example.com", "seen": True} for i in (1, 2)]}
        elif '/messages/' in path and path.rsplit('/', 1)[-1].isdigit():
            aid = path.split('/')[4]
            data = {"uid": int(path.rsplit('/', 1)[-1]), "subject": aid + "-detail-" + path.rsplit('/', 1)[-1], "text": "ISOLATED_MAIL_BODY", "fromAddress": "fixture@example.com"}
        else:
            data = {}
        route.fulfill(json=data)

    def test_mail_list_and_detail_survive_leaving_the_module(self):
        self.seed_mail()
        self.page.locator('[data-mail="1"]').click()
        self.page.locator('.mail-detail-subject').wait_for()
        self.page.locator('#sidebar [data-nav="calendar"]').click()
        self.page.locator('#sidebar [data-nav="mail"]').click()
        self.assertEqual(self.page.locator('[data-mail]').count(), 2)
        self.assertIn('alpha-detail-1', self.page.locator('#mailDetail').inner_text())
        self.assertEqual(self.errors, [])

    def test_mail_late_detail_cannot_replace_the_latest_selection(self):
        self.seed_mail()
        held = []
        self.page.route('**/api/mail/accounts/alpha/messages/1?*', lambda route: held.append(route))
        self.page.locator('[data-mail="1"]').click()
        self.page.wait_for_timeout(60)
        self.page.locator('[data-mail="2"]').click()
        self.page.wait_for_function("document.querySelector('.mail-detail-subject')?.textContent==='alpha-detail-2'")
        held[0].fulfill(json={"uid": 1, "subject": "STALE_DETAIL", "text": "old"})
        self.page.wait_for_timeout(80)
        self.assertEqual(self.page.locator('.mail-detail-subject').inner_text(), 'alpha-detail-2')
        self.assertEqual(self.errors, [])

    def test_mail_late_folders_cannot_switch_back_to_an_old_account(self):
        self.seed_mail()
        held = []
        self.page.route('**/api/mail/accounts/beta/folders', lambda route: held.append(route))
        self.page.evaluate("void selectMailAccount('beta')")
        self.page.wait_for_timeout(60)
        self.page.evaluate("void selectMailAccount('alpha')")
        self.page.wait_for_function("mailCur.accountId==='alpha'")
        held[0].fulfill(json=[{"path": "OLD-INBOX", "name": "Stale", "specialUse": "\\Inbox"}])
        self.page.wait_for_timeout(120)
        self.assertEqual(self.page.evaluate('mailCur.accountId'), 'alpha')
        self.assertEqual(self.page.evaluate('mailCur.folder'), 'INBOX')
        self.assertEqual(self.errors, [])

    def test_mail_retries_account_loading_after_initial_failure(self):
        self.page.route('**/api/mail/**', self.mail_route)
        self.accounts = [{"id": "alpha", "email": "alpha@example.com"}]
        self.page.route('**/api/mail/accounts', lambda r: r.fulfill(status=503, json={"error": "INITIAL_ACCOUNT_FAILURE"}))
        self.load_page()
        self.page.locator('#sidebar [data-nav="mail"]').click()
        self.page.wait_for_function("document.body.textContent.includes('INITIAL_ACCOUNT_FAILURE')")
        self.page.unroute('**/api/mail/accounts')
        self.page.locator('#sidebar [data-nav="calendar"]').click()
        self.page.locator('#sidebar [data-nav="mail"]').click()
        self.page.locator('[data-mail="1"]').wait_for(timeout=2500)
        self.assertEqual(self.errors, [])

    def test_mail_partial_delivery_keeps_only_refused_recipients(self):
        self.seed_mail()
        self.page.route('**/api/mail/accounts/alpha/send', lambda r: r.fulfill(json={'ok': False, 'partial': True, 'refusedRecipients': ['bad@example.com'], 'acceptedCount': 1}))
        self.page.locator('#btnMailCompose').click()
        self.page.locator('#mcTo').fill('ok@example.com, bad@example.com')
        self.page.locator('#mcBody').fill('BODY_TO_RETAIN')
        self.page.locator('#btnMcSend').click()
        self.page.wait_for_function("document.querySelector('#mcTo').value==='bad@example.com'")
        self.assertTrue(self.page.locator('#mailComposeModal').is_visible())
        self.assertEqual(self.page.locator('#mcBody').input_value(), 'BODY_TO_RETAIN')
        self.assertEqual(self.errors, [])

    def test_compose_auto_focus_does_not_steal_subsequent_body_input(self):
        self.seed_mail()
        self.page.evaluate("""()=>{
            const pending=[],original=window.setTimeout;
            window.setTimeout=(callback,delay,...args)=>{pending.push(()=>callback(...args));return 0;};
            try{document.querySelector('#btnMailCompose').click();}finally{window.setTimeout=original;}
            window.flushComposeTimers=()=>pending.splice(0).forEach(callback=>callback());
        }""")
        self.page.locator('#mcTo').fill('fixture@example.com')
        self.page.locator('#mcBody').focus()
        self.page.evaluate('window.flushComposeTimers()')
        self.page.keyboard.insert_text('BODY_TO_RETAIN')
        self.assertEqual(self.page.locator('#mcBody').input_value(),'BODY_TO_RETAIN')
        self.assertEqual(self.page.locator('#mcTo').input_value(),'fixture@example.com')
        self.assertEqual(self.errors,[])

    def test_mail_late_send_does_not_close_a_new_compose_draft(self):
        self.seed_mail()
        held = []
        self.page.route('**/api/mail/accounts/alpha/send', lambda r: held.append(r))
        self.page.locator('#btnMailCompose').click()
        self.page.locator('#mcTo').fill('fixture@example.com')
        self.page.locator('#mcBody').fill('OLD_SUBMISSION')
        self.page.locator('#btnMcSend').click()
        self.page.wait_for_timeout(60)
        self.page.locator('#btnMcClose').click()
        self.page.locator('#btnMailCompose').click()
        self.page.locator('#mcBody').fill('NEW_DRAFT_TO_KEEP')
        held[0].fulfill(json={'ok': True})
        self.page.wait_for_timeout(100)
        self.assertTrue(self.page.locator('#mailComposeModal').is_visible())
        self.assertEqual(self.page.locator('#mcBody').input_value(), 'NEW_DRAFT_TO_KEEP')
        self.assertEqual(self.errors, [])

    def test_mail_late_detail_after_leaving_has_no_dom_error(self):
        self.seed_mail()
        held = []
        self.page.route('**/api/mail/accounts/alpha/messages/1?*', lambda r: held.append(r))
        self.page.locator('[data-mail="1"]').click()
        self.page.wait_for_timeout(60)
        self.page.locator('#sidebar [data-nav="calendar"]').click()
        held[0].fulfill(json={'uid': 1, 'subject': 'old', 'text': 'old'})
        self.page.wait_for_timeout(100)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.page.evaluate('route()'), 'calendar')

    def test_mail_edit_test_uses_saved_password_without_exposing_it(self):
        self.seed_mail()
        posted = []
        def test_form(route):
            posted.append(route.request.post_data_json)
            route.fulfill(json={'ok': True, 'inboxMessages': 2, 'folderCount': 2, 'smtp': {'ok': True}})
        self.page.route('**/api/mail/test', test_form)
        self.page.locator('#btnMailManage').click()
        self.page.locator('[data-ma-edit="alpha"]').click()
        self.page.locator('#btnMaTest').click()
        self.page.wait_for_function("document.querySelector('#maTestResult').textContent.includes('发信连接成功')")
        self.assertEqual(posted[0]['id'], 'alpha')
        self.assertFalse(posted[0]['password'])
        self.assertEqual(self.errors, [])

    def feishu_settings(self):
        self.page.route('**/api/feishu/config?*', lambda r: r.fulfill(json={'config': {}, 'docs': {}, 'bridge': {'status': 'stopped'}}))
        self.load_page()
        self.page.locator('#sidebar [data-nav="settings"]').click()
        self.page.locator('[data-set="channels"]').click()
        self.page.locator('[data-channel="feishu"]').click()
        self.page.locator('#int-assistant').wait_for()
        selected = self.page.locator('#int-assistant').input_value()
        other = self.page.locator('#int-assistant option').evaluate_all('(a,current)=>a.find(e=>e.value!==current).value', selected)
        return selected, other

    def test_feishu_switching_assistant_cancels_pending_send_confirmation(self):
        selected, other = self.feishu_settings()
        sent = []
        self.page.route('**/api/feishu/test', lambda r: (sent.append(r.request.post_data_json), r.fulfill(json={'ok': True, 'message_id': 'test-only'})))
        self.page.evaluate('() => { window.uiConfirm=()=>new Promise(resolve=>{window.resolveFeishuConfirm=resolve;}); }')
        self.page.locator('#fs-message').fill('ORIGINAL_ASSISTANT_MESSAGE')
        self.page.locator('#fs-test').click()
        self.page.locator('#int-assistant').select_option(other)
        self.page.wait_for_function('id=>document.querySelector("#int-assistant")?.value===id', arg=other)
        self.page.evaluate('window.resolveFeishuConfirm(true)')
        self.page.wait_for_timeout(100)
        self.assertEqual(sent, [])
        self.assertEqual(self.errors, [])

    def test_feishu_late_qr_response_stays_with_the_original_assistant(self):
        selected, other = self.feishu_settings()
        held = []
        self.page.route('**/api/feishu/registration/begin', lambda r: held.append(r))
        self.page.locator('#fs-device-begin').click()
        self.page.wait_for_timeout(60)
        self.page.locator('#int-assistant').select_option(other)
        self.page.wait_for_timeout(80)
        held[0].fulfill(json={'session_id': 'isolated-qr', 'qr_url': 'https://accounts.feishu.cn/isolated-qr', 'expires_in': 600, 'interval': 120})
        self.page.wait_for_timeout(80)
        self.assertEqual(self.page.locator('#fs-device-qr a').count(), 0)
        self.page.locator('#int-assistant').select_option(selected)
        self.page.locator('#fs-device-qr a').wait_for()
        self.assertIn('isolated-qr', self.page.locator('#fs-device-qr a').get_attribute('href'))
        self.assertEqual(self.errors, [])
