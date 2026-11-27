import json
import os
import shutil
import sys
from pathlib import Path

from devstudio.orchestration.detect import Scan, scan_project
from devstudio.orchestration.ides import (
    LEGACY_MCP,
    LEGACY_SKILL_DIRS,
    LEGACY_TOML,
    TARGETS,
    describe,
    parse_ides,
    selected,
    usage,
)
from devstudio.orchestration.render import GROUPS, SKILLS, agents_body, project_yaml, render_scan, skill_hint
from devstudio.root import repo_root
from devstudio.yamlio import dump_yaml, load_yaml

BEGIN = "<!-- devstudio:begin -->"
END = "<!-- devstudio:end -->"
STATES = ("sdk_celestia", "sdk_customer_app", "sdk_only")


def run_init(argv: list[str], project: Path | None = None, lines: list[str] | None = None) -> int:
    install = repo_root()
    project = (project or Path.cwd()).resolve()
    yes, sdk_path, state_arg, product_arg, ide_arg, sync_product = False, None, None, None, None, None
    sync_force = False
    rest = []
    args = list(argv)
    while args:
        item = args.pop(0)
        if item == "--yes":
            yes = True
        elif item == "--sdk":
            sdk_path = Path(args.pop(0))
        elif item == "--state":
            state_arg = args.pop(0)
        elif item == "--product":
            product_arg = args.pop(0)
        elif item == "--sync-product":
            if not args:
                sys.stderr.write("Usage: devstudio init --sync-product <product>\n")
                return 2
            sync_product = args.pop(0)
        elif item == "--force":
            sync_force = True
        elif item == "--ide":
            ide_arg = args.pop(0)
        elif item == "--remove":
            return _remove(project)
        else:
            rest.append(item)
    if rest:
        sys.stderr.write(
            "Usage: devstudio init [--yes] [--sdk <path>] [--state <state>] [--product <name>] "
            f"[--sync-product <product>] [{usage()}] [--remove]\n"
        )
        return 2
    if sync_product:
        return _sync_product(install, project, sync_product, force=sync_force)
    ides = parse_ides(ide_arg) if ide_arg else None
    if ide_arg and ides is None:
        sys.stderr.write(f"Unrecognized IDE. Options: {usage()}.\n")
        return 2
    if state_arg and state_arg not in STATES:
        sys.stderr.write("State must be one of sdk_celestia, sdk_customer_app, sdk_only.\n")
        return 2
    scan = scan_project(project, install, sdk_path, product_arg)
    if scan.product_required:
        sys.stdout.write("Multiple product projects found; pick the product to use for this run:\n")
        for index, name in enumerate(scan.products, 1):
            sys.stdout.write(f"  {index}) {name}\n")
        picked = _take(lines, "Product number or name: ").strip()
        if picked.isdigit() and 1 <= int(picked) <= len(scan.products):
            picked = scan.products[int(picked) - 1]
        if picked not in scan.products:
            sys.stdout.write("No valid product selected. No files written.\n")
            return 1
        scan = scan_project(project, install, sdk_path, picked)
    host = "wsl" if sys.platform == "win32" else "local"
    customer = None
    state = state_arg
    if scan.blocked and not scan.mixed:
        sys.stdout.write(render_scan(scan, None, host))
        return 1
    if scan.mixed and state is None:
        sys.stdout.write(render_scan(scan, None, host))
        picked = _take(lines, "Choose [1/2/3]: ")
        mapping = {"1": "sdk_celestia", "2": "sdk_customer_app", "3": "sdk_only"}
        state = mapping.get(picked.strip())
        if state is None:
            sys.stdout.write("No state selected. No files written.\n")
            return 1
    if state is None:
        state = scan.suggested
    if state == "sdk_customer_app":
        if scan.mixed:
            sys.stdout.write(f"Suggested state: {state}\n")
        else:
            sys.stdout.write(render_scan(scan, state, host, part="status"))
        customer, host = _ask_customer(scan, lines, host)
        sys.stdout.write(render_scan(scan, state, host, customer, part="plan"))
    else:
        if sys.platform == "darwin" and not yes:
            host, customer = _ask_mac_build(lines, host)
        elif sys.platform == "darwin" and yes:
            # Non-interactive default still writes local; doctor prompts to switch to ssh after install
            host = "local"
        if scan.mixed:
            sys.stdout.write(f"Suggested state: {state}\n")
            sys.stdout.write(render_scan(scan, state, host, customer, part="plan"))
        else:
            sys.stdout.write(render_scan(scan, state, host, customer))
    if not yes:
        answer = _take(lines, "Confirm? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            sys.stdout.write("Not confirmed. No files written.\n")
            return 0
    ides = ides or (parse_ides("all") if yes else _ask_ide(lines))
    if ides is None:
        sys.stdout.write("No IDE selected. No files written.\n")
        return 1
    written = _write(install, scan, state, host, customer, ides)
    sys.stdout.write("Wrote devstudio.yaml  AGENTS.md  " + "  ".join(written) + "\n")
    if sys.platform == "darwin":
        sys.stdout.write("Next: devstudio doctor  (checks the Mac + UTM/SSH Linux build environment)\n")
    return 0


def _ask_customer(scan: Scan, lines: list[str] | None, host: str) -> tuple[dict, str]:
    default_root = str(scan.project)
    app_root = _take(lines, f"  App root dir [{default_root}]: ") or default_root
    hal_interface = _take(lines, "  HAL interface header dir: ")
    hal_out = _take(lines, "  HAL output dir: ")
    command = _take(lines, "  Build command: ")
    if sys.platform == "darwin":
        host_hint = "ssh/local"
        default_pick = "ssh"
    else:
        host_hint = "local/wsl/ssh"
        default_pick = host
    picked = _take(lines, f"  Build host [{host_hint}]: ") or default_pick
    customer = {
        "app_root": app_root,
        "hal_interface": hal_interface,
        "hal_out": hal_out,
        "command": command,
    }
    if picked == "ssh" or picked.startswith("ssh "):
        customer["ssh_host"] = _take(lines, "  SSH host alias (~/.ssh/config): ")
        customer["ssh_workdir"] = _take(lines, "  Remote project dir: ")
    return customer, picked


def _ask_mac_build(lines: list[str] | None, host: str) -> tuple[str, dict | None]:
    """macOS: chip cross-compilation must run on Linux (UTM Ubuntu / SSH); default to ssh."""
    sys.stdout.write(
        "macOS cannot run the chip cross toolchain directly. Choose a build host:\n"
        "  ssh   — Ubuntu in UTM/Parallels, or a Linux server (recommended)\n"
        "  local — only if this machine already has the Linux toolchain (rare)\n"
    )
    picked = (_take(lines, "  Build host [ssh/local]: ") or "ssh").strip()
    if picked != "ssh" and not picked.startswith("ssh "):
        return picked or host, None
    info = {
        "ssh_host": _take(lines, "  SSH host alias (~/.ssh/config, e.g. utm-ubuntu): "),
        "ssh_workdir": _take(lines, "  Remote project dir (path inside the VM): "),
    }
    return "ssh", info


def _take(lines: list[str] | None, prompt: str) -> str:
    sys.stdout.write(prompt)
    if lines is not None:
        value = lines.pop(0) if lines else ""
        sys.stdout.write(value + "\n")
        return value
    return input()


def _ask_ide(lines: list[str] | None) -> list[str] | None:
    if lines is None and not sys.stdin.isatty():
        return parse_ides("all")
    sys.stdout.write("\n".join(describe()) + "\n")
    picked = _take(lines, "Choose: ").strip()
    if not picked:
        return parse_ides("all")
    return parse_ides(picked)


def _recorded_product(data: dict) -> str:
    project = data.get("project")
    if isinstance(project, dict):
        return str(project.get("name") or "")
    return ""


def _sync_product(install: Path, project: Path, product: str, force: bool = False) -> int:
    """First build runs init. Later builds rewrite AGENTS.md only when the product name changes.

    force=True rewrites AGENTS.md even when the product name is unchanged.
    """
    yaml_path = project / "devstudio.yaml"
    agents_path = project / "AGENTS.md"
    if not yaml_path.is_file() or not agents_path.is_file():
        return run_init(["--yes", "--product", product], project=project)
    try:
        loaded = load_yaml(yaml_path)
    except OSError:
        return run_init(["--yes", "--product", product], project=project)
    if not isinstance(loaded, dict):
        return run_init(["--yes", "--product", product], project=project)
    current = _recorded_product(loaded)
    if current == product and not force:
        sys.stdout.write(f"Product is still {product}; AGENTS.md left unchanged.\n")
        return 0
    scan = scan_project(project, install, None, product)
    host = "wsl" if sys.platform == "win32" else "local"
    build = loaded.get("build")
    if isinstance(build, dict) and build.get("host"):
        host = str(build["host"])
    if scan.blocked and not scan.mixed:
        sys.stdout.write(render_scan(scan, None, host))
        return 1
    state = str(loaded.get("state") or scan.suggested or "")
    if state not in STATES:
        state = scan.suggested
    customer = loaded.get("customer") if isinstance(loaded.get("customer"), dict) else None
    if isinstance(build, dict) and (host == "ssh" or host.startswith("ssh")):
        customer = dict(customer or {})
        customer.setdefault("ssh_host", str(build.get("ssh_host") or ""))
        customer.setdefault("ssh_workdir", str(build.get("ssh_workdir") or ""))
    ide_home = _ide_home(project, install)
    ide_paths = [_display(project, path) for path in sorted(ide_home.glob("*")) if path.is_file()] if ide_home.is_dir() else []
    _write_agents(
        project,
        agents_body(scan, state, host, customer, _skill_paths(install, project, SKILLS[state]), ide_paths),
    )
    project_info = loaded.get("project")
    if not isinstance(project_info, dict):
        project_info = {}
        loaded["project"] = project_info
    project_info["name"] = product
    if not isinstance(build, dict):
        build = {}
        loaded["build"] = build
    build["command"] = f"./build.sh --product-name {product}" if state == "sdk_celestia" else build.get("command", "")
    yaml_path.write_text(dump_yaml(loaded), encoding="utf-8")
    if force and current == product:
        sys.stdout.write(f"Force-updated AGENTS.md (product {product}).\n")
    else:
        sys.stdout.write(f"Product changed from {current or 'unrecorded'} to {product}; AGENTS.md updated.\n")
    return 0


def _ide_home(project: Path, install: Path) -> Path:
    """Write into the project itself when it contains this devstudio; otherwise write under the project's devstudio/ide."""
    try:
        install.resolve().relative_to(project.resolve())
        return install / "ide"
    except ValueError:
        return project / "devstudio" / "ide"


def _display(project: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _skill_paths(install: Path, project: Path, names: list[str]) -> list[tuple[str, str]]:
    found: dict[str, Path] = {}
    for top in ("celestia", "sdk"):
        base = install / top
        if base.is_dir():
            # peripherals/cis/cis-porting etc. may nest one level deeper; dedupe by skill dir name
            for item in sorted(base.rglob("SKILL.md")):
                found.setdefault(item.parent.name, item)
    paths = []
    for name in names:
        chosen = found.get(name, install / "celestia" / name / "SKILL.md")
        paths.append((_display(project, chosen), skill_hint(chosen)))
    return paths


def _write(install: Path, scan: Scan, state: str, host: str, customer: dict | None, ides: list[str] | None = None) -> list[str]:
    project = scan.project
    names = ides or parse_ides("all") or []
    home = _ide_home(project, install)
    groups = GROUPS[state]
    written = _write_ide_files(home, names, groups)
    _drop_unselected(home, names)
    _clear_legacy(project)
    ide_paths = [_display(project, home / item.filename) for item in selected(names)]
    (project / "devstudio.yaml").write_text(
        dump_yaml(project_yaml(scan, state, host, customer)), encoding="utf-8"
    )
    _write_agents(
        project,
        agents_body(scan, state, host, customer, _skill_paths(install, project, SKILLS[state]), ide_paths),
    )
    _write_pointer(project / "CLAUDE.md")
    written.insert(0, "CLAUDE.md")
    return written


def _write_ide_files(home: Path, names: list[str], groups: list[str]) -> list[str]:
    home.mkdir(parents=True, exist_ok=True)
    written = []
    for item in selected(names):
        path = home / item.filename
        if item.kind == "json" and item.json_key and item.mcp_shape:
            servers = _vscode_servers(groups) if item.mcp_shape == "vscode" else _cursor_servers(groups)
            path.write_text(
                json.dumps({item.json_key: servers}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        elif item.kind == "toml":
            _write_codex(path, groups)
        written.append(f"devstudio/ide/{item.filename}")
    return written


def _write_agents(project: Path, body: str) -> None:
    block = f"{BEGIN}\n{body}{END}\n"
    path = project / "AGENTS.md"
    if not path.exists():
        path.write_text(block, encoding="utf-8")
        return
    text = path.read_text(encoding="utf-8")
    if BEGIN in text and END in text:
        pre, rest = text.split(BEGIN, 1)
        _, post = rest.split(END, 1)
        path.write_text(pre + block + post.lstrip("\n"), encoding="utf-8")
        return
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text + "\n" + block, encoding="utf-8")


def _command() -> str:
    return os.environ.get("DEVSTUDIO_BIN") or "devstudio"


def _cursor_servers(groups: list[str]) -> dict:
    command = _command()
    return {f"devstudio-{name}": {"command": command, "args": ["mcp", "--group", name]} for name in groups}


def _vscode_servers(groups: list[str]) -> dict:
    command = _command()
    return {
        f"devstudio-{name}": {"type": "stdio", "command": command, "args": ["mcp", "--group", name]}
        for name in groups
    }


def _write_codex(path: Path, groups: list[str]) -> None:
    command = _command().replace("\\", "\\\\")
    lines = ["# devstudio:begin"]
    for name in groups:
        lines.append(f"[mcp_servers.devstudio-{name}]")
        lines.append(f'command = "{command}"')
        lines.append(f'args = ["mcp", "--group", "{name}"]')
        lines.append("")
    lines.append("# devstudio:end")
    block = "\n".join(lines) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(block, encoding="utf-8")
        return
    text = path.read_text(encoding="utf-8")
    begin, end = "# devstudio:begin", "# devstudio:end"
    if begin in text and end in text:
        pre, rest = text.split(begin, 1)
        _, post = rest.split(end, 1)
        path.write_text(pre + block + post.lstrip("\n"), encoding="utf-8")
        return
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + "\n" + block, encoding="utf-8")


def _write_pointer(path: Path) -> None:
    pointer = "@AGENTS.md\n"
    if not path.exists():
        path.write_text(pointer, encoding="utf-8")
        return
    text = path.read_text(encoding="utf-8")
    if "@AGENTS.md" in text:
        return
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + pointer, encoding="utf-8")


def _remove(project: Path) -> int:
    home = _ide_home(project, repo_root())
    for item in TARGETS:
        path = home / item.filename
        if path.is_file():
            path.unlink()
    _clear_legacy(project)
    agents = project / "AGENTS.md"
    if agents.exists() and BEGIN in agents.read_text(encoding="utf-8"):
        text = agents.read_text(encoding="utf-8")
        pre, rest = text.split(BEGIN, 1)
        _, post = rest.split(END, 1)
        agents.write_text(pre + post.lstrip("\n"), encoding="utf-8")
    sys.stdout.write("Removed devstudio config from the IDE.\n")
    return 0


def _drop_unselected(home: Path, names: list[str]) -> None:
    keep = {item.filename for item in selected(names)}
    for item in TARGETS:
        path = home / item.filename
        if item.filename not in keep and path.is_file():
            path.unlink()


def _clear_legacy(project: Path) -> None:
    for relative, key in LEGACY_MCP:
        path = project / relative
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        servers = data.get(key, {})
        data[key] = {name: value for name, value in servers.items() if not str(name).startswith("devstudio-")}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _clear_marked(project / LEGACY_TOML, "# devstudio:begin", "# devstudio:end")
    for name in {skill for names in SKILLS.values() for skill in names}:
        for relative in LEGACY_SKILL_DIRS:
            dest = project / relative / name
            if dest.exists():
                _rmtree(dest)


def _rmtree(path: Path) -> None:
    def onexc(func, target, _exc) -> None:
        try:
            os.chmod(target, 0o700)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onexc=onexc)
    if path.exists():
        sys.stdout.write(f"Failed to delete stale copy: {path}\n")


def _clear_marked(path: Path, begin: str, end: str) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    if begin not in text or end not in text:
        return
    pre, rest = text.split(begin, 1)
    _, post = rest.split(end, 1)
    path.write_text(pre + post.lstrip("\n"), encoding="utf-8")
