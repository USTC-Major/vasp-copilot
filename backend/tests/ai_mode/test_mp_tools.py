"""Offline regressions for bounded MP retrieval and one-shot POSCAR consent."""
import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pymatgen.io.vasp.inputs import Poscar

from ai_mode import materials
from ai_mode.agent.tools import ToolExecutor, _CONSENT_PENDING, tool_schema_text
from ai_mode.config import AiModeConfig
from ai_mode.consent import get_card, resolve_card
from ai_mode.projects import ProjectStore


@pytest.fixture
def doc():
    return {"material_id": "mp-149", "structure": {
        "lattice": {"matrix": [[5, 0, 0], [0, 5, 0], [0, 0, 5]]},
        "sites": [{"species": [{"element": "Si", "occu": 1}], "abc": xyz}
                  for xyz in [[0, 0, 0], [0.25, 0.25, 0.25]]]}}


def test_http_contract(monkeypatch, doc):
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [doc]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(materials, "_open_stream", client.stream)
        result = materials.fetch_poscar("private-test-key", "mp-149")
    request = seen[0]
    assert str(request.url).startswith(materials.SUMMARY_URL)
    assert request.url.params["_limit"] == "1"
    assert "limit" not in request.url.params
    assert request.headers["X-API-KEY"] == "private-test-key"
    assert "private-test-key" not in json.dumps(result)
    parsed = Poscar.from_str(result["content"]).structure
    assert len(parsed) == 2
    assert parsed[1].frac_coords.tolist() == [0.25, 0.25, 0.25]


@pytest.mark.parametrize("status", [301, 302, 401, 403, 429, 500])
def test_http_errors_never_leak_body_or_follow_redirect(monkeypatch, status):
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(status, text="secret-key", headers={"Location": "https://evil.invalid"})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(materials, "_open_stream", client.stream)
        with pytest.raises(materials.MaterialsError) as err:
            materials.fetch_poscar("secret-key", "mp-149")
    assert len(seen) == 1
    assert "secret-key" not in str(err.value)


@pytest.mark.parametrize("body", [b"not-json", b"{}", b'{"data":[1]}', b"x" * (materials.MAX_RESPONSE_BYTES + 1)],
                         ids=["non-json", "missing-data", "invalid-doc", "oversized"])
def test_invalid_or_oversized_response(monkeypatch, body):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))) as client:
        monkeypatch.setattr(materials, "_open_stream", client.stream)
        with pytest.raises(materials.MaterialsError):
            materials.fetch_poscar("key", "mp-149")


def test_network_error_is_redacted(monkeypatch):
    def fail(*args, **kwargs):
        raise httpx.ConnectError("secret-key in upstream error")
    monkeypatch.setattr(materials, "_open_stream", fail)
    with pytest.raises(materials.MaterialsError, match="MP_NETWORK_ERROR") as err:
        materials.fetch_poscar("secret-key", "mp-149")
    assert "secret-key" not in str(err.value)


@pytest.mark.parametrize("mid", [None, "mp-149?url=x", "https://evil.invalid", "../../POSCAR", "mp-x"])
def test_invalid_id(mid):
    with pytest.raises(materials.MaterialsError, match="MP_INVALID_ID"):
        materials.fetch_poscar("key", mid)


def test_missing_key():
    with pytest.raises(materials.MaterialsError, match="MP_NOT_CONFIGURED"):
        materials.fetch_poscar("", "mp-149")


def test_search_contract(monkeypatch):
    def query(key, params):
        assert params["_limit"] == 5 and params["formula"] == "BaTiO3"
        return [{"material_id": "mp-1", "symmetry": {"number": 99}, "band_gap": 2.0},
                {"material_id": "mp-2", "symmetry": {"number": 221}, "band_gap": float("nan")}]
    monkeypatch.setattr(materials, "_request", query)
    rows = materials.search("key", "BaTiO3")
    assert len(rows) == 2 and rows[0]["spacegroup_number"] == 99
    assert rows[1]["band_gap"] is None


@pytest.mark.parametrize("formula,limit", [("https://example.com", 5), ("Xx2", 5), ("", 5), ("Si", 0), ("Si", True), ("Si", 11)])
def test_invalid_search(formula, limit):
    with pytest.raises(materials.MaterialsError, match="MP_INVALID_QUERY"):
        materials.search("key", formula, limit)


