"""Push firmware by case. Burntool is not used.

usr:   one usr.sqsh4 into the device APP partition.
image: partitions listed in the product burntool config, written on the running
       device with mtd_debug. The device /proc/mtd decides names and sizes.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from devstudio.tools.device.frame import DeviceLink

NO_BURNTOOL = "不使用 burntool。"
BOOT_PARTS = {"SPL", "OPENSBI", "ENV", "ENVBK", "DTB", "UBOOT", "BOOT"}
DEFAULT_PARTS = ("APP", "KERNEL", "ROOTFS")
_ORDER = {"APP": 0, "CONFIG": 1, "KERNEL": 2, "ROOTFS": 9}
_ENTRY = re.compile(r"^\s*download_to_nand\d+\s*=\s*([^,]+),\s*([^,]+),\s*(0x[0-9a-fA-F]+|\d+),\s*(0x[0-9a-fA-F]+|\d+)")
_MTD = re.compile(r'^(mtd\d+):\s+([0-9a-fA-F]+)\s+[0-9a-fA-F]+\s+"([^"]+)"')

_USR_SCRIPT = r"""
set -e
IMG=/tmp/devstudio_usr.sqsh4
LINE=$(grep '"APP"' /proc/mtd || true)
if [ -z "$LINE" ]; then
  echo FAIL no APP partition
  exit 1
fi
NAME=$(echo "$LINE" | awk -F: '{print $1}')
SIZE=$(echo "$LINE" | awk '{print $2}')
BYTES=$((0x$SIZE))
FILE_BYTES=$(wc -c < "$IMG")
if [ "$FILE_BYTES" -gt "$BYTES" ]; then
  echo FAIL image larger than APP
  exit 1
