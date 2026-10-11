# -*- coding: utf-8 -*-
"""_trim_tail 自检：尾部与原文逐字相同的页要被删掉，译文页不能被动。"""
import os, sys, tempfile, unittest, pathlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fitz
import papertran


class TrimTest(unittest.TestCase):
    def _pdf(self, texts):
        p = pathlib.Path(tempfile.mkdtemp()) / "t.pdf"
        d = fitz.open()
        for t in texts:
            pg = d.new_page()
            pg.insert_text((72, 100), t)
        d.save(str(p)); d.close()
        return p

    def test_尾部原页被删(self):
        src = self._pdf(["AAA", "BBB", "CCC"])
        mono = self._pdf(["译文A", "译文B", "CCC"])        # 第 3 页 == 原文第 3 页 -> 未翻译
        papertran._trim_tail(mono, src)
        with fitz.open(str(mono)) as d:
            self.assertEqual(d.page_count, 2)

    def test_已翻译的页不被删(self):
        src = self._pdf(["AAA", "BBB"])
        mono = self._pdf(["译文A", "译文B"])
        papertran._trim_tail(mono, src)
        with fitz.open(str(mono)) as d:
            self.assertEqual(d.page_count, 2)

    def test_单页不动(self):
        src = self._pdf(["AAA"])
        mono = self._pdf(["AAA"])
        papertran._trim_tail(mono, src)
        with fitz.open(str(mono)) as d:
            self.assertEqual(d.page_count, 1)


if __name__ == "__main__":
    unittest.main()
