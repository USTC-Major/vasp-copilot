"""ParaCloud protocol, explicit consent and transfer-safe monitoring tests."""
from pathlib import Path
from unittest.mock import Mock

import pytest

from ai_mode.config import AiModeConfig
from ai_mode.orchestrator import Orchestrator
from ai_mode.settings.global_api import mask_config, update_from_patch
from ai_mode.scheduler_profile import (queue_command, submit_argv, parse_receipt,
    parse_queue, occupied, parse_accounting, target_binding)
from ai_mode.consent import spawn_submit_card
from tests.ai_mode.test_orchestrator import (FakeHPC, _ready_flow, _confirmed_submit,
    env, OUTCAR_OK, OSZICAR_OK)

HEADER = "JOBID PARTITION NAME USER ST TIME CPUS NODES NODELIST(REASON)\n"
SID = "NICHE-I162269"


class CloudHPC(FakeHPC):
    def __init__(self):
        super().__init__(outcar=OUTCAR_OK.encode(), osziacar=OSZICAR_OK.encode())
        self.queue = HEADER
        self.acct = f"{SID} demo NICHE bscc-a2 64 COMPLETED 0:0\n"
        self.receipt = f"Submitted batch job {SID}\n"
        self.submit_exit = 0

    def run(self, command, *, cwd=None, timeout=None):
        if command == "cqueue":
            self.calls.append(command)
            return 0, self.queue, ""
        if command.startswith("cacct -j "):
            self.calls.append(command)
            return 0, self.acct, ""
        if command.startswith("cbatch "):
            self.calls.append(command)
            return self.submit_exit, self.receipt, ""
        return super().run(command, cwd=cwd, timeout=timeout)


def prepared(env, jobs=None):
    store, pid, tid, cfg = env
    cfg = cfg.model_copy(update={"scheduler_backend": "paracloud"})
    hpc = CloudHPC()
    flow = _ready_flow(store, pid, tid, hpc, jobs or [{"key": "relax", "status": "draft", "requires": []}])
    for job in flow["plan"]["jobs"]:
        job.setdefault("label", job["key"])
    orch = Orchestrator(cfg, hpc=hpc)
    orch._precheck(flow, Path(flow["local_dir"]), True, flow["hpc_dir"], [])
    orch._draft(flow)
    store.update_task(pid, tid, flow=flow)
    return store, pid, tid, cfg, hpc, orch


def test_explicit_profiles_and_settings():
    assert submit_argv("run.sh", "paracloud") == ["cbatch", "run.sh"]
    assert queue_command("paracloud", "demo@BSCC-A2") == "cqueue"
    assert queue_command("slurm", "demo") == "squeue -u demo"
    cfg = update_from_patch(AiModeConfig(), {"scheduler_backend": "paracloud"})
    assert mask_config(cfg)["ssh"]["scheduler_backend"] == "paracloud"
    with pytest.raises(ValueError):
        update_from_patch(cfg, {"scheduler_backend": "cbatch;bad"})
    with pytest.raises(ValueError):
        submit_argv("run.sh;bad", "paracloud")


@pytest.mark.parametrize("code,text", [(1, f"Submitted batch job {SID}"),
    (0, ""), (0, "Submitted batch job 42"),
    (0, f"Submitted batch job {SID}\nSubmitted batch job {SID}"),
    (0, f"Submitted batch job {SID};bad")])
def test_ambiguous_or_failed_receipt_never_means_success(code, text):
    with pytest.raises(RuntimeError):
        parse_receipt("paracloud", code, text)


def test_receipt_protocols_do_not_mix():
    assert parse_receipt("paracloud", 0, f"Submitted batch job {SID}\n") == SID
    assert parse_receipt("slurm", 0, "Submitted batch job 42\n") == 42
    with pytest.raises(RuntimeError):
        parse_receipt("slurm", 0, f"Submitted batch job {SID}\n")


@pytest.mark.parametrize("state", ["RD", "UPLoad", "WR", "DOWNLOAD", "PD", "R", "CG", "MYSTERY"])
def test_transferring_and_unknown_jobs_consume_capacity(state):
    states = parse_queue("paracloud", HEADER + f"{SID} NICHE demo user {state} 0 64 1 host\n")
    assert occupied(states) == 1


@pytest.mark.parametrize("text", ["", "bad output", "JOBID bad\n", HEADER + "42 cpu x u R 0\n"])
def test_malformed_queue_fails_closed(text):
    with pytest.raises(ValueError):
        parse_queue("paracloud", text)


def test_accounting_requires_unique_exact_job_and_exit_code():
    row = f"{SID} name NICHE bscc-a2 64 COMPLETED 0:0\n"
    assert parse_accounting(SID, row) == ("COMPLETED", "0:0")
    assert parse_accounting(SID, row.replace("0:0", "1:0")) == ("FAILED", "1:0")
    for bad in ("", row + row, row.replace(SID, SID + ".batch"), row.replace("0:0", "?")):
        with pytest.raises(ValueError):
            parse_accounting(SID, bad)


