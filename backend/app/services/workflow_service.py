"""WorkflowService（IR-03）：WorkflowGenerationPipeline 之上的服务层门面。

在内存中保存生成的 bundle（单进程本地 demo），使下载端点可服务确定性
zip 字节；同时缓存 plan 阶段元数据，使 ``GET /api/v1/workflows/{workflow_id}``
（设计 6.6）可提供 plan/status/revision/file_tree 用于刷新与会话恢复。"""

from __future__ import annotations

import time
import copy
import hashlib
import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List

from backend.app.core.errors import NotFoundError, ConflictError
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
    revision: int = 1
    potcar: Dict[str, Any] = field(default_factory=dict)


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
        self._guard = threading.RLock()
        self._attempts: Dict[str, object] = {}

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
        with self._guard:
            return self._publish_plan(request, preview, status, dftu_block, scheduler_block)

    def _publish_plan(self, request, preview, status, dftu_block, scheduler_block):
        previous = self._plans.get(request.workflow_id)
        plan_revision = previous.revision + 1 if previous else 1
        potcar = self._potcar_state(request, preview['steps'])
        preview.update(revision=plan_revision, potcar=potcar)
        self._plans[request.workflow_id] = WorkflowPlanRecord(
            workflow_id=request.workflow_id,
            workflow_status=status,
            request=request.model_copy(deep=True),
            revision=plan_revision,
            potcar=potcar,
            plan={
                "schema_version": "1.0",
                "steps": preview.get("steps", []),
                "file_inheritance_plan": preview.get("file_inheritance_plan", {}),
                "recipe_compositions": preview.get("recipe_compositions", []),
                "dftu": dftu_block,
                "scheduler": scheduler_block,
                "potcar": potcar,
                "revision": plan_revision,
            },
            confirmations=preview.get("confirmations", []),
            conflicts=preview.get("conflicts", []),
            warnings=preview.get("warnings", []),
            needs_confirmation=bool(preview.get("needs_confirmation")),
        )
        self._artifacts.pop(request.workflow_id, None)
        self._attempts.pop(request.workflow_id, None)
        return preview

    @staticmethod
    def _potcar_state(request, steps, status=None):
        status = status or ('pending_confirmation' if request.potcar.mode == 'include' else 'omitted')
        return {'mode': request.potcar.mode, 'status': status, 'artifact_id': request.potcar.artifact_id,
                'steps': [{'step_id': s['step_id'], 'status': status, 'artifact_id': request.potcar.artifact_id,
                           'sha256': None, 'size_bytes': None} for s in steps]}

    def _binding(self, request, revision, preview):
        from backend.toolbox.potcar.recommendations import RULE_VERSION
        rendered = self._pipeline.final_poscar(request)
        value = request.model_dump(mode='json')
        value['potcar'].pop('artifact_id', None)
        steps = [{'step_id': s['step_id'], 'parameters': s['parameters']} for s in preview['steps']]
        signature = {'request': value, 'final_poscar': rendered, 'steps': steps, 'rule_version': RULE_VERSION}
        sha = hashlib.sha256(json.dumps(signature, sort_keys=True, ensure_ascii=False,
                                       separators=(',', ':')).encode()).hexdigest()
        return {'workflow_id': request.workflow_id, 'revision': revision, 'request_sha256': sha,
                'step_ids': [s['step_id'] for s in preview['steps']]}

    @staticmethod
    def _potcar_context(request, preview):
        parameters = [s['parameters'] for s in preview['steps']]
        hybrid = any(p.get('LHFCALC') is True for p in parameters)
        methods = {'HSE06' if p.get('LHFCALC') is True else ('PBE+U' if p.get('LDAU') is True or request.dftu.enabled else 'PBE')
                   for p in parameters}
        pressure = any(isinstance(p.get('PSTRESS'), (float, int)) and not isinstance(p.get('PSTRESS'), bool)
                       and p['PSTRESS'] != 0 for p in parameters)
        spin = any(p.get('ISPIN') == 2 for p in parameters)
        return {'purpose': 'special' if len(methods) > 1 else 'regular',
                'functional': 'HSE06' if hybrid else ('PBE+U' if request.dftu.enabled else 'PBE'),
                'spin_polarized': True if spin else None, 'high_pressure': True if pressure else None,
                'short_bonds': None, 'high_unoccupied': None, 'magnetic_energy': None}

    def potcar_preview(self, workflow_id, payload, potcar_service):
        from backend.toolbox.potcar.service import exact
        exact(payload, {'revision', 'library_id', 'index_revision', 'dataset_ids'},
              {'revision', 'library_id', 'index_revision'})
        request = self.replay_request(workflow_id)
        record = self._plans[workflow_id]
        if type(payload['revision']) is not int or payload['revision'] != record.revision:
            raise ConflictError('WORKFLOW_REVISION_CONFLICT', '工作流计划已变化；请重新规划并核对', True)
        if request.potcar.mode != 'include':
            raise ConflictError('POTCAR_CONFIRMATION_REQUIRED', '请先将当前工作流计划设为包含 POTCAR', True)
        preview = self._pipeline.preview_plan(request)
        potcar_service.assembly.workflow_binding_validator = self._valid_potcar_binding
        potcar_service.assembly.workflow_publication_guard = self._potcar_publication_guard
        kwargs = {k: payload[k] for k in ('library_id', 'index_revision', 'dataset_ids') if k in payload}
        kwargs.update(poscar_text=self._pipeline.final_poscar(request), context=self._potcar_context(request, preview))
        return potcar_service.assembly.preview(kwargs, workflow_binding=self._binding(request, record.revision, preview))

    def _valid_potcar_binding(self, value):
        """Pure current-plan check injected into Toolbox, without extending TTL."""
        with self._guard:
            record = self._plans.get(value['workflow_id'])
            if (record is None or record.request is None or value['revision'] != record.revision or
                    time.time() - record.touched_at > self._ttl_seconds):
                return False
            recorded_request = record.request
            request = recorded_request.model_copy(deep=True)
        preview = self._pipeline.preview_plan(request)
        expected = self._binding(request, record.revision, preview)
        with self._guard:
            return (self._plans.get(value['workflow_id']) is record and record.request is recorded_request
                    and expected == value)

    @contextmanager
    def _potcar_publication_guard(self, value):
        # Compose outside the short publication lock, then preserve the exact
        # plan/request identity through the caller's atomic artifact rename.
        with self._guard:
            record = self._plans.get(value['workflow_id'])
            recorded_request = record.request if record else None
        valid = self._valid_potcar_binding(value)
        with self._guard:
            current = (valid and self._plans.get(value['workflow_id']) is record and record is not None
                       and record.request is recorded_request and time.time() - record.touched_at <= self._ttl_seconds)
            yield current

    def generate(self, request: WorkflowGenerateRequest, *, potcar_service=None) -> Dict[str, Any]:
        """运行完整生成管线并缓存 bundle 供下载。"""
        attempt = object()
        with self._guard:
            record = self._plans.get(request.workflow_id)
            current_revision = record.revision if record else 1
            self._attempts[request.workflow_id] = attempt
            if request.potcar.mode == 'include':
                self._artifacts.pop(request.workflow_id, None)
                if record:
                    record.potcar = self._potcar_state(request, record.plan['steps'], 'generating')
                    record.plan['potcar'] = record.potcar
        files, metadata = {}, None
        try:
            if request.potcar.mode == 'include':
                if record is None:
                    raise ConflictError('POTCAR_CONFIRMATION_REQUIRED', '请先规划并确认当前工作流 POTCAR', True)
                # Expired plans cannot be revived by supplying a full request.
                self.replay_request(request.workflow_id)
                if not request.potcar.artifact_id:
                    raise ConflictError('POTCAR_CONFIRMATION_REQUIRED', '选择包含后须先确认生成 POTCAR 产物', True)
                if potcar_service is None:
                    raise ConflictError('POTCAR_SERVICE_UNAVAILABLE', '本地赝势服务不可用；请恢复后重试', True)
                preview = self._pipeline.preview_plan(request)
                metadata = potcar_service.artifact(request.potcar.artifact_id)['artifact']
                expected = self._binding(request, record.revision, preview)
                if (metadata.get('workflow_binding') != expected or not self._valid_potcar_binding(expected) or
                        metadata['structure_sha256'] != hashlib.sha256(self._pipeline.final_poscar(request).encode()).hexdigest()):
                    raise ConflictError('POTCAR_BINDING_MISMATCH', '产物与当前计划、参数或最终 POSCAR 不匹配；请重新预览确认', True)
                raw = potcar_service.download(request.potcar.artifact_id)
                files = {step_id: raw for step_id in expected['step_ids']}
            with self._guard:
                if self._plans.get(request.workflow_id) is not record or self._attempts.get(request.workflow_id) is not attempt:
                    raise ConflictError('WORKFLOW_REVISION_CONFLICT', '生成期间工作流计划或生成请求已变化；请重新确认', True)
                if record:
                    record.potcar = self._potcar_state(request, record.plan['steps'], 'generating' if files else 'omitted')
                    record.plan['potcar'] = record.potcar
            result = self._pipeline.generate(request, potcar_files=files, potcar_metadata=metadata, revision=current_revision)
            self._register_generated_files(result)
            body = result.to_response_body()
            body['steps'] = [step.model_dump(mode='json') for step in result.steps]
            with self._guard:
                if self._plans.get(request.workflow_id) is not record or self._attempts.get(request.workflow_id) is not attempt:
                    raise ConflictError('WORKFLOW_REVISION_CONFLICT', '生成期间工作流计划或生成请求已变化；请重新确认', True)
                if record:
                    record.potcar = body['potcar']
                    record.plan['potcar'] = record.potcar
                    record.request = request.model_copy(deep=True)
                self._artifacts[request.workflow_id] = WorkflowArtifact(
                    workflow_id=request.workflow_id, zip_bytes=result.bundle.zip_bytes, body=body)
            return body
        except Exception as exc:
            store_error = request.potcar.mode == 'include' and isinstance(exc, (OSError, ValueError, TypeError, KeyError))
            with self._guard:
                if (record and request.potcar.mode == 'include' and self._plans.get(request.workflow_id) is record
                        and self._attempts.get(request.workflow_id) is attempt):
                    record.potcar = self._potcar_state(request, record.plan['steps'], 'failed')
                    record.potcar['error'] = {'code': 'POTCAR_STORE_INVALID' if store_error else getattr(exc, 'code', 'POTCAR_GENERATION_FAILED'),
                                              'message': '包含 POTCAR 的生成失败；请恢复后重新确认'}
                    record.plan['potcar'] = record.potcar
                    self._artifacts.pop(request.workflow_id, None)
            if store_error:
                from backend.toolbox.contracts import ToolboxError
                raise ToolboxError('POTCAR_STORE_INVALID', '本地赝势产物或状态无法读取；请检查状态目录后重试', 503, True) from None
            raise

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
            if node.name.upper() == 'POTCAR':
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
        return plan.request.model_copy(deep=True)
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
                "revision": plan.revision,
                "potcar": copy.deepcopy(plan.potcar),
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

    def list_recent(self, limit: int = 10,
                    now: float | None = None) -> list[dict[str, object]]:
        """Return unexpired workflow summaries without refreshing their TTL.

        Plans and generated artifacts share an id.  They are folded into one
        entry, with a live artifact taking precedence over its plan.  Expired
        objects are filtered but deliberately not deleted here.
        """
        current = time.time() if now is None else now
        rows: dict[str, dict[str, object]] = {}
        for plan in self._plans.values():
            if current - plan.touched_at > self._ttl_seconds:
                continue
            rows[plan.workflow_id] = {
                "id": plan.workflow_id,
                "kind": "workflow",
                "title": f"工作流 {plan.workflow_id}",
                "status": plan.workflow_status,
                "created_at": _iso(plan.created_at),
                "updated_at": _iso(plan.touched_at),
                "expires_at": _iso(plan.touched_at + self._ttl_seconds),
                "_sort": plan.touched_at,
            }
        for artifact in self._artifacts.values():
            if current - artifact.touched_at > self._ttl_seconds:
                continue
            existing = rows.get(artifact.workflow_id)
            created_at = min(
                artifact.created_at,
                _parse_iso_timestamp(existing["created_at"])
                if existing is not None else artifact.created_at,
            )
            rows[artifact.workflow_id] = {
                "id": artifact.workflow_id,
                "kind": "workflow",
                "title": f"工作流 {artifact.workflow_id}",
                "status": str(artifact.body.get("workflow_status") or "generated"),
                "created_at": _iso(created_at),
                "updated_at": _iso(artifact.touched_at),
                "expires_at": _iso(artifact.touched_at + self._ttl_seconds),
                "_sort": artifact.touched_at,
            }
        ordered = sorted(rows.values(), key=lambda row: float(row["_sort"]),
                         reverse=True)
        for row in ordered:
            row.pop("_sort", None)
        return ordered[:max(0, limit)]


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace(
        "+00:00", "Z")


def _parse_iso_timestamp(value: object) -> float:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
