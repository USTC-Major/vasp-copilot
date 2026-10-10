"""Immutable CAT structure handoff and explicit surface input policy."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class SurfacePolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    policy_id: Literal['cat_surface_fixed_cell_2d_v1'] = 'cat_surface_fixed_cell_2d_v1'
    cell_relaxation: Literal['fixed'] = 'fixed'
    relax_isif: Literal[2] = 2
    kpoint_mode: Literal['gamma_2d', 'explicit_gamma_2d'] = 'gamma_2d'
    vacuum_axis: Literal[2] = 2
    normal_sampling_count: Literal[1] = 1
    c_parallel_to_normal: bool
    normal: tuple[float, float, float]
    sources: list[str]
    warnings: list[str]


class PoscarRowMapping(BaseModel):
    model_config = ConfigDict(extra='forbid')
    poscar_row: int = Field(ge=1)
    snapshot_index: int = Field(ge=0)
    atom_id: str


class CatalysisBinding(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    schema_version: Literal[1] = 1
    binding_id: str
    draft_id: str
    revision: int = Field(ge=1)
    model_kind: Literal['clean_surface', 'adsorption_candidate']
    surface_id: str
    candidate_id: str | None
    snapshot_id: str
    snapshot_sha256: str
    parent_clean_snapshot_id: str
    parent_clean_snapshot_sha256: str
    poscar_sha256: str
    poscar_row_mapping: list[PoscarRowMapping]
    selective_flags_basis: Literal['direct_lattice_vectors'] = 'direct_lattice_vectors'
    source: dict
    parameters: dict | None
    surface: dict
    transform: dict
    adsorbate: dict | None
    candidate: dict | None
    snapshot: dict
    parent_clean_snapshot: dict
    warnings: list[str]
