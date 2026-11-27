"""MCP form elicitation helpers (Cursor / VS Code in-chat forms, not desktop UI)."""
from __future__ import annotations

import itertools
import uuid
from typing import Any, Callable

ReadMessage = Callable[[], dict | None]
WriteMessage = Callable[[dict], None]

_BOARD_TOOLS = frozenset(
    {
        "connect",
        "push_app",
        "metrics",
        "capture",
        "device_log",
        "device_status",
        "device_analyze",
        "device_check",
        "device_rtsp",
        "device_firmware",
        "app_remove",
        "remove_app",
    }
)

_CIS_INTAKE_TOOLS = frozenset({"plan_sensor", "cis_plan"})

# FAE AI sensor-debug need_info 对齐字段（列表外方案流）
CIS_INTAKE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("resolution", "分辨率", "例如 1920x1080"),
    ("fps", "帧率", "例如 30 或 白天30/夜晚15"),
    ("interface", "接口", "MIPI 2lane / MIPI 4lane / DVP"),
    ("cis", "CIS 模式", "1（单目）/ 2主从 / 2从从 / …"),
    ("features", "支持功能", "常电、快启、AOV 等，可空"),
    ("lanes", "MIPI Lane 数", "例如 2 或 4"),
    ("mclk", "MCLK 频率", "例如 24MHz"),
    ("i2c", "I2C 地址", "7 位或 8 位写地址，如 0x37 / 0x6E"),
    ("sequence_text", "寄存器序列内容", "粘贴 ini 或 0xADDR,0xVAL；也可填本地文件路径"),
    ("datasheet", "Datasheet 路径", "可选：PDF/文档本地路径，无可空"),
)

_ID = itertools.count(1)


def tool_needs_board(name: str) -> bool:
    return name in _BOARD_TOOLS or name.endswith("_board")


def tool_needs_cis_intake(name: str) -> bool:
    return name in _CIS_INTAKE_TOOLS


def client_supports_form(capabilities: dict[str, Any] | None) -> bool:
    if not isinstance(capabilities, dict):
        return False
    elicitation = capabilities.get("elicitation")
    if elicitation is True:
        return True
    if not isinstance(elicitation, dict):
        return False
    if not elicitation or "form" in elicitation or elicitation.get("form") is not None:
        return True
    return False


def elicit_form(
    *,
    read_message: ReadMessage,
    write_message: WriteMessage,
    message: str,
    schema: dict[str, Any],
) -> dict[str, Any] | None:
    """Generic elicitation/create. Returns accepted content dict or None."""
    req_id = f"elicit-{next(_ID)}-{uuid.uuid4().hex[:8]}"
    write_message(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "elicitation/create",
            "params": {
                "mode": "form",
                "message": message,
                "requestedSchema": schema,
            },
        }
    )
    while True:
        incoming = read_message()
        if incoming is None:
            return None
        if incoming.get("id") != req_id:
            if "method" in incoming and incoming.get("id") is not None:
                write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": incoming.get("id"),
                        "error": {"code": -32000, "message": "elicitation in progress"},
                    }
                )
            continue
        if "error" in incoming:
            return None
        result = incoming.get("result")
        if not isinstance(result, dict):
            return None
        if str(result.get("action") or "") != "accept":
            return None
        content = result.get("content")
        return content if isinstance(content, dict) else None


def elicit_board_ip(
    *,
    read_message: ReadMessage,
    write_message: WriteMessage,
    default: str = "",
    title: str = "DevStudio Board IP / 板号",
) -> str:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "board_ip": {
                "type": "string",
                "title": "Board IP / 板号",
                "description": "Device IP, e.g. 192.168.0.8 (default port 7777) / 设备 IP，默认端口 7777",
            }
        },
        "required": ["board_ip"],
    }
    if default:
        schema["properties"]["board_ip"]["default"] = default
    content = elicit_form(
        read_message=read_message,
        write_message=write_message,
        message=f"{title}: enter board IP (default port 7777) / 请输入板号 IP（默认端口 7777）",
        schema=schema,
    )
    if not content:
        return ""
    return str(content.get("board_ip") or content.get("board") or "").strip()


def cis_intake_schema(missing_keys: list[str] | None = None, defaults: dict[str, str] | None = None) -> dict[str, Any]:
    """JSON schema for FAE-aligned CIS intake form."""
    defaults = defaults or {}
    miss = set(missing_keys or [])
    if "sequence" in miss:
        miss.add("sequence_text")
        miss.discard("sequence")
    props: dict[str, Any] = {}
    required: list[str] = []
    optional = {"features", "datasheet", "cis"}
    for key, label, hint in CIS_INTAKE_FIELDS:
        if missing_keys is not None and key not in miss:
            continue
        props[key] = {
            "type": "string",
            "title": label,
            "description": hint,
        }
        if defaults.get(key):
            props[key]["default"] = defaults[key]
        if key not in optional:
            required.append(key)
    return {
        "type": "object",
        "properties": props,
        "required": required,
    }


def elicit_cis_intake(
    *,
    read_message: ReadMessage,
    write_message: WriteMessage,
    model: str,
    missing: list[str] | None = None,
    defaults: dict[str, str] | None = None,
) -> dict[str, str]:
    schema = cis_intake_schema(missing, defaults)
    if not schema.get("properties"):
        return {}
    content = elicit_form(
        read_message=read_message,
        write_message=write_message,
        message=f"CIS 方案参数（{model}，对齐 FAE AI 列表外 need_info）：请填写缺项；序列可粘贴全文或填文件路径",
        schema=schema,
    )
    if not content:
        return {}
    out: dict[str, str] = {}
    for key, value in content.items():
        text = str(value or "").strip()
        if text:
            out[str(key)] = text
    return out
