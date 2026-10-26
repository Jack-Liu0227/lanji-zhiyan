"""Exercise installation through the same capability routes used by the UI."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sqlite3
import stat
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

import capabilities
import skill_registry


def skill(name):
    return f"---\nname: {name}\ndescription: Example {name}\n---\n# {name}\n".encode()


def archive(files):
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as package:
        for name, data in files.items():
            package.writestr(name, data)
    return blob.getvalue()


class SkillInstallationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="skill-install-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "project"
        self.home = self.root / "home"
        self.db = sqlite3.connect(self.root / "app.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE research_tasks(id TEXT PRIMARY KEY,base_dir TEXT)")
        self.db.execute("INSERT INTO research_tasks VALUES('project',?)", (str(self.project),))
        self.db.commit()
        for target, name, value in ((capabilities.Path, "home", self.home),
                                    (skill_registry.paths, "CACHE", self.root / "cache"),
                                    (capabilities.paths, "APP", self.root / "app")):
            mock = patch.object(target, name, return_value=value) if name == "home" else patch.object(target, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        skill_registry._SEARCH_CACHE.clear()
        capabilities._ensure(self.db)
        self.db.execute("INSERT INTO capability_bindings VALUES('existing-assistant','skill','existing',0)")
        self.db.commit()

    def route(self, method, path, body=None, query=None):
        return capabilities.route(method, "/api/capabilities/" + path, body or {}, query or {}, self.db)

    def local(self, name="example"):
        folder = self.root / "sources" / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_bytes(skill(name))
        (folder / "references").mkdir()
        (folder / "references" / "guide.md").write_text("Guide", encoding="utf-8")
        (folder / "assets").mkdir()
        (folder / "assets" / "binary.bin").write_bytes(b"\x00\xff\x80\r\n")
        (folder / "scripts").mkdir()
        (folder / "scripts" / "run.py").write_text("raise RuntimeError('must not execute')", encoding="utf-8")
        return folder

    def install(self, preview, names=None, **options):
        return self.route("POST", "skill-install", {"token": preview["token"],
            "skills": names or [item["name"] for item in preview["skills"]], "scope": "user", **options})

    def test_sources_are_built_in_without_network_or_configuration(self):
        with patch.object(skill_registry, "_read", side_effect=AssertionError("Network during source listing")):
            sources = self.route("GET", "skill-sources")["sources"]
        self.assertEqual([item["id"] for item in sources], ["skills-sh", "skillhub", "skillsmp"])
        sources[0]["name"] = "Changed"
        self.assertEqual(self.route("GET", "skill-sources")["sources"][0]["name"], "skills.sh")

    def test_local_install_keeps_all_files_and_assistant_configuration(self):
        folder = self.local()
        before = list(self.db.execute("SELECT * FROM capability_bindings"))
        preview = self.route("POST", "skill-preview", {"source": str(folder)})
        self.assertEqual(preview["skills"][0]["files"], 4)
        result = self.install(preview)
        target = self.home / ".agents/skills/example"
        for file in folder.rglob("*"):
            if file.is_file():
                self.assertEqual((target / file.relative_to(folder)).read_bytes(), file.read_bytes())
        self.assertEqual(list(self.db.execute("SELECT * FROM capability_bindings")), before)
        self.assertEqual(result["installed"][0]["provider"], "local")
        metadata = capabilities.settings_meta(self.db)
        installed = next(item for item in metadata["skills"] if item["id"] == "example")
        self.assertEqual(installed["installation"]["source"], str(folder.resolve()))
        self.assertEqual(installed["installation"]["digest"], skill_registry._digest(target)[0])

    def test_project_install_uses_project_folder_and_preserves_user_copy(self):
        preview = self.route("POST", "skill-preview", {"source": str(self.local())})
        self.install(preview)
        result = self.install(preview, scope="project", project_id="project")
        self.assertTrue((self.project / ".agents/skills/example/SKILL.md").is_file())
        self.assertTrue((self.home / ".agents/skills/example/SKILL.md").is_file())
        self.assertEqual(result["installed"][0]["scope"], "project")
        selected = capabilities.list_skills(str(self.project))[0]
        self.assertEqual(selected["scope"], "project")

    def test_staging_files_are_not_visible_as_installed_skills(self):
        preview = self.route("POST", "skill-preview", {"source": str(self.local())})
        original_copy = skill_registry._copy
        discovered = []
        def copy_and_scan(source, target):
            original_copy(source, target)
            discovered.extend(capabilities.list_skills())
        with patch.object(skill_registry, "_copy", side_effect=copy_and_scan):
            self.install(preview)
        self.assertEqual(discovered, [])
        self.assertEqual([item["name"] for item in capabilities.list_skills()], ["example"])

    def test_skills_sh_filters_one_skill_and_keeps_resources(self):
        data = archive({"repo/skills/first/SKILL.md": skill("first"),
                        "repo/skills/second/SKILL.md": skill("second"),
                        "repo/skills/second/assets/a.bin": b"\xff\x00"})
        with patch.object(skill_registry, "_read", return_value=data) as read:
            preview = self.route("POST", "skill-preview", {"source": "https://skills.sh/owner/repo/second"})
        self.assertIn("codeload.github.com/owner/repo/zip/HEAD", read.call_args.args[0])
        self.assertEqual([item["name"] for item in preview["skills"]], ["second"])
        self.install(preview)
        self.assertFalse((self.home / ".agents/skills/first").exists())
        self.assertEqual((self.home / ".agents/skills/second/assets/a.bin").read_bytes(), b"\xff\x00")

    def test_repository_preview_allows_selecting_individual_skills(self):
        data = archive({"repo/skills/first/SKILL.md": skill("first"), "repo/skills/second/SKILL.md": skill("second")})
        with patch.object(skill_registry, "_read", return_value=data):
            preview = self.route("POST", "skill-preview", {"source": "owner/repo"})
        self.assertEqual(len(preview["skills"]), 2)
        self.install(preview, ["first"])
        self.assertEqual([item["name"] for item in capabilities.list_skills()], ["first"])

    def test_skillhub_namespace_download_and_skillsmp_detail_import(self):
        data = archive({"SKILL.md": skill("example")})
        with patch.object(skill_registry, "_read", return_value=data) as read:
            preview = self.route("POST", "skill-preview", {"source": "https://skillhub.cn/skills/@author/example"})
        self.assertIn("slug=%40author%2Fexample", read.call_args.args[0])
        self.assertEqual(preview["provider"], "skillhub")
        html = b'<a href="https://github.com/owner/repo/tree/main/skills/example">GitHub</a>'
        def fetch(url, *args):
            return html if "skillsmp.com" in url else archive({"repo/skills/example/SKILL.md": skill("example")})
        with patch.object(skill_registry, "_read", side_effect=fetch):
            preview = self.route("POST", "skill-preview", {"source": "https://skillsmp.com/creators/author/repo/example"})
        self.assertEqual(preview["provider"], "skillsmp")
        self.assertEqual(preview["skills"][0]["name"], "example")

    def test_github_directory_and_skill_file_links_use_selected_subtree(self):
        data = archive({"repo/custom/example/SKILL.md": skill("example"), "repo/other/SKILL.md": skill("other")})
        for link in ("https://github.com/owner/repo/tree/main/custom/example",
                     "https://github.com/owner/repo/blob/main/custom/example/SKILL.md"):
            with self.subTest(link=link), patch.object(skill_registry, "_read", return_value=data):
                preview = self.route("POST", "skill-preview", {"source": link})
                self.assertEqual([item["name"] for item in preview["skills"]], ["example"])

    def test_duplicate_install_and_existing_nested_name_never_overwrite(self):
        preview = self.route("POST", "skill-preview", {"source": str(self.local())})
        self.install(preview)
        target = self.home / ".agents/skills/example/SKILL.md"
        target.write_bytes(b"---\nname: example\n---\nLocal changes")
        with self.assertRaisesRegex(ValueError, "同名"):
            self.install(preview)
        self.assertIn(b"Local changes", target.read_bytes())
        nested = self.home / ".agents/skills/nested/another"
        nested.mkdir(parents=True)
        (nested / "SKILL.md").write_bytes(skill("third"))
        other = self.route("POST", "skill-preview", {"source": str(self.local("third"))})
        with self.assertRaisesRegex(ValueError, "同名"):
            self.install(other)

    def test_batch_install_rolls_back_files_and_records_on_database_failure(self):
        data = archive({"repo/first/SKILL.md": skill("first"), "repo/second/SKILL.md": skill("second")})
        with patch.object(skill_registry, "_read", return_value=data):
            preview = self.route("POST", "skill-preview", {"source": "owner/repo"})
        skill_registry._ensure(self.db)
        self.db.execute("CREATE TRIGGER reject_second BEFORE INSERT ON skill_installations WHEN NEW.name='second' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.install(preview)
        self.assertEqual(list(self.db.execute("SELECT * FROM skill_installations")), [])
        self.assertEqual(list((self.home / ".agents/skills").iterdir()), [])

    def test_invalid_archive_paths_links_and_duplicate_paths_are_rejected(self):
        packages = [archive({name: b"bad", "SKILL.md": skill("example")})
                    for name in ("../escape", "C:/escape", "a/../../escape", "assets\\..\\escape", "NUL.txt")]
        packages.append(archive({"SKILL.md": skill("example"), "assets/A": b"a", "assets/a": b"b"}))
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, "w") as package:
            entry = zipfile.ZipInfo("link")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            package.writestr(entry, "../outside")
        packages.append(blob.getvalue())
        for data in packages:
            with self.subTest(size=len(data)), patch.object(skill_registry, "_read", return_value=data):
                with self.assertRaises(ValueError):
                    self.route("POST", "skill-preview", {"source": "owner/repo"})
        self.assertFalse((self.root / "escape").exists())
        self.assertEqual(list((self.root / "cache/skill-imports").iterdir()), [])

    def test_changed_or_expired_preview_cannot_be_installed(self):
        preview = self.route("POST", "skill-preview", {"source": str(self.local())})
        directory = self.root / "cache/skill-imports" / preview["token"]
        (directory / "payload/SKILL.md").write_bytes(skill("changed"))
        with self.assertRaisesRegex(ValueError, "发生变化"):
            self.install(preview)
        manifest_file = directory / "manifest.json"
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        manifest["created_at"] = time.time() - 7200
        manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "过期"):
            self.install(preview)
        self.assertFalse(directory.exists())

    def test_local_tar_and_skill_file_import_keep_complete_directories(self):
        folder = self.local()
        package = self.root / "example.tar.gz"
        with tarfile.open(package, "w:gz") as handle:
            handle.add(folder, arcname=".")
        preview = self.route("POST", "skill-preview", {"source": str(package)})
        self.assertEqual(preview["skills"][0]["files"], 4)
        self.install(preview)
        self.assertEqual((self.home / ".agents/skills/example/assets/binary.bin").read_bytes(), b"\x00\xff\x80\r\n")
        direct = self.route("POST", "skill-preview", {"source": str(folder / "SKILL.md")})
        self.assertEqual(direct["skills"][0]["files"], 4)

    def test_search_adapters_use_the_builtin_sources_and_cache_queries(self):
        fixtures = {
            "skills-sh": {"skills": [{"id": "owner/repo/example", "source": "owner/repo", "skillId": "example", "installs": 42}]},
            "skillhub": {"code": 0, "data": {"skills": [{"slug": "example", "name": "Example", "description_zh": "中文说明", "namespace": {"canonicalName": "@author/example"}}]}},
            "skillsmp": {"success": True, "data": {"skills": [{"name": "example", "githubUrl": "https://github.com/owner/repo/tree/main/example", "skillUrl": "https://skillsmp.com/creators/author/repo/example"}]}},
        }
        for provider, response in fixtures.items():
            with self.subTest(provider=provider), patch.object(skill_registry, "_json", return_value=response) as fetch:
                result = self.route("GET", "skill-search", query={"provider": [provider], "q": ["example"]})
                self.assertEqual(len(result["skills"]), 1)
                self.assertTrue(result["skills"][0]["source"].startswith("https://"))
                self.route("GET", "skill-search", query={"provider": [provider], "q": ["example"]})
                self.assertEqual(fetch.call_count, 1)

    def test_website_index_keeps_binary_assets_and_checks_archive_digest(self):
        index = {"skills": [{"name": "example", "files": ["SKILL.md", "assets/binary.bin"]}]}
        def fetch(url, *args):
            if url.endswith("index.json"):
                return json.dumps(index).encode()
            return skill("example") if url.endswith("SKILL.md") else b"\xff\x00"
        with patch.object(skill_registry, "_read", side_effect=fetch), patch.object(skill_registry, "MAX_EXTRACT", len(skill("example")) + 2):
            preview = self.route("POST", "skill-preview", {"source": "https://skills.sh/site/example.com/example"})
        self.install(preview)
        self.assertEqual((self.home / ".agents/skills/example/assets/binary.bin").read_bytes(), b"\xff\x00")
        data = archive({"SKILL.md": skill("other")})
        index = {"skills": [{"name": "other", "type": "archive", "url": "/other.zip", "digest": "sha256:" + hashlib.sha256(data).hexdigest()}]}
        def download(url, *args):
            return json.dumps(index).encode() if url.endswith("index.json") else data
        with patch.object(skill_registry, "_read", side_effect=download):
            other = self.route("POST", "skill-preview", {"source": "https://example.com"})
            self.assertEqual(other["skills"][0]["name"], "other")
            index["skills"][0]["digest"] = "sha256:incorrect"
            with self.assertRaisesRegex(ValueError, "校验失败"):
                self.route("POST", "skill-preview", {"source": "https://example.com"})

    def test_website_artifacts_share_one_extraction_budget(self):
        index = {"skills": [{"name": name, "type": "skill-md", "url": f"/{name}.md"} for name in ("first", "second")]}
        def fetch(url, *args):
            if url.endswith("index.json"):
                return json.dumps(index).encode()
            return skill("first" if url.endswith("first.md") else "second")
        with patch.object(skill_registry, "_read", side_effect=fetch), patch.object(skill_registry, "MAX_EXTRACT", len(skill("first")) + len(skill("second")) - 1):
            with self.assertRaisesRegex(ValueError, "超过"):
                self.route("POST", "skill-preview", {"source": "https://example.com"})

    def test_remote_download_refuses_credentials_and_private_addresses(self):
        addresses = [(2, 1, 6, "", ("127.0.0.1", 443))]
        with patch.object(skill_registry.socket, "getaddrinfo", return_value=addresses), patch.object(skill_registry.urllib.request, "build_opener") as opener:
            for url in ("https://example.com/skills.zip", "https://user:secret@example.com/skills.zip", "http://example.com/skills.zip"):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    skill_registry._read(url)
            opener.assert_not_called()

    def test_invalid_scope_selection_and_unknown_skills_do_not_install(self):
        preview = self.route("POST", "skill-preview", {"source": str(self.local())})
        for body in ({"scope": "builtin"}, {"scope": "project"}, {"skills": []}, {"skills": ["missing"]}, {"token": "../outside"}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.route("POST", "skill-install", {"scope": "user", "token": preview["token"], "skills": ["example"], **body})
        self.assertEqual(capabilities.list_skills(), [])


if __name__ == "__main__":
    unittest.main()
