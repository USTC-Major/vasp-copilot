"""Tests for /api/v1/materials endpoints (MP search + import).

Live MP network access is not required: we monkeypatch the
MaterialsProjectClient with a canned fake that returns a normalized search
list and a Pymatgen-style structure document.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.api.v1 import deps
from app.services import materials_project as mp_service

client = TestClient(app)

# A Pymatgen-serialized Structure for NaCl (rock salt: Na at 0,0,0 and
# Cl at 0.5,0.5,0.5 with an fcc-style lattice).
_STRUCTURE_DOC = {
    "material_id": "mp-12345",
    "structure": {
        "lattice": {
            "matrix": [
                [5.1062412, 0.0, 0.0],
                [0.0, 5.1062412, 0.0],
                [0.0, 0.0, 5.1062412],
            ],
            "a": 5.1062412,
            "b": 5.1062412,
            "c": 5.1062412,
        },
        "sites": [
            {"species": [{"element": "Na"}], "abc": [0.0, 0.0, 0.0], "label": "Na"},
            {"species": [{"element": "Na"}], "abc": [0.0, 0.5, 0.5], "label": "Na"},
            {"species": [{"element": "Na"}], "abc": [0.5, 0.0, 0.5], "label": "Na"},
            {"species": [{"element": "Na"}], "abc": [0.5, 0.5, 0.0], "label": "Na"},
            {"species": [{"element": "Cl"}], "abc": [0.5, 0.5, 0.5], "label": "Cl"},
            {"species": [{"element": "Cl"}], "abc": [0.0, 0.0, 0.5], "label": "Cl"},
            {"species": [{"element": "Cl"}], "abc": [0.0, 0.5, 0.0], "label": "Cl"},
            {"species": [{"element": "Cl"}], "abc": [0.5, 0.0, 0.0], "label": "Cl"},
        ],
    },
}


class FakeMpClient:
    """Canned in-memory substitute for MaterialsProjectClient."""

    search_result = [
        {
            "material_id": "mp-12345",
            "formula": "NaCl",
            "elements": ["Na", "Cl"],
            "n_elements": 2,
            "spacegroup": {"symbol": "Fm-3m", "number": 225},
            "lattice": {"a": 5.106, "b": 5.106, "c": 5.106, "volume": 133.2},
            "density": 2.16,
            "band_gap": 5.1,
            "is_metal": False,
            "is_stable": True,
            "formation_energy_per_atom": -2.1,
            "energy_above_hull": 0.0,
            "total_magnetization": 0.0,
            "ordering": "NM",
        }
    ]

    def __init__(self, api_key="", base_url="", timeout_seconds=40.0):
        self.api_key = api_key

    def search(self, criteria, limit=20):
        return [dict(self.search_result[0])]

    def get_structure_doc(self, material_id):
        if material_id != "mp-12345":
            raise Exception("not found")
        return _STRUCTURE_DOC

    def close(self):
        pass


def _enable_mp(monkeypatch):
    deps.settings.materials_project.api_key = "fake-key"
    monkeypatch.setattr(mp_service, "MaterialsProjectClient", FakeMpClient)


def test_materials_search_not_configured_422():
    deps.settings.materials_project.api_key = ""
    r = client.post("/api/v1/materials/search", json={"query": "NaCl"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MP_NOT_CONFIGURED"


def test_materials_empty_query_422():
    r = client.post("/api/v1/materials/search", json={"query": "   "})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MP_EMPTY_QUERY"


def test_materials_search_with_fake(monkeypatch):
    _enable_mp(monkeypatch)
    r = client.post("/api/v1/materials/search", json={"query": "NaCl", "limit": 5})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["query"] == "NaCl"
    assert data["count"] == 1
    assert data["materials"][0]["material_id"] == "mp-12345"
    assert data["materials"][0]["formula"] == "NaCl"
    assert data["materials"][0]["spacegroup"]["number"] == 225


def test_materials_import_with_fake(monkeypatch):
    _enable_mp(monkeypatch)
    r = client.post("/api/v1/materials/import", json={"material_id": "mp-12345"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["structure_id"].startswith("str_")
    assert data["material_id"] == "mp-12345"
    assert data["summary"]["formula"] == "Na4Cl4"
    assert data["summary"]["elements"] == ["Na", "Cl"]
    assert data["summary"]["counts"] == [4, 4]
    assert data["summary"]["atom_count"] == 8
    assert abs(data["summary"]["lattice"]["volume"] - 133.2) < 1.0


def test_materials_import_back_analyze_roundtrip(monkeypatch):
    """Imported structure is registered in the shared FileStore so the
    regular /structure/analyze read path can resolve it."""
    _enable_mp(monkeypatch)
    r = client.post("/api/v1/materials/import", json={"material_id": "mp-12345"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    rec = deps.file_store.get_structure(data["structure_id"])
    assert rec.summary.elements == ["Na", "Cl"]
    assert rec.summary.atom_count == 8


def test_materials_import_missing_id_422():
    r = client.post("/api/v1/materials/import", json={"material_id": ""})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MP_EMPTY_MATERIAL_ID"