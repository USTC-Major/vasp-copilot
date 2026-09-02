# -*- coding: utf-8 -*-
"""M14 执行态真实链路测试：注入假 HPC（run/stat/read_file/write_file/mkdir 同
SSHManager 签名）离线验证 规划→准备→搭建→预检→草稿→确认→提交→监控→终态→报告。
"""

import pytest
from pathlib import Path

from ai_mode.config import AiModeConfig
from ai_mode.orchestrator import Orchestrator
from ai_mode.projects import ProjectStore


class FakeHPC:
    """内存假超算：文件系统 + 命令执行，全部真实落在 dict 里。"""

    def __init__(self, squeue_rows=(), outcar=b"", osziacar=b""):
        self.files = {}
        self.calls = []
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
        return {} if remote in self.files else None

    def read_file(self, remote, *, max_bytes=None):
        if remote in self.files:
            return self.files[remote]
        name = remote.rstrip("/").split("/")[-1]
        if name == "OUTCAR":
            return self.outcar
        if name == "OSZICAR":
            return self.osziacar
        raise FileNotFoundError(remote)

    def write_file(self, remote, data):
        self.files[remote] = bytes(data)
        return len(data)

    def mkdir(self, remote):
        return None


class RecordingFakeHPC(FakeHPC):
    """额外记录 run 的 (command, cwd)，用于断言 sbatch 的发起目录。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.run_calls: list[tuple[str, str | None]] = []

    def run(self, command, *, cwd=None, timeout=None):
        self.run_calls.append((command, cwd))
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


def test_offline_no_fake_submission(env):
    store, pid, tid, cfg = env
    orch = Orchestrator(cfg, hpc=None)               # 未配置 SSH
    answer = orch.begin(store, pid, tid, "结构优化")
    flow = store.get_task(pid, tid)["flow"]
    assert flow["phase"] == "await_submit"           # 本地 8 步走到草稿
    assert "未连接超算" in answer
    assert "提交草稿" in answer and "确认提交" in answer
    assert "演示" not in answer and "演示调度" not in answer

    again = orch.handle(store, pid, tid, "确认提交")
    assert "未配置/未连接 SSH" in again
    assert "伪造作业号" in again                      # 绝不假装提交
    assert "Submitted batch job" not in again
    assert store.get_task(pid, tid)["flow"]["phase"] == "await_submit"


def test_full_chain_offline_with_fake_hpc(env):
    store, pid, tid, cfg = env
    hpc = FakeHPC(outcar=OUTCAR_OK.encode("utf-8"),
                  osziacar=OSZICAR_OK.encode("utf-8"))
    orch = Orchestrator(cfg, hpc=hpc, llm_factory=lambda _c: None)

    answer = orch.begin(store, pid, tid, "结构优化")
    flow = store.get_task(pid, tid)["flow"]
    assert flow["phase"] == "await_submit"
    assert "已上传输入文件到超算目录" in answer
    assert "已生成提交草稿" in answer
    assert "/home/user/calc/r1/POSCAR" in hpc.files  # 真实上传落盘
    assert flow["precheck"]["ok"] is True

    submit = orch.handle(store, pid, tid, "确认提交")
    flow = store.get_task(pid, tid)["flow"]
    assert "已提交" in submit and "slurm id 4201" in submit
    assert flow["phase"] == "monitoring"
    assert flow["plan"]["jobs"][0]["slurm_id"] == 4201

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


def test_wait_queue_until_slot_then_backfill(env):
    store, pid, tid, cfg = env
    cfg.max_jobs = 1
    hpc = FakeHPC(outcar=OUTCAR_OK.encode("utf-8"),
                  osziacar=OSZICAR_OK.encode("utf-8"))
    orch = Orchestrator(cfg, hpc=hpc, llm_factory=lambda _c: None)
    orch.begin(store, pid, tid, "结构优化")                     # 真实走到待确认
    assert store.get_task(pid, tid)["flow"]["phase"] == "await_submit"

    # 占满唯一空位 -> 确认提交应进「等待空位」而非假提交
    hpc.squeue_rows.append("111 vaspuser other vasp_std R node 6 2")
    hold = orch.handle(store, pid, tid, "确认提交")
    assert "等待空位" in hold and "已提交" not in hold
    flow = store.get_task(pid, tid)["flow"]
    assert flow["phase"] == "monitoring"
    assert "relax" in flow["waiting"]

    # 空位出现 -> 下一轮消息自动补提并真实提交；M52 起补提在状态推进后，
    # 刚补提的作业下一轮消息才读回 OUTCAR 完成收尾（非假模板）
    hpc.squeue_rows.clear()
    back = orch.handle(store, pid, tid, "查看空位")
    flow = store.get_task(pid, tid)["flow"]
    assert "已自动补提" in back
    assert flow["plan"]["jobs"][0]["slurm_id"] == 4201
    assert flow["plan"]["jobs"][0]["status"] == "submitted"
    assert not flow["waiting"]
    final = orch.handle(store, pid, tid, "再看一下")
    flow = store.get_task(pid, tid)["flow"]
    assert flow["plan"]["jobs"][0]["status"] == "completed"
    assert "已完成（已收敛）" in final


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
    ws = Path(store.get_task(pid, tid)["local_workspace"]).expanduser().resolve()
    store.update_task(pid, tid, flow={
        "phase": "await_submit", "goal": "结构优化 + 静态自洽",
        "local_dir": str(ws), "hpc_dir": "/home/user/calc/r1",
        "uploaded": False,
        "precheck": {"ok": True, "issues": []},
        "draft": [], "waiting": [], "extractions": {}, "report": "", "logs": [],
        "plan": {"strategy": "s", "jobs": [
            {"key": "relax", "label": "结构优化", "description": "",
             "status": "draft", "slurm_id": None},
            {"key": "static", "label": "静态自洽", "description": "",
             "status": "skipped", "slurm_id": None},
        ]},
    })
    answer = orch.handle(store, pid, tid, "确认提交")
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
    ws = Path(store.get_task(pid, tid)["local_workspace"]).expanduser().resolve()
    for key in ("relax", "static"):
        job_dir = ws / key
        job_dir.mkdir(parents=True, exist_ok=True)
        for name in ("INCAR", "POSCAR", "KPOINTS", "POTCAR"):
            (job_dir / name).write_text(f"{name} {key}\n", encoding="utf-8")
        (job_dir / "run.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    # 移除工作区根输入，保证只依赖各作业子目录
    for name in ("POSCAR", "INCAR"):
        (ws / name).unlink(missing_ok=True)
    hpc = RecordingFakeHPC()
    orch = Orchestrator(cfg, hpc=hpc, llm_factory=lambda _c: None)
    store.update_task(pid, tid, flow={
        "phase": "await_submit", "goal": "结构优化 + 静态自洽",
        "local_dir": str(ws), "hpc_dir": "/home/user/calc/r1",
        "uploaded": False,
        "precheck": {"ok": True, "issues": []},
        "draft": [], "waiting": [], "extractions": {}, "report": "", "logs": [],
        "plan": {"strategy": "s", "jobs": [
            {"key": "relax", "label": "结构优化",
             "description": "", "status": "draft", "slurm_id": None},
            {"key": "static", "label": "静态自洽",
             "description": "", "status": "draft", "slurm_id": None},
        ]},
    })
    answer = orch.handle(store, pid, tid, "确认提交")
    flow = store.get_task(pid, tid)["flow"]
    assert "relax 已提交" in answer and "static 已提交" in answer
    # 输入按作业子目录上传（镜像本地 relax/、static/）
    assert "/home/user/calc/r1/relax/INCAR" in hpc.files
    assert "/home/user/calc/r1/relax/POSCAR" in hpc.files
    assert "/home/user/calc/r1/static/INCAR" in hpc.files
    assert "/home/user/calc/r1/static/POTCAR" in hpc.files
    # 提交脚本落在各自作业目录
    assert "/home/user/calc/r1/relax/run.sh" in hpc.files
    assert "/home/user/calc/r1/static/run.sh" in hpc.files
    # sbatch 在作业目录内发起（cwd=作业目录），而非工作区根
    by_cwd: dict[str, list[str]] = {}
    for cmd, cwd in hpc.run_calls:
        if cmd.startswith("sbatch "):
            by_cwd.setdefault(cwd or "", []).append(cmd)
    assert by_cwd.get("/home/user/calc/r1/relax") == ["sbatch run.sh"]
    assert by_cwd.get("/home/user/calc/r1/static") == ["sbatch run.sh"]
    assert "/home/user/calc/r1" not in by_cwd


def _chain_flow(store, pid, tid, ws):
    """M52：relax → relax/static → relax/static/dos 链式规划的 flow。"""
    store.update_task(pid, tid, flow={
        "phase": "await_submit", "goal": "结构优化→静态→DOS",
        "local_dir": str(ws), "hpc_dir": "/home/user/calc/r1",
        "uploaded": True,          # 跳过上传，专注依赖闸门
        "precheck": {"ok": True, "issues": []},
        "draft": [], "waiting": [], "extractions": {}, "report": "", "logs": [],
        "plan": {"strategy": "链式", "jobs": [
            {"key": "relax", "label": "结构优化", "description": "",
             "requires": [], "status": "draft", "slurm_id": None},
            {"key": "relax/static", "label": "静态自洽", "description": "",
             "requires": ["relax"], "status": "draft", "slurm_id": None},
            {"key": "relax/static/dos", "label": "DOS", "description": "",
             "requires": ["relax/static"], "status": "draft", "slurm_id": None},
        ]},
    })


def test_submit_dependency_gate_holds_dependent_jobs(env):
    """M52：确认提交只跑无依赖作业，有依赖的进等待队列并注明原因。"""
    store, pid, tid, cfg = env
    hpc = FakeHPC()
    orch = Orchestrator(cfg, hpc=hpc, llm_factory=lambda _c: None)
    ws = Path(store.get_task(pid, tid)["local_workspace"]).expanduser().resolve()
    _chain_flow(store, pid, tid, ws)
    answer = orch.handle(store, pid, tid, "确认提交")
    flow = store.get_task(pid, tid)["flow"]
    assert "- relax 已提交" in answer
    assert flow["plan"]["jobs"][0]["status"] == "submitted"
    assert flow["plan"]["jobs"][1]["status"] == "waiting"
    assert flow["plan"]["jobs"][2]["status"] == "waiting"
    assert flow["waiting"] == ["relax/static", "relax/static/dos"]
    assert "依赖闸门" in answer
    assert "等待前置 relax 成功" in answer
    assert "等待前置 relax/static 成功" in answer


def test_pump_backfills_chain_after_completion(env):
    """M52：前序 completed 后，监控自动逐层补提下游（relax→static→dos）。"""
    store, pid, tid, cfg = env
    hpc = FakeHPC(outcar=OUTCAR_OK.encode("utf-8"),
                  osziacar=OSZICAR_OK.encode("utf-8"))
    orch = Orchestrator(cfg, hpc=hpc, llm_factory=lambda _c: None)
    ws = Path(store.get_task(pid, tid)["local_workspace"]).expanduser().resolve()
    _chain_flow(store, pid, tid, ws)
    orch.handle(store, pid, tid, "确认提交")          # 只提交 relax（4201）

    # relax 从 squeue 消失 → 终态收敛 → static 自动补提（4202），dos 仍等待
    hpc.squeue_rows.clear()
    first = orch.handle(store, pid, tid, "看进度")
    flow = store.get_task(pid, tid)["flow"]
    assert "已自动补提" in first and "relax/static" in first
    assert flow["plan"]["jobs"][1]["status"] == "submitted"
    assert flow["plan"]["jobs"][1]["slurm_id"] == 4202
    assert flow["plan"]["jobs"][2]["status"] == "waiting"

    # static 完成 → dos 补提（4203），全链完成
    hpc.squeue_rows.clear()
    second = orch.handle(store, pid, tid, "再看")
    flow = store.get_task(pid, tid)["flow"]
    assert flow["plan"]["jobs"][2]["status"] == "submitted"
    assert flow["plan"]["jobs"][2]["slurm_id"] == 4203
    assert "relax/static/dos" in second


def test_pump_blocks_downstream_when_prerequisite_fails(env):
    """M52：前置终态失败 → 下游移出等待队列标 blocked，流程可正常收尾。"""
    store, pid, tid, cfg = env
    hpc = FakeHPC()
    orch = Orchestrator(cfg, hpc=hpc, llm_factory=lambda _c: None)
    ws = Path(store.get_task(pid, tid)["local_workspace"]).expanduser().resolve()
    _chain_flow(store, pid, tid, ws)
    orch.handle(store, pid, tid, "确认提交")
    flow = store.get_task(pid, tid)["flow"]
    flow["plan"]["jobs"][0]["status"] = "failed"      # 手工注入前置失败
    flow["plan"]["jobs"][0]["slurm_id"] = None
    store.update_task(pid, tid, flow=flow)

    hpc.squeue_rows.clear()
    answer = orch.handle(store, pid, tid, "看进度")
    flow = store.get_task(pid, tid)["flow"]
    assert flow["plan"]["jobs"][1]["status"] == "blocked"
    assert flow["plan"]["jobs"][2]["status"] == "blocked"
    assert flow["waiting"] == []
    assert "已停止等待" in answer
    assert "前置 relax 失败" in answer
    assert flow["phase"] == "done"                    # blocked 算终态，流程不卡死
