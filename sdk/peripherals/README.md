# 外设调试能力（peripherals）

本目录集中 **外设调试 / 适配** 方案。每个子目录是一类外设，其下是该外设的 Skill。

| 子目录 | 方案 | 说明 |
|---|---|---|
| `cis/` | Camera Sensor（CIS） | `cis-porting`：支持列表、确认参数、列表内打包 / 列表外方案包 |
| `flash/` | Flash / 存储 | `flash-porting`：查 SPI NOR/NAND 支持列表，指导表外适配（不编造型号） |
| （预留）`wifi/` | WiFi | 后续可加 `wifi-porting`，列表已由 Adapter `wifi_catalog.json` 提供 |

版本相关数据仍在 `../adapters/<release_id>/`：

- Sensor：`catalog.json`
- Flash：`flash_catalog.json`
- WiFi：`wifi_catalog.json`

查询列表示例：

```text
devstudio knowledge peripherals --type sensor
devstudio knowledge peripherals --type flash
devstudio knowledge peripherals --type wifi
```
