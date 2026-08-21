from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict

from ..core.config import Settings
from ..core.errors import NotFoundError
from ..diagnostics.fixes import FixGenerator
from ..llm import get_explainer
from ..schemas.status import ModeKind
from ..services.diagnosis_service import DiagnosisService, _load_parsed
from ..services.run_store import RunStore


class RunDiagnosisArgs(BaseModel):
    """Agent 工具参数：run_diagnosis（MVP 12.2）。"""

    model_config = ConfigDict(extra="forbid")

    diagnosis_id: str
    selected_root: Optional[str] = None
    resources: Optional[dict[str, Any]] = None


class GenerateFixArgs(BaseModel):
    """Agent 工具参数：generate_fix（MVP 12.2）。"""

    model_config = ConfigDict(extra="forbid")

    diagnosis_id: str
    issue_ids: list[str]
    user_confirmed: bool


class GenerateReportArgs(BaseModel):
    """Agent 工具参数：generate_report（MVP 12.2）。"""

    model_config = ConfigDict(extra="forbid")

    diagnosis_id: str
    language: Literal["zh-CN", "en"]
    include_llm_explanation: bool = False


DOCTOR_TOOL_DEFS = [
    {
        "name": "run_diagnosis",
        "description": "解析上传目录并运行确定性规则",
        "input_schema": {
            "type": "object",
            "required": ["diagnosis_id"],
            "properties": {
                "diagnosis_id": {"type": "string"},
                "selected_root": {"type": "string"},
                "resources": {"type": "object"},
            },
        },
        "args_model": RunDiagnosisArgs,
    },
    {
        "name": "generate_fix",
        "description": "为选定 issue 生成白名单修复文件（有副作用，需 user_confirmed）",
        "input_schema": {
            "type": "object",
            "required": ["diagnosis_id", "issue_ids", "user_confirmed"],
            "properties": {
                "diagnosis_id": {"type": "string"},
                "issue_ids": {"type": "array", "items": {"type": "string"}},
                "user_confirmed": {"type": "boolean"},
            },
        },
        "args_model": GenerateFixArgs,
    },
    {
        "name": "generate_report",
        "description": "由结构化诊断生成 Markdown 报告",
        "input_schema": {
            "type": "object",
            "required": ["diagnosis_id", "language"],
            "properties": {
                "diagnosis_id": {"type": "string"},
                "language": {"enum": ["zh-CN", "en"]},
                "include_llm_explanation": {"type": "boolean"},
            },
        },
        "args_model": GenerateReportArgs,
    },
]


def doctor_tool_defs() -> list[dict]:
    """Agent 工具 schema，input_schema 内联（无未解析 $ref，MVP 12.2）。"""
    return [{k: t[k] for k in ("name", "description", "input_schema")}
            for t in DOCTOR_TOOL_DEFS]


def doctor_tool_names() -> set[str]:
    return {t["name"] for t in DOCTOR_TOOL_DEFS}


@dataclass
class AgentState:
    """Agent 可消费的结构化最小会话状态（不含原始文件内容）。"""

    diagnosis_id: Optional[str] = None
    diagnosis_status: Optional[str] = None
    has_result: bool = False
    available_issue_ids: list[str] = field(default_factory=list)
    upload_ok: bool = False


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error_code: Optional[str] = None
    error_message: str = ""
    confirmations: list[dict[str, Any]] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    degraded: bool = False


_INCAR_CANDIDATES = ("INCAR", "incar")


def _read_incar(base_dir) -> str:
    for name in _INCAR_CANDIDATES:
        p = base_dir / name
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""
    return ""


def _count_by_severity(issues) -> dict[str, int]:
    counts: dict[str, int] = {}
    for i in issues:
        counts[i.severity.value] = counts.get(i.severity.value, 0) + 1
    return counts


