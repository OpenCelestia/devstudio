"""Launch the PC web UI (original Node page) via `devstudio web`."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import webbrowser
from pathlib import Path

from devstudio.root import repo_root

USAGE = """用法：
  devstudio web [--port 8081] [--product anypi_nova] [--repo 仓库根] [--tree WSL路径]
"""


def web_dir() -> Path:
    return repo_root() / "web"


def product_rel(product: str) -> str:
    return f"devstudio/web/products/{product}.json"


def parse_web_args(argv: list[str]) -> tuple[dict | None, str | None]:
    opts: dict = {
        "help": False,
        "port": 8081,
        "product": "anypi_nova",
        "repo": "",
        "tree": "",
    }
    flags = {
        "--port": "port",
        "--product": "product",
        "--repo": "repo",
        "--tree": "tree",
    }
    args = list(argv)
    while args:
        item = args.pop(0)
        if item in ("-h", "--help"):
            opts["help"] = True
            continue
        if item in ("--llm", "--model", "--api-key", "--base-url"):
            if args:
                args.pop(0)
            continue
        key = flags.get(item)
        if key is None or not args:
            return None, f"未知参数：{item}\n{USAGE}"
        value = args.pop(0)
        if key == "port":
            try:
                opts["port"] = int(value)
            except ValueError:
                return None, f"端口不是整数：{value}\n"
            continue
        opts[key] = value
    port = opts["port"]
    if not isinstance(port, int) or not 1 <= port <= 65535:
        return None, "端口要在 1 到 65535 之间。\n"
    return opts, None


def host_repo(explicit: str) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        return path.resolve() if (path / "build.sh").is_file() else None
    seen: set[Path] = set()
    for item in (Path.cwd(), repo_root().parent):
        try:
            path = item.resolve()
        except OSError:
            continue
        if path in seen:
            continue
        seen.add(path)
        if (path / "build.sh").is_file():
            return path
    return None


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def _fail(text: str) -> int:
    sys.stderr.write(text if text.endswith("\n") else text + "\n")
    return 1


def _node_bin() -> str | None:
    return shutil.which("node")


def _node_major(node: str) -> int | None:
    try:
        out = subprocess.check_output([node, "-v"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    if not out.startswith("v"):
        return None
    try:
        return int(out[1:].split(".", 1)[0])
    except ValueError:
        return None


def run_web(argv: list[str]) -> int:
    opts, error = parse_web_args(argv)
    if error or opts is None:
        sys.stderr.write(error or USAGE)
        return 2
    if opts["help"]:
        sys.stdout.write(USAGE)
        return 0

    root = web_dir()
    server = root / "server.mjs"
    product_file = root / "products" / f"{opts['product']}.json"
    index = root / "public" / "index.html"
    if not server.is_file():
        return _fail(f"找不到页面服务 {server}")
    if not index.is_file():
        return _fail(f"找不到页面 {index}")
    if not product_file.is_file():
        return _fail(f"找不到产品配置 {product_file}")

    host = host_repo(opts["repo"])
    if host is None:
        return _fail("找不到带 build.sh 的仓库根。请在仓库根目录运行，或加上 --repo。")

    url = f"http://localhost:{opts['port']}"
    if port_open(opts["port"]):
        sys.stdout.write(f"[web] 端口 {opts['port']} 已在监听。打开 {url}\n")
        webbrowser.open(url)
        return 0

    node = _node_bin()
    if not node:
        return _fail("未找到 node。请安装 Node.js 18+ 后重试：devstudio web")
    major = _node_major(node)
    if major is None or major < 18:
        return _fail(f"需要 Node.js ≥ 18，当前：{major or '未知'}。")

    tree = opts["tree"] or str(host)
    cmd = [
        node,
        str(server),
        "--port",
        str(opts["port"]),
        "--repo",
        str(host),
        "--tree",
        tree,
        "--product",
        opts["product"],
    ]
    sys.stdout.write(f"[web] UI {url}\n")
    sys.stdout.write(f"[web] 仓库 {host}\n")
    sys.stdout.write("[web] 启动原版页面（画面/文件/编译推送）；设备协议与 CLI 共用 7777\n")
    webbrowser.open(url)
    env = os.environ.copy()
    # Prefer running from web dir so relative public/products resolve.
    return subprocess.call(cmd, cwd=str(root), env=env)
