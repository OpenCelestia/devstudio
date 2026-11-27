import json
import os
import socket
import struct
import threading
import unittest
from pathlib import Path

from devstudio.cli.main import main
from devstudio.workspace import devices_path, tasks_dir

FILE_PUT = 0x0B
EXEC = 0x07
EXEC_RESULT = 0x08
FILE_GET = 0x0A
SUB = 0x11
METRICS = 0x02
LOG = 0x03


def _read_frame(conn: socket.socket) -> tuple[int, bytes] | None:
    header = _read_exact(conn, 8)
    if header is None:
        return None
    kind, size = struct.unpack("<II", header)
    body = _read_exact(conn, size) if size else b""
    if body is None:
        return None
    return kind, body


def _read_exact(conn: socket.socket, size: int) -> bytes | None:
    data = bytearray()
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            return None
        data.extend(chunk)
    return bytes(data)


def _send(conn: socket.socket, kind: int, payload: bytes) -> None:
    conn.sendall(struct.pack("<II", kind, len(payload)) + payload)


class _Agent(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.puts: list[tuple[str, bytes]] = []
        self.execs: list[str] = []
        self.ready = threading.Event()

    def run(self) -> None:
        self.ready.set()
        while True:
            try:
                conn, _addr = self.sock.accept()
            except OSError:
                return
            with conn:
                self._serve(conn)

    def _serve(self, conn: socket.socket) -> None:
        conn.settimeout(5)
        while True:
            try:
                frame = _read_frame(conn)
            except (TimeoutError, socket.timeout, ConnectionError):
                return
            if frame is None:
                return
            kind, body = frame
            if kind == FILE_PUT and len(body) >= 8:
                pathlen, size = struct.unpack("<II", body[:8])
                path = body[8:8 + pathlen].decode("utf-8")
                self.puts.append((path, body[8 + pathlen:8 + pathlen + size]))
                _send(conn, EXEC_RESULT, json.dumps({"ok": True, "path": path}).encode("utf-8"))
            elif kind == EXEC:
                self.execs.append(json.loads(body.decode("utf-8")).get("cmd", ""))
                _send(conn, EXEC_RESULT, json.dumps({"ok": True, "out": "OK usb /mnt/install/apps/demo\n"}).encode("utf-8"))
            elif kind == SUB:
                sample = {
                    "cpu": 11.0,
                    "mem_free_kb": 18000,
                    "uptime": 9,
                    "load": [0.2, 0.1, 0.1],
                    "temp_c": 40,
                    "net": {"rx_kb": 3, "tx_kb": 4},
                    "procs": [{"name": "clock", "rss_kb": 100}],
                }
                _send(conn, METRICS, json.dumps(sample).encode("utf-8"))
                _send(conn, LOG, json.dumps({"tag": "kmsg", "text": "boot ok"}).encode("utf-8"))
                sample["procs"] = [{"name": "clock", "rss_kb": 40}]
                _send(conn, METRICS, json.dumps(sample).encode("utf-8"))
            elif kind == FILE_GET:
                text = json.dumps({"name": "nova"}).encode("utf-8")
                _send(conn, FILE_GET, struct.pack("<II", 0, len(text)) + text)

    def close(self) -> None:
        self.sock.close()


class DeviceStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self._cwd = os.getcwd()
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        os.environ["DEVSTUDIO_DATA"] = str(self.root / "data")
        self.agent = _Agent()
        self.agent.start()
        self.agent.ready.wait(2)
        self.project = self.root / "tree"
        self.project.mkdir()
        os.chdir(self.project)

    def tearDown(self) -> None:
        self.agent.close()
        os.chdir(self._cwd)
        self._tmp.cleanup()
        os.environ.pop("DEVSTUDIO_DATA", None)

    def _run(self, args: list[str]) -> tuple[int, str, str]:
        import io
        from contextlib import redirect_stderr, redirect_stdout

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def test_push_uploads_checked_app_and_keeps_sources(self) -> None:
        app = self.project / "application" / "packages" / "tablet" / "demo"
        app.mkdir(parents=True)
        (app / "app.json").write_text(
            json.dumps({"app_id": "demo", "entry_symbol": "app_entry_start_demo"}),
            encoding="utf-8",
        )
        (app / "app_entry_demo.c").write_text("void app_entry_start_demo(lv_obj_t *main) {}\n", encoding="utf-8")
        (app / "tablet_demo.c").write_text(
            "lv_result_t tablet_demo_create(lv_obj_t *main) { return 0; }\n",
            encoding="utf-8",
        )
        marker = app / "keep.txt"
        marker.write_text("local", encoding="utf-8")
        board = f"127.0.0.1:{self.agent.port}"
        devices_path(self.project).parent.mkdir(parents=True, exist_ok=True)
        devices_path(self.project).write_text(json.dumps([{"board": board, "product": "anypi_nova"}]), encoding="utf-8")
        (tasks_dir(self.project) / "t0001").mkdir(parents=True)
        code, out, err = self._run(["device", "push", "demo"])
        self.assertEqual(code, 0, out + err)
        self.assertIn("已上传到 /mnt/install/apps/demo", out)
        self.assertIn("完成等级：L4", out)
        self.assertEqual(marker.read_text(encoding="utf-8"), "local")
        names = [path for path, _data in self.agent.puts]
        self.assertIn("/tmp/studio_push.tar.gz", names)
        self.assertIn("/tmp/nova_install.sh", names)
        script = next(data for path, data in self.agent.puts if path.endswith("nova_install.sh"))
        self.assertIn(b"/mnt/install/.app_bak", script)
        self.assertTrue(self.agent.execs)

    def test_capture_and_compare_need_both_samples(self) -> None:
        board = f"127.0.0.1:{self.agent.port}"
        devices_path(self.project).parent.mkdir(parents=True, exist_ok=True)
        devices_path(self.project).write_text(json.dumps([{"board": board}]), encoding="utf-8")
        task = tasks_dir(self.project) / "t0002"
        task.mkdir(parents=True)
        (task / "plan.md").write_text("检查：\n- rss:clock:down\n- log-absent:panic\n- config:/etc/config/settings.json:name=nova\n", encoding="utf-8")
        (task / "task.json").write_text(json.dumps({"task_id": "t0002", "level": "L3"}), encoding="utf-8")
        code, out, err = self._run([
            "device", "capture", "--board", board, "--seconds", "1", "--config", "/etc/config/settings.json",
        ])
        self.assertEqual(code, 0, out + err)
        self.assertIn("已保存", out)
        first = next((task / "capture").iterdir())
        code, out, err = self._run([
            "device", "capture", "--board", board, "--seconds", "1", "--config", "/etc/config/settings.json",
        ])
        self.assertEqual(code, 0, out + err)
        second = next(path for path in (task / "capture").iterdir() if path != first)
        # The fake agent sends rss 100 then 40 in one capture, so each file ends at 40.
        # Make the first sample the higher one.
        metrics = json.loads((first / "metrics.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        metrics["procs"][0]["rss_kb"] = 100
        (first / "metrics.jsonl").write_text(json.dumps(metrics) + "\n", encoding="utf-8")
        code, out, err = self._run(["device", "compare", str(first), str(second)])
        self.assertEqual(code, 0, out + err)
        self.assertIn("rss clock：100 → 40，通过", out)
        self.assertIn("自动项通过，完成等级：L4", out)
        saved = json.loads((task / "task.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["level"], "L4")
        self.assertIn("rtsp", saved["unverified"])
        code, out, err = self._run(["device", "compare", str(second), str(first)])
        self.assertEqual(code, 1, err)
        self.assertIn("不写通过", out)

    def test_compare_without_pair_does_not_pass(self) -> None:
        code, out, err = self._run(["device", "compare", "missing-a", "missing-b", "--check", "rss:clock:down"])
        self.assertEqual(code, 1, err)
        self.assertIn("找不到 before 或 after", out)
