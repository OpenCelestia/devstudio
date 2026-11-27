---
name: build-triage
description: >-
  把编译日志分成源码错误、环境错误、构建系统错误或通过，决定回喂 Agent、重试还是留给人工。
  何时用：编译失败要判断是改代码、重试还是交给人工，或拿到一份构建日志要分类时。
  When: build failed and need to decide code-fix vs retry vs human, or classify a build log.
---

# build-triage

把编译日志分成源码错误、环境错误、构建系统错误或通过。源码错误回喂给 Agent，环境错误重试，构建系统错误留给人工。

## 工具

| 场景 | MCP（devstudio-build） | CLI |
|------|------------------------|-----|
| 执行 `devstudio.yaml` 的 `build.command` 并分类 | `build`（host / command / timeout 可选） | `devstudio build --run [--host local\|wsl\|ssh] [--command …] [--timeout 秒]` |
| 分类已有日志 | `build_log`（path 必填，exit_code / module 可选） | `devstudio build --log 文件 [--exit N] [--module 名] [--round N]` |

`--host` 缺省取 `devstudio.yaml` 的 `build.host`（默认 wsl；`ssh` 时拼 `build.ssh_host`）。没有 `build.command` 又没给 `--command` 时直接报错，不要自己猜命令。

## 分类规则（以 `src/devstudio/tools/build/classify.py` 为准）

按顺序判断，先命中先算：

| 分类 | 命中条件 | 处理 |
|------|----------|------|
| 源码错误 | `文件.c/.h/.cpp:行号: error:`，或含 `undeclared` / `undefined reference` | 回喂给 Agent 改代码，最多 3 轮 |
| 环境错误 | `fixdep:`、`opensbi/kernel build failed`、`u-boot.cfg`、`os/kernel/`、`os/uboot/`、`os/opensbi/`、`找不到`、`SSH`、`not found in PATH`、`No such file` | 不改代码，重试，最多 2 次 |
| 构建系统错误 | `missing separator`、`multiple files` | 停下，留给人工；不改 Makefile |
| 通过 | 退出码 0 且以上都未命中 | 完成等级 L3 |
| 未知 | 退出码非 0 且以上都未命中 | 把日志尾部给用户，不下结论 |

注意：`undefined reference` 也算源码错误，常见原因是缺库或缺 `.c` 进编译；按 `celestia-scripts`、`celestia-third-party` 查链接，不要改模块开关。

## 相关

- 用哪个 flag、改了不生效：`devstudio/celestia/build/celestia-scripts/SKILL.md`
- 第三方库链接失败：`devstudio/celestia/build/celestia-third-party/SKILL.md`
