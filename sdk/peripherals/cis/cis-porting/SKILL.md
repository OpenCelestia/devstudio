---
name: cis-porting
description: >-
  查当前 release_id 的 Camera Sensor（CIS）支持列表；列表内用 sensor confirm/build，列表外按 need_info 收参出方案包。
  何时用：要换或新接 Camera Sensor、问某个 sensor 型号是否支持、sensor 不在列表里要出移植方案时。
  When: swap/add Camera Sensor, ask if a sensor model is supported, or need a porting plan for an out-of-list sensor.
---

# cis-porting

外设调试能力之一（目录：`devstudio/sdk/peripherals/cis/`）。查当前 release_id 的 Camera Sensor（CIS）支持列表。交互对齐 FAE AI `sensor-debug` / `need_info`，**所有问答走 IDE 运行中 Ask**（Cursor AskQuestion；MCP `elicitation/create` 聊天内表单），禁止桌面框、禁止聊天干等。

## 列表内

1. `devstudio sensor confirm --model <型号>` 核对分辨率、帧率、接口、CIS 模式和功能。
2. 厂商文档没有时写未收录，仍直接 `devstudio sensor build`。
3. 实机调试包优先 Adapter，其次与 develop 同级的 FAE AI 下载包。不要求 datasheet 或寄存器序列。

## 列表外（方案流 = FAE need_info）

`devstudio plan sensor --model <型号>` 或 MCP `plan_sensor`：

缺参时立刻 Ask / elicitation 收集（与 FAE 一致）：

| 字段 | 说明 |
|------|------|
| resolution / fps / interface | 分辨率、帧率、接口 |
| cis / features | CIS 目数模式、常电/快启/AOV 等 |
| lanes / mclk / i2c | Lane、MCLK、I2C |
| sequence_text 或 sequence | **粘贴** ini/`0xADDR,0xVAL` 全文，或本地文件路径 |
| datasheet | 可选：PDF/文档本地路径 |

生成未实机验证的方案包到任务目录；不把方案直接写进工程树。批准也走 Ask：`plan approve <id>` → 运行中确认 → `--confirm` 重试。

合入与放置规则另见工程约定：SDK 相关写入对应 SDK；Celestia 写入 `project/<产品>/…`；纯 SDK/客户 SDK 时 Ask 选择「写入该 SDK」或「按 project 同构目录放在 SDK 侧便于 COPY」。

相关：Flash 见 `../flash/flash-porting`；列表 `devstudio knowledge peripherals --type sensor`。
