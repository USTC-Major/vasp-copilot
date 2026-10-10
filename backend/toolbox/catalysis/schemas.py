"""CAT-01/02 request contract. hkl always refers to the supplied input cell."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from backend.app.schemas.surface import CatalysisBinding, SurfacePolicy


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class SourceInput(StrictModel):
    kind: Literal['example', 'poscar', 'cif', 'structure_id']
    role: Literal['bulk', 'slab']
    content: str | None = Field(default=None, max_length=2 * 1024 * 1024)
    name: str | None = Field(default=None, max_length=200)
    structure_id: str | None = Field(default=None, max_length=100)

    @model_validator(mode='after')
    def source_fields(self):
        if self.kind in ('poscar', 'cif') and not self.content:
            raise ValueError('结构正文不能为空')
        if self.kind == 'structure_id' and not self.structure_id:
            raise ValueError('必须提供已有 structure_id')
        if self.kind == 'example' and self.role != 'bulk':
            raise ValueError('理想 Pt 示例是体相，请生成表面后使用')
        if self.kind not in ('poscar', 'cif') and self.content is not None:
            raise ValueError('该来源不接受正文')
        return self


class CreateDraft(StrictModel):
    name: str = Field(default='表面建模草稿', min_length=1, max_length=120)
    source: SourceInput


class SurfaceParams(StrictModel):
    miller_index: tuple[StrictInt, StrictInt, StrictInt] = (1, 1, 1)
    min_slab_size: float = Field(default=8, ge=0.5, le=100)
    min_vacuum_size: float = Field(default=15, ge=1, le=100)
    in_plane_supercell: tuple[StrictInt, StrictInt] = (2, 2)
    layer_tolerance: float = Field(default=0.1, ge=0.001, le=1)

    @model_validator(mode='after')
    def bounds(self):
        if not any(self.miller_index) or any(abs(v) > 3 for v in self.miller_index):
            raise ValueError('hkl 必须非零，各分量限制为 -3 至 3')
        if any(v < 1 or v > 8 for v in self.in_plane_supercell):
            raise ValueError('面内超胞倍数限制为 1 至 8')
        return self


class RevisionRequest(StrictModel):
    revision: StrictInt = Field(ge=1)


class BindWorkflow(RevisionRequest):
    surface_id: str | None = Field(default=None, min_length=1, max_length=80)
    candidate_id: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode='after')
    def single_model(self):
        if (self.surface_id is None) == (self.candidate_id is None):
            raise ValueError('须选择一个当前清洁表面或一个有效吸附候选')
        return self


class BuildSurfaces(SurfaceParams, RevisionRequest):
    pass


class PatchDraft(RevisionRequest):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    active_surface_id: str | None = Field(default=None, max_length=80)
    parameters: SurfaceParams | None = None


class SetConstraints(RevisionRequest):
    bottom_fixed_layers: StrictInt = Field(ge=0, le=2048)
    atom_overrides: dict[str, Literal['fixed', 'free']] = Field(default_factory=dict, max_length=2048)
    reset_existing: bool = False


class AtomProvenance(StrictModel):
    kind: Literal['import', 'surface_replica', 'adsorbate']
    source_atom_id: str | None = None
    source_index: int
    replica_ordinal: int | None = None
    mapping: str | None = None
    source_id: str | None = None


class AtomSnapshot(StrictModel):
    atom_id: str
    element: str
    fractional: tuple[float, float, float]
    cartesian: tuple[float, float, float]
    selective_dynamics: tuple[bool, bool, bool]
    provenance: AtomProvenance


class StructureSnapshot(StrictModel):
    snapshot_id: str
    coordinate_mode: Literal['direct', 'cartesian']
    lattice: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
    atoms: list[AtomSnapshot]
    sha256: str


class Layer(StrictModel):
    layer_index: int
    atom_ids: list[str]
    projection_angstrom: float


class SurfaceGeometry(StrictModel):
    normal: tuple[float, float, float]
    normal_period_angstrom: float
    actual_nuclei_span_angstrom: float
    periodic_vacuum_gap_angstrom: float
    projection_origin_angstrom: float
    projection_method: Literal['largest_periodic_nuclei_free_gap']
    in_plane_lengths_angstrom: tuple[float, float]
    layers: list[Layer]
    layer_tolerance: float
    bottom_fixed_layers: int
    atom_overrides: dict[str, Literal['fixed', 'free']]
    reset_existing: bool
    flag_basis: Literal['direct_lattice_vectors']


class SurfaceOption(StrictModel):
    surface_id: str
    termination_shift: float | None
    snapshot: StructureSnapshot
    baseline_flags: list[tuple[bool, bool, bool]]
    surface: SurfaceGeometry
    transform: dict


class SourceRecord(StrictModel):
    kind: Literal['example', 'poscar', 'cif', 'structure_id']
    role: Literal['bulk', 'slab']
    name: str
    sha256: str
    format: str
    structure_id: str | None
    cell_convention: Literal['supplied_input_cell']
    standardized: Literal[False]


class AdsorbateSource(StrictModel):
    kind: Literal['atom', 'xyz', 'co_example']
    element: str | None = Field(default=None, max_length=3)
    content: str | None = Field(default=None, max_length=65536)
    name: str | None = Field(default=None, max_length=200)

    @model_validator(mode='after')
    def source_fields(self):
        if self.kind == 'atom' and (not self.element or self.content is not None):
            raise ValueError('单原子来源须提供元素且不接受 XYZ 正文')
        if self.kind == 'xyz' and (not self.content or self.element is not None):
            raise ValueError('XYZ 来源须提供正文且不接受单原子元素')
        if self.kind == 'co_example' and (self.element is not None or self.content is not None):
            raise ValueError('CO 示例不接受元素或正文覆盖')
        return self


class SetAdsorbate(RevisionRequest):
    source: AdsorbateSource
    anchor_index: StrictInt = Field(ge=0, le=127)


class AdsorbateAtom(StrictModel):
    atom_id: str
    element: str
    cartesian: tuple[float, float, float]


class AdsorbateRecord(StrictModel):
    source_id: str
    source_sha256: str
    source: AdsorbateSource
    atoms: list[AdsorbateAtom]
    anchor_index: int
    coordinate_frame: Literal['orthonormal_surface_frame']


class Placement(StrictModel):
    height_angstrom: float = Field(default=2, ge=-10, le=100)
    rotation_degrees: tuple[float, float, float] = (0, 0, 0)
    screening_distance_angstrom: float = Field(default=.8, ge=0, le=5)

    @model_validator(mode='after')
    def angles(self):
        if any(abs(v) > 360 for v in self.rotation_degrees):
            raise ValueError('三个固定表面轴旋转角限制为 -360 至 360 度')
        return self


class ManualSite(StrictModel):
    label: str | None = Field(default=None, max_length=80)
    uv: tuple[float, float]

    @model_validator(mode='after')
    def bounds(self):
        if any(v < 0 or v >= 1 for v in self.uv):
            raise ValueError('手动位点 u/v 须位于 [0,1)，沿已保存的晶格 a/b')
        return self


class FindAdsorptionSites(RevisionRequest):
    kinds: list[Literal['ontop', 'bridge', 'hollow']] = Field(default_factory=lambda: ['ontop', 'bridge', 'hollow'], max_length=3)
    manual_sites: list[ManualSite] = Field(default_factory=list, max_length=32)
    dedup_tolerance_angstrom: float = Field(default=.05, ge=.001, le=.5)

    @model_validator(mode='after')
    def kinds_unique(self):
        if len(self.kinds) != len(set(self.kinds)):
            raise ValueError('自动位点类型不可重复')
        if not self.kinds and not self.manual_sites:
            raise ValueError('请选择自动位点类型或提供手动位点')
        return self


class BuildAdsorptionCandidates(RevisionRequest):
    site_ids: list[str] = Field(min_length=1, max_length=16)
    placement: Placement = Field(default_factory=Placement)

    @model_validator(mode='after')
    def unique_ids(self):
        if len(set(self.site_ids)) != len(self.site_ids) or any(len(i) > 80 for i in self.site_ids):
            raise ValueError('位点身份不可重复且每项不超过 80 字符')
        return self


class SelectAdsorptionCandidates(RevisionRequest):
    selected_candidate_ids: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode='after')
    def unique_ids(self):
        if len(set(self.selected_candidate_ids)) != len(self.selected_candidate_ids) or any(len(i) > 80 for i in self.selected_candidate_ids):
            raise ValueError('候选身份不可重复且每项不超过 80 字符')
        return self


class ExportAdsorptionCandidates(RevisionRequest):
    candidate_ids: list[str] = Field(min_length=1, max_length=16)

    @model_validator(mode='after')
    def unique_ids(self):
        if len(set(self.candidate_ids)) != len(self.candidate_ids) or any(len(i) > 80 for i in self.candidate_ids):
            raise ValueError('导出候选身份不可重复且每项不超过 80 字符')
        return self


class AdsorptionSite(StrictModel):
    site_id: str
    kind: Literal['ontop', 'bridge', 'hollow', 'manual']
    label: str
    cartesian: tuple[float, float, float]
    fractional: tuple[float, float, float]
    source_atom_ids: list[str]


class SurfaceFrame(StrictModel):
    x: tuple[float, float, float]
    y: tuple[float, float, float]
    z: tuple[float, float, float]
    rotation_convention: Literal['fixed_surface_xyz_X_then_Y_then_Z']


class SiteSettings(StrictModel):
    kinds: list[Literal['ontop', 'bridge', 'hollow']]
    manual_sites: list[ManualSite]
    dedup_tolerance_angstrom: float


class CandidateValidation(StrictModel):
    minimum_adsorbate_surface_distance_angstrom: float
    minimum_periodic_self_image_distance_angstrom: float
    screening_distance_angstrom: float
    screening_passed: bool
    warnings: list[str]


class AdsorptionCandidate(StrictModel):
    candidate_id: str
    site_id: str
    label: str
    parent_revision: int
    parent_surface_id: str
    parent_snapshot_sha256: str
    adsorbate_source_id: str
    status: Literal['valid', 'stale']
    invalidation_reason: str | None = None
    snapshot: StructureSnapshot
    placement: Placement
    validation: CandidateValidation
    transform: dict


class AdsorptionState(StrictModel):
    adsorbate: AdsorbateRecord | None = None
    sites: list[AdsorptionSite] = Field(default_factory=list)
    site_parent_snapshot_sha256: str | None = None
    site_parent_surface_id: str | None = None
    site_parent_revision: int | None = None
    frame: SurfaceFrame | None = None
    site_settings: SiteSettings | None = None
    site_surface_atoms: list[AdsorbateAtom] = Field(default_factory=list)
    site_map_origin_cartesian: tuple[float, float, float] | None = None
    placement: Placement | None = None
    selected_site_ids: list[str] = Field(default_factory=list)
    candidates: list[AdsorptionCandidate] = Field(default_factory=list)
    selected_candidate_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Draft(StrictModel):
    draft_id: str
    schema_version: Literal[1]
    revision: int
    name: str
    created_at: str
    updated_at: str
    source: SourceRecord
    input_snapshot: StructureSnapshot
    parameters: SurfaceParams | None
    surfaces: list[SurfaceOption]
    active_surface_id: str | None
    warnings: list[str]
    adsorption: AdsorptionState | None = None


class DraftSummary(StrictModel):
    draft_id: str
    revision: int
    name: str
    updated_at: str
    source_role: Literal['bulk', 'slab']
    atom_count: int
    surface_count: int
    active_surface_id: str | None


class DraftResponse(BaseModel):
    mode: Literal['toolbox'] = 'toolbox'
    draft: Draft


class DraftListResponse(BaseModel):
    mode: Literal['toolbox'] = 'toolbox'
    drafts: list[DraftSummary]


class WorkflowBindingResponse(StrictModel):
    mode: Literal['toolbox'] = 'toolbox'
    structure_id: str
    summary: dict
    binding: CatalysisBinding
    surface_policy: SurfacePolicy
