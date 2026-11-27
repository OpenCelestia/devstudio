import os
import sys

from devstudio import __version__
from devstudio.adapter.catalog import list_adapters
from devstudio.adapter.generate import run_adapter_generate
from devstudio.orchestration.env_check import run_doctor
from devstudio.orchestration.init_cmd import run_init
from devstudio.orchestration.switch_cmd import run_switch
from devstudio.mcp.server import run_mcp
from devstudio.tools.build.classify import run_build
from devstudio.tools.device.cmd import run_device
from devstudio.tools.knowledge.peripherals import run_peripherals
from devstudio.tools.knowledge.search import run_knowledge
from devstudio.tools.project.cmd import run_project
from devstudio.tools.report import run_report
from devstudio.tools.source.app_cmd import run_app
from devstudio.tools.source.apply_cmd import run_apply, run_verify
from devstudio.tools.source.driver_cmd import run_driver
from devstudio.tools.source.hal_cmd import run_hal
from devstudio.tools.rules.generate_cmd import run_rules
from devstudio.tools.source.plan_cmd import run_plan
from devstudio.tools.source.sensor_flow import run_sensor
from devstudio.tools.tune.compare import run_tune
from devstudio.tools.web.launch import run_web

USAGE = """用法：
  devstudio --version
  devstudio adapter list
  devstudio adapter generate --sdk <SDK版本目录> [--force] [--fae-root <FAE fae-ai>] [--no-index]
  devstudio init [--yes] [--sdk 路径] [--state 状态] [--product 产品] [--sync-product 产品] [--force] [--ide cursor,vscode,claude,codex|all] [--remove]
  devstudio doctor [--non-interactive]
  devstudio switch [list] | product [产品名] | sdk <SDK版本目录>
  devstudio project info
  devstudio knowledge peripherals [--type sensor|flash|wifi] [--interface 接口]
  devstudio knowledge spec|search|api|errata|index
  devstudio driver locate --model 型号
  devstudio driver diff --model 型号 --against release_id
  devstudio sensor confirm|build --model 型号 [--cis 模式]
  devstudio rules generate --task 任务号 --ref 参考型号 --model 目标型号 [--params 参数] [--sequence 文件]
  devstudio plan show|approve|merge|merge-apply <task_id>
  devstudio plan review <task_id> --item 待复核原文
  devstudio plan sensor --model 型号 [--resolution 宽x高] [--fps 帧率] [--interface 接口] [--cis 模式] [--lanes N] [--mclk 频率] [--i2c 地址] [--sequence 文件]
  devstudio apply <task_id> --name 文件名 --text 内容
  devstudio app add <id> [--name-zh 中文] [--name-en 英文] [--form cloud]
  devstudio app modify|remove|check <id>
  devstudio app apply <task_id>
  devstudio app recipe [--form 形态]
  devstudio hal plan --module 模块 --header 头文件 [--ref 参考.c]
  devstudio hal apply <task_id>
  devstudio tune memory|cpu --before 目录 --after 目录
  devstudio verify
  devstudio build --log 文件 [--exit N] [--module 名] [--host 位置] [--round N]
  devstudio build --run [--host local|wsl|ssh 别名] [--command 命令] [--timeout 秒]   # 缺省取 devstudio.yaml
  devstudio device connect [--board 板号]
  devstudio device push <id> [--board 板号]
  devstudio device metrics [--board 板号]
  devstudio device capture [--board 板号] [--seconds N] [--config 路径] [--serial 端口] [--command 命令]
  devstudio device compare <before> <after> [--check 项]
  devstudio device log [--board 板号] [--seconds N]
  devstudio device status [--board 板号] [--seconds N]
  devstudio device analyze log|status|metrics [--path 文件|目录] [--board 板号] [--seconds N]
  devstudio device check [--board 板号] [--expect 文件] [--save-adapter]
  devstudio device rtsp [--url rtsp://…] [--board 板号] [--port 554] [--seconds N]
  devstudio device firmware [--board 板号] --kind usr --file usr.sqsh4
  devstudio device firmware [--board 板号] --kind image --dir 镜像目录 [--config 名] [--parts 分区] [--yes]
  # 未给 --board：终端提示输入；对话未提板号时先问用户
  devstudio report export <task_id>
  devstudio web [--port 8081] [--product anypi_nova] [--repo 仓库根]
  devstudio mcp --group project|knowledge|rules|source|build|device|tune
"""


def _configure_stdio() -> None:
    """Force UTF-8 stdio so Git Bash / cmd / IDE terminals do not show GBK mojibake."""
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main(argv: list[str] | None = None, lines: list[str] | None = None) -> int:
    _configure_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["--version"], ["-V"]):
        print(f"Anyka DevStudio {__version__}")
        return 0
    if args == ["adapter", "list"]:
        names = list_adapters()
        print("已安装的 Adapter：")
        if not names:
            print("（无）")
        else:
            for name in names:
                print(name)
        return 0
    if args and args[0] == "adapter" and len(args) >= 2 and args[1] == "generate":
        return run_adapter_generate(args[2:])
    if not args:
        sys.stderr.write(USAGE)
        return 2
    command, rest = args[0], args[1:]
    if command == "init":
        return run_init(rest, lines=lines)
    if command in ("doctor", "env"):
        return run_doctor(rest)
    if command == "switch":
        return run_switch(rest, lines=lines)
    if command == "project":
        return run_project(rest)
    if command == "knowledge" and rest and rest[0] == "peripherals":
        return run_peripherals(rest[1:])
    if command == "knowledge":
        return run_knowledge(rest)
    if command == "driver":
        return run_driver(rest)
    if command == "sensor":
        return run_sensor(rest)
    if command == "plan":
        return run_plan(rest)
    if command == "rules":
        return run_rules(rest)
    if command == "apply":
        return run_apply(rest)
    if command == "verify":
        return run_verify(rest)
    if command == "app":
        return run_app(rest)
    if command == "hal":
        return run_hal(rest)
    if command == "tune":
        return run_tune(rest)
    if command == "build":
        return run_build(rest)
    if command == "device":
        return run_device(rest)
    if command == "web":
        return run_web(rest)
    if command == "report":
        return run_report(rest)
    if command == "mcp":
        return run_mcp(rest)
    sys.stderr.write(USAGE)
    return 2
