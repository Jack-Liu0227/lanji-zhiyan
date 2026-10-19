# -*- coding: utf-8 -*-
"""模块接线自检：跨模块用的名字到底在不在（防再出现「重构拆模块时漏了 import」）。

这组名字曾经全部是 NameError：精读/写作/审查/检索/下载 stage、附件读取、英文翻译、
定时任务文件名、审计里的 hashlib —— 平时不走到就当没事，一走到整步报错。
"""
import tempfile
import unittest
from pathlib import Path

import paths
import workflow_documents
import workflow_stages


class ModuleWiringTest(unittest.TestCase):
    def test_stage_globals_resolve(self):
        # chat / chat_json 住在 workflow.py（反向 import 会成环），所以是函数内 import，不在这里查
        for name in ("search", "enrich_oa", "download_pdf", "READ_SYSTEM"):
            self.assertTrue(hasattr(workflow_stages, name), f"workflow_stages 缺 {name}")

    def test_document_globals_resolve(self):
        for name in ("pdfio", "OUTLINE_TEMPLATE", "DRAFT_TEMPLATE"):
            self.assertTrue(hasattr(workflow_documents, name), f"workflow_documents 缺 {name}")

    def test_read_attachment_reads_and_truncates(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", dir=paths.DATA, delete=False,
                                         encoding="utf-8") as fh:
            fh.write("证据原文" * 500)
            tmp = Path(fh.name)
        try:
            got = workflow_documents.read_attachment(str(tmp), chars=600)
            self.assertEqual(got["path"], str(tmp))
            self.assertEqual(len(got["text"]), 600)
            self.assertTrue(got["truncated"])
        finally:
            tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
