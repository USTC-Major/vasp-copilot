from __future__ import annotations

from ..engine import IssueBuilder, Rule
from ..issue_builder import build_issue
from ...schemas.issue import Issue
from ...schemas.parsed import ParsedRunData
from ...schemas.status import Severity


class FileMissingRule(Rule):
    rule_id = "REQUIRED_FILE_MISSING"
    category = "files"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        have = set(parsed.source_files)
        missing = []
        if "INCAR" not in have:
            missing.append("INCAR")
        # POSCAR/CONTCAR 均为结构文件（设计 4.2/VaspFileDetector），任一存在即可。
        if "POSCAR" not in have and "CONTCAR" not in have:
            missing.append("POSCAR")
        if not missing:
            return []
        return [
            build_issue(
                rule_id=self.rule_id,
                severity=Severity.HIGH,
                category=self.category,
                title="关键输入文件缺失",
                summary="缺失: " + ", ".join(missing) + "；因此相关维度无法判断。",
                evidence=[{"file": m, "message": "缺失该文件"} for m in missing],
                recommendations=[
                    {"action": "review", "target": "manifest", "rationale": "补齐缺失文件后再诊断"}
                ],
                confidence=1.0,
                blocking=True,
                possible_causes=["上传不完整", "目录结构不正确"],
            )
        ]


class ElementOrderRule(Rule):
    rule_id = "ELEMENT_ORDER_INCONSISTENT"
    category = "files"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        incar = parsed.incar.effective
        elems = parsed.poscar.elements
        if not elems:
            return []
        # We cannot verify order without an element mapping / POTCAR header.
        # MER: only report length-level risk when a per-element array has the
        # same length as element count (i.e., not a length mismatch).
        for key in ("MAGMOM", "LDAUU", "LDAUL", "LDAUJ"):
            arr = incar.get(key)
            if isinstance(arr, list) and len(arr) == len(elems):
                return [
                    build_issue(
                        rule_id=self.rule_id,
                        severity=Severity.LOW,
                        category=self.category,
                        title="元素顺序无法自动核对",
                        summary=f"存在长度与元素数一致的数组 {key}，但缺少 element mapping/POTCAR 头，无法断言顺序，置信度较低。",
                        evidence=[{"file": parsed.poscar.source_file or "POSCAR", "message": "只能按长度核对，无法核对顺序"}],
                        recommendations=[
                            {"action": "review", "target": "user", "rationale": "请核对数组与 POSCAR 的元素顺序是否一致"}
                        ],
                        confidence=0.3,
                        auto_fixable=False,
                    )
                ]
        return []

class PotcarPoscarMismatchRule(Rule):
    """POTCAR 顺序无法由 Doctor 自动校验（按安全策略从不恢复 POTCAR 内容）。

    仅当顺序有影响（存在逐元素 INCAR 数组）且 POTCAR 存在时，
    才发出证据不足的提示。"""

    rule_id = "POTCAR_POSCAR_MISMATCH"
    category = "files"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        have = set(parsed.source_files)
        if "POTCAR" not in have or not parsed.poscar.elements:
            return []
        incar = parsed.incar.effective
        has_per_elem = any(isinstance(incar.get(k), list) for k in ("LDAUU", "LDAUL", "LDAUJ", "MAGMOM"))
        if not has_per_elem:
            return []
        return [
            build_issue(
                rule_id=self.rule_id,
                severity=Severity.LOW,
                category=self.category,
                title="POTCAR 顺序无法自动核对",
                summary="存在按元素数组且提供 POTCAR，但 Doctor 不读取赝势内容，无法核对顺序；请人工确认。",
                evidence=[{"file": "POTCAR", "message": "POTCAR 内容不在回收范围内，无法核对顺序"}],
                recommendations=[
                    {"action": "review", "target": "user", "rationale": "人工核对 POTCAR 与 POSCAR 的元素顺序"}
                ],
                confidence=0.3,
                auto_fixable=False,
            )
        ]
