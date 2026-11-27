#!/usr/bin/env python3
"""Import FAE peripheral flash/wifi lists into DevStudio Adapter JSON catalogs.

Source of truth for this import: FAE AI markdown already generated from SDK ID tables
(resources/peripheral-support/sdk/<family>/<ver>_flash.md). Does not touch FAE DB/RAG.

Usage:
  python devstudio/sdk/scripts/import_fae_peripheral_catalogs.py \\
    --fae-root "/path/to/FAE AI/fae-ai" \\
    --adapters-root devstudio/sdk/adapters
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# DevStudio release_id → FAE (family, version) under peripheral-support/sdk/
RELEASE_MAP = {
    "AnyCloud39AV200_V1.04": ("AnyCloud39AV200", "V1.04"),
    "AnyCloud39AV100_V2_V2.05": ("AnyCloud39AV100_V2", "V2.05"),
    "Sky39AV130_V1.01": ("Sky39AV130", "V1.01"),
}

# L1 wifi pools from FAE support-judgement (script/ko pools), keyed by release_id
WIFI_L1: dict[str, list[dict]] = {
    "AnyCloud39AV200_V1.04": [
        {"model": "atbm6012bx", "bus": "SDIO", "bt": True, "modes": "b/g/n", "source": "script_pool"},
        {"model": "atbm6031", "bus": "SDIO", "bt": False, "modes": "b/g/n", "source": "script_pool"},
        {"model": "atbm6031x", "bus": "SDIO", "bt": True, "modes": "b/g/n", "source": "script_pool"},
        {"model": "atbm6032", "bus": "SDIO", "bt": False, "modes": "b/g/n", "source": "script_pool"},
        {"model": "atbm6062s", "bus": "SDIO", "bt": True, "modes": "b/g/n/ax", "source": "script_pool"},
        {"model": "atbm6062u", "bus": "USB", "bt": True, "modes": "b/g/n/ax", "source": "script_pool"},
        {"model": "atbm6132", "bus": "USB", "bt": False, "modes": "b/g/n", "source": "script_pool"},
        {"model": "atbm6162", "bus": "SDIO", "bt": False, "modes": "b/g/n/ax", "source": "ko_patch"},
        {"model": "rtl8188", "bus": "USB", "bt": False, "modes": "b/g/n", "source": "script_pool"},
        {"model": "rtl8189", "bus": "SDIO", "bt": False, "modes": "b/g/n", "source": "script_pool"},
        {"model": "ssv6x5x", "bus": "SDIO", "bt": False, "modes": "b/g/n", "source": "script_pool"},
    ],
    "AnyCloud39AV100_V2_V2.05": [
        {"model": "aic8800", "bus": "SDIO", "bt": False, "modes": "b/g/n/ax", "source": "script_pool"},
        {"model": "aic8800d40l", "bus": "SDIO", "bt": False, "modes": "b/g/n/ax", "source": "script_pool"},
        {"model": "atbm6012bx", "bus": "SDIO", "bt": True, "modes": "b/g/n", "source": "script_pool"},
        {"model": "atbm6031", "bus": "SDIO", "bt": False, "modes": "b/g/n", "source": "script_pool"},
        {"model": "atbm6031x", "bus": "SDIO", "bt": True, "modes": "b/g/n", "source": "script_pool"},
        {"model": "atbm6032", "bus": "SDIO", "bt": False, "modes": "b/g/n", "source": "script_pool"},
        {"model": "atbm6062s", "bus": "SDIO", "bt": True, "modes": "b/g/n/ax", "source": "script_pool"},
        {"model": "atbm6062u", "bus": "USB", "bt": True, "modes": "b/g/n/ax", "source": "script_pool"},
        {"model": "atbm6162", "bus": "SDIO", "bt": False, "modes": "b/g/n/ax", "source": "script_pool"},
        {"model": "rtl8188", "bus": "USB", "bt": False, "modes": "b/g/n", "source": "script_pool"},
        {"model": "rtl8189", "bus": "SDIO", "bt": False, "modes": "b/g/n", "source": "script_pool"},
        {"model": "ssv6x5x", "bus": "SDIO", "bt": False, "modes": "b/g/n", "source": "script_pool"},
    ],
    "Sky39AV130_V1.01": [
        {"model": "ak6018_usb", "bus": "USB", "bt": False, "modes": "b/g/n", "source": "lib_pool"},
        {"model": "atbm6012bx", "bus": "SDIO", "bt": True, "modes": "b/g/n", "source": "lib_pool"},
        {"model": "atbm6461", "bus": "SDIO", "bt": True, "modes": "b/g/n/ax", "source": "lib_pool"},
    ],
}


def parse_flash_md(text: str, release_id: str) -> dict:
    spi_nor: list[str] = []
    spi_nand: list[dict] = []
    mmc: list[str] = []
    wifi_dirs: list[str] = []
    cellular: dict = {"supported_as_model_list": False, "notes": "", "evidence": []}

    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if section.startswith("SPI NOR"):
            # backtick-wrapped model lists, possibly comma/顿号 separated
            if "`" in line:
                spi_nor.extend(re.findall(r"`([^`]+)`", line))
            elif "、" in line and not line.startswith("- 证据") and not line.startswith(">"):
                # plain顿号 list without backticks (some generators)
                chunk = line.lstrip("- ").strip()
                if chunk and not chunk.startswith("证据"):
                    spi_nor.extend(x.strip(" 、,") for x in chunk.split("、") if x.strip())
        elif section.startswith("SPI NAND"):
            # table row: | `foress` | `A`、`B` |
            if line.startswith("|") and "驱动文件" not in line and "---" not in line:
                cells = [c.strip() for c in line.strip("|").split("|")]
                if len(cells) >= 2:
                    vendor = re.sub(r"[`]", "", cells[0]).strip()
                    models = re.findall(r"`([^`]+)`", cells[1])
                    if not models:
                        models = [m.strip() for m in cells[1].replace("、", ",").split(",") if m.strip()]
                    if vendor and models:
                        spi_nand.append({"vendor": vendor, "models": models})
            # bullet: - foress：`A`、`B`
            m = re.match(r"[-*]\s*([^：:]+)[：:]\s*(.+)", line)
            if m and "证据" not in m.group(1):
                vendor = m.group(1).strip().strip("`")
                models = re.findall(r"`([^`]+)`", m.group(2))
                if vendor and models:
                    spi_nand.append({"vendor": vendor, "models": models})
        elif "MMC" in section or "存储变体" in section:
            if line.startswith("- `") or (line.startswith("`") and line.endswith("`")):
                mmc.extend(re.findall(r"`([^`]+)`", line))
            elif line.startswith("- ") and line.endswith(".dts"):
                mmc.append(line[2:].strip())
        elif section.startswith("WiFi"):
            if line.startswith("- `"):
                wifi_dirs.extend(re.findall(r"`([^`]+)`", line))
        elif section.startswith("4G"):
            if line.startswith("- "):
                cellular["notes"] = (cellular.get("notes") or "") + line[2:] + " "
            cellular["evidence"].extend(re.findall(r"`([^`]+)`", line))

    def _is_model(name: str) -> bool:
        if not name or "/" in name or "\\" in name:
            return False
        if name.endswith((".c", ".h", ".dts", ".md")):
            return False
        if name.startswith(("extracted", "drivers", "uboot", "linux")):
            return False
        return True

    # de-dup preserve order
    def uniq(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item and _is_model(item) and item not in seen:
                seen.add(item)
                out.append(item)
        return out

    spi_nor = uniq(spi_nor)
    mmc_all = uniq(mmc)
    wifi_dirs = uniq(wifi_dirs)
    cellular["notes"] = (cellular.get("notes") or "").strip()
    if "L2" in cellular["notes"] or "内核配置" in cellular["notes"]:
        cellular["supported_as_model_list"] = False

    # 平台板级 DTS；上游参考板保留在 mmc_variants_all（对齐 FAE 问答口径）
    family = release_id.rsplit("_", 1)[0] if "_" in release_id else release_id
    # AnyCloud39AV200_V1.04 → family AnyCloud39AV200 when mapped; here release_id is full
    if release_id.startswith("AnyCloud39AV200"):
        family = "AnyCloud39AV200"
    elif release_id.startswith("AnyCloud39AV100"):
        family = "AnyCloud39AV100_V2"
    elif release_id.startswith("Sky39AV130"):
        family = "Sky39AV130"
    try:
        from devstudio.tools.knowledge.peripherals import _filter_platform_mmc_dts

        mmc = _filter_platform_mmc_dts(mmc_all, family)
    except Exception:
        mmc = mmc_all

    raw_nand = {"count": 0, "evidence": ""}
    # FAE md: "## raw NAND" + "内核通用 raw NAND ID 表 **N** 条"
    m_raw = re.search(r"raw NAND ID 表\s*\*\*(\d+)\*\*", text)
    if m_raw:
        raw_nand["count"] = int(m_raw.group(1))

    return {
        "release_id": release_id,
        "kind": "flash",
        "source": "fae_peripheral_support_md",
        "spi_nor": [{"model": m} for m in spi_nor],
        "spi_nand": spi_nand,
        "mmc_variants": mmc,
        "mmc_variants_all": mmc_all,
        "raw_nand": raw_nand,
        "wifi_driver_dirs": wifi_dirs,
        "cellular": cellular,
        "counts": {
            "spi_nor": len(spi_nor),
            "spi_nand": sum(len(x.get("models") or []) for x in spi_nand),
            "mmc_variants": len(mmc),
            "mmc_variants_all": len(mmc_all),
            "raw_nand": raw_nand["count"],
            "wifi_driver_dirs": len(wifi_dirs),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fae-root", type=Path, required=True, help="…/FAE AI/fae-ai")
    parser.add_argument("--adapters-root", type=Path, required=True)
    args = parser.parse_args()
    support = args.fae_root / "resources" / "peripheral-support" / "sdk"
    if not support.is_dir():
        raise SystemExit(f"missing {support}")

    for release_id, (family, version) in RELEASE_MAP.items():
        md_path = support / family / f"{version}_flash.md"
        if not md_path.is_file():
            print(f"[skip] {release_id}: no {md_path}")
            continue
        flash = parse_flash_md(md_path.read_text(encoding="utf-8"), release_id)
        wifi_rows = WIFI_L1.get(release_id, [])
        # merge driver dirs from md as L2 hints
        wifi = {
            "release_id": release_id,
            "kind": "wifi",
            "source": "fae_support_judgement_l1",
            "rows": wifi_rows,
            "driver_src_dirs": flash.get("wifi_driver_dirs") or [],
            "counts": {"l1": len(wifi_rows), "driver_src_dirs": len(flash.get("wifi_driver_dirs") or [])},
        }
        dest = args.adapters_root / release_id
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "flash_catalog.json").write_text(
            json.dumps(flash, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (dest / "wifi_catalog.json").write_text(
            json.dumps(wifi, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"[ok] {release_id}: NOR={flash['counts']['spi_nor']} "
            f"NAND={flash['counts']['spi_nand']} WIFI_L1={wifi['counts']['l1']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
