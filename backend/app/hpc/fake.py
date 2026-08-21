from __future__ import annotations

import hashlib
import re
import time
import uuid
from typing import Optional

from ..core.errors import AppError, ConflictError, ValidationError
from .schemas import (
    HPC_COLLECT, HPC_DEPLOY, HPC_SUBMIT,
    AuditEvent, CapabilityGrant, ClusterProfile,
    CollectionFileEntry, DeployOperation, DeploymentPlan,
    JobState, PreflightCheck, RemoteJob, RemoteManifest,
    RemotePreflightResult, ResultCollection, SchedulerProfile,
    SubmissionDraft, SubmissionResources, SubmitReceipt,
    collection_decision,
)

_SHELL_META = re.compile(r"[;|&<>`$()'\" ]")

DEFAULT_CLUSTER = ClusterProfile(
    cluster_profile_id="cluster_demo_slurm",
    display_name="教学 Slurm 集群（Fake）",
    scheduler_profile_id="scheduler_demo_slurm",
    scheduler_type="slurm",
    connector_status="available",
    capabilities=["deploy", "submit", "status", "collect"],
    limits={"max_nodes": 1, "max_tasks": 64, "max_walltime": "24:00:00",
            "max_upload_bytes": 10 * 1024 * 1024},
    allowed_partitions=["cpu"],
    pseudopotential_mode="remote_authorized_library",
    allowed_remote_root="vasp-copilot",
)

DEFAULT_SCHEDULER = SchedulerProfile(
    scheduler_profile_id="scheduler_demo_slurm",
    display_name="教学 Slurm 提交配置（Fake）",
    profile_version="1.0.0",
    scheduler_type="slurm",
    submit_command="sbatch",
    submit_argv_whitelist=[
        "sbatch", "--parsable",
        "--partition=", "--account=", "--nodes=", "--ntasks=",
        "--mem=", "--time=", "--job-name=", "--output=", "--error=",
    ],
    status_command="squeue",
    accounting_command="sacct",
)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _short_hash(data: bytes, length: int = 8) -> str:
    return hashlib.sha256(data).hexdigest()[:length]


def _argv_allowed(profile: SchedulerProfile, argv: list[str],
                allowed_operand: Optional[str] = None) -> Optional[str]:
    if not argv:
        return "空 argv"
    for token in argv:
        if _SHELL_META.search(token):
            return f"argv 含 shell 元字符: {token!r}"
        if token == allowed_operand:
            continue
        if token in profile.submit_argv_whitelist:
            continue
        if any(token.startswith(p) for p in profile.submit_argv_whitelist
               if p.endswith("=")):
            continue
        return f"argv 不在配置化白名单: {token!r}"
    return None


