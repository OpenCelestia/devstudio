import re
from pathlib import Path

from devstudio.orchestration.detect import Scan, chip_of
from devstudio.workspace import data_root

GROUPS = {
    "sdk_celestia": ["project", "knowledge", "rules", "source", "build", "device", "tune"],
    "sdk_customer_app": ["project", "knowledge", "rules", "source", "build"],
    "sdk_only": ["project", "knowledge", "rules", "build"],
}
# Skill bodies live in celestia/<layer>/<skill> and sdk/<scheme>/<skill>. init only writes their paths into AGENTS.md.
CELESTIA_SKILLS = [
    "celestia",
    "celestia-application",
    "celestia-framework",
    "celestia-hardware",
    "celestia-system-core",
    "celestia-aos-port",
    "celestia-project",
    "celestia-scripts",
    "celestia-debug",
    "celestia-studio",
    "celestia-third-party",
    "celestia-contributing",
    "git-gitee",
]
SKILLS = {
    "sdk_celestia": [
        "project-bootstrap",
        "application-feature",
        "cis-porting",
        "flash-porting",
        "build-triage",
        "tune-memory",
        "tune-cpu",
        *CELESTIA_SKILLS,
    ],
    "sdk_customer_app": ["hal-adapt", "cis-porting", "flash-porting", "build-triage"],
    "sdk_only": ["cis-porting", "flash-porting", "build-triage"],
}


def tools_line(state: str) -> str:
    return " ".join(f"devstudio-{name}" for name in GROUPS[state])


def render_scan(scan: Scan, state: str | None, host: str, customer: dict | None = None, part: str = "full") -> str:
    lines = _header_lines(scan)
    if scan.blocked or not scan.sdks or (scan.mixed and state is None) or state is None or part == "header":
        return "\n".join(lines) + "\n"
    status = f"Suggested state: {state}"
    plan = _plan_lines(scan, state, host, customer)
    if part == "status":
        return "\n".join(lines + [status]) + "\n"
    if part == "plan":
        return "\n".join(plan) + "\n"
    return "\n".join(lines + [status, *plan]) + "\n"


def _header_lines(scan: Scan) -> list[str]:
    lines = [f"Scanning {scan.project}"]
    if not scan.sdks:
        if scan.blocked:
            lines.append(scan.blocked)
        return lines
    if len(scan.sdks) > 1 and scan.mixed:
        lines.append("Multiple chip SDKs found; pick one with --sdk:")
        for hit in scan.sdks:
            mark = "registered" if hit.registered else "unregistered"
            lines.append(f"  {hit.path}  {mark} {hit.release_id}")
        return lines
    hit = scan.sdks[0]
    mark = "registered" if hit.registered else "unregistered"
    lines.append(f"  Chip SDK     {_display_sdk(scan.project, hit.path)}     {mark} {hit.release_id}")
    if scan.blocked and not hit.registered:
        lines.append(scan.blocked)
        return lines
    present = [name for name, ok in scan.flags.items() if ok]
    missing = [name for name, ok in scan.flags.items() if not ok]
    if all(scan.flags.values()):
        lines.append("  Celestia     application/ framework/ hardware/ build.sh")
    elif present:
        lines.append("  Celestia     not found")
        lines.append("  Found: " + "  ".join(_flag_name(name) for name in present))
        lines.append("  Missing: " + "  ".join(_flag_name(name) for name in missing))
    else:
        lines.append("  Celestia     not found")
    if scan.product and scan.form_flag:
        lines.append(f"  Product      project/{scan.product} ({scan.form_flag}=y)")
    if scan.mixed:
        lines.append("Cannot determine state. Choose one:")
        lines.append("  1) sdk_celestia      2) sdk_customer_app      3) sdk_only")
    return lines


def _plan_lines(scan: Scan, state: str, host: str, customer: dict | None) -> list[str]:
    hit = scan.sdks[0]
    lines = [f"  Will enable: {tools_line(state)}", "  Skills: " + " ".join(SKILLS[state])]
    if state == "sdk_celestia" and scan.form == "TABLET":
        lines.append(f"  Writable: application/packages/tablet/<id>/  hardware/**/hal/{hit.family}/")
        lines.append(f"  Build: ./build.sh --product-name {scan.product or '<product>'}   Host: {host}")
    elif state == "sdk_celestia":
        lines.append(f"  Writable: hardware/**/hal/{hit.family}/")
        lines.append(f"  Build: ./build.sh --product-name {scan.product or '<product>'}   Host: {host}")
    elif state == "sdk_customer_app" and customer:
        lines.append(f"  Writable: {customer['hal_out']}")
        lines.append(f"  Build: {customer['command']}   Host: {host}")
    elif state == "sdk_only":
        command = "make" if (hit.path / "Makefile").is_file() else "./auto_build.sh"
        lines.append(f"  Writable: data dir {data_root(scan.project) / 'tasks'}")
        lines.append(f"  Build: {command}   Host: {host}")
    return lines


