---
name: hal-adapt
description: >-
  客户 HAL 适配 skill（仅 sdk_customer_app 状态使用）：按客户 HAL 接口与当前 release_id
  的索引写 HAL，只写 `customer.hal_out`。流程为 `devstudio hal plan`（待批准计划）→
  `devstudio plan approve` → `devstudio hal apply <task_id>` 写文件；索引里没有的符号
  不写入调用。sdk_celestia / sdk_only 状态下不使用本 skill（用 celestia-hardware）。
  何时用：仅 sdk_customer_app 状态下，要按客户 HAL 接口写 HAL 适配代码时。
  When: only in sdk_customer_app, writing HAL adapt code against the customer HAL interface.
---

# hal-adapt

按客户 HAL 接口和当前 release_id 的索引写 HAL。只在 `sdk_customer_app` 下使用，并且只写 `customer.hal_out`。

`devstudio hal plan --module 模块 --header 头文件 [--ref 参考.c]` 写待批准计划。索引里没有的符号不写入调用。`devstudio plan approve` 之后，`devstudio hal apply <task_id>` 才写文件。
