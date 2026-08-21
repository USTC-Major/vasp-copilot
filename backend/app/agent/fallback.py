from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .tools import AgentState


@dataclass
class FallbackResolution:
    intent: Optional[str] = None
    args: dict[str, Any] = field(default_factory=dict)
    confirmations: list[dict[str, Any]] = field(default_factory=list)
    explanation: str = ""


_INTENT_KEYWORDS = [
    ("run_diagnosis", ("诊断", "检查", "分析", "看看", "排查", "运行", "run", "diagnos")),
    ("generate_fix", ("修复", "修正", "修改", "补丁", "fix", "打补丁")),
    ("generate_report", ("报告", "出报告", "报表", "summary", "report", "总结")),
]


def _normalize(text: str) -> str:
    return (text or "").strip().lower()


def resolve_fallback(command: str, state: AgentState) -> FallbackResolution:
    text = _normalize(command)
    matched = None
    for intent, keywords in _INTENT_KEYWORDS:
        score = 0
        for kw in keywords:
            if kw in text:
                score += 1
        if score and (matched is None or score > matched[1]):
            matched = (intent, score)

    if matched is None:
        return FallbackResolution(
            intent=None,
            explanation="无法确定意图，请明确要执行的操作。",
            confirmations=[{
                "field": "intent",
                "message": "请选择要执行的操作：诊断 / 修复 / 报告",
                "options": ["run_diagnosis", "generate_fix", "generate_report"],
            }])

    intent = matched[0]
    if intent == "run_diagnosis":
        if not state.upload_ok or not state.diagnosis_id:
            return FallbackResolution(
                intent="run_diagnosis",
                explanation="缺少 diagnosis_id，请先上传运行目录。",
                confirmations=[{
                    "field": "diagnosis_id",
                    "message": "run_diagnosis 需要先上传运行目录并取得 diagnosis_id",
                }])
        return FallbackResolution(intent="run_diagnosis",
                                  args={"diagnosis_id": state.diagnosis_id},
                                  explanation="将运行确定性诊断。")
    if intent == "generate_report":
        if not state.has_result or not state.diagnosis_id:
            return FallbackResolution(
                intent="generate_report",
                explanation="请先运行诊断再生成报告。",
                confirmations=[{
                    "field": "diagnosis_id",
                    "message": "generate_report 需要先执行 run_diagnosis",
                }])
        return FallbackResolution(intent="generate_report",
                                  args={"diagnosis_id": state.diagnosis_id,
                                        "language": "zh-CN"},
                                  explanation="将生成 Markdown 诊断报告。")
    if intent == "generate_fix":
        if not state.has_result or not state.diagnosis_id:
            return FallbackResolution(
                intent="generate_fix",
                explanation="请先运行诊断再生成修复。",
                confirmations=[{
                    "field": "diagnosis_id",
                    "message": "generate_fix 需要先执行 run_diagnosis",
                }])
        return FallbackResolution(
            intent="generate_fix",
            explanation="需要选定 issue 并确认后才能生成修复。",
            args={"diagnosis_id": state.diagnosis_id,
                  "issue_ids": [], "user_confirmed": False},
            confirmations=[{
                "field": "issue_ids",
                "message": "generate_fix 需要选择要修复的 issue 并显式确认",
                "options": state.available_issue_ids or None,
                "requires_user_confirmation": True,
            }])
    return FallbackResolution(intent=None, explanation="未识别操作。")
