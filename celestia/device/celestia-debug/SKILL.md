---
name: celestia-debug
description: >-
  Celestia 上板取证、Bug 分类与闭环验证：device_log / device_analyze / device_check 取证，按 A 崩溃、B 预览编码、
  C 内存DMA、D 启动驱动分区、E App/SO、F 构建stale、G 出流网络 七类给出典型 log、根因、解法与该去的层 skill，
  PASS/FAIL 判据与 LUNA 64MB 内存模型。何时用：用户贴 log 或报错、signal=11 崩溃、不出帧、OOM、启动失败、
  烧录 Page more than end page、改了不生效，或改完要上板验证时。
  When: user pastes logs/errors, signal=11, no frames, OOM, boot fail, burn Page more than end page, changes not applied, or need on-board verify.
---

# Celestia 上板调试、闭环验证与 Bug 分类 skill

> 定位层级见 [celestia](../../overview/celestia/SKILL.md)；编译见 [celestia-scripts](../../build/celestia-scripts/SKILL.md)。
> 闭环：build → 推送 / 烧录 → 抓 log → 按下表**归类** → 套用解法 / 下钻层 skill → 复测 PASS。
> 角色工作表：`docs/quick-start/roles.md` §5（调试与维护）。

## 核心原则

1. 编译通过 ≠ 功能生效；有运行期表现必须上板看 log。
2. **先归类再改代码**：用下方「log 关键词」命中 Bug 类，再进对应解法表。
3. 以本层为主；证据（返回码/log/tests）明确才下钻下层 skill。
4. 改完重跑闭环，直到 PASS/FAIL 判据满足。

串口：Luna 默认 `115200` 8N1（`picocom -b 115200 /dev/ttyUSB0`）。

## DevStudio 取证（先用工具，再人工看）

板号规则同 [celestia-studio](../celestia-studio/SKILL.md)：对话已给 IP 就用，没给走 AskQuestion / MCP 表单。

| 目的 | MCP（`devstudio-device`） | 等价 CLI |
|---|---|---|
| 抓日志并按 A–G 归类 | `device_analyze kind=log board=<IP>`（或 `path=<已存 log>`） | `devstudio device analyze log --board <IP>` |
| 只抓日志 | `device_log board=<IP> seconds=20` | `devstudio device log --board <IP> --seconds 20` |
| 启动脚本期望 ↔ 实机（模块 / 节点 / 进程 / 挂载） | `device_check board=<IP>` | `devstudio device check --board <IP>` |
| CPU / 内存均值峰值 | `device_analyze kind=status board=<IP>` | `devstudio device analyze status --board <IP>` |
| 改前改后对比 | `capture` 两次 → `compare before after check=…` | `devstudio device capture` / `compare` |
| RTSP 连通 | `device_rtsp url=rtsp://<IP>:554/live/main` | `devstudio device rtsp --url …` |
| 构建日志分类（主机侧，F 类） | `devstudio-build` → `build_log path=<log>` | `devstudio build --log <file>` |

- 启动类问题先 `device_check`：它对照产品 `app_01_start.sh` 等脚本里的 `insmod` / `/dev/*` / `MAIN_TARGET` 与实机 `lsmod` / 节点 / 进程，缺项按 D 类处理。`--save-adapter` 会把解析出的期望写进 Adapter 的 `device_expect.json`。
- `device_analyze` 的归类规则在 `src/devstudio/tools/device/analyze.py` 的 `_BUG_RULES`，与下表同源；它只做关键词命中，**不代替**人工看画面和串口现象。没有证据不写通过。

## PASS/FAIL 判据

| 场景 | PASS | FAIL 关键词 |
|---|---|---|
| 预览 | 连续出帧 ≥20s | `invalid inbuf` / `QBUF fail` / select timeout / sensor 反复上下电 |
| 录像 | 文件增大、正常收尾 | live 期 config reload、写文件失败 |
| 崩溃 | 无 signal | `signal=11` / `fault_addr=` |
| 启动 | 主进程起来、外设节点在 | panic、insmod 失败、主进程名错 |
| 出流 | RTSP/nebula ≥20s 稳 | 断流、端口起不来、编码失败 |

