from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import file_location


class FileLocationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="rw-location-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.project = self.root / "项目 with spaces"
        self.project.mkdir()
        self.file = self.project / "实验笔记.md"
        self.file.write_text("evidence", encoding="utf-8")
        self.db = Mock()
        self.db.base_dir.return_value = self.project
        self.open = patch("file_location.paths.host_open", return_value=True).start()
        self.addCleanup(patch.stopall)

    def test_file_location_opens_only_the_containing_directory(self):
        result = file_location.locate(self.db, {"project_id":"p1", "relative":self.file.name, "open":True})
        self.open.assert_called_once_with(self.project)
        self.assertEqual(result["path"], str(self.file))

    def test_copy_path_does_not_open_and_folder_opens_itself(self):
        self.assertEqual(file_location.locate(self.db, {"project_id":"p1", "relative":self.file.name})["path"], str(self.file))
        self.open.assert_not_called()
        file_location.locate(self.db, {"project_id":"p1", "open":True})
        self.open.assert_called_once_with(self.project)

    def test_missing_and_outside_project_paths_are_rejected(self):
        for relative in ("../escape.md", str(self.file), "missing.md"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                file_location.locate(self.db, {"project_id":"p1", "relative":relative, "open":True})
        self.open.assert_not_called()

    def test_absolute_paths_use_the_existing_allowed_roots(self):
        with patch("paths.allowed_roots", return_value=[self.project]):
            result = file_location.locate(self.db, {"path":str(self.file)})
            self.assertEqual(result["directory"], str(self.project))
            with self.assertRaises(ValueError):
                file_location.locate(self.db, {"path":str(self.root)})

    def test_host_failure_is_reported_instead_of_claiming_success(self):
        self.open.return_value = False
        with self.assertRaisesRegex(ValueError, "无法打开"):
            file_location.locate(self.db, {"project_id":"p1", "open":True})
