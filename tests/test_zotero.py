# -*- coding: utf-8 -*-
"""zotero.py 的自检：纯函数 + 分页/参数拼接（打桩 _get，不起 HTTP 服务）。

    python -m unittest test_zotero -v
"""
from __future__ import annotations

import tempfile
import unittest

import zotero


FIXTURE = {
    "key": "ABCD1234",
    "version": 412,
    "data": {
        "itemType": "journalArticle",
        "title": "Sodium layered oxides for Na-ion batteries",
        "creators": [{"firstName": "Yuting", "lastName": "Zuo", "creatorType": "author"},
                     {"name": "高志斌", "creatorType": "author"},
                     {"lastName": "", "firstName": ""}],
        "date": "2024-03-11 2024-03-11",
        "publicationTitle": "Journal of Power Sources",
        "DOI": "10.1016/j.jpowsour.2024.0001",
        "url": "https://doi.org/10.1016/j.jpowsour.2024.0001",
        "abstractNote": "<p>Cu doping improves<br/>capacity retention.</p>",
        "note": "<h2>我的批注</h2><ul><li>IDC 降低</li><li><b>重点</b>：DCF</li></ul>",
        "tags": [{"tag": "Na-ion"}, {"tag": "layered oxide"}],
        "collections": ["COLL0001"],
        "citationKey": "zuo2024sodium",
    },
    "meta": {"numChildren": 1},
}


class TestMapping(unittest.TestCase):
    def test_creator_name(self):
        self.assertEqual(zotero.creator_name({"lastName": "Zuo", "firstName": "Yuting"}), "Zuo Yuting")
        self.assertEqual(zotero.creator_name({"name": "高志斌"}), "高志斌")
        self.assertEqual(zotero.creator_name({}), "")

    def test_year_of(self):
        self.assertEqual(zotero.year_of("2024-03-11 2024-03-11"), "2024")
        self.assertEqual(zotero.year_of({"value": "2019"}), "2019")
        self.assertEqual(zotero.year_of("n.d."), "")

    def test_html_to_text(self):
        text = zotero.html_to_text("<h2>标题</h2><p>甲<br/>乙</p><ul><li>一</li><li><b>二</b></li></ul>")
        self.assertIn("## 标题", text)
        self.assertIn("甲\n乙", text)
        self.assertIn("- 一", text)
        self.assertIn("**二**", text)
        self.assertNotIn("<", text)

    def test_normalize(self):
        it = zotero.normalize(FIXTURE)
        self.assertEqual(it["key"], "ABCD1234")
        self.assertEqual(it["year"], "2024")
        self.assertEqual(it["venue"], "Journal of Power Sources")
        self.assertEqual(it["creators"][:2], ["Zuo Yuting", "高志斌"])
        self.assertEqual(it["tags"], ["Na-ion", "layered oxide"])
        self.assertEqual(it["citation"], "zuo2024sodium")
        self.assertEqual(it["abstract"], "Cu doping improves\ncapacity retention.")

    def test_as_note(self):
        note = zotero.as_note(FIXTURE)
        self.assertTrue(note["title"].startswith("Sodium layered oxides"))
        self.assertIn("（2024）", note["title"])
        for token in ("## 摘要", "## Zotero 笔记", "## 我的批注",
                      "https://doi.org/10.1016/j.jpowsour.2024.0001", "`ABCD1234`", "#Na-ion"):
            self.assertIn(token, note["content"])
        self.assertLessEqual(len(note["title"]), 160)

    def test_as_note_without_year(self):
        item = {"key": "K", "data": {"itemType": "note", "note": "<p>随手记</p>"}}
        note = zotero.as_note(item)
        self.assertFalse(note["title"].endswith("（）"))
        self.assertIn("随手记", note["content"])


