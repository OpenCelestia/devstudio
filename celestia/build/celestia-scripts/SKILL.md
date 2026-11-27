---
name: celestia-scripts
description: >-
  Celestia 构建入口 build.sh 与 scripts/：分层 flag（-s/-t/-h/-f/-a/-k/-p/-c）、--product-name、--build-module、
  --jobs、--release、--copy-build-src，依赖顺序与 clean 语义，DevStudio build / build_log 用法，最小源码集释放，
  构建类常见错误。何时用：要编译、不知道用哪个 flag、编译报错或改了不生效（stale）、改 build.sh / release_src.sh 时。
  When: need to build, unsure which flag, build errors or stale changes, editing build.sh / release_src.sh.
---

# Scripts / 构建 skill

> 文档：`docs/quick-start/build.md`、`docs/quick-start/bsp-and-products.md` §4。总览见 [celestia](../../overview/celestia/SKILL.md)。
> 推到板上见 [celestia-studio](../../device/celestia-studio/SKILL.md)；上板验证见 [celestia-debug](../../device/celestia-debug/SKILL.md)。

## 在哪跑

- 编译与打包只在 **Linux 构建环境**（WSL 或原生 Linux）里跑，工程路径 `~/celestia/develop`（Windows 侧看到的是 `Z:\home\lian\celestia\develop`）。**不要在 PowerShell 里对 `Z:\...` 直接执行 `build.sh`。**
- IDE 里优先用 MCP `devstudio-build`：

| MCP 工具 | 等价 CLI | 说明 |
|---|---|---|
| `build` | `devstudio build --run [--host local\|wsl\|ssh] [--command …] [--timeout 秒]` | 缺省执行 `devstudio.yaml` 的 `build.command`（当前 `./build.sh --product-name anypi_nova`，host `wsl`）；要编单层时传 `command` |
| `build_log` | `devstudio build --log <file> [--exit N] [--module 名]` | 把日志分成源码错误 / 环境错误 / 构建系统错误 / 通过；源码错误回到对应层 skill，环境错误重试，构建系统错误交人工 |

编译通过不等于功能生效，有运行期表现的必须上板。

## build.sh 选项

仓库根 `build.sh` 指向 `scripts/build.sh`。**没有层 flag = 全量构建 + package。**

| flag | 作用 |
|------|------|
| `-s` / `--system-core` | system/core |
| `-t` / `--third-party [module]` | third_party，可只编一个库（如 `-t lvgl`、`-t nimble`） |
| `-h` / `--hardware` | hardware HAL |
| `-f` / `--framework` | framework |
| `-a` / `--application` | application |
| `-k` / `--kernel` | SoC platform / kernel（`system/<SoC>/<SDK>` + project overlay） |
| `-p` / `--package` | rootfs / 镜像打包 |
| `-c` / `--clean` | 单独用：`rm -rf out/<product>` 整个删掉；带层 flag：只清这些层再编（如 `-c -f -a`） |
| `--product-name <p>` | `project/` 下的产品；不给时交互选择并缓存到 `out/.build_product_name`，非交互默认 `anypi_nova` |
| `-d` / `--devstudio` | 必须同时带 `--product-name`。只强制重写该产品的 `AGENTS.md`，不编译 |
| `--build-module <NAME>` | 只编 `config.mk` 里的某个模块，名字去掉 `CONFIG_` 前缀，如 `FW_MULTIMEDIA` |
| `--jobs N` | 并行数，默认 1；**不要用裸 `-j`**（U-Boot 固定 `-j1`） |
| `--log-level DEBUG\|INFO\|ERROR`、`--build-level DEBUG\|RELEASE` | 日志与构建级别，默认 INFO / DEBUG |
| `--release [path]` | 把 install 产物拷到 release 目录（默认 `out/<p>/release`） |
| `--copy-build-src` | 只拷最小源码集，不编译（等价 `scripts/release_src.sh`） |

构建依赖顺序：`third_party → system/core → hardware → framework → application → package`。`build_framework` / `build_system_core` 会自检 `liblvgl.a`、`cJSON.h`，缺失时自动先建 third_party。多个层 flag 会按依赖顺序执行。工具链由 SoC 决定：AV200 `riscv64-anyka-linux-musl-`，AV100_V2 `arm-anycloud-linux-uclibcgnueabi-`，`CONFIG_OS_NAME=aos` 时自动 source `project/<p>/setup_toolchain.sh`。

