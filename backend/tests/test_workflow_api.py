"""Workflow API 集成测试（IR-01/IR-03/IR-05）。

覆盖 /api/v1/workflows/{plan,generate,download}：从已存 doctor run 解析结构
（POSCAR -> StructureContext）、带待确认项的 plan 预览、确定性状态、
zip 下载，以及 BE-A 错误到统一错误封装的映射。"""

from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.v1 import deps
from app.main import app
from app.schemas.detected import DetectedFile, DetectedRun

client = TestClient(app)

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


def _seed_run(diag_id: str, poscar: str = FE2O3_POSCAR) -> str:
    dest = Path(tempfile.mkdtemp(prefix="wf_test_")) / diag_id
    dest.mkdir(parents=True)
    if poscar is not None:
        (dest / "POSCAR").write_text(poscar, encoding="utf-8")
    detected = DetectedRun(
        root=diag_id,
        files=[DetectedFile(name="POSCAR", kind="poscar",
                            size=0, path="POSCAR")],
        missing_recommended=[],
        candidate_job_logs=[],
    )
    deps.store.create(diag_id, detected, dest)
    return diag_id


def _relax_static(wf_id: str = "wf_local") -> dict:
    return {"workflow": {
        "workflow_id": wf_id,
        "requested_tasks": ["relax", "static"],
        "goal_text": "relax then static",
        "confirm": True,
    }}


def test_plan_from_diagnosis_returns_confirmations():
    diag_id = _seed_run("diag_plan_ok")
    r = client.post("/api/v1/workflows/plan",
                    json={"diagnosis_id": diag_id, **_relax_static("wf_plan_ok")})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["workflow_id"]
    assert [s["task"] for s in data["steps"]] == ["relax", "static"]
    assert "file_inheritance_plan" in data
    assert "warnings" in data
    if data["needs_confirmation"]:
        assert data["status"] == "needs_confirmation"
        assert data["confirmations"]


