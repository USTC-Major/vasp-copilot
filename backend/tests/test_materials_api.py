"""Tests for /api/v1/materials endpoints (MP search + import).

Live MP network access is not required: we monkeypatch the
MaterialsProjectClient with a canned fake that returns a normalized search
list and a Pymatgen-style structure document.
"""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Event, get_ident
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.v1 import deps
from app.services import materials_project as mp_service
from app.services.file_store import FileStore
from app.api.v1 import materials as mp_api, structure as structure_api
from backend.toolbox import secrets


@pytest.fixture(autouse=True)
def _isolated_materials(tmp_path, monkeypatch, isolated_runtime_home):
    monkeypatch.delenv('MP_API_KEY', raising=False)
    monkeypatch.setattr(deps.settings, 'data_dir', str(tmp_path / 'app-data'))
    store = FileStore(root=tmp_path / 'files', ttl_seconds=3600)
    monkeypatch.setattr(deps, 'file_store', store)
    monkeypatch.setattr(mp_api, 'file_store', store)
    monkeypatch.setattr(structure_api, 'file_store', store)


@pytest.fixture
def client():
    with TestClient(app) as current:
        yield current

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
    secrets.set_secret('mp_api_key', 'fake-key')
    monkeypatch.setattr(mp_service, "MaterialsProjectClient", FakeMpClient)


def test_materials_search_not_configured_422(client):
    r = client.post("/api/v1/materials/search", json={"query": "NaCl"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MP_NOT_CONFIGURED"


def test_materials_empty_query_422(client):
    r = client.post("/api/v1/materials/search", json={"query": "   "})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MP_EMPTY_QUERY"


def test_materials_search_with_fake(client, monkeypatch):
    _enable_mp(monkeypatch)
    r = client.post("/api/v1/materials/search", json={"query": "NaCl", "limit": 5})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["query"] == "NaCl"
    assert data["count"] == 1
    assert data["materials"][0]["material_id"] == "mp-12345"
    assert data["materials"][0]["formula"] == "NaCl"
    assert data["materials"][0]["spacegroup"]["number"] == 225


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/v1/materials/search", {"query": "NaCl"}),
        ("/api/v1/materials/import", {"material_id": "mp-12345"}),
    ],
    ids=["search", "import"],
)
def test_slow_mp_request_does_not_block_health(path, payload, client, monkeypatch):
    """A synchronous MP request must leave the ASGI loop free for health checks."""
    entered = Event()
    release = Event()

    class SlowFakeMpClient(FakeMpClient):
        def _wait_for_release(self):
            entered.set()
            assert release.wait(timeout=10), "test failed to release the fake MP request"

        def search(self, criteria, limit=20):
            self._wait_for_release()
            return super().search(criteria, limit)

        def get_structure_doc(self, material_id):
            self._wait_for_release()
            return super().get_structure_doc(material_id)

    # Keep this route-level test independent from Toolbox credentials and MP.
    monkeypatch.setattr(mp_api, "_runtime_mp_api_key", lambda _request: "fake-key")
    monkeypatch.setattr(mp_service, "MaterialsProjectClient", SlowFakeMpClient)

    health_done = Event()

    def request_health():
        try:
            return client.get("/health")
        finally:
            health_done.set()

    with ThreadPoolExecutor(max_workers=2) as workers:
        material_request = workers.submit(client.post, path, json=payload)
        health_request = None
        health_returned_while_mp_blocked = False
        try:
            assert entered.wait(timeout=5), "the fake MP request did not start"
            health_request = workers.submit(request_health)
            health_returned_while_mp_blocked = health_done.wait(timeout=3)
        finally:
            release.set()

        material_response = material_request.result(timeout=10)
        health_response = health_request.result(timeout=10) if health_request else None

    assert health_returned_while_mp_blocked, "health waited for the blocked MP request to finish"
    assert health_response is not None and health_response.status_code == 200
    assert material_response.status_code == 200, material_response.text


