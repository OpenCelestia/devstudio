"""Build expected device modules/nodes/processes from SDK/product scripts,
then compare against the live board via Studio EXEC.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from devstudio.adapter.catalog import adapters_root
from devstudio.tools.device.connect import resolve_board
from devstudio.tools.device.frame import DeviceLink
from devstudio.yamlio import load_yaml

_INSMOD_RE = re.compile(
    r"\b(?:insmod|modprobe|insmod_if_absent|insmod_check)\s+([^\s;|&]+)",
    re.I,
)
_KO_ASSIGN_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(?:\"([^\"]+\.ko)\"|'([^']+\.ko)'|([^\s#]+\.ko))\s*$",
    re.M,
)
_DEV_RE = re.compile(r"(/dev/[A-Za-z0-9_./-]+)")
_CALL_SH_RE = re.compile(
    r"(?:^|[\s;|&])(?:\.|/usr/sbin/|/usr/bin/)?([A-Za-z0-9_.-]+\.sh)\b",
)
_SKIP_DEV = {
    "/dev/null",
    "/dev/zero",
    "/dev/console",
    "/dev/tty",
    "/dev/random",
    "/dev/urandom",
}
_COARSE_DEV = {"/dev/input", "/dev/mtdblock", "/dev/mtd", "/dev/block"}
_BOOT_SCRIPTS = (
    "app_01_start.sh",
    "app_02_load.sh",
)
_FOLLOW_SCRIPTS = (
    "wifi_driver.sh",
    "ov8856.sh",
    "lcd.sh",
    "imu_driver.sh",
    "tf_card.sh",
)


def run_check(argv: list[str], project: Path) -> int:
    board = ""
    expect_path = ""
    save_adapter = False
    args = list(argv)
    while args:
        item = args.pop(0)
        if item == "--board" and args:
            board = args.pop(0)
        elif item == "--expect" and args:
            expect_path = args.pop(0)
        elif item == "--save-adapter":
            save_adapter = True
        elif item in ("-h", "--help"):
            sys.stdout.write(
                "用法：devstudio device check [--board 板号] [--expect 文件] [--save-adapter]\n"
                "从产品板级启动脚本 / Adapter 读取期望模块、节点、进程，经 7777 EXEC 与实机比对。\n"
            )
            return 0
        else:
            sys.stderr.write(
                "用法：devstudio device check [--board 板号] [--expect 文件] [--save-adapter]\n"
            )
            return 2

    board_arg = (board or "").strip()
    expected = load_expected(project, Path(expect_path) if expect_path else None)
    if save_adapter:
        path = _write_adapter_expect(project, expected)
        sys.stdout.write(f"已写入期望基线：{path}\n")

    if not any(expected.get(k) for k in ("modules", "nodes", "processes", "mounts")):
        sys.stdout.write(
            "工程/Adapter 未解析到期望项。请确认 project/<产品>/system/.../board/<CONFIG_BOARD> "
            "启动脚本存在，或提供 --expect JSON。\n"
        )
        return 1

    _print_expected(expected)

    if not board_arg:
        if save_adapter:
            sys.stdout.write("未给 --board，仅写入期望，未比对实机。\n")
            return 0
        chosen = resolve_board(project, None)
    else:
        chosen = resolve_board(project, board_arg)
    if not chosen:
        sys.stdout.write(
            "NEED_BOARD ASK_UI=board_ip：比对实机需要板号。"
            "收集 IP 后：devstudio device check --board <IP>\n"
        )
        return 1

    live = _probe_live(chosen, expected)
    if live is None:
        return 1
    return _compare(expected, live)


def load_expected(project: Path, expect_file: Path | None = None) -> dict:
    """Parse current product board start chain; optional Adapter/--expect overlay."""
    base: dict = {
        "modules": [],
        "nodes": [],
        "processes": [],
        "mounts": [],
        "sources": [],
        "config_board": "",
    }
    release = ""
    product = ""
    yaml_path = project / "devstudio.yaml"
    if yaml_path.is_file():
        data = load_yaml(yaml_path)
        release = str(data.get("release_id") or "")
        project_block = data.get("project") if isinstance(data.get("project"), dict) else {}
        product = str(project_block.get("name") or "")

    parsed = _parse_product_scripts(project, product)
    _merge(base, parsed)
    base["config_board"] = parsed.get("config_board") or ""

    # 脚本已解析出期望时，不再把旧 Adapter 条目并进来（避免脏基线污染）。
    # 无脚本结果时才回退 Adapter；--expect 始终作为显式覆盖补充。
    if not any(base.get(k) for k in ("modules", "nodes", "processes")):
        adapter_expect = adapters_root() / release / "device_expect.json" if release else None
        if adapter_expect and adapter_expect.is_file():
            _merge(base, _sanitize_expect(_read_expect_json(adapter_expect)))
            base["sources"].append(str(adapter_expect))

    if expect_file and expect_file.is_file():
        _merge(base, _sanitize_expect(_read_expect_json(expect_file)))
        base["sources"].append(str(expect_file))

    if "/mnt/install" not in base["mounts"]:
        base["mounts"].append("/mnt/install")

    # 触摸兜底节点（脚本里写死 event0）
    if "/dev/input/event0" not in base["nodes"]:
        base["nodes"].append("/dev/input/event0")

    # 相机节点：AV200 走 vicap；其它芯片才默认 video0
    mods = {m.lower() for m in base["modules"]}
    if "ak_vicap" in mods or any(m.startswith("ak_venc") for m in mods):
        if "/dev/vicap-0-0-0" not in base["nodes"]:
            base["nodes"].append("/dev/vicap-0-0-0")
    elif "/dev/video0" not in base["nodes"]:
        base["nodes"].append("/dev/video0")

    main = _config_value(project, product, "CONFIG_APP_MAIN_TARGET") or "nova_main"
    base["processes"] = _uniq([main, "studio"])

    base["modules"] = _uniq([_norm_module(m) for m in base["modules"] if _norm_module(m)])
    base["nodes"] = _uniq([n for n in base["nodes"] if _ok_node(n)])
    base["mounts"] = _uniq(base["mounts"])
    base["release_id"] = release
    base["product"] = product
    return base


def _parse_product_scripts(project: Path, product: str) -> dict:
    out: dict = {
        "modules": [],
        "nodes": [],
        "processes": [],
        "mounts": [],
        "sources": [],
        "config_board": "",
    }
    board_name = _config_value(project, product, "CONFIG_BOARD")
    out["config_board"] = board_name or ""

    scripts = _collect_boot_scripts(project, product, board_name)
    seen_files: set[Path] = set()
    queue: list[Path] = list(scripts)

    while queue:
        path = queue.pop(0)
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen_files or not path.is_file():
            continue
        seen_files.add(resolved)
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        out["sources"].append(
            str(path.relative_to(project)) if _under(project, path) else str(path)
        )
        assigns = _ko_assigns(text)
        for match in _INSMOD_RE.finditer(text):
            token = match.group(1).strip().strip("'\"")
            if token.startswith("-"):
                continue
            if token.startswith("$"):
                key = token[1:].strip("{}")
                token = assigns.get(key, "")
                if not token:
                    continue
            name = Path(token).name
            if name.endswith(".ko"):
                out["modules"].append(name[: -len(".ko")])
        for match in _DEV_RE.finditer(text):
            node = match.group(1).rstrip("/")
            if _ok_node(node):
                out["nodes"].append(node)
        if "/mnt/install" in text:
            out["mounts"].append("/mnt/install")

        # 跟随启动链里点到的关键驱动脚本（同产品树内）
        for name in _FOLLOW_SCRIPTS:
            if name not in text:
                continue
            found = _find_script(project, product, board_name, name)
            if found:
                queue.append(found)

        for match in _CALL_SH_RE.finditer(text):
            name = match.group(1)
            if name in _BOOT_SCRIPTS or name.endswith("_main.sh") or name == "main.sh":
                continue
            if name not in _FOLLOW_SCRIPTS and not name.endswith("_driver.sh"):
                continue
            found = _find_script(project, product, board_name, name)
            if found and found.resolve() not in seen_files:
                queue.append(found)

    return out


def _sdk_layout(project: Path, product: str) -> tuple[str, str]:
    """Return (soc_platform, sdk_version) from release_id / yaml sdk.root."""
    yaml_path = project / "devstudio.yaml"
    release = ""
    sdk_root = ""
    if yaml_path.is_file():
        data = load_yaml(yaml_path)
        release = str(data.get("release_id") or "")
        sdk = data.get("sdk") if isinstance(data.get("sdk"), dict) else {}
        sdk_root = str(sdk.get("root") or "")
    if sdk_root:
        parts = Path(sdk_root.replace("\\", "/")).parts
        # system/AnyCloud39AV200/V1.04
        if len(parts) >= 3 and parts[0] == "system":
            return parts[1], parts[2]
        if len(parts) >= 2:
            return parts[-2], parts[-1]
    if "_" in release:
        # AnyCloud39AV200_V1.04
        soc, ver = release.rsplit("_", 1)
        return soc, ver
    board = _config_value(project, product, "CONFIG_SOC_PLATFORM")
    ver = _config_value(project, product, "CONFIG_SDK_VERSION")
    return board, ver


def _board_root(project: Path, product: str, board_name: str) -> Path | None:
    if not product or not board_name:
        return None
    soc, ver = _sdk_layout(project, product)
    candidates = []
    if soc and ver:
        candidates.append(
            project / "project" / product / "system" / soc / ver / "board" / board_name
        )
        candidates.append(project / "system" / soc / ver / "board" / board_name)
    candidates.append(project / "project" / product / "board" / board_name)
    for path in candidates:
        if path.is_dir():
            return path
    return None


def _system_roots(project: Path, product: str) -> list[Path]:
    soc, ver = _sdk_layout(project, product)
    roots: list[Path] = []
    if product and soc and ver:
        base = project / "project" / product / "system" / soc / ver
        for sub in ("board", "patch", "patch_fastsys"):
            cand = base / sub
            if cand.is_dir():
                roots.append(cand)
        if base.is_dir():
            roots.append(base)
    sys_root = project / "system"
    if soc and ver:
        cand = sys_root / soc / ver
        if cand.is_dir():
            roots.append(cand)
    if sys_root.is_dir():
        roots.append(sys_root)
    return roots


def _collect_boot_scripts(project: Path, product: str, board_name: str) -> list[Path]:
    """Prefer CONFIG_BOARD rootfs scripts; avoid whole-tree rglob on slow FS."""
    found: list[Path] = []
    board_root = _board_root(project, product, board_name)
    if board_root is not None:
        for name in _BOOT_SCRIPTS:
            for sub in (
                board_root / "rootfs" / "utils" / "etc" / "config" / name,
                board_root / "rootfs" / "etc" / "config" / name,
            ):
                if sub.is_file():
                    found.append(sub)
            # shallow fallback under this board only
            config_dir = board_root / "rootfs" / "utils" / "etc" / "config"
            if config_dir.is_dir():
                direct = config_dir / name
                if direct.is_file():
                    found.append(direct)
        if found:
            return _uniq_paths(found)

    for root in _system_roots(project, product)[:4]:
        for name in _BOOT_SCRIPTS:
            for sub in (
                "rootfs/utils/etc/config",
                "rootfs/etc/config",
                "utils/etc/config",
            ):
                cand = root / sub / name
                if cand.is_file():
                    found.append(cand)
        if found:
            break
    return _uniq_paths(found)[:20]


def _find_script(project: Path, product: str, board_name: str, name: str) -> Path | None:
    """Locate a driver helper script without recursive full-tree walks."""
    rels = (
        f"rootfs/utils/usr/sbin/{name}",
        f"rootfs/usr/sbin/{name}",
        f"utils/usr/sbin/{name}",
        f"usr/sbin/{name}",
    )
    board_root = _board_root(project, product, board_name)
    search: list[Path] = []
    if board_root is not None:
        search.append(board_root)
    search.extend(_system_roots(project, product))
    for root in search:
        for rel in rels:
            cand = root / rel
            if cand.is_file():
                return cand
            # patch/board nesting: .../patch/rootfs/...
            if root.name in ("board", "patch", "patch_fastsys"):
                continue
            for sub in ("patch", "patch_fastsys", "board"):
                nested = root / sub
                if not nested.is_dir():
                    continue
                # one level of board name dirs under board/
                if sub == "board" and board_name:
                    for rel2 in rels:
                        cand2 = nested / board_name / rel2
                        if cand2.is_file():
                            return cand2
                for rel2 in rels:
                    cand2 = nested / rel2
                    if cand2.is_file():
                        return cand2
    return None


def _uniq_paths(items: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for item in items:
        key = str(item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _config_value(project: Path, product: str, key: str) -> str:
    cfg = project / "project" / product / "config.mk" if product else None
    if cfg is None or not cfg.is_file():
        projects = project / "project"
        if projects.is_dir():
            for child in sorted(projects.iterdir()):
                cand = child / "config.mk"
                if cand.is_file():
                    cfg = cand
                    break
    if cfg is None or not cfg.is_file():
        return ""
    for line in cfg.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = line.strip()
        if raw.startswith("#") or key not in raw or "=" not in raw:
            continue
        left, right = raw.split("=", 1)
        left = left.replace("?", "").strip()
        if left != key:
            continue
        return right.strip().split()[0].strip()
    return ""


def _probe_live(board: str, expected: dict) -> dict | None:
    nodes = expected.get("nodes") or []
    processes = [p for p in (expected.get("processes") or []) if p != "studio"]
    mounts = expected.get("mounts") or []
    parts = [
        "echo BEGIN_DEVSTUDIO_CHECK",
        "echo MODULES_BEGIN",
        "lsmod 2>/dev/null || true",
        "echo MODULES_END",
        "echo NODES_BEGIN",
    ]
    for node in nodes:
        parts.append(
            f"if test -e {_sh(node)}; then echo NODE_OK {node}; else echo NODE_MISS {node}; fi"
        )
    parts.append("echo NODES_END")
    parts.append("echo PROCS_BEGIN")
    for proc in processes:
        parts.append(
            f"if pidof {_sh(proc)} >/dev/null 2>&1; then echo PROC_OK {proc}; "
            f"else echo PROC_MISS {proc}; fi"
        )
    # studio 常链进主进程；以 7777 监听为准
    parts.append(
        "if netstat -ltn 2>/dev/null | grep -q ':7777' || "
        "ss -ltn 2>/dev/null | grep -q ':7777'; "
        "then echo PROC_OK studio; else echo PROC_MISS studio; fi"
    )
    parts.append("echo PROCS_END")
    parts.append("echo MOUNTS_BEGIN")
    for mount in mounts:
        parts.append(
            f'if grep -q " {mount} " /proc/mounts 2>/dev/null || test -d {_sh(mount)}; '
            f"then echo MOUNT_OK {mount}; else echo MOUNT_MISS {mount}; fi"
        )
    parts.append("echo MOUNTS_END")
    parts.append("echo MTD_BEGIN")
    parts.append("cat /proc/mtd 2>/dev/null || true")
    parts.append("echo MTD_END")
    parts.append("echo END_DEVSTUDIO_CHECK")
    script = "\n".join(parts)
    try:
        with DeviceLink(board, timeout=25) as link:
            link.ping()
            result = link.exec(script, timeout=20)
    except (OSError, TimeoutError, ConnectionError) as exc:
        sys.stdout.write(f"未连接 {board}：{exc}\n")
        return None
    out = str(result.get("out") or result.get("error") or "")
    if "BEGIN_DEVSTUDIO_CHECK" not in out:
        sys.stdout.write(f"设备 EXEC 无有效输出：{out[:200]}\n")
        return None
    return {
        "raw": out,
        "modules_loaded": _parse_lsmod(_section(out, "MODULES_BEGIN", "MODULES_END")),
        "nodes": _parse_flags(out, "NODE_OK", "NODE_MISS"),
        "processes": _parse_flags(out, "PROC_OK", "PROC_MISS"),
        "mounts": _parse_flags(out, "MOUNT_OK", "MOUNT_MISS"),
        "mtd": _section(out, "MTD_BEGIN", "MTD_END"),
        "studio_link": True,
    }


def _compare(expected: dict, live: dict) -> int:
    failed = 0
    loaded = {name.lower() for name in live.get("modules_loaded") or []}

    board_cfg = expected.get("config_board") or ""
    if board_cfg:
        sys.stdout.write(f"\nCONFIG_BOARD={board_cfg}\n")

    sys.stdout.write("\n## 模块（板级启动链 insmod / Adapter）\n")
    for name in expected.get("modules") or []:
        ok = name.lower() in loaded or any(name.lower() in item for item in loaded)
        mark = "OK" if ok else "MISS"
        if not ok:
            failed += 1
        sys.stdout.write(f"[{mark}] module {name}\n")

    sys.stdout.write("\n## 节点（脚本/SDK 期望 vs 实机）\n")
    node_map = live.get("nodes") or {}
    for node in expected.get("nodes") or []:
        ok = node_map.get(node) is True
        mark = "OK" if ok else "MISS"
        if not ok:
            failed += 1
        sys.stdout.write(f"[{mark}] node {node}\n")

    sys.stdout.write("\n## 进程\n")
    proc_map = live.get("processes") or {}
    for proc in expected.get("processes") or []:
        ok = proc_map.get(proc) is True
        # DeviceLink 已连通时，studio 端口判据再兜底一次
        if proc == "studio" and not ok and live.get("studio_link"):
            ok = True
        mark = "OK" if ok else "MISS"
        if not ok:
            failed += 1
        sys.stdout.write(f"[{mark}] process {proc}\n")

    sys.stdout.write("\n## 挂载\n")
    mount_map = live.get("mounts") or {}
    for mount in expected.get("mounts") or []:
        ok = mount_map.get(mount) is True
        mark = "OK" if ok else "MISS"
        if not ok:
            failed += 1
        sys.stdout.write(f"[{mark}] mount {mount}\n")

    mtd = (live.get("mtd") or "").strip()
    if mtd:
        sys.stdout.write("\n## /proc/mtd（实机）\n")
        for line in mtd.splitlines()[:20]:
            sys.stdout.write(line + "\n")
        if '"APP"' not in mtd and "APP" not in mtd:
            sys.stdout.write("[MISS] /proc/mtd 未见 APP 分区名\n")
            failed += 1
        else:
            sys.stdout.write("[OK] /proc/mtd 含 APP\n")

    if failed:
        sys.stdout.write(
            f"\n比对未通过：{failed} 项缺失。对照 celestia-debug D 类（insmod/节点）与板级启动脚本。\n"
        )
        return 1
    sys.stdout.write("\n比对通过：期望项均在实机上找到。\n")
    return 0


def _print_expected(expected: dict) -> None:
    sys.stdout.write(
        f"期望来源产品={expected.get('product') or '?'}  "
        f"release_id={expected.get('release_id') or '?'}"
    )
    if expected.get("config_board"):
        sys.stdout.write(f"  board={expected['config_board']}")
    sys.stdout.write("\n")
    for src in (expected.get("sources") or [])[:10]:
        sys.stdout.write(f"  ← {src}\n")
    sys.stdout.write(
        f"期望：modules={len(expected.get('modules') or [])}  "
        f"nodes={len(expected.get('nodes') or [])}  "
        f"processes={len(expected.get('processes') or [])}  "
        f"mounts={len(expected.get('mounts') or [])}\n"
    )


def _write_adapter_expect(project: Path, expected: dict) -> Path:
    release = str(expected.get("release_id") or "")
    if not release:
        raise SystemExit("没有 release_id，无法写入 Adapter")
    dest = adapters_root() / release
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "device_expect.json"
    payload = {
        "release_id": release,
        "product": expected.get("product") or "",
        "config_board": expected.get("config_board") or "",
        "modules": expected.get("modules") or [],
        "nodes": expected.get("nodes") or [],
        "processes": expected.get("processes") or [],
        "mounts": expected.get("mounts") or [],
        "sources": [
            s
            for s in (expected.get("sources") or [])
            if not str(s).endswith("device_expect.json")
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _read_expect_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        "modules": list(data.get("modules") or []),
        "nodes": list(data.get("nodes") or []),
        "processes": list(data.get("processes") or []),
        "mounts": list(data.get("mounts") or []),
        "sources": list(data.get("sources") or []),
        "config_board": str(data.get("config_board") or ""),
    }


def _sanitize_expect(data: dict) -> dict:
    return {
        "modules": [_norm_module(m) for m in (data.get("modules") or []) if _norm_module(m)],
        "nodes": [n for n in (data.get("nodes") or []) if _ok_node(n)],
        "processes": list(data.get("processes") or []),
        "mounts": list(data.get("mounts") or []),
        "sources": list(data.get("sources") or []),
        "config_board": str(data.get("config_board") or ""),
    }


def _merge(base: dict, extra: dict) -> None:
    for key in ("modules", "nodes", "processes", "mounts", "sources"):
        for item in extra.get(key) or []:
            if item and item not in base[key]:
                base[key].append(item)


def _merge_soft(base: dict, extra: dict) -> None:
    """Adapter 只补充脚本未列出的项；不覆盖已有进程列表。"""
    for key in ("modules", "nodes", "mounts", "sources"):
        for item in extra.get(key) or []:
            if item and item not in base[key]:
                base[key].append(item)


def _uniq(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _norm_module(name: str) -> str:
    name = (name or "").strip()
    if not name or name.startswith("$") or "/" in name:
        return ""
    if name.endswith(".ko"):
        name = name[: -len(".ko")]
    # lsmod 显示下划线；脚本里偶发 i2c-algo-bit
    return name.replace("-", "_")


def _ok_node(node: str) -> bool:
    if not node or "$" in node or "`" in node:
        return False
    if node in _SKIP_DEV or node in _COARSE_DEV:
        return False
    if node.endswith("-") or node.endswith(".") or node.endswith("/"):
        return False
    # 拒绝无编号的 mtdblock / mtd 前缀
    stem = node.rstrip("0123456789")
    if stem in ("/dev/mtdblock", "/dev/mtd") and not node[-1:].isdigit():
        return False
    return node.startswith("/dev/")


def _ko_assigns(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for match in _KO_ASSIGN_RE.finditer(text):
        key = match.group(1)
        value = match.group(2) or match.group(3) or match.group(4) or ""
        if value:
            out[key] = value
    return out


def _under(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _sh(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def _section(text: str, begin: str, end: str) -> str:
    if begin not in text or end not in text:
        return ""
    return text.split(begin, 1)[1].split(end, 1)[0]


def _parse_lsmod(section: str) -> list[str]:
    names = []
    for line in section.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("module"):
            continue
        names.append(line.split()[0])
    return names


def _parse_flags(text: str, ok_prefix: str, miss_prefix: str) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(ok_prefix + " "):
            result[line[len(ok_prefix) + 1 :].strip()] = True
        elif line.startswith(miss_prefix + " "):
            result[line[len(miss_prefix) + 1 :].strip()] = False
    return result
