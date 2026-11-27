---
name: tune-cpu
description: >-
  比较同一块板、同一条命令下前后两次的 cpu，没有两次有效读数或没有阈值记录时不写优化结论。
  何时用：用户问 CPU 占用降没降、改动后负载变化、要对比优化前后 CPU 时。
  When: user asks if CPU load dropped, load after changes, or compare before/after CPU.
---

# tune-cpu

比较同一块板、同一条命令下的前后两次 `cpu`。没有两次读数，或 Adapter 里没有阈值记录时，不写优化结论。

## 步骤

1. 取板号：对话里已提就用该 IP，否则用运行中 Ask 问；不要自己挑板。
2. 改动前取一次：MCP `capture`（devstudio-device）传 `board`、`seconds`、`command`；CLI `devstudio device capture --board <IP> --seconds N --command "<命令>"`。记下保存目录。
3. 改动、编译、推送后，用**同一块板、同一条 command** 再取一次。
4. 比较：MCP `tune_cpu`（devstudio-tune）传 `before`、`after` 两个目录；CLI `devstudio tune cpu --before 目录 --after 目录`。

## 比较规则（以 `src/devstudio/tools/tune/compare.py` 为准）

| 输出 | 原因 | 处理 |
|------|------|------|
| 没有两次读数 | 目录缺 `meta.json` / `metrics.jsonl`，meta 的 channels 没有 metrics，或末帧没有 `cpu` | 重新 capture |
| 板号不一致 | 两次 meta 的 board 不同 | 换同一块板重取 |
| 没有同一命令的两次读数 | command 为空或两次不同 | 用同一条 command 重取 |
| 阈值未收录 | `devstudio/sdk/adapters/<release_id>/tune/cpu.yaml` 不存在或为空 | 只报数值变化，不写优化结论 |
| 已有阈值记录 | 阈值文件存在 | 仍只报数值，结论由人判断 |

取的是 `metrics.jsonl` 的**最后一帧**，单帧波动大；要看均值、最低值，用 `device_analyze`。

相关：`devstudio/sdk/tune/tune-memory/SKILL.md`；卡顿排障见 `devstudio/celestia/device/celestia-debug/SKILL.md`。
