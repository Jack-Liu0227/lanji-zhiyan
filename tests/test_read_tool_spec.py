# -*- coding: utf-8 -*-
"""工具读取上限：读一份 3 万字的正文，得能真的到模型手里。

之前两处卡着：
  * read_attachment 一次只给 12000 字（READ_MAX）；
  * 工具结果回喂给模型时被切到 4000 字（run_agent 里那个切片）—— 3 万字正文只够看到标题。
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
from workflow_prompts import TOOL_SPECS  # noqa: E402


class ReadCapTest(unittest.TestCase):
    def test_读附件一次能吃掉三万字的正文(self):
        self.assertGreaterEqual(docs.READ_MAX, 48000)
        # 必须落在允许范围里（data/uploads），否则会先被白名单挡掉 —— 那是另一道闸，别在这里绕过
        import paths
        host = Path(paths.DATA) / "uploads"
        host.mkdir(parents=True, exist_ok=True)
        p = host / "_cap_test_big.md"
        p.write_text("正文段落。" * 6000, encoding="utf-8")
        try:
            out = docs.read_attachment(str(p), 48000)
        finally:
            p.unlink(missing_ok=True)
        self.assertGreater(out["chars"], 25000)
        self.assertFalse(out["truncated"], "整份正文要能一次读完，不能再被切")

    def test_工具结果回喂模型的上限是两万字(self):
        self.assertEqual(workflow.TOOL_RESULT_MAX, 40000)
        src = Path(workflow.__file__).read_text(encoding="utf-8")
        self.assertIn("[:TOOL_RESULT_MAX]", src, "切片要真的用上这个常量")

    def test_read_attachment在工具表里_且说清默认路径(self):
        names = [t["function"]["name"] for t in TOOL_SPECS]
        self.assertIn("read_attachment", names)
        spec = next(t for t in TOOL_SPECS if t["function"]["name"] == "read_attachment")
        self.assertEqual(spec["function"]["parameters"]["required"], ["path"])
        self.assertIn("论文正文/正文.md",
                      spec["function"]["parameters"]["properties"]["path"]["description"])


if __name__ == "__main__":
    unittest.main()
