"""WorkflowGenerationPipeline（设计文档 4.1 节、6.5/8 节）。

唯一门面：``generate(WorkflowGenerateRequest) -> WorkflowGenerationResult``。

流程：
  planner(DAG+继承计划) → selector/composer 逐 step 组合 → gating 求值
  → POSCAR/INCAR/KPOINTS/submit.sh 逐 step 生成
  → README_run_order.md / workflow_plan.json / INPUT_CHECK_REPORT.md / POTCAR_REQUIRED.md
  → BundleBuilder（manifest + hash + 确定性 zip）

所有产物确定性可复现；不执行任何命令、不拼接 POTCAR。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from backend.input_validation import InputValidationError, validate_potcar
from backend.app.schemas.structure import validated_structure_context
from backend.app.services.surface_inputs import validate_surface_context, surface_grid
from backend.app.generators.archive import FIXED_TIMESTAMP, BundleBuilder
from backend.app.generators.incar import IncarGenerator
from backend.app.generators.kpoints import KpointsGenerator
from backend.app.generators.poscar import PoscarGenerator, default_sample_name
from backend.app.generators.script import ScriptGenerator
from backend.app.recipes.composer import ComposeRequest, RecipeComposer
from backend.app.recipes.derived import BAND_LINE_DIVISIONS, KPPA_TABLE, generate_kpoint_grid, generate_ldau_arrays
from backend.app.recipes.errors import BeAError, DftuConfirmationRequired, RecipeConfirmationRequired, CompositionRevisionConflict
from backend.app.recipes.registry import RecipeRegistry, default_registry
from backend.app.recipes.selector import RecipeSelector
from backend.app.reports.input_check.generator import InputCheckReportGenerator
from backend.app.schemas.generation import (
    GeneratedFileNode,
    KpointsSpec,
    StructureContext,
    WorkflowGenerateRequest,
    ParameterProvenance, ProvenanceSourceType,
)
from backend.app.schemas.recipe import RecipePackManifest, SelectionContext, TaskType
from backend.app.core.file_identity import generated_file_id
from backend.app.schemas.workflow import (
    AssumptionsBlock,
    CompositionFileEntry,
    ConfirmationEntry,
    DftuBlock,
    GoalBlock,
    RecipeComposition,
    RemoteExecutionBlock,
    SchedulerBlock,
    StructureBlock,
    WarningEntry,
    WorkflowPlanFile,
    WorkflowStep,
    PendingConfirmation, RecipeCompositionStatus,
)
from backend.app.workflow.gating import StepGatingEvaluator
from backend.app.workflow.models import ValidationResult, WorkflowGenerationResult
from backend.app.workflow.planner import WorkflowPlanner

README_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "generators" / "templates" / "readme"

GENERATOR_VERSION = "0.1.0"



_PREVIEW_DENIED = {"POTCAR", "WAVECAR", "CHGCAR"}
_PREVIEW_BINARY_EXTS = (
    ".zip", ".gz", ".bz2", ".tar", ".xz", ".png", ".jpg",
    ".jpeg", ".gif", ".pdf", ".pickle", ".npy",
)


def _is_previewable(file_name: str) -> bool:
    """与 files.py 预览策略保持一致：策略受限或二进制文件标记为不可预览。"""
    base = file_name.upper()
    if "POTCAR" in base or base in _PREVIEW_DENIED:
        return False
    if base.endswith(_PREVIEW_BINARY_EXTS):
        return False
    return True


class WorkflowGenerationPipeline:
    def __init__(
        self,
        registry: Optional[RecipeRegistry] = None,
        pack: Optional[RecipePackManifest] = None,
        potcar_prepared: bool = False,
    ) -> None:
        if registry is None or pack is None:
            registry, pack = default_registry()
        self._registry = registry
        self._pack = pack
        self._selector = RecipeSelector()
        self._composer = RecipeComposer(registry, pack)
        self._planner = WorkflowPlanner()
        self._gating = StepGatingEvaluator(potcar_prepared=potcar_prepared)
        # Legacy capability argument never serves as proof that a file exists.
        self._incar = IncarGenerator()
        self._kpoints = KpointsGenerator()
        self._poscar = PoscarGenerator()
        self._script = ScriptGenerator()
        self._report = InputCheckReportGenerator()
        self._builder = BundleBuilder()
        self._templates = Environment(
            loader=FileSystemLoader(str(README_TEMPLATE_DIR)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            autoescape=False,
        )

    # ------------------------------------------------------------------

    def final_poscar(self, request: WorkflowGenerateRequest) -> str:
        self._validate_structure(request)
        return self._poscar.generate(request.structure, sample_name=request.sample_name)

    def generate(self, request: WorkflowGenerateRequest, *, potcar_files: Optional[Dict[str, bytes]] = None,
                 potcar_metadata: Optional[dict] = None, revision: int = 1) -> WorkflowGenerationResult:
        self._validate_request(request)
        planned = self._planner.plan(
            request.workflow_id,
            request.requested_tasks,
            enable_band_workflow=request.enable_band_workflow,
        )
        steps: List[WorkflowStep] = planned["steps"]
        inheritance = planned["file_inheritance_plan"]
        potcar_files = potcar_files or {}
        if request.potcar.mode == 'include':
            if set(potcar_files) != {s.step_id for s in steps} or any(type(raw) is not bytes or not raw for raw in potcar_files.values()):
                raise BeAError('选择包含 POTCAR 后必须提供所有步骤的已确认产物', code='POTCAR_CONFIRMATION_REQUIRED')
            for raw in potcar_files.values():
                validate_potcar(raw, tuple(request.structure.elements))
            if len(set(potcar_files.values())) != 1:
                raise BeAError('同一工作流必须使用相同 POTCAR 字节', code='POTCAR_BINDING_MISMATCH')
        elif potcar_files:
            raise BeAError('未包含模式不接受 POTCAR 产物', code='POTCAR_BINDING_MISMATCH')
        potcar_state = {'mode': request.potcar.mode, 'status': 'generated' if potcar_files else 'omitted',
                        'artifact_id': request.potcar.artifact_id, 'steps': []}
        import hashlib
        for step in steps:
            raw = potcar_files.get(step.step_id)
            potcar_state['steps'].append({'step_id': step.step_id, 'status': 'generated' if raw else 'omitted',
                                         'artifact_id': request.potcar.artifact_id if raw else None,
                                         'sha256': hashlib.sha256(raw).hexdigest() if raw else None,
                                         'size_bytes': len(raw) if raw else None})
        if potcar_metadata:
            potcar_state['artifact'] = potcar_metadata

        compositions: Dict[str, RecipeComposition] = {}
        kpoints_specs: Dict[str, KpointsSpec] = {}
        for step in steps:
            task = TaskType(step.task)
            context = self._selection_context(request, task)
            entries = self._selector.select(context)
            step_patches = [
                patch
                for patch in request.patches
                if patch.step_id in (None, step.step_id)
                and not (request.structure.surface_policy and task == TaskType.RELAX and patch.parameter == 'ISIF')
            ]
            composition = self._composer.compose(
                ComposeRequest(
                    step_id=step.step_id,
                    context=context,
                    entries=entries,
                    patches=step_patches,
                    confirmed_keys=self._confirmed_keys(request, entries),
                    derived_inputs=self._derived_inputs(request, task),
                )
            )
            self._apply_surface_policy(request, task, composition, preview=False)
            if composition.confirmations and not request.confirm:
                raise RecipeConfirmationRequired(
                    f"composition for {step.step_id} has pending confirmations",
                    details={
                        "step_id": step.step_id,
                        "confirmations": [c.key for c in composition.confirmations],
                    },
                )
            compositions[step.step_id] = composition
            kpoints_specs[step.step_id] = self._kpoints_spec(request, task, composition)
            step.parameters = {
                key: composition.resolved_parameters[key]
                for key in sorted(composition.resolved_parameters)
            }

        self._validate_dftu_compositions(request, compositions)
        self._validate_band_compositions(request, compositions)

        self._gating.evaluate(steps, inheritance, prepared_steps=set(potcar_files))

        files: Dict[str, str | bytes] = {}
        for step in steps:
            composition = compositions[step.step_id]
            files[f"{step.directory}/POSCAR"] = self.final_poscar(request)
            if step.step_id in potcar_files:
                files[f"{step.directory}/POTCAR"] = potcar_files[step.step_id]
            files[f"{step.directory}/INCAR"] = self._incar.generate(
                composition.resolved_parameters, request.structure, request.dftu,
                step_id=step.step_id, task=step.task,
            )
            files[f"{step.directory}/KPOINTS"] = self._render_kpoints(
                request, step, kpoints_specs[step.step_id]
            )
            files[f"{step.directory}/submit.sh"] = self._script.render(
                request.scheduler, step_id=step.step_id
            )

        warnings = self._collect_warnings(compositions)
        warnings.extend(self._band_warnings(request))
        plan_file = self._build_plan_file(request, steps, inheritance, compositions, warnings)
        plan_file.revision = revision
        plan_file.potcar = potcar_state
        files["workflow_plan.json"] = self._dump_plan_file(plan_file)
        files["README_run_order.md"] = self._render_readme(request, steps, inheritance, warnings,
                                                          revision=revision, potcar_required=not bool(potcar_files))
        if not potcar_files:
            files["POTCAR_REQUIRED.md"] = self._render_potcar_required(request)
        report_markdown, _report_metadata = self._report.generate(
            workflow_id=request.workflow_id,
            revision=revision,
            structure=request.structure,
            steps=steps,
            plan=inheritance,
            compositions=compositions,
            dftu=request.dftu,
            potcar_prepared=bool(potcar_files),
            potcar_state=potcar_state,
        )
        files["INPUT_CHECK_REPORT.md"] = report_markdown
        if request.structure.catalysis_binding:
            metadata = {'binding': request.structure.catalysis_binding.model_dump(mode='json'),
                        'surface_policy': request.structure.surface_policy.model_dump(mode='json'),
                        'final_poscar_sha256': hashlib.sha256(self.final_poscar(request).encode()).hexdigest(),
                        'identity_restore_rule': 'Verify snapshot, metadata and POSCAR hashes before restoring atom IDs; external edits require fresh import.'}
            files['catalysis_metadata.json'] = json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + '\n'

        bundle = self._builder.build(
            request.workflow_id, files, revision=revision, pack=self._pack, potcar=potcar_state,
            catalysis_binding=request.structure.catalysis_binding, surface_policy=request.structure.surface_policy
        )
        # 内嵌 manifest 与最终 manifest 自洽：先对不含自身的文件集构建，
        # 再把真实 JSON 放回 files 后重新构建，保证 zip 内容与 manifest 逐文件对得上。
        manifest_text = (
            json.dumps(
                bundle.manifest.model_dump(mode="json"), sort_keys=True,
                ensure_ascii=False, indent=2,
            )
            + "\n"
        )
        files["workflow_manifest.json"] = manifest_text
        bundle = self._builder.build(
            request.workflow_id, files, revision=revision, pack=self._pack, potcar=potcar_state,
            catalysis_binding=request.structure.catalysis_binding, surface_policy=request.structure.surface_policy
        )

        file_tree = self._build_file_tree(request.workflow_id, bundle.files)
        validation = ValidationResult(
            valid=True,
            recipe_pack_version=self._pack.version if self._pack else None,
            provenance_complete=self._provenance_complete(compositions),
            warnings=warnings,
        )
        return WorkflowGenerationResult(
            workflow_id=request.workflow_id,
            revision=revision,
            workflow_status="generated",
            plan_file=plan_file,
            steps=steps,
            file_inheritance_plan=inheritance,
            compositions=compositions,
            file_tree=file_tree,
            validation=validation,
            bundle=bundle,
            pack=self._pack,
            potcar=potcar_state,
        )

    # --- Plan preview (IR-01) ---

    def preview_plan(self, request: WorkflowGenerateRequest) -> Dict[str, Any]:
        """Plan-stage preview (IR-01): recipe selection + pending confirmations.

        Reuses planner/selector/composer from ``generate`` but produces no
        files. Returns the same shape the /workflows/plan endpoint exposes.
        """
        self._validate_plan_input(request)
        planned = self._planner.plan(
            request.workflow_id,
            request.requested_tasks,
            enable_band_workflow=request.enable_band_workflow,
        )
        steps: List[WorkflowStep] = planned["steps"]
        inheritance = planned["file_inheritance_plan"]
        if TaskType.BAND in request.requested_tasks:
            # A plan must not promise a path that fails only after confirmation.
            self._kpoints.line_mode(request.structure.poscar_text,
                                    divisions=BAND_LINE_DIVISIONS[request.precision.value])

        confirmations: List[Dict[str, Any]] = []
        conflicts: List[Dict[str, Any]] = []
        seen_keys = set()
        compositions: Dict[str, RecipeComposition] = {}
        for step in steps:
            task = TaskType(step.task)
            context = self._selection_context(request, task)
            entries = self._selector.select(context)
            step_patches = [
                patch
                for patch in request.patches
                if patch.step_id in (None, step.step_id)
                and not (request.structure.surface_policy and task == TaskType.RELAX and patch.parameter == 'ISIF')
            ]
            composition = self._composer.compose(
                ComposeRequest(
                    step_id=step.step_id,
                    context=context,
                    entries=entries,
                    patches=step_patches,
                    confirmed_keys=set(),
                    derived_inputs=self._derived_inputs(request, task),
                )
            )
            self._apply_surface_policy(request, task, composition, preview=True)
            compositions[step.step_id] = composition
            step.parameters = {
                key: composition.resolved_parameters[key]
                for key in sorted(composition.resolved_parameters)
            }
            for pending in composition.confirmations:
                if pending.key in seen_keys:
                    continue
                seen_keys.add(pending.key)
                confirmations.append(pending.model_dump(mode="json"))
            conflicts.extend(c.model_dump(mode="json") for c in composition.conflicts)

        self._validate_dftu_compositions(request, compositions)
        self._validate_band_compositions(request, compositions)
        warnings = self._collect_warnings(compositions)
        warnings.extend(self._band_warnings(request))
        result = {
            "steps": [step.model_dump(mode="json") for step in steps],
            "file_inheritance_plan": inheritance.model_dump(mode="json"),
            "recipe_compositions": [
                composition.model_dump(mode="json")
                for composition in compositions.values()
            ],
            "confirmations": confirmations,
            "conflicts": conflicts,
            "warnings": warnings,
            "needs_confirmation": bool(confirmations),
        }
        if request.structure.catalysis_binding:
            result.update(catalysis_binding=request.structure.catalysis_binding.model_dump(mode='json'),
                          surface_policy=request.structure.surface_policy.model_dump(mode='json'))
        return result

    def _validate_plan_input(self, request: WorkflowGenerateRequest) -> None:
        self._validate_structure(request)
        if request.dftu.enabled:
            if not request.dftu.entries:
                raise DftuConfirmationRequired(
                    "已启用 DFT+U，但没有元素参数；请填写至少一个元素并确认。",
                    details={"dftu": request.dftu.model_dump(mode="json")},
                )
            unknown = sorted({entry.element for entry in request.dftu.entries}
                             - set(request.structure.elements))
            if unknown:
                raise BeAError("DFT+U 元素不在当前 POSCAR 中；请删除对应条目后重新确认。",
                                code="DFTU_ELEMENT_NOT_IN_STRUCTURE",
                                details={"unknown_elements": unknown,
                                         "structure_elements": request.structure.elements})
        if TaskType.BAND in request.requested_tasks and request.material_assumptions.soc:
            raise BeAError("本批不支持 SOC 能带；请关闭 SOC 后重新规划 PBE／PBE+U 能带。",
                            code="BAND_COMBINATION_UNSUPPORTED", details={"combination": "SOC"})
        if not request.structure.elements:
            raise BeAError(
                "structure.elements is required for workflow planning",
                code="UPSTREAM_OUTPUT_MISSING",
                details={"structure_id": request.structure.structure_id},
            )

    # --- 输入校验 ---

    def _validate_request(self, request: WorkflowGenerateRequest) -> None:
        self._validate_plan_input(request)
        if request.dftu.enabled:
            if not request.dftu.all_confirmed:
                unconfirmed = [
                    entry.element for entry in request.dftu.entries
                    if not entry.confirmed_by_user
                ]
                raise DftuConfirmationRequired(
                    "all DFT+U U/J/L entries must be confirmed by user",
                    details={"unconfirmed_elements": unconfirmed},
                )
        if not request.structure.poscar_text:
            raise BeAError(
                "structure.poscar_text is required for file generation",
                code="UPSTREAM_OUTPUT_MISSING",
                details={"structure_id": request.structure.structure_id},
            )
        self._validate_structure(request)

    @staticmethod
    def _validate_structure(request: WorkflowGenerateRequest) -> None:
        try:
            request.structure = validated_structure_context(request.structure)
            validate_surface_context(request.structure)
            if request.structure.surface_policy and TaskType.RELAX in request.requested_tasks:
                for patch in request.patches:
                    if patch.parameter == 'ISIF' and patch.step_id in (None, '01_relax'):
                        if patch.expected_revision != 1:
                            raise CompositionRevisionConflict('ISIF 补丁版本与当前 composition 不一致',
                                details={'expected_revision': patch.expected_revision, 'current_revision': 1})
                        if patch.operation.value == 'remove' or type(patch.value) is not int or patch.value != 2:
                            raise BeAError('当前表面策略固定整个晶胞（ISIF=2）；该 ISIF 补丁冲突，请移除补丁或人工准备其他晶胞策略',
                                           code='CAT_SURFACE_CELL_PATCH_CONFLICT',
                                           details={'patch_id': patch.patch_id, 'ISIF': patch.value})
                        if not patch.confirmed_by_user:
                            raise BeAError('ISIF 补丁须显式 confirmed_by_user=true，表面策略仍需在当前计划确认',
                                           code='CAT_SURFACE_CELL_PATCH_UNCONFIRMED', details={'patch_id': patch.patch_id})
            if request.structure.surface_policy and TaskType.BAND in request.requested_tasks:
                raise BeAError('表面 Workflow 不支持通用体相高对称 band 路径；请使用 relax/static/dos 或人工准备二维路径',
                               code='CAT_SURFACE_BAND_UNSUPPORTED', details={'task': 'band'})
        except InputValidationError as exc:
            raise BeAError(str(exc), code=exc.code) from exc

    def _selection_context(
        self, request: WorkflowGenerateRequest, task: TaskType
    ) -> SelectionContext:
        return SelectionContext(
            task=task,
            electronic_type=request.material_assumptions.electronic_type,
            precision=request.precision,
            magnetic=request.material_assumptions.magnetic,
            dftu=request.dftu.enabled,
            elements=list(request.structure.elements),
        )

    def _confirmed_keys(self, request: WorkflowGenerateRequest, entries) -> set:
        """confirm=True 时确认所选 Recipe 声明的全部确认项。"""

        if not request.confirm:
            return set()
        keys = set()
        for entry in entries:
            manifest = self._registry.get(entry.ref)
            keys.update(confirmation.key for confirmation in manifest.confirmations)
        return keys

    @staticmethod
    def _validate_dftu_compositions(
        request: WorkflowGenerateRequest, compositions: Dict[str, RecipeComposition]
    ) -> None:
        """Compare the final, patched INCAR parameters with the confirmed form."""
        expected: Dict[str, Any] = {}
        if request.dftu.enabled:
            expected = {"LDAU": True,
                        "LDAUTYPE": 1 if request.dftu.form == "liechtenstein" else 2}
            expected.update(generate_ldau_arrays({
                "elements": request.structure.elements,
                "dftu_entries": [entry.model_dump(mode="json") for entry in request.dftu.entries],
                "dftu_input_mode": request.dftu.input_mode,
            }))
        for step_id, composition in compositions.items():
            final = composition.resolved_parameters
            for tag in ("LDAU", "LDAUTYPE", "LDAUL", "LDAUU", "LDAUJ"):
                actual = final.get(tag)
                desired = expected.get(tag)
                if actual == desired:
                    continue
                source = next((item for item in composition.provenance
                               if item.get("parameter") == tag), None)
                raise BeAError(
                    "最终 DFT+U 参数与已确认的形式或数值不一致；请检查覆盖来源后重新规划并确认。",
                    code="DFTU_FINAL_PARAMETERS_MISMATCH",
                    details={"step_id": step_id, "parameter": tag,
                             "expected": desired, "actual": actual, "provenance": source},
                )

    @staticmethod
    def _validate_band_compositions(
        request: WorkflowGenerateRequest, compositions: Dict[str, RecipeComposition]
    ) -> None:
        if TaskType.BAND not in request.requested_tasks:
            return
        static = compositions["02_static"].resolved_parameters
        band = compositions["04_band"].resolved_parameters
        required_spin = 2 if request.material_assumptions.magnetic else 1
        if static.get("ISPIN", 1) != required_spin or band.get("ISPIN", 1) != required_spin:
            raise BeAError(
                "static 与 band 的 ISPIN 必须一致，并与已确认的磁性设置相符；请检查参数覆盖后重新确认。",
                code="BAND_SPIN_INCONSISTENT",
                details={"static_ISPIN": static.get("ISPIN", 1), "band_ISPIN": band.get("ISPIN", 1),
                         "confirmed_ISPIN": required_spin},
            )
        unsupported_tags = ("LHFCALC", "METAGGA", "LSORBIT", "LNONCOLLINEAR")
        for step_id, parameters in (("02_static", static), ("04_band", band)):
            for tag in unsupported_tags:
                if parameters.get(tag) not in (None, False):
                    raise BeAError("本批能带流程只支持 PBE 及现有 PBE+U；请移除 HSE06、meta-GGA 或 SOC 等设置后重新规划。",
                                    code="BAND_COMBINATION_UNSUPPORTED",
                                    details={"step_id": step_id, "parameter": tag})
            if parameters.get("NSW") != 0 or parameters.get("IBRION") != -1:
                raise BeAError("static 和 band 必须使用同一固定结构；请移除改变结构的参数后重新确认。",
                                code="BAND_STRUCTURE_CHANGE_UNSUPPORTED",
                                details={"step_id": step_id})
        if static.get("LCHARG") is not True or band.get("ICHARG") != 11:
            raise BeAError(
                "static 必须写出 CHGCAR，band 必须以 ICHARG=11 读取；请恢复相应参数后重新确认。",
                code="BAND_CHGCAR_INCONSISTENT",
                details={"static_LCHARG": static.get("LCHARG"), "band_ICHARG": band.get("ICHARG")},
            )
        if static.get("ICHARG") not in (None, 2):
            raise BeAError("band 前的 static 必须自洽计算；请恢复 static 的 ICHARG 后重新确认。",
                            code="BAND_CHGCAR_INCONSISTENT",
                            details={"static_ICHARG": static.get("ICHARG")})
        if any(parameters.get("LDAU") is True for parameters in (static, band)) != request.dftu.enabled:
            raise BeAError("最终 DFT+U 开关与已确认设置不一致；请检查参数覆盖并重新确认。",
                            code="BAND_DFTU_INCONSISTENT")
        if not request.dftu.enabled:
            return
        for tag in ("LDAU", "LDAUTYPE", "LDAUL", "LDAUU", "LDAUJ"):
            if static.get(tag) != band.get(tag):
                raise BeAError("static 与 band 的 DFT+U 参数不一致；请检查覆盖并重新确认。",
                                code="BAND_DFTU_INCONSISTENT", details={"parameter": tag})
        channels = static.get("LDAUL", [])
        if (not isinstance(channels, list) or len(channels) != len(request.structure.elements)
                or any(type(l) not in (int, float) or l not in (-1, 2, 3) for l in channels)):
            raise BeAError("本批 PBE+U 能带只支持 d/f 轨道；请核对 LDAUL 后重新确认。",
                            code="BAND_COMBINATION_UNSUPPORTED", details={"LDAUL": channels})
        if static.get("LDAU") is not True or band.get("LDAU") is not True or static.get("LDAUTYPE") not in (1, 2):
            raise BeAError("band 需要已确认的 Dudarev 或 Liechtenstein DFT+U 设置；请检查最终参数后重新确认。",
                            code="BAND_DFTU_INCONSISTENT")
        minimum = 6 if 3 in channels else 4
        for step_id, parameters in (("02_static", static), ("04_band", band)):
            actual = parameters.get("LMAXMIX")
            if isinstance(actual, bool) or not isinstance(actual, int) or actual < minimum:
                raise BeAError(
                    "static 写入及 band 读取 CHGCAR 时 LMAXMIX 必须足够；请提高该步骤参数后重新确认。",
                    code="BAND_LMAXMIX_INSUFFICIENT",
                    details={"step_id": step_id, "required": minimum, "actual": actual},
                )

    @staticmethod
    def _band_warnings(request: WorkflowGenerateRequest) -> List[Dict[str, Any]]:
        if TaskType.BAND not in request.requested_tasks:
            return []
        divisions = BAND_LINE_DIVISIONS[request.precision.value]
        potcar_notice = (
            '本次包含的本地产物在生成时核验 static 与 band 的数据集、物种顺序和逐字节一致性；仍需验证科学适用性。'
            if request.potcar.mode == 'include' else
            'static 与 band 须由用户核验使用逐字一致的 PBE POTCAR（同版本、同元素变体及顺序）；软件不提供也不校验外部 POTCAR。'
        )
        return [{
            "code": "BAND_PATH_INPUT_CELL",
            "severity": "high",
            "message": (
                f"能带路径按当前 POSCAR 原胞与 Setyawan-Curtarolo 约定生成；"
                f"对称识别容差 0.01 Å，每段 {divisions} 点。先完成 static 并继承其 CHGCAR；"
                "若实际晶胞改变，须用最终 CONTCAR 重新生成并确认 static→band。"
                + potcar_notice
            ),
        }]

    def _derived_inputs(
        self, request: WorkflowGenerateRequest, task: TaskType
    ) -> Dict[str, Any]:
        structure = request.structure
        lattice: Dict[str, Any] = {}
        if structure.lattice is not None:
            info = structure.lattice
            # matrix 与 abc/angles 并行传入：派生层以 matrix 为唯一几何真值，
            # abc/angles 仅作容差内一致性交叉校验（矛盾则 fail closed）。
            if info.matrix:
                lattice["matrix"] = [list(row) for row in info.matrix]
            if info.a is not None and info.b is not None and info.c is not None:
                lattice["abc"] = [info.a, info.b, info.c]
            if info.alpha is not None and info.beta is not None and info.gamma is not None:
                lattice["angles"] = [info.alpha, info.beta, info.gamma]
        return {
            "elements": list(structure.elements),
            "counts": list(structure.counts),
            "formula": structure.formula,
            "task": task.value,
            "precision": request.precision.value,
            "element_initial_moments": dict(request.element_initial_moments),
            "dftu_entries": [
                entry.model_dump(mode="json") for entry in request.dftu.entries
            ],
            "dftu_form": request.dftu.form,
            "dftu_input_mode": request.dftu.input_mode,
            "lattice": lattice,
        }

    # --- KPOINTS ---

    def _kpoints_spec(
        self,
        request: WorkflowGenerateRequest,
        task: TaskType,
        composition: RecipeComposition,
    ) -> KpointsSpec:
        if task == TaskType.BAND:
            return KpointsSpec(mode="line_mode", line_density=BAND_LINE_DIVISIONS[request.precision.value])
        kppa = KPPA_TABLE[task.value][request.precision.value]
        if request.structure.surface_policy:
            policy = request.structure.surface_policy
            return KpointsSpec(mode='surface_explicit' if policy.kpoint_mode == 'explicit_gamma_2d' else 'automatic_density',
                kppa=kppa, grid=surface_grid(request.structure.lattice.matrix, policy.normal, kppa,
                                            request.structure.atom_count), centering='Gamma')
        derived_inputs = self._derived_inputs(request, task)
        grid_info = generate_kpoint_grid({
            "kppa": kppa,
            "atom_count": request.structure.atom_count,
            "lattice": derived_inputs["lattice"],
        })
        return KpointsSpec(
            mode="automatic_density",
            kppa=kppa,
            grid=grid_info["grid"],
            centering=grid_info["centering"],
        )

    def _render_kpoints(
        self, request: WorkflowGenerateRequest, step: WorkflowStep, spec: KpointsSpec
    ) -> str:
        if spec.mode == 'surface_explicit':
            return self._kpoints.surface_explicit(request.structure.lattice.matrix,
                request.structure.surface_policy.normal, spec.grid,
                comment=f'KPOINTS for {step.step_id}; explicit planar Gamma mesh; unchanged POSCAR cell')
        if spec.mode == "line_mode":
            return self._kpoints.line_mode(
                request.structure.poscar_text,
                divisions=spec.line_density or 60,
                comment=f"Line-mode {spec.line_density} points/segment; Setyawan-Curtarolo path in POSCAR reciprocal basis; generated by VASP-Copilot",
            )
        return self._kpoints.generate(spec, comment=f"KPOINTS for {step.step_id} generated by VASP-Copilot")

    def _apply_surface_policy(self, request, task, composition, *, preview):
        policy = request.structure.surface_policy
        if policy is None:
            return
        if task == TaskType.BAND:
            raise BeAError('表面 Workflow 不支持通用体相高对称 band 路径；请使用 relax/static/dos 或人工准备二维路径',
                           code='CAT_SURFACE_BAND_UNSUPPORTED', details={'task': task.value})
        is_mear = composition.resolved_parameters.get('ISMEAR')
        if policy.kpoint_mode == 'explicit_gamma_2d' and is_mear in {-4, -5, -14, -15}:
            raise BeAError(f'步骤 {composition.step_id}（{task.value}）的 ISMEAR={is_mear} 使用四面体积分，倾斜 c 的显式二维采样未提供四面体表；'
                           '请在规划前显式确认适用的非四面体 ISMEAR 参数，或人工准备对应 KPOINTS/晶胞后重新导入',
                           code='CAT_SURFACE_TETRAHEDRON_UNSUPPORTED',
                           details={'step_id': composition.step_id, 'task': task.value, 'ISMEAR': is_mear})
        if task == TaskType.RELAX:
            previous = composition.resolved_parameters.get('ISIF')
            previous_source = next((p for p in composition.provenance if p['parameter'] == 'ISIF'), None)
            composition.resolved_parameters['ISIF'] = policy.relax_isif
            composition.provenance = [p for p in composition.provenance if p['parameter'] != 'ISIF']
            composition.provenance.append(ParameterProvenance(parameter='ISIF', value=policy.relax_isif,
                source_type=ProvenanceSourceType.SURFACE_POLICY, source_id=policy.policy_id,
                overrode={'value': previous, 'provenance': previous_source},
                requires_confirmation=True, confirmed=not preview and request.confirm).model_dump(mode='json'))
            composition.patches.extend(patch.model_dump(mode='json') for patch in request.patches
                if patch.parameter == 'ISIF' and patch.step_id in (None, composition.step_id))
        spec = self._kpoints_spec(request, task, composition)
        composition.derived_outputs['surface_kpoints'] = spec.model_dump(mode='json')
        composition.provenance.append(ParameterProvenance(parameter='KPOINTS', value=spec.model_dump(mode='json'),
            source_type=ProvenanceSourceType.SURFACE_POLICY, source_id=policy.policy_id,
            requires_confirmation=True, confirmed=not preview and request.confirm).model_dump(mode='json'))
        if preview or not request.confirm:
            composition.confirmations.append(PendingConfirmation(key='CAT_SURFACE_POLICY', recipe_id=policy.policy_id,
                prompt='确认表面固定晶胞（relax ISIF=2）和面内二维 Gamma 采样；磁性、带电、偶极及收敛参数需另行人工核对。'))
            composition.composition_status = RecipeCompositionStatus.NEEDS_CONFIRMATION
        composition.warnings.extend({'code': 'CAT_SURFACE_INPUT_REVIEW', 'message': warning, 'severity': 'medium'}
                                    for warning in policy.warnings)
        composition.composition_sha256 = self._composer._composition_hash(composition)

    # --- 根目录文件 ---

    @staticmethod
    def _collect_warnings(
        compositions: Dict[str, RecipeComposition]
    ) -> List[Dict[str, Any]]:
        seen = set()
        warnings: List[Dict[str, Any]] = []
        for composition in compositions.values():
            for warning in composition.warnings:
                key = (warning["code"], warning.get("message"))
                if key in seen:
                    continue
                seen.add(key)
                warnings.append(
                    {
                        "code": warning["code"],
                        "message": warning.get("message", warning["code"]),
                        "severity": warning.get("severity", "medium"),
                    }
                )
        return warnings

    def _build_plan_file(
        self,
        request: WorkflowGenerateRequest,
        steps: List[WorkflowStep],
        inheritance,
        compositions: Dict[str, RecipeComposition],
        warnings: List[Dict[str, Any]],
    ) -> WorkflowPlanFile:
        structure = request.structure
        confirmations: List[ConfirmationEntry] = []
        if not request.confirm:
            seen_keys = set()
            for composition in compositions.values():
                for pending in composition.confirmations:
                    if pending.key in seen_keys:
                        continue
                    seen_keys.add(pending.key)
                    confirmations.append(
                        ConfirmationEntry(key=pending.key, prompt=pending.prompt)
                    )
        recipe_compositions = [
            CompositionFileEntry(
                step_id=step.step_id,
                composition_id=composition.composition_id,
                revision=composition.revision,
                recipe_pack=composition.recipe_pack,
                selected=[entry.model_dump(mode="json") for entry in composition.selected],
                patch_ids=[patch["patch_id"] for patch in composition.patches],
                composition_sha256=composition.composition_sha256,
            )
            for step in steps
            for composition in [compositions[step.step_id]]
        ]
        scheduler = request.scheduler
        return WorkflowPlanFile(
            workflow_id=request.workflow_id,
            revision=1,
            created_at=FIXED_TIMESTAMP,
            structure=StructureBlock(
                structure_id=structure.structure_id,
                formula=structure.formula,
                elements=list(structure.elements),
                counts=list(structure.counts),
                source_sha256=structure.source_sha256,
                source_material_id=structure.source_material_id,
                sample_name=(request.sample_name or
                             (default_sample_name(structure) if structure.source_material_id else None)),
                catalysis_binding=structure.catalysis_binding,
                surface_policy=structure.surface_policy,
            ),
            goal=GoalBlock(
                original_text=request.goal_text,
                requested_tasks=[task.value for task in request.requested_tasks],
            ),
            assumptions=AssumptionsBlock(
                electronic_type=request.material_assumptions.electronic_type.value,
                magnetic=request.material_assumptions.magnetic,
                soc=request.material_assumptions.soc,
                precision=request.precision.value,
            ),
            dftu=DftuBlock(
                enabled=request.dftu.enabled,
                form=request.dftu.form,
                input_mode=request.dftu.input_mode,
                entries=[entry.model_dump(mode="json") for entry in request.dftu.entries],
            ),
            scheduler=SchedulerBlock(
                scheduler_type=scheduler.type,
                nodes=scheduler.nodes,
                tasks_per_node=scheduler.tasks_per_node,
                walltime=scheduler.walltime,
                vasp_binary_hint=scheduler.vasp_binary_hint,
            ),
            remote_execution=RemoteExecutionBlock(),
            steps=steps,
            file_inheritance_plan=inheritance,
            recipe_compositions=recipe_compositions,
            confirmations=confirmations,
            warnings=[WarningEntry(**warning) for warning in warnings],
            template_versions={
                "recipe_pack": self._pack.version if self._pack else "unknown",
                "generator": GENERATOR_VERSION,
            },
        )

    @staticmethod
    def _dump_plan_file(plan_file: WorkflowPlanFile) -> str:
        return (
            json.dumps(
                plan_file.model_dump(mode="json"),
                sort_keys=True,
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )

    def _render_readme(
        self,
        request: WorkflowGenerateRequest,
        steps: List[WorkflowStep],
        inheritance,
        warnings: List[Dict[str, Any]],
        *, revision: int = 1, potcar_required: bool = True,
    ) -> str:
        template = self._templates.get_template("README_run_order.md.j2")
        rendered = template.render(
            workflow_id=request.workflow_id,
            revision=revision,
            formula=request.structure.formula,
            elements=request.structure.elements,
            atom_count=request.structure.atom_count,
            steps=[
                {
                    "step_id": step.step_id,
                    "task": step.task,
                    "directory": step.directory,
                    "runnable": "true" if step.runnable else "false",
                    "blocked_by": step.blocked_by,
                    "depends_on": step.depends_on,
                    "produces": step.produces,
                }
                for step in steps
            ],
            dependencies=[dep.model_dump(mode="json") for dep in inheritance.dependencies],
            potcar_required=potcar_required,
            warnings=warnings,
        )
        return rendered.rstrip("\n") + "\n"

    def _render_potcar_required(self, request: WorkflowGenerateRequest) -> str:
        template = self._templates.get_template("POTCAR_REQUIRED.md.j2")
        rendered = template.render(
            workflow_id=request.workflow_id,
            formula=request.structure.formula,
            potcar_symbols=[
                {"element": element, "symbol": element}
                for element in request.structure.elements
            ],
        )
        return rendered.rstrip("\n") + "\n"

    # --- 文件树与校验 ---

    @staticmethod
    def _build_file_tree(workflow_id: str, files: Dict[str, bytes]) -> GeneratedFileNode:
        import hashlib

        root_children: Dict[str, GeneratedFileNode] = {}
        root_files: List[GeneratedFileNode] = []
        for relative_path in sorted(files):
            data = files[relative_path]
            parts = relative_path.split("/")
            if len(parts) == 1:
                root_files.append(
                    GeneratedFileNode(
                        name=parts[0],
                        type="file",
                        relative_path=relative_path,
                        file_id=generated_file_id(workflow_id, relative_path, data),
                        mime_type="text/plain",
                        size_bytes=len(data),
                        sha256=hashlib.sha256(data).hexdigest(),
                        preview_available=_is_previewable(parts[0]),
                        generated_by="be-a",
                    )
                )
                continue
            directory = parts[0]
            node = root_children.get(directory)
            if node is None:
                node = GeneratedFileNode(
                    name=directory,
                    type="directory",
                    relative_path=directory,
                    generated_by="be-a",
                )
                root_children[directory] = node
            node.children.append(
                GeneratedFileNode(
                    name=parts[1],
                    type="file",
                    relative_path=relative_path,
                    file_id=generated_file_id(workflow_id, relative_path, data),
                    mime_type="text/plain",
                    size_bytes=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                    preview_available=_is_previewable(parts[1]),
                    generated_by="be-a",
                )
            )
        children = [root_children[key] for key in sorted(root_children)] + root_files
        return GeneratedFileNode(
            name=workflow_id,
            type="directory",
            relative_path=".",
            children=children,
            generated_by="be-a",
        )

    @staticmethod
    def _provenance_complete(compositions: Dict[str, RecipeComposition]) -> bool:
        for composition in compositions.values():
            covered = {entry.get("parameter") for entry in composition.provenance}
            if covered != set(composition.resolved_parameters):
                return False
        return True
