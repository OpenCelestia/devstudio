---
name: celestia-studio
description: >-
  DevStudio DeviceLink 设备通道（Studio TCP 7777，设备端 application/services/studio）：connect / push_app /
  device_firmware（usr 分区或整包分区）/ device_log / device_status 的用法、落点、relaunch、板号规则，以及
  不经 burntool 的手工推送命令。何时用：要把应用、usr.sqsh4 或镜像推到已启动的 Nova 板，推送失败、7777 连不上、要手工替换目录或写 APP 分区时。
  When: push app/usr.sqsh4/image to a running Nova board, push fail, 7777 unreachable, manual dir replace or APP partition write.
---

# 设备通道：经 Studio 7777 推送与取证

增删应用的完整流程见 [application-feature](../../application/application-feature/SKILL.md)，这里只讲**通道、落点和手工排障**。

## 通道

| 端 | 位置 | 作用 |
|----|------|------|
| 设备 | `application/services/studio`（`libstudio.so`） | 监听 TCP **7777**，处理 HELLO / PING / FILE_* / EXEC / SUB；必须在镜像里 |
| 主机命令 | DevStudio `DeviceLink` | 所有 `devstudio device …` 命令。推送和取证不必先打开页面 |
| 主机页面 | `devstudio/web` | `devstudio web` 打开 `http://localhost:8081`，看画面、指标、日志，也可在页面里编译并推送 |

推送与取证**只走网口 7777**。USB 线、串口都不能 push。应答里的 `OK usb` 指设备落点 `/mnt/install`（place=usb），不是电脑插的 USB 线。整片烧录、引导分区、空板仍需原厂 Burn Tool，不在 DevStudio 内。

## 工具对照（MCP 服务 `devstudio-device`）

| MCP 工具 | 等价 CLI | 说明 |
|---|---|---|
| `connect` | `devstudio device connect --board <IP>` | 探测并登记到 `devices.json`（`project://devices`）；显式给 `board` 时其它命令可直接用，不必先 connect |
| `push_app` | `devstudio device push <id> --board <IP>` | 打包应用目录 → FILE_PUT → 替换 `/mnt/install/apps/<id>` → 自动 relaunch |
| `device_firmware` | `devstudio device firmware --kind usr\|image …` | 不写引导分区；没有 `yes` 只出计划 |
| `device_log` / `device_status` | `devstudio device log\|status --board <IP> [--seconds N]` | 抓 LOG 帧 / CPU 内存 |
| `capture` / `metrics` | `devstudio device capture\|metrics` | 订阅 METRICS+LOG 存到任务目录 |
| `device_check` / `device_analyze` / `device_rtsp` | 同名 CLI | 排障用，见 celestia-debug |

## 板号规则

1. 对话里已经出现 IP → 直接用，不再问。
2. 没出现 → 经 MCP 调用且未给 `board` 时服务端会发 `elicitation/create` 聊天内表单；CLI 输出 `NEED_BOARD ASK_UI=board_ip` 时用 AskQuestion 收集，再按 `RETRY:` 行重调。
3. 禁止臆造 IP、静默挑选已登记的板、在聊天里口头索要后干等、设 `DEVSTUDIO_UI=desktop`。

## 落点：两类应用

| 应用 | 落点 | 方式 |
|------|------|------|
| 一般 tablet 应用（calculator、clock…） | `/mnt/install/apps/<id>`（SD） | `push_app`，不烧分区 |
| `setting`、`libstudio.so` 等进 `/usr` 的内容 | 只读 squashfs `/usr`（分区名 `APP`） | 重打 `usr.sqsh4` → `device_firmware kind=usr file=usr.sqsh4` → 重启 |

`/mnt/install` 必须已挂载且可写。存储处于 U 盘模式（SD 导出给电脑）时设备上没有这个目录，推送会报 `FAIL usb not mounted`。

push 成功后自动 relaunch（`connect.py::relaunch_desktop`），桌面会断开再连上并扫描到新目录：

```sh
(sleep 1; killall nova_main ipc_main ipc_home; sleep 1; /usr/sbin/start_ipc_main.sh --bg) >/tmp/devstudio_relaunch.log 2>&1 &
```

## 固件分区（不经 burntool）

- `kind=usr`：在设备上 `grep '"APP"' /proc/mtd` 取分区，`mtd_debug erase` + `mtd_debug write`。镜像大于分区就拒绝。上限以 `/proc/mtd` 为准（fastsys 实机 APP 为 `0x8AC000`），不以 burntool 文本为准。
- `kind=image dir=<镜像目录> [config=<名>] [parts=APP,KERNEL,ROOTFS] [yes=true]`：分区表从产品 `tools/burntool/burn.ini` 指向的 config 读，只当数据用；设备 `/proc/mtd` 决定分区名和长度。`SPL`、`OPENSBI`、`ENV`、`ENVBK`、`DTB`、`UBOOT`、`BOOT` 在运行中的系统里一律不写。
- 先不带 `yes` 看计划，用 AskQuestion 让用户确认后再带 `yes=true`。

## 手工排障命令（工具失败时对照）

`push_app` 在设备上执行的等价脚本（包先传到 `/tmp/studio_push.tar.gz`）：

```sh
DEST=/mnt/install/apps/<id>
mkdir -p /tmp/studio_pkg /mnt/install/.app_bak
tar -xzf /tmp/studio_push.tar.gz -C /tmp/studio_pkg
BAK=/mnt/install/.app_bak/<id>
rm -rf "$BAK"; [ -d "$DEST" ] && mv "$DEST" "$BAK"
cp -R /tmp/studio_pkg "$DEST" && chmod -R a+rX "$DEST"
```

- 备份必须放 `/mnt/install/.app_bak/<id>`，不要放 `apps/<id>.bak`，否则桌面会多扫到一个应用。
- 用户区工具是 `/bin/mtd_debug`，不是 `flashcp`；不要对正在映射的 `/usr/lib/*.so` 做截断覆盖。

| 现象 | 原因 | 处理 |
|---|---|---|
| connect 超时 | 板没起 studio / IP 错 / 不同网段 | 串口看 `libstudio.so` 是否加载；`ping` 板子；确认镜像含 `services/studio` |
| `FAIL usb not mounted` | `/mnt/install` 未挂载或处于 U 盘模式 | 退出 U 盘模式 / 插好 SD 后重试 |
| 推送成功但桌面没有 | relaunch 失败或 `app.json` 有问题 | 看设备 `/tmp/devstudio_relaunch.log`、`/tmp/nova_main.log`；`devstudio app check <id>` |
| 改了 `setting` 推送无效 | 它在只读 `/usr` | 走 `device_firmware kind=usr` |

编译在 WSL / Linux 内跑（`./build.sh -a --product-name anypi_nova`），不要在 Windows 盘符下直接跑 `build.sh`；IDE 内优先用 MCP `build`。
