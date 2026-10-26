# -*- coding: utf-8 -*-
"""整篇中译英走 translate_doc：一次工具调用、服务端分片并行、落 正文_en.md。

不测文笔（chat 打桩），测的是「接线」：源读对、产物落对、分片数对、空正文会被拒。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _n in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, os.path.join(_ROOT, "packages", _n))

import workflow  # noqa: E402
import workflow_documents as docs  # noqa: E402
import workflow_runners as runners  # noqa: E402


class TranslateDocTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name) / "proj"
        self.base.mkdir(parents=True)
        self.db = workflow.WorkflowDB(Path(self.tmp.name) / "t.sqlite3")
        self.tid = self.db.create_task("三维电荷", "q", "Na", str(self.base))
        self._chat = workflow.chat
        self._model = self.db.active_model

        def fake_chat(profile, messages, **kw):
            self.calls = getattr(self, "calls", 0) + 1
            return "EN: " + messages[-1]["content"][:40]
        workflow.chat = fake_chat
        self.db.active_model = lambda: {"model": "fake", "base_url": "", "api_key": "", "api": "openai-completions"}

    def tearDown(self):
        workflow.chat = self._chat
        self.db.active_model = self._model
        self.db.close()
        self.tmp.cleanup()

    def test_整篇翻译落成正文_en_md(self):
        docs.doc_write(self.db, self.tid, "write", "# 1 引言\n\n## 1.1 背景\n这是中文正文。\n\n## 1.2 论点\n这是第二段中文。\n")
        out = runners.translate_doc(self.db, self.tid, style="formal")
        self.assertTrue(out["path"].endswith("正文_en.md"))
        self.assertGreaterEqual(out["chunks"], 1)
        en = Path(out["path"]).read_text(encoding="utf-8")
        self.assertIn("EN:", en)
        self.assertGreater(out["src_chars"], 10)

    def test_没有正文就报错不乱写(self):
        with self.assertRaises(ValueError):
            runners.translate_doc(self.db, self.tid)


if __name__ == "__main__":
    unittest.main()
