import re
import sys
from pathlib import Path

from devstudio.yamlio import load_yaml

SOURCE_RE = re.compile(r"(.+?\.(?:c|h|cpp)):(\d+):?\s+.*\b(?:fatal )?error:", re.I)
ENV_RE = re.compile(
    r"fixdep:|opensbi build failed|kernel build failed|u-boot\.cfg|os/kernel/|os/uboot/|os/opensbi/|找不到|SSH|not found in PATH|No such file",
    re.I,
)


def classify_log(text: str) -> tuple[str, list[str]]:
    hits = [f"{match.group(1)}:{match.group(2)}" for match in SOURCE_RE.finditer(text)]
    if "undeclared" in text or "undefined reference" in text or hits:
        return "source", hits
    if ENV_RE.search(text):
        return "environment", []
    if re.search(r"missing separator|multiple files", text, re.I):
        return "build_system", []
    return "unknown", []


def run_build(argv: list[str], project: Path | None = None) -> int:
    log_path = None
    exit_code = 0
    module = ""
    host = None
    round_no = 1
    artifact = ""
    run = False
    command = ""
    timeout = 600
    args = list(argv)
    while args:
        item = args.pop(0)
        if item == "--log":
            log_path = Path(args.pop(0))
        elif item == "--exit":
            exit_code = int(args.pop(0))
        elif item == "--module":
            module = args.pop(0)
        elif item == "--host":
            host = args.pop(0)
        elif item == "--round":
            round_no = int(args.pop(0))
        elif item == "--artifact":
            artifact = args.pop(0)
        elif item == "--timeout":
            timeout = int(args.pop(0))
        elif item == "--run":
            run = True
        elif item == "--command":
            command = args.pop(0)
        else:
            sys.stderr.write("用法：devstudio build --log 文件 [--exit N] [--module 名] [--host 位置] [--round N]\n")
            return 2
    config_path = (project or Path.cwd()) / "devstudio.yaml"
    build_cfg: dict = {}
    if config_path.is_file():
        loaded = load_yaml(config_path)
        if isinstance(loaded, dict):
            build_cfg = loaded.get("build", {}) or {}
    if host is None:
        host = str(build_cfg.get("host", "wsl"))
        if host == "ssh" and build_cfg.get("ssh_host"):
            host = f"ssh {build_cfg['ssh_host']}"
    if run:
        from devstudio.exec.host import run_host

        if not command:
            command = str(build_cfg.get("command", "")).strip()
        if not command:
            sys.stderr.write("没有编译命令。devstudio.yaml 缺 build.command，或用 --command 指定。\n")
            return 2
        code, output = run_host(host, command, project or Path.cwd(), timeout=timeout)
        kind, _hits = classify_log(output)
        if code == 0 and kind == "unknown":
            kind = "pass"
        sys.stdout.write(output)
        if kind == "pass":
            sys.stdout.write("完成等级：L3\n")
            return 0
        label = {"source": "源码错误", "environment": "环境错误", "build_system": "构建系统错误"}.get(kind, "未知")
        sys.stdout.write(f"退出码：{code}   分类：{label}\n")
        if kind == "source":
            sys.stdout.write("→ 回喂给 Agent\n")
        return code or 1
    if log_path is None:
        sys.stderr.write("用法：devstudio build --log 文件 [--exit N] [--module 名] [--host 位置] [--round N]\n")
        return 2
    text = log_path.read_text(encoding="utf-8", errors="replace")
    kind, hits = classify_log(text)
    if exit_code == 0 and kind == "unknown":
        kind = "pass"
    name = "devstudio-build.build_module" if module else "devstudio-build.build"
    target = f"  {module}  " if module else "  "
    sys.stdout.write(f"{name}{target}位置：{host}\n")
    label = {"source": "源码错误", "environment": "环境错误", "build_system": "构建系统错误", "pass": "通过", "unknown": "未知"}[kind]
    if kind == "source":
        sys.stdout.write(f"  退出码：{exit_code}   分类：{label}   第 {round_no}/3 轮\n")
        for line in text.splitlines():
            if "error:" in line.lower() or "undeclared" in line:
                sys.stdout.write(f"  {line.strip()}\n")
                break
        sys.stdout.write("  → 回喂给 Agent\n")
    elif kind == "environment":
        sys.stdout.write(f"  退出码：{exit_code}   分类：{label}   重试 {round_no}/2\n")
        tail = next((line.strip() for line in text.splitlines() if line.strip()), "")
        if tail:
            sys.stdout.write(f"  {tail}\n")
    elif kind == "pass":
        sys.stdout.write("  退出码：0   分类：通过\n")
        if artifact:
            sys.stdout.write(f"  产物：{artifact}\n")
        sys.stdout.write("  完成等级：L3\n")
    else:
        sys.stdout.write(f"  退出码：{exit_code}   分类：{label}\n")
    return 0 if kind == "pass" else exit_code or 1
