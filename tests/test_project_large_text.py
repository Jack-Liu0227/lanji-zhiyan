"""Project notes and resources preserve large text through writes, reads and indexing."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import asset_service
import project_notes
import project_resources
import workflow


class ProjectLargeTextTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.db = workflow.WorkflowDB(Path(self.temp.name) / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.project_root = Path(self.temp.name) / "project"
        self.project = self.db.create_task("Large text", base_dir=str(self.project_root))
        self.revision_root = Path(self.temp.name) / "revisions"
        self.revision_root.mkdir()
        import paths
        self.paths_patch = patch.object(paths, "DATA", self.revision_root)
        self.paths_patch.start()
        self.addCleanup(self.paths_patch.stop)
        self.tail_note = "LARGE_NOTE_TAIL_MARKER_XQZ91"
        self.tail_resource = "LARGE_RESOURCE_TAIL_MARKER_RVT82"
        self.large = "begin " + ("large-body-text " * ((5 * 1024 * 1024 // 16) + 8))
        self.assertGreater(len(self.large.encode("utf-8")), 5 * 1024 * 1024)

    def test_large_note_and_resource_roundtrip_and_index_keep_tail_and_metadata(self):
        note_content = self.large + "\n" + self.tail_note
        note_saved = project_notes.write(
            self.db, self.project, "笔记/研究规划/large.md", note_content,
            title="Large note", create=True, metadata={"tags": ["large-text"]})
        note = project_notes.read(self.db, self.project, note_saved["path"])
        self.assertTrue(note["content"].endswith(self.tail_note))
        self.assertEqual(note["content_hash"], note_saved["content_hash"])
        self.assertIn("large-text", note["tags"])

        resource_content = self.large + "\n" + self.tail_resource
        resource_saved = project_resources.write(self.db, self.project, {
            "kind": "manuscript", "title": "Large manuscript", "content": resource_content})
        resource = project_resources.read(self.db, self.project, resource_saved["asset_id"])
        self.assertTrue(resource["content"].endswith(self.tail_resource))
        self.assertEqual(resource["content_hash"], resource_saved["content_hash"])

        result = project_resources.index(self.db, self.project)
        self.assertGreaterEqual(result["indexed"], 2)
        note_hits = asset_service.search(self.db, self.tail_note, project_id=self.project)
        resource_hits = asset_service.search(self.db, self.tail_resource, project_id=self.project)
        self.assertTrue(any(item["id"] == note_saved["asset_id"] for item in note_hits))
        self.assertTrue(any(item["id"] == resource_saved["asset_id"] for item in resource_hits))

        dashboard = project_notes.dashboard(self.db, self.project, query=self.tail_note)
        item = next(item for item in dashboard["items"] if item["path"] == note_saved["path"])
        self.assertEqual(item["tags"], ["large-text"])
        self.assertEqual(item["category_id"], "planning")

        with self.assertRaisesRegex(ValueError, "重新打开"):
            project_notes.write(self.db, self.project, note_saved["path"], note_content,
                                expected_hash="stale-version")


if __name__ == "__main__":
    unittest.main()
