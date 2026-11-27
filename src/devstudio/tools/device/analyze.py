"""Analyze saved device LOG / METRICS (or freshly collected ones).

Does not invent conclusions: keyword classification + numeric summary only.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from devstudio.tools.device.capture import _new_dir, latest_metrics
from devstudio.tools.device.connect import resolve_board
from devstudio.tools.device.observe import run_log, run_status
from devstudio.workspace import tasks_dir

# Aligned with celestia-debug Bug 分类（关键词命中，不替代人工确认）
_BUG_RULES: list[tuple[str, str, tuple[str, ...]]] = (
    (
        "A 崩溃",
        "celestia-debug → 按栈归层",
        ("signal=11", "segfault", "fault_addr=", "backtrace", "panic"),
    ),
    (
        "B 预览/采集/编码",
        "celestia-hardware / celestia-framework",
        ("invalid inbuf", "qbuf fail", "select timeout", "num_slices", "不出帧"),
    ),
    (
        "C 内存/DMA/OOM",
        "celestia-debug 内存节",
        ("oom", "dma alloc", "ion alloc", "alloc fail", "out of memory"),
    ),
    (
        "D 启动/驱动/Board",
        "celestia-project / celestia-hardware",
        ("insmod", "unknown-block", "page more than end page", "no such device", "modprobe"),
    ),
    (
        "E App/GUI/SO",
        "celestia-application",
        ("dlopen", "undefined symbol", "cannot open shared object"),
    ),
    (
        "F 构建/stale/配置",
        "celestia-scripts",
        ("failed to get", "module: -1", "stale"),
    ),
    (
        "G 出流/网络/云",
        "celestia-framework",
        ("rtsp", "stream_reg_client", "nebula", "listen failed", "bind failed", "554"),
    ),
)


def run_analyze(argv: list[str], project: Path) -> int:
    if not argv or argv[0] not in ("log", "status", "metrics"):
        sys.stderr.write(
            "用法：devstudio device analyze log [--path 文件|目录] [--board 板号] [--seconds N]\n"
            "      devstudio device analyze status|metrics [--path 文件|目录] [--board 板号] [--seconds N]\n"
            "未给 --path 时：有板号则先采集再分析；否则找最近一次 capture。\n"
        )
        return 2
    kind = argv[0]
    path = ""
    board = ""
    seconds = 5.0
    args = list(argv[1:])
    while args:
        item = args.pop(0)
        if item == "--path" and args:
            path = args.pop(0)
        elif item == "--board" and args:
            board = args.pop(0)
        elif item == "--seconds" and args:
            seconds = float(args.pop(0))
        else:
            sys.stderr.write("参数无法识别。\n")
            return 2

    if kind == "log":
        return _analyze_log(project, path, board, seconds)
    return _analyze_metrics(project, path, board, seconds, label=kind)


def _analyze_log(project: Path, path: str, board: str, seconds: float) -> int:
    text = ""
    source = path
    if path:
        text = _load_log_text(Path(path))
    else:
        latest = _latest_file(project, "log.jsonl")
        chosen = ""
        if board:
            chosen = resolve_board(project, board)
        elif latest is None:
            chosen = resolve_board(project, None)
        if chosen:
            code = run_log(project, chosen, seconds)
            if code != 0:
                return code
            latest = _latest_file(project, "log.jsonl")
        if latest:
            text = latest.read_text(encoding="utf-8", errors="replace")
            source = str(latest)
        else:
            sys.stdout.write(
                "没有可分析的 LOG。请指定 --path，或带 --board 采集：\n"
                "  devstudio device analyze log --board <IP>\n"
            )
            return 1
    if not text.strip():
        sys.stdout.write("LOG 为空。不写结论。\n")
        return 1

    hits: list[tuple[str, str, str]] = []
    low = text.lower()
    for category, skill, keys in _BUG_RULES:
        for key in keys:
            if key.lower() in low:
                # first matching line snippet
                line = next(
                    (ln.strip() for ln in text.splitlines() if key.lower() in ln.lower()),
                    key,
                )
                hits.append((category, skill, line[:160]))
                break

    sys.stdout.write(f"来源：{source or '（内存）'}\n")
    sys.stdout.write(f"行数：{len([ln for ln in text.splitlines() if ln.strip()])}\n")
    if not hits:
        sys.stdout.write("未命中已知 Bug 关键词。不自动判定 PASS/FAIL；请对照 celestia-debug 人工看。\n")
        return 0
    sys.stdout.write("命中分类（需结合现象人工确认，不代替闭环判据）：\n")
    for category, skill, snippet in hits:
        sys.stdout.write(f"- [{category}] → {skill}\n")
        sys.stdout.write(f"  证据：{snippet}\n")
    sys.stdout.write("建议：按 celestia-debug 解法表处理，复测后再 capture/compare。\n")
    return 0


def _analyze_metrics(
    project: Path, path: str, board: str, seconds: float, *, label: str
) -> int:
    rows: list[dict] = []
    source = path
    if path:
        rows = _load_metrics_rows(Path(path))
    else:
        chosen = resolve_board(project, board or None) if board else ""
        if not chosen and not board:
            # try ask only if we have nothing saved
            found = latest_metrics(project)
            if found:
                source = str(found[0])
                rows = _load_metrics_rows(found[0])
            else:
                chosen = resolve_board(project, None)
        if chosen:
            code = run_status(project, chosen, seconds)
            if code != 0:
                return code
            latest = _latest_file(project, "metrics.jsonl")
            if latest:
                rows = _load_metrics_rows(latest)
                source = str(latest)
        elif not rows:
            found = latest_metrics(project)
            if found:
                source = str(found[0])
                rows = _load_metrics_rows(found[0])

    if not rows:
        sys.stdout.write(
            f"没有可分析的 METRICS。请指定 --path，或：devstudio device analyze {label} --board <IP>\n"
        )
        return 1

    cpus = [_as_float(r.get("cpu")) for r in rows]
    mems = [_as_float(r.get("mem_free_kb")) for r in rows]
    loads = [_load1(r.get("load")) for r in rows]
    temps = [_as_float(r.get("temp_c") if "temp_c" in r else r.get("temp")) for r in rows]
    cpus = [x for x in cpus if x is not None]
    mems = [x for x in mems if x is not None]
    loads = [x for x in loads if x is not None]
    temps = [x for x in temps if x is not None]

    sys.stdout.write(f"来源：{source}\n")
    sys.stdout.write(f"帧数：{len(rows)}\n")
    if cpus:
        sys.stdout.write(f"cpu：均值 { _avg(cpus):.1f}  峰值 {max(cpus):.1f}  末帧 {cpus[-1]:.1f}\n")
    if mems:
        sys.stdout.write(
            f"mem_free_kb：均值 { _avg(mems):.0f}  最低 {min(mems):.0f}  末帧 {mems[-1]:.0f}\n"
        )
    if loads:
        sys.stdout.write(f"load1：均值 { _avg(loads):.2f}  峰值 {max(loads):.2f}\n")
    if temps:
        sys.stdout.write(f"temp_c：均值 { _avg(temps):.1f}  峰值 {max(temps):.1f}\n")

    # process hotspots from last frame if present
    last = rows[-1]
    procs = last.get("procs") or last.get("processes") or last.get("top")
    if isinstance(procs, list):
        ranked = sorted(
            (p for p in procs if isinstance(p, dict)),
            key=lambda p: _as_float(p.get("pcpu")) or 0.0,
            reverse=True,
        )[:5]
        if ranked:
            sys.stdout.write("末帧进程 CPU 前几：\n")
            for p in ranked:
                sys.stdout.write(
                    f"  {p.get('name') or p.get('comm') or '?'}  "
                    f"pcpu={p.get('pcpu', '')}  rss_kb={p.get('rss_kb', p.get('rss', ''))}\n"
                )
    elif isinstance(procs, str) and procs.strip():
        sys.stdout.write(f"top：{procs.strip()[:200]}\n")

    notes = []
    if cpus and max(cpus) >= 90:
        notes.append("CPU 峰值≥90%，关注编码/AI 线程与热节流")
    if mems and min(mems) < 8 * 1024:
        notes.append("mem_free_kb 曾低于 8MB，关注 OOM / DMA")
    if temps and max(temps) >= 85:
        notes.append("温度峰值≥85°C，关注散热与降频")
    if notes:
        sys.stdout.write("提示（非判定）：\n")
        for note in notes:
            sys.stdout.write(f"- {note}\n")
    else:
        sys.stdout.write("未触发内置阈值提示。改前改后对比请用 device compare。\n")
    sys.stdout.write("自动项只到 L4；界面/RTSP 画面仍须人确认到 L5。\n")
    return 0


def _load_log_text(path: Path) -> str:
    if path.is_dir():
        candidate = path / "log.jsonl"
        path = candidate if candidate.is_file() else path
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _load_metrics_rows(path: Path) -> list[dict]:
    if path.is_dir():
        candidate = path / "metrics.jsonl"
        path = candidate if candidate.is_file() else path
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _latest_file(project: Path, name: str) -> Path | None:
    root = tasks_dir(project)
    if not root.is_dir():
        return None
    found = [p for p in root.glob(f"*/capture/*/{name}") if p.is_file()]
    if not found:
        return None
    return max(found, key=lambda item: item.stat().st_mtime)


def _as_float(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value.replace("%", ""))
        if match:
            return float(match.group(0))
    return None


def _load1(value) -> float | None:
    if isinstance(value, (list, tuple)) and value:
        return _as_float(value[0])
    if isinstance(value, str) and value.strip():
        part = value.replace(",", " ").split()[0]
        return _as_float(part)
    return _as_float(value)


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
