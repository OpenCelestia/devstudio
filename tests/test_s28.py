import json
import os
import re
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path

from devstudio.cli.main import main
from devstudio.tools.knowledge.index_headers import index_headers
from devstudio.tools.knowledge.search import read_errata
from devstudio.tools.registry import invoke
from devstudio.workspace import tasks_dir


class _MtdAgent(threading.Thread):
    def __init__(self, mtd: str) -> None:
        super().__init__(daemon=True)
        self.mtd = mtd
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.puts: list[tuple[str, bytes]] = []
        self.execs: list[str] = []

    def run(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                self._serve(conn)

    def _serve(self, conn: socket.socket) -> None:
        conn.settimeout(5)
        while True:
            try:
                header = _exact(conn, 8)
                kind, size = struct.unpack("<II", header)
                body = _exact(conn, size) if size else b""
            except (OSError, ConnectionError):
                return
            if kind == 0x0B:
                length, data_size = struct.unpack("<II", body[:8])
                self.puts.append((body[8:8 + length].decode("utf-8"), body[8 + length:8 + length + data_size]))
                reply = {"ok": True}
            else:
                command = json.loads(body.decode("utf-8")).get("cmd", "")
                self.execs.append(command)
                if command == "cat /proc/mtd":
                    out = self.mtd
                else:
                    names = re.findall(r"echo OK (\w+)", command)
                    out = f"OK {names[0]}\n" if names else ""
                reply = {"ok": True, "out": out}
            payload = json.dumps(reply).encode("utf-8")
            conn.sendall(struct.pack("<II", 0x08, len(payload)) + payload)

    def close(self) -> None:
        self.sock.close()


def _exact(conn: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            raise ConnectionError("closed")
        data.extend(chunk)
    return bytes(data)


class Step28Tests(unittest.TestCase):
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
        import io
        from contextlib import redirect_stderr, redirect_stdout

        os.chdir(path)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def test_errata_reads_records_and_stays_unlisted_without_them(self):
        folder = self.root / "errata"
        folder.mkdir()
        (folder / "errata.json").write_text(
            json.dumps([{"model": "fixture", "revision": "A", "text": "仅测试", "source": "fixture.txt"}]),
            encoding="utf-8",
        )
        rows = read_errata(folder, "fixture")
        self.assertEqual(rows[0]["source"], "fixture.txt")
        self.assertEqual(read_errata(folder, "gc5603"), [])
        project = self.root / "know"
        project.mkdir()
        (project / "devstudio.yaml").write_text(
            "state: sdk_celestia\nrelease_id: AnyCloud39AV200_V1.04\n", encoding="utf-8"
        )
        code, out, err = self.run_at(project, ["knowledge", "errata", "--model", "gc5603"])
        self.assertEqual(code, 0, err)
        self.assertIn("未收录", out)

    def test_driver_diff_lists_catalog_fields_and_does_not_write_sdk(self):
        project = self.root / "diff"
        project.mkdir()
        marker = project / "sdk" / "driver_src" / "sensor" / "gc20c3" / "gc20c3.c"
        marker.parent.mkdir(parents=True)
        marker.write_text("untouched\n", encoding="utf-8")
        (project / "devstudio.yaml").write_text(
            "state: sdk_celestia\nrelease_id: AnyCloud39AV100_V2_V2.05\nsdk:\n  root: sdk\n",
            encoding="utf-8",
        )
        code, out, err = self.run_at(project, ["driver", "diff", "--model", "gc20c3", "--against", "Sky39AV130_V1.01"])
        self.assertEqual(code, 0, err)
        self.assertIn("30fps（另有 45fps 调参档）", out)
        self.assertIn("isp_gc20c3_mipi_2lane_av130_master_slave.conf", out)
        self.assertIn("API：未收录", out)
        self.assertIn("未改两个 SDK", out)
        self.assertEqual(marker.read_text(encoding="utf-8"), "untouched\n")
        result = invoke("source", "driver_diff", {"model": "gc20c3", "against": "Sky39AV130_V1.01"}, project)
        self.assertIn("未改两个 SDK", result["stdout"])

    def test_hal_skips_unindexed_calls_until_approved(self):
        project = self.root / "hal"
        project.mkdir()
        (project / "devstudio.yaml").write_text(
            "state: sdk_celestia\nrelease_id: AnyCloud39AV200_V1.04\n", encoding="utf-8"
        )
        code, out, _ = self.run_at(project, ["hal", "plan", "--module", "camera", "--header", "cam.h"])
        self.assertEqual(code, 1)
        self.assertIn("只在 sdk_customer_app", out)
        (project / "devstudio.yaml").write_text(
            "state: sdk_customer_app\nrelease_id: AnyCloud39AV200_V1.04\ncustomer:\n  hal_out: hal_out\n",
            encoding="utf-8",
        )
        (project / "cam.h").write_text("int camera_open(void);\n", encoding="utf-8")
        (project / "ref.c").write_text(
            "void demo(void) { missing_symbol(); }\nisp_gc5603_mipi_2lane_av200.conf\nSENSOR_ID 1\n",
            encoding="utf-8",
        )
        code, out, err = self.run_at(
            project, ["hal", "plan", "--module", "camera", "--header", "cam.h", "--ref", "ref.c"]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("符号未收录，不写该调用", out)
        self.assertFalse((project / "hal_out").exists())
        code, out, _ = self.run_at(project, ["hal", "apply", "t0001"])
        self.assertEqual(code, 1)
        self.assertIn("计划未批准", out)
        code, _, err = self.run_at(project, ["plan", "approve", "t0001"])
        self.assertEqual(code, 0, err)
        code, out, err = self.run_at(project, ["hal", "apply", "t0001"])
        self.assertEqual(code, 0, out + err)
        text = (project / "hal_out" / "camera_hal.c").read_text(encoding="utf-8")
        self.assertIn("camera_open", text)
        self.assertIn("isp_gc5603_mipi_2lane_av200", text)
        self.assertNotIn("missing_symbol(", text)
        self.assertNotIn("SENSOR_ID", text)
        self.assertFalse((tasks_dir(project) / "t0001" / "plan.md").read_text(encoding="utf-8").startswith("状态：待批准"))

    def test_tune_requires_two_readings_of_the_same_command(self):
        project = self.root / "tune"
        project.mkdir()
        (project / "devstudio.yaml").write_text(
            "state: sdk_celestia\nrelease_id: AnyCloud39AV200_V1.04\n", encoding="utf-8"
        )
        code, out, _ = self.run_at(project, ["tune", "memory", "--before", "a", "--after", "b"])
        self.assertEqual(code, 1)
        self.assertIn("没有两次读数。不写优化结论。", out)
        self._capture(project / "before", "192.168.0.8:7777", "echo same", 1000)
        self._capture(project / "other", "192.168.0.8:7777", "echo other", 2000)
        code, out, _ = self.run_at(project, ["tune", "memory", "--before", "before", "--after", "other"])
        self.assertEqual(code, 1)
        self.assertIn("没有同一命令的两次读数。不写优化结论。", out)
        self._capture(project / "after", "192.168.0.8:7777", "echo same", 4000)
        code, out, err = self.run_at(project, ["tune", "memory", "--before", "before", "--after", "after"])
        self.assertEqual(code, 0, err)
        self.assertIn("1000 → 4000", out)
        self.assertIn("阈值未收录。不写优化结论。", out)
        result = invoke("tune", "tune_cpu", {"before": "before", "after": "after"}, project)
        self.assertIn("不写优化结论", result["stdout"])

    def test_recipe_and_burntool_do_not_create_new_paths(self):
        project = self.root / "app"
        project.mkdir()
        (project / "devstudio.yaml").write_text(
            "state: sdk_celestia\nrelease_id: AnyCloud39AV200_V1.04\n", encoding="utf-8"
        )
        code, out, err = self.run_at(project, ["app", "recipe", "--form", "framework"])
        self.assertEqual(code, 1)
        self.assertIn("不从空目录生成多媒体框架", out + err)
        self.assertFalse((project / "application").exists())
        code, out, err = self.run_at(project, ["app", "recipe"])
        self.assertEqual(code, 0, err)
        self.assertIn("TABLET", out)
        self.assertIn("未新建目录", out)
        code, out, err = self.run_at(project, ["device", "push", "burntool"])
        self.assertEqual(code, 1)
        self.assertIn("burntool", out)
        self.assertIn("未纳入 device push", out)
        code, out, err = self.run_at(project, ["device", "log"])
        self.assertEqual(code, 1)
        self.assertIn("需要板号", out)
        code, out, err = self.run_at(project, ["device", "firmware", "--board", "192.168.0.8:7777", "--kind", "image"])
        self.assertEqual(code, 1)
        self.assertIn("整包分区表未收录", out)
        self.assertIn("不使用 burntool", out)

    def test_image_firmware_follows_burntool_table_and_device_mtd(self):
        project = self.root / "fw"
        burn = project / "project" / "demo" / "system" / "AV200" / "V1.04" / "patch" / "tools" / "burntool"
        burn.mkdir(parents=True)
        (project / "devstudio.yaml").write_text(
            "state: sdk_celestia\nrelease_id: AnyCloud39AV200_V1.04\nproject:\n  name: demo\n", encoding="utf-8"
        )
        (burn / "burn.ini").write_text("[Config]\nFile Name=config_demo.txt\n", encoding="utf-8")
        (burn / "config_demo.txt").write_text(
            "download_to_nand count = 3\n"
            "download_to_nand1 = spl.bin, SPL, 0x0, 0x20000, 0\n"
            "download_to_nand2 = uImage, KERNEL, 0x52000, 0x1000, 0\n"
            "download_to_nand3 = usr.sqsh4, APP, 0x53000, 0x2000, 0\n",
            encoding="utf-16",
        )
        images = self.root / "images"
        images.mkdir()
        (images / "uImage").write_bytes(b"k" * 100)
        (images / "usr.sqsh4").write_bytes(b"u" * 200)
        agent = _MtdAgent('dev: size erasesize name\nmtd5: 00001000 00001000 "KERNEL"\nmtd8: 00001800 00001000 "APP"\n')
        agent.start()
        board = f"127.0.0.1:{agent.port}"
        try:
            base = ["device", "firmware", "--board", board, "--kind", "image"]
            code, out, _ = self.run_at(project, base + ["--parts", "SPL", "--dir", str(images)])
            self.assertEqual(code, 1)
            self.assertIn("引导分区 SPL", out)
            code, out, _ = self.run_at(project, base + ["--parts", "APP,KERNEL", "--dir", str(images)])
            self.assertEqual(code, 0, out)
            self.assertIn("以设备为准", out)
            self.assertIn("未烧录", out)
            self.assertEqual(agent.puts, [])
            code, out, _ = self.run_at(project, base + ["--parts", "APP,KERNEL", "--dir", str(images), "--yes"])
            self.assertEqual(code, 0, out)
            self.assertEqual([path for path, _ in agent.puts], ["/tmp/devstudio_app.img", "/tmp/devstudio_kernel.img"])
            self.assertTrue(any("mtd_debug erase /dev/mtd8 0 6144" in item for item in agent.execs))
            self.assertIn("不使用 burntool", out)
        finally:
            agent.close()

    def test_header_index_records_function_names_only(self):
        sdk = self.root / "sdk"
        header = sdk / "platform" / "include" / "ak_demo.h"
        header.parent.mkdir(parents=True)
        header.write_text("AK_S32 AK_MPI_DEMO_Open(AK_S32 id);\n#define SECRET 0x1234\n", encoding="utf-8")
        dest = self.root / "index"
        count, symbols = index_headers(sdk, dest)
        self.assertEqual(count, 1)
        self.assertEqual(symbols, 1)
        text = (dest / "platform" / "include" / "ak_demo.md").read_text(encoding="utf-8")
        self.assertIn("AK_MPI_DEMO_Open", text)
        self.assertNotIn("0x1234", text)

    def _capture(self, folder: Path, board: str, command: str, free_kb: int) -> None:
        folder.mkdir(parents=True)
        (folder / "meta.json").write_text(
            json.dumps({"board": board, "channels": ["metrics"], "command": command}),
            encoding="utf-8",
        )
        (folder / "metrics.jsonl").write_text(
            json.dumps({"cpu": 1, "mem_free_kb": free_kb}) + "\n",
            encoding="utf-8",
        )
