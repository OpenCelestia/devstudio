import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from devstudio.cli.main import main
from devstudio.workspace import data_root, devices_path, tasks_dir

HOST = "wsl" if sys.platform == "win32" else "local"


class PrototypeTests(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        os.environ["DEVSTUDIO_DATA"] = str(self.root / "data")

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()
        os.environ.pop("DEVSTUDIO_ROOT", None)
        os.environ.pop("DEVSTUDIO_DATA", None)

    def run_at(self, path: Path, args: list[str], lines: list[str] | None = None):
        os.chdir(path)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(args, lines=lines)
        return code, out.getvalue(), err.getvalue()

    def test_celestia_does_not_write_before_confirm(self):
        project = self.celestia()
        code, out, _ = self.run_at(project, ["init"], lines=[""])
        self.assertEqual(code, 0)
        self.assertIn("Suggested state: sdk_celestia", out)
        self.assertIn("Not confirmed. No files written.", out)
        self.assertFalse((project / "devstudio.yaml").exists())
        self.assertFalse((project / "AGENTS.md").exists())

    def test_celestia_yes_writes_guidance(self):
        project = self.celestia()
        (project / "AGENTS.md").write_text("前言\n\n后记\n", encoding="utf-8")
        (project / ".cursor").mkdir()
        (project / ".cursor" / "mcp.json").write_text(
            json.dumps({"mcpServers": {"other": {"command": "echo"}}}), encoding="utf-8"
        )
        code, out, err = self.run_at(project, ["init", "--yes"])
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"Host: {HOST}", out)
        self.assertIn("Wrote devstudio.yaml", out)
        text = (project / "devstudio.yaml").read_text(encoding="utf-8")
        self.assertIn("state: sdk_celestia", text)
        self.assertIn("release_id: AnyCloud39AV200_V1.04", text)
        agents = (project / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(agents.startswith("前言"))
        self.assertIn("后记", agents)
        self.assertEqual(agents.count("<!-- devstudio:begin -->"), 1)
        self.assertIn("Form/形态: TABLET", agents)
        self.assertIn("DevStudio AGENTS", agents)
        self.assertIn("project-bootstrap", agents)
        self.assertIn("cis-porting", agents)
        mcp = json.loads((project / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(mcp["mcpServers"]["other"]["command"], "echo")
        self.assertNotIn("devstudio-device", mcp["mcpServers"])
        owned = json.loads((project / "devstudio" / "ide" / "cursor.mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(owned["mcpServers"]["devstudio-device"]["args"], ["mcp", "--group", "device"])
        vscode = json.loads((project / "devstudio" / "ide" / "vscode.mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(vscode["servers"]["devstudio-device"]["type"], "stdio")
        claude_mcp = json.loads((project / "devstudio" / "ide" / "claude.mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(
            claude_mcp["mcpServers"]["devstudio-knowledge"]["args"], ["mcp", "--group", "knowledge"]
        )
        self.assertIn("cis-porting", agents)
        self.assertIn("devstudio/ide/cursor.mcp.json", agents)
        self.assertFalse((project / ".mcp.json").exists())
        self.assertFalse((project / ".claude" / "skills" / "cis-porting").exists())
        self.assertIn("@AGENTS.md", (project / "CLAUDE.md").read_text(encoding="utf-8"))
        code, _, _ = self.run_at(project, ["init", "--yes"])
        agents = (project / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(agents.count("<!-- devstudio:begin -->"), 1)

    def test_celestia_multiple_products_require_selection_and_bind_sdk(self):
        project = self.celestia()
        sdk = project / "system" / "AnyCloud39AV100_V2" / "V2.05"
        for name in ("os", "platform", "tools"):
            (sdk / name).mkdir(parents=True)
        luna = project / "project" / "anypi_luna"
        luna.mkdir()
        (luna / "config.mk").write_text(
            "CONFIG_SOC_PLATFORM=AnyCloud39AV100_V2\n"
            "CONFIG_SOC_SDK_VERSION=V2.05\n"
            "CONFIG_APP_PACKAGES_WEARABLE=y\n",
            encoding="utf-8",
        )
        code, out, _ = self.run_at(project, ["init", "--yes"], lines=[])
        self.assertEqual(code, 1)
        self.assertIn("Multiple product projects found", out)
        self.assertIn("No valid product selected", out)
        self.assertFalse((project / "devstudio.yaml").exists())

        code, out, err = self.run_at(
            project, ["init", "--yes", "--product", "anypi_luna"]
        )
        self.assertEqual(code, 0, out + err)
        self.assertIn("project/anypi_luna", out)
        self.assertIn("AnyCloud39AV100_V2_V2.05", out)
        text = (project / "devstudio.yaml").read_text(encoding="utf-8")
        self.assertIn("name: anypi_luna", text)
        self.assertIn("release_id: AnyCloud39AV100_V2_V2.05", text)
        self.assertNotIn("AnyCloud39AV200_V1.04", text)

        code, out, err = self.run_at(project, ["switch", "list"])
        self.assertEqual(code, 0, out + err)
        self.assertIn("anypi_luna", out)
        self.assertIn("anypi_nova", out)
        self.assertIn("*current", out)

        code, out, err = self.run_at(
            project, ["switch", "product", "anypi_nova"], lines=["y"]
        )
        self.assertEqual(code, 0, out + err)
        text = (project / "devstudio.yaml").read_text(encoding="utf-8")
        self.assertIn("name: anypi_nova", text)
        self.assertIn("release_id: AnyCloud39AV200_V1.04", text)

        code, _out, err = self.run_at(project, ["switch", "sdk", str(sdk)])
        self.assertEqual(code, 1)
        self.assertIn("cannot switch the SDK independently", err)

    def test_customer_app_questions_then_write(self):
        sdk = self.sdk_tree("AnyCloud39AV100_V2", "V2.05")
        app = self.root / "my_app"
        app.mkdir()
        lines = [
            str(app),
            str(app / "include" / "hal"),
            str(app / "hal"),
            "make -C my_app",
            "ssh",
            "build01",
            str(app),
        ]
        code, out, _ = self.run_at(app, ["init", "--sdk", str(sdk)], lines=list(lines))
        self.assertEqual(code, 0)
        self.assertIn("Not confirmed. No files written.", out)
        self.assertFalse((app / "devstudio.yaml").exists())
        code, out, err = self.run_at(app, ["init", "--yes", "--sdk", str(sdk)], lines=list(lines))
        self.assertEqual(code, 0, out + err)
        self.assertIn("Suggested state: sdk_customer_app", out)
        self.assertIn("hal-adapt", out)
        self.assertNotIn("devstudio-device", out)
        text = (app / "devstudio.yaml").read_text(encoding="utf-8")
        self.assertIn("ssh_host: build01", text)
        mcp = json.loads((app / "devstudio" / "ide" / "cursor.mcp.json").read_text(encoding="utf-8"))
        self.assertNotIn("devstudio-device", mcp["mcpServers"])
        self.assertIn("devstudio-source", mcp["mcpServers"])

    def test_sdk_only_mixed_and_unregistered(self):
        sdk = self.sdk_tree("Sky39AV130", "V1.01", makefile=True)
        code, out, err = self.run_at(sdk, ["init", "--yes"])
        self.assertEqual(code, 0, out + err)
        self.assertIn("Suggested state: sdk_only", out)
        self.assertIn(f"Writable: data dir {data_root(sdk) / 'tasks'}", out)
        self.assertFalse((sdk / ".devstudio").exists())
        self.assertIn("Build: make", out)
        self.assertNotIn("Chip SDK     .", out)
        self.assertIn(str(sdk.resolve()), out)
        text = (sdk / "devstudio.yaml").read_text(encoding="utf-8")
        self.assertIn("model: workspace", text)
        mcp = json.loads((sdk / "devstudio" / "ide" / "cursor.mcp.json").read_text(encoding="utf-8"))
        self.assertNotIn("devstudio-source", mcp["mcpServers"])
        self.assertNotIn("devstudio-device", mcp["mcpServers"])

        mixed = self.root / "mixed"
        (mixed / "application").mkdir(parents=True)
        (mixed / "hardware").mkdir()
        (mixed / "system" / "AnyCloud39AV200" / "V1.04").mkdir(parents=True)
        code, out, _ = self.run_at(mixed, ["init"], lines=["3", "n"])
        self.assertEqual(code, 0)
        self.assertIn("Cannot determine state", out)
        self.assertIn("Not confirmed. No files written.", out)
        self.assertFalse((mixed / "devstudio.yaml").exists())

        unknown = self.root / "unknown"
        (unknown / "system" / "AnyCloud39AV200" / "V1.05").mkdir(parents=True)
        code, out, _ = self.run_at(unknown, ["init", "--yes"])
        self.assertEqual(code, 1)
        self.assertIn("Stopped. Install the Adapter for AnyCloud39AV200_V1.05 and retry. No files written.", out)
        self.assertFalse((unknown / "devstudio.yaml").exists())
        self.assertFalse((unknown / "AGENTS.md").exists())

    def test_peripherals_and_plan(self):
        project = self.root / "know"
        project.mkdir()
        (project / "devstudio.yaml").write_text(
            "state: sdk_celestia\nrelease_id: AnyCloud39AV100_V2_V2.05\nbuild:\n  command: make\n  host: local\n",
            encoding="utf-8",
        )
        code, out, err = self.run_at(
            project,
            ["knowledge", "peripherals", "--type", "sensor", "--interface", "MIPI 2lane"],
        )
        self.assertEqual(code, 0, err)
        self.assertIn("平台 AnyCloud39AV100_V2   版本 V2.05", out)
        self.assertIn("k347p", out)
        self.assertIn("调试包", out)
        self.assertIn("SDK 内置", out)
        self.assertIn("共 23 条", out)
        code, out, _ = self.run_at(project, ["plan", "sensor", "--model", "gc4023"])
        self.assertEqual(code, 0)
        self.assertIn("在", out)
        self.assertIn("支持列表", out)
        self.assertFalse((project / ".devstudio").exists())

        av200 = self.root / "av200"
        av200.mkdir()
        (av200 / "devstudio.yaml").write_text(
            "state: sdk_celestia\nrelease_id: AnyCloud39AV200_V1.04\n"
            "build:\n  command: ./build.sh --product-name anypi_nova\n  host: wsl\n",
            encoding="utf-8",
        )
        code, out, _ = self.run_at(av200, ["plan", "sensor", "--model", "ZZ9999"])
        self.assertEqual(code, 1)
        self.assertIn("将生成方案包（对齐 FAE AI）。", out)
        self.assertIn("NEED_INPUT ASK_UI=cis_intake", out)
        self.assertIn("缺：", out)
        self.assertIn("MIPI Lane 数", out)
        self.assertFalse((av200 / ".devstudio").exists())
        code, out, err = self.run_at(
            av200,
            [
                "plan", "sensor", "--model", "ZZ9999",
                "--resolution", "1920x1080", "--fps", "30fps", "--interface", "MIPI 2lane",
                "--lanes", "2", "--mclk", "24MHz", "--i2c", "0x37", "--sequence", "seq.ini",
            ],
        )
        self.assertEqual(code, 0, out + err)
        plan_path = tasks_dir(av200) / "t0001" / "plan.md"
        plan = plan_path.read_text(encoding="utf-8")
        self.assertFalse((av200 / ".devstudio").exists())
        self.assertFalse(str(plan_path.resolve()).startswith(str(av200.resolve())))
        self.assertIn("状态：待批准", plan)
        self.assertIn("MIPI 控制器 满足", plan)
        self.assertIn("Reset GPIO 待确认", plan)
        self.assertIn("勘误 未收录", plan)
        self.assertEqual(list(av200.rglob("*.c")), [])

    def test_app_add_tablet_refuses_system_app(self):
        project = self.celestia()
        self.run_at(project, ["init", "--yes"])
        code, out, err = self.run_at(project, ["app", "add", "recorder"])
        self.assertEqual(code, 0, err)
        self.assertIn("形态：TABLET（CONFIG_APP_PACKAGES_TABLET=y）", out)
        self.assertIn("目录：application/packages/tablet/recorder/", out)
        self.assertIn("入口：app_entry_start_recorder　开关：CONFIG_APP_TABLET_RECORDER", out)
        self.assertIn("界面函数：tablet_recorder_create", out)
        self.assertIn("已创建计划 t0001", out)
        self.assertFalse((project / "application" / "packages").exists())
        self.assertTrue((tasks_dir(project) / "t0001" / "plan.md").is_file())
        self.assertFalse((project / ".devstudio").exists())
        code, _, err = self.run_at(project, ["app", "add", "camera"])
        self.assertEqual(code, 1)
        self.assertIn("系统应用", err)

    def test_build_classify_three_ways(self):
        source = self.root / "source.log"
        source.write_text("tablet_recorder.c:88  error: 'storage_open' undeclared\n", encoding="utf-8")
        code, out, _ = self.run_at(
            self.root,
            ["build", "--log", str(source), "--exit", "1", "--module", "APP_TABLET_RECORDER", "--host", "ssh build01", "--round", "1"],
        )
        self.assertEqual(code, 1)
        self.assertIn("devstudio-build.build_module  APP_TABLET_RECORDER  位置：ssh build01", out)
        self.assertIn("分类：源码错误   第 1/3 轮", out)
        self.assertIn("回喂给 Agent", out)

        env = self.root / "env.log"
        env.write_text("WSL 中找不到 ~/celestia/develop\n", encoding="utf-8")
        code, out, _ = self.run_at(
            self.root, ["build", "--log", str(env), "--exit", "2", "--host", "wsl", "--round", "1"]
        )
        self.assertEqual(code, 2)
        self.assertIn("devstudio-build.build  位置：wsl", out)
        self.assertIn("分类：环境错误   重试 1/2", out)

        make = self.root / "make.log"
        make.write_text("Makefile:12: *** missing separator.  Stop.\n", encoding="utf-8")
        _, out, _ = self.run_at(self.root, ["build", "--log", str(make), "--exit", "2"])
        self.assertIn("分类：构建系统错误", out)

        ok = self.root / "ok.log"
        ok.write_text("build finished\n", encoding="utf-8")
        code, out, _ = self.run_at(
            self.root,
            ["build", "--log", str(ok), "--exit", "0", "--module", "APP_TABLET_RECORDER", "--artifact", "out/anypi_nova/librecorder_app.so  sha256 3f9a"],
        )
        self.assertEqual(code, 0)
        self.assertIn("分类：通过", out)
        self.assertIn("完成等级：L3", out)

    def test_device_refuses_multiple_and_pushes_one(self):
        project = self.root / "board"
        project.mkdir()
        task = tasks_dir(project) / "t0001"
        task.mkdir(parents=True)
        devices = [
            {"board": "192.168.1.20", "product": "anypi_nova", "usr_size": "9.8MB", "app_mtd": "/dev/mtd5",
             "metrics": {"uptime": "3h12m", "cpu": "37%", "mem": "61%", "load": "1.20 0.98 0.85", "temp": "52°C", "rx": "1.2MB", "tx": "8.4MB", "top": "nova_main 22%"}},
            {"board": "192.168.1.21", "product": "anypi_nova"},
        ]
        devices_path(project).write_text(json.dumps(devices), encoding="utf-8")
        code, out, _ = self.run_at(project, ["device", "push", "recorder"])
        self.assertEqual(code, 1)
        self.assertIn("NEED_CHOICE ASK_UI=choice", out)
        self.assertIn("DevStudio 板号", out)
        self.assertIn("id=192.168.1.20", out)
        self.assertIn("id=192.168.1.21", out)
        self.assertFalse((task / "device.log").exists())

        devices.pop()
        devices_path(project).write_text(json.dumps(devices), encoding="utf-8")
        code, out, _ = self.run_at(project, ["device", "push", "recorder"])
        self.assertEqual(code, 1)
        self.assertIn("NEED_INPUT ASK_UI=text", out)
        self.assertIn("DEFAULT: 192.168.1.20", out)
        self.assertFalse((task / "device.log").exists())

        code, out, err = self.run_at(
            project, ["device", "push", "recorder", "--board", "192.168.1.20"]
        )
        self.assertEqual(code, 1, err)
        self.assertIn("放置：/mnt/install/apps/recorder　备份：/mnt/install/.app_bak/recorder", out)
        self.assertIn("未连接 192.168.1.20:7777。未上传。", out)
        self.assertIn("device.log", out)
        self.assertTrue((task / "device.log").is_file())
        code, out, _ = self.run_at(
            project, ["device", "push", "setting", "--board", "192.168.1.20"]
        )
        self.assertEqual(code, 1)
        self.assertIn("usr.sqsh4（9.8MB），写 APP 分区 /dev/mtd5", out)
        self.assertIn("未上传。", out)
        code, out, _ = self.run_at(project, ["device", "metrics", "--board", "192.168.1.20"])
        self.assertEqual(code, 0)
        self.assertIn("uptime 3h12m", out)
        self.assertIn("temp 52°C", out)
        self.assertFalse((project / ".devstudio").exists())

    def test_report_export(self):
        project = self.root / "report"
        project.mkdir()
        task = tasks_dir(project) / "t0012"
        task.mkdir(parents=True)
        payload = {
            "task_id": "t0012",
            "release_id": "AnyCloud39AV200_V1.04",
            "state": "sdk_celestia",
            "level": "L3",
            "files": [{"path": "hardware/camera/hal/AnyCloud39AV200/gc5603.c", "sha256": "abc"}],
            "numeric_fields": {"by_rule": 12, "needs_review": 0},
            "build": {"host": "wsl", "command": "./build.sh --product-name anypi_nova", "exit": 0, "artifacts": []},
            "unverified": ["i2c_id", "preview"],
        }
        (task / "task.json").write_text(json.dumps(payload), encoding="utf-8")
        code, out, err = self.run_at(project, ["report", "export", "t0012"])
        self.assertEqual(code, 0, err)
        self.assertIn("report.md", out)
        report = (task / "report.md").read_text(encoding="utf-8")
        self.assertIn("完成等级：L3 编译通过（未实机验证）", report)
        self.assertIn("芯片 / 版本：AnyCloud39AV200 V1.04　状态：sdk_celestia", report)
        self.assertIn("数字字段：由规则写入 12 项；待复核 0 项", report)
        self.assertIn("未验证项：I2C 读 ID、出图", report)
        data = json.loads((task / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(data["level"], "L3")
        self.assertEqual(data["unverified"], ["i2c_id", "preview"])
        code, _, err = self.run_at(project, ["report", "export", "t9999"])
        self.assertEqual(code, 1)
        self.assertIn("没有任务", err)
        self.assertFalse((project / ".devstudio").exists())
        self.assertFalse(list(project.rglob("report.md")))

    def celestia(self) -> Path:
        project = self.root / "celestia"
        for name in ("application", "framework", "hardware", "system/AnyCloud39AV200/V1.04", "project/anypi_nova"):
            (project / name).mkdir(parents=True)
        (project / "build.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (project / "project" / "anypi_nova" / "config.mk").write_text(
            "CONFIG_SOC_PLATFORM=AnyCloud39AV200\n"
            "CONFIG_SOC_SDK_VERSION=V1.04\n"
            "CONFIG_APP_PACKAGES_TABLET=y\n",
            encoding="utf-8",
        )
        return project

    def sdk_tree(self, family: str, version: str, makefile: bool = False) -> Path:
        base = self.root / family / version
        for name in ("os", "platform", "tools"):
            (base / name).mkdir(parents=True)
        if makefile:
            (base / "Makefile").write_text("all:\n\t@echo ok\n", encoding="utf-8")
        return base


if __name__ == "__main__":
    unittest.main()
