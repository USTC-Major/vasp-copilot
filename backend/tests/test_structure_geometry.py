"""Physical coordinates, source identity and bounded read-only API regression."""
import hashlib
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.input_validation import validate_poscar
from app.main import app
from app.api.v1 import deps, files, structure, materials
from app.services.file_store import FileStore
from app.services.structure_geometry import build_structure_geometry, VIEW_MAX_ATOMS
from app.schemas.structure import build_structure_summary
from app.core.errors import ValidationError
from backend.tests.test_structure_api import P1_SMALL, FE2O3
from backend.tests.test_materials_api import FakeMpClient, _STRUCTURE_DOC
from app.services import materials_project as mp_service
from backend.toolbox import secrets


def poscar(scale="1", mode="Direct", coordinate="1.2 -0.2 0.3", count=1):
    return f"geometry fixture\n{scale}\n2 0 0\n1 3 0\n0.2 0.4 4\nSi\n{count}\nSelective dynamics\n{mode}\n" + (coordinate + " T F T\n") * count


def summary(text, source="POSCAR"):
    info = validate_poscar(text)
    return build_structure_summary(poscar_text=text, elements=list(info.elements), counts=list(info.counts), source_file=source)


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(deps.settings, "data_dir", str(tmp_path / "app-data"))
    store = FileStore(tmp_path / "files")
    for module in (deps, files, structure, materials):
        monkeypatch.setattr(module, "file_store", store)
    return store


