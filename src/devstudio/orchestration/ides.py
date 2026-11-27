"""MCP definitions for the IDEs. Files are written under devstudio/ide and referenced from the project-root AGENTS.md.

Add a new editor as one more entry in TARGETS. Never write configs into .cursor, .claude or .vscode.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IdeTarget:
    name: str
    title: str
    filename: str
    kind: str
    json_key: str | None = None
    mcp_shape: str | None = None


TARGETS = (
    IdeTarget("cursor", "Cursor", "cursor.mcp.json", "json", "mcpServers", "cursor"),
    IdeTarget("vscode", "VS Code", "vscode.mcp.json", "json", "servers", "vscode"),
    IdeTarget("claude", "Claude Code", "claude.mcp.json", "json", "mcpServers", "cursor"),
    IdeTarget("codex", "Codex", "codex.config.toml", "toml"),
)
BY_NAME = {item.name: item for item in TARGETS}

# Copies left inside the IDE dirs by older versions. init clears the devstudio entries from them.
LEGACY_MCP = (
    (".cursor/mcp.json", "mcpServers"),
    (".vscode/mcp.json", "servers"),
    (".mcp.json", "mcpServers"),
)
LEGACY_SKILL_DIRS = (".cursor/skills", ".claude/skills", ".github/skills", ".agents/skills")
LEGACY_TOML = ".codex/config.toml"


def usage() -> str:
    names = ",".join(item.name for item in TARGETS)
    return f"--ide {names}|all"


def parse_ides(spec: str) -> list[str] | None:
    """Parse all/both, or names separated by commas, spaces or numbers. Returns None on unrecognized input."""
    text = spec.strip().lower().replace("，", ",")
    if text in ("", "all", "both"):
        return [item.name for item in TARGETS]
    parts = [item.strip() for item in text.replace(",", " ").split() if item.strip()]
    if not parts:
        return None
    seen: list[str] = []
    for part in parts:
        if part.isdigit():
            index = int(part)
            if not 1 <= index <= len(TARGETS):
                return None
            name = TARGETS[index - 1].name
        elif part in BY_NAME:
            name = part
        else:
            return None
        if name not in seen:
            seen.append(name)
    return seen


def selected(names: list[str]) -> list[IdeTarget]:
    return [BY_NAME[name] for name in names]


def describe() -> list[str]:
    lines = ["MCP definitions are written to devstudio/ide and referenced from AGENTS.md. Multiple choices allowed."]
    for index, item in enumerate(TARGETS, 1):
        lines.append(f"  {index}) {item.name:<8} {item.title}: devstudio/ide/{item.filename}")
    lines.append("  Press Enter for all. Names can also be entered, separated by commas or spaces.")
    return lines
