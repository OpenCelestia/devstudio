"""把 SDK 参考驱动改成目标 CIS 的源码。

规则见 docs/cis-driver-gen.md。型号与平台都走同一套步骤：
先换型号 token（含字母_数字守卫），再按客户参数和序列改宏、分辨率头和初始化数组。
没有证据的增益表、翻转、二进制 ISP conf 不改数值，只记入待复核。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# 平台 → (家族, conf 文件名里的芯片词, 配置键, 是否逗号列表)
_PROFILES: dict[str, tuple[str, str, str, bool]] = {
    "Sky39AV130": ("rtos", "av130", "CONFIG_CIS_TYPE", True),
    "AnyCloud39AV100_V2": ("linux", "km01a", "CONFIG_SENSOR_TYPE", False),
    "AnyCloud39AV200": ("linux", "av200", "CONFIG_SENSOR_TYPE", False),
}

_I2C_MODE_BYTES = {0: (1, 1), 1: (1, 1), 2: (1, 2), 3: (2, 1), 4: (2, 2)}

_ID_MSB = ("REG_ID_MSB", "ID_REG_H")
_ID_LSB = ("REG_ID_LSB", "ID_REG_L")
_VTS_MSB = ("REG_VTS_MSB", "VTS_REG_H")
_VTS_LSB = ("REG_VTS_LSB", "VTS_REG_L")
_HTS_MSB = ("REG_HTS_MSB", "HTS_REG_H")
_HTS_LSB = ("REG_HTS_LSB", "HTS_REG_L")
_EXP_MSB = ("REG_EXP_MSB", "EXP_REG_H")
_EXP_LSB = ("REG_EXP_LSB", "EXP_REG_L")
_PROBE_MACROS = ("ACTUAL_SENSOR_ID", "SENSOR_PHY_ID", "SENSOR_PHY_ID_A", "SENSOR_PHY_ID_B")


@dataclass
class Sequence:
    regs: list[tuple[int, int]] = field(default_factory=list)
    exposure_addrs: list[int] = field(default_factory=list)
    gain_rows: int = 0
    width: int | None = None
    height: int | None = None
    slave_id: int | None = None
    i2c_mode: int | None = None
    mclk_mhz: int | None = None
    mclk_field_mhz: int | None = None
    mbps: int | None = None
    vts: int | None = None
    hts: int | None = None
    flag_reg: int | None = None
    flag_reg1: int | None = None
    probe_id: int | None = None
    lane_hint: int | None = None
    fps_hint: int | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class AdaptResult:
    files: dict[str, str]
    written: list[str]
    gaps: list[str]
    init_regs_name: str | None
    init_regs_text: str | None
    conf_name: str
    manifest: dict


def profile_for(platform: str) -> tuple[str, str, str, bool]:
    """返回家族、芯片词、配置键、配置值是否为逗号列表。未知平台芯片词为空。"""
    if platform in _PROFILES:
        return _PROFILES[platform]
    low = platform.lower()
    if low.startswith("sky"):
        return ("rtos", "av130", "CONFIG_CIS_TYPE", True)
    if "anycloud" in low:
        return ("linux", "", "CONFIG_SENSOR_TYPE", False)
    return ("unknown", "", "", False)


def decode_text(data: bytes) -> str | None:
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def is_driver_source(name: str) -> bool:
    """参考目录里要改的文本。编译产物改名后仍是参考型号的二进制，不能进方案包。"""
    low = name.lower()
    if low.endswith(".mod.c") or low.endswith(".o.cmd") or low.endswith(".cmd"):
        return False
    if low in {"makefile"} or low.endswith(".mk") or low.endswith(".sh"):
        return True
    return Path(low).suffix in {".c", ".h", ".s"}


def model_id_of(model: str) -> int | None:
    """型号名里的十六进制尾数。gc1084 → 0x1084，sc2336p → 0x2336。对不上返回 None。"""
    m = re.match(r"[A-Za-z]+([0-9A-Fa-f]{3,4})", model)
    if not m:
        return None
    return int(m.group(1), 16)


def _forms(model: str) -> list[str]:
    """小写、大写、字母_数字 三种整词。model 先折成小写。"""
    low = model.lower()
    forms = [low, low.upper()]
    m = re.match(r"^([a-z]+)(\d.*)$", low)
    if m:
        forms.append(f"{m.group(1)}_{m.group(2)}")
        forms.append(f"{m.group(1).upper()}_{m.group(2).upper()}")
    return forms


def replace_model_tokens(text: str, ref_model: str, model: str) -> str:
    pairs = list(zip(_forms(ref_model), _forms(model.lower())))
    pairs.sort(key=lambda it: len(it[0]), reverse=True)
    for old, new in pairs:
        text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(old)}(?![A-Za-z0-9])", new, text)
    return text


def _parse_int(token: str | None) -> int | None:
    if token is None:
        return None
    s = token.strip().strip("()")
    if not s:
        return None
    try:
        return int(s, 0)
    except ValueError:
        return None


def _section_name(line: str) -> str | None:
    m = re.match(r"\s*\[([^\]]+)\]", line)
    return m.group(1).strip().lower() if m else None


def _strip_line_comment(line: str) -> str:
    for sep in ("//", "#"):
        if sep in line:
            line = line.split(sep, 1)[0]
    return line


def _reg_pair(line: str) -> tuple[int, int] | None:
    m = re.search(r"0x([0-9A-Fa-f]+)\s*,?\s*0x([0-9A-Fa-f]+)", line)
    if not m:
        return None
    return int(m.group(1), 16), int(m.group(2), 16)


def _sleep_of(line: str) -> int | None:
    m = re.match(r"\s*(?:sleep|delay|mdelay)\s+(\d+)", line, re.I)
    return int(m.group(1)) if m else None


def parse_sequence(text: str | None, filename: str | None = None) -> Sequence:
    seq = Sequence()
    if not text:
        return seq
    if filename:
        lm = re.search(r"(\d)\s*lane", filename, re.I)
        if lm:
            seq.lane_hint = int(lm.group(1))
        fm = re.search(r"(\d+)\s*mhz", filename, re.I)
        if fm and seq.mclk_mhz is None:
            seq.mclk_mhz = int(fm.group(1))
        pm = re.search(r"(?<![\d.])(\d+)\s*fps", filename, re.I)
        if pm:
            seq.fps_hint = int(pm.group(1))
    section = ""
    sensor_kv: dict[str, str] = {}
    for raw in text.splitlines():
        sec = _section_name(raw)
        if sec is not None:
            section = sec
            continue
        if section == "sensor":
            kv = re.match(r"\s*([A-Za-z0-9_]+)\s*=\s*(\S+)", raw)
            if kv:
                sensor_kv[kv.group(1).lower()] = kv.group(2)
            continue
        line = _strip_line_comment(raw).strip()
        if not line:
            # 注释行里的 mclk / mipi / vts 仍要读，用原始行
            _read_para_comment(raw, seq)
            continue
        if section in {"", "paralist"}:
            _read_para_comment(raw, seq)
            delay = _sleep_of(line)
            if delay is not None:
                seq.regs.append((0xFFFF, delay))
                continue
            pair = _reg_pair(line)
            if pair and section == "paralist" or (pair and section == "" and not sensor_kv):
                # 无节名的纯寄存器文本，或 ParaList
                if section == "paralist" or section == "":
                    if pair:
                        seq.regs.append(pair)
            continue
        if section == "exposure_paralist":
            pair = _reg_pair(line)
            if pair and pair[0] >= 0x100:
                seq.exposure_addrs.append(pair[0])
            continue
        if section == "gain_paralist":
            if _reg_pair(line):
                seq.gain_rows += 1
    _apply_sensor_kv(sensor_kv, seq)
    return seq


def _read_para_comment(raw: str, seq: Sequence) -> None:
    """只读 // 注释。 [Sensor] 的 mclk 是 kHz，不能和注释里的 MHz 混成一次匹配。"""
    raw = "\n".join(line.split("//", 1)[1] for line in raw.splitlines() if "//" in line)
    m = re.search(r"mclk\s*=\s*(\d+)", raw, re.I)
    if m:
        seq.mclk_mhz = int(m.group(1))
    m = re.search(r"mipi\s*=\s*(\d+)", raw, re.I)
    if m:
        seq.mbps = int(m.group(1))
    m = re.search(r"vts\s*=\s*(\d+)", raw, re.I)
    if m:
        seq.vts = int(m.group(1))
    m = re.search(r"hts\s*=\s*(\d+)", raw, re.I)
    if m:
        seq.hts = int(m.group(1))
    m = re.search(r"window\s+(\d+)\s*[xX]?\s*(\d+)", raw, re.I)
    if m and seq.width is None:
        seq.width, seq.height = int(m.group(1)), int(m.group(2))