class DoctorTools:
    """Agent 工具门面：Agent 唯一可调用的入口（MVP 12.1/12.4）。

    绝不暴露原始路径、POTCAR 内容或任意命令执行；
    工具只操作服务生成的 ID。"""

    def __init__(self, settings: Settings, store: RunStore,
                 service: DiagnosisService, explainer_factory=None) -> None:
        self._settings = settings
        self._store = store
        self._service = service
        self._fixer = FixGenerator()
        self._explainer_factory = explainer_factory

    def execute(self, name: str, arguments: dict[str, Any],
                state: AgentState) -> ToolResult:
        meta = next((t for t in DOCTOR_TOOL_DEFS if t["name"] == name), None)
        if meta is None:
            return ToolResult(ok=False, error_code="UNKNOWN_TOOL",
                              error_message="不允许的工具调用: " + str(name))
        try:
            args = meta["args_model"](**arguments)
        except Exception as exc:  # pydantic ValidationError -> reject bad args
            return ToolResult(ok=False, error_code="INVALID_ARGUMENTS",
                              error_message="工具参数校验失败: " + str(exc))
        method = getattr(self, name)
        return method(args, state)

    def run_diagnosis(self, args: RunDiagnosisArgs,
                      state: AgentState) -> ToolResult:
        try:
            record = self._store.get(args.diagnosis_id)
        except NotFoundError as exc:
            return ToolResult(ok=False, error_code="DIAGNOSIS_NOT_FOUND",
                              error_message=str(getattr(exc, "message", exc)))
        if args.resources is not None and (len(args.resources) > 64
                                           or len(str(args.resources)) > 8192):
            return ToolResult(ok=False, error_code="RESOURCES_TOO_LARGE",
                              error_message="resources 超过上限")
        parsed = _load_parsed(record.base_dir, None)
        result, body, fix_files = self._service.run_diagnosis(
            parsed, record.base_dir, llm_explanation=False,
            settings=self._settings)
        result.diagnosis_id = args.diagnosis_id
        record.result = result
        record.report_text = body
        record.report_metadata = result.report
        if result.recommended_fixes and result.recommended_fixes[0].safe_to_generate:
            record.fix_files = {result.recommended_fixes[0].fix_id: fix_files}
        record.diagnosis_status = "succeeded"
        self._store.put(record)
        return ToolResult(ok=True, data={
            "diagnosis_status": "succeeded",
            "issue_count": _count_by_severity(result.issues),
            "issues": [i.model_dump(exclude_none=True) for i in result.issues],
            "recommended_fixes": [f.model_dump(exclude_none=True)
                                  for f in result.recommended_fixes],
            "next_step": result.next_step.model_dump(exclude_none=True),
            "mode": result.provenance.mode.value,
            "summary": result.summary,
        })

    def generate_fix(self, args: GenerateFixArgs,
                     state: AgentState) -> ToolResult:
        try:
            record = self._store.get(args.diagnosis_id)
        except NotFoundError as exc:
            return ToolResult(ok=False, error_code="DIAGNOSIS_NOT_FOUND",
                              error_message=str(getattr(exc, "message", exc)))
        if record.result is None:
            return ToolResult(ok=False, error_code="RUN_REQUIRED",
                              error_message="generate_fix 需要先执行 run_diagnosis")
        known = {i.issue_id for i in record.result.issues}
        unknown = [iid for iid in args.issue_ids if iid not in known]
        if unknown:
            return ToolResult(ok=False, error_code="UNKNOWN_ISSUE_ID",
                              error_message="未知 issue_id: " + ",".join(unknown))
        if not args.issue_ids:
            return ToolResult(ok=False, error_code="NO_ISSUE_SELECTED",
                              error_message="未选择任何 issue")
        if not args.user_confirmed:
            return ToolResult(
                ok=False, error_code="CONFIRMATION_REQUIRED",
                error_message="generate_fix 需要用户明确确认后才能生成修复文件",
                confirmations=[{
                    "field": "user_confirmed",
                    "message": "请确认同意按白名单建议修改参数（只会生成 INCAR.fixed，不覆盖原件）",
                    "issue_ids": args.issue_ids,
                }])
        selected = [i for i in record.result.issues if i.issue_id in set(args.issue_ids)]
        parsed = _load_parsed(record.base_dir, None)
        incar_text = _read_incar(record.base_dir)
        fix, fix_files = self._fixer.generate(parsed=parsed, issues=selected,
                                              incar_text=incar_text)
        if fix.safe_to_generate and fix_files:
            record.fix_files = {fix.fix_id: fix_files}
            self._store.put(record)
            return ToolResult(ok=True, data={
                "fix": fix.model_dump(exclude_none=True),
                "fix_available": True,
                "files": sorted(fix_files.keys()),
                "download_url": "/api/v1/diagnosis/" + args.diagnosis_id + "/download-fix",
            }, messages=["已为选中 issue 生成白名单修复。"])
        return ToolResult(
            ok=True,
            data={"fix": fix.model_dump(exclude_none=True),
                  "fix_available": False,
                  "warnings": fix.warnings},
            messages=["没有可通过白名单安全生成的自动修复，请按建议人工核验后应用。"])

    def generate_report(self, args: GenerateReportArgs,
                        state: AgentState) -> ToolResult:
        try:
            record = self._store.get(args.diagnosis_id)
        except NotFoundError as exc:
            return ToolResult(ok=False, error_code="DIAGNOSIS_NOT_FOUND",
                              error_message=str(getattr(exc, "message", exc)))
        if record.result is None:
            return ToolResult(ok=False, error_code="RUN_REQUIRED",
                              error_message="generate_report 需要先执行 run_diagnosis")
        if args.language != "zh-CN":
            # 设计 12.2 工具枚举含 en；MVP 报告模板暂只有 zh-CN，按 12.5 降级，不伪造英文报告。
            return ToolResult(
                ok=False, error_code="LANGUAGE_UNSUPPORTED",
                error_message="MVP 阶段报告仅支持 zh-CN，en 暂不可用",
                degraded=True,
                data={"requested_language": args.language,
                      "supported_languages": ["zh-CN"]})
        result = record.result
        if args.include_llm_explanation:
            explainer = self._get_explainer()
            if explainer is not None:
                try:
                    result.llm_explanation = explainer.explain(result)
                    result.provenance.llm_used = True
                    result.provenance.mode = ModeKind.RULE_PLUS_LLM
                except Exception:
                    result.llm_explanation = None
                    result.provenance.llm_used = False
                    result.provenance.mode = ModeKind.RULE_BASED
        body, meta = self._service._reporter.generate(result)
        record.report_text = body
        record.report_metadata = meta
        self._store.put(record)
        return ToolResult(ok=True, data={
            "report_ready": bool(body),
            "report_id": meta.report_id,
            "language": "zh-CN",
            "size_bytes": meta.size_bytes,
            "sha256": meta.sha256,
            "download_url": "/api/v1/diagnosis/" + args.diagnosis_id + "/report",
        })

    def _get_explainer(self):
        if self._explainer_factory is not None:
            return self._explainer_factory(self._settings)
        return get_explainer(self._settings)
