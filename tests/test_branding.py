"""统一产品命名：防止用户可见名称重新混入旧英文品牌。"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "workspace-core"))


class BrandingTest(unittest.TestCase):
    def test_app_name_is_chinese(self) -> None:
        import paths
        self.assertEqual(paths.APP_NAME, "览籍智研")

    def test_packaging_artifacts_use_chinese_name(self) -> None:
        spec = (ROOT / "tools" / "览籍智研.spec").read_text(encoding="utf-8")
        installer = (ROOT / "tools" / "installer.spec").read_text(encoding="utf-8")
        self.assertEqual(spec.count("name='览籍智研'"), 2)
        self.assertNotIn("name='ResearchWorkbench'", spec)
        self.assertIn("name='览籍智研-安装程序'", installer)
        installer_py = (ROOT / "tools" / "installer.py").read_text(encoding="utf-8")
        self.assertIn('APP = "览籍智研"', installer_py)

    def test_sidebar_shows_chinese_brand(self) -> None:
        core = (ROOT / "apps" / "desktop" / "web" / "views" / "app-core.js").read_text(encoding="utf-8")
        self.assertIn("<b>览籍智研</b>", core)
        self.assertNotIn("Research workspace", core)


if __name__ == "__main__":
    unittest.main()