def _apply_sensor_kv(kv: dict[str, str], seq: Sequence) -> None:
    if "width" in kv:
        seq.width = _parse_int(kv["width"]) or seq.width
    if "height" in kv:
        seq.height = _parse_int(kv["height"]) or seq.height
    if "slaveid" in kv:
        sid = _parse_int(kv["slaveid"])
        # 度信 ini 的 SlaveID 是 8 位写地址时为偶数（0x6E → 7 位 0x37）。奇数视为已经是 7 位。
        if sid is not None and sid % 2 == 0 and sid > 0:
            seq.notes.append(f"SlaveID {sid:#x} 按 8 位写地址右移 1 位，7 位地址为 {sid >> 1:#x}")
            sid >>= 1
        seq.slave_id = sid
    if "mode" in kv:
        seq.i2c_mode = _parse_int(kv["mode"])
    if "mclk" in kv:
        khz = _parse_int(kv["mclk"])
        if khz:
            seq.mclk_field_mhz = max(1, round(khz / 1000)) if khz >= 1000 else khz
    flag = _parse_int(kv.get("flagdata"))
    flag1 = _parse_int(kv.get("flagdata1"))
    if flag is not None and flag1 is not None:
        seq.probe_id = ((flag & 0xFF) << 8) | (flag1 & 0xFF)
    elif flag is not None:
        seq.probe_id = flag
    seq.flag_reg = _parse_int(kv.get("flagreg"))
    seq.flag_reg1 = _parse_int(kv.get("flagreg1"))


def parse_customer_params(text: str | None) -> dict:
    out: dict = {}
    if not text:
        return out
    m = re.search(r"(\d{3,5})\s*[x×*]\s*(\d{3,5})", text, re.I)
    if m:
        out["width"], out["height"] = int(m.group(1)), int(m.group(2))
    day = re.search(r"白天\s*(\d+)", text)
    night = re.search(r"夜晚\s*(\d+)", text)
    if day:
        out["day_fps"] = int(day.group(1))
    if night:
        out["night_fps"] = int(night.group(1))
    if "day_fps" not in out:
        one = re.search(r"(\d+)\s*fps", text, re.I)
        if one:
            out["day_fps"] = int(one.group(1))
    iface = re.search(r"接口\s*[:：]\s*(.+)", text)
    if iface:
        out["interface"] = iface.group(1).strip()
    return out


def _lane_of(interface: str | None) -> int | None:
    if not interface:
        return None
    if "dvp" in interface.lower():
        return None
    m = re.search(r"(\d)\s*lane", interface, re.I)
    return int(m.group(1)) if m else None


def _bus_token(interface: str | None, lane: int | None) -> str:
    if interface and "dvp" in interface.lower():
        return "dvp"
    if lane:
        return f"mipi_{lane}lane"
    if interface and "mipi" in interface.lower():
        return "mipi"
    return "mipi"


def conf_basename(model: str, bus: str, chip: str, mode: str) -> str:
    parts = ["isp", model.lower(), bus]
    if chip:
        parts.append(chip)
    name = "_".join(parts)
    if mode and mode != "single":
        name += f"_{mode}"
    return name + ".conf"


def _read_macro(text: str, name: str) -> str | None:
    m = re.search(rf"#define\s+{name}\b[ \t]+(\([^)\n]*\)|\S+)", text)
    return m.group(1) if m else None


def _read_macro_files(files: dict[str, str], name: str) -> int | None:
    for text in files.values():
        raw = _read_macro(text, name)
        if raw is not None:
            return _parse_int(raw)
    return None


def _set_macro(text: str, name: str, rendered: str) -> tuple[str, int]:
    pat = re.compile(rf"^(#define\s+{name}\b[ \t]+)(\([^)\n]*\)|\S+)", re.M)

    def repl(m: re.Match) -> str:
        old = m.group(2)
        new = f"({rendered})" if old.startswith("(") else rendered
        return m.group(1) + new

    return pat.subn(repl, text, count=1)