@pytest.mark.parametrize("scale,factors", [("2", [2, 2, 2]), ("2 3 4", [2, 3, 4]), ("-192", [2, 2, 2])])
@pytest.mark.parametrize("mode", ["Direct", "Cartesian", "Kpoints"])
def test_scale_semantics_both_coordinate_modes_and_flags(scale, factors, mode):
    text = poscar(scale, mode)
    parsed = validate_poscar(text)
    assert parsed.coordinates == ()  # Existing non-view validation stays lightweight.
    geometry = build_structure_geometry("str_scale", summary(text))
    raw_basis = np.array([[2, 0, 0], [1, 3, 0], [.2, .4, 4]])
    basis = raw_basis * np.array(factors)
    np.testing.assert_allclose(geometry["basis_cartesian_angstrom"], basis)
    raw = np.array([1.2, -.2, .3])
    expected_cart = raw @ basis if mode == "Direct" else raw * factors
    expected_frac = raw if mode == "Direct" else expected_cart @ np.linalg.inv(basis)
    site = geometry["sites"][0]
    np.testing.assert_allclose(site["cartesian_angstrom"], expected_cart)
    np.testing.assert_allclose(site["fractional"], expected_frac)
    assert site["id"] == 1 and site["element"] == "Si"
    assert site["selective_flags"] == (True, False, True)
    assert geometry["selective_flags_basis"] == "direct_lattice_vectors"
    assert geometry["source"]["poscar_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert geometry["lattice"]["volume"] == pytest.approx(abs(np.linalg.det(basis)))


def test_left_handed_and_unwrapped_coordinates():
    text = poscar(mode="Cartesian").replace("0.2 0.4 4", "0.2 0.4 -4")
    geometry = build_structure_geometry("str_left", summary(text))
    site = geometry["sites"][0]
    np.testing.assert_allclose(np.array(site["fractional"]) @ geometry["basis_cartesian_angstrom"], site["cartesian_angstrom"])
    direct = build_structure_geometry("str_outside", summary(poscar()))
    assert direct["sites"][0]["fractional"] == (1.2, -.2, .3)


def test_bom_and_d_exponents_share_validated_coordinates():
    text = "\ufeff" + poscar(scale="2D0", mode="Cartesian", coordinate="1D-1 -2d-1 3E-1")
    geometry = build_structure_geometry("str_bom", summary(text))
    np.testing.assert_allclose(geometry["sites"][0]["cartesian_angstrom"], [.2, -.4, .6])
    assert geometry["source"]["poscar_sha256"] == hashlib.sha256(text.encode()).hexdigest()


def test_scaled_underflow_is_view_error_without_changing_generation_validation():
    text = "underflow\n1 5e-324 1\n10 .1 0\n0 .2 10\n10 .4 10\nSi\n1\nDirect\n.1 .2 .3\n"
    saved = summary(text)  # Existing validator can accept tiny positive volume.
    assert saved.atom_count == 1
    with pytest.raises(ValidationError) as caught:
        build_structure_geometry("str_underflow", saved)
    assert caught.value.code == "STRUCTURE_VIEW_NUMBER_LIMIT"


@pytest.mark.parametrize("source", ["POSCAR", "p1.cif", "hematite.cif"])
def test_upload_analyze_geometry_uses_same_saved_poscar(source, isolated_store):
    text = poscar() if source == "POSCAR" else P1_SMALL if source == "p1.cif" else FE2O3
    client = TestClient(app)
    uploaded = client.post("/api/v1/files/upload", files={"file": (source, text.encode())}, data={"purpose": "structure"})
    assert uploaded.status_code == 200, uploaded.text
    fid = uploaded.json()["data"]["file"]["file_id"]
    analyzed = client.post("/api/v1/structure/analyze", json={"file_id": fid})
    assert analyzed.status_code == 200, analyzed.text
    sid = analyzed.json()["data"]["structure_id"]
    saved = isolated_store.get_structure(sid).summary
    result = client.get(f"/api/v1/structure/{sid}/geometry")
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert data["source"]["poscar_sha256"] == hashlib.sha256(saved.poscar_text.encode()).hexdigest()
    assert data["atom_count"] == saved.atom_count
    assert [s["id"] for s in data["sites"]] == list(range(1, saved.atom_count + 1))
    assert len(result.content) <= 1024 * 1024
    reference = validate_poscar(saved.poscar_text, include_coordinates=True)
    np.testing.assert_allclose(data["basis_cartesian_angstrom"], reference.matrix)
    np.testing.assert_allclose([s["fractional"] for s in data["sites"]], reference.coordinates)
    # Persistence is the same source, not a new CIF parse or a private view cache.
    restored = FileStore(isolated_store._root)
    again = build_structure_geometry(sid, restored.get_structure(sid).summary)
    assert data["geometry_sha256"] == again["geometry_sha256"]


def test_materials_import_geometry_same_poscar_with_controlled_fixture(isolated_store, monkeypatch):
    secrets.set_secret("mp_api_key", "fixture-memory-only")
    monkeypatch.setattr(mp_service, "MaterialsProjectClient", FakeMpClient)
    with TestClient(app) as client:
        result = client.post("/api/v1/materials/import", json={"material_id": "mp-12345"})
        assert result.status_code == 200, result.text
        sid = result.json()["data"]["structure_id"]
        geometry = client.get(f"/api/v1/structure/{sid}/geometry").json()["data"]
    saved = isolated_store.get_structure(sid).summary
    assert geometry["source"]["material_id"] == "mp-12345"
    assert geometry["source"]["format"] == "materials_project"
    assert geometry["source"]["poscar_sha256"] == hashlib.sha256(saved.poscar_text.encode()).hexdigest()
    np.testing.assert_allclose([s["fractional"] for s in geometry["sites"]], [s["abc"] for s in _STRUCTURE_DOC["structure"]["sites"]])


def test_atom_limit_only_affects_view_and_bad_data_recovers(isolated_store):
    text = poscar(count=VIEW_MAX_ATOMS + 1)
    saved = summary(text)  # Existing analyze/validation supports the larger count.
    record = isolated_store.store_structure("source", saved)
    before = saved.model_dump()
    client = TestClient(app)
    response = client.get(f"/api/v1/structure/{record.structure_id}/geometry")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "STRUCTURE_VIEW_ATOM_LIMIT"
    assert saved.model_dump() == before
    saved.poscar_text = "corrupt"
    # FileStore owns a deep snapshot; changing the caller cannot corrupt it.
    assert record.summary is not saved and record.summary.poscar_text == text
    response = client.get(f"/api/v1/structure/{record.structure_id}/geometry")
    assert response.status_code == 422 and response.json()["error"]["code"] == "STRUCTURE_VIEW_ATOM_LIMIT"
    # Corrupt the authoritative saved record to exercise the failure/recovery.
    record.summary.poscar_text = "corrupt"
    response = client.get(f"/api/v1/structure/{record.structure_id}/geometry")
    assert response.status_code == 422 and response.json()["error"]["code"] == "STRUCTURE_VIEW_INVALID"
    good = isolated_store.store_structure("source", summary(poscar()))
    assert client.get(f"/api/v1/structure/{good.structure_id}/geometry").status_code == 200
    good.touched_at = time.time() - 100000
    assert client.get(f"/api/v1/structure/{good.structure_id}/geometry").status_code == 404
    assert client.get("/api/v1/structure/str_missing/geometry").status_code == 404


def test_finite_view_limit_and_response_bound(monkeypatch):
    with pytest.raises(ValidationError) as caught:
        build_structure_geometry("str_huge", summary(poscar(coordinate="1e7 0 0")))
    assert caught.value.code == "STRUCTURE_VIEW_NUMBER_LIMIT"
    from app.services import structure_geometry
    monkeypatch.setattr(structure_geometry, "VIEW_MAX_BYTES", 1030)
    with pytest.raises(ValidationError) as caught:
        build_structure_geometry("str_big", summary(poscar()))
    assert caught.value.code == "STRUCTURE_VIEW_SIZE_LIMIT"
    # Malformed flags/nan/singular input remains governed by the shared validator.
    for text in (poscar().replace("T F T", "T X T"), poscar(coordinate="NaN 0 0"), poscar().replace("0.2 0.4 4", "0 0 0")):
        with pytest.raises(ValidationError):
            build_structure_geometry("str_bad", summary(poscar()).model_copy(update={"poscar_text": text}))
