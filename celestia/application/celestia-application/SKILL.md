---
name: celestia-application
description: >-
  Celestia 应用层（application/）代码约定与排障：主进程 app_main 启动序列、packages/{tablet,wearable,ipcamera,common}、
  tablet 应用入口/app.json/界面约定、IPCAMERA 单 SO（module_init/module_start）、cloud 适配、services、
  settings.json 与 installed_apps；只经 app_main_get_*() 和 *_open() 调 Framework，禁止直连 HAL/BSP。
  何时用：写或改应用层 C 代码、App 不上桌面/dlopen 失败/IPCAMERA 秒退、新增 cloud 或 IPCAMERA 模块；增删应用的流程先看 application-feature。
  When: write/change app-layer C, app missing from desktop/dlopen fail/IPCAMERA exit, add cloud or IPCAMERA module; for add/remove flow see application-feature first.
---

# Application 层 skill

> 概念：`docs/guide/application.md`；API 索引：`docs/guide/api-index.md`；角色工作表：`docs/quick-start/roles.md` §3。
> 总览与跨层规则见 [celestia](../../overview/celestia/SKILL.md)；增删应用的工具流程见 [application-feature](../application-feature/SKILL.md)。

## 目录地图

```
application/
├── app_main/          # 主进程 main.c / app_main.c / app_config.c；产物名 = CONFIG_APP_MAIN_TARGET
├── packages/
│   ├── common/        # 跨产品共用 GUI（如 gui_camera.c 预览）
│   ├── tablet/        # TABLET 桌面（nova / nova_dev）：launcher/ + 每应用一个目录
│   ├── wearable/      # WEARABLE（luna，240x240）：launcher/ camera/ dashcam/ power_keyd/
│   └── ipcamera/      # IPCAMERA 业务 SO：ipc_cam/（通用）、mike_cam/
├── cloud/             # aliyun / volc_engine / mike
├── services/          # studio（设备端 7777 代理）/ uvc / weather
└── tests/
```

主程序名按产品：nova、nova_dev → `nova_main`；luna → `luna_main`；luna_dev、luna_mi → `luna_dev_main`；mira_dev → `mira_main`。`config.mk` 没写时 `app_main/Makefile` 按 `anypi_<x>` 推成 `<x>_main`。Nova 打包时另装一份 `ipc_home` 别名，只为兼容旧镜像。

## 对下接口（只能这样调）

| 调用 | 头文件 | 用途 |
|------|--------|------|
| `app_main_get_multimedia/network/notification/nebula/runtime()` | `application/app_main/app_main.h` | 取 Framework 服务句柄（`void*` 需强转） |
| `*_open()` + sub-ops | `framework/services/*/include/*.h` | 调 Framework 能力 |
| `system_core.h` 及子头 | `system/core/include/*.h` | 日志 / 线程 / 配置 / 错误码 |

启动序列（`app_main/main.c`）：`app_main_platform_startup()` → `runtime_bootstrap_open()`（+`load_config`、`app_main_register_runtime`）→ `app_main_init()` → `app_main_load_config()` → `app_main_run()` → `app_main_start_framework_modules(headless, gui_config)` → `tablet_run()`（`CONFIG_APP_PACKAGES_TABLET`）或 `app_gui_run()`（`CONFIG_APP_PACKAGES_WEARABLE`）。两个 GUI 都关时，若没有阻塞的 `module_start`，`main` 在 framework 启动后返回。

## 禁止事项（必拒）

- `#include` 任何 `hardware/include/*.h`（`audio.h`、`camera.h`、`display.h`…）或 SoC 私有头（`hal_*_api.h`、`*_hw.h`）。
- 直接 `ioctl`、访问 `/dev/*`、调 BSP。
- tablet 应用里 `lv_obj_create(NULL)` / `lv_scr_load` 另起一屏，或用主程序没导出的 LVGL 控件（见 application-feature 的 `app check` 清单）。
- 需要硬件能力时走 Framework 服务；Framework 缺能力时下钻 [celestia-framework](../../framework/celestia-framework/SKILL.md)。

## tablet 应用约定

目录 `application/packages/tablet/<id>/`。最小参考：`calculator/`（上游已跟踪；本地 `app add` 生成、未提交的目录不要当样例）。

