# SDK

三种状态共用这里。按方案分目录，每个方案下是该方案的 Skill。版本之间不一样的数据只放在 `adapters/<release_id>/`。

| 目录 | 方案 | 使用状态 |
|---|---|---|
| `peripherals/` | **外设调试能力**：其下按外设分子目录（`cis/`、`flash/` 等） | 三种 |
| `peripherals/cis/` | Camera Sensor（CIS）：`cis-porting`；列表内打包，列表外方案包 | 三种 |
| `peripherals/flash/` | Flash / 存储：`flash-porting`；查 `flash_catalog.json`，表外不编造型号 | 三种 |
| `build/` | 编译：命令来自 `devstudio.yaml` 的 `build.command`，日志只分类，编译通过不等于实机完成 | 三种 |
| `tune/` | 调优：`device status`、`device log` 取数，`tune memory|cpu` 只比较同板同命令的两次读数，没有实测不写结论 | sdk_celestia |
| `adapters/<release_id>/` | 该版本的 catalog、flash/wifi 列表、头文件索引、勘误、调试包和实测 | 三种 |
| `scripts/` | Adapter 数据导入等维护脚本（如 FAE 外设列表导入） | 维护用 |

外设调试总览见 [`peripherals/README.md`](peripherals/README.md)。

## 共同规则

- 检索、规格和勘误只读当前 `release_id` 的 Adapter。没有记录写未收录，不查邻近版本。
- 寄存器、Sensor ID、VTS、帧率和初始化序列只来自客户提供的序列。没有序列不生成。
- 驱动差异用 `driver diff` 对照 catalog。API、I2C、时钟没有记录时写未收录，不改两个 SDK。
- 公开头文件索引用 `knowledge index`，只写函数名。

## 新增 SDK / 生成 Adapter

对具体 SDK 版本目录（父目录为平台族、目录名为 `Vx.xx`，含 `os`/`platform`/`tools`）执行：

```text
devstudio adapter generate --sdk system/AnyCloud39AV200/V1.05
# 已存在时：
devstudio adapter generate --sdk … --force
# 若有 FAE AI 的 sensor-debug catalog，可叠加：
devstudio adapter generate --sdk … --fae-root "<FAE AI>/fae-ai"
```

将写入 `adapters/<release_id>/`：`adapter.yaml`、`catalog.json`（CIS，来自 isp conf 池，可选 FAE 合并）、`flash_catalog.json`、`wifi_catalog.json`、`errata.json`，并尽量生成 `index/`。也可用已有脚本从 FAE 清单导入 flash/wifi：

```text
python devstudio/sdk/scripts/import_fae_peripheral_catalogs.py --fae-root … --adapters-root devstudio/sdk/adapters
```

共同规则和方案 Skill 不改。差异只在 Adapter 里写不下时，才在对应方案目录加说明。

HAL、固件推送和 AOS 移植属于 Celestia，见 `../celestia/README.md`。
