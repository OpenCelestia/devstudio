"""Environment doctor: verify DevStudio / build host and print fix hints.

Typical Mac path: macOS (IDE + DevStudio) + UTM Ubuntu (cross compile via SSH).
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from devstudio.root import repo_root
from devstudio.yamlio import load_yaml


@dataclass
class Item:
    name: str
    ok: bool
    detail: str
    fix: str = ""


def run_doctor(argv: list[str], project: Path | None = None) -> int:
    project = (project or Path.cwd()).resolve()
    interactive = True
    set_host = ""
    ssh_host = ""
    ssh_workdir = ""
    args = list(argv)
    while args:
        item = args.pop(0)
        if item in ("--non-interactive", "--ci"):
            interactive = False
        elif item == "--set-host":
            set_host = args.pop(0) if args else ""
        elif item == "--ssh-host":
            ssh_host = args.pop(0) if args else ""
        elif item == "--ssh-workdir":
            ssh_workdir = args.pop(0) if args else ""
        elif item in ("-h", "--help"):
            sys.stdout.write(
                "用法：devstudio doctor [--non-interactive]\n"
                "      devstudio doctor --set-host ssh --ssh-host <别名> --ssh-workdir <路径>\n"
                "在工程根目录检查 DevStudio 运行时与编译主机（Mac+UTM+Ubuntu / SSH）。\n"
                "失败时输出 NEED_* ASK_UI=…，Agent 须用当前 IDE 运行中选择/输入展示，禁止聊天干等。\n"
            )
            return 0
        else:
            sys.stderr.write(
                "用法：devstudio doctor [--non-interactive] "
                "[--set-host ssh --ssh-host 别名 --ssh-workdir 路径]\n"
            )
            return 2

    if set_host:
        return _apply_build_host(project, set_host, ssh_host, ssh_workdir)

    items = collect_checks(project)
    failed = [item for item in items if not item.ok]
    for item in items:
        mark = "OK" if item.ok else "FAIL"
        sys.stdout.write(f"[{mark}] {item.name}: {item.detail}\n")
        if not item.ok and item.fix:
            for line in item.fix.splitlines():
                sys.stdout.write(f"       → {line}\n")

    if not failed:
        sys.stdout.write("环境检查通过。\n")
        return 0

    sys.stdout.write(f"环境检查未通过：{len(failed)} 项。请按上方提示修改后重跑：devstudio doctor\n")
    if interactive and sys.platform == "darwin":
        _print_mac_utm_guide()
    if interactive:
        _prompt_fix_actions(project, failed)
    return 1


def _apply_build_host(project: Path, host: str, ssh_host: str, ssh_workdir: str) -> int:
    yaml_path = project / "devstudio.yaml"
    if not yaml_path.is_file():
        sys.stdout.write("没有 devstudio.yaml，无法写入 build.host。请先 init。\n")
        return 1
    host = host.strip()
    if host not in ("local", "wsl", "ssh"):
        sys.stdout.write("build.host 只能是 local / wsl / ssh。\n")
        return 2
    data = load_yaml(yaml_path)
    build = data.get("build") if isinstance(data.get("build"), dict) else {}
    data["build"] = build
    build["host"] = host
    if host == "ssh":
        if not ssh_host or not ssh_workdir:
            sys.stdout.write("host=ssh 时必须同时给 --ssh-host 与 --ssh-workdir。\n")
            return 2
        build["ssh_host"] = ssh_host
        build["ssh_workdir"] = ssh_workdir
    else:
        build.pop("ssh_host", None)
        build.pop("ssh_workdir", None)
    from devstudio.yamlio import dump_yaml

    yaml_path.write_text(dump_yaml(data), encoding="utf-8")
    sys.stdout.write(f"已更新 {yaml_path}：build.host={host}\n")
    return run_doctor(["--non-interactive"], project=project)


def _prompt_fix_actions(project: Path, failed: list[Item]) -> None:
    from devstudio.ui_prompt import ask_choice, ask_string, prefer_ide_ask

    names = {item.name for item in failed}
    need_ssh = "编译位置" in names and any(
        "host=local" in item.detail or "不支持 build.host=wsl" in item.detail for item in failed
    )
    if need_ssh or (sys.platform == "darwin" and "编译位置" in names):
        picked = ask_choice(
            "DevStudio 环境",
            "macOS/当前配置无法本机交叉编译。请选择如何处理：",
            (
                ("set_ssh", "改为 SSH（UTM Ubuntu / Linux 服务器）"),
                ("guide_only", "先看提示，我稍后自己改"),
                ("recheck", "我已改好，重新检查"),
            ),
            default="set_ssh",
        )
        if picked is None and prefer_ide_ask():
            sys.stdout.write(
                "Agent：用 IDE 选择后，若用户选 set_ssh，再 ASK 收集 ssh 别名与远端目录，"
                "然后执行：devstudio doctor --set-host ssh --ssh-host <别名> --ssh-workdir <路径>\n"
            )
            return
        if picked == "set_ssh":
            alias = ask_string(
                "DevStudio SSH",
                "SSH 主机别名（~/.ssh/config，如 utm-ubuntu）",
            )
            if alias is None and prefer_ide_ask():
                return
            workdir = ask_string(
                "DevStudio SSH",
                "远端工程目录（虚拟机内路径，如 /home/u/celestia/develop）",
            )
            if workdir is None and prefer_ide_ask():
                return
            if alias and workdir:
                _apply_build_host(project, "ssh", alias, workdir)
            return
        if picked == "recheck":
            run_doctor(["--non-interactive"], project=project)
            return
        return

    ask_choice(
        "DevStudio 环境",
        f"有 {len(failed)} 项未通过。下一步？",
        (
            ("guide_only", "按上方 FAIL 提示修改环境"),
            ("recheck", "我已改好，重新检查"),
        ),
        default="guide_only",
    )


def collect_checks(project: Path) -> list[Item]:
    items: list[Item] = []
    items.append(_check_python())
    items.append(_check_launcher())
    items.append(_check_path())
    items.append(_check_cli_resolution())
    items.append(_check_platform())
    yaml_path = project / "devstudio.yaml"
    if yaml_path.is_file():
        items.extend(_check_project_yaml(project, yaml_path))
    else:
        items.append(
            Item(
                "工程配置",
                False,
                f"{project} 没有 devstudio.yaml",
                "在工程根执行 setup 或：devstudio init\n"
                "Mac 芯片工程：init 时编译位置选 ssh，指向 UTM Ubuntu 或 Linux 服务器",
            )
        )
    return items


def _check_python() -> Item:
    ver = sys.version_info
    ok = ver >= (3, 11)
    return Item(
        "Python",
        ok,
        f"{platform.python_implementation()} {ver.major}.{ver.minor}.{ver.micro}",
        "安装 Python 3.11+（macOS：brew install python@3.12），再执行 ./setup_macos.sh",
    )


def _check_launcher() -> Item:
    root = repo_root()
    src = root / "src" / "devstudio"
    if not src.is_dir():
        return Item(
            "DevStudio 安装",
            False,
            f"找不到源码树：{src}",
            "保留完整 DevStudio 目录；不要只拷贝 setup 脚本。重新执行对应平台的 setup_* --install-only",
        )
    if sys.platform == "win32":
        shim_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Anyka" / "DevStudio"
        cmd = shim_dir / "devstudio.cmd"
        sh = shim_dir / "devstudio"
        if cmd.is_file() and sh.is_file():
            return Item("DevStudio 启动器", True, f"root={root}; shim={cmd}", "")
        if cmd.is_file():
            return Item(
                "DevStudio 启动器",
                False,
                f"缺少 Git Bash 入口：{sh}",
                "在 DevStudio 目录执行：powershell -NoProfile -ExecutionPolicy Bypass -File .\\setup_windows.ps1 --install-only",
            )
        return Item(
            "DevStudio 启动器",
            False,
            f"缺少 {cmd}",
            "在 DevStudio 目录执行：powershell -NoProfile -ExecutionPolicy Bypass -File .\\setup_windows.ps1 --install-only",
        )
    bin_path = Path(os.environ.get("DEVSTUDIO_BIN", "") or (Path.home() / ".local" / "bin" / "devstudio"))
    if bin_path.is_file() and os.access(bin_path, os.X_OK):
        return Item("DevStudio 启动器", True, str(bin_path), "")
    setup = "./setup_macos.sh" if sys.platform == "darwin" else "./setup_linux.sh"
    return Item(
        "DevStudio 启动器",
        False,
        f"缺少可执行启动器：{bin_path}",
        f"在 DevStudio 目录执行：{setup} --install-only",
    )


def _check_path() -> Item:
    if sys.platform == "win32":
        shim_dir = str(Path(os.environ.get("LOCALAPPDATA", "")) / "Anyka" / "DevStudio")
        path = os.environ.get("PATH", "")
        parts = [p for p in path.split(os.pathsep) if p]
        if any(p.rstrip("\\/").lower() == shim_dir.rstrip("\\/").lower() for p in parts):
            # Prefer first hit among PATH entries that resolve "devstudio"
            return Item("PATH", True, f"含 {shim_dir}", "")
        return Item(
            "PATH",
            False,
            f"当前 PATH 不含 {shim_dir}",
            "重跑 setup_windows.ps1 --install-only，然后新开终端 / 重启 IDE",
        )
    local_bin = str(Path.home() / ".local" / "bin")
    path = os.environ.get("PATH", "")
    if local_bin in path.split(os.pathsep):
        return Item("PATH", True, f"含 {local_bin}", "")
    launcher = Path(local_bin) / "devstudio"
    if launcher.is_file() and os.access(launcher, os.X_OK):
        return Item(
            "PATH",
            True,
            f"当前非登录 shell 未含 {local_bin}，但启动器存在：{launcher}",
            "",
        )
    profile = "~/.zprofile" if sys.platform == "darwin" else "~/.profile"
    return Item(
        "PATH",
        False,
        f"当前 shell 的 PATH 不含 {local_bin}",
        f'写入 {profile}：export PATH="$HOME/.local/bin:$PATH"\n然后执行：source {profile} 或新开终端',
    )


def _cli_candidates() -> list[Path]:
    """Return PATH-ordered devstudio launchers (unique)."""
    name = "devstudio"
    names = [name, f"{name}.cmd", f"{name}.exe", f"{name}.bat"] if sys.platform == "win32" else [name]
    found: list[Path] = []
    seen: set[str] = set()
    for part in os.environ.get("PATH", "").split(os.pathsep):
        if not part:
            continue
        base = Path(part)
        for n in names:
            cand = base / n
            try:
                if not cand.is_file():
                    continue
            except OSError:
                continue
            key = str(cand).lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(cand)
    return found


def _probe_cli(path: Path) -> tuple[bool, str]:
    name = "devstudio"
    if sys.platform == "win32" and path.suffix.lower() in ("", ".sh"):
        # Extensionless Git Bash shim cannot be CreateProcess'd; validate content.
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return False, str(exc)
        if f"-m {name}" in body:
            return True, "git-bash shim"
        return False, f"not a {name} shim"
    cmd = [str(path), "--version"]
    if sys.platform == "win32" and path.suffix.lower() in (".cmd", ".bat"):
        cmd = ["cmd", "/c", str(path), "--version"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except OSError as exc:
        return False, str(exc)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode == 0 and "DevStudio" in out:
        return True, out.splitlines()[0] if out else "ok"
    if "ModuleNotFoundError" in out or "No module named" in out:
        return False, f"ModuleNotFoundError: No module named '{name}'"
    return False, out.splitlines()[0] if out else f"exit {proc.returncode}"


def _check_cli_resolution() -> Item:
    cands = _cli_candidates()
    if not cands:
        which = shutil.which("devstudio")
        detail = "PATH 上找不到 " + "devstudio"
        if which:
            detail = f"which={which} 但探测列表为空"
        fix = (
            "Windows：重跑 setup_windows.ps1 --install-only\n"
            "Linux/macOS：重跑 ./setup_linux.sh 或 ./setup_macos.sh --install-only"
        )
        return Item("CLI 入口", False, detail, fix)
    lines = []
    first_ok = None
    broken = []
    for path in cands:
        ok, detail = _probe_cli(path)
        mark = "OK" if ok else "BROKEN"
        lines.append(f"{mark} {path} ({detail})")
        if ok and first_ok is None:
            first_ok = path
        if not ok:
            broken.append(path)
    summary = "; ".join(lines[:4])
    if len(lines) > 4:
        summary += f"; …共 {len(lines)} 个"
    if first_ok is None:
        return Item(
            "CLI 入口",
            False,
            summary,
            "存在损坏的 pip/console-script 入口（常见于 IDE 自带 Python 的 Scripts）。\n"
            "Windows：在 DevStudio 目录执行 setup_windows.ps1 --install-only（会停用损坏的 .exe 并写 Git Bash 启动器）\n"
            "勿对第三方 Python 执行 pip install anyka-devstudio；只用 setup 安装的启动器",
        )
    if broken and cands[0] != first_ok:
        return Item(
            "CLI 入口",
            False,
            f"PATH 优先命中损坏入口。{summary}",
            "重跑对应平台 setup_* --install-only，新开终端；Git Bash 勿依赖 .cmd（应使用无后缀启动器）",
        )
    if broken:
        return Item(
            "CLI 入口",
            True,
            f"默认可用：{first_ok}。另有损坏入口已排后或待清理。{summary}",
            "建议重跑 setup_* --install-only 停用损坏入口，避免 Agent/IDE 偶发命中",
        )
    return Item("CLI 入口", True, summary, "")


def _check_platform() -> Item:
    system = platform.system()
    if system == "Darwin":
        return Item(
            "主机平台",
            True,
            "macOS（芯片交叉编译须在 Linux：UTM Ubuntu 或 SSH 服务器）",
            "",
        )
    if system == "Windows":
        return Item("主机平台", True, "Windows（芯片编译通常用 WSL）", "")
    return Item("主机平台", True, f"{system}（可本机编译）", "")


def _check_project_yaml(project: Path, yaml_path: Path) -> list[Item]:
    items: list[Item] = []
    try:
        data = load_yaml(yaml_path)
    except OSError as exc:
        return [Item("工程配置", False, str(exc), "检查 devstudio.yaml 是否可读")]
    build = data.get("build") if isinstance(data.get("build"), dict) else {}
    host = str(build.get("host") or "").strip() or "local"
    command = str(build.get("command") or "").strip()
    items.append(
        Item(
            "devstudio.yaml",
            True,
            f"state={data.get('state', '')} release_id={data.get('release_id', '')} host={host}",
            "",
        )
    )
    if not command:
        items.append(
            Item(
                "编译命令",
                False,
                "build.command 为空",
                "重新 init，或手工写入 build.command（如 ./build.sh --product-name anypi_nova）",
            )
        )
    else:
        items.append(Item("编译命令", True, command, ""))

    if host == "local" and sys.platform == "darwin":
        items.append(
            Item(
                "编译位置",
                False,
                "macOS 上 host=local，芯片交叉工具链无法在本机运行",
                "改 devstudio.yaml：\n"
                "  build:\n"
                "    host: ssh\n"
                "    ssh_host: <UTM-Ubuntu别名>\n"
                "    ssh_workdir: <虚拟机内工程路径>\n"
                "或重新 init，编译位置选 ssh。\n"
                "也可用 IDE Remote-SSH 打开 Ubuntu，在 Linux 侧执行 ./setup_linux.sh",
            )
        )
    elif host == "wsl" and sys.platform == "darwin":
        items.append(
            Item(
                "编译位置",
                False,
                "macOS 不支持 build.host=wsl",
                "改为 host: ssh，指向 UTM Ubuntu 或 Linux 服务器",
            )
        )
    elif host == "ssh" or host.startswith("ssh "):
        items.extend(_check_ssh(build, host))
    elif host == "local":
        items.append(Item("编译位置", True, "local（Linux 本机）", ""))
    elif host == "wsl":
        items.append(Item("编译位置", True, "wsl", "确认 WSL 内有工程副本与工具链"))
    else:
        items.append(
            Item(
                "编译位置",
                False,
                f"不认识的 host={host}",
                "build.host 只能是 local / wsl / ssh",
            )
        )

    release_id = str(data.get("release_id") or "")
    if release_id:
        adapter = repo_root() / "sdk" / "adapters" / release_id
        # release_id may be AnyCloud39AV200_V1.04 while folder is same
        if not adapter.is_dir():
            # try underscore form already
            items.append(
                Item(
                    "Adapter",
                    False,
                    f"缺少 sdk/adapters/{release_id}",
                    "更新 DevStudio 仓库，确认 Adapter 已随仓库分发；devstudio adapter list",
                )
            )
        else:
            items.append(Item("Adapter", True, str(adapter), ""))
    return items


def _check_ssh(build: dict, host: str) -> list[Item]:
    items: list[Item] = []
    alias = str(build.get("ssh_host") or "").strip()
    if host.startswith("ssh ") and not alias:
        alias = host.split(None, 1)[1].strip()
    workdir = str(build.get("ssh_workdir") or "").strip()
    if not alias:
        items.append(
            Item(
                "SSH 主机",
                False,
                "build.ssh_host 为空",
                "在 ~/.ssh/config 增加 Host 别名，例如：\n"
                "  Host utm-ubuntu\n"
                "    HostName 192.168.64.2\n"
                "    User <用户名>\n"
                "    IdentityFile ~/.ssh/id_ed25519\n"
                "然后在 devstudio.yaml 写 ssh_host: utm-ubuntu",
            )
        )
        return items

    if not shutil.which("ssh"):
        items.append(
            Item(
                "SSH 客户端",
                False,
                "未找到 ssh 命令",
                "macOS 安装 Command Line Tools：xcode-select --install",
            )
        )
        return items

    items.append(Item("SSH 主机别名", True, alias, ""))
    probe = _ssh_run(alias, "echo ok && uname -s")
    if probe[0] != 0 or "ok" not in probe[1]:
        items.append(
            Item(
                "SSH 连通",
                False,
                (probe[1] or "连接失败").strip().splitlines()[-1][:160],
                f"确认 UTM Ubuntu 已开机且 SSH 已启用：sudo apt install openssh-server && sudo systemctl enable --now ssh\n"
                f"本机执行：ssh {alias} echo ok（须免密）\n"
                "首次登录接受 host key；密钥：ssh-copy-id {alias}",
            )
        )
        return items
    items.append(Item("SSH 连通", True, probe[1].strip().replace("\n", " / "), ""))

    if not workdir:
        items.append(
            Item(
                "远端工程目录",
                False,
                "build.ssh_workdir 为空",
                "在 UTM Ubuntu 中放置 Celestia/SDK 工程，例如 ~/celestia/develop\n"
                "devstudio.yaml 写 ssh_workdir: /home/<用户>/celestia/develop",
            )
        )
        return items

    remote = _ssh_run(
        alias,
        f"cd {_sh(workdir)} && "
        f"{{ test -f build.sh || test -f Makefile || test -f auto_build.sh; }} "
        f"&& echo TREE_OK || echo TREE_MISSING; "
        f"if command -v riscv64-anyka-linux-musl-gcc >/dev/null 2>&1 || "
        f"command -v arm-anycloud-linux-uclibcgnueabi-gcc >/dev/null 2>&1 || "
        f"command -v aarch64-anycloud-linux-gnu-gcc >/dev/null 2>&1; "
        f"then echo TC_OK; else echo TC_MISSING; fi",
    )
    out = remote[1]
    if "TREE_OK" in out:
        items.append(Item("远端工程树", True, workdir, ""))
    else:
        items.append(
            Item(
                "远端工程树",
                False,
                f"{workdir} 缺少 build.sh/Makefile（或目录不存在）",
                "把源码放到 UTM Ubuntu 该路径，或修正 ssh_workdir\n"
                "共享盘可用 UTM VirtioFS / 共享目录，勿只放在 Mac 盘符指望 Linux 直接编译",
            )
        )
    if "TC_OK" in out:
        items.append(Item("远端工具链", True, "交叉编译器在 PATH 中", ""))
    else:
        items.append(
            Item(
                "远端工具链",
                False,
                "远端 PATH 未找到常见 Anyka 交叉编译器",
                "在 Ubuntu 中解压 SDK 工具链并写入 ~/.bashrc：\n"
                "  export PATH=\"<toolchain>/bin:$PATH\"\n"
                f"验证：ssh {alias} 'which riscv64-anyka-linux-musl-gcc || which arm-anycloud-linux-uclibcgnueabi-gcc'",
            )
        )
    return items


def _ssh_run(alias: str, remote: str) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=5",
                "-o",
                "StrictHostKeyChecking=accept-new",
                alias,
                remote,
            ],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    text = (completed.stdout or b"").decode("utf-8", errors="replace") + (
        completed.stderr or b""
    ).decode("utf-8", errors="replace")
    return completed.returncode, text


def _sh(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def _print_mac_utm_guide() -> None:
    sys.stdout.write(
        "\nMac + UTM + Ubuntu 推荐步骤：\n"
        "  1) UTM 新建 Ubuntu 虚拟机，安装 openssh-server，记下 IP\n"
        "  2) Mac ~/.ssh/config 增加 Host 别名，ssh <别名> echo ok 免密\n"
        "  3) 虚拟机内放置工程与工具链（建议 ~/celestia/develop）\n"
        "  4) Mac 工程 devstudio.yaml：build.host=ssh，填写 ssh_host / ssh_workdir\n"
        "  5) 再执行：devstudio doctor\n"
    )
