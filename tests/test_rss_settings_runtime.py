"""Settings RSS controls persist and drive the real intelligence refresh."""
import http.server
from pathlib import Path
import threading
import unittest

import service_settings
import workflow
from tests import test_web_runtime as web


class FeedFixture:
    def __enter__(self):
        self.requests=[];fixture=self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                fixture.requests.append(self.path)
                body=('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Fixture</title><item><title>固态电解质订阅论文</title><link>http://127.0.0.1:'+str(self.server.server_address[1])+'/article</link><description>可复查的本地订阅内容</description><pubDate>Wed, 28 Oct 2026 08:00:00 GMT</pubDate></item><item><title>非网页地址条目</title><link>javascript:window.rssInjected=1</link><description>仍保留资讯文字</description></item></channel></rss>').encode()
                self.send_response(200);self.send_header('Content-Type','application/rss+xml; charset=utf-8');self.end_headers();self.wfile.write(body)
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.url=f'http://127.0.0.1:{self.server.server_address[1]}/feed.xml';return self
    def __exit__(self,*args):self.server.shutdown();self.server.server_close()


@unittest.skipUnless(web.sync_playwright and web.EDGE,'Requires Playwright and Edge')
class RSSSettingsRuntimeTest(unittest.TestCase):
    setUpClass=classmethod(web.WebRuntimeTest.setUpClass.__func__)
    load_page=web.WebRuntimeTest.load_page

    def setUp(self):
        web.WebRuntimeTest.setUp(self)
        self.assertEqual(self.database_path.resolve().parent,self.temporary_root.resolve())
        with workflow.WorkflowDB(self.database_path) as db:
            service_settings.ensure(db)
            db.db.execute('DELETE FROM rss_items');db.db.execute('DELETE FROM rss_sources')
            db.db.execute("DELETE FROM service_settings WHERE service='rss'");db.db.commit()

    def settings(self):
        self.page.locator('#sidebar [data-nav="settings"]').click()
        self.page.locator('.set-nav [data-set="rss"]').click()
        self.page.locator('[data-rss-add]:enabled').wait_for()

    def save(self,dialog):
        with self.page.expect_response(lambda response:response.url.endswith('/api/rss/sources/save')) as response:
            dialog.get_by_role('button',name='保存订阅源',exact=True).click()
        self.assertTrue(response.value.ok,response.value.text());dialog.wait_for(state='detached')
        return response.value.json()['source']

    def test_settings_crud_controls_fetching_and_keeps_downloaded_intelligence(self):
        self.load_page();self.settings()
        with FeedFixture() as feed:
            self.page.get_by_role('button',name='新增订阅源',exact=True).click()
            dialog=self.page.get_by_role('dialog',name='新增 RSS 订阅源',exact=True)
            dialog.get_by_label('订阅源名称',exact=True).fill('本地材料订阅')
            dialog.get_by_label('RSS 地址',exact=True).fill(feed.url)
            dialog.get_by_label('订阅源分类',exact=True).fill('材料科学')
            source=self.save(dialog);self.assertEqual(feed.requests,[])
            card=self.page.locator('[data-rss-source="'+source['id']+'"]');card.wait_for()
            card.get_by_role('button',name='编辑',exact=True).click()
            edit=self.page.get_by_role('dialog',name='编辑 RSS 订阅源',exact=True)
            edit.get_by_label('订阅源名称',exact=True).fill('更新后的材料订阅');self.save(edit)
            self.page.locator('[data-rss-source="'+source['id']+'"] h4').filter(has_text='更新后的材料订阅').wait_for()
            card.get_by_role('button',name='停用',exact=True).click()
            card.get_by_role('button',name='启用',exact=True).wait_for()
            self.page.locator('#sidebar [data-nav="intel"]').click()
            with self.page.expect_response(lambda response:response.url.endswith('/api/rss/refresh')) as response:self.page.locator('#itGo').click()
            self.assertEqual(response.value.json()['sources'],0);self.assertEqual(feed.requests,[])
            self.page.locator('#itSourceSettings').click()
            self.page.locator('[data-rss-add]:enabled').wait_for()
            self.assertIn('on',self.page.locator('[data-set="rss"]').get_attribute('class'))
            card=self.page.locator('[data-rss-source="'+source['id']+'"]')
            card.get_by_role('button',name='启用',exact=True).click()
            card.get_by_role('button',name='停用',exact=True).wait_for()
            self.page.locator('#sidebar [data-nav="intel"]').click()
            with self.page.expect_response(lambda response:response.url.endswith('/api/rss/refresh')) as response:self.page.locator('#itGo').click()
            self.assertEqual(response.value.json()['added'],2)
            self.page.locator('#itList .it-t').filter(has_text='固态电解质订阅论文').wait_for()
            self.assertEqual(self.page.locator('#itList .it-t').filter(has_text='非网页地址条目').evaluate('element=>element.tagName'),'SPAN')
            self.assertEqual(feed.requests,['/feed.xml'])
            self.page.locator('#itSourceSettings').click();self.page.locator('[data-rss-add]:enabled').wait_for()
            for theme in ('dark','light'):
                self.page.evaluate('theme=>setTheme(theme)',theme);self.page.locator('[data-rss-add]:enabled').wait_for()
                folder=Path(web.ROOT)/'.codex-tasks/20261028-rss-settings/screenshots';folder.mkdir(parents=True,exist_ok=True)
                self.page.screenshot(path=str(folder/('rss-settings-'+theme+'.png')))
            self.page.locator('[data-rss-source="'+source['id']+'"] [data-rss-delete]').click()
            self.page.get_by_role('dialog',name='确认操作',exact=True).get_by_role('button',name='删除订阅源',exact=True).click()
            self.page.locator('[data-rss-source="'+source['id']+'"]').wait_for(state='detached')
            self.page.locator('#sidebar [data-nav="intel"]').click()
            self.page.locator('#itList .it-t').filter(has_text='固态电解质订阅论文').wait_for()
            with self.page.expect_response(lambda response:response.url.endswith('/api/rss/refresh')) as response:self.page.locator('#itGo').click()
            self.assertEqual(response.value.json()['sources'],0)
            self.assertEqual(self.page.request.get(self.url+'api/rss/sources').json()['sources'],[])
            self.assertEqual(len(self.page.request.get(self.url+'api/rss/items').json()['items']),2)
        self.page.reload(wait_until='networkidle');self.page.locator('#itList .it-t').filter(has_text='固态电解质订阅论文').wait_for()
        self.assertEqual(self.errors,[])

    def test_builtin_sources_duplicate_and_invalid_url_feedback_preserve_configuration(self):
        self.load_page();self.settings()
        with self.page.expect_response(lambda response:response.url.endswith('/api/rss/sources/defaults')) as response:
            self.page.get_by_role('button',name='补充内置源',exact=True).click()
        self.assertEqual(response.value.json()['added'],5)
        self.page.wait_for_function('document.querySelectorAll("[data-rss-source]").length===5')
        source=self.page.request.get(self.url+'api/rss/sources').json()['sources'][0]
        card=self.page.locator('[data-rss-source="'+source['id']+'"]');card.get_by_role('button',name='停用',exact=True).click()
        card.get_by_role('button',name='启用',exact=True).wait_for()
        with self.page.expect_response(lambda response:response.url.endswith('/api/rss/sources/defaults')) as response:
            self.page.get_by_role('button',name='补充内置源',exact=True).click()
        self.assertEqual(response.value.json()['added'],0)
        self.page.get_by_role('button',name='新增订阅源',exact=True).click()
        dialog=self.page.get_by_role('dialog',name='新增 RSS 订阅源',exact=True)
        dialog.get_by_label('订阅源名称',exact=True).fill('重复地址')
        dialog.get_by_label('RSS 地址',exact=True).fill(source['url'])
        dialog.get_by_role('button',name='保存订阅源',exact=True).click()
        dialog.locator('.nb-error').filter(has_text='已存在').wait_for()
        self.assertEqual(dialog.get_by_label('订阅源名称',exact=True).input_value(),'重复地址')
        dialog.get_by_label('RSS 地址',exact=True).fill('file:///tmp/private-feed.xml')
        dialog.get_by_role('button',name='保存订阅源',exact=True).click()
        dialog.locator('.nb-error').filter(has_text='http://').wait_for()
        dialog.get_by_role('button',name='关闭',exact=True).click()
        self.page.reload(wait_until='networkidle');self.page.locator('.set-nav [data-set="rss"]').click()
        self.page.locator('[data-rss-add]:enabled').wait_for()
        self.assertEqual(self.page.locator('[data-rss-source]').count(),5)
        card=self.page.locator('[data-rss-source="'+source['id']+'"]')
        self.assertTrue(card.get_by_role('button',name='启用',exact=True).is_visible())
        self.page.set_viewport_size({'width':820,'height':700})
        self.assertTrue(self.page.get_by_role('button',name='新增订阅源',exact=True).is_visible())
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        self.assertEqual(self.errors,[])
