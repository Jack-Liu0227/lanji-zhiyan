# -*- coding: utf-8 -*-
"""格式转换：md → tex 的转义 + 「转 PDF」走宿主通道的降级逻辑。"""
import os
import sys
import unittest
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
for _n in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(Path(_ROOT) / "packages" / _n))

import workflow_converters as conv  # noqa: E402


class TexEscapeTest(unittest.TestCase):
    def test_greek_and_symbols_go_math_mode(self):
        """lmodern 里没有 β/₂/≤ 的字形，直写进 tex 会静默丢字（β-Ga₂O₃ 整篇消失过）。"""
        got = conv.tex_escape("β-Ga₂O₃ μ≤5°")
        self.assertIn(r"$\beta$", got)
        self.assertIn("$_2$", got)
        self.assertIn("$_3$", got)
        self.assertIn(r"$\mu$", got)
        self.assertIn(r"$\le$", got)
        self.assertIn(r"$^\circ$", got)
        self.assertNotIn("β", got, "原字不能再留在 tex 里")

    def test_plain_text_untouched(self):
        self.assertEqual(conv.tex_escape("abc 123 中文"), "abc 123 中文")
        self.assertEqual(conv.tex_escape("100%"), r"100\%")
        self.assertEqual(conv.tex_escape("a_b"), r"a\_b")

    def test_md_to_tex_keeps_headings_and_symbols(self):
        tex = conv.md_to_tex("# 标题\n\n### 1.1 β-Ga₂O₃ 器件\n\n正文 ≤ 5°\n", "标题")
        self.assertIn(r"\section{标题}", tex)
        self.assertIn(r"\subsection{1.1 $\beta$-Ga$_2$O$_3$ 器件}", tex)
        self.assertIn(r"\le", tex)
        self.assertIn(r"\end{document}", tex)

    def test_host_compile_needs_the_channel(self):
        """没有宿主通道（纯 CLI）就得返回 None，让调用方退回沙箱编译，而不是硬崩。"""
        old = {k: os.environ.pop(k, None) for k in ("RW_GRANT", "RW_GRANT_KEY")}
        try:
            self.assertIsNone(conv.host_compile(Path("x.tex")))
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v

    def test_pdf_and_tex_are_offered(self):
        self.assertIn("pdf", conv.CONV_FORMATS)
        self.assertIn("tex", conv.CONV_FORMATS)


class FormatToolRoutingTest(unittest.TestCase):
    """format_convert 的 pdf / tex 必须走本机 MiKTeX 那条（conv_run），docx/html/md 仍走 stage_convert。"""

    def setUp(self):
        import tempfile
        import workflow
        self._tmp = tempfile.TemporaryDirectory()
        self.db = workflow.WorkflowDB(Path(self._tmp.name) / "t.sqlite3")
        base = Path(self._tmp.name) / "proj"
        (base / "论文正文").mkdir(parents=True)
        (base / "论文正文" / "正文.md").write_text(chr(10).join([
            "# 题目", "", "### 1.1 背景", "",
            "[图位 1] 图1 器件结构 | 文件：fig1_schematic.png"]), encoding="utf-8")
        self.tid = self.db.create_task("格式", "q", "Na", str(base))
        self.wf = workflow
        self.seen = []
        self._old = workflow.conv_run
        workflow.conv_run = lambda db, tid, fmt, source="", template="", cite_style="": self.seen.append((fmt, source)) or {"ok": True}

    def tearDown(self):
        self.wf.conv_run = self._old
        self.db.close()
        self._tmp.cleanup()

    def test_pdf_goes_through_miktex_path(self):
        self.wf._tool_impl("format_convert", self.db, self.tid, {"format": "pdf"}, scope="convert")
        self.assertEqual(self.seen, [("pdf", "")])

    def test_tex_carries_the_source_path(self):
        self.wf._tool_impl("format_convert", self.db, self.tid,
                           {"format": "tex", "source": "C:/x/y.tex"}, scope="convert")
        self.assertEqual(self.seen, [("tex", "C:/x/y.tex")])


class ConvertOpeningTest(unittest.TestCase):
    """审查交付后，格式转换要先问三件事：图的位置数目 / 成品格式 / 格式范本。"""

    def setUp(self):
        import tempfile
        import workflow
        self._tmp = tempfile.TemporaryDirectory()
        self.db = workflow.WorkflowDB(Path(self._tmp.name) / "t.sqlite3")
        base = Path(self._tmp.name) / "proj"
        (base / "论文正文").mkdir(parents=True)
        (base / "图").mkdir()
        (base / "图" / "fig1_schematic.png").write_bytes(b"PNG-test")
        (base / "论文正文" / "正文.md").write_text(chr(10).join([
            "# 题目", "", "### 1.1 背景", "",
            "[图位 1] 图1 器件结构 | 文件：fig1_schematic.png"]), encoding="utf-8")
        self.tid = self.db.create_task("开题", "q", "Na", str(base))

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def test_opening_asks_three_things_once(self):
        import workflow_converters as wc
        r = wc.open_convert(self.db, self.tid)
        self.assertTrue(r["opened"])
        msg = r["message"]
        self.assertIn("图", msg)
        self.assertIn("1 处 [图位]", msg)
        self.assertIn("1 张成图", msg)
        self.assertNotIn("elsevier", msg, "开场只问第一件（图），模板留到第三问")
        sys_txt = wc.convert_system(self.db, self.tid)
        for key in ("plain", "elsevier", "ieee", "revtex"):
            self.assertIn(key, sys_txt)
        self.assertIn("一次只问一件", sys_txt)
        self.assertFalse(wc.open_convert(self.db, self.tid)["opened"], "重复交付不刷屏")

    def test_templates_compile_classes_exist(self):
        import workflow_converters as wc
        self.assertEqual(set(wc.TEMPLATES), {"plain", "elsevier", "ieee", "revtex"})
        for k in wc.TEMPLATES:
            tex = wc.md_to_tex(chr(10).join(["# 标题", "", "正文"]), "测试", template=k)
            self.assertTrue(tex.startswith("\documentclass"), k)

if __name__ == "__main__":
    unittest.main()
