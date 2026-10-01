from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from ai_mode.server import create_ai_mode_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    monkeypatch.setenv("AI_MODE_LLM_PROVIDER", "openai")
    monkeypatch.setenv("AI_MODE_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("AI_MODE_LLM_API_KEY", "test-key")
    monkeypatch.setenv("AI_MODE_LLM_MODEL", "test-model")
    with TestClient(create_ai_mode_app()) as test_client:
        yield test_client


def _diagnosis():
    return {
        "diagnosis_id": "diag_demo",
        "diagnosis_status": "succeeded",
        "summary": {"headline": "存在收敛问题"},
        "issues": [{"issue_id": "i1", "severity": "high", "description": "EDIFF 未满足", "evidence": {"source": "OSZICAR"}}],
        "recommended_fixes": [{"fix_id": "f1", "target_file": "INCAR", "safe_to_generate": False}],
        "missing_evidence": [],
        "next_step": {"allowed": True},
        "provenance": {"mode": "rule_based"},
    }


def test_capabilities_do_not_ping_model(client):
    response = client.get("/ai/v1/diagnosis/capabilities")
    assert response.status_code == 200
    assert response.json() == {
        "mode": "ai", "enabled": True, "configured": True,
        "available": True, "reason_code": "AI_MODE_DIAGNOSIS_READY",
    }


def test_explain_reads_toolbox_result_and_returns_evidence_source(client, monkeypatch):
    import ai_mode.diagnosis as diagnosis
    from ai_mode.llm.fake import FakeLLM

    monkeypatch.setattr(diagnosis, "fetch_diagnosis", lambda diagnosis_id, client=None: _diagnosis())
    monkeypatch.setattr(diagnosis, "build_client", lambda *args, **kwargs: FakeLLM(queue=["解释结果"]))
    response = client.post("/ai/v1/diagnosis/explain", json={
        "diagnosis_id": "diag_demo", "question": "请解释主要问题",
    })
    assert response.status_code == 200
    assert response.json() == {
        "mode": "ai", "ok": True, "diagnosis_id": "diag_demo",
        "answer": "解释结果", "evidence_source": "toolbox_diagnosis",
        "context_truncated": False,
    }


def test_explain_rejects_incomplete_diagnosis(client, monkeypatch):
    import ai_mode.diagnosis as diagnosis

    monkeypatch.setattr(diagnosis, "fetch_diagnosis", lambda diagnosis_id, client=None: {
        "diagnosis_id": diagnosis_id,
        "diagnosis_status": "running",
        "summary": {"headline": "尚未完成"},
    })
    response = client.post("/ai/v1/diagnosis/explain", json={
        "diagnosis_id": "diag_demo", "question": "请解释主要问题",
    })
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AI_MODE_DIAGNOSIS_NOT_READY"


def test_explain_rejects_extra_fields_without_reading_toolbox(client, monkeypatch):
    import ai_mode.diagnosis as diagnosis
    called = False

    def fail(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("must reject before reading Toolbox")

    monkeypatch.setattr(diagnosis, "fetch_diagnosis", fail)
    response = client.post("/ai/v1/diagnosis/explain", json={
        "diagnosis_id": "diag_demo", "question": "q", "path": "C:/secret",
    })
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "AI_MODE_DIAGNOSIS_BAD_REQUEST"
    assert called is False


def test_explain_rejects_non_string_request_values(client, monkeypatch):
    import ai_mode.diagnosis as diagnosis

    called = False

    def fail(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("must reject before reading Toolbox")

    monkeypatch.setattr(diagnosis, "fetch_diagnosis", fail)
    response = client.post("/ai/v1/diagnosis/explain", json={
        "diagnosis_id": 123, "question": ["请解释"],
    })
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "AI_MODE_DIAGNOSIS_BAD_REQUEST"
    assert called is False


def test_fake_provider_is_not_reported_as_available(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    monkeypatch.setenv("AI_MODE_LLM_PROVIDER", "fake")
    with TestClient(create_ai_mode_app()) as test_client:
        response = test_client.get("/ai/v1/diagnosis/capabilities")
    assert response.json()["available"] is False
    assert response.json()["reason_code"] == "AI_MODE_DIAGNOSIS_FAKE_PROVIDER"


def test_evidence_projection_does_not_forward_workspace_paths():
    from ai_mode.diagnosis import project_evidence

    projected = project_evidence({
        "issues": [{
            "issue_id": "i1", "severity": "high", "blocking": True,
            "evidence": [{
                "file": "C:/private/run/OUTCAR",
                "data_ref": "C:/private/run/raw.log",
                "message": "see C:/private/run/INCAR",
            }],
        }],
        "missing_evidence": ["C:/private/run/OSZICAR"],
        "next_step": {"allowed": True, "reason": "see C:/private/run"},
        "recommended_fixes": [{"fix_id": "f1", "target_file": "C:/private/run/INCAR", "warnings": ["C:/private/run"]}],
    })

    text = str(projected)
    assert "C:/private" not in text
    assert projected["issues"][0]["evidence"][0]["file"] == "OUTCAR"


def test_all_free_text_projection_fields_are_path_filtered():
    from ai_mode.diagnosis import project_evidence

    projected = project_evidence({
        "summary": {"headline": "inspect C:/private/summary.txt"},
        "issues": [{
            "issue_id": "i1",
            "title": "C:/private/title.txt",
            "summary": "read /tmp/description.txt",
            "possible_causes": ["\\\\server\\share\\cause.txt"],
            "recommendations": [{
                "action": "review",
                "parameter": "C:/private/INCAR",
                "new_value": "see /tmp/value.txt",
                "rationale": "because C:/private/reason.txt",
            }],
        }],
        "missing_evidence": ["C:/private/OUTCAR"],
        "next_step": {"allowed": True, "suggested_task": "open /tmp/task", "reason": "see C:/private/reason"},
        "recommended_fixes": [{
            "fix_id": "f1",
            "warnings": ["C:/private/warning"],
            "changes": [{"parameter": "C:/private/INCAR", "old_value": "/tmp/old", "new_value": "C:/private/new"}],
        }],
    })

    serialized = json.dumps(projected, ensure_ascii=False)
    assert "C:/private" not in serialized
    assert "/tmp/" not in serialized
    assert "missing_evidence" in projected
    assert "title" in projected["issues"][0]
    assert "rationale" in projected["issues"][0]["recommendations"][0]


def test_bounded_context_keeps_blocking_issue_and_missing_evidence_as_valid_json():
    from ai_mode.diagnosis import MAX_DIAGNOSIS_CONTEXT, _bounded_context, project_evidence

    projected = project_evidence({
        "summary": {"headline": "long report"},
        "issues": [{
            "issue_id": "blocking",
            "severity": "critical",
            "blocking": True,
            "title": "blocking issue",
            "summary": "x" * 30_000,
        }] + [{
            "issue_id": f"detail-{index}",
            "severity": "low",
            "blocking": False,
            "summary": "detail" * 2_000,
        } for index in range(20)],
        "missing_evidence": ["OUTCAR missing", "OSZICAR missing"],
    })

    context, truncated = _bounded_context(projected)
    parsed = json.loads(context)
    assert len(context) <= MAX_DIAGNOSIS_CONTEXT
    assert truncated is True
    assert parsed["issues"][0]["issue_id"] == "blocking"
    assert parsed["missing_evidence"] == ["OUTCAR missing", "OSZICAR missing"]


def test_auto_without_real_model_is_not_reported_as_fake_provider(monkeypatch, tmp_path):
    from ai_mode.config import AiModeConfig
    from ai_mode.diagnosis import capabilities

    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    state = capabilities(AiModeConfig(enabled=True, llm_provider="auto"))
    assert state["available"] is False
    assert state["configured"] is False
    assert state["reason_code"] == "AI_MODE_DIAGNOSIS_NOT_CONFIGURED"
