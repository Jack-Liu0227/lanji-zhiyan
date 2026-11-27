# -*- coding: utf-8 -*-
"""附件读取与截断行为自检。"""
import tempfile
import unittest
from pathlib import Path

import paths
import workflow_documents


class ModuleWiringTest(unittest.TestCase):
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
