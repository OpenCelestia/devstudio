import json
import re
import shutil
import sys
from pathlib import Path

from devstudio.tools.source.plan_cmd import next_task_id
from devstudio.workspace import tasks_dir
from devstudio.yamlio import load_yaml

SYSTEM_APPS = {"ipc_camera", "camera", "audio_algo", "setting", "volc_engine", "studio"}
ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
FORMS = {
    "TABLET": ("application/packages/tablet/{id}/", "app_entry_start_{id}", "CONFIG_APP_TABLET_{ID}"),
    "WEARABLE": ("application/packages/wearable/{id}/", "app_entry_start_{id}", "CONFIG_APP_WEARABLE_{ID}"),
    "IPCAMERA": ("application/packages/ipcamera/{id}/", "module_init", "settings.json type:so"),
    "CLOUD": ("application/cloud/{id}/", "module_init", "settings.json type:so"),
}
BANNED_LVGL = re.compile(
    r"\blv_(?:canvas|imagebutton|animimg|animimage|barcode|qrcode|imgfont|ime_pinyin|"
    r"file_explorer|fragment|snapshot|gif|lottie|tiny_ttf|3dtexture|calendar_chinese)\w*\s*\("
)
HAL_INCLUDE = re.compile(r'#\s*include\s*[<"](?:hardware|camera|display|gpio|wifi)[/"]')
ICON_LOOKS = (
    (re.compile(r"录音|麦克风|record|mic|voice", re.I), "mic", "", "#FF8A80", "#E53950"),
    (re.compile(r"音乐|播放|music|player", re.I), "glyph", "audio", "#FF7AB6", "#D6246E"),
    (re.compile(r"视频|video|movie", re.I), "glyph", "video", "#FF9E6D", "#E8562A"),
    (re.compile(r"相册|图片|photo|gallery|image", re.I), "glyph", "image", "#6FC3FF", "#2A7DE1"),
    (re.compile(r"日历|清单|calendar|list", re.I), "glyph", "list", "#7FDCB4", "#25A06E"),
    (re.compile(r"聊天|对话|chat", re.I), "chat", "", "#8EC5FC", "#5B6CF0"),
    (re.compile(r"游戏|game", re.I), "gamepad", "", "#FFB86B", "#EE6A24"),
    (re.compile(r"相机|camera", re.I), "camera", "", "#FFA07A", "#F0533A"),
    (re.compile(r"设置|setting", re.I), "glyph", "settings", "#B8BECB", "#7A8292"),
)


def _recipe(argv: list[str]) -> int:
    form = ""
    args = list(argv)
    while args:
        item = args.pop(0)
        if item == "--form" and args:
            form = args.pop(0)
        else:
            sys.stderr.write("用法：devstudio app recipe [--form 形态]\n")
            return 2
    if form and form.upper() not in FORMS:
        sys.stdout.write("不从空目录生成多媒体框架。组合只用 TABLET、WEARABLE、IPCAMERA、CLOUD 模板。未新建目录。\n")
        return 1
    sys.stdout.write("应用方案只组合已有模板。未新建目录。\n")
    for name, (folder, entry, switch) in FORMS.items():
        sys.stdout.write(f"{name}：{folder}　入口 {entry}　{switch}\n")
    return 0


def run_app(argv: list[str], project: Path | None = None) -> int:
    if argv and argv[0] == "recipe":
        return _recipe(argv[1:])
    if not argv or argv[0] not in ("add", "modify", "remove", "apply", "check"):
        sys.stderr.write(
            "用法：devstudio app add <id> [--name-zh 中文] [--name-en 英文] [--need 需求] [--form cloud] [--yes] [--build]\n"
            "      devstudio app modify|remove <id> [--board 板号] [--yes]\n"
            "      devstudio app check <id>\n"
            "      devstudio app apply <task_id>\n"
            "      devstudio app recipe [--form 形态]\n"
        )
        return 2
    action = argv[0]
    if action == "add":
        return run_app_add(argv[1:], project)
    if action == "check":
        return _check_argv(argv[1:], project)
    if action == "modify":
        return _plan_only(argv[1:], project, "modify")
    if action == "remove":
        return _plan_only(argv[1:], project, "remove")
    return _apply_app(argv[1:], project)


