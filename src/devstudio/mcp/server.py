import json
import sys

from devstudio import __version__
from devstudio.mcp.elicitation import (
    client_supports_form,
    elicit_board_ip,
    elicit_cis_intake,
    tool_needs_board,
    tool_needs_cis_intake,
)
from devstudio.tools.registry import invoke, list_tools

GROUPS = ("project", "knowledge", "rules", "source", "build", "device", "tune")


def run_mcp(argv: list[str]) -> int:
    group = ""
    args = list(argv)
    while args:
        item = args.pop(0)
        if item == "--group" and args:
            group = args.pop(0)
    if group not in GROUPS:
        sys.stderr.write("用法：devstudio mcp --group project|knowledge|rules|source|build|device|tune\n")
        return 2
    serve(group, sys.stdin.buffer, sys.stdout.buffer)
    return 0


def serve(group: str, instream, outstream) -> None:
    client_caps: dict = {}
    while True:
        message = read_message(instream)
        if message is None:
            return
        response = handle(
            group,
            message,
            client_caps=client_caps,
            read_message=lambda: read_message(instream),
            write_message=lambda payload: write_message(outstream, payload),
        )
        if response is not None:
            write_message(outstream, response)


def handle(
    group: str,
    message: dict,
    *,
    client_caps: dict,
    read_message,
    write_message,
) -> dict | None:
    method = message.get("method")
    msg_id = message.get("id")
    if method == "notifications/initialized":
        return None
    if msg_id is None:
        return None
    if method == "initialize":
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        caps = params.get("capabilities") if isinstance(params.get("capabilities"), dict) else {}
        client_caps.clear()
        client_caps.update(caps)
        return _result(
            msg_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": f"devstudio-{group}", "version": __version__},
            },
        )
    if method == "tools/list":
        return _result(msg_id, {"tools": _tools(group)})
    if method == "tools/call":
        params = message.get("params", {})
        name = params.get("name", "")
        allowed = {item["name"] for item in _tools(group)}
        if name not in allowed:
            return _error(msg_id, -32602, "工具不在当前组")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            arguments = {}
        arguments = dict(arguments)
        if client_supports_form(client_caps):
            if tool_needs_board(name) and not str(arguments.get("board") or "").strip():
                board = elicit_board_ip(
                    read_message=read_message,
                    write_message=write_message,
                )
                if board:
                    arguments["board"] = board
            if tool_needs_cis_intake(name):
                arguments = _merge_cis_intake(
                    arguments,
                    read_message=read_message,
                    write_message=write_message,
                )
        result = invoke(group, name, arguments)
        # 仍缺板号时，stdout 里会有 NEED_BOARD ASK_UI=board_ip，供 Agent 用 AskQuestion
        return _result(
            msg_id,
            {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "structuredContent": result,
                "isError": result["status"] != "ok",
            },
        )
    return _error(msg_id, -32601, "方法不存在")


def _merge_cis_intake(arguments: dict, *, read_message, write_message) -> dict:
    """Fill FAE-aligned CIS fields via elicitation when plan_sensor args incomplete."""
    required = ("resolution", "fps", "interface", "lanes", "mclk", "i2c")
    model = str(arguments.get("model") or "").strip()
    if not model:
        filled = elicit_cis_intake(
            read_message=read_message,
            write_message=write_message,
            model="?",
            missing=["resolution", "fps", "interface", "lanes", "mclk", "i2c", "sequence_text", "cis", "features", "datasheet"],
        )
        # first field round may include model if user typed in resolution by mistake — still need model
        arguments.update({k: v for k, v in filled.items() if v})
        return arguments
    missing = [key for key in required if not str(arguments.get(key) or "").strip()]
    has_seq = bool(
        str(arguments.get("sequence") or "").strip()
        or str(arguments.get("sequence_text") or "").strip()
    )
    if not has_seq:
        missing.append("sequence")
    if not missing and str(arguments.get("cis") or "").strip():
        return arguments
    # also ask optional docs when any required missing
    ask_keys = list(missing)
    if "cis" not in arguments or not str(arguments.get("cis") or "").strip():
        ask_keys.append("cis")
    if not str(arguments.get("features") or "").strip():
        ask_keys.append("features")
    if not str(arguments.get("datasheet") or "").strip():
        ask_keys.append("datasheet")
    filled = elicit_cis_intake(
        read_message=read_message,
        write_message=write_message,
        model=model,
        missing=ask_keys,
        defaults={k: str(arguments.get(k) or "") for k in ask_keys},
    )
    for key, value in filled.items():
        if value and not str(arguments.get(key) or "").strip():
            arguments[key] = value
    return arguments


def _tools(group: str) -> list[dict]:
    return list_tools(group)


def read_message(stream):
    headers = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, _, value = line.decode("ascii", errors="replace").partition(":")
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    return json.loads(stream.read(length).decode("utf-8"))


def write_message(stream, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    stream.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii") + data)
    stream.flush()


def _result(msg_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
