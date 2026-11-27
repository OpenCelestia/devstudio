---
name: flash-porting
description: >-
  查当前 release_id 的 Flash / 存储支持列表，指导表内核对与表外适配，不编造未收录型号。
  何时用：要换 Flash 或存储颗粒、问某颗 NOR/NAND/MMC 是否支持、要在 NOR / NAND / MMC 之间切换时。
  When: swap Flash/storage, ask if a NOR/NAND/MMC part is supported, or switch among NOR/NAND/MMC.
---

# flash-porting

查当前 `release_id` 的 Flash / 存储支持列表，指导表内核对与表外适配。不编造未收录型号。口径与 FAE AI `gen_flash_lists.py` / 问答站一致。

## 查列表

```text
devstudio knowledge peripherals --type flash
```

输出含（对齐 FAE localhost 问答）：

| 类别 | 含义 |
|------|------|
| SPI NOR | 驱动 ID 表型号，按厂商分组列出 |
| SPI NAND | 按厂商驱动文件列出 |
| raw NAND | 仅说明 ID 表条数或未入库；不逐型号展开 |
| MMC/SD | **不按型号管理**；只列平台官方 `*_mmc`/`*_spinand` DTS（过滤上游参考板） |
| 4G | 无型号级列表（L2 内核配置） |

「共 N 款」只计 **SPI NOR + SPI NAND** 识别表条目，不把 DTS 变体数算进款数。

数据：`devstudio/sdk/adapters/<release_id>/flash_catalog.json`。未列出即当前 Adapter 未见识别条目。

## 表内

确认 JEDEC / 型号在列表中后，按板级分区与 burntool / env 配套换型；换 NOR↔NAND↔MMC **须重出全套镜像**（`CONFIG_FLASH_TYPE`），不是只改参数。

## 表外

更换表外颗粒属 L2：自编或扩展 ID 表、核对时序与容量、重做分区与烧录配置。先写计划，批准前不改驱动树。

## 相关

- WiFi：`devstudio knowledge peripherals --type wifi`
- Sensor（CIS）：`../cis/cis-porting`
- 从 FAE 重导入：`python devstudio/sdk/scripts/import_fae_peripheral_catalogs.py --fae-root … --adapters-root devstudio/sdk/adapters`
