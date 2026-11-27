import re
import subprocess
from pathlib import Path


def _decode(data: bytes) -> str:
    """子进程输出解码。wsl.exe 自己的诊断输出是无 BOM 的 UTF-16LE，零字节密集；其余按 UTF-8。"""
    if not data:
        return ""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    if len(data) >= 4 and data.count(0) > len(data) // 3:
        return data.decode("utf-16-le", errors="replace")
    return data.decode("utf-8", errors="replace")


def _sh(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def wsl_cwd(cwd: Path) -> tuple[list[str], str] | str:
    """把 Windows 上打开的工程目录换成 WSL 里的路径。网络路径返回说明。"""
    text = str(cwd)
    unc = text.replace("/", "\\")
    match = re.match(r"^\\\\wsl(?:\$|\.localhost)\\([^\\]+)\\(.*)$", unc, re.IGNORECASE)
    if match:
        rest = match.group(2).replace("\\", "/")
        linux = rest if rest.startswith("/") else f"/{rest}"
        return ["-d", match.group(1)], linux
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", text)
    if match:
        rest = match.group(2).replace("\\", "/")
        linux = f"/mnt/{match.group(1).lower()}/{rest}" if rest else f"/mnt/{match.group(1).lower()}"
        return [], linux
    if unc.startswith("\\\\"):
        return "工程在网络路径上，WSL 访问不到。请把工程放在本机磁盘，或放进 WSL 后用 \\\\wsl$\\<发行版>\\ 在 Windows 上打开。"
    return [], text.replace("\\", "/")


def run_host(host: str, command: str, cwd: Path, timeout: int = 30) -> tuple[int, str]:
    """在本机、WSL 或 SSH 上执行已经声明的编译命令。失败时返回退出码和输出。"""
    cwd = Path(cwd).resolve()
    try:
        if host == "local":
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                shell=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        elif host == "wsl":
            located = wsl_cwd(cwd)
            if isinstance(located, str):
                return 1, located
            extra, linux_cwd = located
            completed = subprocess.run(
                ["wsl.exe", *extra, "-e", "bash", "-lc", f"cd {_sh(linux_cwd)} && {command}"],
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        elif host.startswith("ssh"):
            target = host.split(None, 1)[1] if " " in host else ""
            if not target:
                return 1, "SSH 主机别名为空"
            remote = f"cd {_sh(str(cwd))} && {command}"
            completed = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", target, remote],
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        else:
            return 1, f"不认识的编译位置：{host}"
    except subprocess.TimeoutExpired:
        return 1, "超时"
    except OSError as exc:
        return 1, str(exc)
    output = _decode(completed.stdout) + _decode(completed.stderr)
    if host.startswith("ssh") and completed.returncode != 0 and output:
        output = f"SSH\n{output}"
    return completed.returncode, output
