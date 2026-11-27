"""Save one device sample, then compare two samples against checks named in the plan."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from devstudio.tools.device.frame import LOG, METRICS, DeviceLink
from devstudio.workspace import data_root, tasks_dir


def run_capture(project: Path, board: str, seconds: float, configs: list[str], serial: str | None, command: str = "") -> int:
    if not _lock(project, board):
        sys.stdout.write(f"板 {board} 正被占用。已拒绝。\n")
        return 1
    try:
        return _capture(project, board, seconds, configs, serial, command)
    finally:
        _unlock(project)


def run_compare(project: Path, before: Path, after: Path, checks: list[str]) -> int:
    before_dir = _resolve(project, before)
    after_dir = _resolve(project, after)
    if before_dir is None or after_dir is None:
        sys.stdout.write("找不到 before 或 after。不写通过。\n")
        return 1
    left = _meta(before_dir)
    right = _meta(after_dir)
    if not left or not right or left.get("board") != right.get("board"):
        sys.stdout.write("板号不一致或取数记录不完整。不写通过。\n")
        return 1
    if "metrics" not in left.get("channels", []) or "metrics" not in right.get("channels", []):
        sys.stdout.write("通道没打开。不写通过。\n")
        return 1
    named = checks or _checks_in_plan(after_dir)
    if not named:
        sys.stdout.write("计划未写检查项，不写通过。\n")
        return 1
    failed = False
    for item in named:
        text, ok = _one(before_dir, after_dir, item)
        sys.stdout.write(text + "\n")
        failed = failed or not ok
    if failed:
        sys.stdout.write("不写通过。\n")
        return 1
    sys.stdout.write("自动项通过，完成等级：L4。串口现象、界面和 RTSP 仍须人确认后到 L5。\n")
    _mark_l4(after_dir)
    return 0


def latest_metrics(project: Path) -> tuple[Path, dict] | None:
    found: list[Path] = []
    root = tasks_dir(project)
    if not root.is_dir():
        return None
    for path in root.glob("*/capture/*/metrics.jsonl"):
        if path.is_file():
            found.append(path)
    if not found:
        return None
    path = max(found, key=lambda item: item.stat().st_mtime)
    last = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            last = json.loads(line)
    if not isinstance(last, dict):
        return None
    return path, last


def _capture(project: Path, board: str, seconds: float, configs: list[str], serial: str | None, command: str = "") -> int:
    try:
        with DeviceLink(board, timeout=max(seconds + 5, 10)) as link:
            link.subscribe(metrics=True, log=True, frame=False)
            frames = link.collect(seconds)
            files = {path: link.file_get(path) for path in configs}
    except (OSError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
        sys.stdout.write(f"未连接到 {board}：{exc}。未保存取数。\n")
        return 1
    dest = _new_dir(project)
    metrics = [body.decode("utf-8", errors="replace") for kind, body in frames if kind == METRICS and body]
    logs = [body.decode("utf-8", errors="replace") for kind, body in frames if kind == LOG and body]
    (dest / "metrics.jsonl").write_text("\n".join(metrics) + ("\n" if metrics else ""), encoding="utf-8")
    (dest / "log.jsonl").write_text("\n".join(logs) + ("\n" if logs else ""), encoding="utf-8")
    for path, data in files.items():
        relative = path.lstrip("/").replace("\\", "/")
        target = dest / "files" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    channels = ["metrics", "log"]
    if configs:
        channels.append("config")
    if serial:
        sys.stdout.write("串口未采集：本机没有串口读取。未保存 serial.log。\n")
    (dest / "meta.json").write_text(
        json.dumps(
            {"board": board, "seconds": seconds, "channels": channels, "command": command},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    sys.stdout.write(f"已保存 {dest}\n")
    if not metrics:
        sys.stdout.write("没有 METRICS 帧。\n")
        return 1
    return 0


def _one(before: Path, after: Path, item: str) -> tuple[str, bool]:
    if item.startswith("rss:"):
        _kind, name, direction = (item.split(":", 2) + ["", ""])[:3]
        if direction != "down":
            return f"{item}：检查项无法识别，不写通过", False
        left = _rss(before, name)
        right = _rss(after, name)
        if left is None or right is None:
            return f"rss {name}：未找到进程，不写通过", False
        if right < left:
            return f"rss {name}：{left} → {right}，通过", True
        return f"rss {name}：{left} → {right}，未下降", False
    if item.startswith("log-absent:"):
        text = item.split(":", 1)[1]
        blob = (after / "log.jsonl").read_text(encoding="utf-8") if (after / "log.jsonl").is_file() else ""
        if text and text not in blob:
            return f"log-absent {text}：通过", True
        return f"log-absent {text}：仍出现，不写通过", False
    if item.startswith("config:"):
        body = item.split(":", 1)[1]
        path, _, expect = body.partition("=")
        file_part, _, key = path.rpartition(":")
        if not file_part:
            return f"{item}：检查项无法识别，不写通过", False
        target = after / "files" / file_part.lstrip("/")
        if not target.is_file():
            return f"config {file_part}：配置未取到，不写通过", False
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return f"config {file_part}：不是 JSON，不写通过", False
        got = data
        for part in key.split("."):
            if not isinstance(got, dict) or part not in got:
                return f"config {key}：未收录，不写通过", False
            got = got[part]
        if str(got) == expect:
            return f"config {key}：{got}，通过", True
        return f"config {key}：实际 {got}，期望 {expect}，不写通过", False
    return f"{item}：检查项无法识别，不写通过", False


def _rss(folder: Path, name: str) -> int | None:
    path = folder / "metrics.jsonl"
    if not path.is_file():
        return None
    last = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            last = json.loads(line)
    if not isinstance(last, dict):
        return None
    for proc in last.get("procs") or []:
        if proc.get("name") == name and "rss_kb" in proc:
            return int(proc["rss_kb"])
    return None


def _checks_in_plan(after: Path) -> list[str]:
    task = after.parent.parent if after.parent.name == "capture" else None
    if task is None:
        return []
    plan = task / "plan.md"
    if not plan.is_file():
        return []
    lines = plan.read_text(encoding="utf-8").splitlines()
    picked: list[str] = []
    seen = False
    for line in lines:
        if line.strip() == "检查：":
            seen = True
            continue
        if seen and line.startswith("- "):
            picked.append(line[2:].strip())
        elif seen and line.strip():
            break
    return picked


def _new_dir(project: Path) -> Path:
    import datetime

    tasks = tasks_dir(project)
    names = sorted(path.name for path in tasks.iterdir() if path.is_dir()) if tasks.is_dir() else []
    task = tasks / names[-1] if names else tasks / "t0000"
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = task / "capture" / stamp
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _resolve(project: Path, path: Path) -> Path | None:
    if path.is_dir():
        return path
    root = tasks_dir(project)
    if not root.is_dir():
        return None
    matches = [item for item in root.glob(f"*/capture/{path.name}") if item.is_dir()]
    if len(matches) == 1:
        return matches[0]
    return None


def _meta(folder: Path) -> dict:
    path = folder / "meta.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _mark_l4(after: Path) -> None:
    if after.parent.name != "capture":
        return
    task = after.parent.parent
    path = task / "task.json"
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict):
        return
    data["level"] = "L4"
    data["unverified"] = ["serial", "frame", "rtsp"]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _lock(project: Path, board: str) -> bool:
    path = data_root(project) / "device.lock"
    if path.is_file():
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            info = {}
        pid = int(info.get("pid") or 0)
        if pid and pid != os.getpid() and _alive(pid):
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"board": board, "pid": os.getpid()}), encoding="utf-8")
    return True


def _unlock(project: Path) -> None:
    path = data_root(project) / "device.lock"
    if not path.is_file():
        return
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        info = {}
    if int(info.get("pid") or 0) == os.getpid():
        path.unlink()


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True
