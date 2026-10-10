"""Strict request contracts for the energy confirmation table."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

EnergyBasis = Literal['sigma_to_zero_ev', 'without_entropy_ev', 'free_energy_toten_ev']
AnalysisKind = Literal['adsorption', 'formation']
Role = Literal['clean_slab', 'adsorbate', 'adsorbed', 'material', 'element_reference']
AssignmentOrigin = Literal['manual', 'auto']


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class EnergyFields(Model):
    sigma_to_zero_ev: float | None = Field(default=None, allow_inf_nan=False, strict=True)
    without_entropy_ev: float | None = Field(default=None, allow_inf_nan=False, strict=True)
    free_energy_toten_ev: float | None = Field(default=None, allow_inf_nan=False, strict=True)


class Create(Model):
    title: str = Field(default='', max_length=120)
    # Omission is reserved for compatibility with clients using the old groups UI.
    analysis_kind: AnalysisKind | None = None


class Revision(Model):
    expected_revision: int = Field(ge=0, strict=True)


class CopyAnalysis(Revision):
    analysis_kind: AnalysisKind
    title: str | None = Field(default=None, max_length=120)
    group_id: str | None = Field(default=None, max_length=80)


class Manual(Revision):
    name: str = Field(min_length=1, max_length=256)
    composition: dict[str, StrictInt] = Field(strict=True)
    energy_fields: EnergyFields
    energy_basis: EnergyBasis
    unit: Literal['eV']
    reference_note: str = Field(default='', max_length=6000)


class Override(Model):
    composition: dict[str, StrictInt] | None = Field(default=None, strict=True)
    energy_fields: EnergyFields | None = None
    energy_basis: EnergyBasis | None = None
    unit: Literal['eV']
    note: str = Field(min_length=1, max_length=2000)


class SampleConfirmation(Model):
    sample_id: str
    name: str | None = Field(default=None, min_length=1, max_length=256)
    role: Role | None = None
    included: bool = False
    confirmed: bool = False
    accepted_warnings: bool = False
    override: Override | None = None
    role_origin: AssignmentOrigin | None = None
    included_origin: AssignmentOrigin | None = None


class Target(Model):
    sample_id: str
    adsorbate_count: int = Field(default=1, ge=1, strict=True)


class Group(Model):
    id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    name: str = Field(min_length=1, max_length=120)
    kind: Literal['adsorption', 'formation']
    energy_basis: EnergyBasis
    basis_confirmed: bool = False
    clean_sample_id: str | None = None
    adsorbate_sample_id: str | None = None
    reference_units: int = Field(default=1, ge=1, strict=True)
    element_references: dict[str, str] = Field(default_factory=dict)
    targets: list[Target] = Field(default_factory=list, max_length=100)
    reference_note: str = Field(default='', max_length=2000)
    reference_origins: dict[str, AssignmentOrigin] = Field(default_factory=dict)


class Configuration(Revision):
    title: str | None = Field(default=None, max_length=120)
    samples: list[SampleConfirmation] = Field(default_factory=list, max_length=100)
    groups: list[Group] = Field(default_factory=list, max_length=100)


class TaskPreview(Model):
    project_id: str = Field(min_length=1, max_length=256)
    task_id: str = Field(min_length=1, max_length=256)
    job_key: str = Field(min_length=1, max_length=128)
    attempt_id: str = Field(min_length=1, max_length=256)


class TaskImport(Revision):
    preview_id: str
    name: str = Field(default='OUTCAR', min_length=1, max_length=256)


class Reuse(Revision):
    source_collection_id: str
    sample_id: str


class RemoveSamples(Revision):
    sample_ids: list[str] = Field(default_factory=list, max_length=100)
    clear_all: bool = False
