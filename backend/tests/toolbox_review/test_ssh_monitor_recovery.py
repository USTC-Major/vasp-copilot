from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from backend.toolbox.config import ExecutionConfig
from backend.toolbox.monitor import MonitorLoop
from backend.toolbox.orchestrator import Orchestrator
from backend.toolbox.projects import ProjectStore
from backend.toolbox.ssh.connection import SSHManager
from backend.toolbox.ssh.errors import SSHSFTPError


OUTCAR_OK = (
    "ENCUT = 400  EDIFF = 1e-5  IBRION = -1  NSW = 0\n"
    "  free  energy   TOTEN = -10.82044680 eV\n"
    "  General timing and accounting informations for this job:\n"
).encode()
OSZICAR_OK = (
    " DAV:  2    -0.10820447E+02   -5e-7  -4e-8  8  1e-6\n"
    "   1 F= -.10820447E+02 E0= -.10820447E+02 d E = 0.0\n"
).encode()


class _Stream(io.BytesIO):
    @property
    def channel(self):
        return self

    def recv_exit_status(self):
        return 0


@dataclass
class _Remote:
    files: dict[str, bytes] = field(default_factory=dict)
    persistent_sftp_fault: bool = False
    break_current_sftp_once: bool = False
    fault_open_paths: set[str] = field(default_factory=set)
    fault_stat_paths: set[str] = field(default_factory=set)
    queue_output: str = ""
    commands: list[str] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)


class _SFTP:
    def __init__(self, client: "_Client"):
        self.client = client
        self.closed = False
        self.broken = False

    def _check(self):
        if self.closed or self.broken or not self.client.active:
            raise EOFError("synthetic stale SFTP channel")
        remote = self.client.remote
        if remote.persistent_sftp_fault:
            self.broken = True
            raise EOFError("synthetic SFTP outage")
        if remote.break_current_sftp_once:
            remote.break_current_sftp_once = False
            self.broken = True
            raise EOFError("synthetic one-channel failure")

    def open(self, path, mode):
        self._check()
        if path in self.client.remote.fault_open_paths:
            self.broken = True
            raise EOFError("synthetic path read outage")
        if mode != "rb":
            self.client.remote.writes.append(path)
            raise AssertionError("recovery monitor must stay read-only")
        self.client.remote.reads.append(path)
        if path not in self.client.remote.files:
            raise FileNotFoundError(path)
        return io.BytesIO(self.client.remote.files[path])

    def stat(self, path):
        self._check()
        if path in self.client.remote.fault_stat_paths:
            self.broken = True
            raise EOFError("synthetic path stat outage")
        if path not in self.client.remote.files:
            raise FileNotFoundError(path)
        payload = self.client.remote.files[path]
        return type("Stat", (), {"st_size": len(payload), "st_mtime": 1,
                                  "st_mode": 0o100644})()

    def close(self):
        self.closed = True


class _Client:
    def __init__(self, remote: _Remote):
        self.remote = remote
        self.active = True
        self.closed = False
        self.sftps: list[_SFTP] = []

    def connect(self, **_kwargs):
        return None

    def get_transport(self):
        return self

    def is_active(self):
        return self.active

    def set_keepalive(self, _seconds):
        return None

    def exec_command(self, command, **_kwargs):
        self.remote.commands.append(command)
        if not command.startswith("squeue "):
            raise AssertionError(f"unexpected remote command: {command}")
        return None, _Stream(self.remote.queue_output.encode()), _Stream(b"")

    def open_sftp(self):
        sftp = _SFTP(self)
        self.sftps.append(sftp)
        return sftp

    def close(self):
        self.closed = True
        self.active = False


class _Factory:
    def __init__(self, remote: _Remote):
        self.remote = remote
        self.created: list[_Client] = []

    def __call__(self):
        client = _Client(self.remote)
        self.created.append(client)
        return client


def _manager(remote: _Remote):
    factory = _Factory(remote)
    manager = SSHManager(client_factory=factory)
    manager.switch(host="offline.invalid", username="review")
    return manager, factory


def test_transport_reconnect_discards_sftp_owned_by_dead_client():
    remote = _Remote(files={"/result/OUTCAR": OUTCAR_OK})
    manager, factory = _manager(remote)
    try:
        assert manager.read_file("/result/OUTCAR") == OUTCAR_OK
        stale_sftp = factory.created[0].sftps[0]
        factory.created[0].active = False

        assert manager.run("squeue -h -u review")[0] == 0
        assert manager.read_file("/result/OUTCAR") == OUTCAR_OK

        assert len(factory.created) == 2
        assert stale_sftp.closed is True
        assert len(factory.created[1].sftps) == 1
    finally:
        manager.close()