def _flag_name(name: str) -> str:
    return name if name == "build.sh" else f"{name}/"


def _display_sdk(project, sdk: Path) -> str:
    try:
        relative = sdk.resolve().relative_to(project.resolve()).as_posix()
    except ValueError:
        return str(sdk)
    if relative == ".":
        return str(sdk)
    return relative


def agents_body(
    scan: Scan,
    state: str,
    host: str,
    customer: dict | None,
    skill_paths: list[str] | list[tuple[str, str]] | None = None,
    ide_paths: list[str] | None = None,
) -> str:
    """Compact bilingual AGENTS body: English-first rules, short ZH glosses, path|when skills."""
    hit = scan.sdks[0]
    rows = [
        f"- Chip/芯片: {hit.family}  SDK: {hit.version}  release_id: {hit.release_id}",
        f"- State: {state}",
    ]
    if state == "sdk_celestia":
        rows[1] += f"  Product/产品: {scan.product or ''}  Form/形态: {scan.form or ''}"
        rows.extend(
            [
                "- Language: follow the user's latest message (Han/CJK-dominant → Chinese; else English). Reply, Ask UI labels, and skill trigger matching (When: vs 何时用) in that language | 跟用户最新一句切换中英文",
                "- Plan before edits; Ask via IDE Ask UI (AskQuestion / elicitation / options); NEED_* ASK_UI=…; no tkinter; no idle chat wait | 改前计划+IDE问答",
                "- Write only via apply | 只经 apply 写文件",
                "- No Makefile / Kconfig / config.mk module switches | 不改模块开关",
                "- regs / Sensor ID / VTS / fps / init seq: only via `devstudio-rules`",
                "- search: only via `devstudio-knowledge`; else say not indexed | 未收录",
                "- board IP: use if given; else Ask UI; never invent / silent-pick; never DEVSTUDIO_UI=desktop",
                "- env: `devstudio doctor`; on fail ASK_UI → `doctor --set-host ssh --ssh-host … --ssh-workdir …`",
            ]
        )
    elif state == "sdk_customer_app" and customer:
        rows.extend(
            [
                f"- Write only HAL out: `{customer['hal_out']}`",
                f"- HAL interface: `{customer['hal_interface']}`",
                f"- Build: `{customer['command']}`  Host: {host}",
                "- Downstream calls must exist in current SDK headers",
            ]
        )
    else:
        rows.extend(
            [
                f"- Do not write the SDK tree; plans/logs/reports → `{data_root(scan.project)}`",
                "- Do not generate applications or HAL",
            ]
        )
    rows.extend(_reference_rows(skill_paths or [], ide_paths or []))
    return "# DevStudio AGENTS | AI 约束\n" + "\n".join(rows) + "\n"


# Bilingual triggers: When: (EN) and/or 何时用： (ZH). Both → "EN | ZH" for language switch.
_WHEN_MARKS = ("When:", "何时用：")
_HINT_LIMIT = 72
_HINT_LIMIT_BI = 100
_CJK_GAP = re.compile(r"(?<=[\u3000-\u9fff\uff00-\uffef]) (?=[\u3000-\u9fff\uff00-\uffef])")
_SKILL_SUFFIX = "/SKILL.md"
_SKILL_PREFIX = "devstudio/"


