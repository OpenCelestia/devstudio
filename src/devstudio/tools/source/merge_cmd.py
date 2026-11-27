"""合入先写一份新计划。批准前不修改 SDK。"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import date
from pathlib import Path

from devstudio.runtime.guard import approval_block, filename_block, stays_inside
from devstudio.tools.source.plan_cmd import is_approved, load_task, next_task_id
from devstudio.tools.source.sensor_flow import _sdk_root
from devstudio.workspace import tasks_dir
from devstudio.yamlio import load_yaml


def run_review(project: Path, argv: list[str]) -> int:
    if len(argv) < 1:
        sys.stderr.write("用法：devstudio plan review <task_id> --item 待复核原文\n")
        return 2
    task_id = argv[0]
    item = ""
    args = argv[1:]
    while args:
        token = args.pop(0)
        if token == "--item" and args:
            item = args.pop(0)
    loaded = load_task(project, task_id)
    if loaded is None:
        sys.stderr.write(f"没有任务 {task_id}。\n")
        return 1
    gaps = _gaps(tasks_dir(project) / task_id)
    if item not in gaps:
        sys.stdout.write("这条不在待复核列表里。未记录。\n")
        return 1
    path = tasks_dir(project) / task_id / "plan_src" / "reviewed.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    if item not in existing:
        existing.append(item)
        path.write_text("\n".join(existing) + "\n", encoding="utf-8")
    sys.stdout.write(f"已记录复核：{item}\n")
    return 0


def run_merge(project: Path, argv: list[str]) -> int:
    if len(argv) != 1:
        sys.stderr.write("用法：devstudio plan merge <task_id>\n")
        return 2
    task_id = argv[0]
    if not is_approved(project, task_id):
        sys.stdout.write("计划未批准。未写入 SDK。\n")
        return 1
    loaded = load_task(project, task_id)
    if loaded is None:
        sys.stderr.write(f"没有任务 {task_id}。\n")
        return 1
    _path, task = loaded
    pending = _pending(tasks_dir(project) / task_id)
    if pending:
        sys.stdout.write("待复核项还没有逐条处理。未写入 SDK。\n")
        for item in pending:
            sys.stdout.write(f"  - {item}\n")
        return 1
    state = str(task.get("state") or _state(project))
    if state == "sdk_only":
        sys.stdout.write("sdk_only 只把结果留在任务目录，不写 SDK。\n")
        return 0
    source = tasks_dir(project) / task_id / "plan_src"
    names = _listed_files(source, _manifest(source).get("ref_model", ""))
    if not names:
        sys.stdout.write("方案里没有可合入的源码文件。未写入 SDK。\n")
        return 1
    model = str(_manifest(source).get("model") or task.get("title") or "sensor")
    new_id = next_task_id(project)
    dest = tasks_dir(project) / new_id
    dest.mkdir()
    created = date.today().isoformat()
    lines = "\n".join(f"- {name}" for name in names)
    plan = (
        f"# 计划 {new_id}：合入 {model}\n"
        f"状态：待批准\n"
        f"来源任务：{task_id}\n"
        f"创建：{created}\n\n"
        f"## 将增加的文件\n{lines}\n\n"
        "不修改参考型号原文件，不覆盖 ISP conf。\n"
    )
    (dest / "plan.md").write_text(plan, encoding="utf-8")
    record = {
        "task_id": new_id,
        "title": f"合入 {model}",
        "kind": "merge",
        "source_task": task_id,
        "model": model,
        "files": names,
        "state": state,
        "level": "L0",
    }
    (dest / "task.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(f"已写合入计划 {dest / 'plan.md'}。未写入 SDK。\n")
    return 0


def run_merge_apply(project: Path, argv: list[str]) -> int:
    if len(argv) != 1:
        sys.stderr.write("用法：devstudio plan merge-apply <task_id>\n")
        return 2
    task_id = argv[0]
    loaded = load_task(project, task_id)
    if loaded is None:
        sys.stderr.write(f"没有任务 {task_id}。\n")
        return 1
    path, task = loaded
    if task.get("kind") != "merge":
        sys.stdout.write("这不是合入计划。未写入 SDK。\n")
        return 1
    if approval_block(project, task_id):
        sys.stdout.write("合入计划未批准。未写入 SDK。\n")
        return 1
    if str(task.get("state") or _state(project)) == "sdk_only":
        sys.stdout.write("sdk_only 不写 SDK。\n")
        return 1
    sdk = _sdk_root(project)
    if sdk is None:
        sys.stdout.write("没有 SDK 目录。未写入。\n")
        return 1
    model = str(task.get("model") or "")
    source_id = str(task.get("source_task") or "")
    source = tasks_dir(project) / source_id / "plan_src"
    ref = str(_manifest(source).get("ref_model") or "")
    ref_dir = sdk / "driver_src" / "sensor" / ref.lower() if ref else None
    before = _digest(ref_dir)
    target = sdk / "driver_src" / "sensor" / model.lower()
    if ref and target.resolve() == (sdk / "driver_src" / "sensor" / ref.lower()).resolve():
        sys.stdout.write("目标目录与参考型号相同。未写入 SDK。\n")
        return 1
    target.mkdir(parents=True, exist_ok=True)
    written = []
    names = [name for name in (task.get("files") or []) if isinstance(name, str)]
    if len(names) > 32:
        sys.stdout.write("一次合入超过 32 个文件。未写入 SDK。\n")
        return 1
    for name in names:
        if filename_block(name):
            continue
        src = source / name
        if not stays_inside(source, src):
            sys.stdout.write(f"{name} 不在方案目录内。未写入。\n")
            continue
        if name.lower().endswith(".conf") or (ref and ref.lower() in name.lower()):
            sys.stdout.write(f"跳过 {name}。不覆盖参考文件或 ISP conf。\n")
            continue
        if not src.is_file():
            sys.stdout.write(f"方案里没有 {name}。未写入该文件。\n")
            continue
        shutil.copyfile(src, target / name)
        written.append(name)
    after = _digest(ref_dir)
    if before != after:
        sys.stdout.write("参考型号文件发生变化。请检查。\n")
        return 1
    task["level"] = "L1"
    task["merged"] = written
    path.write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(f"已写入 {target}\n")
    sys.stdout.write("参考型号文件未改。完成等级：L1。寄存器差异仍以待复核记录为准，未另写序列。\n")
    return 0


def _gaps(task_dir: Path) -> list[str]:
    data = _manifest(task_dir / "plan_src")
    gaps = data.get("gaps") or []
    return [str(item) for item in gaps]


def _pending(task_dir: Path) -> list[str]:
    reviewed = task_dir / "plan_src" / "reviewed.txt"
    done = set(reviewed.read_text(encoding="utf-8").splitlines()) if reviewed.is_file() else set()
    return [item for item in _gaps(task_dir) if item not in done]


def _manifest(source: Path) -> dict:
    path = source / "merge_manifest.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _listed_files(source: Path, ref: str) -> list[str]:
    if not source.is_dir():
        return []
    names = []
    for path in sorted(source.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".c", ".h", ".txt"}:
            continue
        if path.name in {"reviewed.txt", "方案.md"}:
            continue
        if path.name.lower().endswith(".conf"):
            continue
        if ref and ref.lower() in path.name.lower():
            continue
        names.append(path.name)
    return names


def _state(project: Path) -> str:
    path = project / "devstudio.yaml"
    if not path.is_file():
        return ""
    return str(load_yaml(path).get("state") or "")


def _digest(folder: Path | None) -> str:
    if folder is None or not folder.is_dir():
        return ""
    digest = hashlib.sha256()
    for path in sorted(item for item in folder.rglob("*") if item.is_file()):
        digest.update(path.relative_to(folder).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()
