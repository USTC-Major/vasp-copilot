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

from backend.app.generators.archive import FIXED_TIMESTAMP, BundleBuilder
from backend.app.generators.incar import IncarGenerator
from backend.app.generators.kpoints import KpointsGenerator
from backend.app.generators.poscar import PoscarGenerator
from backend.app.generators.script import ScriptGenerator
from backend.app.recipes.composer import ComposeRequest, RecipeComposer
from backend.app.recipes.derived import KPPA_TABLE, generate_kpoint_grid
from backend.app.recipes.errors import BeAError, DftuConfirmationRequired, RecipeConfirmationRequired
from backend.app.recipes.registry import RecipeRegistry, default_registry
from backend.app.recipes.selector import RecipeSelector
from backend.app.reports.input_check.generator import InputCheckReportGenerator
from backend.app.schemas.generation import (
    GeneratedFileNode,
    KpointsSpec,
    StructureContext,
    WorkflowGenerateRequest,
)
from backend.app.schemas.recipe import RecipePackManifest, SelectionContext, TaskType
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
        self._potcar_prepared = potcar_prepared
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

    def generate(self, request: WorkflowGenerateRequest) -> WorkflowGenerationResult:
        self._validate_request(request)
        planned = self._planner.plan(
            request.workflow_id,
            request.requested_tasks,
            enable_band_workflow=request.enable_band_workflow,
        )
        steps: List[WorkflowStep] = planned["steps"]
        inheritance = planned["file_inheritance_plan"]

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

        self._gating.evaluate(steps, inheritance)

        files: Dict[str, str] = {}
        for step in steps:
            composition = compositions[step.step_id]
            files[f"{step.directory}/POSCAR"] = self._poscar.generate(request.structure)
            files[f"{step.directory}/INCAR"] = self._incar.generate(
                composition.resolved_parameters, request.structure, request.dftu
            )
            files[f"{step.directory}/KPOINTS"] = self._render_kpoints(
                request, step, kpoints_specs[step.step_id]
            )
            files[f"{step.directory}/submit.sh"] = self._script.render(
                request.scheduler, step_id=step.step_id
            )

        warnings = self._collect_warnings(compositions)
        plan_file = self._build_plan_file(request, steps, inheritance, compositions, warnings)
        files["workflow_plan.json"] = self._dump_plan_file(plan_file)
        files["README_run_order.md"] = self._render_readme(request, steps, inheritance, warnings)
        if not self._potcar_prepared:
            files["POTCAR_REQUIRED.md"] = self._render_potcar_required(request)
        report_markdown, _report_metadata = self._report.generate(
            workflow_id=request.workflow_id,
            revision=1,
            structure=request.structure,
            steps=steps,
            plan=inheritance,
            compositions=compositions,
            dftu=request.dftu,
            potcar_prepared=self._potcar_prepared,
        )
        files["INPUT_CHECK_REPORT.md"] = report_markdown

        bundle = self._builder.build(
            request.workflow_id, files, revision=1, pack=self._pack
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
            request.workflow_id, files, revision=1, pack=self._pack
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
            revision=1,
            workflow_status="generated",
            plan_file=plan_file,
            steps=steps,
            file_inheritance_plan=inheritance,
            compositions=compositions,
            file_tree=file_tree,
            validation=validation,
            bundle=bundle,
            pack=self._pack,
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

        warnings = self._collect_warnings(compositions)
        return {
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

    def _validate_plan_input(self, request: WorkflowGenerateRequest) -> None:
        if not request.structure.elements:
            raise BeAError(
                "structure.elements is required for workflow planning",
                code="UPSTREAM_OUTPUT_MISSING",
                details={"structure_id": request.structure.structure_id},
            )

    # --- 输入校验 ---

    def _validate_request(self, request: WorkflowGenerateRequest) -> None:
        if request.dftu.enabled:
            if not request.dftu.entries:
                raise DftuConfirmationRequired(
                    "DFT+U enabled but no entries provided",
                    details={"dftu": request.dftu.model_dump(mode="json")},
                )
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
            "lattice": lattice,
        }

    # --- KPOINTS ---

    def _kpoints_spec(
        self,
        request: WorkflowGenerateRequest,
        task: TaskType,
        composition: RecipeComposition,
    ) -> KpointsSpec:
        kppa = KPPA_TABLE[task.value][request.precision.value]
        if task == TaskType.BAND:
            return KpointsSpec(mode="line_mode", line_density=int(kppa))
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
        if spec.mode == "line_mode":
            return self._kpoints.line_mode(
                request.structure.poscar_text,
                divisions=spec.line_density or 60,
                comment=f"Line-mode band path for {step.step_id} generated by BE-A",
            )
        return self._kpoints.generate(spec, comment=f"KPOINTS for {step.step_id} generated by BE-A")

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
    ) -> str:
        template = self._templates.get_template("README_run_order.md.j2")
        rendered = template.render(
            workflow_id=request.workflow_id,
            revision=1,
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
            potcar_required=not self._potcar_prepared,
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
        file_counter = [0]

        def next_file_id() -> str:
            file_counter[0] += 1
            return f"file_{file_counter[0]:02d}"

        for relative_path in sorted(files):
            data = files[relative_path]
            parts = relative_path.split("/")
            if len(parts) == 1:
                root_files.append(
                    GeneratedFileNode(
                        name=parts[0],
                        type="file",
                        relative_path=relative_path,
                        file_id=next_file_id(),
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
                    file_id=next_file_id(),
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
