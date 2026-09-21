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
    :param notes: 探测小结（给 LLM 的文字说明）。
    :param detected_at: ISO 时间戳。
    """

    found: bool = False
    version: str = ""
    path: str = ""
    tasks: dict[str, list[str]] = field(default_factory=dict)
    notes: str = ""
    detected_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {"found": self.found, "version": self.version, "path": self.path,
                "tasks": self.tasks, "notes": self.notes,
                "detected_at": self.detected_at}

    @classmethod
    def from_dict(cls, data: Mapping) -> "VaspkitSkill":
        return cls(found=bool(data.get("found")),
                   version=str(data.get("version", "")),
                   path=str(data.get("path", "")),
                   tasks={str(k): [str(x) for x in v] for k, v in (data.get("tasks") or {}).items()},
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


def probe_vaspkit(run: Run, *, timeout: int = 30) -> VaspkitSkill:
    """在超算侧探测 vaspkit：先定位可执行文件，再尽力读版本/能力信息。

    探测失败一律返回 ``found=False``，不抛异常（保证集成可用性）。
    """
    skill = VaspkitSkill()
    try:
        code, out, _ = run("which vaspkit 2>/dev/null || command -v vaspkit")
        lines = [ln for ln in (out or "").splitlines() if ln.strip()]
        if code != 0 or not lines:
            return skill
        skill.path = _safe_path(lines[0])
        skill.found = True
        try:
            vc, vout, _ = run(f"{skill.path} -v", timeout=timeout)
            if _run_ok(vc) and (vout or "").strip():
                skill.version = (vout or "").strip().splitlines()[0][:80]
        except Exception:  # noqa: BLE001
            pass
        cap_text = ""
        for cmd in (f"{skill.path} -h", f"echo 0 | {skill.path}"):
            try:
                hc, hout, _ = run(cmd, timeout=timeout)
                if _run_ok(hc) and (hout or "").strip():
                    cap_text = hout
                    break
            except Exception:  # noqa: BLE001
                continue
        skill.tasks = _detect_tasks(cap_text)
        _sync_notes(skill)
        logger.info("vaspkit 探测完成: found=%s path=%s", skill.found, skill.path)
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
