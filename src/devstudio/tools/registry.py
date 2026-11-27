"""Shared tool contracts used by MCP and the command-line implementation.

The handlers intentionally call the existing domain commands instead of
reimplementing their behavior in the protocol layer.  This keeps IDE calls and
CLI calls on the same code path while the domain modules are gradually moved
to structured return values.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from devstudio.tools.build.classify import run_build
from devstudio.tools.device.cmd import run_device
from devstudio.tools.knowledge.peripherals import run_peripherals
from devstudio.tools.knowledge.search import run_knowledge
from devstudio.tools.project.cmd import run_project
from devstudio.tools.source.apply_cmd import run_apply
from devstudio.tools.source.driver_cmd import run_driver
from devstudio.tools.rules.generate_cmd import run_rules
from devstudio.tools.source.hal_cmd import run_hal
from devstudio.tools.source.plan_cmd import run_plan
from devstudio.tools.tune.compare import run_tune

Runner = Callable[[list[str], Path | None], int]


@dataclass(frozen=True)
class ToolSpec:
    group: str
    name: str
    description: str
    input_schema: dict[str, Any]
    runner: Runner
    argv: Callable[[dict[str, Any]], list[str]]

    def mcp_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


def _object(properties: dict[str, Any], *required: str) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


def _flag(arguments: dict[str, Any], name: str, key: str) -> list[str]:
    value = arguments.get(key)
    return [name, str(value)] if value not in (None, "") else []


def _run_numeric_check(argv: list[str], project: Path | None = None) -> int:
    del project
    text = ""
    args = list(argv)
    while args:
        item = args.pop(0)
        if item == "--text" and args:
            text = args.pop(0)
        elif item == "--path" and args:
            path = Path(args.pop(0))
            if not path.is_file():
                sys.stderr.write(f"文件不存在：{path}\n")
                return 1
            text = path.read_text(encoding="utf-8", errors="replace")
        else:
            sys.stderr.write("需要 text 或 path。\n")
            return 2
    if not text:
        sys.stderr.write("需要 text 或 path。\n")
        return 2
    from devstudio.runtime.guard import scan_numeric

    violations = scan_numeric(text)
    sys.stdout.write(json.dumps({"violations": violations}, ensure_ascii=False) + "\n")
    return 1 if violations else 0


SPECS = (
    ToolSpec("project", "project_info", "读取当前 DevStudio 工程、SDK 与构建配置。", _object({}), run_project, lambda _: ["info"]),
    ToolSpec(
        "knowledge",
        "peripheral_list",
        "列出当前 release_id 下的外设支持项（type=sensor|flash|wifi）。",
        _object({
            "type": {"type": "string", "default": "sensor", "description": "sensor|flash|wifi"},
            "interface": {"type": "string"},
        }),
        run_peripherals,
        lambda a: _flag(a, "--type", "type") + _flag(a, "--interface", "interface"),
    ),
    ToolSpec(
        "knowledge",
        "doc_search",
        "只在当前 Adapter 的离线索引中搜索文档。",
        _object({"query": {"type": "string", "minLength": 1}}, "query"),
        run_knowledge,
        lambda a: ["search", "--query", str(a["query"])],
    ),
    ToolSpec(
        "knowledge",
        "api_lookup",
        "在当前 release_id 的 API 索引中查找符号。",
        _object({"symbol": {"type": "string", "minLength": 1}}, "symbol"),
        run_knowledge,
        lambda a: ["api", "--symbol", str(a["symbol"])],
    ),
    ToolSpec(
        "knowledge",
        "errata_lookup",
        "查询当前版本已登记的勘误；没有记录时明确返回未收录。",
        _object({"model": {"type": "string", "minLength": 1}}, "model"),
        run_knowledge,
        lambda a: ["errata", "--model", str(a["model"])],
    ),
    ToolSpec(
        "knowledge",
        "index_headers",
        "把当前 SDK 的公开头文件函数名写入该 release_id 的 index。不复制头文件正文。",
        _object({}),
        run_knowledge,
        lambda a: ["index"],
    ),
    ToolSpec(
        "rules",
        "numeric_check",
        "扫描文本或文件中可能由模型写入的寄存器、ID、时序和初始化序列字段。",
        _object({
            "text": {"type": "string"},
            "path": {"type": "string"},
        }),
        _run_numeric_check,
        lambda a: _flag(a, "--text", "text") + _flag(a, "--path", "path"),
    ),
    ToolSpec(
        "rules",
        "cis_generate",
        "用参考驱动和客户序列生成方案。没有序列文件时不写初始化数组。",
        _object({
            "task_id": {"type": "string", "minLength": 1},
            "ref": {"type": "string", "minLength": 1},
            "model": {"type": "string", "minLength": 1},
            "params": {"type": "string"},
            "sequence": {"type": "string"},
        }, "task_id", "ref", "model"),
        run_rules,
        lambda a: ["generate", "--task", str(a["task_id"]), "--ref", str(a["ref"]), "--model", str(a["model"])]
        + _flag(a, "--params", "params")
        + _flag(a, "--sequence", "sequence"),
    ),
    ToolSpec(
        "source",
        "plan_sensor",
        "列表外 CIS 方案计划（对齐 FAE AI need_info）。缺参时 MCP elicitation / Agent Ask 收集"
        "分辨率、帧率、接口、Lane、MCLK、I2C、序列粘贴或路径、可选 datasheet；不写工程树。",
        _object({
            "model": {"type": "string", "minLength": 1},
            "resolution": {"type": "string"},
            "fps": {"type": "string"},
            "interface": {"type": "string"},
            "cis": {"type": "string"},
            "features": {"type": "string"},
            "lanes": {"type": "string"},
            "mclk": {"type": "string"},
            "i2c": {"type": "string"},
            "sequence": {"type": "string"},
            "sequence_text": {"type": "string"},
            "datasheet": {"type": "string"},
        }, "model"),
        run_plan,
        lambda a: ["sensor", "--model", str(a["model"])]
        + _flag(a, "--resolution", "resolution")
        + _flag(a, "--fps", "fps")
        + _flag(a, "--interface", "interface")
        + _flag(a, "--cis", "cis")
        + _flag(a, "--features", "features")
        + _flag(a, "--lanes", "lanes")
        + _flag(a, "--mclk", "mclk")
        + _flag(a, "--i2c", "i2c")
        + _flag(a, "--sequence", "sequence")
        + _flag(a, "--sequence-text", "sequence_text")
        + _flag(a, "--datasheet", "datasheet"),
    ),
    ToolSpec(
        "source",
        "plan_status",
        "读取任务计划及其批准状态。",
        _object({"task_id": {"type": "string", "pattern": "^t[0-9]+$"}}, "task_id"),
        run_plan,
        lambda a: ["show", str(a["task_id"])],
    ),
    ToolSpec(
        "source",
        "apply",
        "在计划批准后写入任务产物目录。",
        _object({
            "task_id": {"type": "string", "pattern": "^t[0-9]+$"},
            "name": {"type": "string", "minLength": 1},
            "text": {"type": "string"},
        }, "task_id", "name", "text"),
        run_apply,
        lambda a: [str(a["task_id"]), "--name", str(a["name"]), "--text", str(a["text"])],
    ),
    ToolSpec(
        "source",
        "hal_plan",
        "按客户接口和当前索引写 HAL 计划。未收录的符号不写入调用。",
        _object({
            "module": {"type": "string", "minLength": 1},
            "header": {"type": "string", "minLength": 1},
            "ref": {"type": "string"},
        }, "module", "header"),
        run_hal,
        lambda a: ["plan", "--module", str(a["module"]), "--header", str(a["header"])] + _flag(a, "--ref", "ref"),
    ),
    ToolSpec(
        "source",
        "driver_diff",
        "对照两个 release_id 的同一型号。不改 SDK。",
        _object({
            "model": {"type": "string", "minLength": 1},
            "against": {"type": "string", "minLength": 1},
        }, "model", "against"),
        run_driver,
        lambda a: ["diff", "--model", str(a["model"]), "--against", str(a["against"])],
    ),
    ToolSpec(
        "build",
        "build",
        "执行 devstudio.yaml 中声明的构建命令。",
        _object({
            "host": {"type": "string", "enum": ["local", "wsl", "ssh"]},
            "command": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 2400},
        }),
        run_build,
        lambda a: ["--run"]
        + _flag(a, "--host", "host")
        + _flag(a, "--command", "command")
        + _flag(a, "--timeout", "timeout"),
    ),
    ToolSpec(
        "build",
        "build_log",
        "分类已有构建日志。",
        _object({
            "path": {"type": "string", "minLength": 1},
            "exit_code": {"type": "integer"},
            "module": {"type": "string"},
        }, "path"),
        run_build,
        lambda a: ["--log", str(a["path"])]
        + _flag(a, "--exit", "exit_code")
        + _flag(a, "--module", "module"),
    ),
    ToolSpec(
        "device",
        "connect",
        "经 Studio TCP 7777（设备端 application/services/studio）探测板子并写入 project://devices。"
        "看画面用 devstudio web。board 可选：对话未提时先问用户输入，勿臆造。",
        _object({
            "board": {"type": "string"},
            "product": {"type": "string"},
        }),
        run_device,
        lambda a: ["connect"] + _flag(a, "--board", "board") + _flag(a, "--product", "product"),
    ),
    ToolSpec(
        "device",
        "push_app",
        "经 Studio TCP 7777 将应用推到板子 /mnt/install/apps/<id>。"
        "board 可选：对话未提则先问用户输入。看画面用 devstudio web。",
        _object({
            "app_id": {"type": "string", "minLength": 1},
            "board": {"type": "string"},
        }, "app_id"),
        run_device,
        lambda a: ["push", str(a["app_id"])] + _flag(a, "--board", "board"),
    ),
    ToolSpec(
        "device",
        "metrics",
        "读取已登记设备的最近一帧指标。",
        _object({"board": {"type": "string"}}),
        run_device,
        lambda a: ["metrics"] + _flag(a, "--board", "board"),
    ),
    ToolSpec(
        "device",
        "capture",
        "向 studio 订阅 METRICS 和 LOG，保存到任务的 capture 目录。board 可选，未提则先问用户。",
        _object({
            "board": {"type": "string"},
            "seconds": {"type": "number", "minimum": 1},
            "config": {"type": "string"},
            "command": {"type": "string"},
        }),
        run_device,
        lambda a: ["capture"]
        + _flag(a, "--board", "board")
        + _flag(a, "--seconds", "seconds")
        + _flag(a, "--config", "config")
        + _flag(a, "--command", "command"),
    ),
    ToolSpec(
        "device",
        "compare",
        "比较两次取数。检查项未全部满足时不写通过。",
        _object({
            "before": {"type": "string", "minLength": 1},
            "after": {"type": "string", "minLength": 1},
            "check": {"type": "string"},
        }, "before", "after"),
        run_device,
        lambda a: ["compare", str(a["before"]), str(a["after"])] + _flag(a, "--check", "check"),
    ),
    ToolSpec(
        "tune",
        "tune_memory",
        "比较同一块板、同一命令的前后 mem_free_kb。没有两次读数不写优化结论。",
        _object({
            "before": {"type": "string", "minLength": 1},
            "after": {"type": "string", "minLength": 1},
        }, "before", "after"),
        run_tune,
        lambda a: ["memory", "--before", str(a["before"]), "--after", str(a["after"])],
    ),
    ToolSpec(
        "tune",
        "tune_cpu",
        "比较同一块板、同一命令的前后 cpu。没有两次读数不写优化结论。",
        _object({
            "before": {"type": "string", "minLength": 1},
            "after": {"type": "string", "minLength": 1},
        }, "before", "after"),
        run_tune,
        lambda a: ["cpu", "--before", str(a["before"]), "--after", str(a["after"])],
    ),
    ToolSpec(
        "device",
        "device_log",
        "从指定板读取 LOG 帧并保存。board 可选，未提则先问用户。",
        _object({
            "board": {"type": "string"},
            "seconds": {"type": "number"},
        }),
        run_device,
        lambda a: ["log"] + _flag(a, "--board", "board") + _flag(a, "--seconds", "seconds"),
    ),
    ToolSpec(
        "device",
        "device_status",
        "从指定板读取 CPU 和内存。board 可选，未提则先问用户。",
        _object({
            "board": {"type": "string"},
            "seconds": {"type": "number"},
        }),
        run_device,
        lambda a: ["status"] + _flag(a, "--board", "board") + _flag(a, "--seconds", "seconds"),
    ),
    ToolSpec(
        "device",
        "device_analyze",
        "分析设备 LOG（Bug 关键词归类）或 METRICS（均值/峰值摘要）。可给 --path 或 --board。",
        _object({
            "kind": {"type": "string", "enum": ["log", "status", "metrics"]},
            "path": {"type": "string"},
            "board": {"type": "string"},
            "seconds": {"type": "number"},
        }, "kind"),
        run_device,
        lambda a: ["analyze", str(a["kind"])]
        + _flag(a, "--path", "path")
        + _flag(a, "--board", "board")
        + _flag(a, "--seconds", "seconds"),
    ),
    ToolSpec(
        "device",
        "device_check",
        "按产品启动脚本/Adapter 期望比对实机模块、节点、进程、挂载（问题排查用）。",
        _object({
            "board": {"type": "string"},
            "expect": {"type": "string"},
            "save_adapter": {"type": "boolean"},
        }),
        run_device,
        lambda a: ["check"]
        + _flag(a, "--board", "board")
        + _flag(a, "--expect", "expect")
        + (["--save-adapter"] if a.get("save_adapter") else []),
    ),
    ToolSpec(
        "device",
        "device_rtsp",
        "探测 RTSP 连通（TCP/OPTIONS/DESCRIBE），不解码画面、不自动写通过。",
        _object({
            "url": {"type": "string"},
            "board": {"type": "string"},
            "port": {"type": "number"},
            "seconds": {"type": "number"},
        }),
        run_device,
        lambda a: ["rtsp"]
        + _flag(a, "--url", "url")
        + _flag(a, "--board", "board")
        + _flag(a, "--port", "port")
        + _flag(a, "--seconds", "seconds"),
    ),
    ToolSpec(
        "device",
        "device_firmware",
        "按 usr 或 image 推送固件，不使用 burntool。image 按产品 burntool 分区表和设备 /proc/mtd 核对，"
        "不写引导分区；没有 yes 时只出计划。board 可选，未提则先问用户。",
        _object({
            "board": {"type": "string"},
            "kind": {"type": "string", "enum": ["usr", "image"]},
            "file": {"type": "string"},
            "dir": {"type": "string"},
            "config": {"type": "string"},
            "parts": {"type": "string"},
            "yes": {"type": "boolean"},
        }, "kind"),
        run_device,
        lambda a: ["firmware", "--kind", str(a["kind"])]
        + _flag(a, "--board", "board")
        + _flag(a, "--file", "file") + _flag(a, "--dir", "dir") + _flag(a, "--config", "config")
        + _flag(a, "--parts", "parts") + (["--yes"] if a.get("yes") is True else []),
    ),
)

_BY_KEY = {(spec.group, spec.name): spec for spec in SPECS}


def list_tools(group: str) -> list[dict[str, Any]]:
    return [spec.mcp_definition() for spec in SPECS if spec.group == group]


def invoke(group: str, name: str, arguments: dict[str, Any] | None, project: Path | None = None) -> dict[str, Any]:
    spec = _BY_KEY.get((group, name))
    if spec is None:
        return {
            "status": "error",
            "code": "E-TOOL-404",
            "message": "工具不在当前组或尚未实现",
        }
    if arguments is not None and not isinstance(arguments, dict):
        return {
            "status": "error",
            "code": "E-TOOL-INPUT",
            "message": "arguments 必须是对象",
        }
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            exit_code = spec.runner(spec.argv(arguments or {}), project)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        return {
            "status": "error",
            "code": "E-TOOL-INPUT",
            "message": f"参数无效：{exc}",
        }
    return {
        "status": "ok" if exit_code == 0 else "error",
        "exit_code": exit_code,
        "stdout": out.getvalue(),
        "stderr": err.getvalue(),
    }
