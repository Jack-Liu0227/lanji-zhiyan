from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import venv


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "tools" / "start.bat"


@unittest.skipUnless(sys.platform == "win32", "Windows batch launcher")
class LauncherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="rw launch \u79d1\u7814 ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "tools").mkdir()
        self.launcher = self.root / "tools" / LAUNCHER.name
        shutil.copy2(LAUNCHER, self.launcher)
        desktop = self.root / "apps" / "desktop"
        desktop.mkdir(parents=True)
        self.server = desktop / "server.py"
        self.server.write_text(
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "if __name__ == '__main__':\n"
            "    Path(os.environ['RW_LAUNCH_RESULT']).write_text(\n"
            "        json.dumps({'executable': sys.executable, 'argv': sys.argv}),\n"
            "        encoding='utf-8')\n",
            encoding="utf-8",
        )
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.result = self.root / "result.json"
        self.py_calls = self.root / "py-calls.txt"
        self.env = os.environ.copy()
        self.env.update(
            LOCALAPPDATA=str(self.root / "local"),
            PATH=os.pathsep.join((str(self.bin), str(Path(os.environ["SystemRoot"]) / "System32"))),
            RW_LAUNCH_RESULT=str(self.result),
        )
        self.env.pop("PYTHONHOME", None)
        self.env.pop("PYTHONPATH", None)

    def mock_py(self, available: bool = True) -> None:
        self.bin.joinpath("py.cmd").write_text(
            '@echo off\n> "%s" echo %%*\n%s\n'
            % (self.py_calls, "echo " + sys.executable if available else "exit /b 1"),
            encoding="utf-8",
        )

    def run_launcher(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            f'"{os.environ["COMSPEC"]}" /d /c call "{self.launcher}" --port 18765 --no-open',
            cwd=self.root,
            env=self.env,
            input="\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

    def launched_result(self) -> dict:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.result.exists():
                try:
                    return json.loads(self.result.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    pass
            time.sleep(0.05)
        self.fail("The batch launcher did not start the application")

    def test_registered_default_without_python310(self) -> None:
        self.mock_py()
        completed = self.run_launcher()
        self.assertEqual(completed.returncode, 0, completed.stdout)
        result = self.launched_result()
        self.assertEqual(Path(result["executable"]).parent, Path(sys.executable).parent)
        self.assertEqual(result["argv"][1:], ["--port", "18765", "--no-open"])
        self.assertIn("-3 -c", self.py_calls.read_text(encoding="utf-8"))
        self.assertNotIn("-3.10", self.py_calls.read_text(encoding="utf-8"))
        self.assertIn("Python:", (self.root / "data" / "startup.log").read_text(encoding="utf-8"))

    def test_project_venv_takes_precedence(self) -> None:
        self.mock_py()
        environment = self.root / ".venv"
        venv.EnvBuilder(with_pip=False).create(environment)
        completed = self.run_launcher()
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertEqual(Path(self.launched_result()["executable"]).parent, environment / "Scripts")
        self.assertFalse(self.py_calls.exists(), "A project venv must bypass the global launcher")

    def test_path_python_when_launcher_has_no_runtime(self) -> None:
        self.mock_py(available=False)
        self.env["PATH"] += os.pathsep + str(Path(sys.executable).parent)
        completed = self.run_launcher()
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertEqual(Path(self.launched_result()["executable"]).parent, Path(sys.executable).parent)

    def test_missing_runtime_is_visible(self) -> None:
        self.mock_py(available=False)
        completed = self.run_launcher()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Python 3.10 or newer was not found", completed.stdout)
        self.assertFalse(self.result.exists())

    def test_import_failure_is_visible_and_logged(self) -> None:
        self.mock_py()
        self.server.write_text("raise RuntimeError('startup import failed')\n", encoding="utf-8")
        completed = self.run_launcher()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("startup import failed", completed.stdout)
        self.assertIn(
            "RuntimeError: startup import failed",
            (self.root / "data" / "startup.log").read_text(encoding="utf-8"),
        )
        self.assertFalse(self.result.exists())


if __name__ == "__main__":
    unittest.main()
