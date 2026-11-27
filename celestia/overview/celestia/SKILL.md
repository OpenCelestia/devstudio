---
name: celestia
description: >-
  Celestia 分层工程总览与路由：5 层架构与允许/禁止的跨层方向、「本层为主、下层只确认接口契约、
  有证据再下钻」排障原则、build.sh 分层编译闭环、6 个产品对照，以及按场景选哪个 skill / 哪个
  DevStudio 工具。何时用：不确定问题落在哪一层、跨层改动、要选 skill，或拿到需求/报错还不知从哪下手时先读。
  When: unsure which layer, cross-layer change, picking a skill, or starting from a vague requirement/error.
---

# Celestia 总览（路由 skill）

本 skill 只做三件事：**定位层级 → 确认跨层接口 → 路由到分层 skill**。各层实现细节、目录与编译命令在分层 skill 里。
层与目录对照见 `devstudio/celestia/README.md`；架构依据 `docs/architecture/overview.md`（工程根下的 `docs/`）。

## 按场景路由（先查这张表）

| 我要做的事 / 看到的现象 | 读哪个 skill | 首选 DevStudio 工具 |
|---|---|---|
| 新增 / 修改 / 删除 tablet 应用 | [application-feature](../../application/application-feature/SKILL.md)（流程）+ [celestia-application](../../application/celestia-application/SKILL.md)（代码约定） | `devstudio app add\|modify\|remove\|check`（仅 CLI） |
| 推应用 / 写 usr 分区 / 整包分区到已启动的板 | [celestia-studio](../../device/celestia-studio/SKILL.md) | MCP `devstudio-device`：`push_app`、`device_firmware` |
| 贴了 log、崩溃、不出帧、上板不对 | [celestia-debug](../../device/celestia-debug/SKILL.md) | `device_analyze kind=log`、`device_check` |
| 编译报错 / 不知道用哪个 build.sh flag | [celestia-scripts](../../build/celestia-scripts/SKILL.md) | MCP `devstudio-build`：`build`、`build_log` |
| 多媒体 / 网络 / 存储 / 通知 / 协议 / runtime | [celestia-framework](../../framework/celestia-framework/SKILL.md) | `doc_search`、`api_lookup` |
| 驱动、sensor、显示、音频、ISP、新 SoC 的 HAL | [celestia-hardware](../../hardware/celestia-hardware/SKILL.md) | `api_lookup`（SDK 符号） |
| 日志、线程、INI 配置、定时器、错误码 | [celestia-system-core](../../system/celestia-system-core/SKILL.md) | — |
| config.mk、板型、DTS、rootfs、启动脚本、分区表 | [celestia-project](../../project/celestia-project/SKILL.md) | `device_check` |
| Linux 产品移植到 AOS（RTOS） | [celestia-aos-port](../../system/celestia-aos-port/SKILL.md) | — |
| 第三方库、FFmpeg 体积、链接缺 lvgl/cjson | [celestia-third-party](../../build/celestia-third-party/SKILL.md) | `build_log` |
| 命名、文件头、错误码规范、写 docs/README（跟用户中/英文） | [celestia-contributing](../../repo/celestia-contributing/SKILL.md) | — |
| 提交、推送、准备提交、code review、多仓同步（对话跟用户语言） | [git-gitee](../../repo/git-gitee/SKILL.md) | — |
| 首次接入工程、引导文件缺失 | [project-bootstrap](../../project/project-bootstrap/SKILL.md) | `devstudio init`、`project_info` |
| Camera Sensor / Flash 适配 | `devstudio/sdk/peripherals/{cis,flash}` 下的 skill | `peripheral_list`、`plan_sensor` |

一个任务跨多层时，按「调用发起方」所在层开始读，需要下钻时再切下一层 skill。

## 调用关系（允许的方向）

```
Application ──app_main_get_*() / *_open()──► Framework
     │                                           │
     └──#include system_core.h──► System Core ◄──┤   (被三层横向依赖)
                                       ▲          │
Hardware HAL ──#include system_core.h──┘          │
     ▲                                            │
     └──────────hardware/include/*.h◄─────────────┘
     │
     └──/dev/* ioctl / sysfs──► Kernel/BSP（project/、system/<SoC>/）
```

构建依赖顺序（与 `build.sh` 一致）：`third_party → system/core → hardware → framework → application → package`。

## 禁止的方向（必拒）

| 禁止 | 原因 |
|------|------|
| Application → HAL / Kernel/BSP | 必须经 Framework；App 不得 include `hardware/include/*.h` 或 SoC 私有头 |
| Framework → `hardware/*/hal/<SoC>/` | Framework 只能 include `hardware/include/*.h` 公开头 |
| System Core → Framework / Hardware | System Core 是叶子层，不上向依赖 |
| 任意层 include `hal_*_api.h` / `hardware/` 下非 `include/` 的头 | 跨层只走对外公开头 |
| 给同一模块新增第二个公开头 | 每模块只允许一个对外头，新声明加到已有头或内部头 |

## 排障原则：以本层为主，对下层只确认接口

