"""WorkflowService（IR-03）：WorkflowGenerationPipeline 之上的服务层门面。

在内存中保存生成的 bundle（单进程本地 demo），使下载端点可服务确定性
zip 字节；同时缓存 plan 阶段元数据，使 ``GET /api/v1/workflows/{workflow_id}``
（设计 6.6）可提供 plan/status/revision/file_tree 用于刷新与会话恢复。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

from backend.app.core.errors import NotFoundError
from backend.app.schemas.generation import WorkflowGenerateRequest
from backend.app.services.file_store import FileStore
from backend.app.workflow.pipeline import WorkflowGenerationPipeline


@dataclass
class WorkflowArtifact:
    workflow_id: str
    zip_bytes: bytes
    body: Dict[str, Any]
    created_at: float = field(default_factory=time.time)
    touched_at: float = field(default_factory=time.time)


@dataclass
class WorkflowPlanRecord:
    """Design 6.6 plan 阶段元数据（供刷新/恢复 session）。"""

    workflow_id: str
    workflow_status: str  # planned | needs_confirmation
    plan: Dict[str, Any]  # {schema_version, steps, file_inheritance_plan}
    confirmations: List[Dict[str, Any]] = field(default_factory=list)
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    needs_confirmation: bool = False
    request: "WorkflowGenerateRequest | None" = None
    created_at: float = field(default_factory=time.time)
    touched_at: float = field(default_factory=time.time)


class WorkflowService:
    """API 层使用的 plan/generate/download 门面（IR-01/IR-03）。"""

    def __init__(self, ttl_seconds: int = 24 * 3600,
                 potcar_prepared: bool = False,
                 file_store: Optional[FileStore] = None) -> None:
        self._pipeline = WorkflowGenerationPipeline(potcar_prepared=potcar_prepared)
        self._ttl_seconds = ttl_seconds
        self._file_store = file_store
        self._artifacts: Dict[str, WorkflowArtifact] = {}
        self._plans: Dict[str, WorkflowPlanRecord] = {}

    @staticmethod
    def _echo_blocks(request: WorkflowGenerateRequest) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """构造 dftu/scheduler 的稳定响应表示（单一数据源，6.4 节）。

        由同一个 ``request`` 一次构造，同时进入 POST plan 返回与
        ``_plans`` 缓存的 plan 字典，保证 POST plan、GET workflow、
        generate replay 三处的字段与语义一致。scheduler 块对齐响应侧
        ``SchedulerBlock``（scheduler_type + 可选 scheduler_profile_id）。
        """
        scheduler = request.scheduler
        dftu_block = request.dftu.model_dump(mode="json")
        scheduler_block = {
            "scheduler_profile_id": None,
            "scheduler_type": scheduler.type,
            "nodes": scheduler.nodes,
            "tasks_per_node": scheduler.tasks_per_node,
            "walltime": scheduler.walltime,
            "vasp_binary_hint": scheduler.vasp_binary_hint,
        }
        return dftu_block, scheduler_block

    def plan(self, request: WorkflowGenerateRequest) -> Dict[str, Any]:
        """Plan 阶段预览（IR-01）；缓存 plan 元数据与请求供 GET/回放。"""
        preview = self._pipeline.preview_plan(request)
        status = "needs_confirmation" if preview.get("needs_confirmation") else "planned"
        dftu_block, scheduler_block = self._echo_blocks(request)
        # 回显块同时写入 POST 返回体与缓存 plan（GET 透传），单一构造。
        preview["dftu"] = dftu_block
        preview["scheduler"] = scheduler_block
        self._plans[request.workflow_id] = WorkflowPlanRecord(
            workflow_id=request.workflow_id,
            workflow_status=status,
            request=request,
            plan={
                "schema_version": "1.0",
                "steps": preview.get("steps", []),
                "file_inheritance_plan": preview.get("file_inheritance_plan", {}),
                "recipe_compositions": preview.get("recipe_compositions", []),
                "dftu": dftu_block,
                "scheduler": scheduler_block,
            },
            confirmations=preview.get("confirmations", []),
            conflicts=preview.get("conflicts", []),
            warnings=preview.get("warnings", []),
            needs_confirmation=bool(preview.get("needs_confirmation")),
        )
        return preview

    def generate(self, request: WorkflowGenerateRequest) -> Dict[str, Any]:
        """运行完整生成管线并缓存 bundle 供下载。"""
        result = self._pipeline.generate(request)
        self._register_generated_files(result)
        body = result.to_response_body()
        body["steps"] = [step.model_dump(mode="json") for step in result.steps]
        self._artifacts[request.workflow_id] = WorkflowArtifact(
            workflow_id=request.workflow_id,
            zip_bytes=result.bundle.zip_bytes,
            body=body,
        )
        return body

    def _register_generated_files(self, result) -> None:
        """把生成产物按其文件树 file_id 注册进 file_store，供预览端点读取。"""
        if self._file_store is None:
            return
        files = result.bundle.files  # relative_path -> bytes
        stack = list(result.file_tree.children)
        while stack:
            node = stack.pop()
            if node.type == "directory":
                stack.extend(node.children)
                continue
            if node.file_id and node.relative_path in files:
                self._file_store.register_file(
                    node.file_id, node.name, "generated", files[node.relative_path]
                )

    def replay_request(self, workflow_id: str) -> WorkflowGenerateRequest:
        """重放 plan 阶段的完整请求（前端 generate 仅携带 workflow_id 时使用）。"""
        plan = self._plans.get(workflow_id)
        if plan is None or plan.request is None \
                or time.time() - plan.touched_at > self._ttl_seconds:
            raise NotFoundError("WORKFLOW_NOT_FOUND",
                                "no plan recorded for workflow id")
        plan.touched_at = time.time()
        return plan.request
    def get_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """设计 6.6：plan/status/revision/确认项/file_tree 元数据。"""
        artifact = self._artifacts.get(workflow_id)
        if artifact is not None \
                and time.time() - artifact.touched_at <= self._ttl_seconds:
            artifact.touched_at = time.time()
            body = dict(artifact.body)
            body["download_url"] = f"/api/v1/workflows/{workflow_id}/download"
            plan = self._plans.get(workflow_id)
            if plan is not None:
                body.setdefault("plan", plan.plan)
                body.setdefault("confirmations", plan.confirmations)
                body.setdefault("conflicts", plan.conflicts)
                body.setdefault("warnings", plan.warnings)
            return body
        plan = self._plans.get(workflow_id)
        if plan is not None and time.time() - plan.touched_at <= self._ttl_seconds:
            plan.touched_at = time.time()
            return {
                "workflow_id": plan.workflow_id,
                "workflow_status": plan.workflow_status,
                "plan": plan.plan,
                "confirmations": plan.confirmations,
                "conflicts": plan.conflicts,
                "warnings": plan.warnings,
                "needs_confirmation": plan.needs_confirmation,
            }
        raise NotFoundError("WORKFLOW_NOT_FOUND",
                            "unknown or expired workflow id")

    def get_artifact(self, workflow_id: str) -> WorkflowArtifact:
        artifact = self._artifacts.get(workflow_id)
        if artifact is None or time.time() - artifact.touched_at > self._ttl_seconds:
            raise NotFoundError("WORKFLOW_NOT_FOUND",
                                "unknown or expired workflow id")
        artifact.touched_at = time.time()
        return artifact
