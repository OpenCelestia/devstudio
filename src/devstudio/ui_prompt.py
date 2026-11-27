"""User prompts for DevStudio.

Priority (Agent / IDE first):
  1. IDE 运行中选择：输出 NEED_* ASK_UI=…，由 Agent 用当前 IDE 原生控件展示
     （Cursor AskQuestion；MCP 侧缺板号用 elicitation/create 聊天内表单；
      VS Code/Copilot 聊天内选项；Claude Code 选择等）
  2. 交互终端：TTY 输入
  3. 仅当显式 DEVSTUDIO_UI=desktop 时：桌面 tkinter（Agent/Cursor 禁止走这条）

禁止用聊天正文「请回复批准 / 请发板号」干等。
禁止在 Cursor/VS Code Agent 会话里设 DEVSTUDIO_UI=desktop 弹系统框。
强制模式：环境变量 DEVSTUDIO_UI=ide|tty|desktop
"""
from __future__ import annotations

import os
import sys
from typing import Literal, Sequence


Choice = Literal["approve", "reject", "edit"]
UiMode = Literal["ide", "tty", "desktop"]


def ui_mode() -> UiMode:
    forced = (os.environ.get("DEVSTUDIO_UI") or "").strip().lower()
    if forced in ("ide", "tty", "desktop"):
        return forced  # type: ignore[return-value]
    # MCP / Agent 调工具时 stdin 通常不是 TTY → 走 IDE 原生选择
    if not sys.stdin.isatty():
        return "ide"
    # Cursor / VS Code Agent 偶发仍挂着 TTY，用常见环境变量识别
    for key in (
        "CURSOR_AGENT",
        "CURSOR_TRACE_ID",
        "COMPOSER_SESSION_ID",
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
        "DEVSTUDIO_IN_AGENT",
    ):
        if os.environ.get(key):
            return "ide"
    return "tty"


def prefer_ide_ask() -> bool:
    return ui_mode() == "ide"


def emit_ask_ui(
    kind: str,
    *,
    ask_ui: str,
    title: str,
    prompt: str,
    options: Sequence[tuple[str, str]] | None = None,
    fields: Sequence[tuple[str, str, str]] | None = None,
    default: str = "",
    hint: str = "",
    retry: str = "",
) -> None:
    """Print a machine-readable ASK_UI block for the Agent to render in the IDE.

    fields: optional (id, label, hint) rows for multi-field Ask / elicitation forms
    (CIS FAE need_info、板号、批准等一律走此通道)。
    """
    lines = [
        f"NEED_{kind} ASK_UI={ask_ui}{hint}",
        f"TITLE: {title}",
        f"PROMPT: {prompt}",
    ]
    if default:
        lines.append(f"DEFAULT: {default}")
    if options:
        lines.append("OPTIONS:")
        for oid, label in options:
            lines.append(f"- id={oid} label={label}")
    if fields:
        lines.append("FIELDS:")
        for fid, label, field_hint in fields:
            extra = f" hint={field_hint}" if field_hint else ""
            lines.append(f"- id={fid} label={label}{extra}")
    if retry:
        lines.append(f"RETRY: {retry}")
    lines.append(
        "IDE: show this with the IDE in-chat Ask / input control; "
        "localize TITLE/PROMPT/OPTIONS to the user's language (EN or 中文) from the latest user message. "
        "(Cursor: AskQuestion; MCP: elicitation/create; VS Code/Copilot: chat options; Claude Code: choose.) "
        "No desktop tkinter; do not wait for typed replies in chat only. | "
        "用运行中 Ask 展示，并按用户最新一句语言切换中英文标签。"
    )
    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()


def emit_cis_intake_ask(model: str, missing_labels: Sequence[str], *, retry: str) -> None:
    """Ask UI for FAE-aligned CIS out-of-list parameters + sequence paste/path."""
    from devstudio.mcp.elicitation import CIS_INTAKE_FIELDS

    emit_ask_ui(
        "INPUT",
        ask_ui="cis_intake",
        title=f"CIS 方案参数 · {model}",
        prompt=(
            "列表外型号，对齐 FAE AI need_info：用运行中 Ask 补齐下列项。"
            "寄存器序列可粘贴全文，或填本地 ini/txt 路径；Datasheet 可选。"
            + ("\n缺：" + "、".join(missing_labels) if missing_labels else "")
        ),
        fields=CIS_INTAKE_FIELDS,
        retry=retry,
    )