def skill_hint(path: Path) -> str:
    """One-line trigger for AGENTS.md: When:/何时用： (bilingual if both), else first sentence."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    description = _frontmatter_description(text)
    if description:
        en = _mark_text(description, "When:")
        zh = _mark_text(description, "何时用：")
        if en and zh:
            return _clip_bilingual(en, zh)
        if en:
            return _clip(en)
        if zh:
            return _clip(zh)
        return _clip(_first_sentence(description))
    return _clip(_first_sentence(_first_paragraph(text)))


def _mark_text(description: str, mark: str) -> str:
    if mark not in description:
        return ""
    text = description.split(mark, 1)[1]
    for other in _WHEN_MARKS:
        if other != mark and other in text:
            text = text.split(other, 1)[0]
    return text.strip()


def _clip_bilingual(en: str, zh: str) -> str:
    budget = _HINT_LIMIT_BI - 3  # " | "
    left = _clip_to(en, budget // 2)
    right = _clip_to(zh, budget - len(left))
    return f"{left} | {right}"


def _clip_to(text: str, limit: int) -> str:
    text = text.strip()
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in ("、", "，", ",", "；", ";", "。", ".", " ", "/", "「"):
        idx = cut.rfind(sep)
        if idx >= limit // 2:
            return cut[:idx].rstrip("、，,；; /") + "…"
    return text[: max(limit - 1, 1)] + "…"


def skill_ref(path: str) -> str:
    """Short AGENTS skill id: celestia/.../name (open as devstudio/<id>/SKILL.md)."""
    ref = path.replace("\\", "/")
    if ref.endswith(_SKILL_SUFFIX):
        ref = ref[: -len(_SKILL_SUFFIX)]
    marker = "/" + _SKILL_PREFIX  # "/devstudio/"
    idx = ref.rfind(marker)
    if idx >= 0:
        return ref[idx + len(marker) :]
    if ref.startswith(_SKILL_PREFIX):
        return ref[len(_SKILL_PREFIX) :]
    return ref


def _frontmatter_description(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    parts: list[str] = []
    inside = False
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("description:"):
            inside = True
            value = line[len("description:"):].strip()
            if value and value not in (">", ">-", "|", "|-"):
                parts.append(value)
            continue
        if inside:
            if line[:1] in (" ", "\t"):
                parts.append(line.strip())
            else:
                inside = False
    return _CJK_GAP.sub("", " ".join(part for part in parts if part)).strip()


def _first_paragraph(text: str) -> str:
    paragraph: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or (not stripped and not paragraph):
            continue
        if not stripped:
            break
        paragraph.append(stripped)
    return " ".join(paragraph)


def _first_sentence(text: str) -> str:
    end = text.find("。")
    return text[: end + 1] if end >= 0 else text


def _clip(text: str) -> str:
    text = text.strip()
    if len(text) <= _HINT_LIMIT:
        return text
    cut = text[:_HINT_LIMIT]
    for sep in ("、", "，", ",", "；", ";", "。", ".", " "):
        idx = cut.rfind(sep)
        if idx >= _HINT_LIMIT // 2:
            return cut[:idx].rstrip("、，,；; ") + "…"
    return text[: _HINT_LIMIT - 1] + "…"


def _reference_rows(skill_paths: list[str] | list[tuple[str, str]], ide_paths: list[str]) -> list[str]:
    rows = [
        "- Skills: open `devstudio/<id>/SKILL.md`; never copy into `.cursor` / `.claude` / `.vscode`",
        "- Read only the matching Skill (`id | when`); read Skill before its tools:",
    ]
    for item in skill_paths:
        path, hint = item if isinstance(item, tuple) else (item, "")
        ref = skill_ref(path)
        rows.append(f"  - {ref}" + (f" | {hint}" if hint else ""))
    if ide_paths:
        rows.append("- MCP (IDE loads from `devstudio/ide/`; else run `devstudio` CLI):")
        rows.extend(f"  - {path}" for path in ide_paths)
    return rows


def project_yaml(scan: Scan, state: str, host: str, customer: dict | None) -> dict:
    hit = scan.sdks[0]
    command = _build_command(scan, state, customer)
    data = {
        "state": state,
        "release_id": hit.release_id,
        "sdk": {"root": _display_sdk(scan.project, hit.path)},
        "build": {"command": command, "host": host},
    }
    if scan.product:
        data["project"] = {"name": scan.product}
    data["target"] = {"chip": chip_of(hit.family)}
    if state == "sdk_only":
        data["write"] = {"model": "workspace"}
    else:
        data["write"] = {"model": "in_tree"}
    if customer:
        if "app_root" in customer:
            data["customer"] = {
                "app_root": customer["app_root"],
                "hal_interface": customer.get("hal_interface", ""),
                "hal_out": customer.get("hal_out", ""),
            }
        if host == "ssh" or str(host).startswith("ssh"):
            data["build"]["ssh_host"] = customer.get("ssh_host", "")
            data["build"]["ssh_workdir"] = customer.get("ssh_workdir", "")
    data["device"] = {"channel": "studio_frame" if state == "sdk_celestia" else "none"}
    data["data"] = {"root": str(data_root(scan.project))}
    return data


def _build_command(scan: Scan, state: str, customer: dict | None) -> str:
    hit = scan.sdks[0]
    if state == "sdk_celestia":
        return f"./build.sh --product-name {scan.product or '<product>'}"
    if state == "sdk_customer_app" and customer:
        return customer["command"]
    if (hit.path / "Makefile").is_file():
        return "make"
    return "./auto_build.sh"
