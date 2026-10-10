"""CAT-01/02 request contract. hkl always refers to the supplied input cell."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


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
    kind: Literal['import', 'surface_replica']
    source_atom_id: str | None = None
    source_index: int
    replica_ordinal: int | None = None
    mapping: str | None = None


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
