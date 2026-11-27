from pathlib import Path


def load_yaml(path: Path) -> dict:
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        key, sep, value = raw.strip().partition(":")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if sep and value.strip():
            parent[key] = value.strip()
        else:
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
    return root


def dump_yaml(data: dict, indent: int = 0) -> str:
    lines: list[str] = []
    pad = "  " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            lines.append(dump_yaml(value, indent + 1).rstrip("\n"))
        else:
            lines.append(f"{pad}{key}: {value}")
    return "\n".join(lines) + "\n"