class FakeSchedulerAdapter:
    """P1 fake SchedulerAdapter（设计 6.19-6.24）。

    只做参数化 argv 白名单提交与确定性的状态流转，绝不执行真实 shell。
    """

    def __init__(self, profile: SchedulerProfile) -> None:
        self._profile = profile
        self._seq = 842000
        self._polls: dict[str, int] = {}

    @property
    def profile(self) -> SchedulerProfile:
        return self._profile

    def submit(self, draft: SubmissionDraft, grant: CapabilityGrant,
               argv: list[str], scenario: str = "completed",
               allowed_operand: Optional[str] = None) -> tuple[SubmitReceipt, RemoteJob]:
        if grant.capability != HPC_SUBMIT:
            raise ValidationError("HPC_GRANT_SCOPE", "HPC_SUBMIT grant 才能提交")
        if grant.used:
            raise ConflictError("HPC_GRANT_USED", "一次性 HPC_SUBMIT grant 已消费")
        rejected = _argv_allowed(self._profile, argv, allowed_operand=allowed_operand)
        if rejected:
            raise ValidationError("HPC_ARGV_REJECTED", rejected)
        if scenario not in ("completed", "failed"):
            raise ValidationError("HPC_BAD_SCENARIO", "fake scenario 仅 completed/failed")
        grant.used = True
        self._seq += 1
        job_id = f"rjob_{self._seq - 842000:03d}"
        now = _now()
        job = RemoteJob(
            remote_job_id=job_id,
            submission_draft_id=draft.submission_draft_id,
            scheduler_job_id=str(self._seq),
            bundle_hash=draft.idempotency_key.split(":")[-1] or "",
            profile_id=self._profile.scheduler_profile_id,
            profile_version=self._profile.profile_version,
            idempotency_key=draft.idempotency_key,
            status="pending",
            state=JobState(normalized="pending", scheduler_state="PENDING",
                           submitted_at=now),
            resources=draft.resources,
            scenario=scenario,
            last_synced_at=now,
        )
        self._polls[job_id] = 0
        receipt = SubmitReceipt(
            scheduler=self._profile.scheduler_type,
            scheduler_job_id=job.scheduler_job_id,
            submitted_at=now,
            cluster_profile_id="cluster_demo_slurm",
            manifest_id=draft.manifest_id,
            step_id=draft.step_id,
            idempotency_key=draft.idempotency_key,
        )
        return receipt, job

    def query_active(self, job: RemoteJob) -> JobState:
        return self._step(job)

    def query_accounting(self, job: RemoteJob) -> JobState:
        # accounting 与 status 同源：逐步推进到终止（设计 6.22 不以“从活动查询消失”代替完成态）。
        return self._step(job)

    def _step(self, job: RemoteJob) -> JobState:
        n = self._polls.get(job.remote_job_id, 0) + 1
        self._polls[job.remote_job_id] = n
        now = _now()
        if n < 2:
            job.state.normalized = "pending"
            job.state.scheduler_state = "PENDING"
        elif n < 3:
            job.state.normalized = "running"
            job.state.scheduler_state = "RUNNING"
            job.state.started_at = job.state.started_at or now
        else:
            return self._finalize(job)
        job.state.ended_at = None
        job.collectable = False
        job.status = job.state.normalized
        job.last_synced_at = now
        return job.state

    def _finalize(self, job: RemoteJob) -> JobState:
        now = _now()
        ok = job.scenario == "completed"
        job.state.normalized = "completed" if ok else "failed"
        job.state.scheduler_state = "COMPLETED" if ok else "FAILED"
        job.state.exit_code = 0 if ok else 1
        job.state.reason = None if ok else "fake: 计算失败演示"
        job.state.ended_at = now
        job.collectable = True
        job.status = job.state.normalized
        job.last_synced_at = now
        return job.state