```bash
cd ~/celestia/develop
./build.sh --product-name anypi_nova                 # 全量 + package
./build.sh -f -a --jobs 4 --product-name anypi_luna  # 只编 framework + application
./build.sh --build-module FW_MULTIMEDIA --product-name anypi_nova
./build.sh -c -h --product-name anypi_luna           # 清 hardware 层后重编
./build.sh --help                                    # 选项以 --help 为准
```

## 改了哪层编哪层

| 改动位置 | 最少要跑 |
|---|---|
| `system/core` 公开头 | `-s -h -f -a`（三层依赖方都要编过） |
| `hardware/` | `-h`；改了公开头加 `-f -a`，且 AV200、AV100_V2 各编一个产品 |
| `framework/` | `-f`；改了对外头加 `-a` |
| `application/` | `-a`；改共用代码时其它形态产品也编 |
| `third_party/` | `-t <lib>`，再编依赖它的上层 |
| `project/<p>/config.mk`、overlay、DTS | 受影响层 + `-k` / `-p`，或直接全量 |

## 最小源码集（对外释放）

`scripts/release_src.sh` 按产品 `config.mk` 的 `CONFIG_*` 只拷启用模块到 `out/<product>/release-src/`，剔除未启用模块、其它 SoC 的 `hal/`、其它产品目录、`.git` 和构建产物。

```bash
bash scripts/release_src.sh --product-name anypi_luna
bash scripts/release_src.sh --product-name anypi_luna --with-vendor-bsp   # 连 system/<SoC>/<SDK>/
bash scripts/release_src.sh --product-name anypi_luna,anypi_nova --dest out/release-open
bash scripts/release_src.sh --product-name anypi_nova --with-cloud        # 含 application/cloud 厂商 SDK
```

`scripts/` 现有：`build.sh`、`release_src.sh`、`sync_to_release.sh`、`compare_nfs_ref.sh`。PC 页面在 `devstudio/web`，用 `devstudio web` 打开。

`./build.sh` 在解析出产品名之后会调用 `devstudio init --sync-product <产品>`。第一次生成 `devstudio.yaml` 和 `AGENTS.md`。之后只有产品名变了才重写 `AGENTS.md`。`./build.sh -d --product-name <产品>` 只强制重写 `AGENTS.md`，然后退出，不编译。没写 `--product-name` 时 `-d` 直接退出。

## 常见问题（构建）

| 现象 / log | 根因 | 修复 |
|---|---|---|
| 改源板上不变 | stale `out/` / 产品选错 | 确认 `--product-name`；`-c -<层>` 再编；比 `.o` 时间戳确认（见 celestia-debug F 类） |
| 在 Windows 路径跑 build.sh | 不是 Linux 构建环境 | 到 WSL `~/celestia/develop` 再编，或用 MCP `build` |
| 链接缺 lvgl / cjson / nimble | third_party 未建或开关关着 | `-t <lib>`；核对 `CONFIG_THIRD_PARTY_*` |
| 只编一层仍链到旧 `.o` | 依赖层未清 | `-c -h -f -a` 按依赖清编 |
| `fixdep: error opening depfile`（内核） | 中断留下的内核脏增量 | `-c -k` 重建 |
| 并行编译随机失败 | 内核 / U-Boot / OpenSBI 竞争 | 原样重编；`--jobs` 调小 |
| package 缺文件 | overlay / 开关没装进 rootfs | 核对 `config.mk` 与 board rootfs |
| 烧录 APP `Page more than end page` | `usr.sqsh4` > APP 分区 | `-p` 后 `stat out/<p>/burntool/usr.sqsh4`，Nova 上限见 [celestia-project](../../project/celestia-project/SKILL.md) 分区一节 |
| `.sh` `Permission denied` | 提交时丢了可执行位 | 见 [git-gitee](../../repo/git-gitee/SKILL.md)「文件权限」 |

运行期归类见 [celestia-debug](../../device/celestia-debug/SKILL.md) D / F 类。

## 改构建脚本时

1. 保持层 flag 语义和依赖顺序不变。
2. 改完跑 `./build.sh --help` 自检，再实际跑一次受影响层（如 `-a --product-name anypi_luna`），0 error 后交付。
3. 编译失败先看 build.sh 输出定位是哪一层哪条命令：源码错误回对应层 skill，脚本 / 路径问题在本层改。