def test_sftp_channel_failure_is_invalidated_for_next_operation():
    remote = _Remote(files={"/result/OUTCAR": OUTCAR_OK})
    manager, factory = _manager(remote)
    try:
        assert manager.read_file("/result/OUTCAR") == OUTCAR_OK
        failed_sftp = factory.created[0].sftps[0]
        remote.break_current_sftp_once = True

        with pytest.raises(SSHSFTPError):
            manager.read_file("/result/OUTCAR")
        assert manager.read_file("/result/OUTCAR") == OUTCAR_OK

        assert failed_sftp.closed is True
        assert len(factory.created) == 1
        assert len(factory.created[0].sftps) == 2
    finally:
        manager.close()


def _seed_monitoring(store: ProjectStore, local_dir: Path):
    project = store.create_project("offline SSH recovery")
    task = store.create_task(project["id"], goal="read-only result recovery")
    store.update_task(project["id"], task["id"], flow={
        "phase": "monitoring",
        "execution_mode": "Real",
        "local_dir": str(local_dir),
        "hpc_dir": "/replay",
        "waiting": [],
        "plan": {"strategy": "single", "jobs": [{
            "key": "static",
            "label": "static",
            "kind": "static",
            "requires": [],
            "status": "running",
            "slurm_id": 1001,
            "submission_state": "submitted",
        }]},
        "monitor": {
            "state": "monitoring",
            "last_success_at": "2026-09-21T00:00:00+00:00",
            "last_error": "",
        },
    })
    return project["id"], task["id"]


def _real_stack(tmp_path: Path, remote: _Remote, *, local_job_dir: bool = True):
    root = tmp_path / "store"
    root.mkdir()
    local = tmp_path / "local"
    local.mkdir(parents=True)
    if local_job_dir:
        (local / "static").mkdir()
    config = ExecutionConfig(
        data_dir=root,
        poll_interval_seconds=10,
        ssh_host="offline.invalid",
        ssh_username="review",
    )
    manager, factory = _manager(remote)
    orchestrator = Orchestrator(config, hpc=manager)
    monitor = MonitorLoop(
        settings_loader=lambda: config,
        orch_factory=lambda _project, _task, _cfg: orchestrator,
    )
    store = ProjectStore(root)
    project_id, task_id = _seed_monitoring(store, local)
    return store, monitor, manager, factory, project_id, task_id


def _put_required_outputs(remote: _Remote):
    remote.files.update({
        "/replay/static/OUTCAR": OUTCAR_OK,
        "/replay/static/OSZICAR": OSZICAR_OK,
        "/replay/static/INCAR": b"EDIFF = 1e-5\nNSW = 0\nIBRION = -1\n",
        "/replay/static/KPOINTS": b"Gamma\n0\nGamma\n1 1 1\n0 0 0\n",
    })


def test_result_channel_error_is_unhealthy_until_same_job_recovers(tmp_path):
    remote = _Remote(persistent_sftp_fault=True)
    _put_required_outputs(remote)
    store, monitor, manager, _factory, project_id, task_id = _real_stack(tmp_path, remote)
    try:
        assert monitor.tick(store) == 1
        failed = store.get_task(project_id, task_id)["flow"]
        job = failed["plan"]["jobs"][0]
        assert job["slurm_id"] == 1001
        assert job["status"] not in {"completed", "failed", "not_converged"}
        assert failed["phase"] == "monitoring"
        assert failed["monitor"]["state"] == "error"
        assert failed["monitor"]["last_error"]
        assert failed["monitor"]["last_success_at"] == "2026-09-21T00:00:00+00:00"
        assert not failed.get("report")

        remote.persistent_sftp_fault = False
        assert monitor.tick(store) == 1
        recovered = store.get_task(project_id, task_id)["flow"]
        job = recovered["plan"]["jobs"][0]
        assert job["slurm_id"] == 1001
        assert job["status"] == "completed"
        assert recovered["phase"] == "done"
        assert recovered["monitor"]["state"] == "idle"
        assert recovered["monitor"]["last_error"] == ""
        assert recovered["monitor"]["last_success_at"] != "2026-09-21T00:00:00+00:00"
        assert recovered["extractions"]["static"]["outcar"]["final_energy"] == pytest.approx(-10.82044680)
        assert recovered["report"]
        assert all(command.startswith("squeue ") for command in remote.commands)
        assert not remote.writes
    finally:
        monitor.stop()
        manager.close()


