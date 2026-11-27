import json
import re
import sys
from pathlib import Path

from devstudio.orchestration.detect import split_release
from devstudio.adapter.catalog import adapters_root
from devstudio.yamlio import load_yaml

SOURCE_LABEL = {"package": "调试包", "sdk_builtin": "SDK 内置"}
PERIPH_TYPES = ("sensor", "flash", "wifi")


def catalog_rows(project: Path, interface: str | None = None) -> tuple[str, str, list]:
    config = project / "devstudio.yaml"
    if not config.is_file():
        return "当前目录没有 devstudio.yaml。先执行 devstudio init。", "", []
    release_id = load_yaml(config)["release_id"]
    catalog_path = adapters_root() / release_id / "catalog.json"
    if not catalog_path.is_file():
        return "", release_id, []
    loaded = json.loads(catalog_path.read_text(encoding="utf-8"))
    if isinstance(loaded, dict):
        family, _version = split_release(release_id)
        rows = loaded.get(family, [])
    else:
        rows = loaded
    if interface:
        rows = [row for row in rows if interface in str(row.get("interface", ""))]
    return "", release_id, rows


def run_peripherals(argv: list[str], project: Path | None = None) -> int:
    kind, interface = "sensor", None
    args = list(argv)
    while args:
        item = args.pop(0)
        if item == "--type":
            kind = args.pop(0) if args else ""
        elif item == "--interface":
            interface = args.pop(0) if args else None
        else:
            sys.stderr.write(
                "用法：devstudio knowledge peripherals "
                "[--type sensor|flash|wifi] [--interface 接口]\n"
            )
            return 2
    if kind not in PERIPH_TYPES:
        sys.stdout.write("未收录\n")
        return 0
    project = (project or Path.cwd()).resolve()
    if kind == "sensor":
        return _print_sensor(project, interface)
    if kind == "flash":
        return _print_flash(project)
    return _print_wifi(project)


def _release_of(project: Path) -> tuple[str, str]:
    config = project / "devstudio.yaml"
    if not config.is_file():
        return "当前目录没有 devstudio.yaml。先执行 devstudio init。", ""
    return "", str(load_yaml(config).get("release_id") or "")


def _print_sensor(project: Path, interface: str | None) -> int:
    error, release_id, rows = catalog_rows(project, interface)
    if error:
        sys.stderr.write(error + "\n")
        return 1
    if not release_id or not rows and not (adapters_root() / release_id / "catalog.json").is_file():
        sys.stdout.write("未收录\n")
        return 0
    family, version = split_release(release_id)
    sys.stdout.write(f"平台 {family}   版本 {version}\n")
    sys.stdout.write(
        "型号       分辨率               帧率       CIS 模式            功能              来源         本版驱动\n"
    )
    for row in rows:
        modes = "、".join(_mode(item) for item in row.get("cis_modes", []))
        source = SOURCE_LABEL.get(row.get("source"), row.get("source", ""))
        from devstudio.tools.source.sensor_flow import driver_state

        present = driver_state(project, release_id, row)
        sys.stdout.write(
            f"{row['model']:<10} {row.get('resolution',''):<20} {row.get('fps',''):<10} "
            f"{modes:<18} {row.get('features',''):<16} {source:<12} {present}\n"
        )
    sys.stdout.write(f"共 {len(rows)} 条\n")
    return 0


