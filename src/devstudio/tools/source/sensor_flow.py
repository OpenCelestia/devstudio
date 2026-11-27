"""支持列表核对，以及能在本机完成的 Sensor 出包和方案生成。"""
import sys
import tarfile
from pathlib import Path

from devstudio.orchestration.detect import split_release
from devstudio.rules.cis_adapt import (
    adapt_driver,
    decode_text,
    is_driver_source,
    manifest_json,
    render_plan_markdown,
)
from devstudio.adapter.catalog import adapters_root
from devstudio.root import repo_root
from devstudio.tools.knowledge.peripherals import catalog_rows
from devstudio.yamlio import load_yaml


def run_sensor(argv: list[str], project: Path | None = None) -> int:
    project = (project or Path.cwd()).resolve()
    if len(argv) < 1 or argv[0] not in ("confirm", "build"):
        sys.stderr.write("用法：devstudio sensor confirm|build --model 型号 [--cis 模式]\n")
        return 2
    action = argv[0]
    values = _flags(argv[1:], {"--model": "model", "--cis": "cis"})
    model = values.get("model")
    if not model:
        sys.stderr.write("缺少 --model。\n")
        return 2
    error, release_id, rows = catalog_rows(project)
    if error:
        sys.stderr.write(error + "\n")
        return 1
    cis = values.get("cis")
    row = _row(rows, model, cis)
    if action == "confirm":
        if row is None:
            sys.stdout.write(f"{model} 不在 {release_id} 的支持列表里。\n")
            return 1
        return _confirm(row, values.get("cis"))
    if row is None:
        sys.stdout.write(f"{model} 不在 {release_id} 的支持列表里。列表外用 devstudio plan sensor。\n")
        return 1
    return _build_listed(project, release_id, row, values.get("cis"))


def _confirm(row: dict, cis: str | None) -> int:
    modes = row.get("cis_modes") or []
    sys.stdout.write(f"型号：{row['model']}\n")
    sys.stdout.write(f"分辨率：{row.get('resolution', '')}\n")
    sys.stdout.write(f"帧率：{row.get('fps', '')}\n")
    sys.stdout.write(f"接口：{row.get('interface', '')}\n")
    sys.stdout.write("CIS 模式：" + "、".join(modes) + "\n")
    sys.stdout.write(f"功能：{row.get('features', '')}\n")
    docs = [str(item) for item in (row.get("docs") or []) if str(item) not in ("doc", row.get("model", ""))]
    if docs:
        sys.stdout.write("厂商文档：" + "、".join(docs) + "\n")
    else:
        sys.stdout.write("厂商文档：未收录。列表内仍直接出包，不要求补 datasheet 或寄存器序列。\n")
    confs = row.get("confs") or []
    sys.stdout.write("ISP conf：" + ("、".join(str(item) for item in confs) if confs else "未收录") + "\n")
    sys.stdout.write("路径：列表内。确认后执行 devstudio sensor build，不改工程源码。\n")
    if cis and not any(cis in str(item) for item in modes):
        sys.stdout.write(f"CIS 模式 {cis} 不在 {row['model']} 的支持形态内。\n")
        return 1
    sys.stdout.write("参数与支持列表一致。\n")
    return 0


def _build_listed(project: Path, release_id: str, row: dict, cis: str | None = None) -> int:
    from devstudio.workspace import data_root

    model = row["model"]
    out = data_root(project) / "sensor"
    out.mkdir(parents=True, exist_ok=True)
    if row.get("source") == "package" and row.get("package"):
        packed = _package_file(release_id, str(row["package"]))
        if packed is not None:
            sys.stdout.write(f"实机调试包：{packed}\n")
            sys.stdout.write("列表内直接使用该成套件。未写入当前工程目录。\n")
            return 0
        sys.stdout.write("包文件：未收录。未出包。\n")
        return 1
    driver = find_driver_dir(project, model)
    if driver is None:
        sys.stdout.write(f"SDK 内未找到 {model} 的驱动源码。未出包。\n")
        return 1
    mode = _cis_mode(cis)
    chosen = _pick_conf([str(item) for item in (row.get("confs") or [])], mode)
    sdk = _sdk_root(project)
    found = _find_conf(sdk, chosen) if chosen and sdk is not None else None
    dest = out / f"{model}_{mode}.tar.gz"
    note = _conf_note(chosen, found)
    _tar_driver(driver, dest, model, found, note)
    sys.stdout.write(f"已从驱动源码打包 {dest}\n")
    sys.stdout.write(note + "\n")
    sys.stdout.write("未写入当前工程目录。\n")
    return 0


