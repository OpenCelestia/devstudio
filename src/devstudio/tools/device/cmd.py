import json
import socket
import sys
from pathlib import Path

from devstudio.tools.device.capture import _lock, _unlock, latest_metrics, run_capture, run_compare
from devstudio.tools.device.connect import resolve_board, run_connect
from devstudio.tools.device.firmware import Options, run_firmware
from devstudio.tools.device.observe import run_log, run_status
from devstudio.tools.device.frame import split_board
from devstudio.tools.device.push import upload_app
from devstudio.workspace import devices_path, tasks_dir

USAGE = (
    "用法：devstudio device connect [--board 板号]\n"
    "      devstudio device push <id> [--board 板号]\n"
    "      devstudio device metrics [--board 板号]\n"
    "      devstudio device capture [--board 板号] [--seconds N] [--config 路径] [--serial 端口] [--command 命令]\n"
    "      devstudio device compare <before> <after> [--check 项]\n"
    "      devstudio device log [--board 板号] [--seconds N]\n"
    "      devstudio device status [--board 板号] [--seconds N]\n"
    "      devstudio device analyze log|status|metrics [--path 文件|目录] [--board 板号] [--seconds N]\n"
    "      devstudio device check [--board 板号] [--expect 文件] [--save-adapter]\n"
    "      devstudio device rtsp [--url rtsp://…] [--board 板号] [--port 554] [--seconds N]\n"
    "      devstudio device firmware [--board 板号] --kind usr --file usr.sqsh4\n"
    "      devstudio device firmware [--board 板号] --kind image --dir 镜像目录 [--config 名] [--parts APP,KERNEL,ROOTFS] [--yes]\n"
    "说明：未给 --board 时，终端会提示输入板号 IP；对话未提板号时由 Agent 先问用户再调工具。\n"
    "analyze：对 LOG 做 Bug 关键词归类，对 METRICS 做均值/峰值摘要；不代替人工 L5。\n"
    "check：按产品启动脚本/Adapter 期望比对实机模块、节点、进程、挂载。\n"
    "rtsp：只做连通/OPTIONS/DESCRIBE 探测，不解码画面、不自动写通过。\n"
)


def run_device(argv: list[str], project: Path | None = None) -> int:
    project = (project or Path.cwd()).resolve()
    if not argv or argv[0] not in (
        "connect",
        "push",
        "metrics",
        "capture",
        "compare",
        "log",
        "status",
        "analyze",
        "check",
        "rtsp",
        "firmware",
    ):
        sys.stderr.write(USAGE)
        return 2
    action = argv[0]
    if action == "compare":
        return _compare(project, argv[1:])
    if action == "connect":
        return _connect(project, argv[1:])
    if action == "analyze":
        from devstudio.tools.device.analyze import run_analyze

        return run_analyze(argv[1:], project)
    if action == "check":
        from devstudio.tools.device.expect import run_check

        return run_check(argv[1:], project)
    if action == "rtsp":
        from devstudio.tools.device.rtsp import run_rtsp

        return run_rtsp(argv[1:], project)
    if action in ("log", "status", "firmware"):
        return _board_action(project, action, argv[1:])
    if action == "capture":
        return _capture(project, argv[1:])
    app_id = None
    board = None
    args = argv[1:]
    if action == "push":
        if not args:
            sys.stderr.write(USAGE)
            return 2
        app_id = args.pop(0)
        if _full_image(app_id):
            sys.stdout.write("整包固件不走 device push，也不使用 burntool。请用 devstudio device firmware --kind image。未纳入 device push。\n")
            return 1
    while args:
        item = args.pop(0)
        if item == "--board" and args:
            board = args.pop(0)
        else:
            sys.stderr.write(USAGE)
            return 2
    chosen, code = _choose(project, action, board, app_id)
    if chosen is None:
        return code
    if action == "metrics":
        return _metrics(project, chosen)
    return _push(project, chosen, app_id or "")


def _connect(project: Path, args: list[str]) -> int:
    board = ""
    product = ""
    while args:
        item = args.pop(0)
        if item == "--board" and args:
            board = args.pop(0)
        elif item == "--product" and args:
            product = args.pop(0)
        else:
            sys.stderr.write(USAGE)
            return 2
    return run_connect(project, board, product)


def _board_action(project: Path, action: str, args: list[str]) -> int:
    board = ""
    seconds = 3.0
    options = Options()
    while args:
        item = args.pop(0)
        if item == "--board" and args:
            board = args.pop(0)
        elif item == "--seconds" and args:
            try:
                seconds = float(args.pop(0))
            except ValueError:
                sys.stderr.write("秒数必须是数字。\n")
                return 2
        elif item == "--kind" and args:
            options.kind = args.pop(0)
        elif item == "--file" and args:
            options.image = Path(args.pop(0))
        elif item == "--dir" and args:
            options.folder = Path(args.pop(0))
        elif item == "--config" and args:
            options.config = args.pop(0)
        elif item == "--parts" and args:
            options.parts = [name for name in args.pop(0).split(",") if name]
        elif item == "--yes":
            options.yes = True
        else:
            sys.stderr.write(USAGE)
            return 2
    board = resolve_board(project, board)
    if not board:
        return 1
    if action == "log":
        return run_log(project, board, seconds)
    if action == "status":
        return run_status(project, board, seconds)
    return run_firmware(project, board, options)


