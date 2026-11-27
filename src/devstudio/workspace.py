import hashlib
import os
import re
from pathlib import Path


def project_key(project: Path) -> str:
    resolved = str(project.resolve())
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", project.name).strip("._") or "project"
    return f"{slug[:40]}-{digest}"


def data_root(project: Path) -> Path:
    """计划、日志、报告和设备记录所在目录。不在工程树内。"""
    project = project.resolve()
    chosen = os.environ.get("DEVSTUDIO_DATA")
    if chosen:
        base = Path(chosen).expanduser().resolve()
    else:
        base = Path.home() / ".devstudio" / "work"
    return base / project_key(project)


def tasks_dir(project: Path) -> Path:
    return data_root(project) / "tasks"


def devices_path(project: Path) -> Path:
    return data_root(project) / "devices.json"
