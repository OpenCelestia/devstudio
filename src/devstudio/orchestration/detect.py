import re
from dataclasses import dataclass, field
from pathlib import Path

CELESTIA_FLAGS = ("application", "framework", "hardware", "build.sh")
VERSION_RE = re.compile(r"V\d+(?:\.\d+)+")


@dataclass
class SdkHit:
    path: Path
    family: str
    version: str
    release_id: str
    registered: bool


@dataclass
class Scan:
    project: Path
    sdks: list[SdkHit] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    product_required: bool = False
    flags: dict[str, bool] = field(default_factory=dict)
    product: str | None = None
    form: str | None = None
    form_flag: str | None = None
    config_release: str | None = None
    suggested: str | None = None
    blocked: str | None = None
    mixed: bool = False


def split_release(release_id: str) -> tuple[str, str]:
    family, version = release_id.rsplit("_", 1)
    return family, version


def chip_of(family: str) -> str:
    if "AV100" in family:
        return "AV100_V2"
    if "AV130" in family or family.startswith("Sky39"):
        return "AV130"
    if "AV200" in family:
        return "AV200"
    return family


def _registered(install_root: Path, release_id: str) -> bool:
    from devstudio.adapter.catalog import adapters_root

    return (adapters_root(install_root) / release_id / "adapter.yaml").is_file()


def _hit_from_dir(path: Path, install_root: Path) -> SdkHit | None:
    if VERSION_RE.fullmatch(path.name):
        family, version = path.parent.name, path.name
    else:
        return None
    release_id = f"{family}_{version}"
    return SdkHit(path, family, version, release_id, _registered(install_root, release_id))


def find_sdks(project: Path, install_root: Path) -> list[SdkHit]:
    found: list[SdkHit] = []
    own = _hit_from_dir(project, install_root)
    if own and all((project / name).is_dir() for name in ("os", "platform", "tools")):
        found.append(own)
    system = project / "system"
    if system.is_dir():
        for family in sorted(p for p in system.iterdir() if p.is_dir()):
            for version in sorted(p for p in family.iterdir() if p.is_dir()):
                hit = _hit_from_dir(version, install_root)
                if hit:
                    found.append(hit)
    return found


def _read_configs(project: Path) -> dict[str, tuple[str | None, str | None, str]]:
    projects = project / "project"
    if not projects.is_dir():
        return {}
    found: dict[str, tuple[str | None, str | None, str]] = {}
    for cfg in sorted(projects.glob("*/config.mk")):
        text = cfg.read_text(encoding="utf-8", errors="replace")
        platform = _mk_value(text, "CONFIG_SOC_PLATFORM")
        version = _mk_value(text, "CONFIG_SOC_SDK_VERSION")
        if not platform or not version:
            continue
        form, flag = None, None
        for name, macro in (
            ("TABLET", "CONFIG_APP_PACKAGES_TABLET"),
            ("WEARABLE", "CONFIG_APP_PACKAGES_WEARABLE"),
            ("IPCAMERA", "CONFIG_APP_PACKAGES_IPCAMERA"),
        ):
            if re.search(rf"{macro}\s*=\s*y", text):
                form, flag = name, macro
                break
        found[cfg.parent.name] = (form, flag, f"{platform}_{version}")
    return found


def _mk_value(text: str, key: str) -> str | None:
    match = re.search(rf"^{key}\s*=\s*(\S+)", text, re.M)
    return match.group(1) if match else None


def scan_project(
    project: Path,
    install_root: Path,
    sdk_path: Path | None = None,
    product_name: str | None = None,
) -> Scan:
    project = project.resolve()
    result = Scan(project=project)
    result.flags = {
        "application": (project / "application").is_dir(),
        "framework": (project / "framework").is_dir(),
        "hardware": (project / "hardware").is_dir(),
        "build.sh": (project / "build.sh").is_file(),
    }
    configs = _read_configs(project)
    result.products = sorted(configs)
    if product_name:
        if product_name not in configs:
            result.blocked = f"No such product: project/{product_name}/config.mk."
            return result
        result.product = product_name
    elif len(configs) == 1:
        result.product = next(iter(configs))
    elif len(configs) > 1:
        result.product_required = True
    if result.product:
        result.form, result.form_flag, result.config_release = configs[result.product]
    result.sdks = find_sdks(project, install_root)
    if sdk_path is not None:
        sdk_path = sdk_path.resolve()
        hit = _hit_from_dir(sdk_path, install_root)
        ready = hit is not None and all((sdk_path / name).is_dir() for name in ("os", "platform", "tools"))
        if not ready:
            result.blocked = f"Not a chip SDK directory: {sdk_path}"
            result.sdks = []
            return result
        result.sdks = [hit]
    if result.blocked:
        return result
    if not result.sdks:
        result.blocked = "Chip SDK not found. Need os/, platform/, tools/, or system/<SoC>/<version>/."
        return result
    if result.config_release:
        matched = [hit for hit in result.sdks if hit.release_id == result.config_release]
        if len(matched) == 1:
            result.sdks = matched
        elif not matched:
            result.blocked = "The version pointed to by config.mk does not match the system/ directory. No files written."
            return result
    if len(result.sdks) > 1:
        result.mixed = True
        return result
    hit = result.sdks[0]
    if not hit.registered:
        result.blocked = f"Stopped. Install the Adapter for {hit.release_id} and retry. No files written."
        return result
    present = [name for name, ok in result.flags.items() if ok]
    complete = all(result.flags.values())
    if complete:
        result.suggested = "sdk_celestia"
    elif present:
        result.mixed = True
    elif sdk_path is not None and project != sdk_path.resolve():
        result.suggested = "sdk_customer_app"
    else:
        result.suggested = "sdk_only"
    return result
