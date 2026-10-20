"""lowbox 自检：Low IL 子进程写授权目录成功、写桌面被内核拒绝。

这是「真边界」那一层的判据 —— 如果哪天 Windows 改了行为、或者我们的令牌逻辑退化成普通 spawn，
`write_desktop` 就会变成成功，注册表/桌面立刻能被写，这个测试会红。
"""
from __future__ import annotations

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _n in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, os.path.join(_ROOT, "packages", _n))

import lowbox  # noqa: E402


@unittest.skipUnless(lowbox.available() and os.environ.get("RW_TEST_LOWBOX"), "需要 Windows 且显式开启（会改临时目录完整性等级）")
class LowBoxTest(unittest.TestCase):
    def test_授权目录可写_桌面不可写(self) -> None:
        import tempfile
        base = tempfile.mkdtemp(prefix="lowbox_t_", dir=os.environ.get("LOCALAPPDATA"))
        self.assertTrue(lowbox.mark_low(base, recursive=True), "mark_low 失败")
        probe = (
            "import os,sys\n"
            "open(os.path.join(sys.argv[1],'ok.txt'),'w').write('x')\n"
            "print('granted=ok')\n"
            "try:\n"
            "    open(os.path.join(os.path.expanduser('~'),'Desktop','lowbox_test.txt'),'w').write('x')\n"
            "    print('desktop=WROTE')\n"
            "except PermissionError:\n"
            "    print('desktop=denied')\n"
        )
        res = lowbox.run([sys.executable, "-c", probe, base], timeout=120)
        self.assertIn("granted=ok", res.stdout)
        self.assertIn("desktop=denied", res.stdout, "桌面居然写得进去 → Low IL 边界没生效")


if __name__ == "__main__":
    unittest.main()