---

## Bug 分类总表（贴 log 先查这里）

| 类 | 典型 log / 现象 | 主 skill | 辅 skill |
|---|---|---|---|
| **A 崩溃** | `signal=11`、`fault_addr=`、segfault、backtrace | 按栈归层 | [celestia](../../overview/celestia/SKILL.md) |
| **B 预览/采集/编码** | `invalid inbuf`、`QBUF fail`、`select timeout`、`num_slices`、不出帧 | [celestia-hardware](../../hardware/celestia-hardware/SKILL.md) / [celestia-framework](../../framework/celestia-framework/SKILL.md) | — |
| **C 内存/DMA/OOM** | ion/dma alloc fail、`QBUF`+meminfo 正常、OOM、花屏 | 本节「内存/DMA」 | [celestia-project](../../project/celestia-project/SKILL.md)、[celestia-third-party](../../build/celestia-third-party/SKILL.md) |
| **D 启动/驱动/Board** | `/dev/*` 缺失、insmod 失败、拉错主进程、overlay 无效、`unknown-block`、烧录 `Page more than end page` | [celestia-project](../../project/celestia-project/SKILL.md) | [celestia-hardware](../../hardware/celestia-hardware/SKILL.md)、[celestia-third-party](../../build/celestia-third-party/SKILL.md) |
| **E App/GUI/SO** | `dlopen` 失败、不上桌面、图标空白、IOTCamera 形态秒退 | [celestia-application](../../application/celestia-application/SKILL.md) | — |
| **F 构建/stale/配置** | 改源不生效、`Failed to get … module: -1`、config.h 不对 | [celestia-scripts](../../build/celestia-scripts/SKILL.md) | [celestia-hardware](../../hardware/celestia-hardware/SKILL.md)、[celestia-project](../../project/celestia-project/SKILL.md) |
| **G 出流/网络/云** | RTSP/nebula 无流、云连不上、`stream_reg_client` 失败 | [celestia-framework](../../framework/celestia-framework/SKILL.md) | [celestia-application](../../application/celestia-application/SKILL.md) |

不确定类 → 先 [celestia](../../overview/celestia/SKILL.md) 五层路由，再回本表。

### log 关键词速查（工具不可用时手工 grep）

`device_analyze` 已内置下面前半段关键词（大小写不敏感）；手工 grep 时补上后半段扩展词。

```bash
LOG=/tmp/board.log    # 例：devstudio device log --board <IP> --seconds 30 后保存的文件
grep -niE 'signal=11|segfault|fault_addr=|backtrace|panic|SIGSEGV|watchdog' "$LOG"                    # A 崩溃
grep -niE 'invalid inbuf|qbuf fail|select timeout|num_slices|不出帧|3dnr' "$LOG"                        # B 预览/采集
grep -niE 'oom|dma alloc|ion alloc|alloc fail|out of memory|(venc|jpeg).{0,20}alloc|MemFree' "$LOG"     # C 内存/DMA
grep -niE "insmod|unknown-block|page more than end page|no such device|modprobe|can't insert" "$LOG"    # D 启动/Board
grep -niE 'dlopen|undefined symbol|cannot open shared object|dlsym|app entry not found' "$LOG"         # E App/SO
grep -niE 'failed to get|module: -1|stale|undefined reference|Error 1|No rule to make' <构建输出>       # F 构建（主机侧）
grep -niE 'rtsp|stream_reg_client|nebula|listen failed|bind failed|554|vnet0|wlan0' "$LOG"            # G 出流/网络
```

命中即回上表对应类套解法；**全部为空**才走五层路由逐层看。F 类看的是主机构建输出，不是板端 log。

---