@pytest.mark.parametrize("fault", ["partial", "disordered", "nan", "singular", "missing", "too_many", "wrong_id", "empty"])
def test_bad_structures_fail_closed(monkeypatch, doc, fault):
    item = deepcopy(doc)
    site = item["structure"]["sites"][0]
    if fault == "partial": site["species"][0]["occu"] = 0.5
    if fault == "disordered": site["species"].append({"element": "Ge", "occu": 0.5})
    if fault == "nan": site["abc"][0] = float("nan")
    if fault == "singular": item["structure"]["lattice"]["matrix"] = [[0, 0, 0]] * 3
    if fault == "missing": del site["abc"]
    if fault == "too_many": item["structure"]["sites"] = [site] * 501
    if fault == "wrong_id": item["material_id"] = "mp-999"
    monkeypatch.setattr(materials, "_request", lambda *args: [] if fault == "empty" else [item])
    with pytest.raises(materials.MaterialsError):
        materials.fetch_poscar("key", "mp-149")


@pytest.fixture
def ctx(tmp_path, monkeypatch, doc):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(materials, "_request", lambda *args: [deepcopy(doc)])
    store = ProjectStore(tmp_path / "home")
    pid = store.create_project("MP import")["id"]
    root = tmp_path / "workspace"
    root.mkdir()
    tid = store.create_task(pid, goal="Si", local_workspace=str(root))["id"]
    cfg = AiModeConfig(data_dir=tmp_path / "data", mp_api_key="private-test-key")
    def no_hpc():
        pytest.fail("MP import must never construct or contact HPC")
    ex = ToolExecutor(store=store, project_id=pid, task_id=tid, cfg=cfg, orch_factory=no_hpc)
    return SimpleNamespace(store=store, pid=pid, tid=tid, root=root, ex=ex, cfg=cfg)


def preview(ctx, **args):
    result = ctx.ex.handle("mp_import_poscar", {"material_id": "mp-149", **args})
    assert result.startswith(_CONSENT_PENDING), result
    return result[len(_CONSENT_PENDING):]


def resolve(ctx, aid, approved=True):
    return resolve_card(ctx.store, ctx.pid, ctx.tid, aid, approved=approved)


def test_import_consent_exact_content_and_no_hpc(ctx):
    assert "mp_import_poscar" in tool_schema_text()
    aid = preview(ctx)
    assert not (ctx.root / "POSCAR").exists()
    card = get_card(ctx.store, ctx.pid, ctx.tid, aid)
    assert "private-test-key" not in json.dumps(card)
    resolve(ctx, aid)
    result = ctx.ex.execute_action(aid)
    assert "Materials Project mp-149" in result
    assert (ctx.root / "POSCAR").read_bytes() == card["binding"]["content"].encode()
    assert get_card(ctx.store, ctx.pid, ctx.tid, aid)["state"] == "executed"
    stamp = (ctx.root / "POSCAR").stat().st_mtime_ns
    ctx.ex.execute_action(aid)
    assert (ctx.root / "POSCAR").stat().st_mtime_ns == stamp
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert flow["material_imports"]["POSCAR"]["material_id"] == "mp-149"


def test_reject_keeps_file_untouched(ctx):
    (ctx.root / "POSCAR").write_text("original")
    aid = preview(ctx)
    resolve(ctx, aid, False)
    ctx.ex.execute_action(aid)
    assert (ctx.root / "POSCAR").read_text() == "original"


@pytest.mark.parametrize("change", ["file", "workspace", "binding"])
def test_stale_consent_cannot_write(ctx, change, tmp_path):
    aid = preview(ctx)
    resolve(ctx, aid)
    if change == "file": (ctx.root / "POSCAR").write_text("new user content")
    if change == "workspace":
        ctx.store.update_task(ctx.pid, ctx.tid, local_workspace=str(tmp_path / "other"))
    if change == "binding":
        flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
        flow["consent"]["actions"][aid]["binding"]["content"] = "tampered"
        ctx.store.update_task(ctx.pid, ctx.tid, flow=flow)
    ctx.ex.execute_action(aid)
    if change == "file": assert (ctx.root / "POSCAR").read_text() == "new user content"
    else: assert not (ctx.root / "POSCAR").exists()