def test_materials_import_with_fake(client, monkeypatch):
    _enable_mp(monkeypatch)
    r = client.post("/api/v1/materials/import", json={"material_id": "mp-12345"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["structure_id"].startswith("str_")
    assert data["material_id"] == "mp-12345"
    assert data["summary"]["source_material_id"] == "mp-12345"
    assert data["summary"]["formula"] == "Na4Cl4"
    assert data["summary"]["elements"] == ["Na", "Cl"]
    assert data["summary"]["counts"] == [4, 4]
    assert data["summary"]["atom_count"] == 8
    assert abs(data["summary"]["lattice"]["volume"] - 133.2) < 1.0


def test_materials_import_persists_on_asgi_loop_thread(client, monkeypatch):
    _enable_mp(monkeypatch)
    request_thread = []
    persistence_threads = []

    async def capture_request_thread():
        request_thread.append(get_ident())
        return "req_import_thread"

    monkeypatch.setitem(app.dependency_overrides, mp_api.get_request_id, capture_request_thread)
    original_store_file = mp_api.file_store.store_file
    original_store_structure = mp_api.file_store.store_structure

    def track_store_file(*args, **kwargs):
        persistence_threads.append(get_ident())
        return original_store_file(*args, **kwargs)

    def track_store_structure(*args, **kwargs):
        persistence_threads.append(get_ident())
        return original_store_structure(*args, **kwargs)

    monkeypatch.setattr(mp_api.file_store, "store_file", track_store_file)
    monkeypatch.setattr(mp_api.file_store, "store_structure", track_store_structure)
    response = client.post("/api/v1/materials/import", json={"material_id": "mp-12345"})

    assert response.status_code == 200, response.text
    assert len(request_thread) == 1
    assert persistence_threads == [request_thread[0], request_thread[0]]


def test_import_accepts_verified_numeric_to_alpha_alias(client, monkeypatch):
    _enable_mp(monkeypatch)
    class AliasClient(FakeMpClient):
        def get_structure_doc(self, material_id):
            doc = copy.deepcopy(_STRUCTURE_DOC)
            doc["material_id"] = "mp-aaaabwmb"
            return doc
    monkeypatch.setattr(mp_service, "MaterialsProjectClient", AliasClient)
    r = client.post("/api/v1/materials/import", json={"material_id": "mp-32761"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["material_id"] == "mp-32761"
    assert r.json()["data"]["summary"]["source_material_id"] == "mp-aaaabwmb"
    record = deps.file_store.get_structure(r.json()["data"]["structure_id"])
    assert record.summary.source_material_id == "mp-aaaabwmb"
    assert record.summary.source_file == "MaterialsProject/mp-32761"
    reloaded = FileStore(root=deps.file_store._root, ttl_seconds=3600)
    restored = reloaded.get_structure(record.structure_id)
    assert restored.summary.source_material_id == "mp-aaaabwmb"
    assert restored.summary.source_file == "MaterialsProject/mp-32761"


def test_import_rejects_mismatched_or_unsafe_id(client, monkeypatch):
    _enable_mp(monkeypatch)
    class WrongClient(FakeMpClient):
        def get_structure_doc(self, material_id):
            doc = copy.deepcopy(_STRUCTURE_DOC)
            doc["material_id"] = "mp-99999"
            return doc
    monkeypatch.setattr(mp_service, "MaterialsProjectClient", WrongClient)
    r = client.post("/api/v1/materials/import", json={"material_id": "mp-32761"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MP_ID_MISMATCH"
    for bad in ("mp-" + "1" * 100, "mp-1\ninjected", "../../mp-1"):
        r = client.post("/api/v1/materials/import", json={"material_id": bad})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "MP_INVALID_MATERIAL_ID"


def test_materials_import_back_analyze_roundtrip(client, monkeypatch):
    """Imported structure is registered in the shared FileStore so the
    regular /structure/analyze read path can resolve it."""
    _enable_mp(monkeypatch)
    r = client.post("/api/v1/materials/import", json={"material_id": "mp-12345"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    rec = deps.file_store.get_structure(data["structure_id"])
    assert rec.summary.elements == ["Na", "Cl"]
    assert rec.summary.atom_count == 8


def test_materials_import_missing_id_422(client):
    r = client.post("/api/v1/materials/import", json={"material_id": ""})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MP_EMPTY_MATERIAL_ID"


def test_materials_import_invalid_lattice_has_no_new_records(client, monkeypatch):
    _enable_mp(monkeypatch)
    bad = copy.deepcopy(_STRUCTURE_DOC)
    bad["structure"]["lattice"]["matrix"][2] = [0.0, 0.0, 0.0]
    monkeypatch.setattr(FakeMpClient, "get_structure_doc", lambda self, _id: bad)
    before = (len(deps.file_store._files), len(deps.file_store._structures))
    response = client.post("/api/v1/materials/import", json={"material_id": "mp-12345"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "POSCAR_LATTICE_DEGENERATE"
    assert (len(deps.file_store._files), len(deps.file_store._structures)) == before


def test_materials_import_missing_site_coordinates_does_not_drop_atom(client, monkeypatch):
    _enable_mp(monkeypatch)
    bad = copy.deepcopy(_STRUCTURE_DOC)
    bad["structure"]["sites"][0].pop("abc")
    monkeypatch.setattr(FakeMpClient, "get_structure_doc", lambda self, _id: bad)
    before = (len(deps.file_store._files), len(deps.file_store._structures))
    response = client.post("/api/v1/materials/import", json={"material_id": "mp-12345"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MP_INVALID_STRUCTURE"
    assert (len(deps.file_store._files), len(deps.file_store._structures)) == before
