"""M9 工具层：vaspkit 探测与永久技能固化（仅超算侧可用）。

对齐 WORKFLOW.md v14 §2 步4、MODULE_INTERFACES v1.2 §1.6：
- vaspkit 只在超算上存在；本层不 import 工具箱任何代码，不真正执行提交。
- 首次连接超算时探测 vaspkit 能力并固化为永久技能（skills/ 目录，纯文本）。
- 本层只生成/整理指令，不持有 SSH 钥匙；远端动作由受限执行器发起。
"""
from __future__ import annotations

import json
import logging
import re
import shlex
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from .. import paths

logger = logging.getLogger("ai_mode.tools")

#: run 可调用签名与 M6/M7 一致：``(command, *, cwd=None, timeout=None) -> (code, stdout, stderr)``
Run = Callable[..., tuple[int, str, str]]

#: vaspkit 任务族 → 菜单号（已知编号；探测时以其出现来推断能力是否存在）。
#: 编号随 VASPKIT 版本有差异，探测可覆盖；仅作为「技能记录」内容，不改动作。
VASPKIT_TASKS: dict[str, tuple[str, ...]] = {
    "structure": ("101", "102", "103", "111"),
    "kpoints": ("301", "303", "351"),
    "potcar": ("401",),
    "submit": ("501", "511", "521"),
    "post": ("600", "601", "602", "700", "701", "702", "711"),
}

#: 菜单行里出现这些词，说明该任务不止生成 POTCAR（可能连 INCAR/KPOINTS 一起改）；
#: 只有在菜单里找不到「只针对 POTCAR」的项时才退而求其次，并在卡片里说清楚。
_BROAD_TASK_HINTS = ("input file", "input files", "incar", "kpoints", "all")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_path(text: str) -> str:
    return str(text or "").strip().strip('"').strip("'") or ""


@dataclass
class VaspkitSkill:
    """固化为永久技能的内容（严格不含任何凭据）。

    :param found: 本次探测是否发现可用 vaspkit。
    :param version: 版本号（尽力解析，可能为空）。
    :param path: 超算上 vaspkit 可执行文件路径。
    :param tasks: family -> 已探测到可用的任务号列表。
    :param potcar_code: 从菜单里读到的 POTCAR 任务号；空字符串表示没读出来。
    :param potcar_only: 该 POTCAR 任务是否只生成 POTCAR（不连带改 INCAR/KPOINTS）。
    :param notes: 探测小结（给 LLM 的文字说明）。
    :param detected_at: ISO 时间戳。
    """

    found: bool = False
    version: str = ""
    path: str = ""
    tasks: dict[str, list[str]] = field(default_factory=dict)
    potcar_code: str = ""
    potcar_only: bool = False
    notes: str = ""
    detected_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {"found": self.found, "version": self.version, "path": self.path,
                "tasks": self.tasks, "potcar_code": self.potcar_code,
                "potcar_only": self.potcar_only, "notes": self.notes,
                "detected_at": self.detected_at}

    @classmethod
    def from_dict(cls, data: Mapping) -> "VaspkitSkill":
        return cls(found=bool(data.get("found")),
                   version=str(data.get("version", "")),
                   path=str(data.get("path", "")),
                   tasks={str(k): [str(x) for x in v] for k, v in (data.get("tasks") or {}).items()},
                   potcar_code=str(data.get("potcar_code", "")),
                   potcar_only=bool(data.get("potcar_only", False)),
                   notes=str(data.get("notes", "")),
                   detected_at=str(data.get("detected_at", _now_iso())))


def store_path(root: Path | None = None) -> Path:
    """技能文件路径：``<root>/skills/vaspkit.json``（root 默认 paths.home_dir()）。"""
    base = Path(root).expanduser().resolve() if root else paths.home_dir()
    return base / "skills" / "vaspkit.json"


def _extract_numbers(text: str) -> list[str]:
    """从帮助文本里抓取 3 位任务号。"""
    return re.findall(r"\b([1-9]\d{2})\b", text or "")


def _detect_tasks(stdout: str) -> dict[str, list[str]]:
    numbers = set(_extract_numbers(stdout))
    out: dict[str, list[str]] = {}
    for family, codes in VASPKIT_TASKS.items():
        present = [c for c in codes if c in numbers]
        if present:
            out[family] = present
    return out


def _run_ok(code: int) -> bool:
    return code == 0


def potcar_menu_code(menu_text: str) -> tuple[str, bool]:
    """从 vaspkit 菜单文本里读 POTCAR 任务号（不猜编号，按菜单自己写的）。

    返回 ``(code, 是否只针对 POTCAR)``：优先选描述里只有 POTCAR、不含
    INCAR/KPOINTS/"input files" 的那一行；找不到就退回「提到 POTCAR 的任意一行」，
    并标记为可能连带改动其他输入文件（卡片上会写明）。
    """
    broad = ""
    fallback = ""
    for line in (menu_text or "").splitlines():
        upper = line.upper()
        if "POTCAR" not in upper:
            continue
        numbers = _extract_numbers(line)
        if not numbers:
            continue
        low = line.lower()
        if "user specified" in low or "specified potential" in low:
            # 让用户指定赝势的那种：也只会生成 POTCAR，但不是"听默认的"，留作保底
            fallback = fallback or numbers[0]
            continue
        if "default" in low:
            return numbers[0], True
        if any(hint in low for hint in _BROAD_TASK_HINTS):
            broad = broad or numbers[0]
            continue
        return numbers[0], True
    if broad:
        return broad, False
    return fallback, True


