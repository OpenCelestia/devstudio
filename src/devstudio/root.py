import os
from pathlib import Path


def repo_root() -> Path:
    env = os.environ.get("DEVSTUDIO_ROOT")
    if env:
        p = Path(env)
        # Do not Path.resolve() on Win32 Z:/WSL/UNC mounts — it can hang for minutes.
        return p if p.is_absolute() else (Path.cwd() / p).absolute()
    here = Path(__file__).resolve()
    candidate = here.parents[2]
    if (candidate / "pyproject.toml").is_file():
        return candidate
    # runtime-src mirror layout: <shimDir>/runtime-src/devstudio/... → parents[2] is shimDir
    shim_root = here.parents[2]
    if (shim_root / "runtime-src").is_dir() and os.environ.get("DEVSTUDIO_ROOT"):
        return Path(os.environ["DEVSTUDIO_ROOT"])
    return Path.cwd()