def materialize_plan(project: Path, task_dir: Path, config: dict, model: str, values: dict) -> None:
    """列表外：参考驱动和序列都在时，把方案写到数据目录。"""
    family, version = split_release(config["release_id"])
    rows = _family_rows(project)
    ref_model, ref_row = _reference(rows, model)
    seq_path = Path(values["sequence"])
    if not seq_path.is_file():
        seq_path = project / values["sequence"]
    if ref_model is None:
        sys.stdout.write("支持列表为空，无法选择参考驱动。未生成 .c。\n")
        return
    if not seq_path.is_file():
        sys.stdout.write("序列文件不存在，未生成驱动源码。\n")
        return
    driver = find_driver_dir(project, ref_model)
    if driver is None:
        sys.stdout.write(f"未找到参考型号 {ref_model} 的驱动源码，方案里没有 .c。\n")
        return
    sequence_text = decode_text(seq_path.read_bytes())
    if sequence_text is None:
        sys.stdout.write("序列文件无法按文本读取，未生成驱动源码。\n")
        return
    sources: dict[str, str] = {}
    for item in driver.iterdir():
        if not item.is_file() or not is_driver_source(item.name):
            continue
        text = decode_text(item.read_bytes())
        if text is not None:
            sources[item.name] = text
    lanes = values.get("lanes", "")
    params = f"接口：MIPI {lanes}lane\nI2C：{values.get('i2c', '')}\nMCLK：{values.get('mclk', '')}\n"
    adapted = adapt_driver(
        sources,
        ref_model=ref_model,
        model=model,
        platform=family,
        mode="single",
        params_text=params,
        interface=f"MIPI {lanes}lane",
        sequence_text=sequence_text,
        sequence_name=seq_path.name,
    )
    out_dir = task_dir / "plan_src"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, text in adapted.files.items():
        (out_dir / name).write_text(text, encoding="utf-8", newline="\n")
    if adapted.init_regs_text and adapted.init_regs_name:
        (out_dir / adapted.init_regs_name).write_text(adapted.init_regs_text, encoding="utf-8", newline="\n")
    (out_dir / seq_path.name).write_text(sequence_text, encoding="utf-8", newline="\n")
    adapted.manifest["written"] = adapted.written
    adapted.manifest["gaps"] = adapted.gaps
    adapted.manifest["ref_model"] = ref_model
    (out_dir / "merge_manifest.json").write_text(manifest_json(adapted.manifest), encoding="utf-8")
    (out_dir / "方案.md").write_text(
        render_plan_markdown(
            model=model,
            platform=family,
            version=version,
            job_id=task_dir.name,
            cis_mode="1（单目）",
            ref_model=ref_model,
            params_text=params,
            seq_note=f"见 {seq_path.name}",
            up_section="",
            result=adapted,
        ),
        encoding="utf-8",
        newline="\n",
    )
    pkg = task_dir / f"{model}_plan.tar.gz"
    with tarfile.open(pkg, "w:gz") as tar:
        tar.add(out_dir, arcname=f"{model}_plan")
    sys.stdout.write(f"参考型号：{ref_model}\n")
    sys.stdout.write(f"已生成方案包 {pkg}\n")
    sys.stdout.write(f"待复核 {len(adapted.gaps)} 项。未写入当前工程目录。\n")
    if ref_row and ref_row.get("confs"):
        sys.stdout.write("参考 ISP conf 未在本机定位，未改二进制。\n")


def _sdk_root(project: Path) -> Path | None:
    config_path = project / "devstudio.yaml"
    if not config_path.is_file():
        return None
    root = load_yaml(config_path).get("sdk", {}).get("root", "")
    if not root:
        return None
    sdk = Path(root)
    if not sdk.is_absolute():
        sdk = project / sdk
    return sdk if sdk.is_dir() else None


