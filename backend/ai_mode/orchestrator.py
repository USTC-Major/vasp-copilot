# -*- coding: utf-8 -*-
"""AI mode orchestration with explicit, single-use mutation approvals.

Preparation and precheck are inventory-only.  Submission is possible only
after a current script attestation, a hard precheck, and a fresh confirmation;
capacity or dependency changes never trigger automatic submission.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .agent.tools import _label_slug
from .config import AiModeConfig, execution_mode
from .consent import task_lock as _task_lock
from .jobs.scheduler import parse_slurm_output
from .projects import ProjectStore
from .report.extract import summarize_run
from .report.render import render_report
from .schemas import JobEntry, JobStatus as SessionJobStatus, \
    PlanSnapshot, PlanStep, RequirementSnapshot, Session
from .tools.draft import (find_remote_submit_script,
                          fingerprint_local_submit_script,
                          fingerprint_remote_submit_script,
                          input_fingerprint_local,
                          input_fingerprint_remote, precheck_snapshot,
                          resolve_user_submit_script, submit_command)
from .workflow.plan import gate_jobs

logger = logging.getLogger("ai_mode.orchestrator")
__test__ = False

_PHASE_STATUS = {
    "running": "planned",
    "await_submit": "generated",
    "monitoring": "submitted",
    "done": "done",
    "blocked": "planned",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def _is_true_answer(content: str) -> bool:
    text = _norm(content)
    if text in ("确认", "提交", "确认提交", "同意"):
        return True
    return any(p in text for p in ("确认提交", "同意提交", "开始提交",
                                   "提交作业", "确认了"))


def _is_cancel(content: str) -> bool:
    return any(p in _norm(content) for p in ("取消", "不提交", "放弃"))


def _detect_job_label(goal: str) -> str:
    g = _norm(goal)
    if any(w in g for w in ("能带", "band", "bandstructure")):
        return "能带计算"
    if any(w in g for w in ("态密度", "dos", "states")):
        return "态密度计算"
    if any(w in g for w in ("声子", "phonon")):
        return "声子计算"
    if any(w in g for w in ("静态", "自洽", "scf")):
        return "静态自洽"
    if any(w in g for w in ("分子动力学", "aimd")):
        return "分子动力学"
    return "结构优化"


def _remote_state_map(stdout: str) -> dict[str, str]:
    """把 squeue 输出解析为 slurm_id -> 状态列（默认列序 JOBID..ST..）。"""
    out: dict[str, str] = {}
    for raw in (stdout or "").splitlines():
        tokens = (raw or "").strip().split()
        if not tokens or tokens[0].upper() in ("JOBID", "JOB ID"):
            continue
        if len(tokens) >= 5:
            out[tokens[0]] = tokens[4].upper()
    return out


def _status_to_session(job_status: str):
    mapping = {
        "submitted": SessionJobStatus.RUNNING,
        "queued": SessionJobStatus.QUEUED,
        "running": SessionJobStatus.RUNNING,
        "waiting": SessionJobStatus.QUEUED,
        "completed": SessionJobStatus.COMPLETED,
        "failed": SessionJobStatus.FAILED,
        "not_converged": SessionJobStatus.NOT_CONVERGED,
        "canceled": SessionJobStatus.CANCELLED,
        "skipped": SessionJobStatus.CANCELLED,
    }
    return mapping.get(job_status, SessionJobStatus.PLANNED)


def _build_llm(cfg: AiModeConfig):
    try:
        from .llm.factory import build_client
        return build_client(cfg)
    except Exception:  # noqa: BLE001
        return None


class Orchestrator:
    """一个计算任务的中枢：推动真实工序并把流动进程持久化到 task['flow']。

    :param hpc: SSHManager（真实超算）或 None。测试可注入具备 run/stat/
        read_file/write_file/mkdir 同签名方法的假对象来离线验证真实链路。
    :param llm_factory: ``(cfg) -> LLMClient 或 None``；缺省走 M3 工厂。
    :param data_dir: 本地数据根（工作区/技能），缺省用 cfg.data_dir。
    """

    def __init__(self, cfg: AiModeConfig, *, hpc=None, llm_factory=None,
                 data_dir: Optional[Path] = None,
                 hpc_execution_mode: str | None = None):
        self.cfg = cfg
        self.hpc = hpc
        self.execution_mode = execution_mode(hpc, explicit=hpc_execution_mode)
        self.llm_factory = llm_factory or (lambda c: _build_llm(c))
        self.data_dir = Path(data_dir or cfg.data_dir)

    @classmethod
    def from_settings(cls, cfg: AiModeConfig, *, hpc=None,
                      llm_factory=None,
                      hpc_execution_mode: str | None = None) -> "Orchestrator":
        if hpc is not None:
            return cls(cfg=cfg, hpc=hpc, llm_factory=llm_factory,
                       hpc_execution_mode=hpc_execution_mode)
        manager = None
        if cfg.ssh_host and cfg.ssh_username:
            from .ssh.connection import SSHManager
            from .ssh.credentials import KeyringCredentialStore
            manager = SSHManager(credentials=KeyringCredentialStore(),
                                 connect_timeout=15,
                                 known_hosts_path=cfg.ssh_known_hosts_path or None)
            manager.switch(host=cfg.ssh_host, username=cfg.ssh_username,
                           port=cfg.ssh_port or 22)
        return cls(cfg=cfg, hpc=manager, llm_factory=llm_factory)

    # ---------------- 基础 ----------------
    def _local_dir(self, project_id: str, task_id: str,
                   local_workspace: str = "") -> Path:
        ws = (local_workspace or "").strip()
        if ws:
            return Path(ws).expanduser().resolve()
        return self.data_dir / "workspace" / f"{project_id}__{task_id}"

    def _save(self, store: ProjectStore, project_id: str, task_id: str,
              flow: dict) -> None:
        flow["updated_at"] = _now_iso()
        store.update_task(project_id, task_id, flow=dict(flow),
                          status=_PHASE_STATUS.get(flow.get("phase"),
                                                   "planned"))

    def _default_goal(self, task: dict) -> str:
        return str(task.get("goal") or "").strip()

    def _llm(self):
        return self.llm_factory(self.cfg)

    def sync_execution_mode(self, store: ProjectStore, project_id: str,
                            task_id: str) -> str:
        """Migrate any stale persisted LLM-derived label to the HPC truth."""
        with _task_lock(project_id, task_id):
            task = store.get_task(project_id, task_id) or {}
            flow = dict(task.get("flow") or {})
            if flow and flow.get("execution_mode") != self.execution_mode:
                flow["execution_mode"] = self.execution_mode
                self._save(store, project_id, task_id, flow)
            return self.execution_mode

    # ---------------- 对话入口 ----------------
    def begin(self, store: ProjectStore, project_id: str, task_id: str,
              requirement: str) -> str:
        with _task_lock(project_id, task_id):
            return self._begin_locked(store, project_id, task_id, requirement)

    def _begin_locked(self, store: ProjectStore, project_id: str, task_id: str,
                      requirement: str) -> str:
        """确认「开始计算流程」：建 flow 并推进到第一个决策点。"""
        task = store.get_task(project_id, task_id) or {}
        goal = (requirement or "").strip() or self._default_goal(task)
        flow = {
            "phase": "running",
            "goal": goal,
            "logs": [],
            "plan": {"strategy": "", "jobs": []},
            "local_dir": str(self._local_dir(
                project_id, task_id, task.get("local_workspace") or "")),
            "hpc_dir": str(task.get("hpc_workspace") or "").strip(),
            "uploaded": False,
            "execution_mode": self.execution_mode,
            "precheck": {"ok": False, "issues": []},
            "draft": [],
            "waiting": [],
            "extractions": {},
            "report": "",
            "started_at": _now_iso(),
        }
        self._save(store, project_id, task_id, flow)
        return self._advance(store, project_id, task_id, flow, goal)

    def handle(self, store: ProjectStore, project_id: str, task_id: str,
               content: str) -> str:
        with _task_lock(project_id, task_id):
            return self._handle_locked(store, project_id, task_id, content)

    def _handle_locked(self, store: ProjectStore, project_id: str, task_id: str,
                       content: str) -> str:
        """已有 flow 的用户消息：按当前 phase 推进。"""
        task = store.get_task(project_id, task_id) or {}
        flow = dict(task.get("flow") or {})
        if flow and flow.get("execution_mode") != self.execution_mode:
            flow["execution_mode"] = self.execution_mode
            self._save(store, project_id, task_id, flow)
        # M44：任务设了 local_workspace 时，计算目录必须指向该工作区；
        # 历史 flow 若仍指向私有草稿目录，先自愈再继续，避免预检/提交走错目录。
        ws = (task.get("local_workspace") or "").strip()
        if ws and flow:
            want = str(Path(ws).expanduser().resolve())
            if flow.get("local_dir") != want:
                flow = dict(flow)
                flow["local_dir"] = want
                store.update_task(project_id, task_id, flow=dict(flow))
        phase = flow.get("phase")
        if phase in ("done", "blocked"):
            if self._likely_new_requirement(content):
                return self.begin(store, project_id, task_id, content)
            return self._idle_text(flow)
        if phase == "await_submit":
            return self._on_await_submit(store, project_id, task_id, flow,
                                         content)
        if phase == "running":
            return self._advance(store, project_id, task_id, flow,
                                 flow.get("goal") or "")
        if phase == "monitoring":
            return self._pump(store, project_id, task_id, flow)
        return "智能模式执行态尚未启动。直接回复你的计算需求即可开工。"

    # ---------------- 推进器（规划→准备→搭建→预检→草稿） ----------------
    def _advance(self, store, project_id, task_id, flow, goal: str) -> str:
        logs = [f"已记录需求「{goal[:100]}」。现在开始推进真实工序："
                "规划作业 → 准备输入 → 搭建超算目录 → 提交前检查 → "
                "生成提交草稿；每一步都会如实汇报，最终提交必须经你确认。"]
        self._plan(flow, goal)
        logs.append(flow["plan"]["strategy"])
        self._prepare(store, project_id, task_id, flow, logs)
        self._setup(flow, logs)
        local_dir = Path(flow["local_dir"])
        remote_ok = self.hpc is not None and bool(flow.get("hpc_dir"))
        self._precheck(flow, local_dir, remote_ok, flow.get("hpc_dir") or "",
                       logs)
        flow["logs"] = logs

        if not flow["precheck"]["ok"]:
            flow["phase"] = "blocked"
            self._save(store, project_id, task_id, flow)
            issues = "\n".join(f"- [{i['level']}] {i['job']}: {i['message']}"
                               for i in flow["precheck"]["issues"])
            return "\n".join(logs) + "\n\n提交前检查未通过，暂不生成提交草稿：\n" \
                + issues + "\n请补齐上述文件后，重新生成草稿与提交确认卡。"

        try:
            draft_text = self._draft(flow)
        except RuntimeError as exc:
            flow["phase"] = "blocked"
            self._save(store, project_id, task_id, flow)
            return ("\n".join(logs) + "\n\n生成提交草稿失败：\n- " + str(exc)
                    + "\n请把对应的唯一提交脚本（*.sh）放进该作业目录后，"
                      "重新生成草稿与提交确认卡。")
        flow["phase"] = "await_submit"
        self._save(store, project_id, task_id, flow)
        return "\n".join(logs) + "\n\n" + draft_text + \
            "\n系统会展示一次性确认卡；只有卡片批准后才会真实提交到超算。"

    def _plan(self, flow: dict, goal: str) -> None:
        label = _detect_job_label(goal)
        jobs = [{
            "key": _label_slug(label),
            "label": label,
            "requires": [],
            "status": "draft",
            "slurm_id": None,
            "description": (goal or "")[:200],
        }]
        flow["plan"] = {
            "strategy": f"规划：单条作业「{label}」；按固定 8 步工序推进，"
                        f"在材料结构上运行 VASP。",
            "jobs": jobs,
        }

    def _prepare(self, store, project_id, task_id, flow, logs: list[str]) -> None:
        """Inventory only. File copies and uploads require separate actions."""
        local_dir = Path(flow["local_dir"])
        task = store.get_task(project_id, task_id) or {}
        source = (task.get("local_workspace") or "").strip()
        if source and Path(source).is_dir():
            names = [name for name in ("INCAR", "POSCAR", "KPOINTS", "POTCAR")
                     if (Path(source) / name).is_file()]
            logs.append("已盘点用户工作区输入：" + ("、".join(names) or "（无）"))
        else:
            logs.append("本地工作区无效或未设置；未执行任何文件创建/复制。")
        logs.append("文件复制与 SFTP 上传均需逐次确认；本阶段未写入或上传。")

    def _upload_dir(self, local_dir: Path, remote: str,
                    job_keys: list[str] | None = None) -> tuple[bool, str]:
        del local_dir, remote, job_keys
        return False, "P0 禁止编排器隐式上传；请逐个确认绑定的 artifact 上传动作。"

    def _upload_subdir(self, local: Path, remote: str) -> None:
        del local, remote
        raise RuntimeError("P0 禁止编排器隐式递归上传")

    def _setup(self, flow: dict, logs: list[str]) -> None:
        del flow
        logs.append("P0 不执行远端探测命令，也不自动生成 KPOINTS/POTCAR。")

    def _precheck(self, flow: dict, local_dir: Path, remote_ok: bool,
                  remote: str, logs: list[str]) -> None:
        issues: list[dict] = []
        input_records: list[dict] = []
        script_records: list[dict] = []
        remote = (remote or "").rstrip("/")
        flow["execution_mode"] = self.execution_mode
        for job in flow["plan"]["jobs"]:
            if job.get("status") in ("completed", "failed", "not_converged",
                                      "canceled", "skipped", "blocked",
                                      "unknown"):
                continue
            for name in ("INCAR", "POSCAR", "KPOINTS", "POTCAR"):
                try:
                    if remote_ok and self.hpc is not None:
                        calc = self._job_calc_dir(remote, local_dir, job["key"])
                        path = f"{calc.rstrip('/')}/{name}"
                        fingerprint = input_fingerprint_remote(self.hpc, path)
                        source = "remote"
                    else:
                        root = local_dir.resolve()
                        base = self._contained_job_dir(root, job["key"]) or root
                        target = (base / name).resolve()
                        target.relative_to(root)
                        fingerprint = input_fingerprint_local(target)
                        source = "local"
                    input_records.append({"job_key": job["key"], "name": name,
                                          "source": source, **fingerprint})
                    level = "ok"
                    msg = f"{name} 非空且 SHA-256 已绑定"
                except Exception:  # noqa: BLE001
                    level = "error"
                    msg = f"{name} 缺失、为空或无法哈希，无法提交"
                issues.append({"job": job["key"], "file": name, "level": level,
                               "message": msg})
            calc = self._job_calc_dir(remote, local_dir, job["key"])
            has_script = False
            actual: dict | None = None
            if remote_ok and calc:
                try:
                    script_name = find_remote_submit_script(self.hpc, calc)
                    has_script = bool(script_name)
                    if script_name:
                        actual = {"source": "remote", "script_name": script_name,
                                  **fingerprint_remote_submit_script(
                                      self.hpc, calc, script_name)}
                except RuntimeError:
                    has_script = False
            else:
                has_script = self._user_script_exists(local_dir, job["key"])
                if has_script:
                    job_local = self._contained_job_dir(local_dir, job["key"]) or local_dir.resolve()
                    script = resolve_user_submit_script(job_local)
                    actual = {"source": "local", "script_name": script.name,
                              **fingerprint_local_submit_script(script)}
            level = "ok" if has_script else "error"
            msg = "提交脚本(*.sh) 存在" if has_script else (
                "提交脚本(*.sh) 缺失，无法提交（提交脚本必须由用户提供，"
                "系统不代写生成脚本）")
            issues.append({"job": job["key"], "file": "提交脚本(*.sh)",
                           "level": level, "message": msg})
            attestation = (flow.get("script_attestations") or {}).get(job["key"])
            attested = (isinstance(attestation, dict) and isinstance(actual, dict)
                        and all(attestation.get(key) == actual.get(key)
                                for key in ("source", "script_name",
                                            "normalized_path", "sha256", "size")))
            issues.append({"job": job["key"], "file": "提交脚本认领",
                           "level": "ok" if attested else "error",
                           "message": ("脚本已由用户认领" if attested else
                                       "提交脚本尚未显式认领并绑定 SHA-256")})
            if attested:
                script_records.append({"job_key": job["key"], **actual})
        snapshot, digest = precheck_snapshot(
            execution_mode=self.execution_mode, inputs=input_records,
            scripts=script_records)
        flow["precheck"] = {
            "ok": all(i["level"] == "ok" for i in issues),
            "hard": True,
            "issues": issues,
            "execution_mode": self.execution_mode,
            "snapshot": snapshot,
            "digest": digest,
        }

    def _file_exists(self, local_dir: Path, remote_ok: bool, remote: str,
                     name: str, *, job_key: str = "") -> bool:
        remote = (remote or "").rstrip("/")
        if remote_ok and self.hpc is not None:
            calc = self._job_calc_dir(remote, local_dir, job_key)
            try:
                info = self.hpc.stat(f"{calc.rstrip('/')}/{name}")
                return info is not None and info.get("is_dir") is not True
            except Exception:  # noqa: BLE001
                return False
        root = local_dir.resolve()
        base = self._contained_job_dir(root, job_key) or root
        target = (base / name).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return False
        return target.is_file()

    @staticmethod
    def _contained_job_dir(local_dir: Path, job_key: str) -> Path | None:
        root = local_dir.resolve()
        candidate = (root / str(job_key or "")).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate if job_key and candidate.is_dir() else None

    def _user_script_exists(self, local_dir: Path, job_key: str) -> bool:
        """作业目录是否存在用户提供的唯一提交脚本（*.sh）。"""
        job_local = self._contained_job_dir(local_dir, job_key) or local_dir.resolve()
        try:
            resolve_user_submit_script(job_local)
            return True
        except RuntimeError:
            return False

    def _draft(self, flow: dict) -> str:
        remote = (flow.get("hpc_dir") or "").rstrip("/")
        base = remote or flow.get("local_dir") or ""
        local_dir = Path(flow["local_dir"])
        drafts: list[dict] = []
        lines: list[str] = []
        for job in flow["plan"]["jobs"]:
            if job.get("status") in ("completed", "failed", "not_converged",
                                      "canceled", "skipped", "blocked",
                                      "unknown"):
                continue
            calc_dir = self._job_calc_dir(base, local_dir, job["key"])
            job_local = self._contained_job_dir(local_dir, job["key"]) or local_dir.resolve()
            source = "local"
            script_name = ""
            if remote and self.hpc is not None:
                script_name = find_remote_submit_script(self.hpc, calc_dir) or ""
                source = "remote"
            if source == "remote":
                if not script_name:
                    raise RuntimeError(f"{job['key']} 远端提交脚本缺失")
                fingerprint = fingerprint_remote_submit_script(
                    self.hpc, calc_dir, script_name)
            else:
                script = resolve_user_submit_script(job_local)
                script_name = script.name
                fingerprint = fingerprint_local_submit_script(script)
            attestation = (flow.get("script_attestations") or {}).get(job["key"])
            if not isinstance(attestation, dict) or any(
                    attestation.get(key) != value for key, value in {
                        "source": source, "script_name": script_name,
                        "normalized_path": fingerprint["normalized_path"],
                        "sha256": fingerprint["sha256"], "size": fingerprint["size"],
                    }.items()):
                raise RuntimeError(f"{job['key']} 提交脚本未认领或认领已失效")
            drafts.append({
                "job_key": job["key"],
                "dir": calc_dir,
                "script_name": script_name,
                "script_source": source,
                "script_path": fingerprint["normalized_path"],
                "script_sha256": fingerprint["sha256"],
                "script_size": fingerprint["size"],
                "attestation_action_id": attestation.get("action_id"),
                "attestation_binding_hash": attestation.get("binding_hash"),
                "submit_cmd": " ".join(submit_command(script_name)),
            })
            lines.append(
                f"- {job['key']}（{job['label']}）→ 目录 `{calc_dir}`，"
                f"使用用户认领脚本 {script_name}（SHA-256 {fingerprint['sha256']}）")
        flow["draft"] = drafts
        return ("已生成提交草稿（使用用户提供的提交脚本，只校验、未提交）：\n"
                + "\n".join(lines))

    # ---------------- 用户决策 ----------------
    def _on_await_submit(self, store, project_id, task_id, flow,
                         content: str) -> str:
        if _is_cancel(content):
            flow["phase"] = "blocked"
            self._save(store, project_id, task_id, flow)
            return ("已取消本次提交。草稿保留在任务中。"
                    "可直接回复新的计算需求重新规划。")
        if _is_true_answer(content):
            return self._submit(store, project_id, task_id, flow)
        return ("当前处于「提交前检查通过，待你确认提交」环节。\n"
                "请在绑定当前草稿的一次性确认卡中确认；「取消」→ 放弃本次；"
                "也可以补充输入文件后再回来确认。")

    def _cascade_blocks(self, flow: dict) -> list[str]:
        """M52：前置终态失败/已阻断的等待作业级联置 blocked（移出等待队列）。"""
        jobs = ((flow.get("plan") or {}).get("jobs")) or []
        statuses = {j["key"]: (j.get("status") or "draft") for j in jobs}
        notes: list[str] = []
        changed = True
        while changed:
            changed = False
            for j in jobs:
                if j.get("status") not in {"draft", "waiting"}:
                    continue
                bad = [r for r in (j.get("requires") or [])
                       if statuses.get(r) in ("failed", "not_converged",
                                              "canceled", "blocked")]
                if not bad:
                    continue
                j["status"] = "blocked"
                j["wait_reason"] = (f"前置 {'、'.join(bad)} 失败或已阻断，"
                                    "禁止提交")
                if j["key"] in (flow.get("waiting") or []):
                    flow["waiting"].remove(j["key"])
                statuses[j["key"]] = "blocked"
                notes.append(f"{j['key']}（前置 {'、'.join(bad)} 失败）")
                changed = True
        return notes

    def _plan_snapshot(self, flow: dict) -> PlanSnapshot:
        """把 flow.plan.jobs 转成依赖闸门可评估的 PlanSnapshot（M52）。"""
        jobs = ((flow.get("plan") or {}).get("jobs")) or []
        return PlanSnapshot(steps=[
            PlanStep(job_key=j["key"], label=j.get("label") or j["key"],
                     requires=[str(r) for r in (j.get("requires") or [])])
            for j in jobs])

    def _gate(self, flow: dict):
        """依赖闸门：waiting 作业按待提交（draft）评估，返回 GateResult。"""
        statuses = {}
        for j in ((flow.get("plan") or {}).get("jobs")) or []:
            st = j.get("status") or "draft"
            statuses[j["key"]] = "draft" if st == "waiting" else st
        return gate_jobs(self._plan_snapshot(flow), statuses)

    def _submit(self, store, project_id, task_id, flow) -> str:
        """M55：与 _pump 同一把 per-task 锁，提交与监控不并发。"""
        del flow
        with _task_lock(project_id, task_id):
            current = (store.get_task(project_id, task_id) or {}).get("flow") or {}
            return self._submit_locked(store, project_id, task_id,
                                       dict(current))

    def _submit_locked(self, store, project_id, task_id, flow) -> str:
        if (flow.get("execution_mode") != self.execution_mode
                or self.execution_mode == "None" or self.hpc is None):
            return ("[AI_HPC_BACKEND_UNAVAILABLE] 当前流程没有与确认绑定的可用 "
                    "HPC 执行后端；sbatch 次数为 0。")
        remote = str(flow.get("hpc_dir") or flow.get("local_dir") or "").strip()
        drafts = flow.get("draft") or []
        precheck_digest = str((flow.get("precheck") or {}).get("digest") or "")
        executing_action = next((
            action for action in ((flow.get("consent") or {}).get("actions") or {}).values()
            if action.get("kind") == "submit"
            and action.get("state") == "executing"
            and isinstance(action.get("binding"), dict)
            and action["binding"].get("operation") == "submit"
            and action["binding"].get("project_id") == project_id
            and action["binding"].get("task_id") == task_id
            and action["binding"].get("execution_mode") == self.execution_mode
            and action["binding"].get("precheck_digest") == precheck_digest
            and action["binding"].get("remote_root") == remote
            and action["binding"].get("drafts") == drafts
            and action.get("binding_hash") == hashlib.sha256(json.dumps(
                action["binding"], ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
        ), None)
        if executing_action is None:
            return ("[AI_SUBMIT_CONFIRMATION_REQUIRED] 缺少与当前草稿和目标绑定的"
                    "单次提交确认；sbatch 次数为 0。")
        if self.hpc is None:
            return ("未配置/未连接 SSH，无法真实提交到超算（我不会伪造作业号）。"
                    "草稿已保留。请在「设置 → SSH」填写主机/用户名/密码后，"
                    "重新生成并批准提交确认卡；或回复「取消」。\n"
                    "本次没有真正执行任何 sbatch。")
        if not remote:
            flow["phase"] = "blocked"
            self._save(store, project_id, task_id, flow)
            return "任务未填写超算工作区（会话目录），无法定位提交目录。" \
                   "请补充后重新发起。"
        local_dir = Path(flow["local_dir"])
        self._precheck(flow, local_dir, True, remote, [])
        if not flow.get("precheck", {}).get("ok"):
            flow["phase"] = "blocked"
            self._save(store, project_id, task_id, flow)
            return ("[AI_PRECHECK_BLOCKED] 提交前硬检查未通过；缺少任一 "
                    "INCAR/POSCAR/KPOINTS/POTCAR/认领脚本时 sbatch 次数为 0。")
        if str(flow["precheck"].get("digest") or "") != precheck_digest:
            flow["phase"] = "blocked"
            self._save(store, project_id, task_id, flow)
            return ("[AI_PRECHECK_STALE] VASP 输入或脚本在确认后发生变化；"
                    "必须重新预检并确认，sbatch 次数为 0。")
        if not flow.get("draft"):
            return "[AI_PRECHECK_BLOCKED] 缺少绑定脚本哈希的提交草稿；sbatch 次数为 0。"
        logs_note = "已通过远端硬预检；不会在提交阶段隐式上传或改写文件。"
        account = self.cfg.ssh_username
        free = self._free_slots(account)
        if free is None:
            return logs_note + "\n无法查询超算配额（squeue 失败），" \
                "为避免超限未提交。请检查 SSH 后重试。"
        if free <= 0:
            return logs_note + f"\n超算账号「排队+运行中」已达上限（空位 {free}），" \
                "本次未提交。空位恢复后必须重新预检并由用户再次确认。"
        submitted = []
        gate = self._gate(flow)
        flow["waiting"] = []
        for job in flow["plan"]["jobs"]:
            key = job["key"]
            st = job.get("status")
            if st in ("completed", "failed", "not_converged", "canceled",
                      "skipped", "unknown"):
                continue
            if job.get("submission_state") == "executing":
                job["submission_state"] = "unknown"
                job["status"] = "unknown"
                job["submission_error"] = "检测到中断的提交尝试；结果未知"
                submitted.append(f"- {key} 上次提交尝试中断，已标记 unknown；不会重试")
                continue
            if job.get("submission_state") in {"submitted", "unknown"}:
                submitted.append(f"- {key} 已有提交尝试状态 {job['submission_state']}，不会重试")
                continue
            if key not in gate.eligible:
                if st in ("submitted", "queued", "running"):
                    continue
                reason = gate.blocked.get(key) or "等待空位"
                job["wait_reason"] = reason
                submitted.append(f"- {key} 未提交：{reason}；依赖满足后需重新确认")
                continue
            try:
                calc, script_name = self._verify_submit_target(
                    flow, remote, local_dir, job)
            except Exception as exc:  # noqa: BLE001
                submitted.append(f"- {job['key']} 预提交校验失败：{exc}（sbatch=0）")
                continue
            job["submission_state"] = "executing"
            job["submission_action_id"] = executing_action["action_id"]
            self._save(store, project_id, task_id, flow)
            try:
                slurm_id = self._submit_one(calc, script_name)
            except Exception as exc:  # noqa: BLE001
                job["submission_state"] = "unknown"
                job["status"] = "unknown"
                job["submission_error"] = str(exc)[:500]
                self._save(store, project_id, task_id, flow)
                submitted.append(f"- {job['key']} 提交结果不确定：{exc}；不会自动重试")
                continue
            job["slurm_id"] = slurm_id
            job["status"] = "submitted"
            job["submission_state"] = "submitted"
            self._save(store, project_id, task_id, flow)
            submitted.append(f"- {job['key']} 已提交：slurm id {slurm_id} "
                             f"（目录 `{calc}`）")
        if any(j.get("submission_state") == "unknown"
               for j in flow["plan"]["jobs"]):
            flow["phase"] = "blocked"
        elif any(j.get("status") in ("draft", "waiting")
                 for j in flow["plan"]["jobs"]):
            # A dependency or capacity transition never inherits an earlier
            # approval.  Keep an explicit confirmation boundary available.
            flow["phase"] = "await_submit"
        else:
            flow["phase"] = "monitoring"
        self._save(store, project_id, task_id, flow)
        out = logs_note + "\n" + "\n".join(submitted)
        return out + "\n在途作业会随后续消息刷新（squeue 实况）。"

    def _free_slots(self, account: str) -> Optional[int]:
        try:
            code, out, _ = self.hpc.run(self._squeue_command(account))
            if code != 0:
                return None
            pending, running = parse_slurm_output(out or "")
            return max(0, self.cfg.max_jobs - pending - running)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _squeue_command(account: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", str(account or "")):
            raise ValueError("invalid scheduler account")
        return f"squeue -u {account}"

    def _job_calc_dir(self, base: str, local_dir: Path, key: str) -> str:
        """作业计算目录：本地/远端已存在 <base>/<key> 子目录时用该子目录，
        否则退回 base（保持旧版「扁平工作区」行为）。"""
        per_job = self._contained_job_dir(Path(local_dir), key) is not None
        if not per_job and self.hpc is not None and base:
            try:
                info = self.hpc.stat(f"{base.rstrip('/')}/{key}")
                per_job = (info is not None and info.get("is_file") is not True)
            except Exception:  # noqa: BLE001
                per_job = False
        return f"{base.rstrip('/')}/{key}" if per_job else (base or "")

    def _verify_submit_target(self, flow: dict, remote: str, local_dir: Path,
                              job: dict) -> tuple[str, str]:
        calc = self._job_calc_dir(remote, local_dir, job["key"])
        script_name = find_remote_submit_script(self.hpc, calc)
        if not script_name:
            raise RuntimeError("远端作业目录缺少唯一用户脚本")
        fingerprint = fingerprint_remote_submit_script(self.hpc, calc, script_name)
        attestation = (flow.get("script_attestations") or {}).get(job["key"])
        draft = next((d for d in flow.get("draft") or []
                      if d.get("job_key") == job["key"]), None)
        if not isinstance(attestation, dict) or not isinstance(draft, dict):
            raise RuntimeError("脚本认领或提交草稿缺失")
        expected = {
            "script_name": script_name,
            "normalized_path": fingerprint["normalized_path"],
            "sha256": fingerprint["sha256"], "size": fingerprint["size"],
        }
        if any(attestation.get(key) != value for key, value in expected.items()):
            raise RuntimeError("远端脚本与认领哈希不一致")
        if (draft.get("script_sha256") != fingerprint["sha256"]
                or draft.get("script_size") != fingerprint["size"]
                or draft.get("script_path") != fingerprint["normalized_path"]):
            raise RuntimeError("远端脚本与草稿绑定不一致")
        return calc, script_name

    def _submit_one(self, calc: str, script_name: str) -> int:
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\.sh", script_name):
            raise RuntimeError("非法提交脚本名")
        code, out, err = self.hpc.run(f"sbatch {script_name}", cwd=calc)
        match = re.search(r"Submitted batch job (\d+)", out or "")
        if match:
            return int(match.group(1))
        raise RuntimeError(
            f"sbatch 未返回作业号: exit={code} out={(out or '')[:80]} "
            f"err={(err or '')[:80]}")

    # ---------------- 监控 + 收尾 ----------------
    # ---------------- 公共封装（供 agent 的 monitor/report 工具调用真实原语） ----------------
    def monitor(self, store: ProjectStore, project_id: str, task_id: str,
                flow: dict) -> str:
        """查询作业进度并按真实 squeue 状态推进（agent monitor 工具用）。"""
        if not flow:
            flow = (store.get_task(project_id, task_id) or {}).get("flow") or {}
        return self._pump(store, project_id, task_id, flow)

    def stop_monitor(self, store: ProjectStore, project_id: str,
                     task_id: str, flow: dict) -> str:
        """M56：用户终止当前计算流程（agent stop_monitor 工具用）。

        全部未终态作业置 canceled、等待队列清空、phase=done——后台监控
        下轮扫不到本任务。已在超算上运行的作业无法
        从本地真正取消，回执中给出 scancel 建议由用户决定。"""
        del flow
        with _task_lock(project_id, task_id):
            current = (store.get_task(project_id, task_id) or {}).get("flow") or {}
            return self._stop_monitor_locked(store, project_id, task_id,
                                             dict(current))

    def _stop_monitor_locked(self, store, project_id, task_id, flow) -> str:
        if not flow:
            flow = dict((store.get_task(project_id, task_id) or {})
                        .get("flow") or {})
        if not flow:
            return "当前任务没有进行中的计算流程，无需终止。"
        jobs = ((flow.get("plan") or {}).get("jobs")) or []
        live = {"draft", "waiting", "submitted", "queued", "running",
                "not_converged"}
        stopped: list[str] = []
        on_hpc: list[str] = []
        for j in jobs:
            if not isinstance(j, dict) or j.get("status") not in live:
                continue
            key = str(j.get("key") or "")
            stopped.append(f"{key}（原状态 {j.get('status')}）")
            sid = j.get("slurm_id")
            if sid:
                on_hpc.append(f"{sid}（{key}）")
            j["status"] = "canceled"
            j["stop_note"] = "用户终止"
        flow["waiting"] = []
        flow["phase"] = "done"
        self._save(store, project_id, task_id, flow)
        out = "已终止本次计算流程："
        out += ("、".join(stopped) if stopped else "没有未完成的作业") \
            + "。\n后台监控已停止；系统不会自动提交任何作业。"
        if on_hpc:
            out += ("\n注意：以下作业已提交到超算，本地仅标记取消，超算上可能"
                    "仍在运行（会继续占额度）。如需停止请在超算执行：\n"
                    + "\n".join(f"scancel {s.split('（')[0]}" for s in on_hpc)
                    + "\n（scancel 属高风险命令，AI 模式不会代执行。）")
        return out

    def finalize_report(self, store: ProjectStore, project_id: str,
                        task_id: str, flow: dict) -> str:
        """作业全部终态后渲染真实结果报告（agent report 工具用）。"""
        if not flow:
            flow = (store.get_task(project_id, task_id) or {}).get("flow") or {}
        return self._render_report(store, project_id, task_id, flow)

    def _pump(self, store, project_id, task_id, flow) -> str:
        """M55：per-task 互斥——用户消息触发与后台监控线程不并发推进，
        防止同一作业被双补提/状态互相覆盖。"""
        del flow
        with _task_lock(project_id, task_id):
            current = (store.get_task(project_id, task_id) or {}).get("flow") or {}
            return self._pump_locked(store, project_id, task_id,
                                     dict(current))

    def _pump_locked(self, store, project_id, task_id, flow) -> str:
        if self.hpc is None:
            return ("未连接超算，无法查询作业进度。作业在超算上照常运行；"
                    "配置 SSH 后回到本会话即可看到实况与报告。")
        account = self.cfg.ssh_username
        try:
            code, out, _ = self.hpc.run(self._squeue_command(account))
        except Exception as exc:  # noqa: BLE001
            return f"查询 squeue 失败（{type(exc).__name__}），进度未知。"
        states = _remote_state_map(out or "")
        pending, running = parse_slurm_output(out or "")
        free = max(0, self.cfg.max_jobs - pending - running)

        progress: list[str] = []
        for job in flow["plan"]["jobs"]:
            status = job.get("status")
            if status == "waiting":
                progress.append(
                    f"{job['key']}：{job.get('wait_reason') or '等待空位'}")
                continue
            if status not in ("submitted", "queued", "running"):
                progress.append(f"{job['key']}：{status}")
                continue
            sid = str(job.get("slurm_id") or "")
            token = states.get(sid)
            if token in ("PD", "PENDING"):
                job["status"] = "queued"
                progress.append(f"{job['key']}：排队中（{sid}）")
            elif token in ("R", "RUN", "RUNNING"):
                job["status"] = "running"
                progress.append(f"{job['key']}：运行中（{sid}）")
            else:
                result = self._finalize_job(flow, job)
                progress.append(f"{job['key']}：{result}")

        # P0: monitoring is read-only with respect to submission. Dependency
        # completion never authorizes a later sbatch.
        stalled = self._cascade_blocks(flow)
        if flow.get("waiting"):
            progress.insert(0, "等待作业不会自动补提；条件满足后需重新预检并逐次确认")
        for note in stalled:
            progress.append("已停止等待：" + note)
        self._save(store, project_id, task_id, flow)

        all_done = all(
            j.get("status") in ("completed", "failed", "not_converged",
                                "canceled", "not_found", "skipped", "blocked")
            for j in flow["plan"]["jobs"])
        if all_done:
            flow["phase"] = "done"
            self._cleanup_temp_logs(flow)
            report = self._render_report(store, project_id, task_id, flow)
            flow["report"] = report
            self._save(store, project_id, task_id, flow)
            return "\n".join(progress) + "\n\n" + report
        suffix = ""
        if flow.get("waiting"):
            suffix = f"\n（超算空位 {free}；等待队列 {len(flow['waiting'])}）"
        return "\n".join(progress) + suffix

    def _cleanup_temp_logs(self, flow: dict) -> None:
        """Automatic cleanup is disabled because remote deletes require consent."""
        del flow

    def _finalize_job(self, flow: dict, job: dict) -> str:
        remote = flow.get("hpc_dir") or ""
        # M52：按作业定位计算目录（嵌套 key 如 relax/static 也能读到自己的 OUTCAR）
        local_dir = (Path(flow["local_dir"])
                     if flow.get("local_dir") else None)
        base = (self._job_calc_dir(remote, local_dir, job["key"]).rstrip("/")
                if remote else "")
        try:
            outcar = self.hpc.read_file(f"{base}/OUTCAR")
            oszi = self.hpc.read_file(f"{base}/OSZICAR")
        except Exception:  # noqa: BLE001
            job["status"] = "failed"
            return "失败（结果文件不可读，作业可能失败/被取消）"
        text_out = bytes(outcar).decode("utf-8", "replace")
        text_os = bytes(oszi).decode("utf-8", "replace")
        extraction = summarize_run(text_out, text_os)
        flow["extractions"][job["key"]] = extraction
        oc = extraction.get("outcar") or {}
        if oc.get("unrecoverable_error"):
            job["status"] = "failed"
            return "失败（OUTCAR 含 Unrecoverable error）"
        if oc.get("converged"):
            job["status"] = "completed"
            return "已完成（已收敛）"
        if oc.get("n_ionic_steps", 0) > 0:
            job["status"] = "completed"
            return "已完成"
        job["status"] = "failed"
        return "完成但 OUTCAR 中未提取到有效结果"

    def _render_report(self, store: ProjectStore, project_id: str,
                       task_id: str, flow: dict) -> str:
        task_d = store.get_task(project_id, task_id) or {}
        session = Session(
            session_id=task_id,
            project_id=project_id,
            title=str(task_d.get("title") or task_id),
            calc_dir=flow.get("hpc_dir") or flow.get("local_dir") or "",
            local_workspace=str(task_d.get("local_workspace") or ""),
            start_step="understand",
            end_step="report",
            current_step="report",
            duration="full",
            requirement=RequirementSnapshot(raw_goal=flow.get("goal") or ""),
        )
        for job in flow.get("plan", {}).get("jobs", []):
            entry = JobEntry(
                job_key=job["key"],
                description=job.get("description") or "",
                status=_status_to_session(job.get("status") or ""),
                slurm_job_id=(str(job.get("slurm_id"))
                              if job.get("slurm_id") else None),
                step="submit_monitor",
            )
            session.jobs.append(entry)
        report = render_report(session,
                               extractions=flow.get("extractions") or {},
                               refine=self._refine)
        return report.markdown


    def _refine(self, items) -> str:
        llm = self._llm()
        if llm is None:
            return ""
        rows = [f"- {item.job.job_key}: {item.keyword_text}"
                for item in items if item.keyword_text]
        if not rows:
            return ""
        prompt = ("根据以下 VASP 作业提取结果，用一段中文总结本次计算结论：\n"
                  + "\n".join(rows))
        try:
            result = llm.complete([{"role": "user", "content": prompt}],
                                  max_tokens=400)
            return (result.text or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    def _likely_new_requirement(self, content: str) -> bool:
        text = (content or "").strip().lower()
        if not text:
            return False
        keywords = ("计算", "能带", "band", "态密度", "dos",
                    "states", "声子", "phonon", "结构优化", "弛豫",
                    "几何优化", "aimd", "分子动力学", "scf", "自洽",
                    "静态计算", "收敛测试", "poscar", "incar",
                    "kpoints", "k点", "吸附", "缺陷", "表面",
                    "磁性", "投影", "parchg", "chgcar")
        return any(k in text for k in keywords)

    def _idle_text(self, flow: dict) -> str:
        if flow.get("report"):
            return "本次计算已完成，报告如下。\n\n" + flow["report"] + \
                "\n回复新的计算需求即可开始下一轮。"
        return "本次流程已停止（未生成报告）。回复新的计算需求即可重新发起。"
