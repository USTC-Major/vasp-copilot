"""Independent local/remote precheck behavior with an in-memory HPC only."""

from __future__ import annotations

import pytest

from backend.tests.toolbox_review.conftest import FakeHPC
from backend.toolbox.commands import ToolExecutor
from backend.toolbox.config import ExecutionConfig
from backend.toolbox.consent import spawn_submit_card
from backend.toolbox.contracts import ToolboxError
from backend.toolbox.orchestrator import Orchestrator
from backend.toolbox.projects import ProjectStore
from backend.toolbox.tools.draft import fingerprint_remote_submit_script


POSCAR = b"Si\n1\n1 0 0\n0 1 0\n0 0 1\nSi\n1\nDirect\n0 0 0\n"
INCAR = b"SYSTEM = synthetic\nENCUT = 520\n"
KPOINTS = b"Gamma mesh\n0\nGamma\n1 1 1\n0 0 0\n"
POTCAR = b"VRHFIN = Si: synthetic\nTITEL = PAW_PBE Si test\n End of Dataset\n"
SCRIPT = b"#!/bin/sh\n# user-provided synthetic script; never executed\n"


def _stack(tmp_path, *, remote=True):
    store = ProjectStore(tmp_path / "store")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = store.create_project("input QA")
    task = store.create_task(project["id"], goal="static input QA",
                             local_workspace=str(workspace),
                             hpc_workspace="/qa" if remote else "")
    config = ExecutionConfig(data_dir=tmp_path / "store", ssh_username="offline-qa")
    hpc = FakeHPC()
    orch = Orchestrator(config, hpc=hpc, llm_factory=lambda _config: None)
    attempt = "attempt-qa"
    calc = "/qa/a"
    payloads = {"POSCAR": POSCAR, "INCAR": INCAR,
                "KPOINTS": KPOINTS, "POTCAR": POTCAR}
    if remote:
        for name, content in payloads.items():
            hpc.files[f"{calc}/{name}"] = content
        hpc.files[f"{calc}/run.sh"] = SCRIPT
        script_identity = fingerprint_remote_submit_script(hpc, calc, "run.sh")
        source = "remote"
        directory = calc
    else:
        calc_dir = workspace / "a"
        calc_dir.mkdir()
        for name, content in payloads.items():
            (calc_dir / name).write_bytes(content)
        (calc_dir / "run.sh").write_bytes(SCRIPT)
        from backend.toolbox.tools.draft import fingerprint_local_submit_script
        script_identity = fingerprint_local_submit_script(calc_dir / "run.sh")
        source = "local"
        directory = str(calc_dir)
    attestation = {"job_key": "a", "attempt_id": attempt, "source": source,
                   "directory": directory, "script_name": "run.sh",
                   **script_identity}
    flow = {
        "phase": "await_submit", "local_dir": str(workspace),
        "hpc_dir": "/qa" if remote else "", "execution_mode": "Fake",
        "script_attestations": {"a": attestation},
        "plan": {"jobs": [{"key": "a", "label": "A", "kind": "static",
                            "requires": [], "status": "draft", "attempt_id": attempt}]},
    }
    store.update_task(project["id"], task["id"], flow=flow)
    return store, project["id"], task["id"], workspace, hpc, orch, config


def _run_orchestrator_precheck(stack, *, remote):
    store, project, task, workspace, hpc, orch, _config = stack
    flow = store.get_task(project, task)["flow"]
    orch._precheck(flow, workspace, remote, flow["hpc_dir"], [], job_key="a")
    store.update_task(project, task, flow=flow)
    return flow["plan"]["jobs"][0]["precheck"]


def _run_tool_precheck(stack):
    store, project, task, _workspace, _hpc, orch, config = stack
    tool = ToolExecutor(store=store, project_id=project, task_id=task, cfg=config, orch=orch)
    tool.tool_precheck({"job_key": "a", "attempt_id": "attempt-qa"})
    return store.get_task(project, task)["flow"]["plan"]["jobs"][0]["precheck"]


@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("invalid", ["garbage_poscar", "wrong_potcar_species"])
def test_invalid_but_nonempty_input_blocks_both_prechecks_and_submit_card(tmp_path, remote, invalid):
    stack = _stack(tmp_path, remote=remote)
    store, project, task, workspace, hpc, _orch, _config = stack
    name, content = (("POSCAR", b"not a POSCAR\n") if invalid == "garbage_poscar"
                     else ("POTCAR", b"VRHFIN = O: synthetic\nTITEL = PAW_PBE O test\n End of Dataset\n"))
    if remote:
        hpc.files[f"/qa/a/{name}"] = content
    else:
        (workspace / "a" / name).write_bytes(content)
    assert _run_orchestrator_precheck(stack, remote=remote)["ok"] is False
    assert _run_tool_precheck(stack)["ok"] is False
    with pytest.raises(ToolboxError) as error:
        spawn_submit_card(store, project, task, "a", "attempt-qa")
    assert error.value.code == "PRECHECK_BLOCKED"
    assert hpc.submit_count == 0


@pytest.mark.parametrize("remote", [False, True])
def test_valid_synthetic_inputs_pass_both_prechecks_without_auto_submit(tmp_path, remote):
    stack = _stack(tmp_path, remote=remote)
    _store, _project, _task, _workspace, hpc, _orch, _config = stack
    assert _run_orchestrator_precheck(stack, remote=remote)["ok"] is True
    assert _run_tool_precheck(stack)["ok"] is True
    assert hpc.submit_count == 0


@pytest.mark.parametrize("fault", ["truncated", "changed_same_size"])
def test_remote_read_disagreement_fails_closed_before_submit(tmp_path, fault):
    stack = _stack(tmp_path)
    store, project, task, _workspace, hpc, _orch, _config = stack
    original = hpc.read_file

    def faulty_read(path, *, max_bytes=None):
        content = original(path, max_bytes=max_bytes)
        if path.endswith("/POSCAR"):
            return content[:-1] if fault == "truncated" else content[:-2] + b"1\n"
        return content

    hpc.read_file = faulty_read
    checked = _run_orchestrator_precheck(stack, remote=True)
    assert checked["ok"] is False
    assert any(issue["file"] == "POSCAR" and issue["level"] == "error"
               for issue in checked["issues"])
    with pytest.raises(ToolboxError):
        spawn_submit_card(store, project, task, "a", "attempt-qa")
    assert hpc.submit_count == 0
