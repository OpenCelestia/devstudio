"""Index public SDK headers by function name. Header text is not copied."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from devstudio.adapter.catalog import adapters_root
from devstudio.tools.source.sensor_flow import _sdk_root
from devstudio.yamlio import load_yaml

_DECL = re.compile(
    r"^[ \t]*(?:extern[ \t]+)?(?:[\w]+[ \t]+)+(?:\*+[ \t]*)*([A-Za-z_]\w*)\s*\(",
    re.M,
)
_SKIP = {"if", "for", "while", "switch", "return", "defined", "sizeof"}
_PUBLIC = (Path("platform/include"), Path("os/driver/external/include"))


def run_index(project: Path) -> int:
    config = project / "devstudio.yaml"
    if not config.is_file():
        sys.stderr.write("当前目录没有 devstudio.yaml。\n")
        return 1
    release = str(load_yaml(config).get("release_id", ""))
    sdk = _sdk_root(project)
    if not release or sdk is None:
        sys.stdout.write("SDK 未解出。未写索引。\n")
        return 1
    dest = adapters_root() / release / "index"
    count, symbols = index_headers(sdk, dest)
    if count == 0:
        sys.stdout.write("公开头文件未找到。未写索引。\n")
        return 1
    sys.stdout.write(f"已索引 {count} 个头文件，{symbols} 个函数名。目录：{dest}\n")
    sys.stdout.write("未复制头文件正文。\n")
    return 0


def index_headers(sdk: Path, dest: Path) -> tuple[int, int]:
    files = 0
    symbols = 0
    for root in _PUBLIC:
        base = sdk / root
        if not base.is_dir():
            continue
        for header in sorted(base.rglob("*.h")):
            if "sample" in header.parts:
                continue
            names = _names(header)
            relative = header.relative_to(sdk).as_posix()
            target = dest / Path(relative).with_suffix(".md")
            target.parent.mkdir(parents=True, exist_ok=True)
            lines = [f"# {relative}"]
            lines.extend(names)
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
            files += 1
            symbols += len(names)
    return files, symbols


def _names(header: Path) -> list[str]:
    text = header.read_text(encoding="utf-8", errors="replace")
    found = []
    for name in _DECL.findall(text):
        if name in _SKIP or name in found:
            continue
        found.append(name)
    return found