def _set_macro_all(files: dict[str, str], name: str, rendered: str) -> int:
    n = 0
    for key, text in files.items():
        new, c = _set_macro(text, name, rendered)
        files[key] = new
        n += c
    return n


def _set_first(files: dict[str, str], names: tuple[str, ...], rendered: str) -> str | None:
    for name in names:
        if _set_macro_all(files, name, rendered):
            return name
    return None


def _find_value_pair(regs: list[tuple[int, int]], value: int) -> tuple[int, int] | None:
    if value < 0 or value > 0xFFFF:
        return None
    for (r1, v1), (r2, v2) in zip(regs, regs[1:]):
        if r2 == r1 + 1 and (((v1 & 0xFF) << 8) | (v2 & 0xFF)) == value:
            return r1, r2
    return None


def _exposure_pair(seq: Sequence) -> tuple[int, int] | None:
    wanted = set(seq.exposure_addrs)
    if not wanted:
        return None
    for (r1, _), (r2, _) in zip(seq.regs, seq.regs[1:]):
        if r1 in wanted and r2 == r1 + 1:
            return r1, r2
    return None


def _guard_from_filename(name: str) -> str:
    stem = re.sub(r"[^A-Z0-9]", "_", Path(name).stem.upper())
    return f"__{stem}_H__"


def _format_array(symbol: str, regs: list[tuple[int, int]], banner: str) -> str:
    lines = [banner, f"static unsigned short {symbol}[] = {{"]
    for reg, val in regs:
        lines.append(f"    0x{reg:04x}, 0x{val:04x},")
    if not regs:
        lines.append("    /* 序列为空 */")
    lines.append("};")
    return "\n".join(lines)


def _find_arrays(text: str) -> list[tuple[str, int, int]]:
    found = []
    for m in re.finditer(r"static\s+unsigned\s+short\s+(\w+)\s*\[\s*\]\s*=\s*\{", text):
        depth = 1
        i = m.end()
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        end = i
        if end < len(text) and text[end] == ";":
            end += 1
        found.append((m.group(1), m.start(), end))
    return found


def _header_used_by_c(header: str, c_text: str) -> bool:
    for sym in re.findall(r"#define\s+(\w+)", header):
        if sym.startswith("_"):
            continue
        if re.search(rf"\b{re.escape(sym)}\b", c_text):
            return True
    return False


def _parse_res_name(name: str, model: str) -> dict | None:
    m = re.match(rf"{re.escape(model)}_(\d+)(x|_)(\d+)(.*)\.h$", name, re.I)
    if not m:
        return None
    return {"w": int(m.group(1)), "sep": m.group(2), "h": int(m.group(3)), "rest": m.group(4)}


def _retarget_token(token: str, old_w: int, old_h: int, new_w: int, new_h: int,
                    old_fps: int | None, new_fps: int | None) -> str:
    out = token.replace(f"{old_w}x{old_h}", f"{new_w}x{new_h}")
    out = out.replace(f"{old_w}X{old_h}", f"{new_w}X{new_h}")
    out = out.replace(f"{old_w}_{old_h}", f"{new_w}_{new_h}")
    if old_fps and new_fps and old_fps != new_fps:
        out = re.sub(rf"(?<!\d){old_fps}fps", f"{new_fps}fps", out, flags=re.I)
    return out


def _fresh_symbol(model: str, w: int, h: int, sep: str, fps: int | None, slave: bool) -> str:
    base = f"{model}_{w}{sep}{h}"
    if fps:
        base += f"_{fps}fps"
    if slave:
        base += "_slave"
    return base


def _fix_guard(text: str, guard: str) -> str:
    text = re.sub(r"#ifndef\s+\w+", f"#ifndef {guard}", text, count=1)
    text = re.sub(r"#define\s+\w+", f"#define {guard}", text, count=1)
    return text


def _replace_words(text: str, mapping: list[tuple[str, str]]) -> str:
    for old, new in sorted(mapping, key=lambda it: len(it[0]), reverse=True):
        if old == new:
            continue
        text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(old)}(?![A-Za-z0-9])", new, text)
    return text


