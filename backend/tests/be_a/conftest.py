"""BE-A 测试公共 fixtures（运行方式：仓库根目录 python -m pytest backend/tests/be_a -q）。"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

from backend.app.recipes.registry import default_registry
from backend.app.schemas.generation import (
    DftuEntry,
    DftuSettings,
    LatticeInfo,
    MaterialAssumptions,
    SchedulerSettings,
    StructureContext,
    WorkflowGenerateRequest,
)
from backend.app.schemas.recipe import ElectronicType, PrecisionLevel, TaskType

FE2O3_POSCAR = """Fe2O3
1.0
5.03 0.0 0.0
-2.515 4.356 0.0
0.0 0.0 13.75
Fe O
2 3
Direct
0.0 0.0 0.0
0.5 0.5 0.5
0.3 0.3 0.25
0.7 0.7 0.5
0.1 0.1 0.75
"""

NACL_POSCAR = """NaCl
1.0
5.6 0.0 0.0
0.0 5.6 0.0
0.0 0.0 5.6
Na Cl
1 1
Direct
0.0 0.0 0.0
0.5 0.5 0.5
"""


@pytest.fixture(scope="session")
def registry_and_pack():
    return default_registry()


@pytest.fixture(scope="session")
def registry(registry_and_pack):
    return registry_and_pack[0]


@pytest.fixture(scope="session")
def pack(registry_and_pack):
    return registry_and_pack[1]


@pytest.fixture()
def fe2o3_structure() -> StructureContext:
    return StructureContext(
        structure_id="str_fe2o3",
        formula="Fe2O3",
        elements=["Fe", "O"],
        counts=[2, 3],
        lattice=LatticeInfo(a=5.03, b=5.03, c=13.75, alpha=90, beta=90, gamma=120),
        poscar_text=FE2O3_POSCAR,
        source_sha256="0" * 64,
        transition_metals=["Fe"],
    )


@pytest.fixture()
def nacl_structure() -> StructureContext:
    return StructureContext(
        structure_id="str_nacl",
        formula="NaCl",
        elements=["Na", "Cl"],
        counts=[1, 1],
        lattice=LatticeInfo(a=5.6, b=5.6, c=5.6, alpha=90, beta=90, gamma=90),
        poscar_text=NACL_POSCAR,
        source_sha256="1" * 64,
    )


@pytest.fixture()
def fe2o3_request(fe2o3_structure) -> WorkflowGenerateRequest:
    """磁性 + DFT+U（Fe 的 U/J 已用户确认）的 Fe2O3 全流程请求。"""

    return WorkflowGenerateRequest(
        workflow_id="wf_fe2o3",
        structure=fe2o3_structure,
        requested_tasks=[TaskType.RELAX, TaskType.STATIC, TaskType.DOS],
        goal_text="优化结构并做静态和 DOS",
        material_assumptions=MaterialAssumptions(
            electronic_type=ElectronicType.METAL, magnetic=True
        ),
        precision=PrecisionLevel.STANDARD,
        dftu=DftuSettings(
            enabled=True,
            entries=[
                DftuEntry(
                    element="Fe",
                    l=2,
                    u_ev=4.0,
                    j_ev=0.0,
                    source_note="用户课题组设置",
                    confirmed_by_user=True,
                )
            ],
        ),
        scheduler=SchedulerSettings(type="slurm", partition="compute"),
    )


@pytest.fixture()
def nacl_request(nacl_structure) -> WorkflowGenerateRequest:
    """简单非金属（半导体、无磁性、无 DFT+U）请求。"""

    return WorkflowGenerateRequest(
        workflow_id="wf_nacl",
        structure=nacl_structure,
        requested_tasks=[TaskType.RELAX, TaskType.STATIC],
        goal_text="结构优化与静态计算",
        material_assumptions=MaterialAssumptions(
            electronic_type=ElectronicType.SEMICONDUCTOR, magnetic=False
        ),
        precision=PrecisionLevel.QUICK,
        scheduler=SchedulerSettings(type="generic"),
    )
