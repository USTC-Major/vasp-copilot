"""Result reads must not inherit the 64 KiB command-output preview limit."""
import pytest

from ai_mode.config import AiModeConfig
from ai_mode.orchestrator import Orchestrator


class LimitedHPC:
    execution_mode = "Fake"

    def __init__(self, outcar):
        self.files = {
            "OUTCAR": outcar,
            "OSZICAR": b"DAV: 5 -10 -1e-7 -2e-7 20 1e-6\n1 F= -10\n",
            "INCAR": b"EDIFF=1e-6\nNSW=0\nIBRION=-1\n",
            "KPOINTS": b"mesh\n0\nGamma\n6 6 6\n0 0 0\n",
        }
        self.limits = []

    def read_file(self, remote, *, max_bytes=None):
        self.limits.append(max_bytes)
        return self.files[remote.rsplit("/", 1)[-1]][:max_bytes or 65536]

    def stat(self, remote):
        return None


HEADER = b"EDIFF=1e-6 IBRION=-1 NSW=0\n"
FOOTER = b"free energy TOTEN = -10\nGeneral timing and accounting informations\n"


@pytest.mark.parametrize("has_footer", [True, False])
def test_large_outcar_completion_requires_actual_footer(has_footer):
    hpc = LimitedHPC(HEADER + b"padding\n" * 12000 + (FOOTER if has_footer else b""))
    orch = Orchestrator(AiModeConfig(), hpc=hpc)
    flow = {"hpc_dir": "/demo", "local_dir": "/local"}
    job = {"key": "si_scf", "kind": "static"}
    orch._finalize_job(flow, job)
    assert job["status"] == ("completed" if has_footer else "unknown")
    assert all(limit is not None and limit > 65536 for limit in hpc.limits)


def test_over_limit_output_never_uses_complete_looking_prefix():
    hpc = LimitedHPC(HEADER + FOOTER + b"x" * (16 * 1024 * 1024))
    orch = Orchestrator(AiModeConfig(), hpc=hpc)
    job = {"key": "si_scf", "kind": "static"}
    orch._finalize_job({"hpc_dir": "/demo", "local_dir": "/local"}, job)
    assert job["status"] == "unknown"
    assert any("读取上限" in item["text"] for item in job["diagnosis"]["evidence"])
