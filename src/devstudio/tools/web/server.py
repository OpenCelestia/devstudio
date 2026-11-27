"""Thin PC UI server: static files + calls into existing DevStudio device/build/app tools."""
from __future__ import annotations

import contextlib
import json
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from devstudio.root import repo_root
from devstudio.tools.device.connect import run_connect
from devstudio.tools.device.frame import (
    EXEC,
    EXEC_RESULT,
    FILE_GET,
    FILE_PUT,
    PING,
    SUB,
    split_board,
)
from devstudio.tools.device.push import upload_app

HELLO = 0x01
FRAME = 0x04
SNAPSHOT = 0x05
REBOOT = 0x09
INPUT = 0x12


def web_dir() -> Path:
    return repo_root() / "web"


class StudioSession:
    """Long-lived Studio TCP session for UI (status / video / exec)."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.board = ""
        self.sock: socket.socket | None = None
        self.connected = False
        self.hello: dict | None = None
        self.last_jpeg: bytes | None = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._waiters: dict[int, list[threading.Event]] = {}
        self._replies: dict[int, bytes] = {}

    def status(self) -> dict[str, Any]:
        return {"connected": self.connected, "device": self.board, "hello": self.hello}

    def connect(self, board: str) -> None:
        host, port = split_board(board)
        with self.lock:
            self.close_locked()
            sock = socket.create_connection((host, port), timeout=5)
            sock.settimeout(1.0)
            self.sock = sock
            self.board = f"{host}:{port}" if port != 7777 else host
            self.connected = True
            self._stop.clear()
            self._reader = threading.Thread(target=self._loop, name="studio-ui", daemon=True)
            self._reader.start()
            self._send(PING, b"")
            self.subscribe(frame=False)

    def close(self) -> None:
        with self.lock:
            self.close_locked()

    def close_locked(self) -> None:
        self._stop.set()
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self.connected = False
        self.hello = None

    def subscribe(self, *, frame: bool) -> None:
        payload = json.dumps({"metrics": True, "log": True, "frame": frame}).encode("utf-8")
        self._send(SUB, payload)

    def exec(self, cmd: str, timeout: float = 15) -> dict[str, Any]:
        body = self._request(
            EXEC, json.dumps({"cmd": cmd}, ensure_ascii=False).encode("utf-8"), EXEC_RESULT, timeout
        )
        try:
            return json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return {"ok": False, "out": body.decode("utf-8", errors="replace")}

    def file_get(self, path: str, timeout: float = 20) -> bytes:
        body = self._request(FILE_GET, json.dumps({"path": path}).encode("utf-8"), FILE_GET, timeout)
        if len(body) < 8:
            raise ConnectionError("文件应答过短")
        status, size = struct.unpack("<II", body[:8])
        if status != 0:
            raise ConnectionError(f"读取失败 ({status})")
        return body[8 : 8 + size]

    def file_put(self, path: str, data: bytes, timeout: float = 30) -> dict[str, Any]:
        encoded = path.encode("utf-8")
        payload = struct.pack("<II", len(encoded), len(data)) + encoded + data
        body = self._request(FILE_PUT, payload, FILE_PUT, timeout)
        parsed = json.loads(body.decode("utf-8", errors="replace") or "{}")
        if not isinstance(parsed, dict) or not parsed.get("ok", False):
            raise ConnectionError(str((parsed or {}).get("error") or "写入失败"))
        return parsed

    def reboot(self) -> None:
        self._send(REBOOT, b"")

    def snapshot(self) -> None:
        self._send(SNAPSHOT, b"")

    def input_event(self, op: str, x: int, y: int) -> None:
        self._send(INPUT, json.dumps({"op": op, "x": x, "y": y}).encode("utf-8"))

    def _send(self, kind: int, payload: bytes) -> None:
        with self.lock:
            if self.sock is None:
                raise ConnectionError("设备未连接")
            self.sock.sendall(struct.pack("<II", kind, len(payload)) + payload)

    def _request(self, kind: int, payload: bytes, expect: int, timeout: float) -> bytes:
        ev = threading.Event()
        with self.lock:
            self._waiters.setdefault(expect, []).append(ev)
            self._replies.pop(expect, None)
            if self.sock is None:
                raise ConnectionError("设备未连接")
            self.sock.sendall(struct.pack("<II", kind, len(payload)) + payload)
        if not ev.wait(timeout):
            raise TimeoutError("设备应答超时")
        with self.lock:
            return self._replies.pop(expect, b"")

    def _loop(self) -> None:
        buf = bytearray()
        while not self._stop.is_set():
            sock = self.sock
            if sock is None:
                break
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            buf.extend(chunk)
            while len(buf) >= 8:
                kind, size = struct.unpack("<II", buf[:8])
                if len(buf) < 8 + size:
                    break
                body = bytes(buf[8 : 8 + size])
                del buf[: 8 + size]
                self._dispatch(kind, body)
        with self.lock:
            self.connected = False

    def _dispatch(self, kind: int, body: bytes) -> None:
        if kind == HELLO:
            try:
                self.hello = json.loads(body.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                self.hello = {"raw": body.decode("utf-8", errors="replace")}
            return
        if kind in (FRAME, SNAPSHOT):
            self.last_jpeg = body
            return
        with self.lock:
            waiters = self._waiters.pop(kind, [])
            if waiters:
                self._replies[kind] = body
                for ev in waiters:
                    ev.set()


SESSION = StudioSession()
STATE: dict[str, Any] = {
    "product": "anypi_nova",
    "repo": "",
    "tree": "",
    "lang": "zh",
    "view_on": True,
}


def _product_cfg() -> dict[str, Any]:
    path = web_dir() / "products" / f"{STATE['product']}.json"
    if not path.is_file():
        return {"id": STATE["product"]}
    return json.loads(path.read_text(encoding="utf-8"))


def _device_file() -> Path:
    return web_dir() / "device.json"


def _save_device(board: str) -> None:
    _device_file().write_text(json.dumps({"device": board}, ensure_ascii=False) + "\n", encoding="utf-8")


def _load_device() -> str:
    path = _device_file()
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    return str(data.get("device") or data.get("host") or "")


def _list_tablet_apps(repo: Path) -> list[dict[str, str]]:
    root = repo / "application" / "packages" / "tablet"
    if not root.is_dir():
        return []
    apps: list[dict[str, str]] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name == "launcher":
            continue
        app_json = entry / "app.json"
        app_id = entry.name
        name = entry.name
        if app_json.is_file():
            try:
                data = json.loads(app_json.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    app_id = str(data.get("app_id") or app_id)
                    name = str(data.get("name") or data.get("name_zh") or name)
            except json.JSONDecodeError:
                pass
        apps.append({"id": app_id, "name": name})
    return apps


def _project() -> Path:
    raw = STATE.get("repo") or ""
    path = Path(raw).expanduser() if raw else Path.cwd()
    return path.resolve()


def _run_build(_module: str) -> tuple[int, str]:
    from devstudio.tools.build.classify import run_build

    project = _project()
    command = f"./build.sh -a --product-name {STATE['product']}"
    buf = StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = run_build(["--run", "--command", command, "--timeout", "900"], project)
    return code, buf.getvalue()


def _json(handler: BaseHTTPRequestHandler, code: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._file(web_dir() / "public" / "index.html", "text/html; charset=utf-8")
        if path == "/events":
            return self._events()
        if path == "/video":
            return self._video()
        if path == "/api/status":
            return _json(self, 200, SESSION.status())
        if path == "/api/lang":
            return _json(self, 200, {"lang": STATE["lang"]})
        if path == "/api/product":
            cfg = _product_cfg()
            return _json(
                self,
                200,
                {
                    "id": cfg.get("id", STATE["product"]),
                    "procAlias": cfg.get("procAlias") or {},
                    "agentName": cfg.get("agentName") or "",
                },
            )
        if path == "/api/apps":
            return _json(self, 200, {"apps": _list_tablet_apps(_project())})
        if path == "/api/version":
            return _json(self, 200, {"ok": True, "fileApi": 2, "backend": "devstudio-python"})
        if path == "/api/tree":
            return _json(self, 200, {"tree": STATE.get("tree") or str(_project())})
        if path in ("/api/llm/config", "/api/llm/status"):
            return _json(self, 501, {"ok": False, "error": "LLM 已移出 web；请用 IDE Agent / CLI"})
        if path == "/api/serial/ports":
            return _json(self, 200, {"ports": []})
        if path == "/api/file/mnt-status":
            return self._mnt_status()
        return _json(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = _read_json(self)
        try:
            if path == "/api/lang":
                STATE["lang"] = "en" if body.get("lang") == "en" else "zh"
                return _json(self, 200, {"lang": STATE["lang"]})
            if path == "/api/device":
                board = str(body.get("device") or body.get("host") or "").strip()
                if not board:
                    return _json(self, 400, {"ok": False, "error": "missing device"})
                SESSION.connect(board)
                _save_device(board)
                run_connect(_project(), board, STATE["product"])
                return _json(self, 200, {"ok": True, "device": board, "connected": SESSION.connected})
            if path == "/api/exec":
                if not SESSION.connected:
                    return _json(self, 503, {"error": "device not connected"})
                cmd = str(body.get("cmd") or "")
                if not cmd:
                    return _json(self, 400, {"error": "missing cmd"})
                return _json(self, 200, SESSION.exec(cmd))
            if path == "/api/file/read":
                data = SESSION.file_get(str(body.get("path") or ""))
                return _json(
                    self,
                    200,
                    {"ok": True, "kind": "file", "content": data.decode("utf-8", errors="replace")},
                )
            if path in ("/api/file/list", "/api/file/open"):
                p = str(body.get("path") or "/")
                out = SESSION.exec(f"ls -la -- {p}")
                text = str(out.get("out") or "")
                entries = []
                for line in text.splitlines()[1:]:
                    parts = line.split(None, 8)
                    if len(parts) >= 9:
                        entries.append({"name": parts[8], "dir": parts[0].startswith("d"), "size": parts[4]})
                return _json(self, 200, {"ok": True, "kind": "dir", "path": p, "entries": entries, "out": text})
            if path == "/api/file/write":
                p = str(body.get("path") or "")
                content = str(body["content"]) if "content" in body and body["content"] is not None else ""
                SESSION.file_put(p, content.encode("utf-8"))
                return _json(self, 200, {"ok": True, "path": p, "size": len(content.encode("utf-8"))})
            if path == "/api/file/remount-mnt":
                ran = SESSION.exec("mount -o remount,rw /mnt 2>&1")
                return _json(self, 200, {"ok": True, "out": ran.get("out") or ""})
            if path == "/api/sync":
                STATE["view_on"] = body.get("on") is not False
                SESSION.subscribe(frame=bool(STATE["view_on"]))
                return _json(self, 200, {"on": STATE["view_on"]})
            if path == "/api/reboot":
                SESSION.reboot()
                return _json(self, 200, {"ok": True})
            if path == "/api/snap":
                before = SESSION.last_jpeg
                SESSION.snapshot()
                deadline = time.time() + 5
                while time.time() < deadline:
                    if SESSION.last_jpeg and SESSION.last_jpeg is not before:
                        self.send_response(200)
                        self.send_header("Content-Type", "image/jpeg")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.wfile.write(SESSION.last_jpeg)
                        return
                    time.sleep(0.1)
                return _json(self, 504, {"error": "snapshot timeout"})
            if path == "/api/input":
                op = body.get("op") if body.get("op") in ("up", "down", "move") else "move"
                SESSION.input_event(str(op), int(body.get("x") or 0), int(body.get("y") or 0))
                return _json(self, 200, {"ok": True})
            if path == "/api/build":
                code, log = _run_build(str(body.get("module") or _product_cfg().get("agentModule") or ""))
                return _json(self, 200 if code == 0 else 500, {"ok": code == 0, "log": log})
            if path == "/api/push":
                module = str(body.get("module") or "")
                if module.startswith("APP_TABLET_"):
                    app_id = module[len("APP_TABLET_") :].lower()
                elif module:
                    app_id = module
                else:
                    app_id = str(body.get("app_id") or "")
                if body.get("compile") is not False:
                    code, log = _run_build(module)
                    if code != 0:
                        return _json(self, 500, {"ok": False, "error": log or "build failed"})
                board = SESSION.board or _load_device()
                text, code = upload_app(_project(), board, app_id or "studio")
                return _json(self, 200 if code == 0 else 500, {"ok": code == 0, "text": text, "app_id": app_id})
            if path == "/api/apps/delete":
                from devstudio.tools.source.app_cmd import run_app

                app_id = str(body.get("id") or "")
                code = run_app(["remove", app_id, "--confirm"], _project())
                return _json(self, 200 if code == 0 else 400, {"ok": code == 0, "id": app_id})
            if path.startswith("/api/llm"):
                return _json(self, 501, {"ok": False, "error": "LLM 已移出 web；请用 IDE Agent"})
            if path.startswith("/api/serial"):
                return _json(self, 501, {"ok": False, "error": "串口请用本机工具；web 薄壳不内置"})
            if path == "/api/wsl":
                return _json(self, 501, {"ok": False, "error": "请用 devstudio build / ./build.sh"})
        except Exception as exc:  # noqa: BLE001
            return _json(self, 500, {"ok": False, "error": str(exc)})
        return _json(self, 404, {"ok": False, "error": "not found"})

    def _mnt_status(self) -> None:
        if not SESSION.connected:
            return _json(self, 503, {"error": "device not connected"})
        ran = SESSION.exec("mount | grep ' /mnt ' || true")
        out = str(ran.get("out") or "")
        return _json(self, 200, {"ok": True, "readOnly": " ro," in out or ",ro," in f",{out},", "out": out})

    def _file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 2000\n\n")
            while True:
                payload = json.dumps(SESSION.status(), ensure_ascii=False).encode("utf-8")
                self.wfile.write(b"event: status\ndata: " + payload + b"\n\n")
                self.wfile.flush()
                time.sleep(2)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _video(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if SESSION.connected:
            SESSION.subscribe(frame=True)
        try:
            last = None
            while True:
                jpeg = SESSION.last_jpeg
                if jpeg and jpeg is not last:
                    last = jpeg
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                time.sleep(0.05)
        except (BrokenPipeError, ConnectionResetError):
            return


def serve(port: int, *, product: str, repo: str, tree: str) -> int:
    STATE["product"] = product
    STATE["repo"] = repo
    STATE["tree"] = tree
    saved = _load_device()
    if saved:
        try:
            SESSION.connect(saved)
            run_connect(_project(), saved, product)
        except OSError:
            pass
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[web] UI -> http://localhost:{port}")
    print(f"[web] product={product} repo={repo or Path.cwd()} (thin shell -> DeviceLink / device / build)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()
        return 130
    return 0
