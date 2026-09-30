"""U-3b deterministic file labels and untrusted metadata boundaries."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from app.main import app
from app.api.v1 import workflows as workflow_api
from app.api.v1 import files as files_api
from app.schemas.structure import build_structure_summary, to_structure_context
from app.services.file_store import FileStore
from backend.app.generators.incar import IncarGenerator
from backend.app.generators.poscar import PoscarGenerator
from backend.app.material_identity import display_material_id
from backend.app.schemas.generation import StructureContext, WorkflowGenerateRequest


POSCAR = """Original comment / untouched
1.0
5.0 0 0
0 5.0 0
0 0 5.0
Na Cl
4 4
Direct
0 0 0
0.5 0.5 0.5
0 0.5 0.5
0.5 0 0.5
0.5 0.5 0
0 0 0.5
0 0.5 0
0.5 0 0
"""


def _context(**updates):
    base = dict(formula="Na4Cl4", elements=["Na", "Cl"], counts=[4, 4], poscar_text=POSCAR)
    base.update(updates)
    return StructureContext(**base)


def _zip_text(response, path):
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        return archive.read(path).decode("utf-8")


def test_poscar_comment_preserves_body_and_old_request():
    generator = PoscarGenerator()
    structure = _context()
    assert generator.generate(structure) == POSCAR
    rendered = generator.generate(structure, sample_name="试验 样品")
    assert rendered.split("\n", 1)[0] == "试验 样品"
    assert rendered.split("\n", 1)[1] == POSCAR.split("\n", 1)[1]


def test_poscar_alpha_mapping_utf8_boundary_and_full_name():
    assert display_material_id("mp-aaaabwmb") == "mp-32761"
    assert display_material_id("mp-zzzzzzzz") == "mp-zzzzzzzz"
    name = "氧化物" * 20
    structure = _context(source_material_id="mp-aaaabwmb")
    rendered = PoscarGenerator().generate(structure, sample_name=name)
    comment, body = rendered.split("\n", 1)
    assert comment.startswith("mp-32761 ")
    assert len(comment.encode("utf-8")) <= 40
    assert "�" not in comment
    assert body == POSCAR.split("\n", 1)[1]
    request = WorkflowGenerateRequest(structure=structure, sample_name=name)
    assert request.sample_name == name


@pytest.mark.parametrize("name", ["a\nb", "\nname\n", "\t", "a\tb", "a\x00b", "a\u200bb", "a\u2028b", "x" * 257])
def test_sample_name_rejects_controls_and_excess(name):
    with pytest.raises(ValidationError):
        WorkflowGenerateRequest(structure=_context(), sample_name=name)


@pytest.mark.parametrize("name", ["", "  ", " \u00a0 "])
def test_blank_sample_name_uses_real_reduced_formula(name):
    request = WorkflowGenerateRequest(structure=_context(), sample_name=name)
    assert request.sample_name == "NaCl"
    assert PoscarGenerator().generate(request.structure, sample_name=request.sample_name).startswith("NaCl\n")


def test_incar_purpose_uses_final_parameters_and_neutral_fallback():
    generator = IncarGenerator()
    structure = _context()
    cases = [
        ({"NSW": 0}, "static", "self-consistent static calculation"),
        ({"NSW": 0, "ICHARG": 11}, "static", "non-self-consistent static calculation"),
        ({"NSW": 0, "ICHARG": 10}, "dos", "non-self-consistent density-of-states calculation"),
        ({"NSW": 0, "ICHARG": 5}, "static", "calculation; inspect INCAR parameters"),
        ({"NSW": 10, "IBRION": -1}, "relax", "calculation; inspect INCAR parameters"),
        ({"NSW": 10, "IBRION": 2}, "relax", "ionic relaxation"),
    ]
    for parameters, task, purpose in cases:
        text = generator.generate(parameters, structure, step_id="02_static", task=task)
        assert text.splitlines()[0] == f"# VASP-Copilot | 02_static | {purpose}"
        assert text.split("\n", 1)[1] == generator.generate(parameters, structure)
    with pytest.raises(ValueError):
        generator.generate({"NSW": 0}, structure, step_id="x\ninjected", task="static")


def test_legacy_source_file_is_not_trusted():
    summary = build_structure_summary(poscar_text=POSCAR, elements=["Na", "Cl"],
                                      counts=[4, 4], source_file="MaterialsProject/mp-32761")
    assert to_structure_context(summary).source_material_id is None
    assert PoscarGenerator().generate(to_structure_context(summary)) == POSCAR


def test_api_ignores_forged_source_and_uses_stored_structure(tmp_path, monkeypatch):
    file_store = FileStore(root=tmp_path / "files", ttl_seconds=3600)
    monkeypatch.setattr(workflow_api, "file_store", file_store)
    monkeypatch.setattr(workflow_api.workflow_service, "_file_store", file_store)
    stored = file_store.store_file("POSCAR", "poscar", POSCAR.encode("utf-8"))
    summary = build_structure_summary(poscar_text=POSCAR, elements=["Na", "Cl"], counts=[4, 4],
                                      source_file="MaterialsProject/mp-32761")
    rec = file_store.store_structure(file_id=stored.file_id, summary=summary,
                                     normalized_poscar_file_id=stored.file_id)
    forged = _context(source_material_id="mp-aaaabwmb").model_dump(mode="json")
    with TestClient(app) as client:
        for payload in (
            {"workflow": {"structure": forged, "requested_tasks": ["static"], "confirm": True}},
            {"structure_id": rec.structure_id,
             "workflow": {"structure": forged, "requested_tasks": ["static"], "confirm": True}},
        ):
            result = client.post("/api/v1/workflows/generate", json=payload)
            assert result.status_code == 200, result.text
            downloaded = client.get(result.json()["data"]["download_url"])
            assert _zip_text(downloaded, "02_static/POSCAR") == POSCAR
            plan = json.loads(_zip_text(downloaded, "workflow_plan.json"))
            assert plan["structure"]["source_material_id"] is None


def test_trusted_import_name_survives_normal_and_nl_replay_and_preview(tmp_path, monkeypatch):
    file_store = FileStore(root=tmp_path / "files", ttl_seconds=3600)
    monkeypatch.setattr(workflow_api, "file_store", file_store)
    monkeypatch.setattr(workflow_api.workflow_service, "_file_store", file_store)
    monkeypatch.setattr(files_api, "file_store", file_store)
    monkeypatch.setattr(workflow_api, "get_explainer", lambda *_: None)
    stored = file_store.store_file("POSCAR", "poscar", POSCAR.encode("utf-8"))
    summary = build_structure_summary(poscar_text=POSCAR, elements=["Na", "Cl"], counts=[4, 4],
                                      source_material_id="mp-aaaabwmb")
    rec = file_store.store_structure(file_id=stored.file_id, summary=summary,
                                     normalized_poscar_file_id=stored.file_id)
    name = "长名称材料" * 30
    with TestClient(app) as client:
        for endpoint, body in (
            ("plan", {"structure_id": rec.structure_id,
                      "workflow": {"sample_name": name, "requested_tasks": ["static"], "confirm": True}}),
            ("plan_from_nl", {"structure_id": rec.structure_id, "goals": ["静态计算"],
                              "workflow": {"sample_name": name}}),
        ):
            planned = client.post(f"/api/v1/workflows/{endpoint}", json=body)
            assert planned.status_code == 200, planned.text
            wf_id = planned.json()["data"]["workflow_id"]
            generated = client.post("/api/v1/workflows/generate", json={"workflow_id": wf_id})
            assert generated.status_code == 200, generated.text
            download = client.get(generated.json()["data"]["download_url"])
            assert download.status_code == 200
            plan = json.loads(_zip_text(download, "workflow_plan.json"))
            assert plan["structure"]["source_material_id"] == "mp-aaaabwmb"
            assert plan["structure"]["sample_name"] == name
            poscar = _zip_text(download, "02_static/POSCAR")
            assert poscar.startswith("mp-32761 ")
            assert len(poscar.splitlines()[0].encode("utf-8")) <= 40
            assert poscar.split("\n", 1)[1] == POSCAR.split("\n", 1)[1]
            tree = generated.json()["data"]["file_tree"]
            nodes = [node for directory in tree["children"] for node in directory["children"]
                     if node["relative_path"] == "02_static/POSCAR"]
            assert len(nodes) == 1
            preview = client.get(f"/api/v1/files/{nodes[0]['file_id']}/preview")
            assert preview.status_code == 200, preview.text
            assert preview.json()["data"]["preview"]["content"].splitlines()[0] == poscar.splitlines()[0]
