import hashlib
import json
import sys
from pathlib import Path

from devstudio.runtime.guard import approval_block, filename_block, numeric_note, stays_inside, text_block
from devstudio.tools.source.plan_cmd import load_task
from devstudio.workspace import tasks_dir


def run_apply(argv: list[str], project: Path | None = None) -> int:
    project = (project or Path.cwd()).resolve()
    if len(argv) < 1:
        sys.stderr.write("用法：devstudio apply <task_id> --name 文件名 --text 内容\n")
        return 2
    task_id = argv[0]
    name, text = "", ""
    args = argv[1:]
    while args:
        item = args.pop(0)
        if item == "--name" and args:
            name = args.pop(0)
        elif item == "--text" and args:
            text = args.pop(0)
        else:
            sys.stderr.write("用法：devstudio apply <task_id> --name 文件名 --text 内容\n")
            return 2
    if load_task(project, task_id) is None:
        sys.stderr.write(f"没有任务 {task_id}。\n")
        return 1
    blocked = approval_block(project, task_id)
    if blocked:
        sys.stdout.write(blocked)
        return 1
    blocked = filename_block(name) or text_block(text)
    if blocked:
        sys.stderr.write(blocked)
        return 1
    folder = tasks_dir(project) / task_id / "artifacts"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    if not stays_inside(tasks_dir(project) / task_id, target):
        sys.stderr.write("只允许写入任务产物目录下的文件名。\n")
        return 1
    note = numeric_note(text, name)
    if note:
        sys.stdout.write(note)
    target.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    path, task = load_task(project, task_id)
    files = task.setdefault("files", [])
    files.append({"path": f"artifacts/{name}", "sha256": digest})
    task["level"] = "L1"
    path.write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(f"已写入 {target}\n")
    sys.stdout.write(f"sha256 {digest}\n")
    sys.stdout.write("完成等级：L1\n")
    return 0


def run_verify(argv: list[str], project: Path | None = None) -> int:
    project = (project or Path.cwd()).resolve()
    if argv:
        sys.stderr.write("用法：devstudio verify\n")
        return 2
    recorded: set[str] = set()
    base = tasks_dir(project)
    if base.is_dir():
        for task_json in base.glob("*/task.json"):
            task = json.loads(task_json.read_text(encoding="utf-8"))
            for item in task.get("files", []):
                recorded.add(item.get("path", ""))
    sys.stdout.write("apply 记录：\n")
    if not recorded:
        sys.stdout.write("（无）\n")
    else:
        for item in sorted(recorded):
            sys.stdout.write(f"  {item}\n")
    sys.stdout.write("工程内未经 apply 记录的业务源码不计入 L1。\n")
    return 0
