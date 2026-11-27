"""Generate a printable overview DOCX from the product definition (simplified)."""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

OUT = Path(__file__).resolve().parents[1] / "docs" / "Anyka_DevStudio_产品概览.docx"


def _set_run_font(run, size=11, bold=False, color=None, name="微软雅黑"):
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    run.font.size = Pt(size)
    run.bold = bold
    if color is not None:
        run.font.color.rgb = color


def _p(doc, text, *, size=11, bold=False, center=False, space_after=6):
    para = doc.add_paragraph()
    para.paragraph_format.space_after = Pt(space_after)
    para.paragraph_format.space_before = Pt(0)
    if center:
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = para.add_run(text)
    _set_run_font(run, size=size, bold=bold)
    return para


def _heading(doc, text, level=1):
    para = doc.add_heading(text, level=level)
    for run in para.runs:
        _set_run_font(run, size=16 if level == 1 else 13, bold=True)
    return para


def _shade(cell, hex_color: str):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hex_color)
    shd.set(qn("w:val"), "clear")
    tcPr.append(shd)


def _cell_text(cell, text, *, size=10, bold=False, center=True, color=None):
    cell.text = ""
    para = cell.paragraphs[0]
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.LEFT
    para.paragraph_format.space_before = Pt(2)
    para.paragraph_format.space_after = Pt(2)
    run = para.add_run(text)
    _set_run_font(run, size=size, bold=bold, color=color)


def _box_row(doc, texts, fills, *, widths=None):
    table = doc.add_table(rows=1, cols=len(texts))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    for idx, (text, fill) in enumerate(zip(texts, fills)):
        cell = table.rows[0].cells[idx]
        _shade(cell, fill)
        _cell_text(cell, text, size=10, bold=True, color=RGBColor(0x1A, 0x1A, 0x1A))
        if widths:
            cell.width = Cm(widths[idx])
    doc.add_paragraph().paragraph_format.space_after = Pt(4)
    return table


def _arrow(doc, text="▼"):
    _p(doc, text, size=12, bold=True, center=True, space_after=2)


def add_architecture_figure(doc):
    _heading(doc, "架构总图", level=1)
    _p(doc, "DevStudio 是装进客户 IDE 的本机工具层：不自带模型、不新建 IDE、不托管网页。Agent 读 AGENTS.md 与 Skill，经 MCP / CLI 调用同一套工具。", size=10)

    _box_row(doc, ["客户 IDE（VS Code / Cursor / Claude Code / Codex）\nAgent 由客户自带 · 编辑 / Diff / 会话都在 IDE 内"], ["DCEAF7"], widths=[16])
    _arrow(doc, "MCP 工具组　　　　　　　　　devstudio CLI")
    _box_row(doc, ["L5 出口：MCP 服务 · CLI（同一 schema）"], ["BDD7EE"], widths=[16])
    _box_row(doc, ["L4 编排：状态加载 · Skill · 计划批准 · AGENTS.md"], ["C6E0B4"], widths=[16])
    _box_row(doc, ["L3 运行时：Policy Guard · 审计 · 证据链 L0–L5"], ["FFE699"], widths=[16])
    _box_row(
        doc,
        ["project", "knowledge", "rules", "source", "build", "device", "tune"],
        ["F8CBAD"] * 7,
        widths=[2.2] * 7,
    )
    _p(doc, "L2 工具集（按三状态裁剪加载）", size=9, center=True, space_after=4)
    _box_row(doc, ["L1 Adapter：sdk/adapters/<release_id>/（一芯片一版本）"], ["E2D5F1"], widths=[16])
    _box_row(doc, ["L0 执行：本机 / WSL / SSH · Studio TCP 7777"], ["D9D9D9"], widths=[16])
    _arrow(doc)
    _box_row(
        doc,
        ["芯片 SDK\n只读", "Celestia / 客户应用\n声明路径可写", "编译主机\nLinux / WSL / SSH", "开发板\n推送 · 日志 · 状态"],
        ["F2F2F2", "C6E0B4", "BDD7EE", "F8CBAD"],
        widths=[4, 4, 4, 4],
    )

    _p(doc, "三状态与工具组", size=11, bold=True, space_after=4)
    table = doc.add_table(rows=4, cols=3)
    table.style = "Table Grid"
    rows = [
        ("状态", "客户手里的源码", "加载的工具组"),
        ("sdk_celestia", "SDK + 完整 Celestia", "project / knowledge / rules / source / build / device / tune"),
        ("sdk_customer_app", "SDK + 客户自研应用", "前五组（无 device、tune）"),
        ("sdk_only", "只有芯片 SDK（FAE）", "project / knowledge / rules / build"),
    ]
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            cell = table.rows[r].cells[c]
            if r == 0:
                _shade(cell, "305496")
                _cell_text(cell, text, size=10, bold=True, color=RGBColor(255, 255, 255))
            else:
                _cell_text(cell, text, size=9, bold=c == 0, center=False)
    doc.add_paragraph()


