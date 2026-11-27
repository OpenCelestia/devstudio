"""Register a board over Studio TCP 7777. The PC page is `devstudio web`; this command does not need it."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from devstudio.tools.device.frame import DeviceLink
from devstudio.workspace import devices_path

# Same relaunch the PC page uses. Kept here so `devstudio device` does not need the page.
DEFAULT_RELAUNCH = (
    "(sleep 1; killall nova_main ipc_main ipc_home; sleep 1; "
    "/usr/sbin/start_ipc_main.sh --bg) >/tmp/devstudio_relaunch.log 2>&1 &"
)


def resolve_board(project: Path, board: str | None = "", *, ask: bool = True) -> str:
    """Return board IP. Missing board → IDE Ask UI / TTY / optional desktop.

    Never invent or silently pick a board. A single registered device is only the default.
    Agents must use IDE 运行中输入（ASK_UI），not chat prose.
    """
    chosen = (board or "").strip()
    if chosen:
        return chosen
    devices = _load_devices(project)
    boards = [str(item.get("board") or "").strip() for item in devices if item.get("board")]
    default = boards[0] if len(boards) == 1 else ""
    if ask:
        from devstudio.ui_prompt import ask_choice, ask_string, prefer_ide_ask

        # 多块板：先用 IDE/TTY 选择；一块或无登记：直接要 IP
        if len(boards) > 1:
            options = [(b, b) for b in boards]
            options.append(("other", "其它 IP…"))
            picked = ask_choice(
                "DevStudio 板号",
                "请选择已登记板号，或选「其它 IP」后由 Agent 再问一次",
                options,
                default=default,
            )
            if picked is None and prefer_ide_ask():
                return ""
            if picked and picked != "other":
                return picked
        lines = ["请输入板号 IP（默认端口 7777）"]
        if boards:
            lines.append("已登记：" + "、".join(boards))
        typed = ask_string("DevStudio 板号", "\n".join(lines), initial=default)
        if typed is not None:
            return typed or default
        if prefer_ide_ask():
            return ""
    hint = f"（已登记默认 {default}）" if default else ""
    from devstudio.ui_prompt import emit_ask_ui

    emit_ask_ui(
        "BOARD",
        ask_ui="board_ip",
        title="DevStudio 板号",
        prompt=f"请输入板号 IP（例如 192.168.0.8，默认端口 7777）{hint}",
        default=default,
        hint=hint,
    )
    return ""


def run_connect(project: Path, board: str, product: str = "") -> int:
    board = resolve_board(project, board)
    if not board:
        return 1
    try:
        with DeviceLink(board, timeout=10) as link:
            link.ping()
            info = _probe(link)
    except (OSError, TimeoutError, ConnectionError) as exc:
        sys.stdout.write(f"未连接 {board}：{exc}\n")
        sys.stdout.write("板上须运行 application/services/studio（TCP 7777）。看画面用 devstudio web。\n")
        return 1
    path = devices_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    devices = _load_devices(project)
    entry = {"board": board, "product": product or _product_name(project), **info}
    devices = [item for item in devices if isinstance(item, dict) and item.get("board") != board]
    devices.append(entry)
    path.write_text(json.dumps(devices, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(f"已连接 {board}。记录写入 {path}\n")
    sys.stdout.write("通道：Studio TCP 7777（设备端 studio 代理）。看画面用 devstudio web。\n")
    return 0


def _load_devices(project: Path) -> list[dict]:
    path = devices_path(project)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and item.get("board")]


def relaunch_desktop(link: DeviceLink, command: str = "") -> str:
    script = (command or DEFAULT_RELAUNCH).strip()
    if not script:
        return ""
    try:
        link.exec(script, timeout=15)
    except (OSError, TimeoutError, ConnectionError):
        return "已请求重启主程序，应答可能因进程退出而中断。\n"
    return "已重启主程序。\n"


def _probe(link: DeviceLink) -> dict:
    info: dict = {}
    try:
        result = link.exec("grep '\"APP\"' /proc/mtd || true; df -k /mnt/install 2>/dev/null | tail -1 || true", timeout=10)
        out = str(result.get("out") or "")
        for line in out.splitlines():
            if '"APP"' in line and ":" in line:
                name = line.split(":", 1)[0].strip()
                parts = line.split()
                info["app_mtd"] = f"/dev/{name}"
                if len(parts) >= 2:
                    info["usr_size"] = f"0x{parts[1]}"
                break
    except (OSError, TimeoutError, ConnectionError):
        pass
    return info


def _product_name(project: Path) -> str:
    import re

    path = project / "devstudio.yaml"
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^project:\s*\n\s+name:\s*(\S+)", text, re.MULTILINE)
    return match.group(1) if match else ""
