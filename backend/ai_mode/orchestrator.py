# -*- coding: utf-8 -*-
"""中枢执行态推进器（M14）：把聊天端点接上「真实 8 步工序」的执行链路。

对齐 WORKFLOW v14 §2/§9 与 MODULE_INTERFACES §1.1（中枢 Orchestrator）：
- 每一步都做真实操作（本地文件/命令、LLM、SSH/SFTP/sbatch/squeue、
  OUTCAR 提取、报告渲染），不注入任何演示文案。
- 未配置/未连接 SSH 时如实降级：能做的做（理解、规划、本地准备、预检、
  草稿），需要超算的操作明确说明「未连接超算，未执行」，绝不假造作业号。
- 提交作业必须等用户确认（产品红线），确认后才真实 sbatch；在途作业按
  轮询查询 squeue 推进状态；终态后真实提取结果并由 LLM 提炼报告。
- 全局配额（§9）：提交前实时 squeue 查账号「排队+运行中」，无空位进
  「等待空位」本地队列，空位出现时自动补提。
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .agent.tools import _label_slug
from .config import AiModeConfig
from .jobs.scheduler import parse_slurm_output
from .projects import ProjectStore
from .report.extract import summarize_run
from .report.render import render_report
from .schemas import JobEntry, JobStatus as SessionJobStatus, \
    PlanSnapshot, PlanStep, RequirementSnapshot, Session
from .tools.draft import (find_remote_submit_script,
                          resolve_user_submit_script, submit_command)
from .tools.vaspkit import probe_and_store
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


_TASK_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_TASK_LOCKS_GUARD = threading.Lock()


def _task_lock(project_id: str, task_id: str) -> threading.Lock:
    """M55：每任务一把互斥锁，串行化用户消息与后台监控线程的推进。"""
    with _TASK_LOCKS_GUARD:
        return _TASK_LOCKS.setdefault((project_id, task_id), threading.Lock())


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
                 data_dir: Optional[Path] = None):
        self.cfg = cfg
        self.hpc = hpc
        self.llm_factory = llm_factory or (lambda c: _build_llm(c))
        self.data_dir = Path(data_dir or cfg.data_dir)

    @classmethod
    def from_settings(cls, cfg: AiModeConfig, *, hpc=None,
                      llm_factory=None) -> "Orchestrator":
        if hpc is not None:
            return cls(cfg=cfg, hpc=hpc, llm_factory=llm_factory)
        manager = None
        if cfg.ssh_host and cfg.ssh_username:
            from .ssh.connection import SSHManager
            from .ssh.credentials import KeyringCredentialStore
            manager = SSHManager(credentials=KeyringCredentialStore(),
                                 connect_timeout=15)
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

    # ---------------- 对话入口 ----------------
    def begin(self, store: ProjectStore, project_id: str, task_id: str,
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
        """已有 flow 的用户消息：按当前 phase 推进。"""
        task = store.get_task(project_id, task_id) or {}
        flow = task.get("flow") or {}
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
                + issues + "\n请补齐上述文件后，再回复「确认提交」重试。"

        try:
            draft_text = self._draft(flow)
        except RuntimeError as exc:
            flow["phase"] = "blocked"
            self._save(store, project_id, task_id, flow)
            return ("\n".join(logs) + "\n\n生成提交草稿失败：\n- " + str(exc)
                    + "\n请把对应的唯一提交脚本（*.sh）放进该作业目录后，"
                      "再回复「确认提交」重试。")
        flow["phase"] = "await_submit"
        self._save(store, project_id, task_id, flow)
        return "\n".join(logs) + "\n\n" + draft_text + \
            "\n回复「确认提交」将真实提交到超算（需已配置 SSH）；「取消」放弃本次。"

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
        local_dir = Path(flow["local_dir"])
        local_dir.mkdir(parents=True, exist_ok=True)
        task = store.get_task(project_id, task_id) or {}
        source = (task.get("local_workspace") or "").strip()
        copied: list[str] = []
        same_dir = False
        if source:
            src = Path(source)
            if src.is_dir():
                same_dir = src.expanduser().resolve() == \
                    local_dir.expanduser().resolve()
                for name in ("POSCAR", "INCAR"):
                    fp = src / name
                    if fp.is_file():
                        if same_dir:
                            copied.append(name)
                        else:
                            shutil.copy2(fp, local_dir / name)
                            copied.append(name)
                if copied:
                    logs.append("本地工作区即为计算目录：输入文件直接在该目录中，"
                                "无需复制。")
                elif not copied and not same_dir:
                    logs.append("本地工作区里未找到 POSCAR/INCAR，"
                                "输入文件需稍后补齐。")
            else:
                logs.append(f"本地工作区路径无效: {source}")
        else:
            logs.append("任务未填写本地工作区，拷贝初始文件跳过。")
        if copied and not same_dir:
            logs.append(f"已从本地工作区复制: {', '.join(copied)}。")

        if self.hpc is None:
            logs.append("未连接超算（未配置 SSH 账号），输入文件仅存本地，"
                        "尚未上传到任何超算目录。")
            return
        remote = flow.get("hpc_dir") or ""
        if not remote:
            logs.append("任务未填写超算工作区（会话目录），跳过上传；"
                        "确认提交前请先补充该目录。")
            return
        ok, note = self._upload_dir(
            local_dir, remote,
            job_keys=[j["key"] for j in (flow.get("plan") or {}).get("jobs", [])])
        flow["uploaded"] = ok
        logs.append(note)

    def _upload_dir(self, local_dir: Path, remote: str,
                    job_keys: list[str] | None = None) -> tuple[bool, str]:
        remote = (remote or "").rstrip("/")
        try:
            self.hpc.run(f"mkdir -p {self._shell_quote(remote)}")
            for p in sorted(local_dir.iterdir()):
                if p.is_file():
                    self.hpc.write_file(f"{remote}/{p.name}", p.read_bytes())
                elif p.is_dir() and job_keys and (
                        p.name in job_keys
                        or any(k.startswith(p.name + "/") for k in job_keys)):
                    # M52：嵌套作业 key（relax/static）的首段目录也要整树上传
                    self._upload_subdir(p, f"{remote}/{p.name}")
            return True, f"已上传输入文件到超算目录 `{remote}`。"
        except Exception as exc:  # noqa: BLE001
            return False, f"上传超算失败（未执行提交）：{type(exc).__name__}"

    def _upload_subdir(self, local: Path, remote: str) -> None:
        """递归上传作业目录子树（M52：支持嵌套作业目录 relax/static/dos）。"""
        self.hpc.run(f"mkdir -p {self._shell_quote(remote)}")
        for p in sorted(local.iterdir()):
            if p.is_file():
                self.hpc.write_file(f"{remote.rstrip('/')}/{p.name}",
                                    p.read_bytes())
            elif p.is_dir():
                self._upload_subdir(p, f"{remote.rstrip('/')}/{p.name}")

    def _shell_quote(self, text: str) -> str:
        return "'" + str(text).replace("'", r"'\''") + "'"

    def _setup(self, flow: dict, logs: list[str]) -> None:
        if self.hpc is None:
            logs.append("未连接超算：跳过 vaspkit 探测（连接后会自动探测并固化)。")
            return
        try:
            skill = probe_and_store(self.hpc.run, root=self.data_dir,
                                    timeout=float(self.cfg.llm_timeout_seconds or 30))
            if skill.found:
                ver = f"，{skill.version}" if skill.version else ""
                logs.append(f"vaspkit 已探测（{skill.path}{ver}），技能已固化。")
            else:
                logs.append("vaspkit 未在超算上定位；不会自动补 KPOINTS/POTCAR，"
                            "需手动准备后再提交。")
        except Exception as exc:  # noqa: BLE001
            logs.append(f"vaspkit 探测失败: {type(exc).__name__}")

    def _precheck(self, flow: dict, local_dir: Path, remote_ok: bool,
                  remote: str, logs: list[str]) -> None:
        issues: list[dict] = []
        remote = (remote or "").rstrip("/")
        for job in flow["plan"]["jobs"]:
            for name in ("INCAR", "POSCAR"):
                exists = self._file_exists(local_dir, remote_ok, remote, name,
                                           job_key=job["key"])
                level = "ok" if exists else "error"
                msg = f"{name} 存在" if exists else f"{name} 缺失，无法提交"
                issues.append({"job": job["key"], "file": name, "level": level,
                               "message": msg})
            for name in ("POTCAR", "KPOINTS"):
                exists = self._file_exists(local_dir, remote_ok, remote, name,
                                           job_key=job["key"])
                level = "ok" if exists else "warn"
                msg = f"{name} 存在" if exists else (
                    f"{name} 缺失（vaspkit 可生成，提交前建议补齐）")
                issues.append({"job": job["key"], "file": name, "level": level,
                               "message": msg})
            has_script = self._user_script_exists(local_dir, job["key"])
            level = "ok" if has_script else "error"
            msg = "提交脚本(*.sh) 存在" if has_script else (
                "提交脚本(*.sh) 缺失，无法提交（提交脚本必须由用户提供，"
                "系统不代写生成脚本）")
            issues.append({"job": job["key"], "file": "提交脚本(*.sh)",
                           "level": level, "message": msg})
        flow["precheck"] = {
            "ok": all(i["level"] != "error" for i in issues),
            "issues": issues,
        }

    def _file_exists(self, local_dir: Path, remote_ok: bool, remote: str,
                     name: str, *, job_key: str = "") -> bool:
        remote = (remote or "").rstrip("/")
        if remote_ok and self.hpc is not None:
            candidates = ([f"{remote}/{job_key}/{name}", f"{remote}/{name}"]
                          if job_key else [f"{remote}/{name}"])
            for check in candidates:
                try:
                    if self.hpc.stat(check) is not None:
                        return True
                except Exception:  # noqa: BLE001
                    continue
            return False
        if job_key and (local_dir / job_key).is_dir():
            return (local_dir / job_key / name).is_file()
        return (local_dir / name).is_file()

    def _user_script_exists(self, local_dir: Path, job_key: str) -> bool:
        """作业目录是否存在用户提供的唯一提交脚本（*.sh）。"""
        job_local = ((local_dir / job_key)
                     if (local_dir / job_key).is_dir() else local_dir)
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
            calc_dir = self._job_calc_dir(base, local_dir, job["key"])
            job_local = ((local_dir / job["key"])
                         if (local_dir / job["key"]).is_dir() else local_dir)
            script = resolve_user_submit_script(job_local)
            script_text = script.read_text(encoding="utf-8")
            drafts.append({
                "job_key": job["key"],
                "dir": calc_dir,
                "script_name": script.name,
                "script_text": script_text,
                "submit_cmd": " ".join(submit_command(script.name)),
            })
            lines.append(
                f"- {job['key']}（{job['label']}）→ 目录 `{calc_dir}`，"
                f"使用用户提供的提交脚本 {script.name}\n"
                f"```bash\n{script_text}\n```")
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
                "回复「确认提交」→ 真实提交到超算；「取消」→ 放弃本次；"
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
                if j.get("status") != "waiting":
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
        with _task_lock(project_id, task_id):
            return self._submit_locked(store, project_id, task_id, flow)

    def _submit_locked(self, store, project_id, task_id, flow) -> str:
        if self.hpc is None:
            return ("未配置/未连接 SSH，无法真实提交到超算（我不会伪造作业号）。"
                    "草稿已保留。请在「设置 → SSH」填写主机/用户名/密码后，"
                    "再回复「确认提交」；或回复「取消」。\n"
                    "本次没有真正执行任何 sbatch。")
        remote = flow.get("hpc_dir") or ""
        if not remote:
            flow["phase"] = "blocked"
            self._save(store, project_id, task_id, flow)
            return "任务未填写超算工作区（会话目录），无法定位提交目录。" \
                   "请补充后重新发起。"
        local_dir = Path(flow["local_dir"])
        if not flow.get("uploaded"):
            ok, note = self._upload_dir(
                local_dir, remote,
                job_keys=[j["key"] for j in flow["plan"]["jobs"]])
            flow["uploaded"] = ok
            logs_note = note
        else:
            logs_note = "输入已上传超算。"
        account = self.cfg.ssh_username
        free = self._free_slots(account)
        if free is None:
            return logs_note + "\n无法查询超算配额（squeue 失败），" \
                "为避免超限未提交。请检查 SSH 后重试。"
        if free <= 0:
            selected = [j for j in flow["plan"]["jobs"]
                        if j.get("status") not in
                        ("completed", "failed", "canceled", "skipped")]
            for job in selected:
                job["status"] = "waiting"
            flow["waiting"] = [j["key"] for j in selected]
            flow["phase"] = "monitoring"
            self._save(store, project_id, task_id, flow)
            return logs_note + f"\n超算账号「排队+运行中」已达上限（空位 {free}），" \
                "作业已进入「等待空位」本地队列；我每次收到消息都会按空位自动补提。"
        submitted = []
        wait_notes: list[str] = []
        gate = self._gate(flow)
        waiting_set = set(flow.setdefault("waiting", []))
        for job in flow["plan"]["jobs"]:
            key = job["key"]
            st = job.get("status")
            if st in ("completed", "failed", "not_converged", "canceled",
                      "skipped"):
                continue
            if key not in gate.eligible:
                # 依赖未满足（M52 闸门）：不提交，进等待队列并注明原因
                if st in ("submitted", "queued", "running"):
                    continue
                reason = gate.blocked.get(key) or "等待空位"
                job["status"] = "waiting"
                job["wait_reason"] = reason
                if key not in waiting_set:
                    flow["waiting"].append(key)
                    waiting_set.add(key)
                wait_notes.append(f"- {key}：{reason}")
                continue
            try:
                slurm_id = self._submit_one(remote, job,
                                            local_dir=local_dir)
            except Exception as exc:  # noqa: BLE001
                # M56：失败原因（含 sbatch stderr 摘要）必须完整呈现，
                # 不能只报异常类型让用户误以为已提交
                submitted.append(f"- {job['key']} 提交失败：{exc}")
                continue
            job["slurm_id"] = slurm_id
            job["status"] = "submitted"
            calc = self._job_calc_dir(remote, local_dir, job["key"])
            submitted.append(f"- {job['key']} 已提交：slurm id {slurm_id} "
                             f"（目录 `{calc}`）")
        flow["phase"] = "monitoring"
        self._save(store, project_id, task_id, flow)
        out = logs_note + "\n" + "\n".join(submitted)
        if wait_notes:
            out += ("\n\n依赖闸门（计划内等待，无需处理）：以下作业暂不提交，"
                    "前序完成后自动补提：\n" + "\n".join(wait_notes))
        return out + "\n在途作业会随后续消息刷新（squeue 实况）。"

    def _free_slots(self, account: str) -> Optional[int]:
        try:
            code, out, _ = self.hpc.run(f"squeue -u {account}")
            if code != 0:
                return None
            pending, running = parse_slurm_output(out or "")
            return max(0, self.cfg.max_jobs - pending - running)
        except Exception:  # noqa: BLE001
            return None

    def _job_calc_dir(self, base: str, local_dir: Path, key: str) -> str:
        """作业计算目录：本地/远端已存在 <base>/<key> 子目录时用该子目录，
        否则退回 base（保持旧版「扁平工作区」行为）。"""
        base_dir = Path(local_dir) / key
        per_job = base_dir.is_dir()
        if not per_job and self.hpc is not None and base:
            try:
                per_job = self.hpc.stat(f"{base.rstrip('/')}/{key}") is not None
            except Exception:  # noqa: BLE001
                per_job = False
        return f"{base.rstrip('/')}/{key}" if per_job else (base or "")

    def _submit_one(self, remote: str, job: dict, *,
                    local_dir: Path | None = None) -> int:
        calc = remote
        job_local = None
        if local_dir is not None:
            calc = self._job_calc_dir(remote, local_dir, job["key"])
            job_local = ((local_dir / job["key"])
                         if (local_dir / job["key"]).is_dir() else local_dir)
        # M51 远端优先：超算作业目录已有唯一用户提交脚本则直接用（不覆盖）
        if calc:
            try:
                remote_script = find_remote_submit_script(self.hpc, calc)
            except RuntimeError as exc:
                raise RuntimeError(f"{job.get('key')}: {exc}") from exc
            if remote_script:
                code, out, err = self.hpc.run(f"sbatch {remote_script}",
                                              cwd=calc)
                match = re.search(r"Submitted batch job (\d+)", out or "")
                if match:
                    return int(match.group(1))
                raise RuntimeError(
                    f"sbatch 未返回作业号: exit={code} out={(out or '')[:80]} "
                    f"err={(err or '')[:80]}")
        if job_local is None:
            raise RuntimeError("缺少本地作业目录，且超算作业目录里没有提交脚本；"
                               "无法定位用户提供的提交脚本")
        script = resolve_user_submit_script(job_local)
        if calc:
            self.hpc.run(f"mkdir -p {self._shell_quote(calc)}")
        self.hpc.write_file(f"{calc.rstrip('/')}/{script.name}",
                            script.read_bytes())
        code, out, err = self.hpc.run(f"sbatch {script.name}", cwd=calc)
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
        下轮扫不到本任务，自动补提随即停止。已在超算上运行的作业无法
        从本地真正取消，回执中给出 scancel 建议由用户决定。"""
        with _task_lock(project_id, task_id):
            return self._stop_monitor_locked(store, project_id, task_id, flow)

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
            + "。\n后台监控与自动补提已停止，不会再提交任何作业。"
        if on_hpc:
            out += ("\n注意：以下作业已提交到超算，本地仅标记取消，超算上可能"
                    "仍在运行（会继续占额度）。如需停止请在超算执行：\n"
                    + "\n".join(f"scancel {s.split('（')[0]}" for s in on_hpc)
                    + "\n（scancel 属高风险命令，我不会代执行；也可由你授权后"
                      "经 hpc_exec 执行）")
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
        with _task_lock(project_id, task_id):
            return self._pump_locked(store, project_id, task_id, flow)

    def _pump_locked(self, store, project_id, task_id, flow) -> str:
        if self.hpc is None:
            return ("未连接超算，无法查询作业进度。作业在超算上照常运行；"
                    "配置 SSH 后回到本会话即可看到实况与报告。")
        account = self.cfg.ssh_username
        try:
            code, out, _ = self.hpc.run(f"squeue -u {account}")
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

        # M52 依赖闸门补提：状态推进后执行（本轮终态立即解锁下游），
        # 空位允许时从等待队列补提「依赖已满足」的作业
        backfilled: list[str] = []
        stalled = self._cascade_blocks(flow)
        if flow.get("waiting"):
            gate = self._gate(flow)
            for key in list(flow["waiting"]):
                job = next((j for j in flow["plan"]["jobs"]
                            if j["key"] == key), None)
                if job is None:
                    flow["waiting"].remove(key)
                    continue
                if free <= 0 or key not in gate.eligible:
                    continue   # 依赖未满足或无空位：继续等
                try:
                    _backfill_local = (Path(flow.get("local_dir"))
                                       if flow.get("local_dir") else None)
                    job["slurm_id"] = self._submit_one(
                        flow.get("hpc_dir") or "", job,
                        local_dir=_backfill_local)
                    job["status"] = "submitted"
                    flow["waiting"].remove(key)
                    backfilled.append(f"{key}(id {job['slurm_id']})")
                    free -= 1
                except Exception:  # noqa: BLE001
                    continue

        if backfilled:
            progress.insert(0, "等待队列解锁，已自动补提: " + ", ".join(backfilled))
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
        """M57：全部作业终态后清理 vaspkit 等留下的 *.err/*.log 临时文件。

        用户政策：这些文件不需要保留。范围限超算工作区根与各作业目录；
        best-effort，失败不影响报告生成。
        """
        remote = (flow.get("hpc_dir") or "").rstrip("/")
        hpc = getattr(self, "hpc", None)
        if not remote or hpc is None:
            return
        local_dir = Path(flow["local_dir"]) if flow.get("local_dir") else None
        targets = [remote]
        for job in flow.get("plan", {}).get("jobs", []):
            targets.append(self._job_calc_dir(
                remote, local_dir, str(job.get("key") or "")).rstrip("/"))
        seen: set[str] = set()
        for t in targets:
            if not t or t in seen:
                continue
            seen.add(t)
            try:
                hpc.run(f"rm -f '{t}'/*.err '{t}'/*.log 2>/dev/null")
            except Exception:  # noqa: BLE001
                continue

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
