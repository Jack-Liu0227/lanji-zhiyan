"""Saved skill source management and concurrent catalog search behavior."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import capabilities
import skill_registry
from test_skill_installation import archive, skill


class SkillSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="skill-sources-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db_path = self.root / "app.sqlite3"
        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        for target, name, value in ((capabilities.Path, "home", self.root / "home"), (skill_registry.paths, "CACHE", self.root / "cache")):
            mock = patch.object(target, name, return_value=value) if name == "home" else patch.object(target, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        skill_registry._SEARCH_CACHE.clear()

    def route(self, method, name, body=None, query=None):
        return capabilities.route(method, "/api/capabilities/" + name, body or {}, query or {}, self.db)

    def search(self, provider=None):
        query = {"q": ["example"]}
        if provider is not None:
            query["provider"] = [provider]
        return self.route("GET", "skill-search", query=query)

    def fixture(self, url):
        if "skills.sh" in url:
            return {"skills": [{"id": "owner/repo/example", "source": "owner/repo", "skillId": "example", "name": "example"}]}
        if "skillhub.cn" in url:
            return {"code": 0, "data": {"skills": [{"slug": "example", "name": "Example", "namespace": {"canonicalName": "@author/example"}}]}}
        return {"success": True, "data": {"skills": [{"name": "example", "githubUrl": "https://github.com/owner/repo/tree/main/skills/example"}]}}

    def custom(self, **overrides):
        return self.route("POST", "skill-sources", {"name": "Custom catalog", "adapter": "index", "url": "https://example.com",
            "api_url": "https://example.com/catalog.json", "enabled": True, **overrides})["source"]

    def test_default_search_queries_all_enabled_sources_concurrently(self):
        barrier = threading.Barrier(3)
        def fetch(url):
            barrier.wait(timeout=3)
            return self.fixture(url)
        with patch.object(skill_registry, "_json", side_effect=fetch) as request:
            result = self.search()
        self.assertEqual(request.call_count, 3)
        self.assertEqual(result["provider"], "all")
        self.assertEqual(result["errors"], [])
        self.assertEqual([item["provider"] for item in result["skills"]], ["skills-sh", "skillhub", "skillsmp"])
        self.assertEqual([item["provider_name"] for item in result["skills"]], ["skills.sh", "SkillHub", "SkillsMP"])

    def test_partial_failure_keeps_other_results_and_installable_provenance(self):
        def fetch(url):
            if "skillhub.cn" in url:
                raise ValueError("temporary quota")
            return self.fixture(url)
        with patch.object(skill_registry, "_json", side_effect=fetch):
            result = self.search()
        self.assertEqual(len(result["skills"]), 2)
        self.assertEqual(result["errors"][0]["provider"], "skillhub")
        selected = result["skills"][0]
        with patch.object(skill_registry, "_read", return_value=archive({"repo/skills/example/SKILL.md": skill("example")})):
            preview = self.route("POST", "skill-preview", {"source": selected["source"], "provider": selected["provider"], "selection": selected})
        installed = self.route("POST", "skill-install", {"token": preview["token"], "scope": "user", "skills": ["example"]})
        self.assertEqual(installed["installed"][0]["provider"], "skills-sh")

    def test_all_failures_are_reported_per_source(self):
        with patch.object(skill_registry, "_json", side_effect=ValueError("network unavailable")):
            result = self.search()
        self.assertEqual(result["skills"], [])
        self.assertEqual(len(result["errors"]), 3)
        self.assertTrue(all(not source["ok"] for source in result["sources"]))

    def test_disabled_and_deleted_sources_are_excluded(self):
        self.route("POST", "skill-sources", {"id": "skills-sh", "enabled": False})
        self.route("POST", "skill-sources/delete", {"id": "skillsmp"})
        with patch.object(skill_registry, "_json", side_effect=self.fixture) as request:
            result = self.search()
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result["skills"][0]["provider"], "skillhub")
        with self.assertRaises(ValueError):
            self.search("skills-sh")
        self.route("POST", "skill-sources", {"id": "skillhub", "enabled": False})
        with patch.object(skill_registry, "_json") as request:
            result = self.search()
        self.assertEqual(result["sources"], [])
        self.assertIn("没有启用", result["message"])
        request.assert_not_called()

    def test_edits_deletions_and_custom_sources_survive_database_reopen(self):
        self.route("POST", "skill-sources", {"id": "skills-sh", "name": "Renamed source", "enabled": False})
        custom = self.custom()
        self.route("POST", "skill-sources/delete", {"id": "skillhub"})
        self.db.close()
        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        saved = self.route("GET", "skill-sources")["sources"]
        self.assertNotIn("skillhub", {item["id"] for item in saved})
        self.assertIn(custom["id"], {item["id"] for item in saved})
        renamed = next(item for item in saved if item["id"] == "skills-sh")
        self.assertEqual(renamed["name"], "Renamed source")
        self.assertFalse(renamed["enabled"])
        restored = self.route("POST", "skill-sources/restore")["sources"]
        self.assertEqual(len(restored), 4)
        self.assertTrue(next(item for item in restored if item["id"] == "skills-sh")["enabled"])
        self.assertEqual(next(item for item in restored if item["id"] == "skills-sh")["name"], "skills.sh")

    def test_endpoint_changes_invalidate_cached_results_and_preserve_query_parameters(self):
        with patch.object(skill_registry, "_json", side_effect=lambda url: self.fixture("https://skills.sh")) as request:
            self.search("skills-sh")
            self.search("skills-sh")
            self.assertEqual(request.call_count, 1)
            self.route("POST", "skill-sources", {"id": "skills-sh", "name": "Edited source", "api_url": "https://mirror.example/search?tenant=abc"})
            result = self.search("skills-sh")
        self.assertIn("https://mirror.example/search?tenant=abc&q=example", request.call_args.args[0])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["skills"][0]["provider_name"], "Edited source")

    def test_invalid_source_edits_leave_saved_configuration_intact(self):
        before = self.route("GET", "skill-sources")["sources"]
        for changes in ({"name": ""}, {"url": "http://example.com"}, {"api_url": "https://user:password@example.com/search"},
                        {"adapter": "unknown"}, {"adapter": []}, {"enabled": "yes"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.route("POST", "skill-sources", {"id": "skills-sh", **changes})
        with self.assertRaises(ValueError):
            self.custom(adapter="skillhub", download_url="https://example.com/download")
        self.assertEqual(self.route("GET", "skill-sources")["sources"], before)

    def test_custom_index_search_installs_only_selected_skill_with_all_resources(self):
        source = self.custom()
        catalog = {"skills": [{"name": name, "description": "Example skill", "files": ["SKILL.md", "assets/binary.bin"]} for name in ("example", "example-two")]}
        with patch.object(skill_registry, "_json", return_value=catalog):
            result = self.search(source["id"])
        selected = result["skills"][0]
        def read(url, *args):
            if url.endswith("catalog.json"):
                return json.dumps(catalog).encode()
            return skill("example") if url.endswith("SKILL.md") else b"\xff\x00"
        with patch.object(skill_registry, "_read", side_effect=read):
            preview = self.route("POST", "skill-preview", {"source": selected["source"], "provider": source["id"], "selection": selected})
        self.assertEqual([item["name"] for item in preview["skills"]], ["example"])
        self.route("POST", "skill-install", {"token": preview["token"], "scope": "user", "skills": ["example"]})
        target = self.root / "home/.agents/skills/example/assets/binary.bin"
        self.assertEqual(target.read_bytes(), b"\xff\x00")
        self.route("POST", "skill-sources/delete", {"id": source["id"]})
        self.assertTrue(target.is_file())
        self.assertEqual(self.db.execute("SELECT provider FROM skill_installations WHERE name='example'").fetchone()[0], source["id"])

    def test_compatible_skillhub_source_uses_its_configured_download_endpoint(self):
        source = self.custom(adapter="skillhub", api_url="https://mirror.example/api/skills", download_url="https://mirror.example/package?slug={slug}")
        with patch.object(skill_registry, "_json", return_value=self.fixture("https://api.skillhub.cn")):
            selected = self.search(source["id"])["skills"][0]
        with patch.object(skill_registry, "_read", return_value=archive({"SKILL.md": skill("example")})) as read:
            preview = self.route("POST", "skill-preview", {"source": selected["source"], "provider": source["id"], "selection": selected})
        self.assertEqual(read.call_args.args[0], "https://mirror.example/package?slug=%40author%2Fexample")
        self.assertEqual(preview["provider"], source["id"])


if __name__ == "__main__":
    unittest.main()