def ask_yes_no(title: str, message: str, *, default: bool = False) -> bool | None:
    """Yes/No. ide→ASK_UI+None；tty→stdin；desktop→tkinter；不可用→None。"""
    mode = ui_mode()
    if mode == "ide":
        emit_ask_ui(
            "CONFIRM",
            ask_ui="yes_no",
            title=title,
            prompt=message,
            options=(
                ("yes", "Yes / 批准"),
                ("no", "No / 拒绝"),
            ),
            default="yes" if default else "no",
        )
        return None
    if mode == "tty":
        if not sys.stdin.isatty():
            return None
        suffix = "[Y/n] " if default else "[y/N] "
        sys.stdout.write(f"{title}\n{message}\n{suffix}")
        sys.stdout.flush()
        try:
            raw = sys.stdin.readline().strip().lower()
        except EOFError:
            return None
        if not raw:
            return default
        return raw in ("y", "yes")
    return _desktop_yes_no(title, message, default=default)


def ask_string(title: str, prompt: str, *, initial: str = "") -> str | None:
    """Text input. ide→ASK_UI+None；tty→stdin；desktop→tkinter；不可用→None。"""
    mode = ui_mode()
    if mode == "ide":
        emit_ask_ui(
            "INPUT",
            ask_ui="text",
            title=title,
            prompt=prompt,
            default=initial or "",
        )
        return None
    if mode == "tty":
        if not sys.stdin.isatty():
            return None
        label = f"{prompt}"
        if initial:
            label += f" [{initial}]"
        label += ": "
        sys.stdout.write(label)
        sys.stdout.flush()
        try:
            raw = sys.stdin.readline().strip()
        except EOFError:
            return None
        return raw if raw else initial
    return _desktop_string(title, prompt, initial=initial)


def ask_choice(
    title: str,
    prompt: str,
    options: Sequence[tuple[str, str]],
    *,
    default: str = "",
) -> str | None:
    """Single choice. Returns option id, or None if deferred to IDE / cancelled."""
    if not options:
        return None
    mode = ui_mode()
    if mode == "ide":
        emit_ask_ui(
            "CHOICE",
            ask_ui="choice",
            title=title,
            prompt=prompt,
            options=options,
            default=default,
        )
        return None
    if mode == "tty":
        if not sys.stdin.isatty():
            return None
        sys.stdout.write(f"{title}\n{prompt}\n")
        for index, (oid, label) in enumerate(options, 1):
            mark = " *" if oid == default else ""
            sys.stdout.write(f"  {index}) {label}  [{oid}]{mark}\n")
        sys.stdout.write("请选择编号或 id：")
        sys.stdout.flush()
        try:
            raw = sys.stdin.readline().strip()
        except EOFError:
            return None
        if not raw:
            return default or None
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        for oid, _label in options:
            if raw == oid:
                return oid
        return None
    # desktop: numbered messagebox is poor; fall back to string
    labels = "\n".join(f"  {oid} = {label}" for oid, label in options)
    typed = _desktop_string(title, f"{prompt}\n{labels}\n请输入 id", initial=default)
    if typed is None:
        return None
    typed = typed.strip()
    ids = {oid for oid, _ in options}
    return typed if typed in ids else None


def confirm_or_tty(title: str, message: str, *, tty_prompt: str) -> bool:
    """Approve helper. ide→ASK_UI+False（等 Agent 带 --confirm 重试）；tty/desktop 直接答。"""
    answered = ask_yes_no(title, message)
    if answered is not None:
        return answered
    if ui_mode() == "ide":
        return False
    if sys.stdin.isatty() and ui_mode() == "tty":
        sys.stdout.write(tty_prompt)
        sys.stdout.flush()
        try:
            return sys.stdin.readline().strip().lower() in ("y", "yes")
        except EOFError:
            return False
    emit_ask_ui(
        "CONFIRM",
        ask_ui="yes_no",
        title=title,
        prompt=message,
        options=(("yes", "Yes / 批准"), ("no", "No / 拒绝")),
    )
    return False


def ask_plan_choice(task_id: str, summary: str) -> Choice | None:
    """Approve / reject for a pending plan. None if deferred to IDE."""
    message = (
        f"Plan / 计划 {task_id}\n\n{summary}\n\n"
        "Approve and continue? / 是否批准并继续执行？"
    )
    answered = ask_yes_no("DevStudio Approve / 批准计划", message, default=False)
    if answered is None:
        return None
    return "approve" if answered else "reject"


def _desktop_yes_no(title: str, message: str, *, default: bool = False) -> bool | None:
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception:
        return None
    try:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        result = messagebox.askyesno(title, message, parent=root)
        root.destroy()
        return bool(result)
    except Exception:
        return None


def _desktop_string(title: str, prompt: str, *, initial: str = "") -> str | None:
    try:
        import tkinter as tk
        from tkinter import simpledialog
    except Exception:
        return None
    try:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        value = simpledialog.askstring(title, prompt, initialvalue=initial or "", parent=root)
        root.destroy()
    except Exception:
        return None
    if value is None:
        return ""
    return value.strip()