def run_app_add(argv: list[str], project: Path | None = None) -> int:
    form_arg = None
    name_zh = None
    name_en = None
    need = ""
    app_id = None
    yes = False
    build = False
    args = list(argv)
    while args:
        item = args.pop(0)
        if item == "--form" and args:
            form_arg = args.pop(0).upper()
        elif item == "--name-zh" and args:
            name_zh = args.pop(0)
        elif item == "--name-en" and args:
            name_en = args.pop(0)
        elif item in ("--need", "--需求") and args:
            need = args.pop(0)
        elif item == "--yes":
            yes = True
        elif item == "--build":
            build = True
        elif app_id is None and not item.startswith("--"):
            app_id = item
        else:
            sys.stderr.write(
                "用法：devstudio app add <id> [--name-zh 中文] [--name-en 英文] [--need 需求] [--form cloud] [--yes] [--build]\n"
            )
            return 2
    if not app_id:
        sys.stderr.write(
            "用法：devstudio app add <id> [--name-zh 中文] [--name-en 英文] [--need 需求] [--form cloud] [--yes] [--build]\n"
        )
        return 2
    project = (project or Path.cwd()).resolve()
    config_path = project / "devstudio.yaml"
    if not config_path.is_file():
        sys.stderr.write("当前目录没有 devstudio.yaml。先执行 devstudio init。\n")
        return 1
    config = load_yaml(config_path)
    if config.get("state") != "sdk_celestia":
        sys.stderr.write("只有 sdk_celestia 可以新增应用。\n")
        return 1
    if not ID_RE.fullmatch(app_id):
        sys.stderr.write("应用 id 用小写字母开头，只含小写字母、数字、下划线。\n")
        return 1
    if app_id in SYSTEM_APPS:
        sys.stderr.write(f"{app_id} 是系统应用，不能新增。\n")
        return 1
    product = config.get("project", {}).get("name")
    mk = project / "project" / product / "config.mk" if product else Path()
    form = form_arg if form_arg == "CLOUD" else _form(mk.read_text(encoding="utf-8", errors="replace") if mk.is_file() else "")
    if form not in FORMS:
        sys.stderr.write("config.mk 里没有平板、穿戴或 IPC 形态开关。\n")
        return 1
    folder, entry, switch = _named(form, app_id)
    if (project / folder).exists():
        sys.stdout.write(f"目录已存在：{folder}。继续用 devstudio app modify {app_id}，或先删除后再新增。\n")
        return 1
    display = f"{name_zh}({name_en})" if name_zh and name_en else app_id
    task_id = next_task_id(project)
    task_dir = tasks_dir(project) / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "plan.md").write_text(
        f"# 计划 {task_id}：新增应用 {app_id}\n状态：待批准\n\n"
        f"## 目标\n新增 {form} 应用 {app_id}。显示名：{display}。批准前不写源码。\n\n"
        "## 工具写入\n"
        "app.json、入口和头文件由工具写。模型只写业务 .c。不改 Makefile。\n"
        "平板入口符号是 app_entry_start_<id>(lv_obj_t *main)，界面函数是 tablet_<id>_create(lv_obj_t *main)。\n"
        "不要 #include hardware、camera、display、gpio、wifi。不要用 lv_canvas、lv_imagebutton、lv_animimg、lv_gif。\n",
        encoding="utf-8",
    )
    task = {
        "task_id": task_id,
        "title": f"新增应用 {app_id}",
        "release_id": config["release_id"],
        "state": config["state"],
        "level": "L0",
        "app_id": app_id,
        "action": "add",
        "form": form,
        "folder": folder,
        "name": display,
    }
    (task_dir / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    flag = {
        "TABLET": "CONFIG_APP_PACKAGES_TABLET",
        "WEARABLE": "CONFIG_APP_PACKAGES_WEARABLE",
        "IPCAMERA": "CONFIG_APP_PACKAGES_IPCAMERA",
        "CLOUD": "CONFIG_APP_PACKAGES_CLOUD",
    }[form]
    sys.stdout.write(f"形态：{form}（{flag}=y）\n" if form != "CLOUD" else "形态：CLOUD\n")
    sys.stdout.write(f"目录：{folder}\n")
    sys.stdout.write(f"入口：{entry}　开关：{switch}\n")
    sys.stdout.write(f"显示名：{display}\n")
    if form == "TABLET":
        sys.stdout.write(f"界面函数：tablet_{app_id}_create\n")
        sys.stdout.write(f"模板写入：app.json  app_entry_{app_id}.c  tablet_{app_id}.h\n")
        sys.stdout.write(f"模型可写：tablet_{app_id}.c 及同目录 .c/.h\n")
    if not yes:
        from devstudio.ui_prompt import confirm_or_tty

        summary = (
            f"新增 {form} 应用 {app_id}\n显示名：{display}\n目录：{folder}\n"
            f"将写入模板并打开 {switch}。"
        )
        yes = confirm_or_tty(
            "DevStudio 批准计划",
            f"计划 {task_id}\n\n{summary}\n\n是否批准并写入？",
            tty_prompt="批准并写入模板？[y/N] ",
        )
    if not yes:
        sys.stdout.write(f"已创建计划 {task_id}，用户未批准。可稍后：devstudio plan approve {task_id} && devstudio app apply {task_id}\n")
        return 0
    code = _approve_and_apply(project, task_id)
    if code != 0:
        return code
    return _finish_add(project, folder, app_id, form, task_id, need, build)


def _approve_and_apply(project: Path, task_id: str) -> int:
    from devstudio.tools.source.plan_cmd import load_task

    plan_path = tasks_dir(project) / task_id / "plan.md"
    text = plan_path.read_text(encoding="utf-8")
    plan_path.write_text(text.replace("状态：待批准", "状态：已批准", 1), encoding="utf-8")
    loaded = load_task(project, task_id)
    if loaded is not None:
        path, task = loaded
        task["plan_status"] = "已批准"
        path.write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(f"已批准 {plan_path}\n")
    return _apply_app([task_id], project)


def _finish_add(project: Path, folder: Path, app_id: str, form: str, task_id: str, need: str, build: bool) -> int:
    code = _generate_business(project, folder, app_id, form, task_id, need)
    if code != 0:
        return code
    if build:
        from devstudio.tools.build.classify import run_build

        sys.stdout.write("\n── 编译 ──\n")
        return run_build(["--run"], project)
    return 0


def _generate_business(project: Path, folder: Path, app_id: str, form: str, task_id: str, need: str) -> int:
    """Leave business-code generation to the Agent already running in the IDE."""
    del project, folder, form, task_id
    requirement = f"需求：{need}\n" if need else ""
    sys.stdout.write(
        "\n模板已生成。DevStudio 不配置、调用或托管大模型。\n"
        "请由当前 VS Code/Cursor 的 Agent 编写业务代码：\n"
        f"{requirement}"
        f"- 主文件：tablet_{app_id}.c\n"
        f"- 必须实现：lv_result_t tablet_{app_id}_create(lv_obj_t *main)\n"
        "- 只写当前应用目录内的业务 .c/.h，不修改工具生成的登记文件\n"
        "- 完成后执行 devstudio app check "
        f"{app_id}，再执行 devstudio build --run\n"
    )
    return 0


def _plan_only(argv: list[str], project: Path | None, action: str) -> int:
    yes = False
    board = None
    args: list[str] = []
    pending = list(argv)
    while pending:
        item = pending.pop(0)
        if item in ("--yes", "--confirm"):
            yes = True
        elif item == "--board" and pending:
            board = pending.pop(0)
        elif item.startswith("--"):
            sys.stderr.write(
                f"用法：devstudio app {action} <id> [--board 板号] [--yes|--confirm]\n"
            )
            return 2
        else:
            args.append(item)
    if len(args) != 1:
        sys.stderr.write(
            f"用法：devstudio app {action} <id> [--board 板号] [--yes|--confirm]\n"
        )
        return 2
    project = (project or Path.cwd()).resolve()
    app_id = _resolve_app_id(project, args[0], action=action)
    if app_id is None:
        return 1
    if app_id in SYSTEM_APPS:
        sys.stderr.write(f"{app_id} 是系统应用，不能{ '删除' if action == 'remove' else '修改' }。\n")
        return 1
    # 删除必须先有板号：IDE 下输出 NEED_BOARD，Agent 用运行中输入收集后再带 --board 重试
    if action == "remove" and not (board or "").strip():
        board = _board_of(project, None)
        if not (board or "").strip():
            sys.stdout.write(
                f"NEED_BOARD ASK_UI=board_ip：删除 {app_id} 须指定板号。"
                f"用 IDE 输入控件收集 IP 后执行："
                f"devstudio app remove {app_id} --board <IP> [--yes]\n"
                "未获板号：不删源码、不删设备。\n"
            )
            return 1
    found = _locate(project, app_id)
    task_id = next_task_id(project)
    task_dir = tasks_dir(project) / task_id
    task_dir.mkdir(parents=True)
    verb = "修改" if action == "modify" else "删除"
    folder = found[1] if found else ""
    if action == "modify" and found:
        _snapshot(project / folder, task_dir / "snapshot")
    (task_dir / "plan.md").write_text(
        f"# 计划 {task_id}：{verb}应用 {app_id}\n状态：待批准\n\n"
        f"## 目标\n{verb}应用 {app_id}。批准前不改源码。不改 app_id。\n"
        + (f"## 板号\n{board}\n" if board else ""),
        encoding="utf-8",
    )
    config = load_yaml(project / "devstudio.yaml") if (project / "devstudio.yaml").is_file() else {}
    task = {
        "task_id": task_id,
        "title": f"{verb}应用 {app_id}",
        "release_id": config.get("release_id", ""),
        "state": config.get("state", ""),
        "level": "L0",
        "app_id": app_id,
        "action": action,
        "form": found[0] if found else "",
        "folder": folder,
        "board": board or "",
    }
    (task_dir / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if action == "modify" and found:
        sys.stdout.write(f"已快照到数据目录。\n")
    elif action == "modify":
        sys.stdout.write(f"没有应用目录 {app_id}。批准后不会改文件。\n")
    if not yes:
        from devstudio.ui_prompt import confirm_or_tty, prefer_ide_ask

        verb = "修改" if action == "modify" else "删除"
        yes = confirm_or_tty(
            "DevStudio 批准计划",
            f"计划 {task_id}\n\n{verb}应用 {app_id}\n"
            f"目录：{folder or '（无本地目录，可能仅清设备）'}\n"
            f"板号：{board or '（未指定）'}\n\n是否批准并执行？",
            tty_prompt=f"批准并{verb}？[y/N] ",
        )
        if not yes and prefer_ide_ask():
            sys.stdout.write(
                f"已创建计划 {task_id}。Agent：用 IDE 选择「是」后执行："
                f"devstudio app {action} {app_id}"
                + (f" --board {board}" if board else "")
                + " --yes\n"
            )
            return 1
    if not yes:
        sys.stdout.write(
            f"已创建计划 {task_id}，用户未批准。可稍后：devstudio plan approve {task_id} "
            f"&& devstudio app apply {task_id}\n"
        )
        return 0
    return _approve_and_apply(project, task_id)


def _apply_app(argv: list[str], project: Path | None) -> int:
    from devstudio.runtime.guard import approval_block
    from devstudio.tools.source.plan_cmd import load_task

    if len(argv) != 1:
        sys.stderr.write("用法：devstudio app apply <task_id>\n")
        return 2
    project = (project or Path.cwd()).resolve()
    task_id = argv[0]
    loaded = load_task(project, task_id)
    if loaded is None:
        sys.stderr.write(f"没有任务 {task_id}。\n")
        return 1
    blocked = approval_block(project, task_id)
    if blocked:
        sys.stdout.write(blocked)
        return 1
    _path, task = loaded
    app_id = task.get("app_id", "")
    action = task.get("action", "add")
    folder = project / task.get("folder", "") if task.get("folder") else None
    if action == "remove":
        return _apply_remove(project, app_id, folder, task.get("board") or None)
    if action != "add":
        if folder is None or not folder.is_dir():
            sys.stdout.write("没有应用目录，未修改。\n")
            return 1
        if task.get("form") == "TABLET":
            _write_tablet_scaffold(folder, app_id, task.get("name") or _existing_name(folder, app_id), keep_business=True)
        notes = _check_dir(folder, app_id, task.get("form"))
        sys.stdout.write("修改不自动改业务代码。入口和 app.json 的固定字段已按模板核对。\n")
        for note in notes:
            sys.stdout.write(note + "\n")
        return 1 if notes else 0
    if folder is None:
        sys.stderr.write("计划里没有应用目录。\n")
        return 1
    folder.mkdir(parents=True, exist_ok=True)
    form = task.get("form")
    if form == "TABLET":
        _write_tablet_scaffold(folder, app_id, task.get("name") or app_id, keep_business=False)
        switched = _enable_macro(project, f"CONFIG_APP_TABLET_{app_id.upper()}")
        if switched:
            sys.stdout.write(f"已打开 CONFIG_APP_TABLET_{app_id.upper()}\n")
        else:
            sys.stdout.write("未找到 config.mk，开关未写入。\n")
    else:
        (folder / "app.json").write_text(
            json.dumps({"app_id": app_id, "form": form}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    (folder / ".devstudio-managed").write_text("managed\n", encoding="utf-8")
    sys.stdout.write(f"已写入模板 {folder}\n")
    sys.stdout.write("业务 .c 留给模型写。\n")
    return 0


def _apply_remove(project: Path, app_id: str, folder: Path | None, board: str | None = None) -> int:
    if app_id in SYSTEM_APPS:
        sys.stdout.write(f"{app_id} 是系统应用，未删除。\n")
        return 1
    # 先拿板号再动任何文件：缺板号则整单失败（避免只删电脑、设备残留）
    chosen = _board_of(project, board)
    if not chosen:
        sys.stdout.write(
            f"NEED_BOARD ASK_UI=board_ip：删除 {app_id} 须指定板号。"
            f"用 IDE 输入控件收集后："
            f"devstudio app remove {app_id} --board <IP> --yes\n"
            "或：devstudio app apply <task_id> 前把 task.json 的 board 写上。\n"
            "未获板号：不删源码、不删设备、不改 config。\n"
        )
        return 1
    device_code = _remove_on_device(project, app_id, chosen)
    if device_code != 0:
        sys.stdout.write("设备删除未成功，保留本地源码与配置，便于重试。\n")
        return device_code
    macro = f"CONFIG_APP_TABLET_{app_id.upper()}"
    if _strip_macro(project, macro):
        sys.stdout.write(f"已关掉 {macro}\n")
    _strip_settings(project, app_id)
    if folder is None or not folder.exists():
        sys.stdout.write("源码目录不存在（可能仅清理了设备）。\n")
        return 0
    if not _owned(folder, app_id):
        sys.stdout.write("已拒绝。目录不是本工具创建的，未删除源码（设备侧已清理）。\n")
        return 1
    shutil.rmtree(folder)
    sys.stdout.write(f"已删除 {folder}\n")
    return 0


def _check_argv(argv: list[str], project: Path | None) -> int:
    if len(argv) != 1:
        sys.stderr.write("用法：devstudio app check <id>\n")
        return 2
    project = (project or Path.cwd()).resolve()
    found = _locate(project, argv[0])
    if not found:
        sys.stdout.write(f"没有应用目录 {argv[0]}。\n")
        return 1
    notes = _check_dir(project / found[1], argv[0], found[0])
    if not notes:
        sys.stdout.write("检查通过。\n")
        return 0
    for note in notes:
        sys.stdout.write(note + "\n")
    return 1


def _write_tablet_scaffold(folder: Path, app_id: str, name: str, keep_business: bool) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    kind, glyph, top, bottom = _icon(app_id, name)
    payload = {
        "app_id": app_id,
        "name": name,
        "action": app_id,
        "entry_so": f"lib/lib{app_id}_app.so",
        "entry_symbol": f"app_entry_start_{app_id}",
        "version": "1.0.0",
        "icon_kind": kind,
        "icon_color": top,
        "icon_color2": bottom,
    }
    if glyph:
        payload["icon_glyph"] = glyph
    existing = {}
    app_json = folder / "app.json"
    if keep_business and app_json.is_file():
        try:
            loaded = json.loads(app_json.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except json.JSONDecodeError:
            existing = {}
    for key, value in existing.items():
        if key not in payload and key not in {"entry", "entry_point", "entry_func", "icon"}:
            payload[key] = value
    if keep_business and isinstance(existing.get("name"), str) and existing.get("name"):
        payload["name"] = existing["name"]
    app_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (folder / f"app_entry_{app_id}.c").write_text(_entry_source(app_id), encoding="utf-8")
    (folder / f"tablet_{app_id}.h").write_text(_header_source(app_id), encoding="utf-8")


def _entry_source(app_id: str) -> str:
    return f"""#define LOG_TAG "TABLET_APP_ENTRY_{app_id.upper()}"

#include "tablet_{app_id}.h"

#define APP_ENTRY_EXPORT __attribute__((visibility("default"))) __attribute__((used))

APP_ENTRY_EXPORT int app_entry_start_{app_id}(lv_obj_t *main)
{{
    if (!main)
        return -1;
    if (tablet_{app_id}_create(main) != LV_RESULT_OK)
        return -1;
    lv_obj_add_flag(main, LV_OBJ_FLAG_HIDDEN);
    return 0;
}}

static int (*const s_keep_app_entry_{app_id})(lv_obj_t *) = app_entry_start_{app_id};
"""


def _header_source(app_id: str) -> str:
    guard = f"APPLICATION_TABLET_{app_id.upper()}_H"
    return f"""#ifndef {guard}
#define {guard}

#ifdef __cplusplus
extern "C" {{
#endif

#include "lvgl.h"

lv_result_t tablet_{app_id}_create(lv_obj_t *main);

#ifdef __cplusplus
}}
#endif

#endif /* {guard} */
"""


def _check_dir(folder: Path, app_id: str, form: str | None) -> list[str]:
    notes: list[str] = []
    if form != "TABLET":
        return notes
    app_json = folder / "app.json"
    if not app_json.is_file():
        notes.append("缺少 app.json")
    else:
        try:
            data = json.loads(app_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
            notes.append("app.json 无法解析")
        if data.get("app_id") not in (None, app_id):
            notes.append(f"app.json 的 app_id 必须是 {app_id}")
        if data.get("entry_symbol") not in (None, f"app_entry_start_{app_id}"):
            notes.append(f"app.json 的 entry_symbol 必须是 app_entry_start_{app_id}")
    entry = folder / f"app_entry_{app_id}.c"
    if not entry.is_file() or f"app_entry_start_{app_id}" not in entry.read_text(encoding="utf-8", errors="replace"):
        notes.append(f"入口必须导出 app_entry_start_{app_id}(lv_obj_t *main)")
    business = folder / f"tablet_{app_id}.c"
    if not business.is_file():
        notes.append(f"缺少 tablet_{app_id}.c")
        return notes
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in folder.glob("*.c")
        if path.name != f"app_entry_{app_id}.c"
    )
    if not re.search(rf"lv_result_t\s+tablet_{app_id}_create\s*\(\s*lv_obj_t\s*\*", text):
        notes.append(f"tablet_{app_id}.c 没有定义 lv_result_t tablet_{app_id}_create(lv_obj_t *main)")
    if re.search(r"\blv_obj_create\s*\(\s*NULL\s*\)|\blv_scr_load|\blv_screen_load", text):
        notes.append("不要用 lv_obj_create(NULL) 或 lv_scr_load")
    if HAL_INCLUDE.search(text):
        notes.append("不要 #include hardware、camera、display、gpio、wifi")
    banned = sorted(set(BANNED_LVGL.findall(text)))
    if banned:
        notes.append("用了主程序没有导出的 LVGL 控件：" + "、".join(item.rstrip("(").strip() for item in banned))
    return notes


def _enable_macro(project: Path, macro: str) -> bool:
    changed = False
    for cfg in _configs(project):
        text = cfg.read_text(encoding="utf-8", errors="replace")
        line = f"{macro} = y"
        pattern = re.compile(rf"^{re.escape(macro)}\s*=.*$", re.M)
        if pattern.search(text):
            nxt = pattern.sub(line, text)
        else:
            nxt = text.rstrip() + f"\n{line}\n"
        if nxt != text:
            cfg.write_text(nxt, encoding="utf-8")
            changed = True
        else:
            changed = True
    return changed


def _strip_macro(project: Path, macro: str) -> bool:
    changed = False
    pattern = re.compile(rf"^\s*{re.escape(macro)}\s*\??=.*$", re.M)
    for cfg in _configs(project):
        text = cfg.read_text(encoding="utf-8", errors="replace")
        nxt = pattern.sub("", text)
        nxt = re.sub(r"\n{3,}", "\n\n", nxt)
        if nxt != text:
            cfg.write_text(nxt, encoding="utf-8")
            changed = True
    return changed


def _strip_settings(project: Path, app_id: str) -> None:
    name = _product_name(project)
    root = project / "project" / name if name else project / "project"
    if not root.is_dir():
        return
    for path in root.rglob("settings.json"):
        if "out" in path.parts or "node_modules" in path.parts:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        apps = data.get("installed_apps", {}).get("apps") if isinstance(data, dict) else None
        if not isinstance(apps, list) or not any(isinstance(item, dict) and item.get("app_id") == app_id for item in apps):
            continue
        data["installed_apps"]["apps"] = [item for item in apps if not (isinstance(item, dict) and item.get("app_id") == app_id)]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        sys.stdout.write(f"已从 {path.relative_to(project)} 去掉登记\n")


def _product_name(project: Path) -> str:
    path = project / "devstudio.yaml"
    if not path.is_file():
        return ""
    project_info = load_yaml(path).get("project") or {}
    return str(project_info.get("name") or "")


def _configs(project: Path) -> list[Path]:
    name = _product_name(project)
    if name:
        path = project / "project" / name / "config.mk"
        return [path] if path.is_file() else []
    root = project / "project"
    if not root.is_dir():
        return []
    return sorted(path for path in root.glob("*/config.mk") if path.is_file())


def _owned(folder: Path, app_id: str) -> bool:
    if (folder / ".devstudio-managed").is_file():
        return True
    app_json = folder / "app.json"
    if not app_json.is_file():
        return False
    try:
        data = json.loads(app_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return data.get("app_id") == app_id or data.get("id") == app_id


def _resolve_app_id(project: Path, token: str, *, action: str = "") -> str | None:
    if ID_RE.fullmatch(token):
        return token
    matches = []
    roots = (
        (project / "application" / "packages", "*/*/app.json"),
        (project / "application" / "cloud", "*/app.json"),
    )
    for root, pattern in roots:
        if not root.is_dir():
            continue
        for path in root.glob(pattern):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            name = str(data.get("name") or "")
            app_id = str(data.get("app_id") or data.get("id") or path.parent.name)
            if token and token in name and ID_RE.fullmatch(app_id):
                matches.append(app_id)
    unique = list(dict.fromkeys(matches))
    if len(unique) == 1:
        sys.stdout.write(f"按名称对应应用 {unique[0]}\n")
        return unique[0]
    if len(unique) > 1:
        sys.stderr.write("名称对应多个应用：" + "、".join(unique) + "\n")
        return None
    if (token == "天气" or token.lower() == "weather") and action == "remove":
        sys.stdout.write(
            "工程里没有天气平板应用（不删 service_weather）。"
            "将按设备目录 /mnt/install/apps/weather 清理；"
            "须经 IDE「运行中输入」提供板号（NEED_BOARD ASK_UI=board_ip），禁止聊天口头索要。\n"
        )
        return "weather"
    sys.stderr.write(f"没有名为 {token} 的应用。应用 id 形如 clock、gallery。\n")
    return None


def _board_of(project: Path, board: str | None) -> str:
    from devstudio.tools.device.connect import resolve_board

    return resolve_board(project, board)


def _remove_on_device(project: Path, app_id: str, board: str | None) -> int:
    chosen = (board or "").strip() or _board_of(project, None)
    if not chosen:
        sys.stdout.write(
            "NEED_BOARD ASK_UI=board_ip：未获板号，未删设备目录。\n"
        )
        return 1
    command = (
        "mount -o remount,rw /mnt 2>&1 || true; "
        f"rm -rf /mnt/install/apps/{app_id} /mnt/install/.app_bak/{app_id} "
        f"/mnt/install/apps/{app_id}.bak /usr/apps/{app_id} /usr/apps/{app_id}.bak 2>&1"
    )
    try:
        from devstudio.tools.device.frame import DeviceLink

        with DeviceLink(chosen, timeout=20) as link:
            result = link.exec(command, timeout=20)
            if not (result.get("ok") is True or result.get("exit") == 0):
                detail = str(result.get("out") or result.get("error") or result.get("exit") or "删除失败")
                sys.stdout.write(f"设备目录删除失败：{detail}\n")
                return 1
            sys.stdout.write(f"已删除设备上的 /mnt/install/apps/{app_id}（板 {chosen}）\n")
            _strip_device_settings(link, app_id)
            _relaunch(project, link)
    except (OSError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
        sys.stdout.write(
            f"设备未连接（{chosen}:7777）：{exc}。未删本地源码；请确认 studio 在线后重试。\n"
        )
        return 1
    return 0


def _strip_device_settings(link, app_id: str) -> None:
    try:
        raw = link.file_get("/etc/config/settings.json").decode("utf-8")
        data = json.loads(raw)
    except (OSError, TimeoutError, ConnectionError, json.JSONDecodeError, UnicodeError) as exc:
        sys.stdout.write(f"设备登记未改：{exc}\n")
        return
    apps = data.get("installed_apps", {}).get("apps") if isinstance(data, dict) else None
    if not isinstance(apps, list):
        return
    data["installed_apps"]["apps"] = [
        item for item in apps if not (isinstance(item, dict) and item.get("app_id") == app_id)
    ]
    try:
        link.file_put("/etc/config/settings.json", (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    except (OSError, TimeoutError, ConnectionError) as exc:
        sys.stdout.write(f"设备登记未改：{exc}\n")
        return
    sys.stdout.write("已从设备 settings.json 去掉登记\n")


def _relaunch(project: Path, link) -> None:
    del project
    from devstudio.tools.device.connect import relaunch_desktop

    text = relaunch_desktop(link)
    if text:
        sys.stdout.write(text)


def _locate(project: Path, app_id: str) -> tuple[str, str] | None:
    for form in FORMS:
        folder = _named(form, app_id)[0]
        if (project / folder).is_dir():
            return form, folder
    return None


def _named(form: str, app_id: str) -> tuple[str, str, str]:
    folder, entry, switch = FORMS[form]
    return (
        folder.format(id=app_id),
        entry.format(id=app_id),
        switch.format(ID=app_id.upper(), id=app_id),
    )


def _snapshot(folder: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(folder, dest)


def _existing_name(folder: Path, app_id: str) -> str:
    app_json = folder / "app.json"
    if not app_json.is_file():
        return app_id
    try:
        data = json.loads(app_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return app_id
    name = data.get("name")
    return name if isinstance(name, str) and name else app_id


def _icon(app_id: str, name: str) -> tuple[str, str, str, str]:
    text = f"{name} {app_id}"
    for pattern, kind, glyph, top, bottom in ICON_LOOKS:
        if pattern.search(text):
            return kind, glyph, top, bottom
    return "glyph", "file", "#7CC8FF", "#2F86EB"


def _form(text: str) -> str | None:
    for form, macro in (
        ("TABLET", "CONFIG_APP_PACKAGES_TABLET"),
        ("WEARABLE", "CONFIG_APP_PACKAGES_WEARABLE"),
        ("IPCAMERA", "CONFIG_APP_PACKAGES_IPCAMERA"),
    ):
        if re.search(rf"{macro}\s*=\s*y", text):
            return form
    return None