def _print_flash(project: Path) -> int:
    """Print flash list aligned with FAE AI gen_flash_lists / localhost:5173 口径."""
    error, release_id = _release_of(project)
    if error:
        sys.stderr.write(error + "\n")
        return 1
    path = adapters_root() / release_id / "flash_catalog.json"
    if not path.is_file():
        sys.stdout.write("未收录\n")
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    family, version = split_release(release_id)
    nor_models = [
        str(item.get("model") if isinstance(item, dict) else item)
        for item in (data.get("spi_nor") or [])
    ]
    nor_models = [m for m in nor_models if m]
    nand = [item for item in (data.get("spi_nand") or []) if isinstance(item, dict)]
    nand_n = sum(len(item.get("models") or []) for item in nand)
    mmc_all = [str(x) for x in (data.get("mmc_variants") or [])]
    mmc_plat = _filter_platform_mmc_dts(mmc_all, family)
    raw = data.get("raw_nand") or {}
    raw_n = int(raw.get("count") or 0) if isinstance(raw, dict) else 0

    sys.stdout.write(f"{family} {version} flash 支持列表\n\n")
    sys.stdout.write(
        "型号全部枚举自 SDK 源码驱动识别表（与 FAE `gen_flash_lists.py` 同源口径）；"
        "每个型号均为树内驱动真实识别条目。未列出即 SDK 内未见识别条目。\n\n"
    )
    sys.stdout.write("## 总览\n\n")
    sys.stdout.write("| 类别 | 型号数量 | 接口 | 说明 |\n|---|---:|---|---|\n")
    sys.stdout.write(f"| SPI NOR | {len(nor_models)} | SPI | SDK 内置驱动识别表条目 |\n")
    sys.stdout.write(
        f"| SPI NAND | {nand_n} | SPI | SDK 内置；官方提供 *_spinand.dts 板级变体 |\n"
    )
    if raw_n:
        sys.stdout.write(
            f"| raw NAND（并行） | {raw_n}（ID 表条数） | 并行 NAND | "
            "通用容量/位宽表，具体颗粒以 JEDEC 命中为准；不逐型号展开 |\n"
        )
    else:
        sys.stdout.write(
            "| raw NAND（并行） | — | 并行 NAND | "
            "具体型号清单未入库/未展开，避免失实暂不列出 |\n"
        )
    sys.stdout.write(
        "| MMC/SD（含 SD NAND） | 不按型号管理 | SD/MMC | "
        "块设备协议识别，无固定型号 ID 表；下列仅为平台官方 *_mmc/*_spinand DTS |\n"
    )
    sys.stdout.write(
        f"\n驱动识别表合计：**SPI NOR {len(nor_models)} + SPI NAND {nand_n} = "
        f"{len(nor_models) + nand_n}**（勿把 DTS 变体数算进「款」）。\n\n"
    )

    sys.stdout.write(f"## SPI NOR 支持型号（共 {len(nor_models)} 个）\n\n")
    for label, models in _group_nor_by_vendor(nor_models):
        sys.stdout.write(f"### {label}（{len(models)}）\n")
        sys.stdout.write("、".join(models) + "\n\n")

    sys.stdout.write(f"## SPI NAND 支持型号（共 {nand_n} 个，按厂商）\n\n")
    for item in nand:
        vendor = str(item.get("vendor") or "")
        mods = [str(m) for m in (item.get("models") or [])]
        sys.stdout.write(f"- **{vendor}**（{len(mods)}）：{'、'.join(mods)}\n")
    sys.stdout.write("\n")

    sys.stdout.write("## raw NAND\n\n")
    if raw_n:
        ev = ""
        if isinstance(raw, dict) and raw.get("evidence"):
            ev = f"（证据：{raw.get('evidence')}）"
        sys.stdout.write(
            f"内核 raw NAND ID 表约 **{raw_n}** 条{ev}；按容量/位宽组织，不在此逐型号罗列。\n\n"
        )
    else:
        sys.stdout.write(
            "本次 Adapter 未收录该平台 raw NAND 具体型号清单，暂不列出以免失实。"
            "可在 SDK 树重跑 FAE `gen_flash_lists.py` 或核对其 `nand_ids.c`。\n\n"
        )

    sys.stdout.write("## MMC/SD（含 SD NAND）\n\n")
    sys.stdout.write(
        "MMC/SD 与 SD NAND 走标准 SD 协议，经控制器挂载为块设备，**不依赖固定型号 ID 表**，"
        "因此没有对应的「型号清单」。\n"
    )
    if mmc_plat:
        sys.stdout.write(
            f"平台相关官方存储 DTS 变体（{len(mmc_plat)}，已过滤上游参考板）：\n"
        )
        sys.stdout.write("、".join(mmc_plat) + "\n")
    else:
        sys.stdout.write("当前 catalog 未见平台侧 *_mmc.dts / *_spinand.dts 文件名。\n")
    skipped = len(mmc_all) - len(mmc_plat)
    if skipped > 0:
        sys.stdout.write(
            f"（catalog 另有 {skipped} 个上游/参考板 DTS 文件名未计入平台支持列表。）\n"
        )
    sys.stdout.write("\n")

    cell = data.get("cellular") or {}
    sys.stdout.write("## 4G / 蜂窝\n\n")
    sys.stdout.write(
        "无型号级支持列表——换模组属内核配置级改动（L2：USB net / PPP / option 串口驱动 + DTS）。\n"
    )
    if cell.get("notes"):
        sys.stdout.write(str(cell["notes"]).strip() + "\n")
    sys.stdout.write("\n")

    sys.stdout.write("## 总结\n\n")
    sys.stdout.write(
        f"- SPI NOR {len(nor_models)} + SPI NAND {nand_n} 均为 SDK 内置驱动识别表条目；"
        "表外型号需自编/扩展驱动（L2）。\n"
        "- flash 类型由构建配置决定：`CONFIG_FLASH_TYPE = NOR | NAND | MMC`；"
        "分区表与启动镜像随类型变化，**更换 flash 类型必须重新生成整套烧写镜像**，不能只改参数。\n"
        "- 个别型号在焊接组合、时钟下的兼容属硬件实测，建议实机确认。\n"
    )
    return 0