1. **定位层级**：按上面的场景表或调用栈，确定主排查层。
2. **主攻本层**：先假设下层正确，集中分析本层逻辑。
3. **对被调层只确认对外接口**：看其 `include/*.h` 签名、返回值、错误码，看 `tests/`，核对本层的参数、时序、返回值处理。
4. **有证据才下钻**：下层返回错误码 / 日志 / tests 表明契约不符、本层调用确认正确仍失败，才切换到下层 skill，重复 2–4。
5. **改完按层编译**，有运行期表现的再上板复测（见 celestia-debug）。

> 一句话：本层逻辑 → 下层接口契约 →（有据再）下层实现。

## 三条标准流程

**新增 tablet 应用到桌面可见**：`devstudio app add` → AskQuestion 批准 → apply → 模型写 `tablet_<id>.c` → `app check` → `build` → `push_app` → 桌面看到图标、能进能退、`device_log` 无 `dlopen` 失败。细节见 application-feature。

**Bug 排障闭环**：收集产品名 + 复现步骤 + log → `device_analyze kind=log` 归 A–G 类 → 套已知解法或进对应层 skill → 对应层编译 → 推送 / 烧录 → 按 PASS/FAIL 判据复测。细节见 celestia-debug。

**新芯片 / 新产品移植**：新 SoC 走 celestia-hardware「新增 SoC」+ celestia-project「新增产品 / Board」；`system/core` 通常不动（新增 HAL 模块号除外）；SDK 只提供 AOS 时改走 celestia-aos-port。

## 产品对照（以 `project/<p>/config.mk` 为准）

| 产品 | SoC / SDK | OS | 主程序 `CONFIG_APP_MAIN_TARGET` | 形态 | Board |
|------|-----------|----|------|------|------|
| anypi_nova | AnyCloud39AV200 / V1.04 | linux | `nova_main` | TABLET | PDB_MBD_AK3918AV200_ACTCAM |
| anypi_nova_dev | AnyCloud39AV200 / V1.04 | linux | `nova_main` | TABLET | EVB_MBD_AK3918AV200 |
| anypi_luna | AnyCloud39AV100_V2 / V2.05 | linux | `luna_main` | WEARABLE | PDB_MBD_KM01W_ACTCAM |
| anypi_luna_dev | AnyCloud39AV100_V2 / V2.04 | linux | `luna_dev_main` | IPCAMERA | EVB_MBD_KM01A |
| anypi_luna_mi | AnyCloud39AV100_V2 / V2.05 | linux | `luna_dev_main` | IPCAMERA | EVB_MBD_KM01A |
| anypi_mira_dev | Sky39AV130 / V1.01 | aos | `mira_main` | IPCAMERA | EVB_CBD_AK3918AV130 |

- Nova 的 `ipc_main` / `ipc_home` 只是旧镜像兼容名：`start_ipc_main.sh` 优先找 `nova_main`，`application/Makefile` 另装一份 `ipc_home` 别名。新代码和文档一律写 `nova_main`。
- GUI 目录：`application/packages/tablet`（nova / nova_dev 共用）、`packages/wearable`（luna）；IPCAMERA 形态用 `packages/ipcamera`（`ipc_cam`、`mike_cam`）。
- 工具链按 SoC 选：AV200 `riscv64-anyka-linux-musl-`；AV100_V2 `arm-anycloud-linux-uclibcgnueabi-`；Sky39AV130 由 `project/anypi_mira_dev/setup_toolchain.sh` 提供 `arm-none-eabi-`。

## 分层编译（改完代码必做）

```bash
cd ~/celestia/develop            # Linux / WSL 内；不要在 Windows 盘符下跑 build.sh
./build.sh -s --product-name <p>    # system/core
./build.sh -t [module] --product-name <p>   # third_party
./build.sh -h --product-name <p>    # hardware
./build.sh -f --product-name <p>    # framework
./build.sh -a --product-name <p>    # application
./build.sh -k --product-name <p>    # kernel / BSP
./build.sh    --product-name <p>    # 全量 + package
```

跨多层时按依赖顺序从下往上一起编（如 `-h -f -a`）。在 IDE 里优先用 MCP `devstudio-build` 的 `build`（命令取自 `devstudio.yaml`），失败日志交 `build_log` 分类。

## 产品隔离

- `project/<p>/config.mk` → 生成 `config.h`，是唯一合法的跨产品差异点。
- 源码里不用 `#ifdef CONFIG_SOC_*` 做产品分支；差异走 HAL ops 或模块开关。
- 改一个产品的配置只编该产品。工程规则禁止 Agent 改 Makefile、Kconfig、config.mk 的模块开关；需要改时先说明原因并经 AskQuestion 批准。

## 检索与事实来源

- 历史问题、文档、SDK 符号只用 `devstudio-knowledge`（`doc_search`、`api_lookup`、`errata_lookup`）；没有结果就明说未收录。
- 事实优先级：`tests/` > `*.c` > `*.h` > `Makefile` / `build.sh` / `config.mk` > `docs/` > 本 skill。发现 skill 或文档过期时在交付说明里指出。
