---
name: project-bootstrap
description: >-
  新工程接入（sdk_celestia）引导 skill：扫描工作区并确认 `devstudio init` 给出的状态
  （工程根、state、产品形态、引导文件是否齐全），与 devstudio/celestia/README.md 的
  分层对照；引导文件只在人经 IDE「运行中 Ask」批准后写入。
  何时用：首次接入新工程、AGENTS.md / devstudio.yaml / MCP 配置缺失或过期、要切换产品或 SDK（devstudio switch）时。
  When: first-time project onboarding, missing/stale AGENTS.md / devstudio.yaml / MCP config, or switching product/SDK (devstudio switch).
---

# project-bootstrap

1. **扫描**：确认工作区根、`devstudio init` 给出的状态（state / 产品形态 / 引导文件是否已生成），并与 `devstudio/celestia/README.md` 的分层与状态对照（`sdk_celestia` 用全部层、`sdk_customer_app` 只用 `hardware/hal-adapt`）。
2. **汇报**：把扫描结果（已有什么、缺什么）列给用户，此阶段**不写任何文件**。
3. **批准**：需要写入引导文件时，先给出逐文件计划，经 IDE「运行中 Ask」批准（Cursor AskQuestion / MCP elicitation / VS Code 聊天内选项 / Claude Code 选择）。**未批准不创建、不覆盖任何文件。**
4. **写入**：批准后仅写引导所需文件，逐文件说明用途。

禁止：未经确认创建/覆盖文件；把 SDK 版本差异写进引导文件（见 `devstudio/sdk/README.md`）；改 Makefile、Kconfig、config.mk 的模块开关。
