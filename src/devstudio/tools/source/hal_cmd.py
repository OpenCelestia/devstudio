"""Plan a customer HAL from an interface header. Unindexed SDK calls are not written."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from devstudio.runtime.guard import approval_block, scan_numeric, stays_inside
from devstudio.tools.knowledge.search import find_in_index
from devstudio.tools.source.plan_cmd import load_task, next_task_id
from devstudio.workspace import tasks_dir
from devstudio.yamlio import load_yaml

_PROTO = re.compile(
    r"^[ \t]*(?:extern[ \t]+)?(?:[\w]+\s+)+([A-Za-z_]\w*)\s*\([^;{}]*\)\s*;",
    re.M,
)
_TOKEN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
_SKIP = {
    "int", "void", "char", "const", "static", "extern", "return", "if", "else",
    "for", "while", "unsigned", "struct", "sizeof", "include", "define",
}


def run_hal(argv: list[str], project: Path | None = None) -> int:
    project = (project or Path.cwd()).resolve()
    if not argv or argv[0] not in ("plan", "apply"):
        sys.stderr.write("用法：devstudio hal plan --module 模块 --header 头文件 [--ref 参考.c]\n      devstudio hal apply <task_id>\n")
        return 2
    config = _config(project)
    if config is None:
        return 1
    if config.get("state") != "sdk_customer_app":
        sys.stdout.write("HAL 只在 sdk_customer_app 生成。未写文件。\n")
        return 1
    if argv[0] == "apply":
        return _apply(project, argv[1:])
    return _plan(project, config, argv[1:])


def _plan(project: Path, config: dict, argv: list[str]) -> int:
    module = ""
    header = ""
    ref = ""
    args = list(argv)
    while args:
        item = args.pop(0)
        if item == "--module" and args:
            module = args.pop(0)
        elif item == "--header" and args:
            header = args.pop(0)
        elif item == "--ref" and args:
            ref = args.pop(0)
        else:
            sys.stderr.write("用法：devstudio hal plan --module 模块 --header 头文件 [--ref 参考.c]\n")
            return 2
    header_path = project / header if header else Path()
    if not module or not header or not header_path.is_file() or not stays_inside(project, header_path):
        sys.stdout.write("没有可读的接口头文件。未写文件。\n")
        return 1
    out = str((config.get("customer") or {}).get("hal_out", "")).strip()
    out_path = (project / out) if out else Path()
    if not out or not stays_inside(project, out_path):
        sys.stdout.write("devstudio.yaml 没有位于工程内的 customer.hal_out。未写文件。\n")
        return 1
    text = header_path.read_text(encoding="utf-8", errors="replace")
    names = []
    signatures = []
    for match in _PROTO.finditer(text):
        name = match.group(1)
        if name in _SKIP or name in names:
            continue
        names.append(name)
        signatures.append(" ".join(match.group(0).split()))
    if not names:
        sys.stdout.write("接口头文件里没有函数声明。未写文件。\n")
        return 1
    indexed, missing, numeric = _from_ref(project, ref, set(names))
    task_id = next_task_id(project)
    task_dir = tasks_dir(project) / task_id
    task_dir.mkdir(parents=True)
    lines = [
        f"# 计划 {task_id}：HAL {module}",
        "状态：待批准",
        "",
        f"输出目录：{out}",
        f"接口：{header}",
        "",
        "## 接口函数",
        *[f"- {item}" for item in signatures],
        "",
        "## 索引内符号",
    ]
    if indexed:
        lines.extend(f"- {name} {place}" for name, place in indexed)
    else:
        lines.append("- （无）")
    lines.append("")
    lines.append("## 未写入的调用")
    if missing:
        lines.extend(f"- {name} 符号未收录，不写该调用" for name in missing)
    else:
        lines.append("- （无）")
    if numeric:
        lines.append("")
        lines.append("## 数字字段")
        lines.extend(f"- 数字字段未写入：{item}" for item in numeric)
    lines.append("")
    lines.append("批准前不写 HAL 文件。")
    (task_dir / "plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    task = {
        "task_id": task_id,
        "title": f"HAL {module}",
        "kind": "hal",
        "release_id": config.get("release_id", ""),
        "state": "sdk_customer_app",
        "level": "L0",
        "module": module,
        "header": header,
        "out": out,
        "signatures": signatures,
        "indexed": indexed,
        "missing": missing,
    }
    (task_dir / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(f"已创建计划 {task_id}。未写 HAL 文件。\n")
    for name in missing:
        sys.stdout.write(f"{name} 符号未收录，不写该调用。\n")
    sys.stdout.write(f"devstudio plan approve {task_id} 后执行 devstudio hal apply {task_id}。\n")
    return 0


def _apply(project: Path, argv: list[str]) -> int:
    if len(argv) != 1:
        sys.stderr.write("用法：devstudio hal apply <task_id>\n")
        return 2
    task_id = argv[0]
    blocked = approval_block(project, task_id)
    if blocked:
        sys.stdout.write(blocked)
        return 1
    loaded = load_task(project, task_id)
    if loaded is None:
        sys.stderr.write(f"没有任务 {task_id}。\n")
        return 1
    _path, task = loaded
    if task.get("kind") != "hal":
        sys.stdout.write("不是 HAL 计划。未写文件。\n")
        return 1
    out = project / str(task.get("out", ""))
    if not stays_inside(project, out):
        sys.stdout.write("输出目录不在工程内。未写文件。\n")
        return 1
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{task.get('module', 'hal')}_hal.c"
    if not stays_inside(out, target):
        sys.stdout.write("输出文件不在 HAL 目录内。未写文件。\n")
        return 1
    body = _render(task)
    target.write_text(body, encoding="utf-8")
    sys.stdout.write(f"已写入 {target}\n")
    sys.stdout.write("未收录的符号没有写入调用。完成等级：L1。\n")
    return 0


def _from_ref(project: Path, ref: str, own: set[str]) -> tuple[list[list[str]], list[str], list[str]]:
    if not ref:
        return [], [], []
    path = project / ref
    if not path.is_file() or not stays_inside(project, path):
        return [], [], []
    indexed = []
    missing = []
    numeric = []
    seen = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if scan_numeric(line):
            numeric.append(line.strip()[:200])
            continue
        for token in _TOKEN.findall(line):
            if len(token) < 8 or token in _SKIP or token in own or token in seen:
                continue
            seen.add(token)
            place = find_in_index(project, token)
            if place:
                indexed.append([token, place])
            else:
                missing.append(token)
            if len(seen) >= 20:
                return indexed, missing, numeric
    return indexed, missing, numeric


def _render(task: dict) -> str:
    lines = [
        f"/* hal-adapt {task.get('module', '')}",
        " * 索引内符号只记在注释里。未收录的符号没有调用。",
        " */",
    ]
    for name, place in task.get("indexed") or []:
        lines.append(f"/* 索引：{name} {place} */")
    if task.get("missing"):
        lines.append("/* 符号未收录，不写该调用 */")
    for signature in task.get("signatures") or []:
        decl = signature[:-1].strip() if signature.endswith(";") else signature
        lines.extend(["", f"{decl}", "{", "}"])
    lines.append("")
    return "\n".join(lines)


def _config(project: Path) -> dict | None:
    path = project / "devstudio.yaml"
    if not path.is_file():
        sys.stderr.write("当前目录没有 devstudio.yaml。\n")
        return None
    loaded = load_yaml(path)
    return loaded if isinstance(loaded, dict) else None