# FAE 问答同款 NOR 厂商分组（前缀匹配，先长后短）
_NOR_VENDOR_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Atmel / Adesto（at…）", ("at",)),
    ("EON（en…）", ("en",)),
    ("GigaDevice 兆易创新（gd…）", ("gd",)),
    ("ISSI 芯成（is…）", ("is",)),
    ("Macronix 旺宏（mx66…）", ("mx66",)),
    ("Macronix 旺宏（mx25…）", ("mx25",)),
    ("Micron / Numonyx（n25q、mt25、mt35…）", ("n25q", "mt25", "mt35")),
    ("Spansion / Cypress / Infineon（s25、s70、s28…）", ("s25", "s70", "s28")),
    ("SST / Microchip（sst…）", ("sst",)),
    ("ST 意法半导体（m25p…）", ("m25p", "m25pe", "m25px")),
    ("Winbond 华邦（w25…）", ("w25",)),
    ("芯天下 XTX（XM / XT…）", ("xm25", "xt25")),
    ("普冉 PUYA（P25 / PY25…）", ("p25", "py25")),
    ("其他厂商（NM / GT / GM / ME / FM / BW 等）", ()),
)


def _group_nor_by_vendor(models: list[str]) -> list[tuple[str, list[str]]]:
    buckets: dict[str, list[str]] = {label: [] for label, _ in _NOR_VENDOR_RULES}
    other_label = _NOR_VENDOR_RULES[-1][0]
    for model in models:
        low = model.lower()
        placed = False
        for label, prefixes in _NOR_VENDOR_RULES[:-1]:
            if any(low.startswith(p) for p in prefixes):
                buckets[label].append(model)
                placed = True
                break
        if not placed:
            buckets[other_label].append(model)
    return [(label, items) for label, items in buckets.items() if items]


def _filter_platform_mmc_dts(names: list[str], family: str) -> list[str]:
    """Keep Anyka/board DTS; drop upstream reference SoC trees (对齐 FAE 问答口径)."""
    family_l = family.lower()
    # tokens that strongly indicate this SDK's boards / SoC
    keep_tokens = (
        "ak3918",
        "km02",
        "km01",
        "ca200",
        "evb_cbdm",
        "sq38",
        "actcam",
        "anycloud",
        "sky39",
        "av200",
        "av100",
        "av130",
    )
    if "av200" in family_l:
        keep_tokens = keep_tokens + ("ak3918av200", "km02x", "km02d")
    out: list[str] = []
    for name in names:
        low = name.lower()
        if any(tok in low for tok in keep_tokens):
            out.append(name)
    return sorted(set(out))


def _print_wifi(project: Path) -> int:
    error, release_id = _release_of(project)
    if error:
        sys.stderr.write(error + "\n")
        return 1
    path = adapters_root() / release_id / "wifi_catalog.json"
    if not path.is_file():
        sys.stdout.write("未收录\n")
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    family, version = split_release(release_id)
    rows = data.get("rows") or []
    sys.stdout.write(f"平台 {family}   版本 {version}   外设 wifi（L1 脚本/库池）\n")
    sys.stdout.write("型号            总线        BT     制式           来源\n")
    for row in rows:
        sys.stdout.write(
            f"{str(row.get('model','')):<14} {str(row.get('bus','')):<10} "
            f"{'是' if row.get('bt') else '否':<6} {str(row.get('modes','')):<14} "
            f"{row.get('source','')}\n"
        )
    dirs = data.get("driver_src_dirs") or []
    if dirs:
        sys.stdout.write(f"L2 源码目录（driver_src/wifi，共 {len(dirs)}）：{'、'.join(dirs)}\n")
    sys.stdout.write(f"共 {len(rows)} 条 L1\n")
    return 0


def _mode(text: str) -> str:
    match = re.search(r"（(.+)）", text)
    inner = match.group(1) if match else text
    return inner.replace("，", "")


def models_in_catalog(project: Path) -> set[str]:
    config = project / "devstudio.yaml"
    if not config.is_file():
        return set()
    release_id = load_yaml(config)["release_id"]
    path = adapters_root() / release_id / "catalog.json"
    if not path.is_file():
        return set()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(loaded, dict):
        family, _version = split_release(release_id)
        loaded = loaded.get(family, [])
    return {str(row["model"]).lower() for row in loaded}
