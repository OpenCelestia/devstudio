<p align="right"><b>English</b> | <a href="README.md">中文</a></p>

# Anyka DevStudio

DevStudio is a host-side tool layer that plugs into existing IDEs (Cursor / VS Code / Claude Code / Codex). It exposes the same capabilities through a **CLI** and **MCP**, and constrains agents with **Skills**. It is not a new IDE and does not host or configure LLMs — chat and models stay with the IDE agent.

In the Celestia manifest this repository is `devstudio` (`https://github.com/OpenCelestia/devstudio`), checked out as `devstudio/` under the workspace root.

## Quick start

Install from this directory (runtimes are installed if missing, then the tree is detected; use `--install-only` for tools only):

| Platform | Command |
|----------|---------|
| Windows | `setup_windows.cmd` (or `powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1`) |
| Linux | `./setup_linux.sh` |
| macOS | `./setup_macos.sh` (chip cross builds need Linux: SSH / UTM, etc.) |

Sanity check: `devstudio --version`, `devstudio adapter list`. Keep sources and toolchains inside a Linux / WSL tree (do not build only from `/mnt/c` on Windows).

**Windows entry**: `setup_windows.ps1` writes `devstudio.cmd` (cmd/PowerShell) and suffix-less `devstudio` (Git Bash) under `%LOCALAPPDATA%\Anyka\DevStudio\`, and syncs a local `runtime-tree/` (`src/` + `sdk/` + `celestia/`). It runs a **Win32 venv directly (no `wsl.exe`)**, so UTF-16 does not leak into pipes. PATH is prepended and broken third-party `Scripts\devstudio.exe` entries are disabled. Output is UTF-8; do not pipe through `| iconv -f GBK`. After source changes, re-run `setup_windows.ps1 --install-only`, then open a new terminal / restart the IDE.

### `sdk_celestia` (SDK + Celestia): two stages

| Stage | You do | Automatic |
|-------|--------|-----------|
| Build | `./build.sh --product-name <product>` at the workspace root | Maintains root `devstudio.yaml` and `AGENTS.md` (no IDE prompt) |
| Open IDE | Open the **workspace root** (the layer with `build.sh`) | Refreshes MCP under `devstudio/ide/` for the current IDE |

Force AGENTS rewrite without building: `./build.sh -d --product-name <product>`.

### Other states: full init

| State | What to do |
|-------|------------|
| `sdk_customer_app` | `devstudio init` (or `setup_*`) → confirm state → answer app root / HAL / build questions |
| `sdk_only` | `devstudio init` at the SDK root → confirm `sdk_only` |

After init, the root has `devstudio.yaml` and `AGENTS.md`; MCP defs live under `devstudio/ide/`. Skill **bodies stay in this repo**, referenced from `AGENTS.md` as `id | when` (open `devstudio/<id>/SKILL.md`). **Do not** copy them into `.cursor` / `.claude` / `.vscode`.

Then ask in the agent panel, e.g. “list MIPI sensors for the current release”, “add a recorder tablet app; do not write sources before approval”, “push calculator to the board”.

## Layout

| Path | Content |
|------|---------|
| `src/devstudio/` | CLI / MCP / orchestration / device and knowledge tools |
| `celestia/` | Celestia product-layer Skills |
| `sdk/` | SDK solution Skills and `adapters/<release_id>/` |
| `ide/` | MCP definitions for Cursor / VS Code / Claude / Codex |
| `web/` | PC UI (`devstudio web`; device protocol shares 7777 with CLI) |
| `tests/` | Unit and integration tests |

Day-to-day usage (command groups, push, Ask conventions): Celestia docs `docs/guide/devstudio.en.md`.

## Three workspace states

| State | Source mix | Main capabilities |
|-------|------------|-------------------|
| `sdk_celestia` | SDK + Celestia | Apps, sensors, build, device, Web |
| `sdk_customer_app` | SDK + customer app | HAL, driver plans, customer builds |
| `sdk_only` | SDK only | Search, solution packs, SDK builds |

## Core constraints

- Knowledge and tasks bind to the current `release_id`; never invent missing entries.
- Writing files requires in-IDE Ask approval of a plan; missing board ids require Ask — no guessing or silent board picks.
- Registers, Sensor IDs, VTS, frame rates, init sequences only via rules tools / customer sequences.
- L3 means compile-only; on-device verification plus human confirmation is L5.
- Do not set `DEVSTUDIO_UI=desktop` for OS dialogs.

## Web

```bash
devstudio web [--port 8081] [--product anypi_nova]
```

Requires Node.js ≥ 18. See [`web/README.md`](web/README.md).

## Dev checks

```powershell
$env:PYTHONPATH="$PWD\src"
python -m unittest discover -s tests -p "test_*.py"
```

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py'
```
