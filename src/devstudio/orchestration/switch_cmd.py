"""Switch the active DevStudio product/SDK context without editing product sources."""
from __future__ import annotations

import sys
from pathlib import Path

from devstudio.orchestration.detect import Scan, scan_project
from devstudio.orchestration.init_cmd import run_init
from devstudio.root import repo_root
from devstudio.yamlio import load_yaml


def run_switch(
    argv: list[str],
    project: Path | None = None,
    lines: list[str] | None = None,
) -> int:
    project = (project or Path.cwd()).resolve()
    if not argv:
        return _interactive_product(project, lines)
    action, args = argv[0], list(argv[1:])
    if action == "list":
        return _list(project)
    if action == "product":
        return _switch_product(project, args, lines)
    if action == "sdk":
        return _switch_sdk(project, args, lines)
    _usage()
    return 2


def _base_scan(project: Path) -> Scan:
    return scan_project(project, repo_root())


def _list(project: Path) -> int:
    scan = _base_scan(project)
    current = _current(project)
    if scan.products:
        sys.stdout.write("Available products:\n")
        for name in scan.products:
            selected = scan_project(project, repo_root(), product_name=name)
            release = selected.config_release or "SDK not declared"
            mark = " *current" if current.get("product", {}).get("name") == name else ""
            registered = selected.sdks[0].registered if len(selected.sdks) == 1 else False
            status = "registered" if registered else "unavailable or unregistered"
            sys.stdout.write(f"  {name:<24} {release:<32} {status}{mark}\n")
    if scan.sdks:
        sys.stdout.write("Available SDKs:\n")
        for hit in scan.sdks:
            mark = " *current" if current.get("release_id") == hit.release_id else ""
            status = "registered" if hit.registered else "unregistered"
            sys.stdout.write(f"  {hit.release_id:<40} {status}{mark}\n")
    if not scan.products and not scan.sdks:
        sys.stderr.write(scan.blocked or "No switchable products or SDKs found.")
        sys.stderr.write("\n")
        return 1
    return 0


def _interactive_product(project: Path, lines: list[str] | None) -> int:
    scan = _base_scan(project)
    if not scan.products:
        _usage()
        return 2
    sys.stdout.write("Choose a product (the product uniquely determines the SDK):\n")
    for index, name in enumerate(scan.products, 1):
        selected = scan_project(project, repo_root(), product_name=name)
        sys.stdout.write(f"  {index}) {name}  ->  {selected.config_release or 'SDK not declared'}\n")
    picked = _answer(lines, "Product number or name: ").strip()
    name = _pick(scan.products, picked)
    if name is None:
        sys.stdout.write("No valid product selected. Not switched.\n")
        return 1
    return run_init(["--product", name], project=project, lines=lines)


def _switch_product(project: Path, args: list[str], lines: list[str] | None) -> int:
    if len(args) > 1:
        _usage()
        return 2
    scan = _base_scan(project)
    if not scan.products:
        sys.stderr.write("No switchable Celestia product projects in the current directory.\n")
        return 1
    if args:
        name = args[0]
    else:
        for index, item in enumerate(scan.products, 1):
            sys.stdout.write(f"  {index}) {item}\n")
        name = _pick(scan.products, _answer(lines, "Product number or name: ").strip()) or ""
    if name not in scan.products:
        sys.stderr.write(f"No such product: project/{name}/config.mk.\n")
        return 1
    return run_init(["--product", name], project=project, lines=lines)


def _switch_sdk(project: Path, args: list[str], lines: list[str] | None) -> int:
    if not args:
        sys.stderr.write("Missing SDK version directory.\n")
        _usage()
        return 2
    sdk_path = Path(args.pop(0))
    if args:
        _usage()
        return 2
    if not sdk_path.is_absolute():
        sdk_path = (project / sdk_path).resolve()
    current = _current(project)
    state = current.get("state", "")
    base = _base_scan(project)
    if state == "sdk_celestia" or base.products:
        sys.stderr.write(
            "Celestia projects cannot switch the SDK independently. Use devstudio switch product <name>; "
            "the SDK is determined by that product's config.mk.\n"
        )
        return 1
    if state == "sdk_only":
        return run_init([], project=sdk_path, lines=lines)
    if state == "sdk_customer_app":
        return run_init(
            ["--state", "sdk_customer_app", "--sdk", str(sdk_path)],
            project=project,
            lines=lines,
        )
    sys.stderr.write("Project is not initialized yet; cannot determine how to switch the SDK. Run devstudio init first.\n")
    return 1


def _current(project: Path) -> dict:
    path = project / "devstudio.yaml"
    if not path.is_file():
        return {}
    loaded = load_yaml(path)
    return loaded if isinstance(loaded, dict) else {}


def _pick(items: list[str], value: str) -> str | None:
    if value.isdigit() and 1 <= int(value) <= len(items):
        return items[int(value) - 1]
    return value if value in items else None


def _answer(lines: list[str] | None, prompt: str) -> str:
    sys.stdout.write(prompt)
    if lines is not None:
        value = lines.pop(0) if lines else ""
        sys.stdout.write(value + "\n")
        return value
    return input()


def _usage() -> None:
    sys.stderr.write(
        "Usage: devstudio switch [list]\n"
        "       devstudio switch product [<product>]\n"
        "       devstudio switch sdk <SDK version dir>  # sdk_only / sdk_customer_app only\n"
    )
