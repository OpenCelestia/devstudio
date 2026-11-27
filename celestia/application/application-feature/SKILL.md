---
name: application-feature
description: >-
  sdk_celestia 下新增、修改、删除应用的唯一流程入口：devstudio app add|modify|remove|check|recipe
  → 计划 → IDE 运行中 Ask 批准（NEED_* ASK_UI）→ apply 写模板 → 模型只写业务 .c → app check → build →
  push_app；含系统应用保护与删除顺序。何时用：用户说「加一个/做一个 xx 应用」「改 xx 应用」「删掉 xx 应用」，或要把新应用推到板上。
  When: user asks to add/make/modify/delete an app, or to push a new app to the board.
---

# application-feature

新增、修改、删除应用都从这里开始。代码怎么写（入口、`app.json`、禁用头）见 [celestia-application](../celestia-application/SKILL.md)；推送通道与手工排障见 [celestia-studio](../../device/celestia-studio/SKILL.md)。

## 工具对照

| 步骤 | 命令 | MCP |
|---|---|---|
| 看可组合的形态模板 | `devstudio app recipe [--form TABLET\|WEARABLE\|IPCAMERA\|CLOUD]` | 无（CLI） |
| 新增 | `devstudio app add <id> --name-zh <中文> --name-en <英文> --need <需求> [--form cloud] [--yes] [--build]` | 无（CLI） |
| 修改 / 删除 | `devstudio app modify\|remove <id> [--board <IP>] [--yes]` | 无（CLI） |
| 自检 | `devstudio app check <id>` | 无（CLI） |
| 编译 | `devstudio build --run` | `devstudio-build` → `build` |
| 推送 | `devstudio device push <id> --board <IP>` | `devstudio-device` → `push_app` |
| 计划状态 | `devstudio plan show <task_id>` | `devstudio-source` → `plan_status` |

## 批准、板号与选择（所有问答）

工具输出 `NEED_* ASK_UI=…` 块（含 `TITLE` / `PROMPT` / `OPTIONS` / `FIELDS` / `RETRY`）时：

1. **立刻**用 IDE 运行中控件展示：Cursor 用 AskQuestion；MCP 缺板号时服务端发 `elicitation/create` 聊天内表单；VS Code/Copilot 用聊天内选项。
2. 用户选择后，**按 `RETRY:` 行**带上 `--yes` / `--board <IP>` 重调同一命令。
3. 禁止桌面 tkinter、禁止 `DEVSTUDIO_UI=desktop`、禁止在聊天里写「请回复批准 t00xx」然后干等。
4. 对话里已经给过 IP 就直接用，不要再问；没给过才问，不要臆造或自己挑板。

## 新增

1. **定 id**：中文名先译成英文 id（小写字母开头，只含 `a-z0-9_`，≤41 字符）。系统应用名不能用（见下）。目录已存在时改走「修改」。
2. `devstudio app add <id> --name-zh … --name-en … --need "<一句话需求>"`：工具按 `config.mk` 的 `CONFIG_APP_PACKAGES_*` 判断形态（`--form cloud` 可强制 CLOUD），写计划并输出批准 ASK_UI。
3. 批准后工具写模板（TABLET）：`app.json`、`app_entry_<id>.c`、`tablet_<id>.h`，并写 `CONFIG_APP_TABLET_<ID> = y`。tablet Makefile 默认启用所有带 `app.json` 的目录，所以不需要改 Makefile。
4. **模型只写业务代码**：`tablet_<id>.c`（及同目录内其它 `.c/.h`），必须实现 `lv_result_t tablet_<id>_create(lv_obj_t *main)`。界面约定见 celestia-application「tablet 业务代码约定」。不要改工具生成的登记文件。
5. `devstudio app check <id>` 必须输出「检查通过」。它检查：`app.json` 的 `app_id` / `entry_symbol`、入口导出 `app_entry_start_<id>`、`tablet_<id>_create` 签名、没有 `lv_obj_create(NULL)` / `lv_scr_load`、没有 include hardware/camera/display/gpio/wifi、没有用主程序未导出的 LVGL 控件（`lv_canvas`、`lv_imagebutton`、`lv_animimg`、`lv_gif`、`lv_qrcode` 等）。
6. 编译：MCP `build`（或 `devstudio build --run`）。源码错误交回模型修，最多 4 轮；内核 / U-Boot / OpenSBI 的并行竞争失败只重编。失败日志用 `build_log` 分类。
7. 推送：`push_app app_id=<id>`。设备落点 `/mnt/install/apps/<id>`，旧版移到 `/mnt/install/.app_bak/<id>`，然后自动 relaunch 桌面。
8. 验收：桌面出现图标、能进入、上滑能退回桌面；`device_log` 里没有 `dlopen` / `app entry not found`。

## 修改

1. `devstudio app modify <id>` → ASK_UI 批准 → 只检查模板，不推设备。
2. 只改 `application/packages/tablet/<id>/` 里已有文件；不新建目录，不改 `app.json` 的 `app_id`。
3. `app check` → `build` → `push_app`（覆盖原 `/mnt/install/apps/<id>`）。

## 删除（顺序不能反）

1. `devstudio app remove <id>`：没给 `--board` 时先输出 `NEED_BOARD ASK_UI=board_ip`，立刻用运行中输入收集 IP。
2. 再输出 `NEED_CONFIRM` 批准；用户选「是」后按 RETRY 执行 `devstudio app remove <id> --board <IP> --yes`。
3. 工具**先**经 7777 删设备 `/mnt/install/apps/<id>` 并 relaunch；设备侧成功后才关宏、去 `settings.json` 登记、删本地 `.devstudio-managed` 目录。
4. 没拿到板号或设备侧失败时工具返回非 0，本地源码保留。**禁止 Agent 绕过工具直接删目录。**
5. 「天气」：本地没有平板目录时仍按设备 `weather` 清理；不删 `service_weather`。

## 系统应用（不能新增同名、不能删除）

`ipc_camera`、`camera`、`audio_algo`、`setting`、`volc_engine`、`studio`（`app_cmd.py` 的 `SYSTEM_APPS`）。

`setting` 和 `libstudio.so` 在只读 `/usr` 里，改了要重打 `usr.sqsh4` 用 `device_firmware kind=usr` 写分区，不能用 `push_app`。详见 celestia-studio。
