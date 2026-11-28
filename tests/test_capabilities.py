"""Behavior checks for expert capability defaults and per-expert overrides."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _name in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(_ROOT / "packages" / _name))

import capabilities  # noqa: E402
import workflow  # noqa: E402
import workflow_context  # noqa: E402


class CapabilityDefaultsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.temp.name) / "capabilities.sqlite3"
        self.previous_db = os.environ.get("RESEARCH_WORKBENCH_DB")
        os.environ["RESEARCH_WORKBENCH_DB"] = str(self.path)
        self.db = workflow.WorkflowDB(self.path)

    def tearDown(self):
        self.db.close()
        if self.previous_db is None:
            os.environ.pop("RESEARCH_WORKBENCH_DB", None)
        else:
            os.environ["RESEARCH_WORKBENCH_DB"] = self.previous_db
        self.temp.cleanup()

    def test_builtin_skill_defaults_are_role_scoped_and_explicit_bindings_win(self):
        self.assertTrue(capabilities.enabled(self.db, "builtin-searcher", "skill",
                                             "scheduled-news-digest", item_scope="builtin"))
        self.assertFalse(capabilities.enabled(self.db, "builtin-searcher", "skill",
                                              "scheduled-feishu-draft", item_scope="builtin"))
        self.assertTrue(capabilities.enabled(self.db, "builtin-searcher", "skill",
                                             "custom-user-skill", item_scope="user"))
        self.assertTrue(capabilities.enabled(self.db, "builtin-searcher", "skill",
                                             "research-steward", item_scope="project"))

        conn = self.db.db
        conn.execute("INSERT INTO capability_bindings VALUES(?,?,?,?)",
                     ("*", "skill", "scheduled-news-digest", 0))
        conn.commit()
        self.assertFalse(capabilities.enabled(self.db, "builtin-searcher", "skill",
                                              "scheduled-news-digest", item_scope="builtin"))
        conn.execute("INSERT INTO capability_bindings VALUES(?,?,?,?)",
                     ("builtin-searcher", "skill", "scheduled-news-digest", 1))
        conn.commit()
        self.assertTrue(capabilities.enabled(self.db, "builtin-searcher", "skill",
                                             "scheduled-news-digest", item_scope="builtin"))
        conn.execute("UPDATE capability_bindings SET enabled=0 WHERE assistant_id='builtin-searcher' "
                     "AND kind='skill' AND item_id='scheduled-news-digest'")
        conn.commit()
        self.assertFalse(capabilities.enabled(self.db, "builtin-searcher", "skill",
                                              "scheduled-news-digest", item_scope="builtin"))

    def test_skill_context_exposes_only_default_builtin_skills_for_the_expert(self):
        skills = [
            {"id": name, "description": name, "scope": "builtin"}
            for name in ("research-steward", "scheduled-feishu-draft", "scheduled-literature-matrix",
                         "scheduled-literature-review", "scheduled-news-digest")
        ]
        with patch.object(capabilities, "list_skills", return_value=skills):
            context = workflow_context.skill_catalog(self.db, "builtin-searcher")
        payload = json.loads(context.split("\n", 1)[0][len(workflow_context.SKILL_CATALOG_PREFIX):])
        self.assertEqual(
            {item["skill_id"] for item in payload["enabled_skills"]},
            {"scheduled-literature-matrix", "scheduled-literature-review", "scheduled-news-digest"},
        )

    def test_skill_read_enforces_the_same_default_as_discovery(self):
        with tempfile.TemporaryDirectory() as root:
            skill_dir = pathlib.Path(root) / "research-steward"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("---\nname: research-steward\n---\n\nrole instructions\n", encoding="utf-8")
            item = {"id": "research-steward", "name": "research-steward", "scope": "builtin",
                    "path": str(skill_dir / "SKILL.md"), "dir": str(skill_dir), "description": ""}
            with patch.object(capabilities, "list_skills", return_value=[item]):
                with self.assertRaises(PermissionError):
                    capabilities.invoke_tool(self.db, "builtin-searcher", "skill_read",
                                              {"skill_id": "research-steward"}, root)
                result = capabilities.invoke_tool(self.db, "builtin-steward", "skill_read",
                                                  {"skill_id": "research-steward"}, root)
            self.assertIn("role instructions", result["content"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
