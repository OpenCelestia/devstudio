"""Upload one app over Studio TCP 7777. `devstudio web` is only the PC page.

Transport: DeviceLink → on-device application/services/studio (FILE_PUT + EXEC).
Landing: /mnt/install/apps/<id> (place=usb on device storage, not a PC USB cable).
"""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

from devstudio.tools.device.connect import relaunch_desktop
from devstudio.tools.device.frame import DeviceLink
from devstudio.tools.source.app_cmd import _check_dir, _locate

USR_APPS = {"setting", "libstudio"}


def upload_app(project: Path, board: str, app_id: str, *, relaunch: bool = True) -> tuple[str, int]:
    if app_id in USR_APPS:
        return "已连接。用户区应用不走 /mnt/install。未生成 usr.sqsh4，未烧录。本地源码未改。\n", 1
    located = _locate(project, app_id)
    if located is None:
        return "应用目录不存在，未上传。本地源码未改。\n", 1
    form, relative = located
    source = project / relative
    notes = _check_dir(source, app_id, form)
    if notes:
        lines = "\n".join(notes)
        return f"检查未通过，未上传。本地源码未改。\n{lines}\n", 1
    if not _safe_id(app_id):
        return "应用 id 不能作为设备路径，未上传。本地源码未改。\n", 1
    folder = _install_package(project, app_id) or source
    dest = f"/mnt/install/apps/{app_id}"
    try:
        payload = _pack(folder)
        with DeviceLink(board, timeout=30) as link:
            link.ping()
            link.file_put("/tmp/studio_push.tar.gz", payload, timeout=30)
            link.file_put("/tmp/nova_install.sh", _install_script(dest).encode("utf-8"), timeout=20)
            result = link.exec("/bin/sh /tmp/nova_install.sh 2>&1", timeout=60)
            extra = relaunch_desktop(link) if relaunch else ""
    except (OSError, TimeoutError, ConnectionError) as exc:
        return (
            f"上传失败：{exc}。未上传。本地源码未改。\n"
            "板上须运行 application/services/studio（TCP 7777）。看画面用 devstudio web。\n"
        ), 1
    output = str(result.get("out") or "")
    if not (result.get("ok") is True or result.get("exit") == 0) or "OK usb " not in output:
        detail = output or result.get("error") or "安装脚本没有成功"
        return f"设备安装失败：{detail}\n未上传。本地源码未改。\n", 1
    return (
        f"已上传到 {dest}，备份 /mnt/install/.app_bak/{app_id}。\n"
        "通道：Studio TCP 7777（设备端 studio）。看画面用 devstudio web。\n"
        "完成等级：L4。界面和日志须人确认后到 L5。\n"
        f"{output}{extra}",
        0,
    )


def _install_package(project: Path, app_id: str) -> Path | None:
    """Prefer built install/apps/<id> (has lib/*.so) over source tree."""
    out = project / "out"
    if not out.is_dir():
        return None
    matches = sorted(
        path
        for path in out.glob(f"*/burntool/install/apps/{app_id}")
        if path.is_dir() and (path / "app.json").is_file()
    )
    if not matches:
        matches = sorted(
            path
            for path in out.glob(f"*/**/install/apps/{app_id}")
            if path.is_dir() and (path / "app.json").is_file() and (path / "lib").is_dir()
        )
    return matches[0] if matches else None


def _safe_id(app_id: str) -> bool:
    return bool(app_id) and app_id[0].isalpha() and all(ch.islower() or ch.isdigit() or ch == "_" for ch in app_id)


def _pack(folder: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(item for item in folder.rglob("*") if item.is_file()):
            archive.add(path, arcname=path.relative_to(folder).as_posix())
    return buffer.getvalue()


def _install_script(dest: str) -> str:
    return f"""#!/bin/sh
DEST={dest}
echo "place=usb dest=$DEST"
if [ ! -d /mnt/install ]; then
  echo FAIL usb not mounted
  exit 1
fi
rm -rf /tmp/studio_pkg "$DEST.bak"
mkdir -p /tmp/studio_pkg /mnt/install/.app_bak
tar -xzf /tmp/studio_push.tar.gz -C /tmp/studio_pkg || {{ echo FAIL unpack; exit 1; }}
mkdir -p "$(dirname "$DEST")"
BAK=/mnt/install/.app_bak/$(basename "$DEST")
rm -rf "$BAK"
if [ -d "$DEST" ]; then mv "$DEST" "$BAK" || {{ echo FAIL backup; exit 1; }}; fi
if cp -R /tmp/studio_pkg "$DEST"; then
  rm -rf /tmp/studio_pkg
  chmod -R a+rX "$DEST" 2>/dev/null || true
  echo OK usb "$DEST"
  exit 0
fi
rm -rf "$DEST"
if [ -d "$BAK" ]; then mv "$BAK" "$DEST"; fi
echo FAIL copy
exit 1
"""