def _compare(project: Path, args: list[str]) -> int:
    if len(args) < 2:
        sys.stderr.write(USAGE)
        return 2
    before, after = Path(args[0]), Path(args[1])
    checks: list[str] = []
    rest = args[2:]
    while rest:
        item = rest.pop(0)
        if item == "--check" and rest:
            checks.append(rest.pop(0))
        else:
            sys.stderr.write(USAGE)
            return 2
    return run_compare(project, before, after, checks)


def _capture(project: Path, args: list[str]) -> int:
    board = None
    seconds = 3.0
    configs: list[str] = []
    serial = None
    command = ""
    while args:
        item = args.pop(0)
        if item == "--board" and args:
            board = args.pop(0)
        elif item == "--seconds" and args:
            try:
                seconds = float(args.pop(0))
            except ValueError:
                sys.stderr.write("秒数必须是数字。\n")
                return 2
        elif item == "--config" and args:
            configs.append(args.pop(0))
        elif item == "--serial" and args:
            serial = args.pop(0)
        elif item == "--command" and args:
            command = args.pop(0)
        else:
            sys.stderr.write(USAGE)
            return 2
    chosen, code = _choose(project, "capture", board, None)
    if chosen is None:
        return code
    return run_capture(project, str(chosen["board"]), seconds, configs, serial, command)


def _full_image(app_id: str) -> bool:
    name = app_id.lower()
    return name in {"burn", "burntool", "image", "usr.sqsh4"} or name.endswith((".img", ".bin"))


def _choose(project: Path, action: str, board: str | None, app_id: str | None) -> tuple[dict | None, int]:
    del action, app_id
    board = resolve_board(project, board)
    if not board:
        return None, 1
    devices = _devices(project)
    chosen = next((item for item in devices if item["board"] == board), None)
    if chosen is None:
        return {"board": board}, 0
    return chosen, 0


def _devices(project: Path) -> list[dict]:
    path = devices_path(project)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and item.get("board")]


def _connected(board: str) -> bool:
    host, port = split_board(board)
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _push(project: Path, device: dict, app_id: str) -> int:
    board = device["board"]
    product = device.get("product", "")
    host, port = split_board(board)
    online = _connected(board)
    if app_id in ("setting", "libstudio"):
        size = f"（{device['usr_size']}）" if device.get("usr_size") else ""
        mtd = f" {device['app_mtd']}" if device.get("app_mtd") else ""
        plan = f"用户区应用：重新打包 usr.sqsh4{size}，写 APP 分区{mtd}\n板 {board}（{product}）\n"
    else:
        plan = (
            f"板 {board}（{product}）\n"
            f"放置：/mnt/install/apps/{app_id}　备份：/mnt/install/.app_bak/{app_id}\n"
        )
    if not online:
        text = plan + f"未连接 {host}:{port}。未上传。\n"
        code = 1
    elif not _lock(project, board):
        text = plan + f"板 {board} 正被占用。已拒绝。\n"
        code = 1
    else:
        try:
            extra, code = upload_app(project, board, app_id)
        finally:
            _unlock(project)
        text = plan + extra
    log = _log_path(project)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(text)
    sys.stdout.write(text)
    sys.stdout.write(f"已写入 {log}\n")
    return code


def _metrics(project: Path, device: dict) -> int:
    found = latest_metrics(project)
    if found is not None:
        path, metrics = found
        load = metrics.get("load")
        load_text = " ".join(str(item) for item in load) if isinstance(load, list) else load or ""
        net = metrics.get("net") if isinstance(metrics.get("net"), dict) else {}
        sys.stdout.write(
            f"板 {device['board']}  uptime {metrics.get('uptime', '')}\n"
            f"cpu {metrics.get('cpu', '')}  mem_free_kb {metrics.get('mem_free_kb', '')}\n"
            f"load {load_text}  temp_c {metrics.get('temp_c', '')}\n"
            f"net rx_kb {net.get('rx_kb', '')} tx_kb {net.get('tx_kb', '')}\n"
            f"来源：{path}\n"
        )
        return 0
    metrics = device.get("metrics")
    if not metrics:
        sys.stdout.write(f"板 {device['board']} 没有 METRICS 帧。\n")
        return 1
    sys.stdout.write(
        f"板 {device['board']}  uptime {metrics.get('uptime', '')}\n"
        f"cpu {metrics.get('cpu', '')}  mem {metrics.get('mem', '')}  "
        f"load {metrics.get('load', '')}  temp {metrics.get('temp', '')}\n"
        f"net rx {metrics.get('rx', '')} tx {metrics.get('tx', '')}\n"
        f"top: {metrics.get('top', '')}\n"
        "来源：devices.json 记录，未读取实时 METRICS 帧。\n"
    )
    return 0


def _log_path(project: Path) -> Path:
    tasks = tasks_dir(project)
    if tasks.is_dir():
        names = sorted(path.name for path in tasks.iterdir() if path.is_dir())
        if names:
            return tasks / names[-1] / "device.log"
    return tasks.parent / "device.log"
