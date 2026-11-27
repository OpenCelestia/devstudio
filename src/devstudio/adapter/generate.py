"""Generate a new DevStudio Adapter from an SDK version directory.

Ports the FAE-side catalog generation idea into offline Adapter files:
adapter.yaml, catalog.json (CIS), flash_catalog.json, wifi_catalog.json, errata.json,
optional header index.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from devstudio.adapter.catalog import adapters_root
from devstudio.orchestration.detect import VERSION_RE, chip_of
from devstudio.yamlio import dump_yaml

_NOR_UBOOT_RE = re.compile(r'\{\s*INFO(?:6|_DTR)?\(\s*"([^"]+)"')
_NOR_KERNEL_RE = re.compile(r'\{\s*"([^"]+)",\s*(?:CAT25_)?INFO')
_SPINAND_RE = re.compile(r'SPINAND_INFO\(\s*"([^"]+)"')
_CONF_MODEL_RE = re.compile(r"^isp_([A-Za-z0-9]+)_", re.I)


def run_adapter_generate(argv: list[str]) -> int:
    sdk_path = ""
    force = False
    fae_root = ""
    no_index = False
    args = list(argv)
    while args:
        item = args.pop(0)
        if item == "--sdk" and args:
            sdk_path = args.pop(0)
        elif item == "--force":
            force = True
        elif item == "--fae-root" and args:
            fae_root = args.pop(0)
        elif item == "--no-index":
            no_index = True
        elif item in ("-h", "--help"):
            sys.stdout.write(
                "用法：devstudio adapter generate --sdk <SDK版本目录> [--force] "
                "[--fae-root <FAE fae-ai 根>] [--no-index]\n"
                "从 SDK 树生成 sdk/adapters/<release_id>/（CIS/flash/wifi 列表 + adapter.yaml）。\n"
            )
            return 0
        else:
            sys.stderr.write(
                "用法：devstudio adapter generate --sdk <SDK版本目录> [--force] "
                "[--fae-root <路径>] [--no-index]\n"
            )
            return 2
    if not sdk_path:
        sys.stderr.write("必须指定 --sdk <SDK版本目录>。\n")
        return 2
    sdk = Path(sdk_path).resolve()
    hit = _sdk_identity(sdk)
    if hit is None:
        sys.stderr.write(
            f"不是 SDK 版本目录：{sdk}\n"
            "目录名须为 Vx.xx，父目录为平台族，且含 os、platform、tools。\n"
        )
        return 1
    family, version, release_id = hit
    dest = adapters_root() / release_id
    if dest.is_dir() and (dest / "adapter.yaml").is_file() and not force:
        sys.stderr.write(f"已存在 {dest}。加 --force 覆盖生成。\n")
        return 1
    dest.mkdir(parents=True, exist_ok=True)

    (dest / "adapter.yaml").write_text(
        dump_yaml(
            {
                "release_id": release_id,
                "family": family,
                "version": version,
                "chip": chip_of(family),
            }
        ),
        encoding="utf-8",
    )
    if not (dest / "errata.json").is_file() or force:
        (dest / "errata.json").write_text("[]\n", encoding="utf-8")

    sensor_rows = _sensor_from_conf_pool(sdk)
    if fae_root:
        sensor_rows = _merge_fae_sensor(Path(fae_root), family, sensor_rows)
    (dest / "catalog.json").write_text(
        json.dumps(sensor_rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )

    flash = _flash_from_sdk(sdk, release_id)
    (dest / "flash_catalog.json").write_text(
        json.dumps(flash, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    wifi = _wifi_from_sdk(sdk, release_id)
    (dest / "wifi_catalog.json").write_text(
        json.dumps(wifi, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    sys.stdout.write(f"已生成 Adapter：{dest}\n")
    sys.stdout.write(
        f"  CIS catalog：{len(sensor_rows)} 条；"
        f"SPI NOR：{flash['counts']['spi_nor']}；"
        f"SPI NAND：{flash['counts']['spi_nand']}；"
        f"WiFi L1：{wifi['counts']['l1']}\n"
    )

    if not no_index:
        from devstudio.tools.knowledge.index_headers import index_headers

        count, symbols = index_headers(sdk, dest / "index")
        if count:
            sys.stdout.write(f"  头文件索引：{count} 个文件，{symbols} 个符号\n")
        else:
            sys.stdout.write("  头文件索引：未找到 platform/include（可稍后 knowledge index）\n")
    return 0


def _sdk_identity(sdk: Path) -> tuple[str, str, str] | None:
    if not VERSION_RE.fullmatch(sdk.name):
        return None
    if not all((sdk / name).is_dir() for name in ("os", "platform", "tools")):
        return None
    family, version = sdk.parent.name, sdk.name
    return family, version, f"{family}_{version}"


def _uniq(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen and "/" not in item and not item.endswith(".c"):
            seen.add(item)
            out.append(item)
    return out


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _sensor_from_conf_pool(sdk: Path) -> list[dict]:
    """Minimal CIS catalog from isp_*.conf in the SDK tree."""
    by_model: dict[str, list[str]] = {}
    roots = [
        sdk / "rootfs" / "utils" / "etc",
        sdk / "tools" / "isptool",
        sdk / "rootfs" / "resource" / "etc" / "config",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        for conf in sorted(root.glob("isp_*.conf")):
            match = _CONF_MODEL_RE.match(conf.name)
            if not match:
                continue
            model = match.group(1).lower()
            by_model.setdefault(model, [])
            if conf.name not in by_model[model]:
                by_model[model].append(conf.name)
    rows = []
    for model in sorted(by_model):
        confs = by_model[model]
        lane = ""
        for name in confs:
            m = re.search(r"(\d)lane", name, re.I)
            if m:
                lane = f"MIPI {m.group(1)}lane"
                break
        rows.append(
            {
                "model": model,
                "resolution": "",
                "interface": lane or "",
                "fps": "",
                "cis_modes": ["1（单目）"],
                "features": "常电",
                "source": "sdk_builtin",
                "package": None,
                "docs": [],
                "confs": confs,
            }
        )
    return rows


def _merge_fae_sensor(fae_root: Path, family: str, rows: list[dict]) -> list[dict]:
    """Prefer FAE sensor-debug catalog rows when present; keep confs from SDK scan."""
    path = fae_root / "resources" / "sensor-debug" / "catalog.json"
    if not path.is_file():
        # also accept peripheral-support path via import of adapter catalog shape
        return rows
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return rows
    fae_rows = loaded.get(family) if isinstance(loaded, dict) else None
    if not isinstance(fae_rows, list) or not fae_rows:
        return rows
    conf_map = {str(r["model"]).lower(): r.get("confs") or [] for r in rows}
    merged = []
    for item in fae_rows:
        if not isinstance(item, dict) or not item.get("model"):
            continue
        model = str(item["model"]).lower()
        entry = dict(item)
        entry["model"] = model
        if not entry.get("confs"):
            entry["confs"] = conf_map.get(model, [])
        merged.append(entry)
    # keep SDK-only models not in FAE list
    seen = {str(r["model"]).lower() for r in merged}
    for row in rows:
        if row["model"] not in seen:
            merged.append(row)
    return merged


def _flash_from_sdk(sdk: Path, release_id: str) -> dict:
    nor: list[str] = []
    nand: list[dict] = []
    evidence: list[str] = []
    for rel in (
        "os/uboot/uboot/drivers/mtd/spi/spi-nor-ids.c",
        "os/uboot/drivers/mtd/spi/spi-nor-ids.c",
        "uboot/uboot/drivers/mtd/spi/spi-nor-ids.c",
    ):
        path = sdk / rel
        if path.is_file():
            nor.extend(_NOR_UBOOT_RE.findall(_read(path)))
            evidence.append(rel)
            break
    for rel in (
        "os/kernel/kernel/drivers/mtd/spi-nor/spi-nor.c",
        "os/linux/kernel/drivers/mtd/spi-nor/spi-nor.c",
        "kernel/drivers/mtd/spi-nor/spi-nor.c",
    ):
        path = sdk / rel
        if path.is_file():
            nor.extend(_NOR_KERNEL_RE.findall(_read(path)))
            evidence.append(rel)
            break
    for base_rel in (
        "os/uboot/uboot/drivers/mtd/nand/spi",
        "os/uboot/drivers/mtd/nand/spi",
        "uboot/uboot/drivers/mtd/nand/spi",
    ):
        base = sdk / base_rel
        if base.is_dir():
            for path in sorted(base.glob("*.c")):
                models = _uniq(_SPINAND_RE.findall(_read(path)))
                if models:
                    nand.append({"vendor": path.stem, "models": models})
            break
    mmc_all: list[str] = []
    for dts_root in (
        sdk / "os" / "kernel",
        sdk / "os" / "linux",
        sdk / "kernel",
        sdk / "platform",
    ):
        if not dts_root.is_dir():
            continue
        for p in dts_root.glob("**/*spinand*.dts"):
            mmc_all.append(p.name)
        for p in dts_root.glob("**/*_mmc*.dts"):
            mmc_all.append(p.name)
        if mmc_all:
            break
    mmc_all = sorted(set(mmc_all))
    # 与 FAE 问答一致：平台板级 DTS；上游参考板另存 raw 供排查
    from devstudio.tools.knowledge.peripherals import _filter_platform_mmc_dts
    from devstudio.orchestration.detect import split_release as _split

    family, _ver = _split(release_id)
    mmc = _filter_platform_mmc_dts(mmc_all, family)
    nor = _uniq(nor)
    raw_n, raw_ev = _raw_nand_count(sdk)
    return {
        "release_id": release_id,
        "kind": "flash",
        "source": "sdk_tree_scan",
        "spi_nor": [{"model": m} for m in nor],
        "spi_nand": nand,
        "mmc_variants": mmc,
        "mmc_variants_all": mmc_all,
        "raw_nand": {"count": raw_n, "evidence": raw_ev or ""},
        "wifi_driver_dirs": [],
        "cellular": {
            "supported_as_model_list": False,
            "notes": "换 4G 模组属内核配置级（L2），无型号级支持列表。",
            "evidence": [],
        },
        "evidence": evidence,
        "counts": {
            "spi_nor": len(nor),
            "spi_nand": sum(len(x.get("models") or []) for x in nand),
            "mmc_variants": len(mmc),
            "mmc_variants_all": len(mmc_all),
            "raw_nand": raw_n,
            "wifi_driver_dirs": 0,
        },
    }


def _raw_nand_count(sdk: Path) -> tuple[int, str]:
    """Count generic raw NAND ID-table entries (FAE gen_flash_lists 同口径，不逐型号展开)."""
    cands = (
        "os/linux/kernel/drivers/mtd/nand/raw/nand_ids.c",
        "os/linux/kernel/drivers/mtd/nand/nand_ids.c",
        "os/kernel/kernel/drivers/mtd/nand/raw/nand_ids.c",
        "kernel/drivers/mtd/nand/raw/nand_ids.c",
    )
    for rel in cands:
        path = sdk / rel
        if path.is_file():
            n = len(re.findall(r'\{\s*"NAND ', _read(path)))
            return n, rel
    return 0, ""


def _wifi_from_sdk(sdk: Path, release_id: str) -> dict:
    rows: list[dict] = []
    script_dir = sdk / "rootfs" / "scripts" / "wifi"
    skip = {"connect_ap.sh", "wifi.sh", "wifi_start.sh", "wifi_stop.sh"}
    if script_dir.is_dir():
        for path in sorted(script_dir.glob("*.sh")):
            name = path.name
            if name in skip or name.startswith("wifi_bt_driver_"):
                # wifi_bt_driver_<chip>.sh → mark BT on that chip
                continue
            model = name.removesuffix(".sh").lower()
            if model.startswith("wifi_"):
                model = model[len("wifi_") :]
            text = _read(path).lower()
            bus = "资料未标"
            if "wifi_usb" in text or "ak_hcd" in text:
                bus = "USB"
            if "wifi_sdio" in text or "ak_mci" in text:
                bus = "SDIO" if bus == "资料未标" else "USB/SDIO"
            bt = (script_dir / f"wifi_bt_driver_{model}.sh").is_file() or "wifi_bt_comb=1" in text
            rows.append(
                {
                    "model": model,
                    "bus": bus,
                    "bt": bt,
                    "modes": "资料未标",
                    "source": "script_pool",
                }
            )
    dirs: list[str] = []
    for rel in (
        "os/driver/external/driver_src/wifi",
        "driver_src/wifi",
        "os/driver/driver_src/wifi",
    ):
        d = sdk / rel
        if d.is_dir():
            dirs = sorted(p.name for p in d.iterdir() if p.is_dir())
            break
    # RTOS：无脚本池时用源码目录名作 L1 提示
    if not rows and dirs:
        for name in dirs:
            model = name.lower()
            if "ble" in model:
                continue
            rows.append(
                {
                    "model": model,
                    "bus": "资料未标",
                    "bt": False,
                    "modes": "资料未标",
                    "source": "driver_src",
                }
            )
    return {
        "release_id": release_id,
        "kind": "wifi",
        "source": "sdk_tree_scan",
        "rows": rows,
        "driver_src_dirs": dirs,
        "counts": {"l1": len(rows), "driver_src_dirs": len(dirs)},
    }