## A. 崩溃 / 死机

| 现象 / log | 根因 | 解法 | skill |
|---|---|---|---|
| 开 AI 后 `signal=11 fault_addr=0x90` | `camera_manager` close 与 `ai_enable` 竞态 | 生命周期 mutex；`ai_enable` 路径本地捕获 device 指针 | framework / hardware |
| 任意 `signal=11` + backtrace | 空指针 / UAF / 越界 | 抓栈 → 归层 → 核对本层句柄生命周期与锁 | [celestia](../../overview/celestia/SKILL.md) → 层 skill |
| `signal=11` 在 `free`/`malloc` 内，`fault_addr` 接近 `0xffffffff`（如 `free+0x50` / `0xfffffffd`）| uclibc `free()` 先读 `mem-8` 的 chunk 头：`fault_addr≈0xffffffff` ⇒ 传进去的是**小整数**（`free(5)`→`0xfffffffd`），**不是**真 double-free。两种来源：① 指针字段被当 int 字段读（结构体布局不一致，走 F 类 stale）；② 堆被更早的越界写坏，free-list 的 `fd` 被覆盖成小整数 | **先看 `r0`**（崩溃处理器已打印）：`r0` 是小整数→①/上游传错指针；`r0` 是合法堆指针→②，故障在更早的越界写，与崩溃点无关。再看 `tid` + `stack[]` 定栈 | 按 `stack[]` 归层 |
| 拍照/录像后进程挂死 | teardown 关电或 live reload 清零 priv | 对称 teardown；live 期禁止 `load_config` 清 live | framework / hardware |
| watchdog / 整机复位 | 驱动死锁或内核 oops | `dmesg`；先 D 类查 ko，再 HAL ioctl | project / hardware |

**抓证**：串口完整 backtrace + 崩溃前 50 行 + 产品名 + 复现步骤。

---

## B. 预览 / 采集 / 编码

| 现象 / log | 根因 | 解法 | skill |
|---|---|---|---|
| `invalid inbuf cap=524288` 丢帧 | `max_frame_payload` 在 `codec_mode` 前算，NV12 退回 512KB | **先解析 `codec_mode` 再算 payload** | framework / hardware |
| `QBUF fail` / 重入失败 | capture/record teardown 破坏 live 通路 | 对称 teardown；勿在 live 期 reload | framework / hardware |
| CHN0 `select timeout` | `num_slices≥2` 但 VENC 未 bind 到 VI（slice 通道不向用户态出 YUV）| AV100_V2 走 bind + `venc_get_stream`；未实现的 SoC 保持 `num_slices=1` | hardware |
| sensor 不出帧 / `/dev/video0` 无 | ko 未载或 HAL 未 open | 先 D 类 insmod，再 `hw_get_module` | project → hardware |
| `vi 3dnr memsize … 5040 KB` 异常 | 3DNR 常量未 clamp | clamp + `HAL_CAMERA_VI_3DNR_KB` | hardware |
| 花屏 / 花块但进程在 | DMA 帧不足或格式错 | 走 **C 类** 决策树；核对宽高/stride | debug 内存节 |
| 码流改了不生效 | 改了 `channels/streams` 能力集 | 只动 `app_stream_usage`（framework §2.5.1）| application + framework |

