from pathlib import Path

from devstudio.root import repo_root


def adapters_root(root: Path | None = None) -> Path:
    base = root if root is not None else repo_root()
    modern = base / "sdk" / "adapters"
    if modern.is_dir():
        return modern
    return base / "adapters"


def list_adapters(root: Path | None = None) -> list[str]:
    base = adapters_root(root)
    if not base.is_dir():
        return []
    names = []
    for child in sorted(base.iterdir()):
        if child.is_dir() and (child / "adapter.yaml").is_file():
            names.append(child.name)
    return names
