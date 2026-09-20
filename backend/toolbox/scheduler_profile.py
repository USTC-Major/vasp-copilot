"""Explicit scheduler protocols. Never probe by submitting or retry another CLI.

ParaCloud shapes were checked read-only against cbatch binary receipt string,
cqueue header and cacct historical record on 2026-09-16. Not CraneSched cbatch.
"""
from __future__ import annotations

import re

PROFILES = {"slurm": ("sbatch", "squeue", "scancel"),
            "paracloud": ("cbatch", "cqueue", "ccancel")}
CLOUD_ID = r"[A-Z][A-Z0-9_]{0,31}-I[0-9]+"
SLURM_ID = r"[0-9][0-9_\[\],%-]*"
TERMINAL = {"CD", "COMPLETED", "F", "FAILED", "CA", "CANCELLED", "TO",
            "TIMEOUT", "OOM", "OUT_OF_MEMORY", "NF", "NODE_FAIL"}
PENDING = {"PD", "PENDING", "RD", "UPLOAD"}
RUNNING = {"R", "RUN", "RUNNING", "WR", "DOWNLOAD", "CG", "COMPLETING"}


def profile(name: str) -> tuple[str, str, str]:
    if name not in PROFILES:
        raise ValueError("unsupported scheduler profile")
    return PROFILES[name]


def target_binding(cfg) -> dict:
    profile(cfg.scheduler_backend)
    return {"scheduler": cfg.scheduler_backend, "host": cfg.ssh_host,
            "port": cfg.ssh_port, "username": cfg.ssh_username,
            "identity_file": cfg.ssh_identity_file,
            "known_hosts_path": cfg.ssh_known_hosts_path}


def submit_argv(script: str, backend: str = "slurm") -> list[str]:
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\.sh", script):
        raise ValueError("invalid submission script name")
    return [profile(backend)[0], script]


def queue_command(backend: str, account: str) -> str:
    command = profile(backend)[1]
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", account or ""):
        raise ValueError("invalid scheduler account")
    # ParaCloud cqueue is scoped by the authenticated cloud session and does
    # not accept -u. The SSH username suffix is NOT the compute-node username.
    return command if backend == "paracloud" else f"{command} -u {account}"


def parse_receipt(backend: str, code: int, stdout: str) -> int | str:
    profile(backend)
    pattern = CLOUD_ID if backend == "paracloud" else r"[0-9]+"
    matches = re.findall(rf"^Submitted batch job ({pattern})\s*$", stdout or "", re.M)
    if code != 0 or len(matches) != 1:
        raise RuntimeError("提交回执缺失、失败或不唯一；结果未知，禁止自动重提")
    return matches[0] if backend == "paracloud" else int(matches[0])


def parse_queue(backend: str, stdout: str) -> dict[str, str]:
    profile(backend)
    cloud = backend == "paracloud"
    pattern = CLOUD_ID if cloud else SLURM_ID
    states = {}
    header = False
    for line in (stdout or "").splitlines():
        tokens = line.split()
        if not tokens:
            continue
        if tokens[0] == "JOBID":
            if len(tokens) < 5 or tokens[4] != "ST":
                raise ValueError("unrecognized queue header")
            header = True
            continue
        if len(tokens) < 5 or not re.fullmatch(pattern, tokens[0]):
            raise ValueError("unrecognized queue row")
        if tokens[0] in states or not re.fullmatch(r"[A-Za-z_]+", tokens[4]):
            raise ValueError("ambiguous queue row")
        states[tokens[0]] = tokens[4].upper()
    if cloud and not header:
        raise ValueError("missing ParaCloud queue header")
    return states


def occupied(states: dict[str, str]) -> int:
    # Includes unknown/transferring/completing states: no accidental free slots.
    return sum(state not in TERMINAL for state in states.values())


def accounting_command(job_id: str) -> str:
    if not re.fullmatch(CLOUD_ID, job_id):
        raise ValueError("invalid ParaCloud job id")
    return f"cacct -j {job_id}"


def parse_accounting(job_id: str, stdout: str) -> tuple[str, str]:
    accounting_command(job_id)
    matches = []
    for line in (stdout or "").splitlines():
        tokens = line.split()
        if tokens and tokens[0] == job_id:
            if len(tokens) != 7 or not re.fullmatch(r"[0-9]+:[0-9]+", tokens[-1]):
                raise ValueError("unrecognized accounting row")
            matches.append((tokens[-2].upper(), tokens[-1]))
    if len(matches) != 1:
        raise ValueError("missing or ambiguous accounting record")
    state, exit_code = matches[0]
    if state in TERMINAL and exit_code != "0:0":
        return "FAILED", exit_code
    return state, exit_code
