# -*- coding: utf-8 -*-
"""M14 执行态真实链路测试：注入假 HPC（run/stat/read_file/write_file/mkdir 同
SSHManager 签名）离线验证 规划→准备→搭建→预检→草稿→确认→提交→监控→终态→报告。
"""

import hashlib

import pytest
from pathlib import Path

from ai_mode.config import AiModeConfig
from ai_mode.consent import (claim_action, finish_action, resolve_card,
                             spawn_submit_card)
from ai_mode.orchestrator import Orchestrator
from ai_mode.projects import ProjectStore
from ai_mode.tools.draft import fingerprint_remote_submit_script


class FakeHPC:
    """内存假超算：文件系统 + 命令执行，全部真实落在 dict 里。"""

    execution_mode = "Fake"

    def __init__(self, squeue_rows=(), outcar=b"", osziacar=b""):
        self.files = {}
        self.calls = []
        self.write_calls = []
        self.squeue_rows = list(squeue_rows)
        self._job_id = 4200
        self.outcar = outcar
        self.osziacar = osziacar

    def run(self, command, *, cwd=None, timeout=None):
        self.calls.append(command)
        if command.startswith("mkdir -p"):
            return 0, "", ""
        if "which vaspkit" in command or "command -v vaspkit" in command:
            return 0, "/share/apps/vaspkit\n", ""
        if command.rstrip().endswith(" -v"):
            return 0, "VASPKIT 3.3.1\n", ""
        if "echo 0 |" in command or command.rstrip().endswith(" -h"):
            return 0, "VASPKIT tasks: 101 301 401 501\n", ""
        if command.startswith("squeue"):
            return 0, "\n".join(self.squeue_rows), ""
        if command.startswith("sbatch"):
            self._job_id += 1
            return 0, f"Submitted batch job {self._job_id}\n", ""
        return 0, "", ""

    def stat(self, remote):
        if remote in self.files:
            return {"size": len(self.files[remote]), "mtime": 1}
        prefix = remote.rstrip("/") + "/"
        return {} if any(path.startswith(prefix) for path in self.files) else None

    def list_dir_info(self, remote):
        prefix = remote.rstrip("/") + "/"
        rows = []
        seen = set()
        for path, data in self.files.items():
            if not path.startswith(prefix):
                continue
            tail = path[len(prefix):]
            name, separator, _rest = tail.partition("/")
            if not name or name in seen:
                continue
            seen.add(name)
            rows.append({"name": name, "is_dir": bool(separator),
                         "size": 0 if separator else len(data)})
        return rows

    def read_file(self, remote, *, max_bytes=None):
        if remote in self.files:
            data = self.files[remote]
            return data[:max_bytes] if max_bytes else data
        name = remote.rstrip("/").split("/")[-1]
        if name == "OUTCAR":
            return self.outcar
        if name == "OSZICAR":
            return self.osziacar
        raise FileNotFoundError(remote)

    def write_file(self, remote, data):
        self.write_calls.append(remote)
        self.files[remote] = bytes(data)
        return len(data)

    def atomic_write_file(self, remote, data, *, expected_sha256):
        payload = bytes(data)
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise RuntimeError("hash mismatch")
        self.write_calls.append(remote)
        self.files[remote] = payload
        return len(payload)

    def mkdir(self, remote):
        return None

    def sha256_file(self, remote):
        return hashlib.sha256(self.files[remote]).hexdigest()