def test_missing_optional_chgcar_does_not_block_completed_report(tmp_path):
    remote = _Remote(files={
        "/replay/static/OUTCAR": OUTCAR_OK,
        "/replay/static/OSZICAR": OSZICAR_OK,
        "/replay/static/INCAR": b"EDIFF = 1e-5\nNSW = 0\nIBRION = -1\n",
        "/replay/static/KPOINTS": b"Gamma\n0\nGamma\n1 1 1\n0 0 0\n",
    })
    store, monitor, manager, _factory, project_id, task_id = _real_stack(tmp_path, remote)
    try:
        assert monitor.tick(store) == 1
        flow = store.get_task(project_id, task_id)["flow"]
        assert flow["plan"]["jobs"][0]["status"] == "completed"
        assert flow["phase"] == "done"
        assert flow["monitor"]["state"] == "idle"
        assert flow["monitor"]["last_error"] == ""
        assert flow["extractions"]["static"]["outcar"]["final_energy"] == pytest.approx(-10.82044680)
        assert flow["report"]
        assert not remote.writes
    finally:
        monitor.stop()
        manager.close()


def test_missing_result_file_is_not_network_error_and_can_appear_next_round(tmp_path):
    remote = _Remote(files={
        "/replay/static/OUTCAR": OUTCAR_OK,
        "/replay/static/INCAR": b"EDIFF = 1e-5\nNSW = 0\nIBRION = -1\n",
        "/replay/static/KPOINTS": b"Gamma\n0\nGamma\n1 1 1\n0 0 0\n",
    })
    store, monitor, manager, _factory, project_id, task_id = _real_stack(tmp_path, remote)
    try:
        assert monitor.tick(store) == 1
        incomplete = store.get_task(project_id, task_id)["flow"]
        assert incomplete["plan"]["jobs"][0]["status"] == "unknown"
        assert incomplete["phase"] == "monitoring"
        assert incomplete["monitor"]["state"] == "monitoring"
        assert incomplete["monitor"]["last_error"] == ""
        assert not incomplete.get("report")

        remote.files["/replay/static/OSZICAR"] = OSZICAR_OK
        assert monitor.tick(store) == 1
        recovered = store.get_task(project_id, task_id)["flow"]
        assert recovered["plan"]["jobs"][0]["status"] == "completed"
        assert recovered["phase"] == "done"
        assert recovered["extractions"]["static"]["outcar"]["final_energy"] == pytest.approx(-10.82044680)
        assert recovered["report"]
        assert all(command.startswith("squeue ") for command in remote.commands)
        assert not remote.writes
    finally:
        monitor.stop()
        manager.close()


def test_job_directory_transport_error_never_falls_back_to_parent_outputs(tmp_path):
    remote = _Remote(
        files={
            "/replay/OUTCAR": OUTCAR_OK,
            "/replay/OSZICAR": OSZICAR_OK,
            "/replay/INCAR": b"EDIFF = 1e-5\nNSW = 0\nIBRION = -1\n",
            "/replay/KPOINTS": b"Gamma\n0\nGamma\n1 1 1\n0 0 0\n",
        },
        fault_stat_paths={"/replay/static"},
    )
    stack = _real_stack(tmp_path, remote, local_job_dir=False)
    store, monitor, manager, _factory, project_id, task_id = stack
    try:
        assert monitor.tick(store) == 1
        flow = store.get_task(project_id, task_id)["flow"]
        job = flow["plan"]["jobs"][0]
        assert job["status"] == "unknown"
        assert flow["phase"] == "monitoring"
        assert flow["monitor"]["state"] == "error"
        assert flow["monitor"]["last_success_at"] == "2026-09-21T00:00:00+00:00"
        assert flow["extractions"]["static"]["outcar"]["final_energy"] is None
        assert "/replay/OUTCAR" not in remote.reads
        assert not remote.writes
    finally:
        monitor.stop()
        manager.close()


