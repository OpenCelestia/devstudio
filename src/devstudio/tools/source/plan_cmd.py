import json
import sys
from datetime import date
from pathlib import Path

from devstudio.tools.knowledge.peripherals import models_in_catalog
from devstudio.workspace import tasks_dir
from devstudio.yamlio import load_yaml

REQUIRED = (
    ("resolution", "分辨率"),
    ("fps", "帧率"),
    ("interface", "接口"),
    ("lanes", "MIPI Lane 数"),
    ("mclk", "MCLK 频率"),
    ("i2c", "I2C 地址"),
    ("sequence", "寄存器初始化序列（ini / 文本 / 粘贴）"),
)


def next_task_id(project: Path) -> str:
    base = tasks_dir(project)
    highest = 0
    if base.is_dir():
        for child in base.iterdir():
            if child.name.startswith("t") and child.name[1:].isdigit():
                highest = max(highest, int(child.name[1:]))
    return f"t{highest + 1:04d}"


def load_task(project: Path, task_id: str) -> tuple[Path, dict] | None:
    path = tasks_dir(project) / task_id / "task.json"
    if not path.is_file():
        return None
    return path, json.loads(path.read_text(encoding="utf-8"))


def is_approved(project: Path, task_id: str) -> bool:
    plan = tasks_dir(project) / task_id / "plan.md"
    if not plan.is_file():
        return False
    text = plan.read_text(encoding="utf-8")
    return "状态：已批准" in text and "状态：待批准" not in text


