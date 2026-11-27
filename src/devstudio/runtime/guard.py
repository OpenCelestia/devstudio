"""写入前的共同检查。apply、应用模板和合入都从这里进入。"""
from __future__ import annotations

import re
from pathlib import Path

from devstudio.tools.source.plan_cmd import is_approved

MAX_TEXT_BYTES = 256 * 1024
_PATTERNS = {
    "register": re.compile(r"\b(?:reg(?:ister)?|0x[0-9a-f]{3,})\b", re.I),
    "sensor_id": re.compile(r"\b(?:sensor_id|actual_sensor_id|phy_id)\b", re.I),
    "timing": re.compile(r"\b(?:vts|hts|max_fps|def_fps|mipi_lanes|mclk)\b", re.I),
    "sequence": re.compile(r"\b(?:init_regs?|paralist|reg_info)\b", re.I),
}


def approval_block(project: Path, task_id: str) -> str | None:
    if is_approved(project, task_id):
        return None
    return "已拒绝。计划未批准。审阅后执行 devstudio plan approve。未写入任何文件。\n"


def filename_block(name: str) -> str | None:
    if not name or name in {".", ".."} or Path(name).name != name or ".." in name:
        return "只允许写入任务产物目录下的文件名。\n"
    if name.lower() in {"makefile", "kconfig", "config.mk"}:
        return "Makefile、Kconfig、config.mk 的模块开关只允许应用模板工具修改。\n"
    return None


def text_block(text: str) -> str | None:
    raw = text.encode("utf-8")
    if "\0" in text or len(raw) > MAX_TEXT_BYTES:
        return "拒绝写入。内容不是可审阅的文本，或超过大小限制。\n"
    return None


def scan_numeric(text: str) -> list[dict]:
    found = []
    for line_no, line in enumerate(text.splitlines(), 1):
        kinds = sorted(name for name, pattern in _PATTERNS.items() if pattern.search(line))
        if kinds:
            found.append({"line": line_no, "kinds": kinds, "text": line.strip()[:200]})
    return found


def numeric_note(text: str, label: str) -> str:
    found = scan_numeric(text)
    if not found:
        return ""
    lines = [f"标出数字字段 {label}:{item['line']} {'、'.join(item['kinds'])}" for item in found]
    return "\n".join(lines) + "\n"


def stays_inside(root: Path, path: Path) -> bool:
    try:
        root_real = root.resolve()
        real = path.resolve()
        real.relative_to(root_real)
    except (OSError, ValueError):
        return False
    return True
