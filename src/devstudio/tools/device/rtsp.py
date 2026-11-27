"""Probe RTSP reachability. No frame decode / no auto PASS on picture quality."""
from __future__ import annotations

import json
import re
import socket
import sys
import time
import urllib.parse
from pathlib import Path

from devstudio.tools.device.capture import _new_dir
from devstudio.tools.device.connect import resolve_board

_DEFAULT_PATHS = ("/live/main", "/live", "/stream", "/")


def run_rtsp(argv: list[str], project: Path) -> int:
    url = ""
    board = ""
    seconds = 5.0
    port = 554
    args = list(argv)
    while args:
        item = args.pop(0)
        if item == "--url" and args:
            url = args.pop(0)
        elif item == "--board" and args:
            board = args.pop(0)
        elif item == "--seconds" and args:
            seconds = float(args.pop(0))
        elif item == "--port" and args:
            port = int(args.pop(0))
        elif item in ("-h", "--help"):
            _usage()
            return 0
        else:
            _usage()
            return 2

    if not url:
        host = resolve_board(project, board or None)
        if not host:
            sys.stdout.write(
                "NEED_INPUT ASK_UI=text：请提供 RTSP URL，或板号 IP 以探测 rtsp://<板号>:554/…\n"
                "TITLE: DevStudio RTSP\n"
                "PROMPT: RTSP 地址（例 rtsp://192.168.0.8:554/live/main）\n"
            )
            return 1
        # strip :7777 if user passed board as host:port from devices
        host = host.split(":")[0]
        url = f"rtsp://{host}:{port}/live/main"

    parsed = _parse_url(url)
    if parsed is None:
        sys.stdout.write(f"无法解析 URL：{url}\n")
        return 1
    host, rtsp_port, path = parsed
    dest = _new_dir(project)
    result = {
        "url": url,
        "host": host,
        "port": rtsp_port,
        "path": path,
        "seconds": seconds,
        "tcp_ok": False,
        "options_ok": False,
        "describe_ok": False,
        "bytes_read": 0,
        "note": "",
    }

    # 1) TCP connect
    try:
        with socket.create_connection((host, rtsp_port), timeout=min(seconds, 5.0)) as sock:
            result["tcp_ok"] = True
            # 2) OPTIONS / DESCRIBE (best-effort text RTSP)
            options_ok, describe_ok, nbytes, detail = _rtsp_handshake(
                sock, host, rtsp_port, path, seconds
            )
            result["options_ok"] = options_ok
            result["describe_ok"] = describe_ok
            result["bytes_read"] = nbytes
            result["note"] = detail
    except OSError as exc:
        result["note"] = str(exc)

    (dest / "rtsp.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (dest / "rtsp.txt").write_text(
        f"url={url}\n"
        f"tcp_ok={result['tcp_ok']}\n"
        f"options_ok={result['options_ok']}\n"
        f"describe_ok={result['describe_ok']}\n"
        f"bytes_read={result['bytes_read']}\n"
        f"note={result['note']}\n"
        "picture_auto_pass=false\n",
        encoding="utf-8",
    )

    sys.stdout.write(f"已保存 {dest / 'rtsp.txt'}\n")
    sys.stdout.write(f"URL：{url}\n")
    sys.stdout.write(f"TCP：{'通' if result['tcp_ok'] else '不通'}\n")
    sys.stdout.write(f"OPTIONS：{'有应答' if result['options_ok'] else '无/失败'}\n")
    sys.stdout.write(f"DESCRIBE：{'有应答' if result['describe_ok'] else '无/失败'}\n")
    if result["note"]:
        sys.stdout.write(f"说明：{result['note']}\n")
    sys.stdout.write(
        "不解码画面，不因有流自动写通过（L5 仍须人看画面）。"
        "无 URL/板号时不写结论。\n"
    )
    if not result["tcp_ok"]:
        return 1
    # TCP 通但协议无应答：仍返回 0，仅作可达性证据
    return 0


def _usage() -> None:
    sys.stderr.write(
        "用法：devstudio device rtsp [--url rtsp://host:554/path] [--board 板号] "
        "[--port 554] [--seconds N]\n"
        "未给 --url 时用板号拼 rtsp://<IP>:554/live/main 做连通探测；不解码画面。\n"
    )


def _parse_url(url: str) -> tuple[str, int, str] | None:
    text = url.strip()
    if not text.lower().startswith("rtsp://"):
        # allow bare host
        if re.match(r"^[\w.-]+(?::\d+)?(?:/.*)?$", text):
            text = "rtsp://" + text
        else:
            return None
    parsed = urllib.parse.urlparse(text)
    host = parsed.hostname or ""
    if not host:
        return None
    port = parsed.port or 554
    path = parsed.path or "/"
    return host, port, path


def _rtsp_handshake(
    sock: socket.socket, host: str, port: int, path: str, seconds: float
) -> tuple[bool, bool, int, str]:
    sock.settimeout(max(1.0, min(seconds, 8.0)))
    cseq = 1
    options_ok = False
    describe_ok = False
    total = 0
    detail = ""

    def send_recv(method: str) -> str:
        nonlocal cseq, total
        req = (
            f"{method} rtsp://{host}:{port}{path} RTSP/1.0\r\n"
            f"CSeq: {cseq}\r\n"
            f"User-Agent: DevStudio-rtsp-probe\r\n"
            f"\r\n"
        )
        cseq += 1
        sock.sendall(req.encode("ascii", errors="ignore"))
        deadline = time.time() + max(1.0, min(seconds, 5.0))
        chunks: list[bytes] = []
        while time.time() < deadline:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
            total += len(data)
            if b"\r\n\r\n" in b"".join(chunks):
                break
        return b"".join(chunks).decode("utf-8", errors="replace")

    try:
        opt = send_recv("OPTIONS")
        if opt.upper().startswith("RTSP/1.0") or "200" in opt.split("\r\n", 1)[0]:
            options_ok = True
        desc = send_recv("DESCRIBE")
        head = desc.split("\r\n", 1)[0] if desc else ""
        if "200" in head:
            describe_ok = True
            detail = "DESCRIBE 200"
        elif "401" in head:
            detail = "DESCRIBE 需要鉴权（401），TCP/服务可达"
            describe_ok = False
        elif "404" in head:
            detail = f"路径可能不对（404）：{path}，可换 --url"
        elif options_ok:
            detail = "OPTIONS 有应答，DESCRIBE 无 200"
        else:
            detail = "已连上 TCP，但未见标准 RTSP 应答"
    except OSError as exc:
        detail = str(exc)
    return options_ok, describe_ok, total, detail
