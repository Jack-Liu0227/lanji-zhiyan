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
    def test_verified_low_root_is_readonly_and_changed_label_is_repaired(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        temporary = tempfile.TemporaryDirectory(prefix='lowbox-label-')
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        self.assertTrue(lowbox.mark_low(folder, recursive=True))
        self.assertTrue(lowbox.has_inheritable_low_label(folder))
        with patch.object(lowbox.subprocess, 'run', side_effect=AssertionError('Repeated ACL rewrite')):
            self.assertTrue(lowbox.mark_low(folder, recursive=False))
        for label in ('M', 'L'):
            with self.subTest(label=label):
                result = lowbox.subprocess.run(['icacls', str(folder), '/setintegritylevel', label],
                    capture_output=True, creationflags=0x08000000, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(lowbox.has_inheritable_low_label(folder))
                self.assertTrue(lowbox.mark_low(folder, recursive=False))
                self.assertTrue(lowbox.has_inheritable_low_label(folder))
        self.assertFalse(lowbox.has_inheritable_low_label(folder / 'missing'))

    def test_granted_directory_writable_protected_directory_denied(self) -> None:
        import tempfile
        from pathlib import Path
        temporary = tempfile.TemporaryDirectory(prefix="lowbox_t_", dir=os.environ.get("LOCALAPPDATA"))
        self.addCleanup(temporary.cleanup)
        base = str(Path(temporary.name) / "granted")
        protected = Path(temporary.name) / "protected"
        Path(base).mkdir()
        protected.mkdir()
        self.assertTrue(lowbox.mark_low(base, recursive=True), "mark_low 失败")
        probe = (
            "import os,sys\n"
            "open(os.path.join(sys.argv[1],'ok.txt'),'w').write('x')\n"
            "print('granted=ok')\n"
            "try:\n"
            "    open(os.path.join(sys.argv[2],'denied.txt'),'w').write('x')\n"
            "    print('protected=WROTE')\n"
            "except PermissionError:\n"
            "    print('protected=denied')\n"
        )
        res = lowbox.run([sys.executable, "-c", probe, base, str(protected)], timeout=120)
        self.assertIn("granted=ok", res.stdout)
        self.assertIn("protected=denied", res.stdout, "Low IL failed to block writes to the protected directory")
        self.assertFalse((protected / "denied.txt").exists())


if __name__ == "__main__":
    unittest.main()