class FakeHpcBridge:
    """P1 fake HPC Bridge：与真实 Bridge 同一 schema/状态机（设计 6.13-6.24）。

    - 一次性 HPC_DEPLOY / HPC_SUBMIT / HPC_COLLECT grant；
    - 幂等键禁止重复提交；
    - argv 参数化白名单，无任意 shell；
    - 回收白名单永不包含 POTCAR/WAVECAR/CHGCAR/隐藏/符号链接；
    - enabled=False 时整段关闭（默认开关见 FeatureFlag.local_fake_hpc）。
    """

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self._cluster = DEFAULT_CLUSTER
        self._scheduler = DEFAULT_SCHEDULER
        self._adapter = FakeSchedulerAdapter(self._scheduler)
        self._deployments: dict[str, dict] = {}
        self._drafts: dict[str, SubmissionDraft] = {}
        self._jobs: dict[str, RemoteJob] = {}
        self._jobs_by_key: dict[str, str] = {}
        self._manifests: dict[str, RemoteManifest] = {}
        self._grants: dict[str, CapabilityGrant] = {}
        self._deploy_idempotency: dict[str, str] = {}
        self.audit_log: list[AuditEvent] = []

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise AppError("HPC_BRIDGE_DISABLED",
                           "HPC Bridge 已关闭（本地/演示默认无须 HPC，P0 不受影响）", 409)

    def _audit(self, action: str, entity_id: str, ok: bool = True,
               detail: str = "") -> None:
        self.audit_log.append(AuditEvent(ts=_now(), action=action,
                                         entity_id=entity_id, ok=ok, detail=detail))

    def _store_grant(self, grant: CapabilityGrant) -> CapabilityGrant:
        self._grants[grant.grant_id] = grant
        return grant

    def _new_grant(self, capability: str, *, denied: Optional[list[str]] = None,
                   submit_allowed: bool = False) -> CapabilityGrant:
        return self._store_grant(CapabilityGrant(
            grant_id=f"grant_{uuid.uuid4().hex[:8]}",
            capability=capability, single_use=True, expires_at=_now(),
            submit_allowed=submit_allowed, denied_patterns=denied or [],
        ))

    def list_clusters(self) -> list[ClusterProfile]:
        self._require_enabled()
        return [self._cluster]

    def plan_deployment(self, bundle: dict[str, bytes],
                        workflow_id: str = "wf_01",
                        revision: int = 1) -> DeploymentPlan:
        self._require_enabled()
        deployment_id = f"deploy_{uuid.uuid4().hex[:8]}"
        ops: list[DeployOperation] = []
        total = 0
        for i, rel in enumerate(sorted(bundle), start=1):
            content = bundle[rel]
            total += len(content)
            ops.append(DeployOperation(
                operation_id=f"op_{i:02d}", type="upload_file",
                relative_path=rel,
                sha256=hashlib.sha256(content).hexdigest()[:12],
                size_bytes=len(content)))
        total_bytes = total
        revision_dir = f"{workflow_id}/rev-{revision}"
        plan = DeploymentPlan(
            deployment_id=deployment_id,
            deployment_status="draft",
            bundle_sha256=hashlib.sha256(b"|".join(
                sorted(bundle.values()))).hexdigest(),
            target_relative_path=f"{self._cluster.allowed_remote_root}/{revision_dir}",
            file_count=len(bundle), total_bytes=total_bytes, overwrite=False,
            operations=ops, required_capability=HPC_DEPLOY,
            warnings=["Fake：本适配器仅演示同一 schema，不真实写远程"] if not self._enabled else [],
        )
        self._deployments[deployment_id] = {"plan": plan}
        self._audit("plan_deployment", deployment_id)
        return plan

    def preflight(self, deployment_id: str) -> RemotePreflightResult:
        self._require_enabled()
        self._ensure_deploy(deployment_id)
        checks = [
            PreflightCheck(check_id="connector", check_status="passed",
                           message="Fake Connector 可用"),
            PreflightCheck(check_id="target_absent", check_status="passed",
                           message="目标 revision 不存在（不覆盖）"),
            PreflightCheck(check_id="quota", check_status="passed",
                           message="可用空间满足本次上传"),
        ]
        rec = self._deployments[deployment_id]
        rec["preflight"] = RemotePreflightResult(passed=True, checked_at=_now(),
                                                 checks=checks, expires_at=_now())
        self._audit("preflight", deployment_id)
        return rec["preflight"]

    def authorize_deploy(self, deployment_id: str) -> CapabilityGrant:
        self._require_enabled()
        self._ensure_deploy(deployment_id)
        grant = self._new_grant(HPC_DEPLOY)
        self._deployments[deployment_id]["grant"] = grant
        self._deployments[deployment_id]["plan"].deployment_status = "authorized"
        self._audit("authorize_deploy", deployment_id)
        return grant

    def execute_deploy(self, deployment_id: str, grant_id: str,
                       idempotency_key: str) -> RemoteManifest:
        self._require_enabled()
        self._ensure_deploy(deployment_id)
        rec = self._deployments[deployment_id]
        if deployment_id in self._deploy_idempotency:
            if self._deploy_idempotency[deployment_id] != idempotency_key:
                raise ConflictError("HPC_IDEMPOTENCY_MISMATCH", "幂等键与首次执行不一致")
            self._audit("deploy_idempotent_replay", deployment_id)
            return rec["manifest"]
        grant = self._grants.get(grant_id)
        if grant is None or grant.capability != HPC_DEPLOY:
            raise ValidationError("HPC_GRANT_INVALID", "缺少有效的 HPC_DEPLOY grant")
        if grant.used:
            raise ConflictError("HPC_GRANT_USED", "一次性 HPC_DEPLOY grant 已消费")
        grant.used = True
        plan = rec["plan"]
        manifest = RemoteManifest(
            manifest_id=f"rmanifest_{uuid.uuid4().hex[:8]}",
            cluster_profile_id=self._cluster.cluster_profile_id,
            target_relative_path=plan.target_relative_path,
            bundle_sha256=plan.bundle_sha256,
            file_count=plan.file_count, verified=True, deployed_at=_now(),
            pseudopotential={"included": False, "mode": "required_before_submit"},
            next_action="create_submission_draft",
        )
        rec["manifest"] = manifest
        rec["plan"].deployment_status = "deployed"
        self._manifests[manifest.manifest_id] = manifest
        self._deploy_idempotency[deployment_id] = idempotency_key
        self._audit("execute_deploy", deployment_id)
        return manifest

    def get_deployment(self, deployment_id: str) -> dict:
        self._require_enabled()
        self._ensure_deploy(deployment_id)
        rec = self._deployments[deployment_id]
        return {
            "deployment_id": deployment_id,
            "deployment_status": rec["plan"].deployment_status,
            "progress": {"uploaded_files": rec["plan"].file_count,
                         "total_files": rec["plan"].file_count},
            "remote_manifest": rec.get("manifest"),
            "error": None,
        }

    def create_submission_draft(self, deployment_id: str, step_id: str = "01_relax",
                                resources: Optional[SubmissionResources] = None,
                                submit_script: str = "submit.sh") -> SubmissionDraft:
        self._require_enabled()
        self._ensure_deploy(deployment_id)
        manifest = self._deployments[deployment_id].get("manifest")
        if manifest is None:
            raise ConflictError("HPC_NOT_DEPLOYED", "部署完成后才能创建提交草稿")
        res = resources or SubmissionResources()
        key_seed = f"{manifest.bundle_sha256}:{step_id}:{submit_script}"
        key = (f"{self._cluster.cluster_profile_id}:{manifest.manifest_id}:"
               f"{step_id}:{_short_hash(key_seed.encode(), 12)}")
        draft = SubmissionDraft(
            submission_draft_id=f"subdraft_{uuid.uuid4().hex[:8]}",
            hpc_job_status="ready_for_confirmation",
            manifest_id=manifest.manifest_id, step_id=step_id,
            script_relative_path=f"{step_id}/{submit_script}",
            resources=res,
            scheduler_profile={"scheduler_profile_id": self._scheduler.scheduler_profile_id,
                               "display_name": self._scheduler.display_name,
                               "profile_version": self._scheduler.profile_version},
            action_preview=(f"使用已审核 scheduler profile 提交 "
                            f"{step_id}/{submit_script}（命令不可在 UI 编辑）"),
            preflight={"passed": True, "checks": [], "warnings": []},
            idempotency_key=key,
        )
        self._drafts[draft.submission_draft_id] = draft
        self._audit("plan_submission", draft.submission_draft_id)
        return draft

    def authorize_submit(self, submission_draft_id: str) -> CapabilityGrant:
        self._require_enabled()
        if submission_draft_id not in self._drafts:
            raise ConflictError("HPC_DRAFT_NOT_FOUND", "提交草稿不存在")
        grant = self._new_grant(HPC_SUBMIT)
        self._drafts[submission_draft_id].hpc_job_status = "authorized"
        self._audit("authorize_submit", submission_draft_id)
        return grant

    def submit(self, submission_draft_id: str, grant_id: str,
               idempotency_key: str, argv: Optional[list[str]] = None,
               scenario: str = "completed") -> tuple[SubmitReceipt, RemoteJob]:
        self._require_enabled()
        draft = self._drafts.get(submission_draft_id)
        if draft is None:
            raise ConflictError("HPC_DRAFT_NOT_FOUND", "提交草稿不存在")
        if idempotency_key != draft.idempotency_key:
            raise ValidationError("HPC_IDEMPOTENCY_MISMATCH",
                                  "幂等键与草稿不一致，禁止重复/篡改提交")
        existing = self._jobs_by_key.get(idempotency_key)
        if existing is not None:
            job = self._jobs[existing]
            self._audit("submit_idempotent_replay", job.remote_job_id)
            return self._receipt_for(job), job
        grant = self._grants.get(grant_id)
        if grant is None or grant.capability != HPC_SUBMIT:
            raise ValidationError("HPC_GRANT_INVALID", "缺少有效的 HPC_SUBMIT grant")
        argv = argv or ["sbatch", f"--job-name={draft.step_id}",
                        f"--nodes={draft.resources.nodes}",
                        f"--ntasks={draft.resources.tasks}",
                        f"--time={draft.resources.walltime or '12:00:00'}",
                        draft.script_relative_path]
        receipt, job = self._adapter.submit(
            draft, grant, argv, scenario=scenario,
            allowed_operand=draft.script_relative_path)
        self._jobs[job.remote_job_id] = job
        self._jobs_by_key[idempotency_key] = job.remote_job_id
        self._audit("submit", job.remote_job_id)
        return receipt, job

    def _receipt_for(self, job: RemoteJob) -> SubmitReceipt:
        return SubmitReceipt(
            scheduler="slurm", scheduler_job_id=job.scheduler_job_id,
            submitted_at=job.state.submitted_at, cluster_profile_id="cluster_demo_slurm",
            manifest_id=job.submission_draft_id, step_id=job.state.normalized,
            idempotency_key=job.idempotency_key,
        )

    def advance(self, job_id: str, steps: Optional[int] = None) -> RemoteJob:
        self._require_enabled()
        job = self._jobs.get(job_id)
        if job is None:
            raise ConflictError("HPC_JOB_NOT_FOUND", "作业不存在")
        if steps is None:
            while job.status not in ("completed", "failed"):
                self._adapter.query_accounting(job)
        else:
            for _ in range(steps):
                if job.status in ("completed", "failed"):
                    break
                self._adapter.query_active(job)
        self._audit("status", job_id, detail=job.status)
        return job

    def get_job(self, job_id: str) -> RemoteJob:
        self._require_enabled()
        return self.advance(job_id, steps=1)

    def authorize_collection(self, job_id: str) -> CapabilityGrant:
        self._require_enabled()
        job = self._jobs.get(job_id)
        if job is None:
            raise ConflictError("HPC_JOB_NOT_FOUND", "作业不存在")
        if job.status not in ("completed", "failed"):
            raise ConflictError("HPC_NOT_TERMINAL", "terminal 后才能回收结果")
        grant = self._new_grant(HPC_COLLECT, denied=[
            "POTCAR", "WAVECAR", "CHGCAR", "symlink", "hidden_file"])
        self._audit("authorize_collection", job_id)
        return grant

    def collect(self, job_id: str, grant_id: str,
                remote_files: dict[str, bytes], create_diagnosis: bool = True,
                max_total_bytes: int = 100 * 1024 * 1024) -> ResultCollection:
        self._require_enabled()
        job = self._jobs.get(job_id)
        if job is None:
            raise ConflictError("HPC_JOB_NOT_FOUND", "作业不存在")
        if job.status not in ("completed", "failed"):
            raise ConflictError("HPC_NOT_TERMINAL", "terminal 后才能回收结果")
        grant = self._grants.get(grant_id)
        if grant is None or grant.capability != HPC_COLLECT:
            raise ValidationError("HPC_GRANT_INVALID", "缺少有效的 HPC_COLLECT grant")
        if grant.used:
            raise ConflictError("HPC_GRANT_USED", "一次性 HPC_COLLECT grant 已消费")
        files: list[CollectionFileEntry] = []
        excluded: list[dict[str, str]] = []
        total = 0
        for rel in sorted(remote_files):
            data = remote_files[rel]
            allowed, reason = collection_decision(rel)
            if allowed:
                files.append(CollectionFileEntry(
                    relative_path=rel, size_bytes=len(data),
                    sha256=hashlib.sha256(data).hexdigest()[:12]))
                total += len(data)
            else:
                excluded.append({"relative_path": rel, "reason": reason})
        if total > max_total_bytes:
            raise AppError("HPC_COLLECT_TOO_LARGE", "回收文件超过大小上限", 413)
        grant.used = True
        job.collected = True
        collection_id = f"collect_{uuid.uuid4().hex[:8]}"
        diagnosis_id = f"diag_remote_{job_id}" if create_diagnosis else None
        result = ResultCollection(
            collection_id=collection_id,
            collection_status="succeeded",
            manifest_files=files, excluded=excluded, partial=False,
            diagnosis_id=diagnosis_id,
            next_url=(f"/api/v1/diagnosis/{diagnosis_id}" if diagnosis_id else ""),
        )
        self._audit("collect", job_id, detail=f"{len(files)} files, {len(excluded)} excluded")
        return result

    def _ensure_deploy(self, deployment_id: str) -> None:
        if deployment_id not in self._deployments:
            raise ConflictError("HPC_DEPLOY_NOT_FOUND", "部署不存在")