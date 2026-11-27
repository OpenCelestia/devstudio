import json
import sys
from pathlib import Path

from devstudio.orchestration.detect import split_release
from devstudio.workspace import tasks_dir


def run_report(argv: list[str], project: Path | None = None) -> int:
    if len(argv) != 2 or argv[0] != "export":
        sys.stderr.write("用法：devstudio report export <task_id>\n")
        return 2
    project = (project or Path.cwd()).resolve()
    task_id = argv[1]
    path = tasks_dir(project) / task_id / "task.json"
    if not path.is_file():
        sys.stderr.write(f"没有任务 {task_id}。\n")
        return 1
    task = json.loads(path.read_text(encoding="utf-8"))
    family, version = split_release(task["release_id"])
    level = task.get("level", "L0")
    level_text = "L3 编译通过（未实机验证）" if level == "L3" else level
    build = task.get("build", {})
    files = task.get("files", [])
    numeric = task.get("numeric_fields", {"by_rule": 0, "needs_review": 0})
    unverified = task.get("unverified", [])
    labels = {"i2c_id": "I2C 读 ID", "preview": "出图"}
    command = build.get("command", "")
    host = build.get("host", "")
    exit_code = build.get("exit", "")
    body = f"""# 任务报告 {task_id}
完成等级：{level_text}
芯片 / 版本：{family} {version}　状态：{task.get('state', '')}
修改文件：{len(files)}
数字字段：由规则写入 {numeric.get('by_rule', 0)} 项；待复核 {numeric.get('needs_review', 0)} 项
编译：{command} 位置 {host}，退出码 {exit_code}
未验证项：{'、'.join(labels.get(item, item) for item in unverified) if unverified else '无'}
下一步：指定板号推送并执行 serial-verify
"""
    report_json = {
        "task_id": task_id,
        "release_id": task["release_id"],
        "state": task.get("state"),
        "level": level,
        "files": files,
        "numeric_fields": numeric,
        "build": build,
        "unverified": unverified,
    }
    folder = path.parent
    (folder / "report.md").write_text(body, encoding="utf-8")
    (folder / "report.json").write_text(json.dumps(report_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(f"已写入 {folder / 'report.md'}\n")
    sys.stdout.write(f"已写入 {folder / 'report.json'}\n")
    return 0
