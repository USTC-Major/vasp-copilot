"""HTTP preview contract: opaque IDs, immutable contents and legacy fail-closed."""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.v1 import files, workflows
from app.main import app
from app.services.file_store import FileStore
from backend.app.core.file_identity import generated_file_id
from backend.app.services.workflow_service import WorkflowService


def test_legacy_generated_preview_404_without_deleting_persisted_bytes(tmp_path, monkeypatch):
    root = tmp_path / "files"
    store = FileStore(root)
    record = store.register_file("file_01", "POSCAR", "generated", b"ambiguous legacy content\n")
    index_before = (tmp_path / "structs.index.json").read_bytes()
    monkeypatch.setattr(files, "file_store", FileStore(root))
    response = TestClient(app).get("/api/v1/files/file_01/preview")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FILE_NOT_FOUND"
    assert response.json()["error"]["message"] == "旧版生成文件预览无法确认归属，请重新生成工作流"
    assert record.path.read_bytes() == b"ambiguous legacy content\n"
    assert (tmp_path / "structs.index.json").read_bytes() == index_before


def test_same_id_shaped_upload_remains_previewable(tmp_path, monkeypatch):
    store = FileStore(tmp_path / "files")
    store.register_file("file_01", "notes.txt", "file", b"ordinary upload\n")
    monkeypatch.setattr(files, "file_store", store)
    response = TestClient(app).get("/api/v1/files/file_01/preview")
    assert response.status_code == 200
    assert response.json()["data"]["preview"]["content"] == "ordinary upload"


def test_generated_id_survives_storage_reload_and_endpoint_returns_bound_bytes(tmp_path, monkeypatch):
    root = tmp_path / "files"
    first = b"ENCUT = 520\n"
    second = b"ENCUT = 650\n"
    store = FileStore(root)
    first_id = generated_file_id("wf_a", "01_relax/INCAR", first)
    second_id = generated_file_id("wf_a", "01_relax/INCAR", second)
    store.register_file(first_id, "INCAR", "generated", first)
    store.register_file(second_id, "INCAR", "generated", second)
    monkeypatch.setattr(files, "file_store", FileStore(root))
    client = TestClient(app)
    for identity, expected in [(first_id, first), (second_id, second), (first_id, first)]:
        response = client.get(f"/api/v1/files/{identity}/preview")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["file_id"] == identity
        assert data["sha256"] == hashlib.sha256(expected).hexdigest()
        assert data["preview"]["content"] == expected.decode().rstrip("\n")


def test_plan_generate_preview_across_workflows_keeps_original_content(tmp_path, monkeypatch):
    store = FileStore(tmp_path / "files")
    monkeypatch.setattr(files, "file_store", store)
    monkeypatch.setattr(workflows, "workflow_service", WorkflowService(file_store=store))
    source = (Path(__file__).parent / "be_a/golden/nacl/01_relax/POSCAR").read_text(encoding="utf-8")
    client = TestClient(app)
    references = []
    for name in ["workflow A", "workflow B"]:
        response = client.post("/api/v1/workflows/plan", json={"workflow": {
            "sample_name": name, "requested_tasks": ["relax", "static"], "confirm": True,
            "structure": {"formula": "NaCl", "elements": ["Na", "Cl"], "counts": [1, 1], "poscar_text": source},
        }})
        assert response.status_code == 200, response.text
        wid = response.json()["data"]["workflow_id"]
        generated = client.post("/api/v1/workflows/generate", json={"workflow_id": wid})
        assert generated.status_code == 200, generated.text
        tree = generated.json()["data"]["file_tree"]
        poscars = [node for folder in tree["children"] for node in folder.get("children", []) if node["name"] == "POSCAR"]
        assert len(poscars) == 2
        assert poscars[0]["file_id"] != poscars[1]["file_id"]
        references.append((poscars[0]["file_id"], name))
    assert references[0][0] != references[1][0]
    for identity, name in [references[0], references[1], references[0]]:
        response = client.get(f"/api/v1/files/{identity}/preview")
        assert response.status_code == 200
        assert response.json()["data"]["preview"]["content"].splitlines()[0] == name
