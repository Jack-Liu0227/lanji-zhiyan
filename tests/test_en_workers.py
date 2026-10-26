# -*- coding: utf-8 -*-
"""一键翻译的并发：21 片 4 路要等 6 波，8 路三波就完 —— 这里盯住并发数别被改回去。"""
import os
import sys
import threading
import time
import unittest
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _n in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, os.path.join(_ROOT, "packages", _n))

import workflow  # noqa: E402
import workflow_converters as conv  # noqa: E402


class EnWorkersTest(unittest.TestCase):
    def test_并发数至少八路(self):
        self.assertGreaterEqual(conv.EN_WORKERS, 8)

    def test_分片是真并行而不是排队(self):
        self._orig = workflow.chat
        live, peak, lock = [0], [0], threading.Lock()

        def slow_chat(profile, messages, **kw):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.15)
            with lock:
                live[0] -= 1
            return "EN"

        workflow.chat = slow_chat
        try:
            md = "\n\n".join(f"第 {i} 段中文。" + "正文内容" * 150 for i in range(30))   # 约 1.8 万字 → 15 片
            out = conv.en_translate({"model": "fake", "base_url": "", "api_key": ""}, md)
        finally:
            workflow.chat = self._orig
        self.assertGreater(out["chunks"], 8)
        self.assertGreaterEqual(peak[0], 8, f"同时在跑的翻译只有 {peak[0]} 路，说明并发没生效")


if __name__ == "__main__":
    unittest.main()
