import json
import sys
from pathlib import Path

from devstudio.orchestration.detect import split_release
from devstudio.adapter.catalog import adapters_root
from devstudio.tools.knowledge.peripherals import SOURCE_LABEL, catalog_rows
from devstudio.yamlio import load_yaml


def run_driver(argv: list[str], project: Path | None = None) -> int:
    project = (project or Path.cwd()).resolve()
    if argv and argv[0] == "diff":
        return _diff(project, argv[1:])
    if len(argv) < 2 or argv[0] != "locate" or argv[1] != "--model" or len(argv) < 3:
        sys.stderr.write(
            "用法：devstudio driver locate --model 型号\n"
            "      devstudio driver diff --model 型号 --against release_id\n"
        )
        return 2
    model = argv[2]
    error, release_id, rows = catalog_rows(project)
    if error:
        sys.stderr.write(error + "\n")
        return 1
    row = next((item for item in rows if str(item.get("model", "")).lower() == model.lower()), None)
    if row is None:
        sys.stdout.write(f"{model} 不在 {release_id} 的支持列表里。\n")
        return 1
    source = SOURCE_LABEL.get(row.get("source"), row.get("source", ""))
    package = row.get("package") or "无"
    sys.stdout.write(f"型号：{row['model']}\n")
    sys.stdout.write(f"来源：{source}\n")
    sys.stdout.write(f"调试包：{package}\n")
    if row.get("package"):
        packed = adapters_root() / release_id / "packages" / str(row["package"])
        if packed.is_file():
            sys.stdout.write(f"包文件：{packed}\n")
        else:
            sys.stdout.write("包文件：未收录\n")
    else:
        sys.stdout.write("包文件：SDK 内置，需在当前 SDK 的 driver_src 中定位。\n")
    return 0


def _diff(project: Path, argv: list[str]) -> int:
    model = ""
    other = ""
    args = list(argv)
    while args:
        item = args.pop(0)
        if item == "--model" and args:
            model = args.pop(0)
        elif item == "--against" and args:
            other = args.pop(0)
        else:
            sys.stderr.write("用法：devstudio driver diff --model 型号 --against release_id\n")
            return 2
    config = project / "devstudio.yaml"
    if not config.is_file() or not model or not other:
        sys.stderr.write("用法：devstudio driver diff --model 型号 --against release_id\n")
        return 2
    current = str(load_yaml(config).get("release_id", ""))
    sys.stdout.write(f"型号：{model}\n")
    sys.stdout.write(f"当前：{current}\n")
    sys.stdout.write(f"对照：{other}\n")
    left = _rows(current, model)
    right = _rows(other, model)
    for label, key in (
        ("分辨率", "resolution"),
        ("接口", "interface"),
        ("帧率", "fps"),
        ("CIS", "cis_modes"),
        ("ISP conf", "confs"),
    ):
        sys.stdout.write(f"{label}：{_show(left, key)} → {_show(right, key)}\n")
    sys.stdout.write("API：未收录\n")
    sys.stdout.write("I2C：未收录\n")
    sys.stdout.write("时钟：未收录\n")
    sys.stdout.write("未改两个 SDK。\n")
    return 0


def _rows(release_id: str, model: str) -> list[dict]:
    path = adapters_root() / release_id / "catalog.json"
    if not path.is_file():
        return []
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(loaded, dict):
        family, _version = split_release(release_id)
        rows = loaded.get(family, [])
    else:
        rows = loaded
    return [row for row in rows if str(row.get("model", "")).lower() == model.lower()]


def _show(rows: list[dict], key: str) -> str:
    if not rows:
        return "未收录"
    values = []
    for row in rows:
        value = row.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value if str(item))
        elif value:
            values.append(str(value))
    unique = []
    for item in values:
        if item not in unique:
            unique.append(item)
    return "；".join(unique) if unique else "未收录"