fi
mtd_debug erase /dev/$NAME 0 $BYTES
mtd_debug write /dev/$NAME 0 $FILE_BYTES "$IMG"
sync
echo OK usr /dev/$NAME
"""


@dataclass
class Part:
    file: str
    name: str
    offset: int
    size: int


@dataclass
class Options:
    kind: str = ""
    image: Path | None = None
    folder: Path | None = None
    config: str = ""
    parts: list[str] | None = None
    yes: bool = False


def run_firmware(project: Path, board: str, options: Options) -> int:
    if options.kind == "usr":
        return _usr(board, options.image)
    if options.kind == "image":
        return _image(project, board, options)
    sys.stdout.write("固件只分 usr 和 image。未烧录。" + NO_BURNTOOL + "\n")
    return 1


def find_configs(project: Path) -> list[Path]:
    """Burntool configs named by burn.ini under project/<product>/system."""
    name = _product(project)
    base = project / "project"
    roots = [base / name / "system"] if name else sorted(base.glob("*/system"))
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for ini in sorted(root.rglob("tools/burntool/burn.ini")):
            text = _read_text(ini)
            match = re.search(r"^\s*File Name\s*=\s*(\S+)", text, re.MULTILINE)
            if match and (ini.parent / match.group(1)).is_file():
                found.append(ini.parent / match.group(1))
    return found


def parse_config(path: Path) -> list[Part]:
    parts = []
    for line in _read_text(path).splitlines():
        match = _ENTRY.match(line)
        if match:
            parts.append(Part(match.group(1).strip(), match.group(2).strip().upper(),
                              int(match.group(3), 0), int(match.group(4), 0)))
    return parts


def parse_mtd(text: str) -> dict[str, tuple[str, int]]:
    table = {}
    for line in text.splitlines():
        match = _MTD.match(line.strip())
        if match:
            table[match.group(3).upper()] = (match.group(1), int(match.group(2), 16))
    return table


def _image(project: Path, board: str, options: Options) -> int:
    configs = find_configs(project)
    if not configs:
        sys.stdout.write("整包分区表未收录（产品下没有 tools/burntool/burn.ini）。未烧录。" + NO_BURNTOOL + "\n")
        return 1
    if options.config:
        configs = [item for item in configs if options.config in item.as_posix()]
    if len(configs) != 1:
        sys.stdout.write("分区表有多份，用 --config 指定其一：\n")
        for item in find_configs(project):
            sys.stdout.write(f"  {_display(project, item)}\n")
        sys.stdout.write("未烧录。" + NO_BURNTOOL + "\n")
        return 1
    config = configs[0]
    table = {part.name: part for part in parse_config(config)}
    sys.stdout.write(f"分区表：{_display(project, config)}（只读数据。{NO_BURNTOOL}）\n")
    wanted = [item.upper() for item in (options.parts or DEFAULT_PARTS)]
    boot = [item for item in wanted if item in BOOT_PARTS]
    if boot:
        sys.stdout.write(f"引导分区 {','.join(boot)} 不在运行中的系统里写。未烧录。\n")
        return 1
    missing = [item for item in wanted if item not in table]
    if missing:
        sys.stdout.write(f"分区表里没有 {','.join(missing)}。未烧录。\n")
        return 1
    chosen = sorted((table[item] for item in wanted), key=lambda part: _ORDER.get(part.name, 5))
    if options.folder is None:
        for part in chosen:
            sys.stdout.write(f"  {part.name:<8} {part.file:<28} 表内长度 {part.size:#x}\n")
        sys.stdout.write("没有 --dir 镜像目录。未烧录。\n")
        return 1
    images: dict[str, bytes] = {}
    for part in chosen:
        path = options.folder / part.file
        if not path.is_file():
            sys.stdout.write(f"缺少 {path}。未烧录。\n")
            return 1
        images[part.name] = path.read_bytes()
    try:
        with DeviceLink(board, timeout=120) as link:
            result = link.exec("cat /proc/mtd", timeout=10)
            mtd = parse_mtd(str(result.get("out") or ""))
            if not _check(chosen, images, mtd):
                return 1
            if not options.yes:
                sys.stdout.write("以上为计划。未烧录。确认后加 --yes 执行。\n")
                return 0
            return _write_parts(link, chosen, images, mtd)
    except (OSError, TimeoutError, ConnectionError) as exc:
        sys.stdout.write(f"未连接 {board}：{exc}。未烧录。" + NO_BURNTOOL + "\n")
        return 1


def _check(chosen: list[Part], images: dict[str, bytes], mtd: dict[str, tuple[str, int]]) -> bool:
    if not mtd:
        sys.stdout.write("设备 /proc/mtd 没有分区。未烧录。\n")
        return False
    ok = True
    for part in chosen:
        size = len(images[part.name])
        if part.name not in mtd:
            sys.stdout.write(f"  {part.name:<8} 设备上没有该分区。\n")
            ok = False
            continue
        dev, dev_size = mtd[part.name]
        note = "" if dev_size == part.size else f"（表内 {part.size:#x}，以设备为准）"
        state = "可写" if size <= dev_size else "镜像超出分区"
        ok = ok and size <= dev_size
        sys.stdout.write(f"  {part.name:<8} /dev/{dev} 分区 {dev_size:#x}{note} 镜像 {size:#x} {state}\n")
    if not ok:
        sys.stdout.write("分区检查未通过。未烧录。\n")
    return ok


def _write_parts(link: DeviceLink, chosen: list[Part], images: dict[str, bytes], mtd: dict[str, tuple[str, int]]) -> int:
    for part in chosen:
        dev, dev_size = mtd[part.name]
        data = images[part.name]
        tmp = f"/tmp/devstudio_{part.name.lower()}.img"
        link.file_put(tmp, data, timeout=120)
        script = (
            f"set -e\nmtd_debug erase /dev/{dev} 0 {dev_size}\n"
            f"mtd_debug write /dev/{dev} 0 {len(data)} {tmp}\nrm -f {tmp}\necho OK {part.name}\n"
        )
        result = link.exec(script, timeout=300)
        output = str(result.get("out") or "")
        if f"OK {part.name}" not in output:
            sys.stdout.write(output + f"{part.name} 写入失败，后续分区未写。\n")
            return 1
        sys.stdout.write(f"  {part.name} 已写入 /dev/{dev}\n")
    link.exec("sync", timeout=30)
    sys.stdout.write("整包分区已写入。" + NO_BURNTOOL + "完成等级：L4。重启和界面须人确认后到 L5。\n")
    return 0


def _usr(board: str, image: Path | None) -> int:
    if image is None or not image.is_file():
        sys.stdout.write("没有 usr.sqsh4。未烧录。" + NO_BURNTOOL + "\n")
        return 1
    data = image.read_bytes()
    try:
        with DeviceLink(board, timeout=60) as link:
            link.file_put("/tmp/devstudio_usr.sqsh4", data, timeout=60)
            result = link.exec(_USR_SCRIPT, timeout=120)
    except (OSError, TimeoutError, ConnectionError) as exc:
        sys.stdout.write(f"未连接 {board}：{exc}。未烧录。" + NO_BURNTOOL + "\n")
        return 1
    output = str(result.get("out") or "")
    sys.stdout.write(output)
    if result.get("ok") is True or "OK usr " in output:
        sys.stdout.write("用户区已按设备 APP 分区写入。" + NO_BURNTOOL + "完成等级：L4。重启和界面须人确认后到 L5。\n")
        return 0
    sys.stdout.write("用户区未写入。" + NO_BURNTOOL + "\n")
    return 1


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


def _product(project: Path) -> str:
    path = project / "devstudio.yaml"
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^project:\s*\n\s+name:\s*(\S+)", text, re.MULTILINE)
    return match.group(1) if match else ""


def _display(project: Path, path: Path) -> str:
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return path.as_posix()