def _rewrite_resolution_headers(files: dict[str, str], model: str, width: int | None,
                                height: int | None, fps: int | None,
                                master_regs: list[tuple[int, int]],
                                slave_regs: list[tuple[int, int]] | None,
                                old_fps: int | None) -> tuple[str | None, list[str]]:
    """按目标分辨率重命名头文件并写入数组。返回主机数组名和说明。"""
    notes: list[str] = []
    model_l = model.lower()
    c_text = "\n".join(v for k, v in files.items() if k.lower().endswith(".c"))
    planned: list[tuple[str, str, dict, str]] = []
    symbol_map: list[tuple[str, str]] = []
    for name, text in list(files.items()):
        meta = _parse_res_name(name, model_l)
        if meta is None:
            continue
        new_w = width or meta["w"]
        new_h = height or meta["h"]
        slave = "slave" in meta["rest"].lower() or "slave" in name.lower()
        rest = _retarget_token(meta["rest"], meta["w"], meta["h"], new_w, new_h, old_fps, fps)
        new_name = f"{model_l}_{new_w}{meta['sep']}{new_h}{rest}.h"
        arrays = _find_arrays(text)
        if arrays:
            for arr_name, _, _ in arrays:
                new_arr = _retarget_token(arr_name, meta["w"], meta["h"], new_w, new_h, old_fps, fps)
                symbol_map.append((arr_name, new_arr))
        planned.append((name, new_name, {**meta, "new_w": new_w, "new_h": new_h, "slave": slave}))

    # 文件名比数组名更长时先替换，避免 sc2336_1920_1080 命中 sc2336_1920_1080_slave.h
    mapping = [(a, b) for a, b in symbol_map if a and a != b]
    mapping += [(old, new) for old, new, _meta in planned if old != new]
    for key in list(files):
        files[key] = _replace_words(files[key], mapping)

    # 文件名冲突时保留原 rest，已经含在 new_name 里；仍冲突则加序号
    used: dict[str, int] = {}
    renamed: list[tuple[str, str, dict]] = []
    for old, new, meta in planned:
        if new in used or (new in files and new != old):
            stem = Path(new).stem + f"_{used.get(new, 1)}"
            new = stem + ".h"
            notes.append(f"分辨率头 {old} 与其它头重名，落成 {new}")
        used[new] = used.get(new, 0) + 1
        renamed.append((old, new, meta))

    new_files: dict[str, str] = {}
    master_symbol = None
    taken = {new for _, new, _ in renamed}
    for name, text in files.items():
        if name not in {old for old, _, _ in renamed}:
            if name not in taken:
                new_files[name] = text
    # includes 已在 files 里替换过，上面 new_files 取的是替换后的正文

    for old, new, meta in renamed:
        text = files[old]
        slave = meta["slave"]
        regs = slave_regs if slave and slave_regs is not None else master_regs
        banner = "/* 由客户序列生成，未实机验证。ISP 已下发初始化参数时以参数为准。 */"
        if slave and slave_regs is None:
            banner = "/* 未单独提供从机序列，暂与主机相同。多目前必须换成从机序列。 */"
        guard = _guard_from_filename(new)
        arrays = _find_arrays(text)
        if not master_regs:
            # 没有客户序列时不覆盖参考数组，只改文件名和守卫
            new_files[new] = _fix_guard(text, guard)
            if not slave and arrays:
                master_symbol = arrays[0][0]
            if old != new:
                notes.append(f"分辨率头 {old} → {new}")
            continue
        if arrays:
            # 从后往前替换，避免位移
            for arr_name, start, end in reversed(arrays):
                body = _format_array(arr_name, regs, banner)
                text = text[:start] + body + text[end:]
            text = _fix_guard(text, guard)
            if not slave:
                master_symbol = arrays[0][0]
            new_files[new] = text
            if old != new:
                notes.append(f"分辨率头 {old} → {new}")
            continue
        if _header_used_by_c(text, c_text):
            sym = _fresh_symbol(model_l, meta["new_w"], meta["new_h"], meta["sep"], fps, slave)
            text = _fix_guard(text, guard).rstrip() + "\n\n" + _format_array(sym, regs, banner) + "\n"
            new_files[new] = text
        else:
            sym = _fresh_symbol(model_l, meta["new_w"], meta["new_h"], meta["sep"], fps, slave)
            new_files[new] = (
                f"#ifndef {guard}\n#define {guard}\n\n"
                + _format_array(sym, regs, banner)
                + f"\n\n#endif /* {guard} */\n"
            )
        if not slave:
            master_symbol = sym
        notes.append(f"分辨率头 {old} → {new}")
    files.clear()
    files.update(new_files)
    return master_symbol, notes


def _inject_builtin_seq(files: dict[str, str], symbol: str) -> bool:
    """ISP 参数为空时回放内建数组。驱动本来就强制用内建数组的不改控制流。"""
    pat = re.compile(
        r"(?P<ind>[ \t]*)preg_info\s*=\s*para->reg_info\s*;[ \t]*\r?\n"
        r"(?P=ind)reg_num\s*=\s*para->num\s*;"
    )
    changed = False
    for key, text in files.items():
        if not key.lower().endswith(".c"):
            continue
        if re.search(r"preg_info\s*=\s*\(\s*void\s*\*\s*\)", text):
            continue
        repl = (
            f"\\g<ind>if (para->num > 0 && para->reg_info) {{\n"
            f"\\g<ind>    preg_info = para->reg_info;\n"
            f"\\g<ind>    reg_num = para->num;\n"
            f"\\g<ind>}} else {{\n"
            f"\\g<ind>    preg_info = (void *){symbol};\n"
            f"\\g<ind>    reg_num = sizeof({symbol}) / sizeof({symbol}[0]) / 2;\n"
            f"\\g<ind>}}"
        )
        new, n = pat.subn(repl, text, count=1)
        if n:
            files[key] = new
            changed = True
    return changed


def _decl_span(text: str, name: str) -> tuple[int, int] | None:
    """从数组声明到分号。"""
    m = re.search(rf"(?m)^[ \t]*.*\b{name}\b[^\n]*=\s*\{{", text)
    if not m:
        return None
    line = text.rfind("\n", 0, m.start()) + 1
    depth = 0
    started = False
    for i in range(m.end() - 1, len(text)):
        if text[i] == "{":
            depth += 1
            started = True
        elif text[i] == "}":
            depth -= 1
            if started and depth == 0:
                end = text.find(";", i)
                if end < 0:
                    return None
                return line, end + 1
    return None


def _expand_defines(text: str, expr: str) -> str:
    for name, value in re.findall(r"(?m)^#define\s+([A-Za-z_]\w*)\s+\((0x[0-9A-Fa-f]+)\)", text):
        expr = re.sub(rf"\b{name}\b", value, expr)
    for name, value in re.findall(r"(?m)^#define\s+([A-Za-z_]\w*)\s+(0x[0-9A-Fa-f]+)\s*$", text):
        expr = re.sub(rf"\b{name}\b", value, expr)
    return expr