def test_plan_with_explicit_structure():
    payload = {
        "workflow": {
            "structure": {
                "formula": "Fe2O3",
                "elements": ["Fe", "O"],
                "counts": [2, 3],
                "poscar_text": FE2O3_POSCAR,
                "transition_metals": ["Fe"],
            },
            "requested_tasks": ["relax"],
        }
    }
    r = client.post("/api/v1/workflows/plan", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["workflow_id"]


def test_generate_and_download_roundtrip():
    diag_id = _seed_run("diag_gen_ok")
    g = client.post("/api/v1/workflows/generate",
                    json={"diagnosis_id": diag_id, **_relax_static("wf_gen_ok")})
    assert g.status_code == 200, g.text
    data = g.json()["data"]
    assert data["workflow_status"] == "generated"
    url = data["download_url"]
    assert url.startswith("/api/v1/workflows/")

    d = client.get(url)
    assert d.status_code == 200
    assert d.headers["content-type"].startswith("application/zip")
    zf = zipfile.ZipFile(io.BytesIO(d.content))
    names = zf.namelist()
    for expected in ("workflow_plan.json", "workflow_manifest.json",
                     "README_run_order.md", "01_relax/POSCAR",
                     "01_relax/INCAR", "01_relax/KPOINTS", "01_relax/submit.sh"):
        assert expected in names, expected
    assert any("02_static/POSCAR" in n for n in names)


def test_generate_without_confirmation_is_409():
    diag_id = _seed_run("diag_conf_needed")
    payload = {"diagnosis_id": diag_id, "workflow": {
        "requested_tasks": ["relax", "static"],
        "confirm": False,
    }}
    r = client.post("/api/v1/workflows/generate", json=payload)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "RECIPE_CONFIRMATION_REQUIRED"


def test_dftu_unconfirmed_is_409():
    diag_id = _seed_run("diag_dftu")
    payload = {"diagnosis_id": diag_id, "workflow": {
        "requested_tasks": ["relax"],
        "confirm": True,
        "dftu": {"enabled": True, "entries": []},
    }}
    r = client.post("/api/v1/workflows/generate", json=payload)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "DFTU_CONFIRMATION_REQUIRED"


def test_missing_diagnosis_404():
    r = client.post("/api/v1/workflows/plan", json={"diagnosis_id": "diag_nope"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "DIAGNOSIS_NOT_FOUND"


def test_no_structure_required_409():
    r = client.post("/api/v1/workflows/plan", json={})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "STRUCTURE_REQUIRED"


def test_download_unknown_workflow_404():
    r = client.get("/api/v1/workflows/wf_nope/download")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "WORKFLOW_NOT_FOUND"


def test_diagnosis_without_poscar_404():
    diag_id = _seed_run("diag_no_poscar", poscar=None)
    r = client.post("/api/v1/workflows/plan", json={"diagnosis_id": diag_id})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "STRUCTURE_NOT_FOUND"

def test_plan_returns_workflow_status_alias():
    diag_id = _seed_run("diag_plan_alias")
    r = client.post("/api/v1/workflows/plan",
                    json={"diagnosis_id": diag_id, **_relax_static("wf_plan_alias")})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["workflow_status"] == data["status"]


def test_get_planned_workflow_metadata():
    diag_id = _seed_run("diag_plan_get")
    r = client.post("/api/v1/workflows/plan",
                    json={"diagnosis_id": diag_id, **_relax_static("wf_plan_get")})
    assert r.status_code == 200, r.text
    wf_id = r.json()["data"]["workflow_id"]
    g = client.get(f"/api/v1/workflows/{wf_id}")
    assert g.status_code == 200, g.text
    data = g.json()["data"]
    assert data["workflow_id"] == wf_id
    assert data["workflow_status"] in ("planned", "needs_confirmation")
    assert data["plan"]["schema_version"] == "1.0"
    assert [s["task"] for s in data["plan"]["steps"]] == ["relax", "static"]
    assert "confirmations" in data and "warnings" in data


def test_get_generated_workflow_metadata():
    diag_id = _seed_run("diag_gen_get")
    g = client.post("/api/v1/workflows/generate",
                    json={"diagnosis_id": diag_id, **_relax_static("wf_gen_get")})
    assert g.status_code == 200, g.text
    wf_id = g.json()["data"]["workflow_id"]
    meta = client.get(f"/api/v1/workflows/{wf_id}")
    assert meta.status_code == 200, meta.text
    data = meta.json()["data"]
    assert data["workflow_status"] == "generated"
    assert isinstance(data["revision"], int)
    assert "file_tree" in data
    assert data["validation"]["valid"] is True
    assert data["download_url"].endswith(f"{wf_id}/download")


def test_get_unknown_workflow_404():
    r = client.get("/api/v1/workflows/wf_nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "WORKFLOW_NOT_FOUND"


# ---------------------------------------------------------------------------
# B1-B5：v0.1.2 第一阶段 工作流参数链路契约测试（6.4 节回显/一致性）
# ---------------------------------------------------------------------------


def _confirmed_dftu_scheduler_payload() -> dict:
    return {"workflow": {
        "requested_tasks": ["relax"],
        "goal_text": "relax with DFT+U",
        "confirm": True,
        "dftu": {
            "enabled": True,
            "entries": [{
                "element": "Fe", "l": 2, "u_ev": 5.3, "j_ev": 0.0,
                "source_note": "user_input", "confirmed_by_user": True,
            }],
        },
        "scheduler": {
            "type": "slurm", "nodes": 2, "tasks_per_node": 48,
            "walltime": "08:00:00", "vasp_binary_hint": "vasp_gam",
        },
    }}


def test_plan_echoes_confirmed_dftu_and_scheduler():
    """B1：嵌套请求的 dftu/scheduler 必须原样回显在 plan 响应中。"""
    diag_id = _seed_run("diag_echo_plan")
    r = client.post("/api/v1/workflows/plan", json={
        "diagnosis_id": diag_id,
        **_confirmed_dftu_scheduler_payload(),
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["dftu"]["enabled"] is True
    fe = data["dftu"]["entries"][0]
    assert (fe["element"], fe["l"], fe["u_ev"], fe["j_ev"]) == ("Fe", 2, 5.3, 0.0)
    assert fe["source_note"] == "user_input"
    assert fe["confirmed_by_user"] is True
    sched = data["scheduler"]
    assert sched["scheduler_type"] == "slurm"
    assert sched["nodes"] == 2 and sched["tasks_per_node"] == 48
    assert sched["walltime"] == "08:00:00"
    assert sched["vasp_binary_hint"] == "vasp_gam"


def test_plan_echo_matches_get_workflow_plan():
    """B2：POST plan 与 GET workflow 的 dftu/scheduler 必须完全一致。"""
    diag_id = _seed_run("diag_echo_get")
    p = client.post("/api/v1/workflows/plan", json={
        "diagnosis_id": diag_id,
        **_confirmed_dftu_scheduler_payload(),
    })
    assert p.status_code == 200, p.text
    post_data = p.json()["data"]
    wf_id = post_data["workflow_id"]
    g = client.get(f"/api/v1/workflows/{wf_id}")
    assert g.status_code == 200, g.text
    plan = g.json()["data"]["plan"]
    assert plan["dftu"] == post_data["dftu"]
    assert plan["scheduler"] == post_data["scheduler"]


def test_generate_replay_preserves_confirmed_parameters():
    """B3：plan 后仅凭 workflow_id 回放生成，产物必须与确认参数一致。"""
    diag_id = _seed_run("diag_echo_replay")
    p = client.post("/api/v1/workflows/plan", json={
        "diagnosis_id": diag_id,
        **_confirmed_dftu_scheduler_payload(),
    })
    assert p.status_code == 200, p.text
    post_data = p.json()["data"]
    wf_id = post_data["workflow_id"]
    g = client.post("/api/v1/workflows/generate", json={"workflow_id": wf_id})
    assert g.status_code == 200, g.text
    d = client.get(g.json()["data"]["download_url"])
    assert d.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(d.content))
    import json as _json
    plan_file = _json.loads(zf.read("workflow_plan.json"))
    assert plan_file["dftu"] == post_data["dftu"]
    assert plan_file["scheduler"]["scheduler_type"] == post_data["scheduler"]["scheduler_type"]
    assert plan_file["scheduler"]["nodes"] == post_data["scheduler"]["nodes"]
    assert plan_file["scheduler"]["walltime"] == post_data["scheduler"]["walltime"]
    # INCAR 精确解析（复用现有 IncarParser，禁止宽泛子串碰巧匹配）：
    # POSCAR 元素顺序为 Fe、O，仅 Fe 配置了条目；未配置的 O 保持派生 0。
    from app.generators.serializer import IncarParser
    incar_params = IncarParser().parse(zf.read("01_relax/INCAR").decode("utf-8"))
    assert incar_params["LDAU"] is True
    assert incar_params["LDAUL"] == [2, -1]          # Fe=d(2)，O 未配置→派生 -1
    assert incar_params["LDAUU"] == [5.3, 0.0]       # Fe 确认值 5.3，O 派生 0
    assert incar_params["LDAUJ"] == [0.0, 0.0]       # Fe 确认 J=0，O 派生 0
    poscar_elements = zf.read("01_relax/POSCAR").decode("utf-8").splitlines()[5].split()
    assert len(incar_params["LDAUL"]) == len(poscar_elements) == 2
    assert len(incar_params["LDAUU"]) == len(poscar_elements)
    assert len(incar_params["LDAUJ"]) == len(poscar_elements)
    # submit.sh 逐行精确验证（资源行与启动行均为确认值）。
    submit = zf.read("01_relax/submit.sh").decode("utf-8")
    submit_lines = [line.strip() for line in submit.splitlines()]
    assert "#SBATCH --nodes=2" in submit_lines
    assert "#SBATCH --ntasks-per-node=48" in submit_lines
    assert "#SBATCH --time=08:00:00" in submit_lines
    # 实际启动行：slurm profile 用 srun 启动确认的 vasp_gam（精确整行匹配）。
    assert "srun vasp_gam" in submit_lines


def test_unconfirmed_dftu_entry_generate_is_409():
    """B4：条目存在但未经用户确认时，generate 必须 fail-closed。"""
    diag_id = _seed_run("diag_echo_unconfirmed")
    payload = {"diagnosis_id": diag_id, "workflow": {
        "requested_tasks": ["relax"],
        "confirm": True,
        "dftu": {"enabled": True, "entries": [{
            "element": "Fe", "l": 2, "u_ev": 5.3, "j_ev": 0.0,
            "source_note": "user_input", "confirmed_by_user": False,
        }]},
    }}
    r = client.post("/api/v1/workflows/generate", json=payload)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "DFTU_CONFIRMATION_REQUIRED"


def test_legacy_toplevel_goals_assumptions_still_works():
    """B5：旧 top-level goals/assumptions 请求路径保持可用。"""
    diag_id = _seed_run("diag_echo_legacy")
    r = client.post("/api/v1/workflows/plan", json={
        "diagnosis_id": diag_id,
        "goals": ["relax"],
        "assumptions": {"electronic_type": "semiconductor",
                        "magnetic": True, "soc": False, "precision": "standard"},
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert [s["task"] for s in data["steps"]] == ["relax"]
    # 未显式提供时回显为后端默认值（dftu 关闭）
    assert data["dftu"]["enabled"] is False and data["dftu"]["entries"] == []
    assert data["scheduler"]["scheduler_type"] == "slurm"
