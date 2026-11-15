"""Behavioral contract for composable API modules."""
import unittest

from api_registry import ApiRegistry


class ApiRegistryTest(unittest.TestCase):
    def test_set_enabled_changes_module_dispatch(self):
        registry = ApiRegistry()
        calls = []
        registry.register("module", lambda *args: calls.append("called") or {"ok": True})

        registry.set_enabled("module", False)

        self.assertIsNone(registry.dispatch("GET", "/api/test", {}, {}, object()))
        self.assertEqual(calls, [])
        registry.set_enabled("module", True)
        self.assertEqual(registry.dispatch("GET", "/api/test", {}, {}, object()), {"ok": True})

    def test_dispatch_uses_first_module_that_handles_request(self):
        registry = ApiRegistry()
        calls = []

        def unhandled(method, path, body, query, db):
            calls.append("unhandled")
            return None

        def handled(method, path, body, query, db):
            calls.append("handled")
            return {"module": "handled"}

        registry.register("first", unhandled)
        registry.register("second", handled)
        registry.register("third", lambda *args: calls.append("too-late"))

        self.assertEqual(registry.dispatch("GET", "/api/test", {}, {}, object()), {"module": "handled"})
        self.assertEqual(calls, ["unhandled", "handled"])

    def test_module_ids_must_be_unique(self):
        registry = ApiRegistry()
        registry.register("same", lambda *args: None)
        with self.assertRaisesRegex(ValueError, "same"):
            registry.register("same", lambda *args: None)

    def test_unhandled_request_returns_none(self):
        registry = ApiRegistry()
        registry.register("module", lambda *args: None)
        self.assertIsNone(registry.dispatch("GET", "/api/missing", {}, {}, object()))

    def test_module_contract_records_version_dependencies_and_enabled_state(self):
        registry = ApiRegistry(available_capabilities={"database"})
        registry.register("papers", lambda *args: {"ok": True}, version="1.2.0",
                          requires=("database",), enabled=False)
        self.assertEqual(registry.describe(), [{
            "id": "papers", "version": "1.2.0", "requires": ["database"], "enabled": False,
        }])
        self.assertIsNone(registry.dispatch("GET", "/api/papers", {}, {}, object()))

    def test_module_cannot_require_missing_host_capability(self):
        registry = ApiRegistry(available_capabilities={"database"})
        with self.assertRaisesRegex(ValueError, "files"):
            registry.register("papers", lambda *args: None, requires=("files",))

    def test_module_version_must_be_semver(self):
        registry = ApiRegistry()
        with self.assertRaisesRegex(ValueError, "version"):
            registry.register("papers", lambda *args: None, version="next")


if __name__ == "__main__":
    unittest.main()
