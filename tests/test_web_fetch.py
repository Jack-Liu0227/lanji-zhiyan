# -*- coding: utf-8 -*-
"""POST 必须带 Content-Type: application/json —— 服务端 do_POST 见不到这个头就直接 415，
请求连路由都进不去。2026-10-12 开始卡片的「Enter → 全屏」就是栽在这上面：
splash.js 里裸 fetch 了 /api/win/maximize，被 415 挡掉、catch 一吞，表现成「点了没反应」。

    python -m unittest test_web_fetch -v
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CALL = re.compile(r"fetch\((.*?)\)", re.S)
EXEMPT = "/api/upload"          # 唯一豁免：它走原始字节 body，server.py 在闸门之前就 return 了


class WebFetchTest(unittest.TestCase):
    def test_post_fetches_carry_json_header(self) -> None:
        bad = []
        files = [ROOT / "web" / "index.html"] + sorted((ROOT / "web" / "vendor").glob("*.js"))
        for f in files:
            for m in CALL.finditer(f.read_text(encoding="utf-8")):
                arg = m.group(1)
                if '"POST"' not in arg and "'POST'" not in arg:
                    continue
                if EXEMPT in arg or "Content-Type" in arg:
                    continue
                bad.append("%s: %s" % (f.name, " ".join(arg.split())[:90]))
        self.assertEqual(bad, [], "这些 POST 少了 Content-Type: application/json（会被 415 挡掉）：\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