class TestTransport(unittest.TestCase):
    def test_rejects_non_loopback(self):
        with self.assertRaises(zotero.ZoteroError):
            zotero._base("http://192.168.1.9:23119")

    def test_url_params(self):
        url = zotero._api_url("http://127.0.0.1:23119", "/users/0/items",
                              {"limit": 50, "q": None, "empty": "", "sort": "dateModified"})
        self.assertIn("/api/users/0/items?", url)
        self.assertIn("limit=50", url)
        self.assertIn("sort=dateModified", url)
        self.assertNotIn("q=", url)          # None / 空串都不拼进去
        self.assertNotIn("empty=", url)

    def test_items_paging_math(self):
        captured = {}

        def fake_get(url, timeout=0):
            captured["url"] = url
            return 200, {"total-results": "120"}, [FIXTURE] * 3

        original = zotero._get
        zotero._get = fake_get
        try:
            out = zotero.items(start=50, limit=3, collection="COLL0001", q="cu doping")
        finally:
            zotero._get = original
        self.assertIn("/api/users/0/collections/COLL0001/items?", captured["url"])
        self.assertIn("start=50", captured["url"])
        self.assertIn("cu+doping", captured["url"].replace("%20", "+"))
        self.assertEqual(out["returned"], 3)
        self.assertEqual(out["total"], 120)
        self.assertTrue(out["hasMore"])
        self.assertEqual(out["items"][0]["key"], "ABCD1234")

    def test_status_when_zotero_down(self):
        """真连一个关着的端口：必须是「没启动」的中文状态，而不是抛异常。"""
        out = zotero.status("http://127.0.0.1:9")
        self.assertFalse(out["connected"])
        self.assertFalse(out["readable"])
        self.assertIn("没启动", out["message"])

    def test_note_for_needs_key(self):
        with self.assertRaises(zotero.ZoteroError):
            zotero.note_for(key="  ")


class TestPdfPath(unittest.TestCase):
    """附件在本机的定位：本机 API 不给 path，只能按附件 key 进 storage/<key>/ 找。"""

    def _run(self, children, tmp):
        from pathlib import Path
        root = Path(tmp) / "storage"
        original = (zotero._get, zotero.storage_dir)
        zotero._get = lambda url, timeout=0: (200, {}, children)
        zotero.storage_dir = lambda: root
        try:
            return zotero.pdf_path("ABCD1234"), root
        finally:
            zotero._get, zotero.storage_dir = original

    def test_picks_pdf_attachment(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path
            root = Path(tmp) / "storage"
            for key in ("ATT00001", "ATT00002"):
                (root / key).mkdir(parents=True)
                (root / key / "f.pdf").write_bytes(b"%PDF-1.4")
            children = [{"key": "ATT00001", "data": {"itemType": "attachment", "contentType": "text/html"}},
                        {"key": "ATT00002", "data": {"itemType": "attachment", "contentType": "application/pdf"}}]
            path, root = self._run(children, tmp)
            self.assertEqual(path, str(root / "ATT00002" / "f.pdf"))

    def test_no_pdf_attachment(self):
        with tempfile.TemporaryDirectory() as tmp:
            children = [{"key": "ATT00001", "data": {"itemType": "attachment", "contentType": "text/html"}},
                        {"key": "ATT00009", "data": {"itemType": "attachment", "contentType": "application/pdf"}}]
            path, _ = self._run(children, tmp)          # 目录里没有文件 → 不能假装找到
            self.assertEqual(path, "")


class TestZoteroImport(unittest.TestCase):
    """分类 → 下载队列：有 PDF 的进「已入库」，没有的标「待手动」，笔记/附件条目不算论文。"""

    def test_routes_pdf_and_manual(self):
        import workflow

        seen = {"cache": [], "queue": [], "ingest": []}

        class FakeDB:
            def cache_result(self, task_id, it):
                seen["cache"].append(it)
                return "r%d" % len(seen["cache"])

            def set_queue(self, task_id, rid, status, path="", error=""):
                seen["queue"].append((rid, status, error))

            def ingest_pdf(self, task_id, path):
                seen["ingest"].append(path)
                return {"id": "p1"}

            def log_step(self, *a):
                pass

            def queue(self, task_id):
                return []

        paper = {"key": "K1", "type": "journalArticle", "title": "Cu 掺杂", "creators": ["Zuo Yuting"],
                 "year": "2024", "doi": "10.1/x", "url": "", "abstract": "摘要"}
        note = {"key": "K2", "type": "note", "title": "笔记", "creators": [], "year": "",
                "doi": "", "url": "", "abstract": ""}
        original = (zotero.items, zotero.pdf_path)
        zotero.items = lambda **kw: {"items": [paper, note], "returned": 2, "hasMore": False, "total": 2}
        zotero.pdf_path = lambda key, base=None: "C:/z/K1.pdf" if key == "K1" else ""
        try:
            out = workflow.stage_zotero_import(FakeDB(), "t1")
        finally:
            zotero.items, zotero.pdf_path = original

        self.assertEqual(out["total"], 1)                    # 笔记条目被排除
        self.assertEqual(out["downloaded"], 1)
        self.assertEqual(seen["ingest"], ["C:/z/K1.pdf"])
        self.assertEqual(seen["queue"], [("r1", "downloaded", "")])
        self.assertEqual(seen["cache"][0]["source"], "zotero")
        self.assertEqual(seen["cache"][0]["url"], "https://doi.org/10.1/x")


if __name__ == "__main__":
    unittest.main()
