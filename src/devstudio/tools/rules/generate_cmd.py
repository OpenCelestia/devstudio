"""CLI and MCP share this entry. It only calls cis_adapt.adapt_driver."""
from __future__ import annotations

import sys
from pathlib import Path

from devstudio.orchestration.detect import split_release
from devstudio.rules.cis_adapt import adapt_driver, decode_text, manifest_json
from devstudio.workspace import tasks_dir
from devstudio.yamlio import load_yaml


def run_rules(argv: list[str], project: Path | None = None) -> int:
    project = (project or Path.cwd()).resolve()
    if not argv or argv[0] != "generate":
        sys.stderr.write(
            "用法：devstudio rules generate --task 任务号 --ref 参考型号 --model 目标型号 "
            "[--params 参数] [--sequence 文件]\n"
        )
        return 2
    values = _flags(argv[1:])
    task_id = values.get("task")
    ref = values.get("ref")
    model = values.get("model")
    if not task_id or not ref or not model:
        sys.stderr.write("缺少 --task、--ref 或 --model。\n")
        return 2
    config_path = project / "devstudio.yaml"
    if not config_path.is_file():
        sys.stderr.write("当前目录没有 devstudio.yaml。\n")
        return 1
    task_dir = tasks_dir(project) / task_id
    if not task_dir.is_dir():
        sys.stderr.write(f"没有任务 {task_id}。\n")
        return 1
    config = load_yaml(config_path)
    family, _version = split_release(str(config.get("release_id", "")))
    sequence_text = None
    sequence_name = None
    seq_path = values.get("sequence")
    if seq_path:
        path = Path(seq_path)
        if not path.is_file():
            path = project / seq_path
        if not path.is_file():
            sys.stdout.write("序列文件不存在，未生成初始化数组。\n")
        else:
            sequence_text = decode_text(path.read_bytes())
            sequence_name = path.name
            if sequence_text is None:
                sys.stdout.write("序列文件无法按文本读取，未生成初始化数组。\n")
                sequence_name = None
    else:
        sys.stdout.write("没有序列文件，未生成初始化数组。\n")
    source = task_dir / "ref" / f"{ref.lower()}.c"
    files: dict[str, str] = {}
    if source.is_file():
        text = decode_text(source.read_bytes())
        if text is not None:
            files[source.name] = text
    if not files:
        files[f"{ref.lower()}.c"] = f"/* {ref.lower()} reference */\n"
    adapted = adapt_driver(
        files,
        ref_model=ref,
        model=model,
        platform=family or "AnyCloud39AV200",
        mode="single",
        params_text=values.get("params"),
        sequence_text=sequence_text,
        sequence_name=sequence_name,
    )
    out = task_dir / "plan_src"
    out.mkdir(parents=True, exist_ok=True)
    for name, text in adapted.files.items():
        (out / name).write_text(text, encoding="utf-8", newline="\n")
    if sequence_text and adapted.init_regs_text and adapted.init_regs_name:
        (out / adapted.init_regs_name).write_text(adapted.init_regs_text, encoding="utf-8", newline="\n")
    adapted.manifest["written"] = adapted.written
    adapted.manifest["gaps"] = adapted.gaps
    (out / "merge_manifest.json").write_text(manifest_json(adapted.manifest), encoding="utf-8")
    sys.stdout.write(f"已写 {out / 'merge_manifest.json'}\n")
    sys.stdout.write(f"已写字段 {len(adapted.written)} 项，待复核 {len(adapted.gaps)} 项。\n")
    sys.stdout.write("未写入当前工程目录。\n")
    return 0


def _flags(argv: list[str]) -> dict[str, str]:
    names = {"--task": "task", "--ref": "ref", "--model": "model", "--params": "params", "--sequence": "sequence"}
    values: dict[str, str] = {}
    args = list(argv)
    while args:
        item = args.pop(0)
        if item in names and args:
            values[names[item]] = args.pop(0)
    return values