| 文件 | 谁写 | 要点 |
|---|---|---|
| `app.json` | 工具 | `app_id`、`name`、`action`、`entry_so`（如 `lib/lib<id>_app.so`）、`entry_symbol`=`app_entry_start_<id>`、`version`；图标用 `icon_kind` / `icon_glyph` / `icon_color` / `icon_color2`，由 `launcher/tablet_home.c` 画渐变圆角块。旧应用可能还有 `icon: res/icon.png` |
| `app_entry_<id>.c` | 工具 | 首行 SPDX；导出 `app_entry_start_<id>(lv_obj_t *main)`，`visibility("default")` + `used`，外加 keep-alive 静态指针防 `--gc-sections` 剪掉 |
| `tablet_<id>.h` | 工具 | guard 复用 `CONFIG_APP_TABLET_<ID>` |
| `tablet_<id>.c` 及其它 `.c/.h` | 模型 | 实现 `lv_result_t tablet_<id>_create(lv_obj_t *main)` |
| `app.mk`（可选） | 模型 | 应用专属编译 / 链接选项：`APP_CFLAGS_EXTRA`、`<ID>_SHLIB_LIBS`、`<ID>_APP_EXTRA_OBJS`、`<ID>_SRCS_FILTER_OUT`；参照 `setting/app.mk`、`ipc_camera/app.mk` |

**业务代码约定**（`launcher/tablet_common.h`、`tablet_font.h`）：

- 界面建在传入的 `main` 所在屏幕的 window 里，不另起一屏。
- 调 `tablet_create_top(parent, label, back_cb, desktop)` 得到顶部条 / 上滑退出；退出回调里恢复桌面并删除自己的 window。
- 文字字体用 `tablet_font_get_freetype_font(weight)`（粗体 `tablet_font_get_freetype_bold_font`）。
- 不写 weak 空实现充数。

**构建与发现**：`packages/tablet/Makefile` 不含任何应用信息。它扫描带 `app.json` 的目录，**默认全部启用**，只有产品 `config.mk` 写了 `CONFIG_APP_TABLET_<ID> = n` 才跳过。`.so` 名取 `entry_so` 的 basename，Makefile 用 `-include` 强制包含生成的 `config.h`。

**运行期登记**：`launcher/tablet_app_registry.c` 先读 `settings.json` 内嵌的 `installed_apps`（legacy 回退 `/etc/config/installed_apps.json`），再扫描 `APPS_BASE_DIR`（缺省 `/usr/apps`）和 `APPS_SD_BASE_DIR`（SD 安装区，nova 为 `/mnt/install/apps`），把带 `app.json` 但未登记的目录补进来。所以推到 `/mnt/install/apps/<id>` 的新应用一般不用改 `settings.json`；要固化进 rootfs 时才改板级 overlay `project/<p>/system/<SoC>/<SDK>/board/<Board>/rootfs/utils/etc/config/settings.json`。

**自检**：

```bash
grep -rE '#include\s+[<"](audio|camera|display|osd|gpu|isp|wifi|gpio|hardware)[./]' application/packages/tablet/<id>/   # 应为空
${CROSS}nm -D out/<p>/install/application/lib/lib<id>_app.so | grep app_entry_start_<id>   # AV200: riscv64-anyka-linux-musl-
devstudio app check <id>
```

## wearable 应用（luna）

`packages/wearable/<id>/`：`wearable_<id>_*.c/.h` + `app_entry_<id>.c` + `app.json`。入口同样要 `visibility("default")` + keep-alive；特性宏 `CONFIG_APP_WEARABLE_<ID>`；注册表 `launcher` 下的 `wearable_app_registry.c`，经 `app_gui_run()` 进入；`install_path=/usr/apps/<id>`。码流用量只改 `fw_video_config.json` 的 `app_stream_usage`（`docs/guide/framework.md` §2.5.1），不改 `channels/streams` 能力集。

## IPCAMERA 单应用（luna_dev / luna_mi / mira_dev）

装载契约在 `app_main/app_config.c::load_so_module()`：只 `dlsym` `module_init(const char *config_path)` 和 `module_start(void)`，**没有** `module_exit`。

| 项 | 做法 |
|---|---|
| 主程序 | `luna_dev_main`（mira 为 `mira_main`）；`CONFIG_APP_PACKAGES_IPCAMERA=y`，TABLET/WEARABLE=`n` |
| 目录 | `packages/ipcamera/<id>/`，Makefile 产出 `.so`（AOS 产 `.a`，见 celestia-aos-port） |
| 保活 | 在 `module_start` 内阻塞跑业务循环，否则主进程直接退出 |
| 注册 | `settings.json`：`launch_mode` 为 `ipcamera` 或 `headless`，唯一 `type:so` 模块，`so_path` 写运行期路径（如 `/usr/lib/libipc_cam_app.so`） |
| 参考 | `ipc_cam/`（通用）、`mike_cam/`；录像 / RTSP 可对照 `packages/wearable/dashcam/`（去掉 LVGL） |

## cloud 适配

