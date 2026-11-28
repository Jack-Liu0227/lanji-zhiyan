import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for name in ("database", "workspace-core", "workspace-service", "connectors"):
    sys.path.insert(0, str(ROOT / "packages" / name))

import capabilities
import zotero_mcp
import zotero_mcp_routes


class ZoteroMcpConfigTest(unittest.TestCase):
    def test_source_mode_launches_the_stdio_script_with_console_python(self):
        with patch.object(sys, "frozen", False, create=True):
            config = capabilities._zotero_mcp_config()

        self.assertEqual(config["command"], sys.executable)
        self.assertEqual(Path(config["args"][0]).name, "zotero_mcp.py")
        self.assertTrue(Path(config["args"][0]).is_file())

    def test_packaged_mode_uses_the_bundled_stdio_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary)
            executable = app / "tools" / "zotero-mcp" / "zotero-mcp.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"mcp")
            with patch.object(sys, "frozen", True, create=True), patch.object(capabilities.paths, "APP", app):
                config = capabilities._zotero_mcp_config()

        self.assertEqual(config, {"command": str(executable), "args": []})

    def test_packaged_mode_reports_a_missing_bundled_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(sys, "frozen", True, create=True), patch.object(capabilities.paths, "APP", Path(temporary)):
                with self.assertRaisesRegex(FileNotFoundError, "reinstall"):
                    capabilities._zotero_mcp_config()

    def test_stale_builtin_zotero_path_auto_heals_on_config_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary)
            executable = app / "tools" / "zotero-mcp" / "zotero-mcp.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"mcp")
            db = sqlite3.connect(":memory:")
            stale_cfg = {
                "mcpServers": {
                    "zotero": {
                        "command": str(app / "old-folder" / "tools" / "zotero-mcp" / "zotero-mcp.exe"),
                        "args": [],
                        "transport": "stdio",
                        "enabled": True,
                    }
                }
            }
            capabilities._ensure(db).execute(
                "INSERT OR REPLACE INTO capability_mcp_config(id,protected) VALUES(1,?)",
                (capabilities._protect(stale_cfg),),
            )
            db.commit()
            with patch.object(sys, "frozen", True, create=True), patch.object(capabilities.paths, "APP", app):
                healed = capabilities._config(db)
            self.assertEqual(healed["mcpServers"]["zotero"]["command"], str(executable))
            self.assertTrue(healed["mcpServers"]["zotero"]["enabled"])
            db.close()


class ZoteroMcpToolsTest(unittest.TestCase):
    def test_lists_builtin_tools_including_collections_recent_and_annotations(self):
        names = {t["name"] for t in zotero_mcp._tools_list()["tools"]}
        self.assertTrue({"zotero_status", "zotero_collections", "zotero_recent", "zotero_annotations"} <= names)

    def test_annotations_tool_parses_highlights_and_notes(self):
        children = [
            {"key": "ANN00001", "data": {
                "itemType": "annotation", "annotationType": "highlight",
                "annotationText": "Solid electrolyte interphase", "annotationComment": "key point",
                "annotationColor": "#ffd400", "annotationPageLabel": "4", "parentItem": "PDF00001",
            }},
            {"key": "NOTE0001", "data": {
                "itemType": "note", "note": "<p>Summary note</p>", "tags": [{"tag": "review"}],
            }},
        ]
        with patch.object(zotero_mcp.zotero, "_get", return_value=(200, {}, children)):
            out = zotero_mcp.tool_annotations({"key": "ITEM0001"})
        self.assertEqual(out["annotations"][0]["text"], "Solid electrolyte interphase")
        self.assertEqual(out["annotations"][0]["page"], "4")
        self.assertEqual(out["notes"][0]["text"], "Summary note")
        self.assertEqual(out["notes"][0]["tags"], ["review"])


class ZoteroMcpStatusTest(unittest.TestCase):
    def test_status_checks_tool_discovery_and_local_zotero_readability(self):
        server = {"command": "zotero-mcp.exe", "enabled": True}
        db = object()
        response = {"content": [{"type": "text", "text": json.dumps({
            "local": {"readable": True, "message": "Zotero ready"}})}]}
        with patch.object(capabilities, "_config", return_value={"mcpServers": {"zotero": server}}), \
                patch.object(capabilities, "discover_mcp", return_value={
                    "tools": [{"name": "zotero_status"}], "probe": response}) as discover:
            result = zotero_mcp_routes.route("POST", "/api/zotero/mcp/check", {}, {}, db)

        self.assertTrue(result["connected"])
        self.assertTrue(result["local_readable"])
        self.assertEqual(result["tools_count"], 1)
        discover.assert_called_once_with(db, "zotero", probe_tool="zotero_status")

    def test_status_reports_failed_process_start_without_claiming_connected(self):
        server = {"command": "zotero-mcp.exe", "enabled": True}
        with patch.object(capabilities, "_config", return_value={"mcpServers": {"zotero": server}}), \
                patch.object(capabilities, "discover_mcp", side_effect=FileNotFoundError("private path")):
            result = zotero_mcp_routes.route("POST", "/api/zotero/mcp/check", {}, {}, None)

        self.assertFalse(result["connected"])
        self.assertIn("FileNotFoundError", result["error"])
        self.assertNotIn("private path", result["error"])

    def test_render_status_does_not_start_an_enabled_mcp_server(self):
        server = {"command": "configured-command", "enabled": True}
        with patch.object(capabilities, "_config", return_value={"mcpServers": {"zotero": server}}), \
                patch.object(capabilities, "discover_mcp") as discover:
            result = zotero_mcp_routes.route("GET", "/api/zotero/mcp/status", {}, {}, None)

        self.assertTrue(result["enabled"])
        self.assertFalse(result["checked"])
        self.assertIn("尚未测试", result["error"])
        discover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
