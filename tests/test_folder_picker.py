"""Folder selection does not create an open temporary file on Windows."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import folder_picker


class FolderPickerTest(unittest.TestCase):
    def test_path_and_cancellation_use_stdout_without_tempfiles(self):
        for value in ("", "C:/用户/研究 空间"):
            with self.subTest(value=value), patch.object(folder_picker.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, json.dumps({"dir": value}).encode(), b"")
                self.assertEqual(folder_picker.pick_folder("C:/initial", "Pick"), {"dir": value})
                self.assertEqual(run.call_args.args[0][0], sys.executable)
                self.assertEqual(run.call_args.args[0][-2:], ["C:/initial", "Pick"])
                self.assertNotIn("tempfile", run.call_args.args[0][2])
                self.assertTrue(run.call_args.kwargs["capture_output"])

    def test_child_script_executes_and_destroys_tk_after_cancel(self):
        prefix = """import sys, types
tk = types.ModuleType('tkinter')
class Root:
    def withdraw(self): pass
    def attributes(self, *args): pass
    def destroy(self): pass
tk.Tk = Root
fd = types.ModuleType('tkinter.filedialog')
fd.askdirectory = lambda **kwargs: 'C:/研究/with spaces'
tk.filedialog = fd
sys.modules['tkinter'] = tk
sys.modules['tkinter.filedialog'] = fd
"""
        proc = subprocess.run([sys.executable, "-c", prefix + folder_picker._HELPER,
                               str(Path(folder_picker.__file__).resolve().parent), "", "Test"],
                              capture_output=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(proc.stdout), {"dir": "C:/研究/with spaces"})

    def test_child_failures_and_timeout_are_actionable(self):
        with patch.object(folder_picker.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 1, b"", b"tk unavailable")
            self.assertIn("tk unavailable", folder_picker.pick_folder()["error"])
            run.side_effect = subprocess.TimeoutExpired("python", 600)
            self.assertIn("超时", folder_picker.pick_folder()["error"])
            run.side_effect = PermissionError("access denied")
            self.assertIn("PermissionError", folder_picker.pick_folder()["error"])

    def test_invalid_child_result_is_not_silently_a_cancellation(self):
        for value in (b"bad JSON", b"null", b'{"dir":42}', b"\xff"):
            with self.subTest(value=value), patch.object(folder_picker.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, value, b"")
                self.assertIn("error", folder_picker.pick_folder())

    def test_frozen_app_uses_closed_result_file_and_removes_directory(self):
        used = []

        def run(command, **kwargs):
            self.assertEqual(command[1], "--pick-folder-helper")
            output = Path(command[2])
            self.assertFalse(output.exists())
            output.write_text(json.dumps({"dir": "C:/用户/研究"}), encoding="utf-8")
            used.append(output.parent)
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with patch.object(sys, "frozen", True, create=True), patch.object(folder_picker.subprocess, "run", side_effect=run):
            self.assertEqual(folder_picker.pick_folder(), {"dir": "C:/用户/研究"})
        self.assertTrue(used)
        self.assertFalse(used[0].exists())

    def test_frozen_helper_handles_cancellation_and_tk_failure(self):
        with tempfile.TemporaryDirectory(prefix="rw-picker-test-") as directory:
            output = Path(directory) / "result.json"
            with patch.object(folder_picker, "_select_folder", return_value=""):
                self.assertEqual(folder_picker.helper_main([str(output), "", "Pick"]), 0)
                self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"dir": ""})
            with patch.object(folder_picker, "_select_folder", side_effect=RuntimeError("Tk unavailable")):
                folder_picker.helper_main([str(output), "", "Pick"])
                self.assertIn("Tk unavailable", json.loads(output.read_text(encoding="utf-8"))["error"])
        self.assertEqual(folder_picker.helper_main([]), 2)


if __name__ == "__main__":
    unittest.main()