现有两种形态，按其一仿制：`cloud/aliyun/`（静态库 `libaliyun_app.a`，导出 `aliyun_app_init/deinit`）、`cloud/volc_engine/`（`libvolc_engine_svc.so`，导出 `volc_engine_init/deinit`）；另有 `cloud/mike/`。

1. 目录 `cloud/<id>/`：`Makefile`、`include/<id>_app.h`、`<id>_app.c`（可选 `src/`、`lib/` 厂商 SDK）。
2. Makefile：`include $(COMPILATION_RULES)`、`include $(PROJECT_CONFIG_FILE)`、`PROJ = <name>`，安装到 `$(APPLICATION_INSTALL_DIR)/lib`。
3. 顶层接线 `application/Makefile`：`obj-$(CONFIG_APP_CLOUD_<ID>) += cloud/<id>`；宏后缀可与目录名不同（`volc_engine` → `CONFIG_APP_CLOUD_VOLC`）。
4. 走通用 SO 装载才需要导出 `module_init/module_start`；已发布的 aliyun/volc 链接进主进程，不走这条路径。
5. 可用 Framework `*_open()`、`system/core`、适配器内部厂商 SDK 头；禁止 HAL 头、禁止在 SO 里开 UI。

## 仿照现有代码（不要从零写）

| 目标 | 仿照路径 |
|---|---|
| 最小 tablet 壳 | `packages/tablet/calculator/`、`calendar/` |
| 预览 / 拍照 | `packages/tablet/camera/`、`packages/wearable/camera/`、`packages/common/` |
| 循环录像 + RTSP + 私有配置 | `packages/wearable/dashcam/` |
| 扫码 / AI 设置 | `packages/tablet/setting/tablet_setting_qr_scan.c`、`tablet_setting_ai.c` |
| 音频 / 播放 | `packages/tablet/audio_algo/`、`music_player/`、`recoder/` |
| 相册 / 游戏 / 模拟器 | `gallery/`、`snake/`、`emu/`（手柄蓝牙链接 `third_party/nimble`） |
| IPCAMERA 单 SO | `packages/ipcamera/ipc_cam/`、`mike_cam/` |
| UVC / 天气 | `services/uvc/`、`services/weather/`（`app_runtime` 里为 `type:static`） |

## 排障：以应用层为主

1. 先在 `application/` 内分析：启动流程、GUI 事件、应用登记、`settings.json` 解析、SO `dlopen/dlsym`。
2. 怀疑下层时只确认 Framework 接口契约：`framework/services/*/include/*.h` 的签名 / 返回值，本层 `*_open()` 的参数、时序、句柄是否为 NULL。
3. Framework 接口返回错误码或日志证明其失败，才切到 celestia-framework。
4. 改 `packages/common`、`app_main` 时评估 tablet / wearable / ipcamera 三种形态。

| 现象 | 根因 | 修复 |
|---|---|---|
| `dlopen` 失败 / 入口找不到 | `--gc-sections` 剪掉 entry | `visibility("default")` + keep-alive 自引用 |
| 应用不出现在桌面 | 目录没有 `app.json` / `install_path` 错 / 被 `CONFIG_APP_TABLET_<ID>=n` 关掉 | 核对目录与 `app.json`；查 `config.mk` |
| `app_runtime` SO 没加载 | `so_path` 写了源码路径 | 写运行期路径；SD 覆盖加载时 `/mnt/install/lib` 也在 `LD_LIBRARY_PATH` |
| cloud 的 `module_exit` 不执行 | 通用装载只认 `module_init/module_start` | 不依赖 `module_exit`，自管生命周期 |
| 码流改了不生效 | 改了 `channels/streams` 能力集 | 只改 `app_stream_usage` |
| 越层 include 报未知类型 | include 了 `hardware/include/*.h` | 改走 Framework `*_open()` |
| IPCAMERA 主进程启动即退出 | 无 GUI 时 `main` 返回 | `module_start` 阻塞保活 |

运行期 log 归类见 [celestia-debug](../../device/celestia-debug/SKILL.md) E 类。

## 编译（改完必做）

```bash
cd ~/celestia/develop
./build.sh -a --product-name anypi_nova        # TABLET / nova_main
./build.sh -a --product-name anypi_luna        # WEARABLE / luna_main
./build.sh -a --product-name anypi_luna_dev    # IPCAMERA / luna_dev_main
./build.sh -a --product-name anypi_mira_dev    # IPCAMERA AOS / mira_main
```

改了共用代码（`common/`、`app_main/`、`ipcamera/ipc_cam/`）时其它形态的产品也跑一次 `-a`。错误来自 Framework 头 / 符号时，先判断是本层调用用错还是需要下钻 framework。
