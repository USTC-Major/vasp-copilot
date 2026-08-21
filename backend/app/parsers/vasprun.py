"""最小化可选 vasprun.xml 解析器（MVP 3.3：vasprun.xml 可选）。

设计只要求对大小受限的 vasprun.xml 做可选探测/接收；详细的证据提取
（能量/力/应力趋势）属于 roadmap 项。因此该解析器只提取微小安全的摘要，
绝不喂给确定性规则，诊断结果保持不变。"""

from __future__ import annotations

import re

from ..schemas.vasprun import VasprunInfo

VASPRUN_MAX_PARSE_BYTES = 2 * 1024 * 1024  # 受限大小 vasprun.xml（设计回收白名单）

_ENERGY_RE = re.compile(
    r'<i\s+name="e_fr_energy">\s*'
    r'([-+]?(?:\d+\.?\d*|\.\d+)(?:[eEdD][-+]?\d+)?)\s*</i>',
    re.IGNORECASE)
_CONV_RE = re.compile(
    r'<c\s+name="reached_required_accuracy">\s*([TtFf])\s*</c>')


def _to_float(token: str) -> float:
    return float(token.replace("D", "E").replace("d", "e"))


def parse_vasprun(text: str, *, source_file: str = "vasprun.xml") -> VasprunInfo:
    """返回一个小的安全摘要；无法解析/空内容时直接产出空值。"""
    final_energy = None
    for m in _ENERGY_RE.finditer(text):
        final_energy = _to_float(m.group(1))
    convs = [m.group(1).upper() == "T" for m in _CONV_RE.finditer(text)]
    return VasprunInfo(
        source_file=source_file,
        present=True,
        size_bytes=len(text.encode("utf-8", "ignore")),
        truncated=False,
        final_energy=final_energy,
        converged=convs[-1] if convs else None,
        n_ionic_steps=len(convs),
        warnings=[],
    )