def test_cloud_consent_receipt_and_completed_evidence(env):
    store, pid, tid, cfg, hpc, orch = prepared(env)
    card = spawn_submit_card(store, pid, tid)
    assert card["binding"]["execution_kind"] == "paracloud_cbatch"
    assert card["binding"]["drafts"][0]["submit_cmd"] == "cbatch run.sh"
    answer = _confirmed_submit(orch, store, pid, tid)
    assert SID in answer
    job = store.get_task(pid, tid)["flow"]["plan"]["jobs"][0]
    assert job["slurm_id"] == SID
    assert job["scheduler_target"] == target_binding(cfg)
    orch.monitor(store, pid, tid, {})
    job = store.get_task(pid, tid)["flow"]["plan"]["jobs"][0]
    assert job["status"] == "completed"
    assert job["accounting_evidence"]["exit_code"] == "0:0"
    assert hpc.calls.count("cbatch run.sh") == 1
    assert not any(c.startswith("sbatch") for c in hpc.calls)


@pytest.mark.parametrize("state", ["RD", "UPLoad", "WR", "CG", "MYSTERY"])
def test_active_transfer_never_reads_old_successful_outputs(env, state):
    store, pid, tid, cfg, hpc, orch = prepared(env)
    _confirmed_submit(orch, store, pid, tid)
    hpc.queue = HEADER + f"{SID} NICHE demo user {state} 0 64 1 host\n"
    hpc.read_file = Mock(side_effect=AssertionError("must not read outputs"))
    orch.monitor(store, pid, tid, {})
    hpc.read_file.assert_not_called()
    assert not any(c.startswith("cacct") for c in hpc.calls)
    assert store.get_task(pid, tid)["flow"]["plan"]["jobs"][0]["status"] in {"submitted", "queued", "running"}


def test_missing_accounting_retains_state_and_never_retries(env):
    store, pid, tid, cfg, hpc, orch = prepared(env)
    _confirmed_submit(orch, store, pid, tid)
    hpc.acct = "JobID JobName Partition Account AllocCPUS State ExitCode\n"
    hpc.read_file = Mock(side_effect=AssertionError("must not read outputs"))
    for _ in range(2):
        orch.monitor(store, pid, tid, {})
    hpc.read_file.assert_not_called()
    assert hpc.calls.count("cbatch run.sh") == 1
    assert store.get_task(pid, tid)["flow"]["plan"]["jobs"][0]["status"] == "submitted"


def test_nonzero_exit_overrides_scientific_success(env):
    store, pid, tid, cfg, hpc, orch = prepared(env)
    _confirmed_submit(orch, store, pid, tid)
    hpc.acct = hpc.acct.replace("0:0", "1:0")
    orch.monitor(store, pid, tid, {})
    assert store.get_task(pid, tid)["flow"]["plan"]["jobs"][0]["status"] == "failed"


@pytest.mark.parametrize("field,value", [("scheduler_backend", "slurm"), ("ssh_host", "other.invalid"),
    ("ssh_port", 2222), ("ssh_username", "other"), ("ssh_identity_file", "/other/key")])
def test_changed_identity_or_platform_invalidates_confirmation(env, field, value):
    store, pid, tid, cfg, hpc, orch = prepared(env)
    setattr(orch.cfg, field, value)
    assert "AI_SCHEDULER_CHANGED" in _confirmed_submit(orch, store, pid, tid)
    assert hpc.calls == []


def test_changed_platform_stops_monitor_before_any_remote_call(env):
    store, pid, tid, cfg, hpc, orch = prepared(env)
    _confirmed_submit(orch, store, pid, tid)
    hpc.calls.clear()
    orch.cfg.scheduler_backend = "slurm"
    assert "进度未知" in orch.monitor(store, pid, tid, {})
    assert hpc.calls == []


def test_uncertain_receipt_is_not_retried(env):
    store, pid, tid, cfg, hpc, orch = prepared(env)
    hpc.receipt = "lost reply"
    assert "提交结果不确定" in _confirmed_submit(orch, store, pid, tid)
    orch.monitor(store, pid, tid, {})
    assert hpc.calls.count("cbatch run.sh") == 1
    assert store.get_task(pid, tid)["flow"]["plan"]["jobs"][0]["status"] == "unknown"


def test_batch_never_exceeds_available_capacity(env):
    store, pid, tid, cfg, hpc, orch = prepared(env, [
        {"key": "a", "status": "draft", "requires": []},
        {"key": "b", "status": "draft", "requires": []},
    ])
    orch.cfg.max_jobs = 1
    _confirmed_submit(orch, store, pid, tid)
    assert hpc.calls.count("cbatch run.sh") == 1
