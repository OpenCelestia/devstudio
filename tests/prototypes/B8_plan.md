# 计划 t0001：GC5603 适配
状态：待批准
release_id：AnyCloud39AV200_V1.04　状态：sdk_celestia　创建：2026-11-19

## 目标
为 GC5603 生成驱动方案，不改参考型号的 ISP 二进制。

## 已确认参数（来源）
- Lane：2
- MCLK：24MHz
- I2C：0x37
- 序列：seq.ini

## 待确认项
- Reset GPIO

## 硬件约束检查结果
MIPI 控制器 满足
Lane 满足
I2C 地址 满足
Reset GPIO 待确认（板卡配置里未找到）
勘误 未收录

## 将修改的文件
## 由规则写入的数字字段
## 将调用的 SDK 符号（头文件:行）
## 编译命令与位置
./build.sh --product-name anypi_nova　位置：wsl

## 风险
列表外型号，方案未实机验证。

## 验证方法
编译通过后，指定板号再读 I2C ID 和出图。
