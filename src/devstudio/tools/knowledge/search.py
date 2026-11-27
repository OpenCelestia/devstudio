import json
import sys
from pathlib import Path

from devstudio.adapter.catalog import adapters_root
from devstudio.tools.knowledge.index_headers import run_index
from devstudio.tools.knowledge.peripherals import catalog_rows
from devstudio.yamlio import load_yaml


def run_knowledge(argv: list[str], project: Path | None = None) -> int:
    project = (project or Path.cwd()).resolve()
    if not argv:
        sys.stderr.write("用法：devstudio knowledge peripherals|spec|search|api|errata|index\n")
        return 2
    action = argv[0]
    if action == "index":
        return run_index(project)
    if action == "spec":
        return _missing_or_row(project, argv[1:], "规格")
    if action == "search":
        return _search(project, argv[1:])
    if action == "api":
        return _missing_or_row(project, argv[1:], "API")
    if action == "errata":
        return _errata(project, argv[1:])
    sys.stderr.write("用法：devstudio knowledge peripherals|spec|search|api|errata|index\n")
    return 2


def _release(project: Path) -> str:
    path = project / "devstudio.yaml"
    if not path.is_file():
        return ""
    return load_yaml(path).get("release_id", "")


def _search(project: Path, argv: list[str]) -> int:
    query = ""
    args = list(argv)
    while args:
        item = args.pop(0)
        if item == "--query" and args:
            query = args.pop(0)
        elif not item.startswith("--") and not query:
            query = item
    release = _release(project)
    # 外设支持列表走 peripherals，不靠 API 索引全文
    q = query.lower().strip()
    if q and any(token in q for token in ("flash", "spi nor", "spi-nor", "nand", "存储", "spinand")):
        sys.stdout.write(
            "外设 flash 支持列表请用：devstudio knowledge peripherals --type flash\n"
        )
        return 0
    if q and any(token in q for token in ("wifi", "wi-fi", "wlan", "无线")):
        sys.stdout.write(
            "外设 wifi 支持列表请用：devstudio knowledge peripherals --type wifi\n"
        )
        return 0
    index = adapters_root() / release / "index" if release else Path()
    if not query or not index.is_dir() or not any(index.rglob("*.md")):
        sys.stdout.write("未收录\n")
        return 0
    hits = []
    for file in sorted(index.rglob("*.md")):
        text = file.read_text(encoding="utf-8", errors="replace")
        if query.lower() in text.lower():
            line_no = next((n for n, line in enumerate(text.splitlines(), 1) if query.lower() in line.lower()), 1)
            hits.append(f"{file.relative_to(index).as_posix()}:{line_no}")
            if len(hits) >= 5:
                break
    if not hits:
        sys.stdout.write("未收录\n")
        return 0
    for hit in hits:
        sys.stdout.write(hit + "\n")
    return 0


def _missing_or_row(project: Path, argv: list[str], kind: str) -> int:
    model = ""
    args = list(argv)
    while args:
        item = args.pop(0)
        if item == "--model" and args:
            model = args.pop(0)
        elif item == "--symbol" and args:
            model = args.pop(0)
    if kind == "API":
        release = _release(project)
        index = adapters_root() / release / "index" if release else Path()
        if model and index.is_dir():
            for file in sorted(index.rglob("*.md")):
                text = file.read_text(encoding="utf-8", errors="replace")
                if model not in text:
                    continue
                line_no = next((n for n, line in enumerate(text.splitlines(), 1) if model in line), 1)
                sys.stdout.write(f"{file.relative_to(index).as_posix()}:{line_no}\n")
                return 0
        sys.stdout.write("未收录\n")
        return 0
    error, _release_id, rows = catalog_rows(project, None) if (project / "devstudio.yaml").is_file() else ("", "", [])
    if error or not model:
        sys.stdout.write("未收录\n")
        return 0
    row = next((item for item in rows if str(item.get("model", "")).lower() == model.lower()), None)
    if row is None:
        sys.stdout.write("未收录\n")
        return 0
    sys.stdout.write(f"型号：{row.get('model', model)}\n")
    sys.stdout.write(f"分辨率：{row.get('resolution', '')}\n")
    sys.stdout.write(f"帧率：{row.get('fps', '')}\n")
    sys.stdout.write(f"接口：{row.get('interface', '')}\n")
    sys.stdout.write(f"功能：{row.get('features', '')}\n")
    docs = [str(item) for item in (row.get("docs") or []) if str(item) not in ("doc", str(row.get("model", "")))]
    if docs:
        sys.stdout.write("厂商文档：" + "、".join(docs) + "\n")
    else:
        sys.stdout.write("厂商文档：未收录\n")
    return 0


def find_in_index(project: Path, symbol: str) -> str | None:
    release = _release(project)
    index = adapters_root() / release / "index" if release else Path()
    if not symbol or not index.is_dir():
        return None
    for file in sorted(index.rglob("*.md")):
        text = file.read_text(encoding="utf-8", errors="replace")
        if symbol not in text:
            continue
        line_no = next((n for n, line in enumerate(text.splitlines(), 1) if symbol in line), 1)
        return f"{file.relative_to(index).as_posix()}:{line_no}"
    return None


def read_errata(directory: Path, model: str, revision: str = "") -> list[dict]:
    path = directory / "errata.json"
    if not model or not path.is_file():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    found = []
    for item in loaded:
        if not isinstance(item, dict):
            continue
        if str(item.get("model", "")).lower() != model.lower():
            continue
        if revision and str(item.get("revision", "")).lower() != revision.lower():
            continue
        if not str(item.get("text", "")).strip() or not str(item.get("source", "")).strip():
            continue
        found.append(item)
    return found


def _errata(project: Path, argv: list[str]) -> int:
    model = ""
    revision = ""
    args = list(argv)
    while args:
        item = args.pop(0)
        if item == "--model" and args:
            model = args.pop(0)
        elif item == "--revision" and args:
            revision = args.pop(0)
        else:
            sys.stderr.write("用法：devstudio knowledge errata --model 型号 [--revision 修订]\n")
            return 2
    release = _release(project)
    rows = read_errata(adapters_root() / release, model, revision) if release else []
    if not rows:
        sys.stdout.write("未收录\n")
        return 0
    for item in rows:
        sys.stdout.write(f"型号：{item.get('model', model)}\n")
        sys.stdout.write(f"修订：{item.get('revision', '')}\n")
        sys.stdout.write(f"内容：{item.get('text', '')}\n")
        sys.stdout.write(f"出处：{item.get('source', '')}\n")
    return 0
