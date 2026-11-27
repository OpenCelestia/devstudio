import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from devstudio.cli.main import main
from devstudio.mcp.server import handle, write_message, read_message
from devstudio.workspace import tasks_dir


class DesignCommandTests(unittest.TestCase):
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
        from contextlib import redirect_stderr
        with redirect_stdout(out), redirect_stderr(err):
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def test_approve_gates_apply_and_app_template(self):
        project = self.root / "app"
        project.mkdir()
        (project / "devstudio.yaml").write_text(
            "state: sdk_celestia\nrelease_id: AnyCloud39AV200_V1.04\nproject:\n  name: anypi_nova\n",
            encoding="utf-8",
        )
        (project / "project" / "anypi_nova").mkdir(parents=True)
        (project / "project" / "anypi_nova" / "config.mk").write_text(
            "CONFIG_APP_PACKAGES_TABLET=y\n", encoding="utf-8"
        )
        code, out, err = self.run_at(project, ["app", "add", "recorder"])
        self.assertEqual(code, 0, err)
        code, out, _ = self.run_at(project, ["app", "apply", "t0001"])
        self.assertEqual(code, 1)
        self.assertIn("计划未批准", out)
        self.assertFalse((project / "application").exists())
        code, _, err = self.run_at(project, ["plan", "approve", "t0001"])
        self.assertEqual(code, 0, err)
        code, out, err = self.run_at(project, ["app", "apply", "t0001"])
        self.assertEqual(code, 0, out + err)
        app_dir = project / "application" / "packages" / "tablet" / "recorder"
        self.assertTrue((app_dir / "app.json").is_file())
        entry = (app_dir / "app_entry_recorder.c").read_text(encoding="utf-8")
        self.assertIn("app_entry_start_recorder(lv_obj_t *main)", entry)
        self.assertIn("CONFIG_APP_TABLET_RECORDER = y", (project / "project" / "anypi_nova" / "config.mk").read_text(encoding="utf-8"))
        self.assertFalse((app_dir / "tablet_recorder.c").exists())
        (app_dir / "tablet_recorder.c").write_text("void x(void) { lv_canvas_create(main); }\n", encoding="utf-8")
        code, out, _ = self.run_at(project, ["app", "check", "recorder"])
        self.assertEqual(code, 1)
        self.assertIn("lv_canvas_create", out)
        code, _, err = self.run_at(project, ["app", "remove", "recorder"])
        self.assertEqual(code, 0, err)
        code, _, err = self.run_at(project, ["plan", "approve", "t0002"])
        self.assertEqual(code, 0, err)
        code, out, err = self.run_at(project, ["app", "apply", "t0002"])
        self.assertEqual(code, 0, out + err)
        self.assertIn("设备未连接", out)
        self.assertFalse(app_dir.exists())
        self.assertNotIn("CONFIG_APP_TABLET_RECORDER", (project / "project" / "anypi_nova" / "config.mk").read_text(encoding="utf-8"))
        code, out, _ = self.run_at(project, ["apply", "t0001", "--name", "note.txt", "--text", "hello"])
        self.assertEqual(code, 0, out)
        note = tasks_dir(project) / "t0001" / "artifacts" / "note.txt"
        self.assertEqual(note.read_text(encoding="utf-8"), "hello")
        self.assertFalse((project / ".devstudio").exists())

    def test_lists_and_missing_knowledge(self):
        project = self.root / "know"
        project.mkdir()
        (project / "devstudio.yaml").write_text(
            "state: sdk_celestia\nrelease_id: AnyCloud39AV200_V1.04\n", encoding="utf-8"
        )
        code, out, err = self.run_at(project, ["knowledge", "peripherals", "--type", "sensor"])
        self.assertEqual(code, 0, err)
        self.assertIn("共 23 条", out)
        code, out, _ = self.run_at(project, ["driver", "locate", "--model", "gc5603"])
        self.assertEqual(code, 0, out)
        self.assertIn("gc5603", out.lower())
        code, out, err = self.run_at(project, ["sensor", "confirm", "--model", "gc5603"])
        self.assertEqual(code, 0, err)
        self.assertIn("参数与支持列表一致", out)
        code, out, _ = self.run_at(project, ["sensor", "confirm", "--model", "gc5603", "--cis", "不存在"])
        self.assertEqual(code, 1)
        self.assertIn("不在", out)
        code, out, _ = self.run_at(project, ["sensor", "build", "--model", "gc5603"])
        self.assertEqual(code, 1)
        self.assertIn("未出包", out)
        code, out, _ = self.run_at(project, ["knowledge", "spec", "--model", "gc5603"])
        self.assertIn("未收录", out)
        code, out, _ = self.run_at(project, ["knowledge", "errata", "--model", "gc5603"])
        self.assertIn("未收录", out)
        code, out, _ = self.run_at(project, ["project", "info"])
        self.assertEqual(code, 0)
        self.assertIn("AnyCloud39AV200_V1.04", out)

    def test_sdk_builtin_pack_includes_matching_conf(self):
        project = self.root / "sensor"
        project.mkdir()
        sdk = project / "sdk"
        driver = sdk / "driver_src" / "sensor" / "gc5603"
        driver.mkdir(parents=True)
        (driver / "gc5603.c").write_text("int gc5603_init(void) { return 0; }\n", encoding="utf-8")
        single = sdk / "platform" / "isp" / "isp_gc5603_mipi_2lane_av200.conf"
        dual = sdk / "platform" / "isp" / "isp_gc5603_mipi_2lane_av200_dual.conf"
        single.parent.mkdir(parents=True)
        single.write_text("single-conf\n", encoding="utf-8")
        dual.write_text("dual-conf\n", encoding="utf-8")
        (project / "devstudio.yaml").write_text(
            "state: sdk_celestia\nrelease_id: AnyCloud39AV200_V1.04\nsdk:\n  root: sdk\n",
            encoding="utf-8",
        )
        code, out, err = self.run_at(project, ["sensor", "build", "--model", "gc5603", "--cis", "1（单目）"])
        self.assertEqual(code, 0, out + err)
        self.assertIn("isp_gc5603_mipi_2lane_av200.conf", out)
        self.assertIn("未写入当前工程目录", out)
        packed = next((self.root / "data").rglob("gc5603_single.tar.gz"))
        with tarfile.open(packed, "r:gz") as tar:
            names = tar.getnames()
        self.assertTrue(any(name.endswith("gc5603.c") for name in names))
        self.assertTrue(any(name.endswith("isp_gc5603_mipi_2lane_av200.conf") for name in names))
        self.assertFalse(any(name.endswith("_dual.conf") for name in names))
        self.assertFalse((project / "isp_gc5603_mipi_2lane_av200.conf").exists())

        code, out, err = self.run_at(project, ["sensor", "build", "--model", "gc5603", "--cis", "2（双目，主从）"])
        self.assertEqual(code, 0, out + err)
        packed = next((self.root / "data").rglob("gc5603_dual.tar.gz"))
        with tarfile.open(packed, "r:gz") as tar:
            names = tar.getnames()
        self.assertTrue(any(name.endswith("isp_gc5603_mipi_2lane_av200_dual.conf") for name in names))

        single.unlink()
        code, out, err = self.run_at(project, ["sensor", "build", "--model", "gc5603"])
        self.assertEqual(code, 0, out + err)
        self.assertIn("未在已解出的 SDK 中找到", out)
        self.assertIn("未编造文件", out)

    def test_api_lookup_stays_inside_release_index(self):
        project = self.root / "index"
        project.mkdir()
        (project / "devstudio.yaml").write_text(
            "state: sdk_celestia\nrelease_id: AnyCloud39AV200_V1.04\n",
            encoding="utf-8",
        )
        code, out, err = self.run_at(project, ["knowledge", "api", "--symbol", "isp_gc5603_mipi_2lane_av200.conf"])
        self.assertEqual(code, 0, err)
        self.assertIn("isp_gc5603.md:1", out)
        (project / "devstudio.yaml").write_text(
            "state: sdk_celestia\nrelease_id: Sky39AV130_V1.01\n",
            encoding="utf-8",
        )
        code, out, _ = self.run_at(project, ["knowledge", "api", "--symbol", "isp_gc5603_mipi_2lane_av200.conf"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "未收录")

    def test_local_build_and_mcp_group(self):
        project = self.root / "build"
        project.mkdir()
        code, out, err = self.run_at(
            project,
            ["build", "--run", "--host", "local", "--command", f"\"{sys.executable}\" -c \"print('build-ok')\""],
        )
        self.assertEqual(code, 0, out + err)
        self.assertIn("build-ok", out)
        self.assertIn("完成等级：L3", out)
        (project / "devstudio.yaml").write_text(
            "state: sdk_only\nrelease_id: Sky39AV130_V1.01\n"
            "build:\n  command: make\n  host: local\n",
            encoding="utf-8",
        )
        response = handle("project", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(response["result"]["serverInfo"]["name"], "devstudio-project")
        listed = handle("project", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [item["name"] for item in listed["result"]["tools"]]
        self.assertIn("project_info", names)
        self.assertNotIn("push_app", names)
        called = handle(
            "project",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "project_info", "arguments": {}},
            },
        )
        result = called["result"]["structuredContent"]
        self.assertEqual(result["status"], "ok")
        self.assertIn("Sky39AV130_V1.01", result["stdout"])
        self.assertNotIn("具体参数由 devstudio 命令执行", called["result"]["content"][0]["text"])
        from devstudio.exec.host import wsl_cwd
        self.assertEqual(wsl_cwd(Path("D:/work/anypi")), ([], "/mnt/d/work/anypi"))
        self.assertEqual(
            wsl_cwd(Path("\\\\wsl$\\Ubuntu\\home\\me\\celestia")),
            (["-d", "Ubuntu"], "/home/me/celestia"),
        )
        self.assertIn("网络路径", wsl_cwd(Path("\\\\server\\share\\anypi")))
        buf = io.BytesIO()
        write_message(buf, {"jsonrpc": "2.0", "id": 3, "method": "ping"})
        buf.seek(0)
        self.assertEqual(read_message(buf)["id"], 3)


if __name__ == "__main__":
    unittest.main()
