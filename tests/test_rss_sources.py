# -*- coding: utf-8 -*-
"""情报日报订阅源管理自检：增删改 / URL 校验 / 只抓启用源 / 删光不自动补。

    python -m unittest tests.test_rss_sources -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from email.message import Message
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _n in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, os.path.join(_ROOT, "packages", _n))

import literature_import                     # noqa: E402
import project_library                       # noqa: E402
import rss                                   # noqa: E402
import rss_routes                            # noqa: E402
from workflow import WorkflowDB              # noqa: E402


class TestFeedParsing(unittest.TestCase):
    def test_arxiv_abstract_url_requires_official_abs_link(self) -> None:
        self.assertEqual(rss.arxiv_abstract_url('http://www.arxiv.org/abs/2610.17392v1?access=private#anchor'),
                         'https://arxiv.org/abs/2610.17392v1')
        for link in ('https://arxiv.org.example.test/abs/2610.17392', 'https://arxiv.org@127.0.0.1/abs/2610.17392',
                     'https://arxiv.org:8443/abs/2610.17392', 'https://arxiv.org/pdf/2610.17392',
                     'https://arxiv.org/abs/../2610.17392', 'https://arxiv.org/abs/2610.17392/other'):
            with self.subTest(link=link):
                self.assertEqual(rss.arxiv_abstract_url(link), '')

    def test_rss_keeps_long_arxiv_abstract_paragraphs_and_repeated_creators(self) -> None:
        first = 'Evidence from repeated measurements & controlled simulations. ' * 12
        row, = rss.parse(f'''<rss xmlns:dc="http://purl.org/dc/elements/1.1/">
          <channel><item><link>https://arxiv.org/abs/2606.17392</link>
            <dc:creator>Doe, Jane</dc:creator><dc:creator>Wei Liang</dc:creator>
            <dc:creator>Doe, Jane</dc:creator>
            <description><![CDATA[arXiv:2606.17392v2 Announce Type: replace-cross Abstract:
              <p>{first.replace('&', '&amp;')}</p>
              <p>At x &lt; 2, Fe<sub>2</sub>O remains stable.</p>
              <script>unwanted script text</script><style>unwanted style text</style>
            ]]></description>
          </item></channel></rss>''')
        self.assertEqual(row['authors'], 'Doe, Jane; Wei Liang')
        self.assertEqual(row['summary'], first.strip() + '\n\nAt x < 2, Fe2O remains stable.')

    def test_atom_entry_authors_dates_and_xhtml_take_precedence(self) -> None:
        entry, inherited, plain = rss.parse('''<feed xmlns="http://www.w3.org/2005/Atom">
          <author><name>Feed Author</name></author>
          <entry><title>Paper</title><link rel="self" href="https://example.test/feed"/>
            <link rel="alternate" href="https://example.test/paper"/>
            <author><name>First Author</name><email>private@example.test</email></author>
            <author><name>Second Author</name></author><contributor><name>Editor</name></contributor>
            <updated>2026-10-28T13:00:00Z</updated><published>2026-10-28T08:00:00Z</published>
            <summary type="html">Result in a nutshell…</summary>
            <content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">
              <p>Result in a nutshell: first evidence.</p><p>Second evidence.</p>
            </div></content>
          </entry>
          <entry><link href="https://example.test/another"/>
            <source><author><name>Original Author</name></author></source>
          </entry>
          <entry><link href="https://example.test/plain"/>
            <summary type="text">Write &lt;T&gt; as a symbol.\n\nKeep the second paragraph.</summary>
          </entry>
        </feed>''')
        self.assertEqual(entry['link'], 'https://example.test/paper')
        self.assertEqual(entry['authors'], 'First Author; Second Author')
        self.assertEqual(entry['published_at'], '2026-10-28 08:00')
        self.assertEqual(entry['summary'], 'Result in a nutshell: first evidence.\n\nSecond evidence.')
        self.assertEqual(inherited['authors'], 'Original Author')
        self.assertEqual(plain['authors'], 'Feed Author')
        self.assertEqual(plain['summary'], 'Write <T> as a symbol.\n\nKeep the second paragraph.')


class TestRssSources(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = WorkflowDB(Path(self.td.name) / "r.sqlite3")
        self._old_fetch = rss.fetch
        self.fetched: list[str] = []

    def tearDown(self) -> None:
        rss.fetch = self._old_fetch
        self.db.close()
        self.td.cleanup()

    def _fake_fetch(self, url: str, timeout: int = 0) -> list:
        self.fetched.append(url)
        return [{"link": f"https://e.example/{len(self.fetched)}", "title": "T"}]

    def test_save_edit_delete(self) -> None:
        got = rss.save_source(self.db, {"url": "https://a.example/rss", "title": "A 源"})["source"]
        self.assertEqual(got["title"], "A 源")
        self.assertEqual(got["enabled"], 1)
        edited = rss.save_source(self.db, {"id": got["id"], "enabled": False})["source"]
        self.assertEqual(edited["enabled"], 0)
        self.assertEqual(edited["url"], "https://a.example/rss")        # 没传的字段保留
        self.assertEqual(rss.delete_source(self.db, {"id": got["id"]})["deleted"], got["id"])
        self.assertEqual(self.db.rss_sources(), [])

    def test_url_validation(self) -> None:
        for bad in ("", "ftp://a.example/rss", "not a url", "https://has space/rss"):
            with self.assertRaises(ValueError, msg=bad):
                rss.save_source(self.db, {"url": bad})
        rss.save_source(self.db, {"url": "https://a.example/rss"})
        with self.assertRaises(ValueError):                             # 重复地址不许静默覆盖
            rss.save_source(self.db, {"url": "https://a.example/rss", "title": "重名"})
        with self.assertRaises(ValueError):                             # 不存在的源
            rss.save_source(self.db, {"id": "nope", "enabled": True})

    def test_delete_keeps_items(self) -> None:
        src = rss.save_source(self.db, {"url": "https://a.example/rss"})["source"]
        self.db.save_rss_items(self.db.rss_source(src["id"]), [{"link": "https://e/1", "title": "T"}])
        self.assertEqual(rss.delete_source(self.db, {"id": src["id"]})["retained_items"], 1)
        items = self.db.rss_items()
        self.assertEqual(len(items), 1)                                 # 条目留着
        self.assertTrue(items[0]["source_deleted"])

    def test_refresh_only_enabled(self) -> None:
        rss.fetch = self._fake_fetch
        rss.add_defaults(self.db)                                       # 5 个内置源
        first = self.db.rss_sources()[0]
        rss.save_source(self.db, {"id": first["id"], "enabled": False})
        res = rss.refresh(self.db)
        self.assertEqual(res["sources"], 4)
        self.assertEqual(res["fetched"], 4)
        self.assertNotIn(first["url"], self.fetched)

    def test_refresh_enriches_old_metadata_and_preserves_project_import(self) -> None:
        src = rss.save_source(self.db, {'url': 'https://example.test/rss'})['source']
        link = 'https://arxiv.org/abs/2606.17392'
        prefix = 'arXiv:2606.17392v2 Announce Type: replace-cross Abstract: '
        first = 'The reported measurement used a calibrated resonator. ' * 12
        self.db.save_rss_items(src, [{'link': link, 'title': link, 'authors': 'First Author'}])
        old, = self.db.rss_items()
        # A snapshot written by the old parser, including the prefix that counted toward its limit.
        self.db.db.execute('UPDATE rss_items SET summary=? WHERE id=?', ((prefix + first)[:400], old['id']))
        self.db.db.commit()
        pid = self.db.create_task('Local test project')
        imported = literature_import.import_rss(self.db, pid, [old['id']])
        result_id = imported['items'][0]['result_id']
        project_library.update_record(self.db, pid, result_id, {'abstract': 'Researcher notes'})

        feed = f'''<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item>
          <link>{link}</link><title>Calibrated measurement</title><dc:creator>First Author</dc:creator>
          <dc:creator>Second Author</dc:creator><pubDate>Wed, 28 Oct 2026 08:00:00 GMT</pubDate>
          <description><![CDATA[{prefix}<p>{first}</p><p>The final result confirms the effect.</p>]]></description>
        </item></channel></rss>'''
        rss.fetch = lambda url: rss.parse(feed)
        self.assertEqual(rss.refresh(self.db)['added'], 0)
        updated, = self.db.rss_items(q='Second Author', year='2026')
        self.assertEqual(updated['id'], old['id'])
        self.assertEqual(updated['title'], 'Calibrated measurement')
        self.assertEqual(updated['authors'], 'First Author; Second Author')
        self.assertEqual(updated['summary'], first.strip() + '\n\nThe final result confirms the effect.')
        self.assertEqual(updated['published_at'], '2026-10-28 08:00')

        rss.fetch = lambda url: [
            {'link': link, 'title': '', 'authors': 'First Author', 'summary': 'The reported measurement'},
            {'link': link, 'title': 'List entry', 'authors': 'Another Author', 'summary': '',
             'published_at': '2026-10-29 08:00'},
        ]
        self.assertEqual(rss.refresh(self.db)['added'], 0)
        retained, = self.db.rss_items()
        self.assertEqual(retained, updated)
        self.assertEqual(literature_import.annotate(self.db, [retained], pid)[0]['imported_result_id'], result_id)
        self.assertEqual(self.db.result(result_id)['abstract'], 'Researcher notes')

    def test_recover_archived_arxiv_item_checks_official_document_before_updating(self) -> None:
        src = rss.save_source(self.db, {'url': 'https://example.test/old-feed'})['source']
        link = 'https://arxiv.org/abs/2610.17392v1?from=feed'
        paragraph = 'An archived paper reports direct measurements of the effect. ' * 10
        self.db.save_rss_items(src, [{'link': link, 'title': 'Archived arXiv paper', 'authors': 'First Author'}])
        old, = self.db.rss_items()
        legacy = ('arXiv:2610.17392v1 Announce Type: new Abstract: ' + paragraph)[:400]
        self.db.db.execute('UPDATE rss_items SET summary=? WHERE id=?', (legacy, old['id']))
        self.db.db.commit()
        original, = self.db.rss_items()
        self.assertTrue(original['can_recover_arxiv'])
        item_id = original['id']
        pid = self.db.create_task('Archived paper test')
        result_id = literature_import.import_rss(self.db, pid, [item_id])['items'][0]['result_id']
        route = lambda body: rss_routes.route('POST', '/api/rss/recover-arxiv', body, {}, self.db)

        def response(identifier: str, abstract: str) -> BytesIO:
            page = f'''<html><head><meta name="citation_arxiv_id" content="{identifier}">
                <meta name="citation_abstract" content="{abstract}">
                <meta name="citation_author" content="Author, First">
                <meta name="citation_author" content="Author, Second">
                </head><body><meta name="citation_author" content="Ignore Body"></body></html>'''
            result = BytesIO(page.encode())
            result.status = 200
            result.headers = Message()
            result.headers['Content-Type'] = 'text/html; charset=utf-8'
            return result

        with patch.object(rss.urllib.request, 'build_opener') as opener:
            with self.assertRaisesRegex(ValueError, 'ID'):
                route({'item_id': item_id, 'url': 'https://example.test/replacement'})
            opener.assert_not_called()
            opener.return_value.open.return_value = response('2610.12345', paragraph)
            with self.assertRaisesRegex(ValueError, '论文编号'):
                route({'item_id': item_id})
            self.assertEqual(self.db.rss_items(), [original])

            opener.return_value.open.return_value = response('2610.17392', paragraph + '\n\nThe full result.')
            item = route({'item_id': item_id})['item']
            self.assertEqual(opener.return_value.open.call_args.args[0].full_url, 'https://arxiv.org/abs/2610.17392v1')
        self.assertEqual(item, {'id': item_id, 'summary': paragraph.strip() + '\n\nThe full result.',
                                'authors': 'Author, First; Author, Second', 'can_recover_arxiv': False})
        stored, = self.db.rss_items()
        self.assertEqual(stored['link'], link)
        self.assertEqual(stored['summary'], item['summary'])
        self.assertEqual(literature_import.annotate(self.db, [stored], pid)[0]['imported_result_id'], result_id)
        self.assertEqual(self.db.result(result_id)['abstract'], legacy)

    def test_registered_route_dispatch(self) -> None:
        listed = rss_routes.route("GET", "/api/rss/sources", {}, {}, self.db)
        self.assertEqual(listed, {"sources": [], "defaults": rss.DEFAULT_SOURCES})
        defaults = rss_routes.route("POST", "/api/rss/sources/defaults", {}, {}, self.db)
        self.assertEqual(defaults["added"], 5)
        saved = rss_routes.route("POST", "/api/rss/sources/save",
                                 {"url": "https://route.example/rss"}, {}, self.db)
        source_id = saved["source"]["id"]
        deleted = rss_routes.route("POST", "/api/rss/sources/delete",
                                   {"id": source_id}, {}, self.db)
        self.assertEqual(deleted["deleted"], source_id)
        rss.fetch = self._fake_fetch
        refreshed = rss_routes.route("POST", "/api/rss/refresh", {}, {}, self.db)
        self.assertEqual(refreshed["fetched"], 5)
        self.assertIsNone(rss_routes.route("GET", "/api/rss/refresh", {}, {}, self.db))

    def test_cleared_sources_are_not_reseeded(self) -> None:
        rss.fetch = self._fake_fetch
        rss.add_defaults(self.db)
        for s in self.db.rss_sources():
            rss.delete_source(self.db, {"id": s["id"]})
        self.assertEqual(self.db.rss_sources(), [])
        self.assertEqual(rss.refresh(self.db)["sources"], 0)             # 用户删光了：不许再补内置源
        self.assertEqual(self.db.rss_sources(), [])
        self.assertEqual(rss.add_defaults(self.db)["added"], 5)          # 想补回来得显式点


if __name__ == "__main__":
    unittest.main(verbosity=2)