def transplant_gain(files: dict[str, str], donor: str | None) -> str | None:
    """把同型号 SDK 驱动里的增益表和写寄存器顺序搬进当前平台的函数。"""
    if not donor or "regValTable" not in donor:
        return None
    table = _decl_span(donor, "regValTable")
    levels = _decl_span(donor, "gainLevelTable")
    if table is None:
        return None
    donor_table = donor[table[0]:table[1]]
    donor_levels = donor[levels[0]:levels[1]] if levels else None
    # 供体里从第一处 regValTable[i] 写到随后连续的 write 调用
    write_line = re.compile(r"^[ \t]*\w+\s*\(.*\)\s*;\s*$")
    lines = donor.splitlines()
    use = re.compile(r"regValTable\s*\[\s*[A-Za-z_]\w*\s*\]\s*\[\s*\d+\s*\]")
    start = next((i for i, line in enumerate(lines) if use.search(line)), None)
    if start is None:
        return None
    block: list[str] = []
    for line in lines[start:]:
        if write_line.match(line):
            block.append(_expand_defines(donor, line.strip()))
            continue
        if block and line.strip() == "":
            continue
        break
    if not block:
        return None
    changed = False
    for key, text in files.items():
        if "regValTable" not in text:
            continue
        ref_fn = None
        mfn = re.search(r"\b(\w+)\s*\(\s*[^,;]+,\s*0x[0-9A-Fa-f]+\s*,\s*regValTable", text)
        if mfn:
            ref_fn = mfn.group(1)
        new = text
        span = _decl_span(new, "regValTable")
        if span:
            new = new[:span[0]] + donor_table + new[span[1]:]
        if donor_levels:
            span = _decl_span(new, "gainLevelTable")
            if span:
                new = new[:span[0]] + donor_levels + new[span[1]:]
        ref_lines = new.splitlines()
        rstart = next((i for i, line in enumerate(ref_lines) if use.search(line)), None)
        if rstart is None or ref_fn is None:
            files[key] = new
            changed = True
            continue
        rend = rstart
        while rend < len(ref_lines) and (write_line.match(ref_lines[rend]) or ref_lines[rend].strip() == ""):
            rend += 1
        converted = []
        for line in block:
            line = re.sub(r"^\s*\w+\s*\(", f"{ref_fn}(", line, count=1)
            converted.append(line)
        ref_lines[rstart:rend] = converted
        joined = "\n".join(ref_lines)
        donor_tol = re.search(r"(?m)^[ \t]*tol_dig_gain\s*=\s*[^;]+;", donor)
        if donor_tol:
            var = "tmp_gain" if "tmp_gain" in joined else "gain"
            expr = re.sub(r"\bagain\b", var, donor_tol.group(0).strip())
            joined2, ntol = re.subn(r"(?m)^[ \t]*tol_dig_gain\s*=\s*[^;]+;", expr, joined, count=1)
            if ntol:
                joined = joined2
        files[key] = joined + ("\n" if text.endswith("\n") else "")
        changed = True
    return "增益表和增益寄存器换成 SDK 里同型号驱动的原始表" if changed else None


