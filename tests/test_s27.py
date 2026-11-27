import json
import os
import tempfile
import unittest
from pathlib import Path

from devstudio.cli.main import main
from devstudio.workspace import tasks_dir


class RuntimeGuardTests(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        os.environ["DEVSTUDIO_DATA"] = str(self.root / "data")
        self.project = self.root / "tree"
        self.project.mkdir()
        os.chdir(self.project)
        task = tasks_dir(self.project) / "t0001"
        task.mkdir(parents=True)
        (task / "task.json").write_text(json.dumps({"task_id": "t0001"}), encoding="utf-8")

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()
        os.environ.pop("DEVSTUDIO_DATA", None)

    def _run(self, args):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def test_unapproved_write_is_refused(self):
        (tasks_dir(self.project) / "t0001" / "plan.md").write_text("状态：待批准\n", encoding="utf-8")
        code, out, err = self._run(["apply", "t0001", "--name", "note.txt", "--text", "hello"])
        self.assertEqual(code, 1, err)
        self.assertIn("计划未批准", out)
        self.assertFalse((tasks_dir(self.project) / "t0001" / "artifacts" / "note.txt").exists())

    def test_numeric_fields_are_marked_and_binary_is_refused(self):
        (tasks_dir(self.project) / "t0001" / "plan.md").write_text("状态：已批准\n", encoding="utf-8")
        code, out, err = self._run([
            "apply", "t0001", "--name", "regs.txt", "--text", "SENSOR_ID 1\nVTS 2\n",
        ])
        self.assertEqual(code, 0, out + err)
        self.assertIn("标出数字字段 regs.txt:1 sensor_id", out)
        self.assertIn("标出数字字段 regs.txt:2 timing", out)
        code, _out, err = self._run(["apply", "t0001", "--name", "bad.txt", "--text", "a\0b"])
        self.assertEqual(code, 1)
        self.assertIn("不是可审阅的文本", err)
        code, _out, err = self._run(["apply", "t0001", "--name", "../escape.txt", "--text", "no"])
        self.assertEqual(code, 1)
        self.assertIn("只允许写入任务产物目录下的文件名", err)