def find_driver_dir(project: Path, model: str) -> Path | None:
    sdk = _sdk_root(project)
    if sdk is None:
        return None
    name = model.lower()
    relatives = (
        Path("extracted/driver_src/driver_src/sensor") / name,
        Path("extracted/driver_src/sensor") / name,
        Path("driver_src/sensor") / name,
    )
    for rel in relatives:
        cand = sdk / rel
        if cand.is_dir():
            return cand
    return None


def driver_state(project: Path, release_id: str, row: dict) -> str:
    if row.get("source") == "package" and row.get("package"):
        return "有" if _package_file(release_id, str(row["package"])) is not None else "包未收录"
    if find_driver_dir(project, str(row.get("model", ""))):
        return "有"
    return "未定位"


def _family_rows(project: Path) -> list:
    _error, _release, rows = catalog_rows(project)
    return rows


def _reference(rows: list, model: str) -> tuple[str | None, dict | None]:
    import re

    match = re.match(r"[a-z]+", model.lower())
    prefix = match.group(0) if match else ""
    for row in rows:
        name = str(row.get("model", ""))
        if name.lower().startswith(prefix) and name.lower() != model.lower():
            return name, row
    if rows:
        return str(rows[0]["model"]), rows[0]
    return None, None


def _row(rows: list, model: str, cis: str | None = None) -> dict | None:
    matched = [item for item in rows if str(item.get("model", "")).lower() == model.lower()]
    if cis:
        for item in matched:
            if any(cis in str(mode) for mode in (item.get("cis_modes") or [])):
                return item
    return matched[0] if matched else None


def _package_file(release_id: str, package: str) -> Path | None:
    """先查 Adapter，再查与 develop 同级的 FAE AI 实机调试包。"""
    local = adapters_root() / release_id / "packages" / package
    if local.is_file():
        return local
    family, _version = split_release(release_id)
    root = repo_root().parent.parent / "FAE AI" / "fae-ai" / "resources" / "downloads" / "camera" / family
    candidate = root / package
    if candidate.is_file():
        return candidate
    return None


def _cis_mode(cis: str | None) -> str:
    text = (cis or "").lower()
    if any(token in text for token in ("双目", "主从", "dual", "master_slave")):
        return "dual"
    return "single"


def _pick_conf(names: list[str], mode: str) -> str | None:
    def multi(name: str) -> bool:
        low = name.lower()
        return any(token in low for token in ("dual", "slave", "quad", "triple", "master_slave"))

    if mode == "dual":
        for name in names:
            low = name.lower()
            if "dual" in low or "master_slave" in low:
                return name
        return None
    for name in names:
        if name and not multi(name):
            return name
    return None


def _find_conf(sdk: Path, name: str | None) -> Path | None:
    if not name or not sdk.is_dir():
        return None
    return next((item for item in sdk.rglob(name) if item.is_file()), None)


def _conf_note(chosen: str | None, found: Path | None) -> str:
    if not chosen:
        return "ISP conf：未收录。未编造文件。"
    if found is None:
        return f"ISP conf：{chosen} 未在已解出的 SDK 中找到。未编造文件。"
    return f"ISP conf：{chosen}"


def _tar_driver(driver: Path, dest: Path, model: str, conf: Path | None = None, note: str = "") -> None:
    import shutil

    stage = dest.parent / model
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    for item in driver.iterdir():
        if item.is_file() and is_driver_source(item.name):
            text = decode_text(item.read_bytes())
            if text is not None:
                (stage / item.name).write_text(text, encoding="utf-8", newline="\n")
    if conf is not None and conf.is_file():
        shutil.copyfile(conf, stage / conf.name)
    readme = f"{model} 驱动源码，从当前 SDK 的 driver_src 取出。\n"
    if note:
        readme += note + "\n"
    (stage / "README.txt").write_text(readme, encoding="utf-8")
    with tarfile.open(dest, "w:gz") as tar:
        tar.add(stage, arcname=model)


def _flags(argv: list[str], names: dict[str, str]) -> dict[str, str]:
    values: dict[str, str] = {}
    args = list(argv)
    while args:
        item = args.pop(0)
        if item in names and args:
            values[names[item]] = args.pop(0)
    return values