@pytest.mark.parametrize("args", [{"url": "https://evil.invalid"}, {"content": "Si fake"}, {"job_key": "../outside"}, {"job_key": "unplanned"}])
def test_invalid_tool_arguments(ctx, args, monkeypatch):
    monkeypatch.setattr(materials, "_request", lambda *a: pytest.fail("must reject before network"))
    result = ctx.ex.handle("mp_import_poscar", {"material_id": "mp-149", **args})
    assert "MP_INVALID" in result
    assert not (ctx.root / "POSCAR").exists()


def test_selected_workspace_required(ctx):
    ctx.store.update_task(ctx.pid, ctx.tid, local_workspace="")
    assert "MP_WORKSPACE_REQUIRED" in ctx.ex.handle("mp_import_poscar", {"material_id": "mp-149"})


def test_planned_job_target(ctx):
    ctx.store.update_task(ctx.pid, ctx.tid, flow={"plan": {"jobs": [{"key": "relax"}]}})
    aid = preview(ctx, job_key="relax")
    resolve(ctx, aid)
    ctx.ex.execute_action(aid)
    assert (ctx.root / "relax" / "POSCAR").is_file()


def test_existing_file_explicit_overwrite(ctx):
    (ctx.root / "POSCAR").write_text("original")
    aid = preview(ctx)
    card = get_card(ctx.store, ctx.pid, ctx.tid, aid)
    assert "覆盖现有文件" in card["summary"]
    assert (ctx.root / "POSCAR").read_text() == "original"
    resolve(ctx, aid)
    ctx.ex.execute_action(aid)
    assert (ctx.root / "POSCAR").read_bytes() == card["binding"]["content"].encode()


def test_link_target_rejected_before_fetch(ctx, monkeypatch):
    from pathlib import Path
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda p: p.name == "POSCAR" or original(p))
    monkeypatch.setattr(materials, "_request", lambda *a: pytest.fail("must not fetch"))
    result = ctx.ex.handle("mp_import_poscar", {"material_id": "mp-149"})
    assert "symbolic links" in result
    assert not (ctx.root / "POSCAR").exists()


def test_agent_stream_emits_import_card_and_stops(ctx):
    from ai_mode.agent import run_agent_stream
    from ai_mode.agent.protocol import TOOL_MARK
    from ai_mode.agent.runner import build_messages
    from ai_mode.llm.fake import FakeLLM
    task = ctx.store.get_task(ctx.pid, ctx.tid)
    messages = build_messages(ctx.store, task, [], "导入 mp-149")
    assert "mp_import_poscar" in messages[0]["content"]
    assert "private-test-key" not in str(messages)
    llm = FakeLLM()
    llm.enqueue(TOOL_MARK + json.dumps({"name": "mp_import_poscar", "args": {"material_id": "mp-149"}}))
    events = list(run_agent_stream(ctx.store, ctx.pid, ctx.tid, "导入 mp-149",
                                  cfg=ctx.cfg, llm_factory=lambda c: llm, auto_resume=False))
    cards = [event["card"] for event in events if event["type"] == "card"]
    assert len(cards) == 1 and cards[0]["kind"] == "mp_poscar_write"
    assert events[-1]["type"] == "done"
    assert not (ctx.root / "POSCAR").exists()


def test_consent_endpoint_executes_restored_import_without_live_chat(
        ctx, monkeypatch):
    """A restored card remains executable after its original chat run died."""
    from ai_mode import server

    aid = preview(ctx)
    ctx.store.update_task(
        ctx.pid, ctx.tid,
        generation={"run_id": "old-process", "state": "running"})
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    monkeypatch.setenv("VASP_AI_HOME", str(ctx.store.root))
    monkeypatch.setattr(server, "_get_project_store", lambda: ctx.store)

    client = TestClient(server.create_ai_mode_app())
    url = f"/ai/v1/projects/{ctx.pid}/tasks/{ctx.tid}/messages/consent"
    response = client.post(url, json={"card_id": aid, "approved": True})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["state"] == "executed"
    assert (ctx.root / "POSCAR").is_file()
    assert get_card(ctx.store, ctx.pid, ctx.tid, aid)["state"] == "executed"
    messages = ctx.store.list_messages(ctx.pid, ctx.tid)
    assert messages[-1]["role"] == "assistant"
    assert "Materials Project mp-149" in messages[-1]["content"]

    # Replaying the same HTTP decision is idempotent and adds no message.
    again = client.post(url, json={"card_id": aid, "approved": True})
    assert again.status_code == 200
    assert again.json()["state"] == "executed"
    assert ctx.store.list_messages(ctx.pid, ctx.tid) == messages