def vasp_input_menu_code(menu_text: str) -> str:
    """主菜单里「VASP Input-Files Generator」那一项的两位菜单号（1.5.x 是两级菜单）。"""
    for line in (menu_text or "").splitlines():
        low = line.lower()
        if "vasp input" not in low or "generator" not in low:
            continue
        found = re.search(r"^\s*([0-9]{2})\)", line)
        if found:
            return found.group(1)
    return ""


_VERSION_RE = re.compile(r"VASPKIT\s+[A-Za-z ]*Edition\s+([0-9][0-9.]*)")


def parse_version(text: str) -> str:
    """从 banner 里读版本号（1.5.x 的 `-v` 是非法参数，只能从 banner 读）。"""
    found = _VERSION_RE.search(text or "")
    return found.group(1) if found else ""


def _run_text(run: Run, command: str, timeout: float) -> str:
    """跑一条只读探测命令，返回 stdout（失败/为空都返回空串）。"""
    try:
        code, out, _err = run(command, timeout=timeout)
    except Exception:  # noqa: BLE001 - 探测命令失败按"读不到"处理
        return ""
    return (out or "") if code == 0 or (out or "").strip() else ""


def read_menu(run: Run, path: str, *, timeout: float = 30.0) -> str:
    """拿菜单文本：1.5.x 靠 `printf '0\\n' | vaspkit`（主菜单 + 版本 banner），
    老版本退回 `-h` / `-v`。全部失败返回空串。"""
    quoted = shlex.quote(path)
    for command in (f"printf '0\\n' | {quoted}", f"{quoted} -h", f"{quoted} -v"):
        text = _run_text(run, command, timeout)
        if (text or "").strip():
            return text
    return ""


def probe_vaspkit(run: Run, *, timeout: int = 30) -> VaspkitSkill:
    """在超算侧探测 vaspkit：定位可执行文件、读版本、读 POTCAR 菜单号。

    兼容两种风格：
    - 1.5.x：`-v`/`-h` 是非法参数，版本在 banner 里，POTCAR 在「VASP Input-Files
      Generator」二级菜单里（实测 1.5.1 是 `103) Generate POTCAR File with Default Setting`）；
    - 老版本：扁平菜单，POTCAR 行直接出现在 `-h` 输出里。
    探测失败一律返回 ``found=False`` / 空字段，不抛异常。
    """
    skill = VaspkitSkill()
    try:
        code, out, _ = run("which vaspkit 2>/dev/null || command -v vaspkit")
        lines = [ln for ln in (out or "").splitlines() if ln.strip()]
        if code != 0 or not lines:
            return skill
        skill.path = _safe_path(lines[0])
        skill.found = True
        cap_text = read_menu(run, skill.path, timeout=timeout)
        if not cap_text.strip():
            _sync_notes(skill)
            return skill
        skill.version = parse_version(cap_text)
        direct, direct_only = potcar_menu_code(cap_text)
        if direct:
            skill.potcar_code, skill.potcar_only = direct, direct_only
        else:
            parent = vasp_input_menu_code(cap_text)
            if parent:
                quoted = shlex.quote(skill.path)
                sub = _run_text(run, f"printf '{parent}\\n0\\n' | {quoted}", timeout)
                if sub.strip():
                    cap_text = f"{cap_text}\n{sub}"
                    found, only = potcar_menu_code(sub)
                    if found:
                        skill.potcar_code, skill.potcar_only = found, only
        skill.tasks = _detect_tasks(cap_text)
        if skill.potcar_code:
            skill.tasks.setdefault("potcar", [skill.potcar_code])
        _sync_notes(skill)
        logger.info("vaspkit 探测完成: found=%s path=%s version=%s potcar=%s",
                    skill.found, skill.path, skill.version, skill.potcar_code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("vaspkit 探测异常，按未发现处理: %s", exc)
        skill.found = False
    return skill


def _sync_notes(skill: VaspkitSkill) -> None:
    if skill.notes.strip():
        return
    parts = [f"{family}: {' '.join(codes)}" for family, codes in skill.tasks.items()]
    skill.notes = "; ".join(parts) if parts else "已定位 vaspkit，但未探测到具体任务号"


def probe_and_store(run: Run, *, root: Path | None = None,
                    timeout: float = 30.0) -> VaspkitSkill:
    """探测 vaspkit 并固化为永久技能文件（幂等，可反复调用）。

    :param run: 超算侧受限执行器的 run 可调用。
    :param root: 本地数据根目录；默认 paths.home_dir()。
    :param timeout: 单条命令超时。
    """
    skill = probe_vaspkit(run, timeout=timeout)
    path = store_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(skill.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    logger.info("vaspkit 技能已固化: %s", path)
    return skill
