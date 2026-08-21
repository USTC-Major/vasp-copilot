from __future__ import annotations

import json
import time
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from ..core.config import Settings
from ..core.errors import NotFoundError
from ..llm import get_explainer
from ..services.diagnosis_service import DiagnosisService
from ..services.run_store import RunStore
from .fallback import resolve_fallback
from .prompts import build_agent_messages
from .tools import (AgentState, DoctorTools, ToolResult,
                    doctor_tool_defs, doctor_tool_names)


class AgentCallResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    arguments: dict[str, Any] = {}
    ok: bool = True
    error_code: Optional[str] = None
    error_message: str = ""
    data: dict[str, Any] = {}
    confirmations: list[dict[str, Any]] = []
    messages: list[str] = []
    degraded: bool = False
    execution_time_ms: int = 0


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str = "ok"            # ok|confirmation_needed|error|degraded
    command: str = ""
    explanation: str = ""
    mode: str = "rule_based"      # rule_based|rule_plus_llm
    llm_generated: bool = False
    degraded: bool = False
    calls: list[AgentCallResult] = []
    confirmations: list[dict[str, Any]] = []


class AgentOrchestrator:
    """MVP 12.1/12.5：将自然语言 / 结构化 ID 映射为经验证的工具调用。

    LLM 路径可选；超时或输出非法时降级为确定性 fallback（rule_based）。
    只暴露 doctor 工具，且每次调用都经过 Pydantic 校验与状态检查；
    原始文件内容绝不会进入 prompt。"""

    def __init__(self, settings: Settings, store: RunStore,
                 service: Optional[DiagnosisService] = None,
                 explainer_factory=None) -> None:
        self._settings = settings
        self._store = store
        self._service = service or DiagnosisService()
        self._tools = DoctorTools(self._settings, self._store, self._service,
                                  explainer_factory=explainer_factory)
        self._tool_defs = doctor_tool_defs()
        self._tool_names = doctor_tool_names()
        self.max_tool_calls = 3

    # ---- state ----
    def _build_state(self, diagnosis_id: Optional[str]) -> AgentState:
        state = AgentState(diagnosis_id=diagnosis_id)
        if not diagnosis_id:
            return state
        try:
            record = self._store.get(diagnosis_id)
        except NotFoundError:
            state.diagnosis_status = "unknown"
            return state
        state.diagnosis_status = record.diagnosis_status
        state.upload_ok = True
        if record.result is not None:
            state.has_result = True
            state.available_issue_ids = [i.issue_id for i in record.result.issues]
        return state

    def _build_snapshot(self, state: AgentState) -> dict[str, Any]:
        snap: dict[str, Any] = {
            "session": {"diagnosis_id": state.diagnosis_id,
                        "diagnosis_status": state.diagnosis_status,
                        "has_result": state.has_result},
        }
        if not state.diagnosis_id:
            return snap
        try:
            record = self._store.get(state.diagnosis_id)
        except NotFoundError:
            return snap
        if record.detected is not None:
            snap["files_detected"] = [
                {"name": f.name, "kind": f.kind, "size_bytes": f.size}
                for f in record.detected.files][:50]
        if record.result is not None:
            r = record.result
            snap["diagnosis_summary"] = r.summary
            snap["issues"] = [
                {"issue_id": i.issue_id, "rule_id": i.rule_id,
                 "severity": i.severity.value, "title": i.title,
                 "auto_fixable": i.auto_fixable, "blocking": i.blocking,
                 "evidence_files": [e.file for e in i.evidence]}
                for i in r.issues]
            snap["next_step"] = {"allowed": r.next_step.allowed,
                                 "reason": r.next_step.reason}
            snap["missing_evidence"] = r.missing_evidence
        return snap

    # ---- entry ----
    def handle(self, command: str, *, diagnosis_id: Optional[str] = None,
               structured_call: Optional[dict] = None,
               use_llm: Optional[bool] = None,
               llm_client=None) -> AgentResponse:
        state = self._build_state(diagnosis_id)
        response = AgentResponse(command=command)

        # 1) structured ID / form path (deterministic, always available)
        if structured_call is not None:
            result = self._run_call(structured_call, state)
            response.calls = [result]
            response.confirmations = result.confirmations
            response.status = self._status_of(result)
            response.explanation = self._call_explanation(command, result)
            response.mode = "rule_based"
            return response

        # 2) optional LLM path with timeout/validation; degrades on failure
        llm_gate = (self._settings.feature_flags.llm_enabled
                    if use_llm is None else use_llm)
        client = llm_client if llm_client is not None else get_explainer(self._settings)
        if llm_gate and client is not None and hasattr(client, "complete"):
            resolved = self._try_llm(command, state, client)
            if resolved is not None:
                calls, confirmations, explanation = resolved
                response.calls = calls
                response.confirmations = confirmations
                response.explanation = explanation
                response.mode = "rule_plus_llm"
                response.llm_generated = True
                response.status = self._status_of_many(calls)
                return response
            response.degraded = True

        # 3) deterministic fallback (MVP 12.5)
        fb = resolve_fallback(command, state)
        if fb.confirmations:
            response.status = "confirmation_needed"
            response.confirmations = fb.confirmations
            response.explanation = fb.explanation or "需要补充关键信息后继续。"
        elif fb.intent:
            result = self._tools.execute(fb.intent, fb.args, state)
            call = self._to_call(fb.intent, fb.args, result)
            response.calls = [call]
            response.confirmations = result.confirmations
            response.explanation = self._call_explanation(command, call)
            response.status = self._status_of(call)
        else:
            response.status = "error"
            response.explanation = "无法确定执行计划，请补充信息。"
        return response

    # ---- execution ----
    def _run_call(self, item: dict, state: AgentState) -> AgentCallResult:
        name = item.get("name") if isinstance(item, dict) else None
        args = item.get("arguments", {}) if isinstance(item, dict) else {}
        if not isinstance(name, str):
            result = ToolResult(ok=False, error_code="INVALID_CALL",
                                error_message="结构化调用缺少 name")
            return self._to_call("", args, result)
        started = time.monotonic()
        tool_result = self._tools.execute(name, args, state)
        call = self._to_call(name, args, tool_result)
        call.execution_time_ms = int((time.monotonic() - started) * 1000)
        return call

    def _try_llm(self, command: str, state: AgentState, client):
        snapshot = self._build_snapshot(state)
        messages = build_agent_messages(snapshot, command, self._tool_defs)
        try:
            raw = client.complete(messages)
        except Exception:
            return None
        payload = self._parse_llm_payload(raw)
        if payload is None:
            return None
        calls: list[AgentCallResult] = []
        confirmations: list[dict[str, Any]] = []
        for item in payload.get("calls", [])[:self.max_tool_calls]:
            name = item.get("name") if isinstance(item, dict) else None
            if not isinstance(name, str) or name not in self._tool_names:
                return None  # permission violation -> fall to rule_based
            call = self._run_call(item, state)
            calls.append(call)
            confirmations.extend(call.confirmations)
        return calls, confirmations, payload.get("explanation") or ""

    @staticmethod
    def _parse_llm_payload(raw: str) -> Optional[dict]:
        if not isinstance(raw, str):
            return None
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text.lstrip("json").strip()
        try:
            data = json.loads(text)
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    # ---- helpers ----
    @staticmethod
    def _to_call(name: str, arguments: dict, result: ToolResult) -> AgentCallResult:
        return AgentCallResult(
            name=name, arguments=dict(arguments) if isinstance(arguments, dict)
            else {}, ok=result.ok, error_code=result.error_code,
            error_message=result.error_message, data=result.data,
            confirmations=result.confirmations, messages=result.messages,
            degraded=result.degraded)

    @staticmethod
    def _status_of(call: AgentCallResult) -> str:
        if call.degraded:
            return "degraded"
        if not call.ok:
            if call.confirmations:
                return "confirmation_needed"
            return "error"
        if call.confirmations:
            return "confirmation_needed"
        return "ok"

    @staticmethod
    def _status_of_many(calls: list[AgentCallResult]) -> str:
        if not calls:
            return "ok"
        if any(c.degraded for c in calls):
            return "degraded"
        if any(not c.ok and not c.confirmations for c in calls):
            return "error"
        if any(c.confirmations for c in calls):
            return "confirmation_needed"
        return "ok"

    @staticmethod
    def _call_explanation(command: str, call: AgentCallResult) -> str:
        if call.ok:
            if call.name == "run_diagnosis":
                counts = call.data.get("issue_count") or {}
                return ("已运行确定性诊断，未发现新信息需人工读取原始文件。"
                        "问题数：critical=" + str(counts.get("critical", 0))
                        + " high=" + str(counts.get("high", 0))
                        + " medium=" + str(counts.get("medium", 0))
                        + "；下一步是否允许：" + str(call.data.get("next_step", {}).get("allowed")))
            if call.name == "generate_report":
                return ("报告已生成（report_id="
                        + str(call.data.get("report_id"))
                        + "），可下载：" + str(call.data.get("download_url")))
            if call.name == "generate_fix":
                if call.data.get("fix_available"):
                    return ("已为选中 issue 生成白名单修复，可下载："
                            + str(call.data.get("download_url")))
                return "没有可通过白名单安全生成的自动修复，请按建议人工核验。"
            return "操作已完成。"
        if call.error_code == "CONFIRMATION_REQUIRED":
            return "操作需要用户确认，未执行任何修复。"
        return call.error_message or "操作未完成。"