def add_directory_figure(doc):
    _heading(doc, "目录总图", level=1)
    _p(doc, "DevStudio 仓库按 Celestia（产品分层）与 SDK（方案 + Adapter）分开，便于后续维护。", size=10)

    table = doc.add_table(rows=1, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    left, right = table.rows[0].cells
    _shade(left, "C6E0B4")
    _shade(right, "BDD7EE")
    left_text = (
        "celestia/　产品分层\n"
        "├ overview/　路由 celestia\n"
        "├ application/　应用 / feature\n"
        "├ framework/\n"
        "├ hardware/　硬件 + HAL\n"
        "├ system/　系统 + AOS 移植\n"
        "├ project/　产品 / bootstrap\n"
        "├ build/　脚本 / 第三方\n"
        "├ device/　固件·日志·状态\n"
        "└ repo/　贡献 / git-gitee"
    )
    right_text = (
        "sdk/　各 SDK 共用\n"
        "├ peripherals/　外设调试（cis / flash …）\n"
        "├ build/　编译分类\n"
        "├ tune/　memory / cpu\n"
        "└ adapters/<release_id>/\n"
        "　　catalog · index · errata\n"
        "　　packages · tune 阈值\n"
        "\n"
        "setup_windows.ps1\n"
        "setup_linux.sh / setup_macos.sh\n"
        "src/devstudio/　实现\n"
        "tests/　测试\n"
        "docs/　设计文档\n"
        "ide/　init 生成（不进 Git）"
    )
    _cell_text(left, left_text, size=9, bold=False, center=False)
    _cell_text(right, right_text, size=9, bold=False, center=False)
    for para in left.paragraphs + right.paragraphs:
        para.paragraph_format.space_before = Pt(4)
        para.paragraph_format.space_after = Pt(2)
    doc.add_paragraph()

    _p(doc, "归属原则", size=11, bold=True, space_after=4)
    bullets = [
        "应用增删改、HAL、AOS、设备推送 / 日志 / 状态 → celestia/",
        "CIS 移植、编译分类、调优、版本差异 → sdk/",
        "新增一个 SDK：只加 sdk/adapters/<release_id>/，核心代码零改动",
        "Skill 正文不复制到 .cursor / .claude / .vscode；AGENTS.md 只列路径",
    ]
    for item in bullets:
        para = doc.add_paragraph(style="List Bullet")
        run = para.add_run(item)
        _set_run_font(run, size=10)


def _table(doc, headers, rows):
    table = doc.add_table(rows=len(rows) + 1, cols=len(headers))
    table.style = "Table Grid"
    for c, text in enumerate(headers):
        cell = table.rows[0].cells[c]
        _shade(cell, "305496")
        _cell_text(cell, text, size=9, bold=True, color=RGBColor(255, 255, 255))
    for r, row in enumerate(rows, start=1):
        for c, text in enumerate(row):
            _cell_text(table.rows[r].cells[c], text, size=8, bold=c == 0, center=False)
    doc.add_paragraph()
    return table


def add_howto_use(doc):
    _heading(doc, "怎么使用", level=1)
    _p(doc, "DevStudio 装进现有 IDE：先按平台安装并初始化，再在 Agent 面板描述任务，或在终端直接跑命令。模型用 IDE 自带的，不配 API Key。", size=10)

    _p(doc, "1. 安装并初始化（在 DevStudio 目录）", size=11, bold=True, space_after=4)
    _table(
        doc,
        ["平台", "命令"],
        [
            ("Windows", "powershell -NoProfile -ExecutionPolicy Bypass -File .\\setup_windows.ps1"),
            ("Linux", "./setup_linux.sh"),
            ("macOS", "./setup_macos.sh"),
            ("只装运行时", "同上，追加 --install-only"),
            ("指定产品", "… --product anypi_nova"),
            ("客户应用+SDK", "… --sdk sdk\\AnyCloud39AV100_V2\\V2.05"),
        ],
    )
    _p(doc, "确认后写出：devstudio.yaml、AGENTS.md、devstudio/ide/ 下的 MCP 定义。未确认不写文件。", size=9)

    _p(doc, "2. 研发闭环：改代码 → 编译 → 推板 → 看 log / 状态 / RTSP", size=11, bold=True, space_after=4)
    _p(
        doc,
        "sdk_celestia 上日常以「改一处、编一次、推一块板、取一轮证据」为闭环。"
        "通道是 Studio TCP 7777（设备端 application/services/studio；主机用 DevStudio DeviceLink，"
        "看画面用 devstudio web）。完成等级：编译过关最多 L3；自动取数/推送成功到 L4；"
        "预览、RTSP、界面须人确认后到 L5。",
        size=9,
    )
    _p(doc, "板号 IP 怎么给（无网页填 IP）", size=10, bold=True, space_after=4)
    _table(
        doc,
        ["场景", "做法"],
        [
            (
                "对话已提 IP",
                "直接用，例如「推到 192.168.0.8」→ --board / MCP board=192.168.0.8",
            ),
            (
                "对话未提板号",
                "调用 device/app 工具 → 弹出桌面板号输入框等待用户填写；"
                "禁止聊天正文「请发板号」；勿臆造、勿静默挑板",
            ),
            (
                "终端未给 --board",
                "同样弹输入框；框不可用时 TTY input；仅登记一块板时作为默认值",
            ),
            (
                "输入框不可用",
                "输出 NEED_BOARD ASK_UI=board_ip → Agent 用 AskQuestion 表单，仍禁止纯聊天索要",
            ),
            (
                "登记 / 探测",
                "devstudio device connect [--board IP] → PING/PONG，写入 ~/.devstudio/work/<工程>/devices.json",
            ),
        ],
    )
    _table(
        doc,
        ["步骤", "做什么", "命令 / 做法"],
        [
            (
                "① 改代码",
                "先计划再写。模板/宏走工具；业务 .c 由 IDE Agent 写。",
                "devstudio app modify <id>   # 或 app add\n"
                "devstudio plan approve <task_id>\n"
                "devstudio app apply <task_id>\n"
                "# 对话示例：「改 recorder 增加水印，先出计划」",
            ),
            (
                "② 编译",
                "执行 yaml 里的 build.command；失败先分类日志。",
                "devstudio build --run\n"
                "devstudio build --log <编译日志>\n"
                "# 对话：「编译当前模块并分析日志」",
            ),
            (
                "③ 下载到设备（仅网口 7777）",
                "推送只走 Studio TCP。对话已提 IP 则带 --board；未提则先问用户输入。"
                "成功日志里的 OK usb / place=usb 表示落点 /mnt/install/apps（板上应用区），"
                "不是 PC 用 USB 线传文件。USB 线推送、串口推送均未实现。",
                "devstudio device connect --board 192.168.0.8\n"
                "devstudio device push <id> --board 192.168.0.8\n"
                "devstudio device firmware --board 192.168.0.8 --kind usr --file usr.sqsh4\n"
                "devstudio device firmware --board … --kind image --dir <镜像目录> [--yes]\n"
                "# 对话：「推到 192.168.0.8」或「推 recorder」（未提 IP 则先问）",
            ),
            (
                "④a log / 状态（网口）",
                "订阅 LOG、METRICS（仍走 7777）。缺板号同样先问。",
                "devstudio device log --board 192.168.0.8 [--seconds N]\n"
                "devstudio device status --board 192.168.0.8\n"
                "devstudio device capture --board … --seconds 5 --command \"复测预览\"\n"
                "# 对话：「看这块板的 log 和 CPU 内存」",
            ),
            (
                "④b log（串口，可选，不推送）",
                "串口只看启动/驱动等输出，不能代替 push。"
                "DevStudio capture --serial 目前提示未实现。实时画面用 devstudio web。",
                "外部：picocom -b 115200 /dev/ttyUSB0（或 COMx）\n"
                "# 对话：「串口看启动 log」（人工/外部工具）",
            ),
            (
                "⑤ 预览 / RTSP 出流",
                "工具侧用 log/capture 看出流与关键错误；RTSP 地址用播放器人眼确认。"
                "自动判 RTSP/帧稳定尚未单独成命令（capture 仍标 unverified: rtsp）。",
                "推板后：\n"
                "devstudio device log --board <板号> --seconds 20\n"
                "# 在 log 中查 RTSP/stream_reg、编码、断流等关键字\n"
                "devstudio device capture --board <板号> --seconds 20 --command \"rtsp复测\"\n"
                "# 播放器打开产品 RTSP URL（如 rtsp://<板IP>/…）≥20s\n"
                "# 对话：「推板后抓 20 秒 log，看 RTSP 有没有断流」\n"
                "# PASS 判据见 celestia-debug Skill（预览/出流 ≥20s）",
            ),
            (
                "⑥ 对比与结论",
                "同板、同命令的前后两次取数才可对比；没有两次不写优化结论。",
                "devstudio device compare <before> <after> [--check 项]\n"
                "devstudio tune memory|cpu --before <目录> --after <目录>\n"
                "devstudio report export <task_id>",
            ),
        ],
    )
    _p(
        doc,
        "一条命令串起来的典型终端闭环（板号请换成实板；也可省略 --board 由终端提示输入）：",
        size=9,
        bold=True,
        space_after=4,
    )
    _p(
        doc,
        "devstudio app apply t00xx\n"
        "devstudio build --run\n"
        "devstudio device connect --board 192.168.0.8\n"
        "devstudio device push recorder --board 192.168.0.8\n"
        "devstudio device log --board 192.168.0.8 --seconds 20\n"
        "devstudio device status --board 192.168.0.8\n"
        "devstudio device capture --board 192.168.0.8 --seconds 20 --command \"rtsp复测\"",
        size=8,
        space_after=8,
    )

    _p(doc, "3. 日常怎么用：对话命中 → 工具执行（不是 IDE 自动改写成 shell）", size=11, bold=True, space_after=4)
    points = [
        "主路径在 IDE Agent 面板：用人话描述任务。Agent 读 AGENTS.md 与 Skill，按意图选择 MCP 工具调用；"
        "MCP 与 CLI 共用同一套 tools，不是把你的话「翻译成一行 shell 再交给终端」。",
        "终端里的 devstudio … 是同一能力的第二入口：脚本、CI、排障、或未加载 MCP 时直接敲。",
        "命中靠约定：Skill 写清何时用、先问什么、何时停；AGENTS.md 列出可用 Skill 与禁写规则；"
        "init 写入的 ide MCP 配置决定当前状态能调哪些组。",
        "改工程：先 plan（待批准）→ 人确认 → apply。"
        "设备：对话已提板号则用；未提则先问用户输入，勿臆造、勿静默挑板。数字字段：只走 rules。",
        "看画面用 devstudio web；板上须有 application/services/studio（TCP 7777）。",
    ]
    for item in points:
        para = doc.add_paragraph(style="List Bullet")
        run = para.add_run(item)
        _set_run_font(run, size=9)

    _p(doc, "对话示例 → 通常命中的工具 / 命令", size=10, bold=True, space_after=4)
    _table(
        doc,
        ["你在 Agent 里说（示例）", "通常命中", "等价 CLI（可选手敲）"],
        [
            ("这一版支持哪些 MIPI Sensor？", "knowledge.peripheral_list", "devstudio knowledge peripherals --type sensor"),
            ("查一下 AK_MPI_xxx 在哪", "knowledge.api_lookup", "devstudio knowledge api --symbol …"),
            ("给当前产品加一个 recorder 应用，先出计划", "source 计划 / app 流程（经 Skill）", "devstudio app add recorder … → plan approve → app apply"),
            ("按这份序列适配 GC5603", "rules.cis_generate + plan", "devstudio plan sensor …\ndevstudio rules generate …"),
            ("编译当前模块并看日志", "build.build / build_log", "devstudio build --run\ndevstudio build --log <文件>"),
            ("连接 192.168.0.8 这块板", "device.connect", "devstudio device connect --board 192.168.0.8"),
            ("推到 192.168.0.8（仅网口 7777）", "device.push_app", "devstudio device push <id> --board 192.168.0.8\n（USB线/串口不能 push；OK usb=落点/mnt/install）"),
            ("推 recorder（未提 IP）", "先问板号 → device.push_app", "终端省略 --board 会提示输入；对话则先问用户"),
            ("看板上 CPU 和内存（网口）", "device.device_status", "devstudio device status [--board …]"),
            ("串口看启动 log（不推送）", "—（CLI 未采串口）", "picocom -b 115200 <串口>（无 Studio 网页）"),
            ("推板后抓 log 看 RTSP/预览", "device.device_log / capture", "devstudio device log|capture --board … --seconds 20"),
            ("对比优化前后内存", "tune.tune_memory", "devstudio tune memory --before … --after …"),
        ],
    )

    _p(doc, "4. 命令速查（含闭环；工程根目录，终端备用）", size=11, bold=True, space_after=4)
    _table(
        doc,
        ["场景", "命令"],
        [
            ("看版本 / Adapter", "devstudio --version\ndevstudio adapter list"),
            ("工程信息", "devstudio project info"),
            ("切产品 / SDK", "devstudio switch list\ndevstudio switch product anypi_nova"),
            ("查 CIS 列表", "devstudio knowledge peripherals --type sensor"),
            ("检索 / API / 勘误", "devstudio knowledge search|api|errata|index …"),
            ("Sensor 确认与出包", "devstudio sensor confirm|build --model <型号> [--cis 模式]"),
            ("数字字段规则生成", "devstudio rules generate --task t00xx --ref <参考> --model <目标> [--sequence 文件]"),
            ("计划批准", "devstudio plan show|approve <task_id>"),
            ("① 改代码（应用）", "devstudio app add|modify|remove|check <id>\ndevstudio app apply <task_id>"),
            ("HAL（客户应用）", "devstudio hal plan --module … --header …\ndevstudio hal apply <task_id>"),
            ("② 编译", "devstudio build --run\ndevstudio build --log <文件>"),
            ("板号 / 连接（无网页）", "device connect [--board IP]\n未给 --board：弹桌面输入框；不可用则 TTY / NEED_BOARD ASK_UI；禁止聊天要板号"),
            ("③ 推应用/固件（仅网口7777）", "device push <id> [--board IP]\ndevice firmware [--board IP] --kind usr|image …\nUSB线/串口不能 push；OK usb=落点/mnt/install"),
            ("④a 日志/CPU（网口）", "device log|status [--board IP]\ndevice metrics；device capture [--board …]"),
            ("④b 串口 log（不推送）", "picocom -b 115200 <串口>\ncapture --serial 未实现；实时画面用 devstudio web"),
            ("⑤ RTSP / 预览核对", "device log|capture 看关键字 + 播放器打开 rtsp://<板IP>/…\n（尚无独立 rtsp 子命令；自动判流未纳入 L4）"),
            ("⑥ 对比 / 调优 / 报告", "devstudio device compare <before> <after>\ndevstudio tune memory|cpu --before … --after …\ndevstudio report export <task_id>"),
            ("启动 MCP（单组）", "devstudio mcp --group project|knowledge|rules|source|build|device|tune"),
        ],
    )



def add_tools_and_extension(doc):
    _heading(doc, "MCP 工具组与 CLI（及如何扩展）", level=1)

    _p(doc, "MCP 与 CLI 各干什么", size=11, bold=True, space_after=4)
    _table(
        doc,
        ["入口", "作用", "谁用"],
        [
            (
                "MCP",
                "给 IDE 里的 Agent 调用的工具面：按组暴露 schema（参数、说明）。"
                "Agent 选工具、填参数、收结果；不负责聊天本身。",
                "Cursor / VS Code / Claude Code / Codex 等已接 MCP 的 IDE",
            ),
            (
                "CLI（devstudio）",
                "同一能力的命令行入口：人、脚本、CI 直接执行。"
                "未加载 MCP 时也能完整跑通；init/switch 等编排命令主要在 CLI。",
                "终端、自动化、排障、批处理",
            ),
            (
                "共用实现",
                "tools/registry.py 一张表：MCP 工具名 ↔ CLI argv ↔ run_*。"
                "同输入同结果；业务只写一份。",
                "扩展时改这一处即可两边生效（再视需要补 CLI 分发）",
            ),
        ],
    )

    _p(doc, "src/devstudio 目录职责", size=11, bold=True, space_after=4)
    _table(
        doc,
        ["目录", "职责"],
        [
            ("cli/", "命令行入口 main.py：解析子命令，转到各 run_*"),
            ("mcp/", "MCP stdio 服务：按 --group 暴露一组工具"),
            ("tools/", "业务实现：project / knowledge / rules / source / build / device / tune"),
            ("tools/registry.py", "ToolSpec 表：组名、工具名、schema、映射到 CLI argv"),
            ("rules/", "数字字段唯一写入（cis_adapt 等）；模型不得直接写寄存器/ID/VTS/序列"),
            ("orchestration/", "init / switch / 状态与 Skill 名单 / 写 AGENTS.md 与 ide MCP"),
            ("adapter/", "读 sdk/adapters/<release_id>/（catalog、index、errata）"),
            ("runtime/", "Policy Guard：批准、路径、文本、数字字段扫描"),
            ("exec/", "本机 / WSL / SSH 执行编译命令"),
        ],
    )

    _p(doc, "七个 MCP 工具组（当前已挂工具）", size=11, bold=True, space_after=4)
    _table(
        doc,
        ["组", "MCP 工具名", "对应 CLI / 说明", "状态"],
        [
            ("project", "project_info", "devstudio project info", "C A S"),
            ("knowledge", "peripheral_list", "knowledge peripherals", "C A S"),
            ("knowledge", "doc_search / api_lookup / errata_lookup", "knowledge search|api|errata", "C A S"),
            ("knowledge", "index_headers", "knowledge index", "C A S"),
            ("rules", "numeric_check", "扫描文本/文件中的数字字段", "C A S"),
            ("rules", "cis_generate", "rules generate …", "C A S"),
            ("source", "plan_status / apply", "plan show；apply", "C A"),
            ("source", "hal_plan / driver_diff", "hal plan；driver diff", "C A"),
            ("build", "build / build_log", "build --run；build --log", "C A S"),
            ("device", "connect / push_app / metrics / capture / compare", "device connect|push|metrics|capture|compare", "C"),
            ("device", "device_log / device_status / device_firmware", "device log|status|firmware（board 可选，未提先问）", "C"),
            ("tune", "tune_memory / tune_cpu", "tune memory|cpu", "C"),
        ],
    )
    _p(doc, "C=sdk_celestia　A=sdk_customer_app　S=sdk_only。init 只加载当前状态的组。", size=9)

    _p(doc, "CLI 比 MCP 多出来的常用命令（仍走同一套 tools）", size=11, bold=True, space_after=4)
    bullets = [
        "编排：init、switch、adapter list、mcp --group",
        "知识/驱动：driver locate、sensor confirm|build、knowledge spec",
        "源码：plan sensor|approve|review|merge|merge-apply、app add|modify|remove|check|apply|recipe、hal apply、verify",
        "报告：report export",
    ]
    for item in bullets:
        para = doc.add_paragraph(style="List Bullet")
        run = para.add_run(item)
        _set_run_font(run, size=9)

    _p(doc, "还可以扩展什么、怎么扩（举例）", size=11, bold=True, space_after=4)
    _table(
        doc,
        ["想扩展的能力（例子）", "落点", "怎么做"],
        [
            (
                "例：device reboot --board\n让 Agent/CLI 能远程重启板子",
                "tools/device/ + registry + cli",
                "在 observe 或 cmd 里加 run_reboot；registry 增 device_reboot；"
                "cli USAGE 加一行。IDE 对话「重启这块板」即可命中。",
            ),
            (
                "例：knowledge 增加 Flash 外设列表\n「这版支持哪些 SPI Flash？」",
                "Adapter catalog + peripherals",
                "在当前 release_id 的 catalog.json 加 flash 条目；"
                "peripherals 已按 type 过滤则可直接查。不必改 MCP 组。",
            ),
            (
                "例：新芯片 AnyCloud39AVxxx V2.00",
                "sdk/adapters/",
                "新建 adapters/AnyCloud39AVxxx_V2.00/："
                "adapter.yaml、catalog.json、errata.json=[]，再 knowledge index。"
                "核心代码零改动。",
            ),
            (
                "例：ISP 调参规则（数字字段）",
                "rules/",
                "新增 rules/isp_adapt.py（或 tools/rules）；只允许 rules 写增益/曝光等；"
                "guard 扫描要能标出；对外用 rules generate 同类子命令。",
            ),
            (
                "例：穿戴应用增删改 Skill 更细",
                "celestia/application/",
                "改 application-feature/SKILL.md 步骤；或新 Skill 目录；"
                "render.py 的 SKILLS 挂上；init 后 AGENTS.md 出现路径。",
            ),
            (
                "例：独立「仿真」工具组 sim",
                "新组 tools/sim/ + GROUPS",
                "① 写 run_*；② registry group=sim；③ mcp GROUPS 与 render GROUPS "
                "按状态挂上；④ 再 init。勿另建第二套 agents。",
            ),
            (
                "例：把已有 CLI app recipe 暴露给 Agent",
                "仅 registry",
                "已有 run_app：在 registry 加 source/app_recipe 的 ToolSpec，"
                "argv 映射到 recipe。不必重写业务。",
            ),
        ],
    )
    _p(
        doc,
        "扩展原则：不新建第二套 agents/commands；不把 Skill/MCP 复制进 .cursor/.claude/.vscode；"
        "会改工程的先计划后批准；检索只绑当前 release_id；设备板号：对话已提则用，未提则先问用户输入。",
        size=9,
    )


def add_product_body(doc):
    _heading(doc, "产品定位", level=1)
    _p(doc, "一句话：从芯片到产品——让客户 IDE 里的 Agent，在当前芯片、当前 SDK 版本、当前源码组合下完成研发动作，并给出可核对的证据。")
    points = [
        "交付的是工具层：MCP、devstudio CLI、AGENTS.md、Skill、按芯片版本的 Adapter。",
        "不新建 IDE、不托管模型、不迁移 FAE 网页/JWT/管理后台。",
        "硬件数字字段（寄存器、Sensor ID、VTS、帧率、初始化序列）只由 rules 生成，模型不得直接写。",
        "检索只读当前 release_id；没有记录就写「未收录」，不从邻近版本补。",
        "会改工程的操作先写计划，人批准后再 apply；设备操作必须带板号。",
    ]
    for item in points:
        para = doc.add_paragraph(style="List Bullet")
        run = para.add_run(item)
        _set_run_font(run, size=10)

    _heading(doc, "谁用、干什么", level=1)
    table = doc.add_table(rows=5, cols=2)
    table.style = "Table Grid"
    rows = [
        ("角色", "典型工作"),
        ("产品 / 应用", "新建产品、增删改平板应用、编译、推板"),
        ("驱动 / BSP", "查 CIS 支持列表、列表内出包、列表外方案、规则生成数字字段"),
        ("FAE", "只有 SDK 时检索接口与外设、出调试包、编译分类"),
        ("客户应用团队", "按自有接口写 HAL，用客户声明的编译命令"),
    ]
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            cell = table.rows[r].cells[c]
            if r == 0:
                _shade(cell, "305496")
                _cell_text(cell, text, size=10, bold=True, color=RGBColor(255, 255, 255))
            else:
                _cell_text(cell, text, size=9, center=False)
    doc.add_paragraph()

    _heading(doc, "主路径（摘要）", level=1)
    paths = [
        ("首次加载", "devstudio init → 确认状态 → 写出 yaml / AGENTS.md / ide MCP 配置"),
        ("CIS 移植", "peripherals / sensor confirm → plan → rules → apply → build"),
        ("应用增删改", "app add|modify|remove → 批准 → apply → build → device push（未提板号先问）"),
        ("HAL（客户应用）", "hal plan → 批准 → hal apply（只写收录符号）→ 客户编译命令"),
        ("用户区 / 整包", "device firmware --kind usr|image …（不使用 burntool；引导分区不写）"),
        ("观测与调优", "device log|status --board；tune memory|cpu 比较同板同命令两次读数"),
    ]
    table = doc.add_table(rows=len(paths) + 1, cols=2)
    table.style = "Table Grid"
    header = table.rows[0].cells
    _shade(header[0], "305496")
    _shade(header[1], "305496")
    _cell_text(header[0], "路径", size=10, bold=True, color=RGBColor(255, 255, 255))
    _cell_text(header[1], "步骤", size=10, bold=True, color=RGBColor(255, 255, 255))
    for i, (name, step) in enumerate(paths, start=1):
        _cell_text(table.rows[i].cells[0], name, size=9, bold=True, center=False)
        _cell_text(table.rows[i].cells[1], step, size=9, center=False)
    doc.add_paragraph()

    _heading(doc, "完成等级（证据链）", level=1)
    levels = [
        "L0 计划　L1 代码生成　L2 静态检查　L3 编译通过　L4 自动测试　L5 实机验证（人确认）",
        "L1–L3 不得写成功能完成；绕过工具的改动不计等级。",
    ]
    for item in levels:
        para = doc.add_paragraph(style="List Bullet")
        run = para.add_run(item)
        _set_run_font(run, size=10)

    _heading(doc, "边界（不做）", level=1)
    bans = [
        "不配置、调用或托管大模型；不自建网页工作台 / JWT / 管理后台。",
        "不发明寄存器、Sensor ID、VTS、帧率或初始化序列。",
        "不改 Makefile / Kconfig / config.mk 的模块开关（除应用模板工具）。",
        "不在运行中的系统里写 SPL / OPENSBI / ENV / DTB 等引导分区。",
        "不把 Skill 或 MCP 定义复制进 .cursor / .claude / .vscode。",
    ]
    for item in bans:
        para = doc.add_paragraph(style="List Bullet")
        run = para.add_run(item)
        _set_run_font(run, size=10)

    _heading(doc, "配套文档", level=1)
    docs = [
        ("Anyka_DevStudio_产品定义.md", "完整 PRD、场景、功能清单、竞品"),
        ("Anyka_DevStudio_使用说明.md", "安装、三状态日常用法、命令速查"),
        ("Anyka_DevStudio_总体与架构设计.md", "分层、契约、Adapter、部署与验收（已合入原总体设计方案）"),
        ("设计步骤.md", "S0 起逐步实现与验证记录"),
    ]
    table = doc.add_table(rows=len(docs) + 1, cols=2)
    table.style = "Table Grid"
    _shade(table.rows[0].cells[0], "305496")
    _shade(table.rows[0].cells[1], "305496")
    _cell_text(table.rows[0].cells[0], "文档", size=10, bold=True, color=RGBColor(255, 255, 255))
    _cell_text(table.rows[0].cells[1], "内容", size=10, bold=True, color=RGBColor(255, 255, 255))
    for i, (name, desc) in enumerate(docs, start=1):
        _cell_text(table.rows[i].cells[0], name, size=9, center=False)
        _cell_text(table.rows[i].cells[1], desc, size=9, center=False)


def build() -> Path:
    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(1.8)
    section.right_margin = Cm(1.8)
    section.top_margin = Cm(1.6)
    section.bottom_margin = Cm(1.6)

    _p(doc, "Anyka DevStudio", size=22, bold=True, center=True, space_after=4)
    _p(doc, "产品概览（可打印）", size=16, bold=True, center=True, space_after=8)
    _p(doc, "From Chip to Product, Powered by AI.", size=11, center=True, space_after=2)
    _p(doc, "从芯片到产品，让 AI 赋能研发。", size=11, center=True, space_after=8)
    _p(
        doc,
        "依据《Anyka_DevStudio_产品定义》简化。含架构/目录总图、研发闭环（改代码→编译→推板→log/状态/RTSP）、"
        "使用命令、板号询问规则、MCP与CLI及扩展说明。详细契约与步骤见配套四份 Markdown。",
        size=9,
        center=True,
        space_after=12,
    )

    add_architecture_figure(doc)
    add_directory_figure(doc)
    add_howto_use(doc)
    add_tools_and_extension(doc)
    add_product_body(doc)

    footer = doc.sections[0].footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run("Anyka DevStudio 产品概览 · 内部阅读 · 与 src 不一致时以仓库实现为准")
    _set_run_font(run, size=8, color=RGBColor(0x66, 0x66, 0x66))

    doc.save(OUT)
    return OUT


if __name__ == "__main__":
    path = build()
    print(path)
    print("bytes", path.stat().st_size)