class RecordingFakeHPC(FakeHPC):
    """额外记录 run 的 (command, cwd)，用于断言 sbatch 的发起目录。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.run_calls: list[tuple[str, str | None]] = []

    def run(self, command, *, cwd=None, timeout=None):
        self.run_calls.append((command, cwd))
        return super().run(command, cwd=cwd, timeout=timeout)


class UncertainSubmitHPC(FakeHPC):
    def run(self, command, *, cwd=None, timeout=None):
        if command.startswith("sbatch"):
            self.calls.append(command)
            raise TimeoutError("connection lost after dispatch")
        return super().run(command, cwd=cwd, timeout=timeout)


OUTCAR_OK = (
    "ENCUT = 400  EDIFF = 1e-4  IBRION = 2  ISIF = 3  NSW = 99\n"
    "  free  energy   TOTEN = -123.456789 eV\n"
    "  free  energy   TOTEN = -123.456789 eV\n"
    "  reached required accuracy - stopping structural energy minimisation\n"
    "  achieved convergence\n"
)
OSZICAR_OK = (
    "   1 F= -.12345679E+03     E0= -.12345679E+03  d E =-.45678E+00\n"
    " DAV:  1    -0.12345679E+03   -0.12345679E+03  -0.12345679E+03  -0.12121E+01\n"
)


@pytest.fixture
def env(tmp_path):
    store = ProjectStore(tmp_path / "home")
    prj = store.create_project("执行态项目")
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "POSCAR").write_text("POSCAR fake\n", encoding="utf-8")
    (ws / "INCAR").write_text("SYSTEM = test\n", encoding="utf-8")
    (ws / "run.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    task = store.create_task(prj["id"], goal="结构优化",
                             local_workspace=str(ws),
                             hpc_workspace="/home/user/calc/r1")
    cfg = AiModeConfig(data_dir=(tmp_path / "data"), ssh_username="vaspuser",
                       max_jobs=2)
    return store, prj["id"], task["id"], cfg


def _remote_job(hpc, root: str, key: str, *, script_name: str = "run.sh"):
    calc = f"{root.rstrip('/')}/{key}" if key else root.rstrip("/")
    for name in ("INCAR", "POSCAR", "KPOINTS", "POTCAR"):
        hpc.files[f"{calc}/{name}"] = f"{name} {key}\n".encode()
    script = b"#!/bin/bash\nsrun vasp_std\n"
    script_path = f"{calc}/{script_name}"
    hpc.files[script_path] = script
    digest = hashlib.sha256(script).hexdigest()
    attestation = {
        "source": "remote", "directory": calc, "script_name": script_name,
        "normalized_path": script_path, "sha256": digest,
        "size": len(script), "mtime": 1, "action_id": "act_test",
        "binding_hash": "binding_test",
    }
    draft = {
        "job_key": key, "dir": calc, "script_name": script_name,
        "script_source": "remote", "script_path": script_path,
        "script_sha256": digest, "script_size": len(script),
        "attestation_action_id": "act_test",
        "attestation_binding_hash": "binding_test",
        "submit_cmd": f"sbatch {script_name}",
    }
    return attestation, draft


def _ready_flow(store, pid, tid, hpc, jobs, *, root="/home/user/calc/r1"):
    ws = Path(store.get_task(pid, tid)["local_workspace"]).resolve()
    attestations = {}
    drafts = []
    for job in jobs:
        if job.get("status") == "skipped":
            continue
        attestation, draft = _remote_job(hpc, root, job["key"])
        attestations[job["key"]] = attestation
        drafts.append(draft)
    flow = {
        "phase": "await_submit", "goal": "test",
        "local_dir": str(ws), "hpc_dir": root, "uploaded": False,
        "execution_mode": "Fake", "script_attestations": attestations,
        "precheck": {"ok": True, "hard": True, "issues": []},
        "draft": drafts, "waiting": [], "extractions": {}, "report": "",
        "logs": [], "plan": {"strategy": "test", "jobs": jobs},
    }
    checker = Orchestrator(AiModeConfig(data_dir=ws.parent), hpc=hpc)
    checker._precheck(flow, ws, True, root, [])
    store.update_task(pid, tid, flow=flow)
    return flow


def _confirmed_submit(orch, store, pid, tid):
    """Exercise the same approve -> claim -> submit -> finish boundary as chat."""
    card = spawn_submit_card(store, pid, tid)
    assert card["binding"]["execution_mode"] == orch.execution_mode
    assert len(card["binding"]["precheck_digest"]) == 64
    resolved = resolve_card(store, pid, tid, card["card_id"], approved=True)
    assert resolved["approved"] is True
    action = claim_action(store, pid, tid, card["action_id"])
    assert action is not None and action["state"] == "executing"
    flow = store.get_task(pid, tid)["flow"]
    result = orch._submit(store, pid, tid, flow)
    latest = store.get_task(pid, tid)["flow"]
    if any(j.get("submission_state") == "unknown"
           for j in latest["plan"]["jobs"]):
        terminal = "unknown"
    elif "AI_PRECHECK_BLOCKED" in result:
        terminal = "failed"
    else:
        terminal = "executed"
    assert finish_action(store, pid, tid, card["action_id"],
                         state=terminal, result=result) is not None
    return result


def test_remote_script_fingerprint_rejects_empty_file():
    """远端空脚本不能获得可用于预检或提交的 SHA-256 指纹。"""
    hpc = FakeHPC()
    hpc.files["/home/user/calc/r1/relax/run.sh"] = b""

    with pytest.raises(RuntimeError, match="远端提交脚本为空"):
        fingerprint_remote_submit_script(
            hpc, "/home/user/calc/r1/relax", "run.sh")


def test_offline_no_fake_submission(env):
    store, pid, tid, cfg = env
    orch = Orchestrator(cfg, hpc=None)               # 未配置 SSH
    answer = orch.begin(store, pid, tid, "结构优化")
    flow = store.get_task(pid, tid)["flow"]
    assert flow["phase"] == "blocked"
    assert "提交前检查未通过" in answer
    assert not flow.get("draft")
    assert "演示" not in answer and "演示调度" not in answer

    again = orch.handle(store, pid, tid, "确认提交")
    assert "Submitted batch job" not in again
    assert "Submitted batch job" not in again
    assert store.get_task(pid, tid)["flow"]["phase"] == "blocked"


def test_stale_execution_mode_migrates_to_actual_hpc_backend(env):
    store, pid, tid, cfg = env
    store.update_task(pid, tid, flow={
        "phase": "blocked", "execution_mode": "Real", "plan": {"jobs": []},
    })
    orch = Orchestrator(cfg, hpc=None)
    orch.handle(store, pid, tid, "查看状态")
    assert store.get_task(pid, tid)["flow"]["execution_mode"] == "None"

    hpc = FakeHPC()
    fake_orch = Orchestrator(cfg, hpc=hpc)
    fake_orch.handle(store, pid, tid, "查看状态")
    assert store.get_task(pid, tid)["flow"]["execution_mode"] == "Fake"


def test_full_chain_offline_with_fake_hpc(env):
    store, pid, tid, cfg = env
    hpc = FakeHPC(outcar=OUTCAR_OK.encode("utf-8"),
                  osziacar=OSZICAR_OK.encode("utf-8"))
    orch = Orchestrator(cfg, hpc=hpc, llm_factory=lambda _c: None)

    jobs = [{"key": "relax", "label": "结构优化", "description": "",
             "requires": [], "status": "draft", "slurm_id": None}]
    _ready_flow(store, pid, tid, hpc, jobs)
    before_files = dict(hpc.files)

    submit = _confirmed_submit(orch, store, pid, tid)
    flow = store.get_task(pid, tid)["flow"]
    assert "已提交" in submit and "slurm id 4201" in submit
    assert flow["phase"] == "monitoring"
    assert flow["plan"]["jobs"][0]["slurm_id"] == 4201
    assert hpc.files == before_files
    assert hpc.write_calls == []

    # 在途作业：squeue 给 R -> 运行中
    hpc.squeue_rows.append(
        "4201  vaspuser  r1  vasp_std  R  node01  6 2")
    running = orch.handle(store, pid, tid, "状态")
    assert "运行中（4201）" in running

    # 作业终态：squeue 空 -> 提取 OUTCAR/OSZICAR -> 报告
    hpc.squeue_rows.clear()
    final = orch.handle(store, pid, tid, "再看一下")
    flow = store.get_task(pid, tid)["flow"]
    assert flow["phase"] == "done"
    assert "已完成（已收敛）" in final
    assert "## 概览" in final and "-123.456789" in final
    assert final.count("## 概览") == 1               # 真实报告，不是模板


def test_orchestrator_submit_requires_an_executing_bound_action(env):
    store, pid, tid, cfg = env
    hpc = FakeHPC()
    orch = Orchestrator(cfg, hpc=hpc, llm_factory=lambda _c: None)
    jobs = [{"key": "relax", "label": "结构优化", "description": "",
             "requires": [], "status": "draft", "slurm_id": None}]
    flow = _ready_flow(store, pid, tid, hpc, jobs)

    by_text = orch.handle(store, pid, tid, "确认提交")
    direct = orch._submit(store, pid, tid, flow)

    assert "AI_SUBMIT_CONFIRMATION_REQUIRED" in by_text
    assert "AI_SUBMIT_CONFIRMATION_REQUIRED" in direct
    assert not [call for call in hpc.calls if call.startswith("sbatch")]


def test_wait_queue_until_slot_then_backfill(env):
    store, pid, tid, cfg = env
    cfg.max_jobs = 1
    hpc = FakeHPC(outcar=OUTCAR_OK.encode("utf-8"),
                  osziacar=OSZICAR_OK.encode("utf-8"))
    orch = Orchestrator(cfg, hpc=hpc, llm_factory=lambda _c: None)
    jobs = [{"key": "relax", "label": "结构优化", "description": "",
             "requires": [], "status": "draft", "slurm_id": None}]
    _ready_flow(store, pid, tid, hpc, jobs)

    # 占满唯一空位 -> 确认提交应进「等待空位」而非假提交
    hpc.squeue_rows.append("111 vaspuser other vasp_std R node 6 2")
    hold = _confirmed_submit(orch, store, pid, tid)
    assert "必须重新预检" in hold and "已提交" not in hold
    flow = store.get_task(pid, tid)["flow"]
    assert flow["phase"] == "await_submit"
    assert flow["waiting"] == []
    assert not [call for call in hpc.calls if call.startswith("sbatch")]

    # Capacity recovery alone cannot inherit the prior confirmation.
    hpc.squeue_rows.clear()
    back = orch.handle(store, pid, tid, "查看空位")
    assert "待你确认提交" in back
    assert not [call for call in hpc.calls if call.startswith("sbatch")]
    submitted = _confirmed_submit(orch, store, pid, tid)
    assert "slurm id 4201" in submitted
    assert len([call for call in hpc.calls if call.startswith("sbatch")]) == 1


def test_begin_uses_user_workspace_as_compute_dir(env):
    """M42.5 回归：任务设了 local_workspace 时，orchestrator.begin 的
    flow.local_dir 即用户工作区，且不再创建私有草稿目录。"""
    store, pid, tid, cfg = env
    ws = Path(store.get_task(pid, tid)["local_workspace"]).expanduser().resolve()
    orch = Orchestrator(cfg, hpc=None)
    orch.begin(store, pid, tid, "structure relax")
    flow = store.get_task(pid, tid)["flow"]
    assert Path(flow["local_dir"]).expanduser().resolve() == ws
    assert not (cfg.data_dir / "workspace").exists()


def test_handle_heals_stale_local_dir_to_workspace(env):
    """M44 回归：历史 flow.local_dir 仍指向私有草稿目录时，只要任务设了
    local_workspace，handle() 就自愈为工作区，避免后续预检/提交走旧目录。"""
    store, pid, tid, cfg = env
    ws = Path(store.get_task(pid, tid)["local_workspace"]).expanduser().resolve()
    private = cfg.data_dir / "workspace" / f"{pid}__{tid}"
    store.update_task(pid, tid, flow={
        "phase": "await_submit", "goal": "x",
        "local_dir": str(private),
        "hpc_dir": "", "plan": {"strategy": "", "jobs": []},
        "precheck": {"ok": True, "issues": []}, "draft": [],
    })
    orch = Orchestrator(cfg, hpc=None)
    orch.handle(store, pid, tid, "取消")   # 触发 handle() 顶部自愈
    flow = store.get_task(pid, tid)["flow"]
    assert Path(flow["local_dir"]).expanduser().resolve() == ws
    assert not (cfg.data_dir / "workspace").exists()   # 私有目录从未被创建/使用


def test_submit_skips_user_skipped_jobs(env):
    """M46：用户跳过的作业（status=skipped）确认提交时被跳过，
    只真实提交被选中的作业。"""
    store, pid, tid, cfg = env
    hpc = FakeHPC()
    orch = Orchestrator(cfg, hpc=hpc, llm_factory=lambda _c: None)
    jobs = [
        {"key": "relax", "label": "结构优化", "description": "",
         "requires": [], "status": "draft", "slurm_id": None},
        {"key": "static", "label": "静态自洽", "description": "",
         "requires": [], "status": "skipped", "slurm_id": None},
    ]
    _ready_flow(store, pid, tid, hpc, jobs)
    answer = _confirmed_submit(orch, store, pid, tid)
    flow = store.get_task(pid, tid)["flow"]
    assert "relax 已提交" in answer
    assert "static 已提交" not in answer
    assert flow["plan"]["jobs"][0]["slurm_id"] == 4201
    assert flow["plan"]["jobs"][1]["status"] == "skipped"
    assert flow["plan"]["jobs"][1].get("slurm_id") is None
def test_per_job_dir_submit_runs_sbatch_in_job_dir(env):
    """问题回归：每个作业独占一个子目录（输入+提交脚本同路径），sbatch 在
    该作业目录内执行，绝不在工作区根/上一级目录 sbatch。"""
    store, pid, tid, cfg = env
    hpc = RecordingFakeHPC()
    orch = Orchestrator(cfg, hpc=hpc, llm_factory=lambda _c: None)
    jobs = [
        {"key": "relax", "label": "结构优化", "description": "",
         "requires": [], "status": "draft", "slurm_id": None},
        {"key": "static", "label": "静态自洽", "description": "",
         "requires": [], "status": "draft", "slurm_id": None},
    ]
    _ready_flow(store, pid, tid, hpc, jobs)
    before_files = dict(hpc.files)
    answer = _confirmed_submit(orch, store, pid, tid)
    flow = store.get_task(pid, tid)["flow"]
    assert "relax 已提交" in answer and "static 已提交" in answer
    assert hpc.files == before_files  # submit never uploads or rewrites inputs
    assert hpc.write_calls == []
    # sbatch 在作业目录内发起（cwd=作业目录），而非工作区根
    by_cwd: dict[str, list[str]] = {}
    for cmd, cwd in hpc.run_calls:
        if cmd.startswith("sbatch "):
            by_cwd.setdefault(cwd or "", []).append(cmd)
    assert by_cwd.get("/home/user/calc/r1/relax") == ["sbatch run.sh"]
    assert by_cwd.get("/home/user/calc/r1/static") == ["sbatch run.sh"]
    assert "/home/user/calc/r1" not in by_cwd


def test_changed_remote_script_blocks_submit_with_zero_sbatch(env):
    store, pid, tid, cfg = env
    hpc = FakeHPC()
    orch = Orchestrator(cfg, hpc=hpc, llm_factory=lambda _c: None)
    jobs = [{"key": "relax", "label": "结构优化", "description": "",
             "requires": [], "status": "draft", "slurm_id": None}]
    _ready_flow(store, pid, tid, hpc, jobs)
    hpc.files["/home/user/calc/r1/relax/run.sh"] = b"#!/bin/bash\nchanged\n"
    answer = _confirmed_submit(orch, store, pid, tid)
    assert "AI_PRECHECK_BLOCKED" in answer
    assert not [call for call in hpc.calls if call.startswith("sbatch")]
    assert store.get_task(pid, tid)["flow"]["phase"] == "blocked"


def test_changed_vasp_input_invalidates_confirmed_precheck_snapshot(env):
    store, pid, tid, cfg = env
    hpc = FakeHPC()
    orch = Orchestrator(cfg, hpc=hpc, llm_factory=lambda _c: None)
    jobs = [{"key": "relax", "label": "结构优化", "description": "",
             "requires": [], "status": "draft", "slurm_id": None}]
    flow = _ready_flow(store, pid, tid, hpc, jobs)
    assert {item["name"] for item in flow["precheck"]["snapshot"]["inputs"]} == {
        "INCAR", "POSCAR", "KPOINTS", "POTCAR"}
    card = spawn_submit_card(store, pid, tid)
    resolve_card(store, pid, tid, card["card_id"], approved=True)
    assert claim_action(store, pid, tid, card["action_id"]) is not None

    hpc.files["/home/user/calc/r1/relax/POTCAR"] = b"changed POTCAR\n"
    answer = orch._submit(store, pid, tid, flow)
    assert "AI_PRECHECK_STALE" in answer
    assert not [call for call in hpc.calls if call.startswith("sbatch")]


def test_uncertain_submit_is_persisted_and_never_retried(env):
    store, pid, tid, cfg = env
    hpc = UncertainSubmitHPC()
    orch = Orchestrator(cfg, hpc=hpc, llm_factory=lambda _c: None)
    jobs = [{"key": "relax", "label": "结构优化", "description": "",
             "requires": [], "status": "draft", "slurm_id": None}]
    _ready_flow(store, pid, tid, hpc, jobs)
    first = _confirmed_submit(orch, store, pid, tid)
    flow = store.get_task(pid, tid)["flow"]
    assert "提交结果不确定" in first
    assert flow["plan"]["jobs"][0]["submission_state"] == "unknown"
    assert flow["phase"] == "blocked"
    assert len([call for call in hpc.calls if call.startswith("sbatch")]) == 1
    orch._submit(store, pid, tid, flow)
    assert len([call for call in hpc.calls if call.startswith("sbatch")]) == 1
    assert store.get_task(pid, tid)["flow"]["plan"]["jobs"][0][
        "submission_state"] == "unknown"


def _chain_flow(store, pid, tid, hpc):
    """Ready remote relax → static → DOS chain with exact script bindings."""
    jobs = [
        {"key": "relax", "label": "结构优化", "description": "",
         "requires": [], "status": "draft", "slurm_id": None},
        {"key": "relax/static", "label": "静态自洽", "description": "",
         "requires": ["relax"], "status": "draft", "slurm_id": None},
        {"key": "relax/static/dos", "label": "DOS", "description": "",
         "requires": ["relax/static"], "status": "draft", "slurm_id": None},
    ]
    return _ready_flow(store, pid, tid, hpc, jobs)


def test_submit_dependency_gate_holds_dependent_jobs(env):
    store, pid, tid, cfg = env
    hpc = FakeHPC()
    orch = Orchestrator(cfg, hpc=hpc, llm_factory=lambda _c: None)
    _chain_flow(store, pid, tid, hpc)
    answer = _confirmed_submit(orch, store, pid, tid)
    flow = store.get_task(pid, tid)["flow"]
    assert "- relax 已提交" in answer
    assert flow["plan"]["jobs"][0]["status"] == "submitted"
    assert flow["plan"]["jobs"][1]["status"] == "draft"
    assert flow["plan"]["jobs"][2]["status"] == "draft"
    assert flow["waiting"] == []
    assert flow["phase"] == "await_submit"
    assert "需重新确认" in answer
    assert len([call for call in hpc.calls if call.startswith("sbatch")]) == 1


def test_pump_backfills_chain_after_completion(env):
    store, pid, tid, cfg = env
    hpc = FakeHPC(outcar=OUTCAR_OK.encode("utf-8"),
                  osziacar=OSZICAR_OK.encode("utf-8"))
    orch = Orchestrator(cfg, hpc=hpc, llm_factory=lambda _c: None)
    _chain_flow(store, pid, tid, hpc)
    _confirmed_submit(orch, store, pid, tid)          # 只提交 relax（4201）

    # relax 从 squeue 消失 → 终态收敛 → static 自动补提（4202），dos 仍等待
    hpc.squeue_rows.clear()
    first = orch.monitor(store, pid, tid,
                         store.get_task(pid, tid)["flow"])
    flow = store.get_task(pid, tid)["flow"]
    assert "自动补提" not in first
    assert flow["plan"]["jobs"][1]["status"] == "draft"
    assert flow["plan"]["jobs"][1]["slurm_id"] is None
    assert len([call for call in hpc.calls if call.startswith("sbatch")]) == 1

    # A fresh explicit confirmation may submit only the newly eligible job.
    refreshed = store.get_task(pid, tid)["flow"]
    orch._precheck(refreshed, Path(refreshed["local_dir"]), True,
                   refreshed["hpc_dir"], [])
    store.update_task(pid, tid, flow=refreshed)
    second = _confirmed_submit(orch, store, pid, tid)
    flow = store.get_task(pid, tid)["flow"]
    assert "relax/static 已提交" in second
    assert flow["plan"]["jobs"][2]["status"] == "draft"
    assert len([call for call in hpc.calls if call.startswith("sbatch")]) == 2


def test_pump_blocks_downstream_when_prerequisite_fails(env):
    store, pid, tid, cfg = env
    hpc = FakeHPC()
    orch = Orchestrator(cfg, hpc=hpc, llm_factory=lambda _c: None)
    _chain_flow(store, pid, tid, hpc)
    _confirmed_submit(orch, store, pid, tid)
    flow = store.get_task(pid, tid)["flow"]
    flow["plan"]["jobs"][0]["status"] = "failed"      # 手工注入前置失败
    flow["plan"]["jobs"][0]["slurm_id"] = None
    store.update_task(pid, tid, flow=flow)

    hpc.squeue_rows.clear()
    answer = orch.monitor(store, pid, tid,
                          store.get_task(pid, tid)["flow"])
    flow = store.get_task(pid, tid)["flow"]
    assert flow["plan"]["jobs"][1]["status"] == "blocked"
    assert flow["plan"]["jobs"][2]["status"] == "blocked"
    assert flow["waiting"] == []
    assert "自动补提" not in answer
    assert len([call for call in hpc.calls if call.startswith("sbatch")]) == 1
