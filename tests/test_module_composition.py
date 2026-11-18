"""User-controlled API module composition contract."""
import sqlite3
import unittest
from types import SimpleNamespace

from api_registry import ApiRegistry
from module_composition import route_for
from service_settings import read, write


class ModuleCompositionTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.db = SimpleNamespace(db=self.conn)
        self.registry = ApiRegistry({"database"})
        self.calls = []
        self.registry.register("module-composition", route_for(self.registry), requires=("database",))
        self.registry.register("papers", lambda *args: self.calls.append("papers") or {"ok": True},
                              version="1.2.0", requires=("database",))

    def tearDown(self):
        self.conn.close()

    def test_preferences_persist_and_apply_before_dispatch(self):
        result = self.registry.dispatch("POST", "/api/modules/papers", {"enabled": False}, {}, self.db)
        self.assertEqual(result, {"ok": True, "module": "papers", "enabled": False})
        self.assertIsNone(self.registry.dispatch("GET", "/api/papers", {}, {}, self.db))
        self.assertEqual(self.calls, [])
        self.assertEqual(read(self.db, "module-composition")["modules"]["papers"],
                         {"enabled": False, "version": "1.2.0"})

    def test_major_change_requires_acknowledgement_and_records_current_version(self):
        write(self.db, "module-composition", {"modules": {
            "papers": {"enabled": False, "version": "0.9.0"},
        }})

        result = self.registry.dispatch("POST", "/api/modules/papers",
                                        {"enabled": True, "accept_major_change": True}, {}, self.db)

        self.assertTrue(result["enabled"])
        saved = read(self.db, "module-composition")["modules"]["papers"]
        self.assertEqual(saved, {"enabled": True, "version": "1.2.0"})
        modules = self.registry.dispatch("GET", "/api/modules", {}, {}, self.db)["modules"]
        self.assertEqual(next(m for m in modules if m["id"] == "papers")["compatibility"], "current")

    def test_major_version_change_is_reported_and_enable_is_blocked(self):
        write(self.db, "module-composition", {"modules": {
            "papers": {"enabled": False, "version": "0.9.0"},
            "removed-module": {"enabled": True, "version": "4.0.0"},
        }})
        modules = self.registry.dispatch("GET", "/api/modules", {}, {}, self.db)["modules"]
        papers = next(module for module in modules if module["id"] == "papers")
        self.assertEqual(papers["compatibility"], "major-change")
        self.assertFalse(papers["enabled"])
        with self.assertRaisesRegex(ValueError, "major version"):
            self.registry.dispatch("POST", "/api/modules/papers", {"enabled": True}, {}, self.db)
        self.assertIn("removed-module", read(self.db, "module-composition")["modules"])


if __name__ == "__main__":
    unittest.main()