def test_missing_job_directory_keeps_compatible_flat_workspace_fallback(tmp_path):
    remote = _Remote(files={
        "/replay/OUTCAR": OUTCAR_OK,
        "/replay/OSZICAR": OSZICAR_OK,
        "/replay/INCAR": b"EDIFF = 1e-5\nNSW = 0\nIBRION = -1\n",
        "/replay/KPOINTS": b"Gamma\n0\nGamma\n1 1 1\n0 0 0\n",
    })
    stack = _real_stack(tmp_path, remote, local_job_dir=False)
    store, monitor, manager, _factory, project_id, task_id = stack
    try:
        assert monitor.tick(store) == 1
        flow = store.get_task(project_id, task_id)["flow"]
        assert flow["plan"]["jobs"][0]["status"] == "completed"
        assert flow["phase"] == "done"
        assert flow["monitor"]["state"] == "idle"
        assert flow["monitor"]["last_error"] == ""
        assert "/replay/OUTCAR" in remote.reads
        assert flow["report"]
        assert not remote.writes
    finally:
        monitor.stop()
        manager.close()


def test_one_job_result_failure_keeps_whole_monitor_round_unhealthy(tmp_path):
    remote = _Remote()
    for key in ("bad", "good"):
        remote.files.update({
            f"/replay/{key}/OUTCAR": OUTCAR_OK,
            f"/replay/{key}/OSZICAR": OSZICAR_OK,
            f"/replay/{key}/INCAR": b"EDIFF = 1e-5\nNSW = 0\nIBRION = -1\n",
            f"/replay/{key}/KPOINTS": b"Gamma\n0\nGamma\n1 1 1\n0 0 0\n",
        })
    remote.fault_open_paths = {
        f"/replay/bad/{name}" for name in ("OUTCAR", "OSZICAR", "INCAR", "KPOINTS")
    }
    store, monitor, manager, _factory, project_id, task_id = _real_stack(tmp_path, remote)
    local = Path(store.get_task(project_id, task_id)["flow"]["local_dir"])
    (local / "bad").mkdir()
    (local / "good").mkdir()
    flow = store.get_task(project_id, task_id)["flow"]
    flow["plan"]["jobs"] = [
        {"key": key, "label": key, "kind": "static", "requires": [],
         "status": "running", "slurm_id": sid, "submission_state": "submitted"}
        for key, sid in (("bad", 1001), ("good", 1002))
    ]
    store.update_task(project_id, task_id, flow=flow)
    try:
        assert monitor.tick(store) == 1
        failed = store.get_task(project_id, task_id)["flow"]
        jobs = {job["key"]: job for job in failed["plan"]["jobs"]}
        assert jobs["bad"]["status"] == "unknown"
        assert jobs["good"]["status"] == "completed"
        assert failed["monitor"]["state"] == "error"
        assert failed["monitor"]["last_success_at"] == "2026-09-21T00:00:00+00:00"

        remote.fault_open_paths.clear()
        assert monitor.tick(store) == 1
        recovered = store.get_task(project_id, task_id)["flow"]
        assert [job["status"] for job in recovered["plan"]["jobs"]] == [
            "completed", "completed"]
        assert recovered["phase"] == "done"
        assert recovered["monitor"]["state"] == "idle"
        assert recovered["monitor"]["last_error"] == ""
        assert recovered["report"]
        assert all(command.startswith("squeue ") for command in remote.commands)
        assert not remote.writes
    finally:
        monitor.stop()
        manager.close()


def test_running_queue_state_does_not_clear_unresolved_result_read_error(tmp_path):
    remote = _Remote(persistent_sftp_fault=True)
    _put_required_outputs(remote)
    store, monitor, manager, _factory, project_id, task_id = _real_stack(tmp_path, remote)
    try:
        assert monitor.tick(store) == 1
        first = store.get_task(project_id, task_id)["flow"]
        assert first["plan"]["jobs"][0]["status"] == "unknown"
        assert first["monitor"]["state"] == "error"
        first_error = first["monitor"]["last_error"]

        remote.persistent_sftp_fault = False
        remote.queue_output = "1001 review static review R node01"
        assert monitor.tick(store) == 1
        running = store.get_task(project_id, task_id)["flow"]
        assert running["plan"]["jobs"][0]["status"] == "running"
        assert running["monitor"]["state"] == "error"
        assert running["monitor"]["last_error"] == first_error
        assert running["monitor"]["last_success_at"] == "2026-09-21T00:00:00+00:00"
        assert not running.get("report")

        remote.queue_output = ""
        assert monitor.tick(store) == 1
        recovered = store.get_task(project_id, task_id)["flow"]
        assert recovered["plan"]["jobs"][0]["status"] == "completed"
        assert recovered["phase"] == "done"
        assert recovered["monitor"]["state"] == "idle"
        assert recovered["monitor"]["last_error"] == ""
        assert recovered["report"]
        assert all(command.startswith("squeue ") for command in remote.commands)
        assert not remote.writes
    finally:
        monitor.stop()
        manager.close()
