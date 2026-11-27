import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from devstudio.cli.main import main
from devstudio.workspace import tasks_dir


class SimplifyTests(unittest.TestCase):
    """简化后操作路径的回归：build 走 yaml，应用生成交给 IDE Agent。"""

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        os.environ["DEVSTUDIO_DATA"] = str(self.root / "data")

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()
        os.environ.pop("DEVSTUDIO_DATA", None)

    def run_at(self, path: Path, args: list[str]):
        os.chdir(path)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def celestia(self) -> Path:
        project = self.root / "proj"
        (project / "project" / "anypi_nova").mkdir(parents=True)
        (project / "devstudio.yaml").write_text(
            "state: sdk_celestia\nrelease_id: AnyCloud39AV200_V1.04\nproject:\n  name: anypi_nova\n"
            "build:\n  command: echo yaml-cmd-ok\n  host: local\n",
            encoding="utf-8",
        )
        (project / "project" / "anypi_nova" / "config.mk").write_text(
            "CONFIG_APP_PACKAGES_TABLET=y\n", encoding="utf-8"
        )
        return project

    def test_build_run_uses_yaml_defaults(self):
        project = self.celestia()
        code, out, err = self.run_at(project, ["build", "--run"])
        self.assertEqual(code, 0, out + err)
        self.assertIn("yaml-cmd-ok", out)
        self.assertIn("完成等级：L3", out)

    def test_app_add_yes_writes_template_and_hands_off_to_ide_agent(self):
        project = self.celestia()
        code, out, err = self.run_at(project, ["app", "add", "recorder", "--name-zh", "录音", "--yes"])
        self.assertEqual(code, 0, out + err)
        app_dir = project / "application" / "packages" / "tablet" / "recorder"
        self.assertTrue((app_dir / "app.json").is_file())
        entry = (app_dir / "app_entry_recorder.c").read_text(encoding="utf-8")
        self.assertIn("app_entry_start_recorder(lv_obj_t *main)", entry)
        config = (project / "project" / "anypi_nova" / "config.mk").read_text(encoding="utf-8")
        self.assertIn("CONFIG_APP_TABLET_RECORDER = y", config)
        self.assertIn("DevStudio 不配置、调用或托管大模型", out)
        plan = (tasks_dir(project) / "t0001" / "plan.md").read_text(encoding="utf-8")
        self.assertIn("状态：已批准", plan)
        self.assertFalse((app_dir / "tablet_recorder.c").exists())

    def test_app_add_without_yes_keeps_plan_gate(self):
        project = self.celestia()
        code, out, _ = self.run_at(project, ["app", "add", "recorder"])
        self.assertEqual(code, 0)
        self.assertIn("已创建计划 t0001", out)
        self.assertFalse((project / "application" / "packages").exists())
        plan = (tasks_dir(project) / "t0001" / "plan.md").read_text(encoding="utf-8")
        self.assertIn("状态：待批准", plan)


if __name__ == "__main__":
    unittest.main()
