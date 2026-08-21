from __future__ import annotations

from ..issue_builder import build_issue
from ..engine import Rule
from ...schemas.issue import Issue
from ...schemas.parsed import ParsedRunData
from ...schemas.status import Severity


def _cat_keywords(parsed: ParsedRunData, cat: str) -> list:
    out = []
    for job in parsed.job_logs:
        for kw in job.keywords:
            if kw.get("category") == cat:
                out.append(kw)
    return out


class JobOomRule(Rule):
    rule_id = "JOB_OOM"
    category = "scheduler"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        hits = _cat_keywords(parsed, "oom")
        if not hits:
            return []
        kw = hits[0]
        return [build_issue(
            rule_id=self.rule_id, severity=Severity.HIGH, category=self.category,
            title="作业内存不足(OOM)",
            summary="作业日志出现 OOM/内存超限证据。",
            evidence=[{"file": kw.get("file", "job.log"), "line": kw.get("line"),
                       "message": kw.get("text", "")}],
            recommendations=[
                {"action": "review", "target": "submit", "rationale": "减小核/内存占用、增加内存、检查 NCORE/KPAR"}],
            confidence=0.9, blocking=True,
            possible_causes=["内存配给不足", "并行设置不合理"],
        )]


class JobTimeLimitRule(Rule):
    rule_id = "JOB_TIME_LIMIT"
    category = "scheduler"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        hits = _cat_keywords(parsed, "time_limit")
        if not hits:
            return []
        kw = hits[0]
        return [build_issue(
            rule_id=self.rule_id, severity=Severity.HIGH, category=self.category,
            title="作业超时/到达时间上限",
            summary="作业日志出现 TIME LIMIT/超时证据。",
            evidence=[{"file": kw.get("file", "job.log"), "line": kw.get("line"),
                       "message": kw.get("text", "")}],
            recommendations=[
                {"action": "review", "target": "submit", "rationale": "增加 walltime 或分段续算，检查收敛慢原因"}],
            confidence=0.9, blocking=True,
            possible_causes=["walltime 不足", "计算收敛慢"],
        )]


class ModuleNotFoundRule(Rule):
    rule_id = "MODULE_NOT_FOUND"
    category = "scheduler"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        hits = _cat_keywords(parsed, "module")
        if not hits:
            return []
        kw = hits[0]
        return [build_issue(
            rule_id=self.rule_id, severity=Severity.HIGH, category=self.category,
            title="模块/命令不存在",
            summary="作业日志出现模块加载或命令不存在错误。",
            evidence=[{"file": kw.get("file", "job.log"), "line": kw.get("line"),
                       "message": kw.get("text", "")}],
            recommendations=[
                {"action": "review", "target": "user", "rationale": "使用集群实际模块名，不要由系统猜模块"}],
            confidence=0.9, blocking=True,
            possible_causes=["模块名错误", "VASP 命令不存在"],
        )]


class PathOrFileNotFoundRule(Rule):
    rule_id = "PATH_OR_FILE_NOT_FOUND"
    category = "scheduler"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        hits = _cat_keywords(parsed, "path")
        if not hits:
            return []
        kw = hits[0]
        return [build_issue(
            rule_id=self.rule_id, severity=Severity.HIGH, category=self.category,
            title="路径/文件不存在",
            summary="作业日志出现 no such file/cannot open 等路径错误。",
            evidence=[{"file": kw.get("file", "job.log"), "line": kw.get("line"),
                       "message": kw.get("text", "")}],
            recommendations=[
                {"action": "review", "target": "user", "rationale": "检查运行目录、脚本相对路径与所需文件"}],
            confidence=0.9, blocking=True,
            possible_causes=["相对路径错误", "文件缺失"],
        )]


class ParallelConfigRiskRule(Rule):
    rule_id = "PARALLEL_CONFIG_RISK"
    category = "scheduler"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        eff = parsed.incar.effective
        ncore = eff.get("NCORE")
        kpar = eff.get("KPAR")
        have_cores = _cat_keywords(parsed, "scheduler") or True
        # No total core count is known -> INSUFFICIENT_RESOURCE_EVIDENCE
        missing = {}
        if isinstance(ncore, int) and isinstance(kpar, int) and ncore * kpar == 0:
            missing = {"NCORE": ncore, "KPAR": kpar}
        if not missing:
            return []
        return [build_issue(
            rule_id=self.rule_id, severity=Severity.INFO, category=self.category,
            title="并行配置资源证据不足",
            summary="缺少总核数/k 点数信息，无法做整除判断；给出算术相关提醒。",
            evidence=[{"file": "INCAR", "message": f"NCORE={ncore}, KPAR={kpar}",
                      "data_ref": "INSUFFICIENT_RESOURCE_EVIDENCE"}],
            recommendations=[
                {"action": "review", "target": "submit", "rationale": "结合集群实际核数与 k 点数核对并行设置"}],
            confidence=0.4, blocking=False,
            possible_causes=["并行设置与资源不匹配", "资源信息缺失"],
        )]