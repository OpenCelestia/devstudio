# Celestia

这一目录按 Celestia 产品分层放 Skill。SDK 版本差异不写在这里，见 `../sdk/README.md`。

| 目录 | 层 | 工程目录 | Skill |
|---|---|---|---|
| `overview/` | 总览与路由 | 工程根 | `celestia` |
| `application/` | 应用 | `application/` | `celestia-application`、`application-feature` |
| `framework/` | 框架 | `framework/` | `celestia-framework` |
| `hardware/` | 硬件与 HAL | `hardware/` | `celestia-hardware`、`hal-adapt` |
| `system/` | 系统与 AOS | `system/core/`、AOS 产品 | `celestia-system-core`、`celestia-aos-port` |
| `project/` | 产品 | `project/` | `celestia-project`、`project-bootstrap` |
| `build/` | 编译脚本与第三方 | `scripts/`、`build.sh`、`third_party/` | `celestia-scripts`、`celestia-third-party` |
| `device/` | 设备代理、固件、日志、状态 | `application/services/studio` | `celestia-studio`、`celestia-debug` |
| `repo/` | 仓库 | Git | `celestia-contributing`、`git-gitee` |

`sdk_celestia` 使用全部层。`sdk_customer_app` 只用 `hardware/hal-adapt`。`sdk_only` 不用本目录。

## 写 Skill 的约定

- frontmatter 只有 `name` 和 `description`。`description` 先写「做什么」，再写双触发：`何时用：…`（中文）与 `When: …`（英文，必填）。AGENTS 写入 `EN | ZH`（双语合计 ≤100 字）；Agent 按用户最新一句语言选中/英触发与回复。`devstudio init` 写进根目录 `AGENTS.md` 为 `id | when`（`id` 如 `celestia/project/project-bootstrap`，打开 `devstudio/<id>/SKILL.md`）；无标记时取 description 第一句。
- 正文开头给「工具对照」：MCP 工具名（`devstudio-<组>` → 工具）和等价 CLI。增删应用只有 CLI（`devstudio app …`）。
- 事实以代码为准：产品、主程序、开关读 `project/<p>/config.mk`；分区读 burn / env / DTS 原文件；头文件个数用 `ls`。写死的数值旁注明「以文件为准」。
- 文档引用写工程根相对路径（如 `docs/quick-start/roles.md`），不要写相对 skill 文件的 `../quick-start/…` 链接；skill 之间互链用 `../../<层>/<name>/SKILL.md`。
- 一个流程只在一个 skill 里写全，其它 skill 只链过去（应用增删改 → `application-feature`，推送通道 → `celestia-studio`，log 归类 → `celestia-debug`）。

## 应用流程

| 操作 | 命令 | 设备上 |
|---|---|---|
| 增加 | `app add` → 批准 → apply 写模板 → 模型写 `tablet_<id>.c` → `app check` → `build` → `device push <id> --board` | 放到 `/mnt/install/apps/<id>`，旧版备份到 `/mnt/install/.app_bak/<id>` |
| 修改 | `app modify` → 批准 → `app check` → `build` → `device push <id> --board` | 同上，apply 本身不推设备 |
| 删除 | `app remove <id> --board` | 删除应用目录并从 `settings.json` 去掉；系统应用不删 |

## 设备流程

全部需要 `--board`，帧只经 Studio TCP 7777。不使用 burntool。

| 情况 | 命令 |
|---|---|
| 单个应用目录 | `device push <id> --board` |
| 用户区 `usr.sqsh4` | `device firmware --board --kind usr --file usr.sqsh4` |
| 整包分区 | `device firmware --board --kind image --dir 镜像目录 [--config 名] [--parts APP,KERNEL,ROOTFS] [--yes]` |
| 日志 | `device log --board [--seconds N]` |
| CPU、内存、负载、温度 | `device status --board [--seconds N]` |

整包分区表从产品 `tools/burntool/burn.ini` 指向的 config 读取，只当数据用。设备 `/proc/mtd` 决定分区名和长度。SPL、OPENSBI、ENV、DTB 等引导分区不在运行中的系统里写。没有 `--yes` 只出计划。引导分区和空板仍需原厂烧录工具，不在 DevStudio 内。
