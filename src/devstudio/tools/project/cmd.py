import sys
from pathlib import Path

from devstudio.workspace import data_root
from devstudio.yamlio import load_yaml


def run_project(argv: list[str], project: Path | None = None) -> int:
    if argv != ["info"]:
        sys.stderr.write("用法：devstudio project info\n")
        return 2
    project = (project or Path.cwd()).resolve()
    path = project / "devstudio.yaml"
    if not path.is_file():
        sys.stderr.write("当前目录没有 devstudio.yaml。先执行 devstudio init。\n")
        return 1
    config = load_yaml(path)
    sys.stdout.write(f"state: {config.get('state', '')}\n")
    sys.stdout.write(f"release_id: {config.get('release_id', '')}\n")
    sys.stdout.write(f"chip: {config.get('target', {}).get('chip', '')}\n")
    build = config.get("build", {})
    sys.stdout.write(f"build: {build.get('command', '')}\n")
    sys.stdout.write(f"host: {build.get('host', '')}\n")
    sys.stdout.write(f"data: {data_root(project)}\n")
    return 0
