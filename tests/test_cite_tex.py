# -*- coding: utf-8 -*-
"""正文标记 → 引用：md_to_tex 的 {{Cn}} 转换 + 真编译一条链（xelatex → bibtex → xelatex ×2）。

py -3.10 test_cite_tex.py
判据不是「编出 PDF 了」，而是 `.aux` 里出现 `\\bibcite{key}{1}` —— 那才说明编号真解析了，
以前只跑一遍 xelatex，这里会是 `??`。
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _n in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, os.path.join(_ROOT, "packages", _n))

from workflow_converters import latex_chain, md_to_tex  # noqa: E402

BIB = """@article{smith2020,
  author  = {Smith, John},
  title   = {A study of sodium layered cathodes},
  journal = {Journal of Testing},
  year    = {2020},
  volume  = {12},
  pages   = {345--356}
}
"""

TEX = r"""\documentclass{article}
\begin{document}
Layered cathodes degrade fast \cite{smith2020}.
\bibliographystyle{unsrt}
\bibliography{refs}
\end{document}
"""


class MarkTest(unittest.TestCase):
    def test_标记变成cite(self) -> None:
        tex = md_to_tex("结果见文献{{C3}}与{{C1}}。", "标题", cite_keys={3: "smith2020", 1: "lee2021"},
                        cite_style="gbt7714-numeric", cite_package="gbt7714")
        self.assertIn(r"\cite{smith2020}", tex)
        self.assertIn(r"\cite{lee2021}", tex)
        self.assertNotIn("{{C3}}", tex)
        self.assertIn(r"\usepackage{gbt7714}", tex)          # 不装宏包，.bbl 里的 \doi 会直接编崩
        self.assertIn(r"\bibliographystyle{gbt7714-numeric}", tex)
        self.assertIn(r"\bibliography{refs}", tex)

    def test_对不上的编号原样留着(self) -> None:
        tex = md_to_tex("见{{C9}}。", "标题", cite_keys={1: "a2020"}, cite_style="plain")
        self.assertIn(r"C9", tex)
        self.assertIn(r"\{\{C9\}\}", tex)              # 花括号转义后原样印出来，不能悄悄丢掉这处引用
        self.assertIn(r"\bibliography{refs}", tex)    # 有 keys 就挂参考文献表
        tex2 = md_to_tex("没有引用的正文。", "标题")
        self.assertNotIn(r"\bibliography", tex2)      # 没引用就不生成参考文献表


class ChainTest(unittest.TestCase):
    def test_真编译一遍带引用的tex(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "main.tex").write_text(TEX, encoding="utf-8", newline="\n")
            (d / "refs.bib").write_text(BIB, encoding="utf-8")

            def run(cmd, cwd):
                return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                                      timeout=300, creationflags=0x08000000)

            got = latex_chain(d / "main.tex", run)
            self.assertIsNone(got.get("error"), got.get("error"))
            self.assertTrue(got["bibtex"].startswith("ok"), got["bibtex"])   # ok(bibtex) / ok(bibtex8) 都算
            self.assertEqual(got["passes"], 3)         # xelatex、bibtex、xelatex、xelatex
            self.assertTrue(Path(got["pdf"]).is_file())
            aux = (d / "main.aux").read_text(encoding="utf-8", errors="replace")
            self.assertIn(r"\bibcite{smith2020}{1}", aux)   # 编号真的解析了，不是 ??
            self.assertIn("Smith", (d / "main.bbl").read_text(encoding="utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