def run_plan(argv: list[str], project: Path | None = None) -> int:
    project = (project or Path.cwd()).resolve()
    if argv and argv[0] == "show":
        return _show(project, argv[1:])
    if argv and argv[0] == "approve":
        return _approve(project, argv[1:])
    if argv and argv[0] == "review":
        from devstudio.tools.source.merge_cmd import run_review

        return run_review(project, argv[1:])
    if argv and argv[0] == "merge":
        from devstudio.tools.source.merge_cmd import run_merge

        return run_merge(project, argv[1:])
    if argv and argv[0] == "merge-apply":
        from devstudio.tools.source.merge_cmd import run_merge_apply

        return run_merge_apply(project, argv[1:])
    if not argv or argv[0] != "sensor":
        sys.stderr.write(
            "用法：devstudio plan sensor --model 型号 "
            "[--resolution 宽x高] [--fps 帧率] [--interface 接口] [--cis 模式] "
            "[--features 功能] [--lanes N] [--mclk 频率] [--i2c 地址] "
            "[--sequence 文件] [--sequence-text 粘贴内容] [--datasheet 路径]\n"
            "缺参时走 IDE Ask / MCP elicitation（对齐 FAE AI need_info），禁止聊天干等。\n"
            "      devstudio plan show|approve <task_id>\n"
            "      devstudio plan review <task_id> --item 待复核原文\n"
            "      devstudio plan merge <task_id>\n"
            "      devstudio plan merge-apply <task_id>\n"
        )
        return 2
    values = _parse(argv[1:])
    model = values.get("model")
    if not model:
        from devstudio.ui_prompt import emit_ask_ui, prefer_ide_ask

        if prefer_ide_ask():
            emit_ask_ui(
                "INPUT",
                ask_ui="text",
                title="CIS 型号",
                prompt="请输入要适配的 Sensor 型号（列表外将走方案流）",
                retry="devstudio plan sensor --model <型号>",
            )
        else:
            sys.stderr.write("缺少 --model。\n")
        return 2
    config_path = project / "devstudio.yaml"
    if not config_path.is_file():
        sys.stderr.write("当前目录没有 devstudio.yaml。先执行 devstudio init。\n")
        return 1
    config = load_yaml(config_path)
    known = model.lower() in models_in_catalog(project)
    if known:
        sys.stdout.write(
            f"{model} 在 {config['release_id']} 的支持列表里。"
            "列表内用 devstudio sensor confirm/build 或 driver locate，不要求 datasheet/序列。\n"
        )
        return 0

    values = _normalize_sequence(project, values)
    missing_keys = [key for key, _label in REQUIRED if not values.get(key)]
    sys.stdout.write(
        f"{model} 不在 {config['release_id']} 的支持列表里。"
        "列表外方案未实机验证，将生成方案包（对齐 FAE AI）。\n"
    )
    if missing_keys:
        filled = _ask_cis_fields(model, values, missing_keys)
        if filled is None:
            return 1
        values.update(filled)
        values = _normalize_sequence(project, values)
        missing_keys = [key for key, _label in REQUIRED if not values.get(key)]
        if missing_keys:
            labels = [label for key, label in REQUIRED if key in missing_keys]
            _emit_cis_ask(model, labels, values)
            return 1

    task_id = next_task_id(project)
    task_dir = tasks_dir(project) / task_id
    task_dir.mkdir(parents=True)
    values = _materialize_uploads(task_dir, model, values)
    created = date.today().isoformat()
    plan = f"""# 计划 {task_id}：{model} 适配
状态：待批准
release_id：{config['release_id']}　状态：{config['state']}　创建：{created}

## 目标
为 {model} 生成驱动方案，不改参考型号的 ISP 二进制。

## 已确认参数（来源：Ask / FAE need_info 对齐）
- 分辨率：{values.get('resolution', '')}
- 帧率：{values.get('fps', '')}
- 接口：{values.get('interface', '')}
- CIS 模式：{values.get('cis') or '1（单目）'}
- 功能：{values.get('features', '')}
- Lane：{values['lanes']}
- MCLK：{values['mclk']}
- I2C：{values['i2c']}
- 序列：{values['sequence']}
- Datasheet：{values.get('datasheet') or '未提供'}

## 待确认项
- Reset GPIO

## 硬件约束检查结果
MIPI 控制器 满足
Lane 满足
I2C 地址 满足
Reset GPIO 待确认（板卡配置里未找到）
勘误 未收录

## 将修改的文件
## 由规则写入的数字字段
## 将调用的 SDK 符号（头文件:行）
## 编译命令与位置
{config['build']['command']}　位置：{config['build']['host']}

## 风险
列表外型号，方案未实机验证。

## 验证方法
编译通过后，指定板号再读 I2C ID 和出图。
"""
    (task_dir / "plan.md").write_text(plan, encoding="utf-8")
    task = {
        "task_id": task_id,
        "title": f"{model} 适配",
        "release_id": config["release_id"],
        "state": config["state"],
        "level": "L0",
        "plan": "plan.md",
        "kind": "cis_plan",
        "model": model,
        "intake": {k: values.get(k, "") for k, _ in REQUIRED},
    }
    (task_dir / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from devstudio.tools.source.sensor_flow import materialize_plan

    materialize_plan(project, task_dir, config, model, values)
    from devstudio.ui_prompt import emit_ask_ui, prefer_ide_ask

    sys.stdout.write(f"已写计划 {task_dir / 'plan.md'}。\n")
    if prefer_ide_ask():
        emit_ask_ui(
            "CONFIRM",
            ask_ui="yes_no",
            title="批准 CIS 方案计划",
            prompt=f"计划 {task_id}：{model} 适配\n是否批准？批准后可 plan merge。",
            options=(("yes", "是 / 批准"), ("no", "否 / 拒绝")),
            default="no",
            retry=f"devstudio plan approve {task_id} --confirm",
        )
    else:
        sys.stdout.write(f"请审阅后：devstudio plan approve {task_id}\n")
    return 0


def _ask_cis_fields(model: str, values: dict[str, str], missing_keys: list[str]) -> dict[str, str] | None:
    """TTY/desktop 当场问；IDE 只发 ASK_UI，返回 None 让 Agent 用 Ask 重试。"""
    from devstudio.ui_prompt import ask_string, prefer_ide_ask

    if prefer_ide_ask():
        labels = [label for key, label in REQUIRED if key in missing_keys]
        _emit_cis_ask(model, labels, values)
        return None

    filled: dict[str, str] = {}
    for key, label in REQUIRED:
        if key not in missing_keys:
            continue
        if key == "sequence":
            typed = ask_string(
                f"CIS · {model}",
                f"{label}：粘贴序列全文，或填本地文件路径",
                initial=values.get("sequence_text") or values.get("sequence") or "",
            )
        else:
            typed = ask_string(f"CIS · {model}", label, initial=values.get(key, ""))
        if typed is None:
            return None
        if typed.strip():
            if key == "sequence":
                filled["sequence_text"] = typed.strip()
            else:
                filled[key] = typed.strip()
    # optional
    for key, label in (("cis", "CIS 模式"), ("features", "支持功能"), ("datasheet", "Datasheet 路径（可空）")):
        if values.get(key):
            continue
        typed = ask_string(f"CIS · {model}", label, initial="")
        if typed and typed.strip():
            filled[key] = typed.strip()
    return filled


def _emit_cis_ask(model: str, missing_labels: list[str], values: dict[str, str]) -> None:
    from devstudio.ui_prompt import emit_cis_intake_ask

    parts = [f"devstudio plan sensor --model {model}"]
    for key, _label in REQUIRED:
        if values.get(key) and key != "sequence":
            parts.append(f"--{key} {values[key]}")
    if values.get("cis"):
        parts.append(f"--cis {values['cis']}")
    if values.get("features"):
        parts.append(f"--features {values['features']}")
    if values.get("sequence") and Path(values["sequence"]).is_file():
        parts.append(f"--sequence {values['sequence']}")
    parts.append("[Ask 收集的 --resolution/--fps/.../--sequence-text/--datasheet]")
    emit_cis_intake_ask(model, missing_labels, retry=" ".join(parts))


def _normalize_sequence(project: Path, values: dict[str, str]) -> dict[str, str]:
    """Accept --sequence path, --sequence-text paste, or path stuffed in sequence_text."""
    out = dict(values)
    text = (out.get("sequence_text") or "").strip()
    seq = (out.get("sequence") or "").strip()
    if text and not seq:
        path_candidate = Path(text)
        if len(text) < 260 and (path_candidate.is_file() or (project / text).is_file()):
            out["sequence"] = str(path_candidate if path_candidate.is_file() else project / text)
        else:
            out["sequence"] = text  # raw content; materialize later
    elif seq and not Path(seq).is_file() and not (project / seq).is_file():
        # treat as pasted content mistakenly passed as --sequence
        if "\n" in seq or "0x" in seq.lower() or "[" in seq:
            out["sequence_text"] = seq
    return out


def _materialize_uploads(task_dir: Path, model: str, values: dict[str, str]) -> dict[str, str]:
    """Write pasted sequence / copy datasheet into task dir; set sequence to file path."""
    out = dict(values)
    seq = (out.get("sequence") or "").strip()
    seq_text = (out.get("sequence_text") or "").strip()
    seq_path = Path(seq) if seq else None
    if seq_path and seq_path.is_file():
        dest = task_dir / seq_path.name
        if seq_path.resolve() != dest.resolve():
            dest.write_bytes(seq_path.read_bytes())
        out["sequence"] = str(dest)
    elif seq_text or (seq and not (seq_path and seq_path.is_file())):
        body = seq_text or seq
        dest = task_dir / f"{model.lower()}_sequence.txt"
        dest.write_text(body, encoding="utf-8", newline="\n")
        out["sequence"] = str(dest)
        out["sequence_text"] = body
    ds = (out.get("datasheet") or "").strip()
    if ds:
        src = Path(ds)
        if src.is_file():
            dest = task_dir / src.name
            dest.write_bytes(src.read_bytes())
            out["datasheet"] = str(dest)
    return out


def _show(project: Path, argv: list[str]) -> int:
    if len(argv) != 1:
        sys.stderr.write("用法：devstudio plan show <task_id>\n")
        return 2
    plan = tasks_dir(project) / argv[0] / "plan.md"
    if not plan.is_file():
        sys.stderr.write(f"没有任务 {argv[0]}。\n")
        return 1
    sys.stdout.write(plan.read_text(encoding="utf-8"))
    return 0


def _approve(project: Path, argv: list[str]) -> int:
    confirm = False
    apply_after = False
    args = list(argv)
    task_id = ""
    while args:
        item = args.pop(0)
        if item == "--confirm":
            confirm = True
        elif item == "--apply":
            apply_after = True
        elif not item.startswith("--") and not task_id:
            task_id = item
        else:
            sys.stderr.write("用法：devstudio plan approve <task_id> [--confirm] [--apply]\n")
            return 2
    if not task_id:
        sys.stderr.write("用法：devstudio plan approve <task_id> [--confirm] [--apply]\n")
        return 2
    loaded = load_task(project, task_id)
    plan_path = tasks_dir(project) / task_id / "plan.md"
    if loaded is None or not plan_path.is_file():
        sys.stderr.write(f"没有任务 {task_id}。\n")
        return 1
    text = plan_path.read_text(encoding="utf-8")
    if "状态：已批准" in text and "状态：待批准" not in text:
        sys.stdout.write("计划已经批准。\n")
        if apply_after:
            from devstudio.tools.source.app_cmd import run_app

            return run_app(["apply", task_id], project)
        return 0
    if "状态：待批准" not in text:
        sys.stdout.write("计划里没有待批准状态。未改文件。\n")
        return 1
    path, task = loaded
    from devstudio.ui_prompt import confirm_or_tty, prefer_ide_ask

    if not confirm:
        title = str(task.get("title") or task_id)
        summary = f"{title}\n型号：{task.get('model', '')}"
        if not confirm_or_tty(
            "DevStudio 批准计划",
            f"计划 {task_id}\n\n{summary}\n\n是否批准"
            + ("并 apply？" if apply_after else "？"),
            tty_prompt=f"批准计划 {task_id}？[y/N] ",
        ):
            if prefer_ide_ask():
                sys.stdout.write(
                    f"NEED_CONFIRM ASK_UI=yes_no：用运行中 Ask 批准后执行："
                    f"devstudio plan approve {task_id} --confirm"
                    + (" --apply" if apply_after else "")
                    + "\n"
                )
            else:
                sys.stdout.write(f"用户未批准 {task_id}。\n")
            return 1
    plan_path.write_text(text.replace("状态：待批准", "状态：已批准", 1), encoding="utf-8")
    task["plan_status"] = "已批准"
    path.write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(f"已批准 {plan_path}\n")
    if apply_after:
        from devstudio.tools.source.app_cmd import run_app

        return run_app(["apply", task_id], project)
    return 0


def _parse(argv: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    args = list(argv)
    flags = {
        "--model": "model",
        "--resolution": "resolution",
        "--fps": "fps",
        "--interface": "interface",
        "--cis": "cis",
        "--features": "features",
        "--lanes": "lanes",
        "--mclk": "mclk",
        "--i2c": "i2c",
        "--sequence": "sequence",
        "--sequence-text": "sequence_text",
        "--datasheet": "datasheet",
    }
    while args:
        item = args.pop(0)
        if item not in flags or not args:
            continue
        values[flags[item]] = args.pop(0)
    return values
