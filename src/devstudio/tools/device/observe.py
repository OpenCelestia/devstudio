"""Read device logs and live CPU/memory. A board number is required."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from devstudio.tools.device.capture import _lock, _new_dir, _unlock
from devstudio.tools.device.frame import LOG, METRICS, DeviceLink


def run_log(project: Path, board: str, seconds: float) -> int:
    return _collect(project, board, seconds, metrics=False, log=True)


def run_status(project: Path, board: str, seconds: float) -> int:
    return _collect(project, board, seconds, metrics=True, log=False)


def _collect(project: Path, board: str, seconds: float, metrics: bool, log: bool) -> int:
    if not _lock(project, board):
        sys.stdout.write(f"板 {board} 正被占用。已拒绝。\n")
        return 1
    try:
        with DeviceLink(board, timeout=max(seconds + 5, 10)) as link:
            link.subscribe(metrics=metrics, log=log, frame=False)
            frames = link.collect(seconds)
    except (OSError, TimeoutError, ConnectionError) as exc:
        sys.stdout.write(f"未连接 {board}：{exc}。未保存。\n")
        return 1
    finally:
        _unlock(project)
    dest = _new_dir(project)
    if log:
        lines = [body.decode("utf-8", errors="replace") for kind, body in frames if kind == LOG and body]
        (dest / "log.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        sys.stdout.write(f"已保存 {dest / 'log.jsonl'}\n")
        if not lines:
            sys.stdout.write("没有 LOG 帧。\n")
            return 1
        for line in lines[-20:]:
            sys.stdout.write(line.rstrip() + "\n")
        return 0
    rows = []
    for kind, body in frames:
        if kind == METRICS and body:
            try:
                rows.append(json.loads(body.decode("utf-8")))
            except json.JSONDecodeError:
                continue
    (dest / "metrics.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )
    sys.stdout.write(f"已保存 {dest / 'metrics.jsonl'}\n")
    if not rows:
        sys.stdout.write("没有 METRICS 帧。\n")
        return 1
    last = rows[-1]
    sys.stdout.write(f"cpu：{last.get('cpu', '')}\n")
    sys.stdout.write(f"mem_total_kb：{last.get('mem_total_kb', '')}\n")
    sys.stdout.write(f"mem_free_kb：{last.get('mem_free_kb', '')}\n")
    sys.stdout.write(f"load：{last.get('load', '')}\n")
    sys.stdout.write(f"temp_c：{last.get('temp_c', '')}\n")
    sys.stdout.write(f"来源：{dest / 'metrics.jsonl'}\n")
    return 0