层内补充：[hardware 常见问题](../../hardware/celestia-hardware/SKILL.md#常见问题)、[application 常见问题](../../application/celestia-application/SKILL.md#常见问题)。

---

## C. 内存 / DMA / OOM（LUNA）

> 数值细节见下方「内存模型」；改前改后用 `device_status` / `tune_memory` 取两次实测再下结论（见 `devstudio/sdk/tune/tune-memory`）。

| 现象 / log | 分流 | 解法 |
|---|---|---|
| dmesg `ion`/`dma`/`QBUF`/`VENC` alloc fail，meminfo 尚可 | **DMA 池不足** | A：DTS `RESERVED_MEMORY_SIZE` 升档（board+patch 同步）；B：降 `max_buffers`/分辨率 |
| OOM / malloc 失败 / MemFree 极低，无 ion 报错 | **MemTotal 不足** | C：`CONFIG_FW_MULTIMEDIA_MEDIA_PLAYER=n`、curl mbedTLS；D：调 `cirbuf_size`（勿拿它省 DMA）|
| 预览 OOM 同时编码挂 | 两池此消彼长 | 先分清哪池；加大 DMA 会压缩 MemTotal |

主 skill：本节；改 DTS → [celestia-project](../../project/celestia-project/SKILL.md)；裁库 → [celestia-third-party](../../build/celestia-third-party/SKILL.md)。

### 内存模型（64MB 切两块）

| 预算 | 大小 | 用途 | 调整 | meminfo |
|------|------|------|------|:---:|
| DMA/ION 预留 | **20MB**（`0x1400000`）| VI/VENC/JPEG/显存 | DTS `RESERVED_MEMORY_SIZE` | ❌ |
| Linux MemTotal | **~25MB** | 进程 + cirbuf | config.mk / `fw_video_config.json` | ✅ |

形态：`0x1980000`=25.5M · `0x1A00000`=26M · `0x2200000`=34M · `0x2400000`=36M。
路径（须 **board + patch 双改**）：

- `project/anypi_luna/.../board/PDB_MBD_KM01W_ACTCAM/os/kernel/.../anycloud_km01a.dtsi`
- `project/anypi_luna/.../patch/os/kernel/.../anycloud_km01a.dtsi`

`fw_video_config.json` 在 **board rootfs overlay**（非 `project/*/config/`）。

---

## D. 启动 / 驱动 / Board

| 现象 / log | 根因 | 解法 | skill |
|---|---|---|---|
| `/dev/video0` 等缺失 | insmod 顺序/路径错 | 核对 `app_01_start.sh` / `app.sh` + `lsmod`/`dmesg` | project |
| 开机拉错主进程 | 脚本二进制 ≠ `CONFIG_APP_MAIN_TARGET` | 改启动脚本对齐 `<suffix>_main` | project |
| overlay 配置不生效 | 改了 vendor 树或被 patch 覆盖 | 只改 `board/<BOARD>/`；核对接叠顺序 | project |
| 无串口 / 不启动 | bootloader/kernel/DTB | 查烧录分区、DTB 与 BOARD 是否匹配 | project |
| `unknown-block(31,6)` / 挂不上 root | burn / env / DTS `mtdparts` 三处不一致 | 对齐现表；重编 DTB + 重烧 ENV/DTB | project §分区 |
| 烧录 `Page more than end page`（APP/ROOTFS） | `usr.sqsh4`/`root.sqsh4` > 分区；APP 常见 FFmpeg 膨胀 | `DECODE_ONLY`+`LINK_MODE=decode` 或挪 SD `install/`；见 project | project / third-party |
| SDK 版本与 overlay 目录不一致 | `CONFIG_SOC_SDK_VERSION` 错 | 对齐版本目录 | project |

详见 [project 常见问题](../../project/celestia-project/SKILL.md#常见问题) / [分区调优](../../project/celestia-project/SKILL.md#anyka-burntool-分区调优nova-16mb-spl-fastsys)。

---

## E. App / GUI / SO

| 现象 / log | 根因 | 解法 | skill |
|---|---|---|---|
| `dlopen` 失败 / 入口找不到 | `--gc-sections` 剪 entry | `visibility("default")` + keep-alive | application |
| App 不上桌面 | `installed_apps` 未注册 / path 错 | 注册并对齐运行期 path | application |
| `so_path` 加载失败 | 写了源码路径 | 以板级 `settings.json` 实际运行期路径为准（如 `/usr/lib/lib*.so`）；SD 覆盖加载时 `/mnt/install/lib`、`/mnt/afht`（`afht.sqsh4` loop 挂载）也在 `LD_LIBRARY_PATH` | application |
| Luna 图标空白 | GPU draw 失败丢弃、无 SW 回退 | `ret<0` 重新入队回退 SW（id=1）| application / hardware |
| IOTCamera 形态启动即退出 | 无 GUI 时 `main` return | SO `module_start` 阻塞保活 | application |
| 通话无画面 / `VIDEO_DECODER_SW_STUB` | `FFMPEG_LINK_MODE=none`（stub） | Nova：`decode` + `DECODE_ONLY`；`-t`/`-f`/`-p` | framework / third-party |

详见 [application 常见问题](../../application/celestia-application/SKILL.md#常见问题)。

---

## F. 构建 / stale / 配置

| 现象 / log | 根因 | 解法 | skill |
|---|---|---|---|
| 改源板上行为不变 | stale `out/` / 旧 install 头 | `./build.sh -c` 再编对应层；确认 `--product-name` | scripts |
| 改私有头的结构体布局后随机崩溃 / `free` 内 SIGSEGV | **Makefile 无头依赖**：`%.o: %.c` 只依赖 .c，未改的 .c 保留旧 offset，指针字段落到 int 字段上 | Makefile 加 `-MMD -MP` + `-include $(OBJS:.o=.d)`（`hardware/camera` 已加）；删该模块 `out/**/build` 下旧 `.o` 再全编 | scripts + 对应层 |
| 想确认是不是 stale | — | **比时间戳**：`ls -l out/<product>/build/<模块>/**/*.o`，只要有一批明显早于本次改动就是 stale；全批同一时间戳 + 有 `.d` ⇒ **排除** stale，别再往这条路上查 | scripts |
| `Failed to get video hardware module: -1` | stale `hardware.o`（config.h 非依赖）| Makefile 加 config.h 依赖；`-c -h` | hardware + scripts |
| `config.h` 与预期不符 | config.mk 语法 / 产品名错 | 核对 `project/<p>/config.mk` | project |
| 链接缺符号 | third_party 未先编 | `-t` 再编上层；查 CONFIG 裁剪 | scripts / third-party |
| 工具链错误 | 按产品名选链 | 按平台：AV200 riscv64 / AV100_V2 arm | project / scripts |

---

## G. 出流 / 网络 / 云

| 现象 / log | 根因 | 解法 | skill |
|---|---|---|---|
| RTSP 无流 | 未 `stream_reg_client` / `CONFIG_APP_RTSP` 关 / 编码失败 | 开开关；App 注册 main；先 B 类保证编码 | framework + application |
| nebula 连不上 | 配置/开关/网络 | 核 `CONFIG_APP_NEBULA` 与 gateway open | framework |
| 云推流失败 | cloud SO 未加载或网络 | 查 `settings.json`/`load_so_module`；禁依赖 `module_exit` | application |
| WiFi 无 `wlan0`（Luna）| 接口名是 `vnet0` | 用 `vnet0`；查 `wifi_driver.sh` | project |

---

## 排障流程（给 AI）

```
1. 收集：产品名 + 复现步骤 + 关键 log（含前后文）；有板号就 device_analyze kind=log，启动类再 device_check
2. 用「Bug 分类总表」命中 A–G（工具已给类别时直接用，人工复核一次）
3. 打开对应解法表；能套已知模式则直接修
4. 否则进主 skill，以本层为主；有证据再下钻；历史结论只用 doc_search 查，未收录就说未收录
5. 改完：对应 build.sh flag（或 MCP build）→ push_app / device_firmware / 烧录 → 按 PASS/FAIL 复测
6. 回填 docs/quick-start/roles.md 排障表：现象 / 归类 / 证据 / 根因 / 修复 / 验证
```

## 定位到层后

按 [celestia](../../overview/celestia/SKILL.md)「本层 → 接口契约 →（有据）下层」收敛；改完回到本 skill 闭环直到 PASS。
