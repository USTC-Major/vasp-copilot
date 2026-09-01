"""M10 报告与收尾：清理建议（只建议，绝不自动删除）。

对齐 WORKFLOW.md v14 §6 清理策略：不自动删除任何超算文件；发现已失效/可清理
文件时给出清理建议，由用户决定执行。本模块是纯函数，不写盘、不删除。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

#: 大文件阈值（字节）——超过才建议清理（避免误伤正常正尺寸输出）。
LARGE_THRESHOLD = 512 * 1024 * 1024

_SLURM_LOG = re.compile(r"slurm-\d+\.(?:out|err)$")
_WAVECAR_BAK = re.compile(r"WAVECAR\.\d+$")


@dataclass
class CleanupSuggestion:
    """一条清理建议（只建议；用户决定后才执行）。"""

    name: str
    reason: str
    size: int = 0
    action: str = "建议清理（不自动删除）"

    def to_dict(self) -> dict:
        return {"name": self.name, "reason": self.reason,
                "size": self.size, "action": self.action}


def _classify(name: str, size: int, *, job_done: bool = False) -> Optional[str]:
    base = (name or "").rsplit("/", 1)[-1]
    if not base:
        return None
    if base == "core" or base.startswith("core."):
        return "核心转储文件（crash dump），通常可安全清理"
    if base in ("CHG", "CHGCAR", "WAVECAR") and size > LARGE_THRESHOLD:
        return "大型重启动文件，确认无需续跑后建议清理"
    if _WAVECAR_BAK.match(base):
        return "重启动历史备份，确认无需后建议清理"
    if base[:3].lower() in ("tmp", "tem") or base.endswith((".bak", ".old", ".orig")):
        return "临时/备份文件"
    if _SLURM_LOG.match(base) and job_done:
        return "已结束作业的队列日志，确认无误后建议清理"
    return None


def suggest_cleanup(files: Iterable[Mapping]) -> list[CleanupSuggestion]:
    """对给定文件清单（name+size，可选 job_done）给出清理建议。只读不改。"""
    suggestions: list[CleanupSuggestion] = []
    seen: set[str] = set()
    for item in files or []:
        if item is None or not isinstance(item, Mapping):
            continue
        name = str(item.get("name", ""))
        if not name or name in seen:
            continue
        seen.add(name)
        size = int(item.get("size", 0) or 0)
        job_done = bool(item.get("job_done", False))
        reason = _classify(name, size, job_done=job_done)
        if reason:
            suggestions.append(CleanupSuggestion(name=name, reason=reason, size=size))
    return suggestions


def cleanup_text(suggestions: Iterable[CleanupSuggestion]) -> str:
    """把建议列表转成报告可用的文本（空则返回无需清理提示）。"""
    items = list(suggestions)
    if not items:
        return "未发现需要特别清理的超算文件。"
    lines = ["清理建议（仅供参看，不自动删除）："]
    for s in items:
        size_txt = f"（{s.size / 1024 / 1024:.0f} MiB）" if s.size else ""
        lines.append(f"- {s.name}{size_txt}：{s.reason}")
    return "\n".join(lines)
