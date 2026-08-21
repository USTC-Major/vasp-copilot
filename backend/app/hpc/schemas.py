from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

HPC_DEPLOY = "HPC_DEPLOY"
HPC_SUBMIT = "HPC_SUBMIT"
HPC_COLLECT = "HPC_COLLECT"

# 结果回收白名单（设计 6.23 / 6.24 / 8.5）：POTCAR/WAVECAR/CHGCAR 永久排除。
COLLECT_ALLOWED_EXACT = ("INCAR", "POSCAR", "CONTCAR", "KPOINTS",
                         "OSZICAR", "OUTCAR")
COLLECT_ALLOWED_LOG_MARKERS = (".out", ".log", "job", "slurm")
COLLECT_DENIED_EXACT = ("POTCAR", "WAVECAR", "CHGCAR")


def collection_decision(name: str, *, is_symlink: bool = False) -> tuple[bool, str]:
    """终态结果收集的 (allowed, reason)（设计 6.23 禁用列表）。"""
    if is_symlink:
        return False, "symlink"
    if name.startswith("."):
        return False, "hidden_file"
    up = name.upper()
    if up in COLLECT_DENIED_EXACT or up.startswith("WAVEDER") or up.startswith("CHG"):
        return False, "policy_denied"
    if up in COLLECT_ALLOWED_EXACT:
        return True, ""
    low = name.lower()
    if low == "vasprun.xml":
        return True, ""
    if any(m in low for m in COLLECT_ALLOWED_LOG_MARKERS):
        return True, ""
    return False, "not_in_whitelist"


class ClusterProfile(BaseModel):
    """设计 6.13：绝不携带 host/user/私钥/token 等机密。"""

    model_config = ConfigDict(extra="ignore")

    cluster_profile_id: str
    display_name: str = ""
    scheduler_profile_id: str = ""
    scheduler_type: str = "slurm"
    connector_status: str = "available"
    capabilities: list[str] = ["deploy", "submit", "status", "collect"]
    limits: dict[str, Any] = {}
    allowed_partitions: list[str] = ["cpu"]
    pseudopotential_mode: str = "remote_authorized_library"
    allowed_remote_root: str = ""
    max_upload_bytes: int = 10 * 1024 * 1024


class SchedulerProfile(BaseModel):
    """管理员版本化的提交配置（设计 6.19/8.x：仅 argv 白名单）。"""

    model_config = ConfigDict(extra="ignore")

    scheduler_profile_id: str
    display_name: str = ""
    profile_version: str = "1.0.0"
    scheduler_type: str = "slurm"
    submit_command: str = "sbatch"
    submit_argv_whitelist: list[str] = []
    status_command: str = "squeue"
    accounting_command: str = "sacct"
    submit_timeout_seconds: float = 30.0


class CapabilityGrant(BaseModel):
    model_config = ConfigDict(extra="ignore")

    grant_id: str
    capability: str = HPC_DEPLOY
    single_use: bool = True
    expires_at: str = ""
    overwrite_allowed: bool = False
    delete_allowed: bool = False
    submit_allowed: bool = False
    deployment_write_allowed: bool = False
    denied_patterns: list[str] = []
    used: bool = False


class DeployOperation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    operation_id: str
    type: str  # create_directory|upload_file|atomic_rename
    relative_path: str = ""
    source_file_id: Optional[str] = None
    sha256: Optional[str] = None
    size_bytes: int = 0


class DeploymentPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = "1.0"
    deployment_id: str
    deployment_status: str = "draft"
    bundle_sha256: str = ""
    target_relative_path: str = ""
    file_count: int = 0
    total_bytes: int = 0
    overwrite: bool = False
    operations: list[DeployOperation] = []
    required_capability: str = HPC_DEPLOY
    warnings: list[str] = []


class PreflightCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")

    check_id: str
    check_status: str = "passed"  # passed|failed|warning
    message: str = ""


class RemotePreflightResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    passed: bool = True
    checked_at: str = ""
    checks: list[PreflightCheck] = []
    warnings: list[str] = []
    expires_at: str = ""


class RemoteManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    manifest_id: str
    cluster_profile_id: str
    target_relative_path: str
    bundle_sha256: str
    file_count: int = 0
    verified: bool = True
    deployed_at: str = ""
    pseudopotential: dict[str, Any] = {}
    next_action: str = "create_submission_draft"


class SubmissionResources(BaseModel):
    model_config = ConfigDict(extra="ignore")

    partition: Optional[str] = None
    account: Optional[str] = None
    nodes: int = 1
    tasks: int = 1
    memory_gb: int = 0
    walltime: str = ""
    max_rss_mb: Optional[int] = None
    elapsed: str = ""


class SubmissionDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    submission_draft_id: str
    hpc_job_status: str = "ready_for_confirmation"
    manifest_id: str
    step_id: str
    script_relative_path: str = ""
    resources: SubmissionResources = SubmissionResources()
    scheduler_profile: dict[str, str] = {}
    action_preview: str = ""
    preflight: dict[str, Any] = {}
    idempotency_key: str = ""


class SubmitReceipt(BaseModel):
    model_config = ConfigDict(extra="ignore")

    scheduler: str = "slurm"
    scheduler_job_id: str = ""
    submitted_at: str = ""
    cluster_profile_id: str = ""
    manifest_id: str = ""
    step_id: str = ""
    idempotency_key: str = ""


class JobState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    normalized: str = "pending"  # pending|running|completed|failed
    scheduler_state: str = ""
    reason: Optional[str] = None
    exit_code: Optional[int] = None
    submitted_at: str = ""
    started_at: Optional[str] = None
    ended_at: Optional[str] = None


class RemoteJob(BaseModel):
    model_config = ConfigDict(extra="ignore")

    remote_job_id: str
    submission_draft_id: str
    scheduler_job_id: str
    bundle_hash: str = ""
    profile_id: str = ""
    profile_version: str = ""
    idempotency_key: str = ""
    status: str = "pending"
    state: JobState = JobState()
    resources: SubmissionResources = SubmissionResources()
    collectable: bool = False
    collected: bool = False
    last_synced_at: str = ""
    source: str = "scheduler_status_adapter"
    scenario: str = "completed"  # fake-only control: completed|failed


class CollectionFileEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    relative_path: str
    size_bytes: int = 0
    sha256: str = ""


class ResultCollection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    collection_id: str
    collection_status: str = "succeeded"
    manifest_files: list[CollectionFileEntry] = []
    excluded: list[dict[str, str]] = []
    partial: bool = False
    diagnosis_id: Optional[str] = None
    next_url: str = ""


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ts: str = ""
    action: str = ""
    entity_id: str = ""
    ok: bool = True
    detail: str = ""
