<p align="right"><a href="README.en.md">English</a> | <b>中文</b></p>

# Anyka DevStudio

DevStudio 是装进现有 IDE（Cursor / VS Code / Claude Code / Codex）的本机工具层：用 **CLI** 与 **MCP** 提供同一套能力，用 **Skill** 约束 Agent 怎么改代码、怎么上板。它不是新的 IDE，也不配置或托管大模型——对话与模型用 IDE 自带的 Agent。

本仓在 Celestia manifest 中为 `devstudio`（`https://github.com/OpenCelestia/devstudio`），检出路径为工程根下的 `devstudio/`。

## 快速开始

在本目录按平台安装（缺运行时会先装，再识别工程；只要运行时可加 `--install-only`）：

| 平台 | 命令 |
|------|------|
| Windows | `setup_windows.cmd`（或 `powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1`） |
| Linux | `./setup_linux.sh` |
| macOS | `./setup_macos.sh`（芯片交叉编译须 Linux：SSH / UTM 等） |

装完自检：`devstudio --version`、`devstudio adapter list`。源码与工具链放在 Linux / WSL 树内（Windows 勿只在 `/mnt/c` 下编译）。

**Windows 入口**：`setup_windows.ps1` 在 `%LOCALAPPDATA%\\Anyka\\DevStudio\\` 写入 `devstudio.cmd`（cmd/PowerShell）、无后缀 `devstudio`（Git Bash），并同步本地 `runtime-tree/`（`src/` + `sdk/` + `celestia/`）。用 **Win32 venv 直跑，不经过 wsl.exe**，避免 UTF-16 混进管道。PATH 置顶并停用损坏的第三方 `Scripts\\devstudio.exe`。输出 UTF-8，不要再 `| iconv -f GBK`。改源码后重跑 `setup_windows.ps1 --install-only`；新开终端/重启 IDE 后再用。

### `sdk_celestia`（SDK + Celestia）：两段式

| 段 | 你做什么 | 自动发生什么 |
|----|----------|--------------|
| 构建 | 工程根 `./build.sh --product-name <产品>` | 维护根目录 `devstudio.yaml` 与 `AGENTS.md`（不问 IDE） |
| 开 IDE | 用 IDE **打开工程根**（有 `build.sh` 的那一层） | 按当前 IDE 刷新 `devstudio/ide/` 下 MCP |

只强制重写 AGENTS、不编译：`./build.sh -d --product-name <产品>`。

### 其它状态：完整 init

| 状态 | 做法 |
|------|------|
| `sdk_customer_app` | `devstudio init`（或 `setup_*`）→ 确认状态 → 答应用根 / HAL / 编译相关问题 |
| `sdk_only` | 在 SDK 根 `devstudio init` → 确认 `sdk_only` |

初始化后根目录有 `devstudio.yaml`、`AGENTS.md`；MCP 定义在 `devstudio/ide/`。Skill **正文留在本仓**，由 `AGENTS.md` 按 `id \| when` 引用（打开 `devstudio/<id>/SKILL.md`），**不要**复制到 `.cursor` / `.claude` / `.vscode`。

然后在 Agent 面板直接说任务，例如：「查当前版本支持的 MIPI Sensor」「新增 recorder 应用，批准前不要写源码」「把 calculator 推到板子」。

## 目录

| 路径 | 内容 |
|------|------|
| `src/devstudio/` | CLI / MCP / 编排 / 设备与知识工具 |
| `celestia/` | Celestia 产品分层 Skill |
| `sdk/` | SDK 方案 Skill 与 `adapters/<release_id>/` |
| `ide/` | Cursor / VS Code / Claude / Codex 的 MCP 定义 |
| `web/` | PC 页面（`devstudio web`；设备协议与 CLI 共用 7777） |
| `tests/` | 单元与集成测试 |

日常用法主页（命令族、推板、Ask 约定）见 Celestia 文档仓：`docs/guide/devstudio.md`。

## 三种工程状态

| 状态 | 源码组合 | 主要能力 |
|------|----------|----------|
| `sdk_celestia` | SDK + Celestia | 应用、Sensor、构建、设备、Web |
| `sdk_customer_app` | SDK + 客户应用 | HAL、驱动方案、客户构建 |
| `sdk_only` | 只有 SDK | 检索、方案包、SDK 构建 |

## 核心约束

- 知识与任务绑定当前 `release_id`；未收录不编造。
- 写文件前须经 IDE 运行中 Ask 批准计划；缺板号须 Ask，禁止臆造或静默挑板。
- 寄存器、Sensor ID、VTS、帧率、初始化序列只走规则工具 / 客户序列。
- L3 只表示编译通过；实机验证并由人确认后才是 L5。
- 禁止设 `DEVSTUDIO_UI=desktop` 弹系统框。

## Web

```bash
devstudio web [--port 8081] [--product anypi_nova]
```

需要 Node.js ≥ 18。详见 [`web/README.md`](web/README.md)。

## 开发验证

```powershell
$env:PYTHONPATH="$PWD\src"
python -m unittest discover -s tests -p "test_*.py"
```

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py'
```