def _function_body(text: str, brace_at: int) -> str:
    depth = 0
    for i in range(brace_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[brace_at:i + 1]
    return ""


def donor_flip_addrs(donor: str | None) -> list[int]:
    """同型号驱动翻转函数里实际写入的寄存器地址，按出现顺序去重。"""
    if not donor:
        return []
    match = re.search(r"\w*flip_mirror\w*\s*\([^;]*\)\s*\{", donor)
    if not match:
        return []
    body = _function_body(donor, match.end() - 1)
    found: list[int] = []
    for hit in re.finditer(
        r"\b\w*write\w*\s*\(\s*[^,]+,\s*\(?\s*(0x[0-9a-fA-F]+)\s*\)?",
        body,
        re.I,
    ):
        addr = int(hit.group(1), 16)
        if addr not in found:
            found.append(addr)
    return found


def apply_donor_flip(files: dict[str, str], donor: str | None) -> str | None:
    """把翻转宏改成同型号驱动写入的地址。该函数还写了别的地址时，在原写入旁用同一个值补上。"""
    addrs = donor_flip_addrs(donor)
    if not addrs:
        return None
    got = _set_first(files, ("REG_FLIP_MIRROR", "FLIP_REG", "REG_FLIP"), f"0x{addrs[0]:04x}")
    if got is None:
        return None
    if len(addrs) > 1:
        extra = addrs[1:]
        pat = re.compile(
            r"^([ \t]*)(\w+)\s*\(([^,\n]+),\s*REG_FLIP_MIRROR\s*,\s*([^;\n]+)\)\s*;",
            re.M,
        )

        def repl(match: re.Match, pending: list[int] = extra) -> str:
            if not pending:
                return match.group(0)
            indent, fn, client, val = match.group(1), match.group(2), match.group(3), match.group(4)
            lines = [match.group(0)]
            for addr in pending:
                lines.append(f"{indent}{fn}({client}, 0x{addr:04x}, {val.strip()});")
            pending.clear()
            return "\n".join(lines)

        for key, text in files.items():
            files[key] = pat.sub(repl, text)
    shown = "、".join(f"0x{addr:x}" for addr in addrs)
    return f"翻转寄存器改为 SDK 同型号驱动的 {shown}"


def _pad_field(old: bytes, value: int) -> bytes:
    digits = str(value).encode()
    if len(digits) > len(old):
        return old
    return digits.rjust(len(old), b"0")


def patch_isp_conf(data: bytes, *, ref_id: int | None, new_id: int | None,
                   vts: int | None, fps: int | None) -> bytes:
    """在 SDK 原始 conf 上改文件头。长度不变，避免把后面的 3A 表挪位。"""
    buf = bytearray(data)
    if ref_id is not None and new_id is not None and ref_id != new_id:
        old = int(ref_id).to_bytes(4, "little")
        new = int(new_id).to_bytes(4, "little")
        at = buf.find(old, 0, 64)
        if at >= 0:
            buf[at:at + 4] = new
    text = bytes(buf)

    def sub_num(blob: bytes, key: bytes, value: int | None) -> bytes:
        if value is None:
            return blob
        m = re.search(key + rb"(\d+)", blob)
        if not m:
            return blob
        padded = _pad_field(m.group(1), value)
        return blob[:m.start(1)] + padded + blob[m.end(1):]

    text = sub_num(text, b"VTS=", vts)
    text = sub_num(text, b"MAX_FPS=", fps)
    return text


def _cap_max_fps(files: dict[str, str], new_fps: int) -> int:
    n = 0
    pat = re.compile(r"(MAX_FPS\s*=\s*)(\d+)\b")

    def repl(m: re.Match) -> str:
        nonlocal n
        if int(m.group(2)) == new_fps:
            return m.group(0)
        n += 1
        return m.group(1) + str(new_fps)

    for key, text in files.items():
        files[key] = pat.sub(repl, text)
    return n


def _patch_i2c(files: dict[str, str], addr: int) -> None:
    rendered = f"0x{addr:x}"
    _set_macro_all(files, "SENSOR_I2C_ADDR", rendered)
    for key, text in files.items():
        lines = text.splitlines(keepends=True)
        out = []
        for line in lines:
            if "sensor_i2c_addr" in line.lower() or "SENSOR_I2C_ADDR" in line:
                line = re.sub(r"0x[0-9A-Fa-f]+", rendered, line, count=1)
            out.append(line)
        files[key] = "".join(out)


def _patch_dvp_flag(files: dict[str, str], dvp: bool) -> None:
    val = "1" if dvp else "0"
    pat = re.compile(r"((?:static\s+)?int\s+dvp\s*=\s*)\d+")
    for key, text in files.items():
        files[key] = pat.sub(rf"\g<1>{val}", text, count=1)


def _format_init_txt(model: str, regs: list[tuple[int, int]], src: str | None) -> str:
    lines = [
        f"# {model} 初始化寄存器，由客户序列生成，未实机验证",
        f"# 来源: {src or '粘贴文本'}",
        "# 0xffff,N 表示延时 N 毫秒。二进制 ISP conf 需用 isptool 另存。",
    ]
    for reg, val in regs:
        if reg == 0xFFFF:
            lines.append(f"0xffff,{val}")
        else:
            lines.append(f"0x{reg:04x},0x{val:02x}" if val <= 0xFF else f"0x{reg:04x},0x{val:04x}")
    lines.append("")
    return "\n".join(lines)


def _apply_ids(files: dict[str, str], ref_model: str, model: str, seq: Sequence,
               written: list[str], gaps: list[str]) -> None:
    sensor_id = _read_macro_files(files, "SENSOR_ID")
    actual = None
    for name in ("ACTUAL_SENSOR_ID", "SENSOR_PHY_ID"):
        actual = _read_macro_files(files, name)
        if actual is not None:
            break
    ref_mid = model_id_of(ref_model)
    tgt_mid = model_id_of(model)
    logical_separate = (
        sensor_id is not None and ref_mid is not None and sensor_id == ref_mid
        and actual is not None and actual != sensor_id
    )
    probe = seq.probe_id
    if logical_separate:
        new_sensor = tgt_mid if tgt_mid is not None else probe
        new_probe = probe if probe is not None else actual
    else:
        new_sensor = probe if probe is not None else tgt_mid
        new_probe = probe if probe is not None else new_sensor
    if probe is not None and tgt_mid is not None and probe != tgt_mid and not logical_separate:
        gaps.append(
            f"型号名对应的 ID {tgt_mid:#x} 与序列读回 {probe:#x} 不一致，驱动按序列读回填写"
        )
        new_sensor = probe
        new_probe = probe
    if new_sensor is None:
        gaps.append("序列没有 Flag 读回值，SENSOR_ID 仍是参考型号")
        return
    if _set_macro_all(files, "SENSOR_ID", f"0x{new_sensor:x}"):
        written.append(f"SENSOR_ID = {new_sensor:#x}")
    if new_probe is None:
        return
    probe_txt = f"0x{new_probe:x}"
    touched = []
    for name in _PROBE_MACROS:
        if _set_macro_all(files, name, probe_txt):
            touched.append(name)
    if touched:
        written.append(f"读回 ID {probe_txt} 写入 " + "、".join(touched))
    phy_a = _read_macro_files(files, "SENSOR_PHY_ID_A")
    phy_b = _read_macro_files(files, "SENSOR_PHY_ID_B")
    if phy_a is not None and phy_b is not None and phy_a == phy_b and probe is not None:
        # 两者都被写成同一个读回值；若参考驱动原来不同，调用方在改写前已经看过。
        pass
    if seq.flag_reg is not None:
        got = _set_first(files, _ID_MSB, f"0x{seq.flag_reg:04x}")
        got_l = _set_first(files, _ID_LSB, f"0x{seq.flag_reg1:04x}") if seq.flag_reg1 is not None else None
        if got:
            written.append(f"ID 寄存器 {got}" + (f"/{got_l}" if got_l else ""))
        else:
            gaps.append(f"序列 ID 寄存器为 {seq.flag_reg:#x}，驱动里没有 REG_ID/ID_REG 宏")


def _hts_after_exposure(seq: Sequence) -> tuple[int, int] | None:
    """曝光寄存器后面紧挨的地址对。GC 系列里这通常是 HTS，并且就写在客户序列里。"""
    exp = _exposure_pair(seq)
    if exp is None:
        return None
    want = {exp[1] + 1, exp[1] + 2}
    found = {reg: val for reg, val in seq.regs if reg in want}
    if want <= set(found):
        hi, lo = exp[1] + 1, exp[1] + 2
        return hi, lo
    return None


def adapt_driver(files: dict[str, str], *, ref_model: str, model: str,
                 platform: str, mode: str = "single",
                 params_text: str | None = None, interface: str | None = None,
                 sequence_text: str | None = None, sequence_name: str | None = None,
                 donor_c: str | None = None) -> AdaptResult:
    """改一份参考驱动。files 的键是文件名，调用后再读取 result.files。"""
    written: list[str] = []
    gaps: list[str] = []
    written.append("快起 AE、上电时序、复位极性沿用本平台参考驱动的原始值")
    model_l = model.lower()
    renamed = {
        replace_model_tokens(name, ref_model, model_l): replace_model_tokens(text, ref_model, model_l)
        for name, text in files.items()
    }
    written.append(f"型号名和头文件守卫已写成 {model_l}")

    params = parse_customer_params(params_text)
    if interface and not params.get("interface"):
        params["interface"] = interface
    elif interface:
        params["interface"] = interface
    seq = parse_sequence(sequence_text, sequence_name)
    written.extend(seq.notes)

    width = params.get("width") or seq.width
    height = params.get("height") or seq.height
    if params.get("width") and seq.width and (params["width"], params.get("height")) != (seq.width, seq.height):
        gaps.append(
            f"客户分辨率 {params['width']}x{params.get('height')} 与序列 {seq.width}x{seq.height} 不一致，驱动按客户参数"
        )
    day_fps = params.get("day_fps") or seq.fps_hint
    if params.get("day_fps") and seq.fps_hint and params["day_fps"] != seq.fps_hint:
        gaps.append(f"序列文件名帧率 {seq.fps_hint}fps 与客户白天帧率 {params['day_fps']}fps 不一致，宏按客户参数")
    if params.get("night_fps"):
        written.append(f"夜晚 {params['night_fps']}fps 不写入最大帧率宏，运行时靠加大 VTS")

    iface = params.get("interface")
    lane = _lane_of(iface)
    if lane is None and (not iface or "dvp" not in iface.lower()):
        lane = seq.lane_hint
    dvp = bool(iface and "dvp" in iface.lower())

    old_fps = _read_macro_files(renamed, "DEF_FPS")
    if old_fps is None:
        for text in renamed.values():
            m = re.search(r"\bMAX_FPS\s*=\s*(\d+)", text)
            if m:
                old_fps = int(m.group(1))
                break

    old_phy_a = _read_macro_files(renamed, "SENSOR_PHY_ID_A")
    old_phy_b = _read_macro_files(renamed, "SENSOR_PHY_ID_B")

    master_symbol, res_notes = _rewrite_resolution_headers(
        renamed, model_l, width, height, day_fps, seq.regs,
        None, old_fps,
    )
    written.extend(res_notes)
    if seq.regs and master_symbol:
        if _inject_builtin_seq(renamed, master_symbol):
            written.append(f"初始化数组 {master_symbol}（{len(seq.regs)} 项）；ISP 参数为空时回放该数组")
        elif any(re.search(r"preg_info\s*=\s*\(\s*void\s*\*", t) for t in renamed.values()):
            written.append(f"初始化数组已替换为客户序列（{len(seq.regs)} 项）")
        else:
            gaps.append("分辨率头里已有初始化数组，但 .c 没有把它接到 init，需手工接上")
    elif not seq.regs:
        gaps.append("没有寄存器序列，初始化表未改")

    if "slave" in "".join(renamed) and mode != "single":
        gaps.append("多目模式没有单独的从机序列，从机头暂与主机相同")

    _apply_ids(renamed, ref_model, model_l, seq, written, gaps)
    if old_phy_a is not None and old_phy_b is not None and old_phy_a != old_phy_b and seq.probe_id is not None:
        written.append("本平台用 PHY_A 或 PHY_B 识别，序列只有一个读回值，两个宏都写成这个值")

    if width and _set_macro_all(renamed, "SENSOR_OUTPUT_WIDTH", str(width)):
        written.append(f"输出宽度 {width}")
    if height and _set_macro_all(renamed, "SENSOR_OUTPUT_HEIGHT", str(height)):
        written.append(f"输出高度 {height}")
    if day_fps is not None:
        if _set_macro_all(renamed, "DEF_FPS", str(day_fps)):
            written.append(f"DEF_FPS = {day_fps}")
        n_fps = _cap_max_fps(renamed, day_fps)
        if n_fps:
            written.append(
                f"MAX_FPS 赋值已改为 {day_fps}（{n_fps} 处）。配置号分支仍是本平台参考驱动的原值"
            )
    else:
        gaps.append("没有白天帧率，DEF_FPS 仍是参考型号")

    donor_mclk = _read_macro_files({"donor": donor_c}, "SENSOR_MCLK") if donor_c else None
    mclk = donor_mclk or seq.mclk_mhz
    if donor_mclk and seq.mclk_mhz and donor_mclk != seq.mclk_mhz:
        written.append(f"SENSOR_MCLK 采用 SDK 同型号驱动的 {donor_mclk}，序列注释是 {seq.mclk_mhz}")
    elif seq.mclk_mhz and seq.mclk_field_mhz and seq.mclk_mhz != seq.mclk_field_mhz:
        written.append(
            f"SENSOR_MCLK 用序列注释的 {seq.mclk_mhz}MHz。"
            f"度信 [Sensor] mclk 是 {seq.mclk_field_mhz}MHz，本平台宏按 MHz 计"
        )
    if mclk is None:
        mclk = seq.mclk_field_mhz
    if mclk and _set_macro_all(renamed, "SENSOR_MCLK", str(mclk)):
        written.append(f"SENSOR_MCLK = {mclk}")
    if seq.mbps and _set_macro_all(renamed, "MIPI_MBPS", str(seq.mbps)):
        written.append(f"MIPI_MBPS = {seq.mbps}")
    if dvp:
        if _set_macro_all(renamed, "SENSOR_IO_INTERFACE", "DVP_INTERFACE"):
            written.append("接口改为 DVP")
        _patch_dvp_flag(renamed, True)
    else:
        if lane and _set_macro_all(renamed, "MIPI_LANES", str(lane)):
            written.append(f"MIPI_LANES = {lane}")
        elif lane is None:
            gaps.append("没有 lane 数，MIPI_LANES 仍是参考型号")
        _patch_dvp_flag(renamed, False)

    if seq.i2c_mode is not None and seq.i2c_mode in _I2C_MODE_BYTES:
        addr_b, data_b = _I2C_MODE_BYTES[seq.i2c_mode]
        _set_macro_all(renamed, "SENSOR_REGADDR_BYTE", str(addr_b))
        _set_macro_all(renamed, "SENSOR_DATA_BYTE", str(data_b))
        written.append(f"寄存器地址/数据宽度 {addr_b}/{data_b} 字节")
    if seq.slave_id is not None:
        _patch_i2c(renamed, seq.slave_id)
        written.append(f"I2C 7 位地址 {seq.slave_id:#x}（实际地址仍以 DTS reg 或模块参数为准）")
    else:
        gaps.append("序列没有 SlaveID，I2C 地址仍是参考型号")

    if seq.vts is not None:
        pair = _find_value_pair(seq.regs, seq.vts)
        if _set_macro_all(renamed, "DEF_VTS", str(seq.vts)):
            written.append(f"DEF_VTS = {seq.vts}")
        if pair:
            got_h = _set_first(renamed, _VTS_MSB, f"0x{pair[0]:04x}")
            got_l = _set_first(renamed, _VTS_LSB, f"0x{pair[1]:04x}")
            if got_h:
                written.append(f"VTS 寄存器 {got_h}/{got_l} = {pair[0]:#x}/{pair[1]:#x}")
            else:
                gaps.append(f"序列 VTS={seq.vts} 对应 {pair[0]:#x}/{pair[1]:#x}，驱动里没有 VTS 寄存器宏")
        else:
            gaps.append(f"注释给出 VTS={seq.vts}，序列中没有对得上的连续寄存器")
    if seq.hts is not None:
        pair = _find_value_pair(seq.regs, seq.hts)
        if pair:
            got_h = _set_first(renamed, _HTS_MSB, f"0x{pair[0]:04x}")
            got_l = _set_first(renamed, _HTS_LSB, f"0x{pair[1]:04x}")
            if got_h:
                written.append(f"HTS 寄存器 {got_h}/{got_l}")
        else:
            gaps.append(f"注释给出 HTS={seq.hts}，序列中没有对得上的连续寄存器")
    else:
        followed = _hts_after_exposure(seq)
        if followed:
            got_h = _set_first(renamed, _HTS_MSB, f"0x{followed[0]:04x}")
            got_l = _set_first(renamed, _HTS_LSB, f"0x{followed[1]:04x}")
            if got_h:
                written.append(f"HTS 寄存器 {got_h}/{got_l} = {followed[0]:#x}/{followed[1]:#x}，取自曝光寄存器后的序列")
            else:
                written.append("序列里曝光寄存器后面有 HTS 地址，本平台驱动没有 HTS 宏")
        else:
            written.append("HTS 寄存器沿用本平台参考驱动。初始化数组里没有这对地址时，驱动会再从芯片读回")

    exp = _exposure_pair(seq)
    if exp:
        got_h = _set_first(renamed, _EXP_MSB, f"0x{exp[0]:04x}")
        got_l = _set_first(renamed, _EXP_LSB, f"0x{exp[1]:04x}")
        if got_h:
            written.append(f"曝光寄存器 {got_h}/{got_l} = {exp[0]:#x}/{exp[1]:#x}")
        else:
            gaps.append(f"曝光段指向 {exp[0]:#x}/{exp[1]:#x}，驱动里没有 EXP 寄存器宏")
    else:
        exp_addr = _read_macro_files(renamed, "REG_EXP_MSB")
        if exp_addr is None:
            exp_addr = _read_macro_files(renamed, "EXP_REG_H")
        if exp_addr is not None and seq.regs and not any(r == exp_addr for r, _ in seq.regs):
            gaps.append(f"曝光寄存器宏仍是参考型号的 {exp_addr:#x}，序列曝光段没有给出连续寄存器对")

    gained = transplant_gain(renamed, donor_c)
    if gained:
        written.append(gained)
    elif any("regValTable" in t or "gainLevelTable" in t for t in renamed.values()):
        written.append("增益表沿用本平台现有驱动的原始表")
    elif seq.gain_rows:
        gaps.append("序列含增益段，但参考驱动不是查表形式，没有自动改写增益函数")
    flip_note = apply_donor_flip(renamed, donor_c)
    if flip_note:
        written.append(flip_note)
    elif any(re.search(r"\b(?:REG_FLIP_MIRROR|FLIP_REG|REG_FLIP)\b", text) for text in renamed.values()):
        written.append("翻转寄存器沿用本平台驱动")

    family, chip, _key, _multi = profile_for(platform)
    bus = _bus_token(iface, lane)
    conf = conf_basename(model_l, bus, chip, mode)
    init_name = f"{model_l}_init_regs.txt" if seq.regs else None
    init_text = _format_init_txt(model_l, seq.regs, sequence_name) if seq.regs else None
    manifest = {
        "model": model_l,
        "ref_model": ref_model,
        "platform": platform,
        "family": family,
        "chip": chip,
        "mode": mode,
        "bus": bus,
        "conf_name": conf,
        "width": width,
        "height": height,
        "fps": day_fps,
        "vts": seq.vts,
        "sensor_id": _read_macro_files(renamed, "SENSOR_ID"),
        "i2c_addr_7bit": f"0x{seq.slave_id:x}" if seq.slave_id is not None else None,
        "driver_files": sorted(renamed),
        "init_regs": init_name,
        "written": written,
        "gaps": gaps,
    }
    return AdaptResult(renamed, written, gaps, init_name, init_text, conf, manifest)


def render_plan_markdown(*, model: str, platform: str, version: str | None, job_id: str,
                         cis_mode: str | None, ref_model: str, params_text: str | None,
                         seq_note: str, up_section: str, result: AdaptResult) -> str:
    written = "\n".join(f"- {item}" for item in result.written) or "- （无）"
    gaps = "\n".join(f"- {item}" for item in result.gaps) or "- （无）"
    ver = version or ""
    return (
        f"# {model} 调试方案（{platform} {ver}）\n\n"
        f"- 任务号：{job_id}　CIS 模式：{cis_mode or '1（单目）'}\n"
        f"- 驱动结构沿用 {platform} 现有 CIS 驱动，参数和序列按客户资料写入\n"
        f"- 来源：{model} 不在 {platform} 支持列表，参数由客户提供，**未实机验证**\n\n"
        f"## 已确认参数（客户提供，上板前与 datasheet 复核）\n\n"
        f"{params_text or '（未提供）'}\n\n"
        f"## 已写入驱动\n\n{written}\n\n"
        f"## 仍待复核\n\n{gaps}\n\n"
        f"## 初始化序列\n\n{seq_note}\n\n"
        f"{up_section}"
        f"## 合入 SDK\n\n"
        f"本目录有 `merge_into_sdk.py`。在含 `config.mk` 的 SDK 版本目录旁执行"
        f"（先 `--dry-run` 看报告）：\n\n"
        f"```text\n"
        f"python merge_into_sdk.py --sdk <SDK版本目录> [--project <工程目录>] [--unpack] [--dry-run] [--select]\n"
        f"```\n\n"
        f"脚本按目录里实际有的文件落点，不按型号写死路径。"
        f"`--sdk` 把源码放进 `sensor/{model.lower()}/`，并给每个已有 `dir_y +=` 的分支加一行；"
        f"源码若还在 `os/driver_src.tar.gz` 里，加 `--unpack`（不回写 tar）。"
        f"`--project` 指向 `system/<平台>/<版本>` 或其中的 `board/<板级>`："
        f"改板级 `config.mk`，把摄像头配置里的 isp 路径改成目标 conf 名，"
        f"并在已有 sensor 启动脚本的目录写 `{model.lower()}.sh`。"
        f"`CONFIG_CIS_TYPE` 在 SDK 上追加；工程里若当前只写了一颗，换成新型号。"
        f"`CONFIG_SENSOR_TYPE` 在 SDK 上已有别的型号时要加 `--select`，在 `--project` 上会换成新型号。"
        f"方案里的 `{result.conf_name}` 用本平台参考 conf 改了 sensor id、VTS、MAX_FPS，"
        f"3A 表仍是那份原始文件。合入时和寄存器表一起放到 isptool 与工程配置目录。"
        f"`.a` / `.ko` 仍用 SDK 现有方式编译。\n\n"
        f"## 上板\n\n"
        f"1. probe 读 ID（check_id 仍为 0，确认地址和 ID 后再打开）\n"
        f"2. 出图，核对分辨率、lane、帧率\n"
        f"3. AE 扫描。增益表和曝光寄存器若在待复核里，这一步不能当通过\n"
        f"4. 翻转、多目、AOV 按需再改\n"
    )


def manifest_json(manifest: dict) -> str:
    return json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
