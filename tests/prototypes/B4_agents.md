<!-- devstudio:begin -->
# DevStudio AGENTS | AI 约束
- Chip/芯片: AnyCloud39AV200  SDK: V1.04  release_id: AnyCloud39AV200_V1.04
- State: sdk_celestia  Product/产品: anypi_nova  Form/形态: TABLET
- Plan before edits; Ask via IDE Ask UI (AskQuestion / elicitation / options); NEED_* ASK_UI=…; no tkinter; no idle chat wait | 改前计划+IDE问答
- Write only via apply | 只经 apply 写文件
- No Makefile / Kconfig / config.mk module switches | 不改模块开关
- regs / Sensor ID / VTS / fps / init seq: only via `devstudio-rules`
- search: only via `devstudio-knowledge`; else say not indexed | 未收录
- board IP: use if given; else Ask UI; never invent / silent-pick; never DEVSTUDIO_UI=desktop
- env: `devstudio doctor`; on fail ASK_UI → `doctor --set-host ssh --ssh-host … --ssh-workdir …`
- Skills: open `devstudio/<id>/SKILL.md`; never copy into `.cursor` / `.claude` / `.vscode`
- Read only the matching Skill (`id | when`); read Skill before its tools:
  - (ids filled by init)
- MCP (IDE loads from `devstudio/ide/`; else run `devstudio` CLI):
  - devstudio/ide/cursor.mcp.json
<!-- devstudio:end -->
