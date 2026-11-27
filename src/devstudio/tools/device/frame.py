"""One-shot Studio TCP 7777 client. Frames are little-endian [4B type][4B length][payload]."""
from __future__ import annotations

import json
import socket
import struct
import time

HELLO = 0x01
METRICS = 0x02
LOG = 0x03
EXEC = 0x07
EXEC_RESULT = 0x08
FILE_GET = 0x0A
FILE_PUT = 0x0B
ERR = 0x0E
PING = 0x0F
PONG = 0x10
SUB = 0x11


def split_board(board: str) -> tuple[str, int]:
    host, sep, port = board.rpartition(":")
    if sep and port.isdigit() and ":" not in host:
        return host, int(port)
    return board, 7777


class DeviceLink:
    def __init__(self, board: str, port: int | None = None, timeout: float = 20):
        host, parsed = split_board(board)
        self.timeout = timeout
        self.sock = socket.create_connection((host, port or parsed), timeout=5)
        self.sock.settimeout(timeout)

    def close(self) -> None:
        self.sock.close()

    def __enter__(self) -> "DeviceLink":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def ping(self, timeout: float | None = None) -> None:
        """Reach the on-device studio agent (application/services/studio). The PC page is `devstudio web`."""
        kind, _body = self._request(PING, b"", timeout)
        if kind != PONG:
            raise ConnectionError(f"未收到 PONG（type={kind:#x}）")

    def exec(self, command: str, timeout: float | None = None) -> dict:
        kind, body = self._request(EXEC, json.dumps({"cmd": command}, ensure_ascii=False).encode("utf-8"), timeout)
        return _json_body(kind, body)

    def file_get(self, path: str, timeout: float | None = None) -> bytes:
        kind, body = self._request(FILE_GET, json.dumps({"path": path}).encode("utf-8"), timeout)
        if kind == ERR:
            raise ConnectionError(_json_body(kind, body).get("error") or "读取失败")
        if len(body) < 8:
            raise ConnectionError("文件应答过短")
        status, size = struct.unpack("<II", body[:8])
        if status != 0:
            raise ConnectionError(f"读取失败 ({status})")
        return body[8:8 + size]

    def file_put(self, path: str, data: bytes, timeout: float | None = None) -> dict:
        encoded = path.encode("utf-8")
        payload = struct.pack("<II", len(encoded), len(data)) + encoded + data
        kind, body = self._request(FILE_PUT, payload, timeout)
        parsed = _json_body(kind, body)
        if kind == ERR or not parsed.get("ok", False):
            raise ConnectionError(str(parsed.get("error") or "写入失败"))
        return parsed

    def subscribe(self, *, metrics: bool, log: bool, frame: bool = False) -> None:
        payload = json.dumps({"metrics": metrics, "log": log, "frame": frame}).encode("utf-8")
        self.sock.sendall(struct.pack("<II", SUB, len(payload)) + payload)

    def collect(self, seconds: float) -> list[tuple[int, bytes]]:
        deadline = time.monotonic() + seconds
        frames: list[tuple[int, bytes]] = []
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            self.sock.settimeout(min(0.5, max(remaining, 0.05)))
            try:
                header = _read_exact(self.sock, 8)
            except (TimeoutError, socket.timeout):
                continue
            except ConnectionError:
                break
            got, size = struct.unpack("<II", header)
            body = _read_exact(self.sock, size) if size else b""
            frames.append((got, body))
        self.sock.settimeout(self.timeout)
        return frames

    def _request(self, kind: int, payload: bytes, timeout: float | None) -> tuple[int, bytes]:
        self.sock.settimeout(timeout or self.timeout)
        self.sock.sendall(struct.pack("<II", kind, len(payload)) + payload)
        for _ in range(40):
            header = _read_exact(self.sock, 8)
            got, size = struct.unpack("<II", header)
            body = _read_exact(self.sock, size) if size else b""
            # FILE_PUT/HELLO-style replies reuse the request type; EXEC/PING use paired types.
            if got in (EXEC_RESULT, FILE_GET, ERR, PONG) or got == kind:
                return got, body
        raise TimeoutError("未收到设备应答")


def _json_body(kind: int, body: bytes) -> dict:
    try:
        parsed = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        parsed = {"ok": kind != ERR, "out": body.decode("utf-8", errors="replace")}
    return parsed if isinstance(parsed, dict) else {"ok": False, "out": str(parsed)}


def _read_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("设备连接已关闭")
        data.extend(chunk)
    return bytes(data)
