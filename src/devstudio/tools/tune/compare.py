"""Compare two captures from the same board and the same command. No conclusion without both."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from devstudio.adapter.catalog import adapters_root
from devstudio.yamlio import load_yaml

_FIELDS = {"memory": "mem_free_kb", "cpu": "cpu"}


def run_tune(argv: list[str], project: Path | None = None) -> int:
    project = (project or Path.cwd()).resolve()
    if not argv or argv[0] not in _FIELDS or len(argv) < 5:
        sys.stderr.write("用法：devstudio tune memory|cpu --before 目录 --after 目录\n")
        return 2
    kind = argv[0]
    before = after = ""
    args = argv[1:]
    while args:
        item = args.pop(0)
        if item == "--before" and args:
            before = args.pop(0)
        elif item == "--after" and args:
            after = args.pop(0)
        else:
            sys.stderr.write("用法：devstudio tune memory|cpu --before 目录 --after 目录\n")
            return 2
    if not before or not after:
        sys.stderr.write("用法：devstudio tune memory|cpu --before 目录 --after 目录\n")
        return 2
    left = _sample(project, Path(before))
    right = _sample(project, Path(after))
    if left is None or right is None:
        sys.stdout.write("没有两次读数。不写优化结论。\n")
        return 1
    if left["board"] != right["board"]:
        sys.stdout.write("板号不一致。不写优化结论。\n")
        return 1
    if not left["command"] or left["command"] != right["command"]:
        sys.stdout.write("没有同一命令的两次读数。不写优化结论。\n")
        return 1
    field = _FIELDS[kind]
    if left["frame"].get(field) is None or right["frame"].get(field) is None:
        sys.stdout.write("没有两次读数。不写优化结论。\n")
        return 1
    sys.stdout.write(f"板号：{left['board']}\n")
    sys.stdout.write(f"命令：{left['command']}\n")
    sys.stdout.write(f"{field}：{left['frame'][field]} → {right['frame'][field]}\n")
    if not _has_record(project, kind):
        sys.stdout.write("阈值未收录。不写优化结论。\n")
    else:
        sys.stdout.write("已有阈值记录，仍不自动写优化结论。\n")
    return 0


def _sample(project: Path, path: Path) -> dict | None:
    folder = path if path.is_absolute() else project / path
    meta_path = folder / "meta.json"
    metrics = folder / "metrics.jsonl"
    if not meta_path.is_file() or not metrics.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    last = None
    for line in metrics.read_text(encoding="utf-8").splitlines():
        if line.strip():
            last = json.loads(line)
    if not isinstance(meta, dict) or not isinstance(last, dict):
        return None
    if "metrics" not in meta.get("channels", []):
        return None
    return {
        "board": str(meta.get("board", "")),
        "command": str(meta.get("command", "")).strip(),
        "frame": last,
    }


def _has_record(project: Path, kind: str) -> bool:
    config = project / "devstudio.yaml"
    if not config.is_file():
        return False
    release = str(load_yaml(config).get("release_id", ""))
    path = adapters_root() / release / "tune" / f"{kind}.yaml"
    if not path.is_file():
        return False
    loaded = load_yaml(path)
    return isinstance(loaded, dict) and any(value not in (None, "", [], {}) for value in loaded.values())
