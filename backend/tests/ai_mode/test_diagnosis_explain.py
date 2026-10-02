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


@pytest.mark.parametrize("content,reason,reasoning,code,hint", [
    ("", "stop", "", "AI_MODE_DIAGNOSIS_EMPTY_RESPONSE", "检查模型接口"),
    (None, "length", "private-thought", "AI_MODE_DIAGNOSIS_RESPONSE_TRUNCATED", "缩小问题范围"),
    (None, "stop", "private-thought", "AI_MODE_DIAGNOSIS_REASONING_ONLY", "思考设置"),
    ("partial-private-answer", "length", "", "AI_MODE_DIAGNOSIS_RESPONSE_TRUNCATED", "缩小问题范围"),
])
def test_explain_response_failure_classification(client, monkeypatch, caplog, content, reason, reasoning, code, hint):
    import httpx
    import ai_mode.diagnosis as diagnosis
    from ai_mode.llm.openai_compat import OpenAIClient

    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": content, "reasoning_content": reasoning}, "finish_reason": reason}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 30},
        })
    model = OpenAIClient(base_url="https://example.invalid/v1", api_key="sk-private", model="test",
                         max_retries=2, http=httpx.Client(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(diagnosis, "fetch_diagnosis", lambda diagnosis_id, client=None: _diagnosis())
    monkeypatch.setattr(diagnosis, "build_client", lambda *args, **kwargs: model)
    with caplog.at_level("INFO", logger="ai_mode.llm.openai"):
        response = client.post("/ai/v1/diagnosis/explain", json={
            "diagnosis_id": "diag_demo", "question": "private-question",
        })
    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == code and error["retryable"] is True
    assert hint in error["message"]
    assert "answer" not in response.json()
    assert all(value not in response.text + caplog.text for value in (
        "private-thought", "partial-private-answer", "private-question", "sk-private"))
    assert len(requests) == 1  # even configured transport retries cannot repeat a completed empty answer


@pytest.mark.parametrize("case,status,code", [
    ("malformed", 502, "AI_MODE_DIAGNOSIS_INVALID_RESPONSE"),
    ("timeout", 503, "AI_MODE_DIAGNOSIS_MODEL_TIMEOUT"),
    ("http503", 503, "AI_MODE_DIAGNOSIS_MODEL_UNAVAILABLE"),
    ("http401", 502, "AI_MODE_DIAGNOSIS_MODEL_FAILED"),
])
def test_explain_transport_and_structure_failures(client, monkeypatch, case, status, code):
    import httpx
    import ai_mode.diagnosis as diagnosis
    from ai_mode.llm.openai_compat import OpenAIClient

    calls = []
    def handle(request):
        calls.append(request)
        if case == "timeout":
            raise httpx.ReadTimeout("sk-private private-question", request=request)
        if case.startswith("http"):
            return httpx.Response(int(case[4:]), text="private-provider-body sk-private")
        return httpx.Response(200, json={"choices": [], "private": "private-provider-body"})
    model = OpenAIClient(base_url="https://example.invalid/v1", api_key="sk-private", model="test",
                         max_retries=0, http=httpx.Client(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(diagnosis, "fetch_diagnosis", lambda diagnosis_id, client=None: _diagnosis())
    monkeypatch.setattr(diagnosis, "build_client", lambda *args, **kwargs: model)
    response = client.post("/ai/v1/diagnosis/explain", json={"diagnosis_id": "diag_demo", "question": "private-question"})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "private" not in response.text
    assert len(calls) == 1


def test_explain_normal_openai_response_compatible(client, monkeypatch):
    import httpx
    import ai_mode.diagnosis as diagnosis
    from ai_mode.llm.openai_compat import OpenAIClient

    model = OpenAIClient(base_url="https://example.invalid/v1", api_key="test-key", model="test", http=httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "choices": [{"message": {"content": "  完整解释  ", "reasoning_content": "private-thought"}, "finish_reason": "stop"}],
        }))))
    monkeypatch.setattr(diagnosis, "fetch_diagnosis", lambda diagnosis_id, client=None: _diagnosis())
    monkeypatch.setattr(diagnosis, "build_client", lambda *args, **kwargs: model)
    response = client.post("/ai/v1/diagnosis/explain", json={"diagnosis_id": "diag_demo", "question": "请解释"})
    assert response.status_code == 200
    assert response.json()["answer"] == "完整解释"
    assert "private-thought" not in response.text


@pytest.mark.parametrize("body,ok,hint", [
    ({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}, True, "本次返回了正文"),
    ({"choices": [{"message": {"content": None, "reasoning_content": "private-thought"}, "finish_reason": "length"}]}, True, "本次未生成正文"),
    ({"choices": []}, False, "检查模型接口兼容性"),
])
def test_settings_test_route_preserves_connectivity_semantics(client, monkeypatch, body, ok, hint):
    import httpx
    import ai_mode.llm.factory as factory
    from ai_mode.llm.openai_compat import OpenAIClient

    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=body)
    model = OpenAIClient(base_url="https://example.invalid/v1", api_key="test-key", model="test", max_retries=0,
                         http=httpx.Client(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(factory, "_openai_factory", lambda config: model)
    response = client.post("/ai/v1/settings/test/llm")
    assert response.status_code == 200
    assert response.json()["ok"] is ok
    assert hint in response.json()["message"]
    if ok:
        assert "接口已连通" in response.json()["message"]
        assert "未验证完整解释生成" in response.json()["message"]
    assert "private-thought" not in response.text
    assert len(requests) == 1 and requests[0]["max_tokens"] == 1


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


def test_scientific_slashes_survive_evidence_projection():
    from ai_mode.diagnosis import _bounded_context, project_evidence

    science = "OUTCAR/job log 出现 ZHEGV/LAPACK；static/CHGCAR，eV/atom，FM/AFM，1/2"
    causes = ["结构畸变/重叠", "结构/泛函/U 问题", "元素顺序/数量不一致"]
    projected = project_evidence({
        "summary": {"headline": science},
        "issues": [{
            "issue_id": "blocking", "blocking": True, "summary": science,
            "possible_causes": causes,
            "evidence": [{"file": "OUTCAR", "message": science, "excerpt": science}],
            "recommendations": [{"rationale": science}],
        }],
        "missing_evidence": [science],
        "next_step": {"allowed": False, "reason": science},
        "recommended_fixes": [{"warnings": [science], "changes": [{"new_value": science}]}],
    })
    context, truncated = _bounded_context(projected)
    parsed = json.loads(context)
    assert parsed["summary"]["headline"] == science
    assert parsed["issues"][0]["summary"] == science
    assert parsed["issues"][0]["possible_causes"] == causes
    assert parsed["issues"][0]["evidence"][0]["excerpt"] == science
    assert parsed["issues"][0]["recommendations"][0]["rationale"] == science
    assert parsed["missing_evidence"] == [science]
    assert parsed["next_step"]["reason"] == science
    assert parsed["recommended_fixes"][0]["changes"][0]["new_value"] == science
    assert "[path]" not in context
    assert truncated is False


@pytest.mark.parametrize(("text", "expected"), [
    ("see C:/private/OUTCAR", "see [path]"),
    (r"读取C:\private\OUTCAR，ZHEGV/LAPACK 失败", "读取[path]，ZHEGV/LAPACK 失败"),
    ("读取/tmp/OUTCAR；保留 eV/atom", "读取[path]；保留 eV/atom"),
    ("路径：/home/用户/OUTCAR。", "路径：[path]。"),
    ("证据 (/tmp/OUTCAR) 和 [/var/log/job.log]", "证据 ([path]) 和 [[path]]"),
    ('路径 "C:/private/my run/OUTCAR" 缺失', '路径 "[path]" 缺失'),
    ("路径 '/tmp/my run/OUTCAR' 缺失", "路径 '[path]' 缺失"),
    ("路径 “/tmp/my run/OUTCAR” 缺失", "路径 “[path]” 缺失"),
    (r"缺少 \\server\share\OUTCAR；static/CHGCAR", "缺少 [path]；static/CHGCAR"),
    (r"读取\\server\share\OUTCAR。", "读取[path]。"),
    ("路径 //server/share/OUTCAR", "路径 [path]"),
    ("/OUTCAR 缺失，结构/泛函/U 问题", "[path] 缺失，结构/泛函/U 问题"),
])
def test_absolute_paths_are_redacted_at_prose_boundaries(text, expected):
    from ai_mode.diagnosis import _bounded_context, project_evidence

    context, truncated = _bounded_context(project_evidence({"summary": text}))
    assert json.loads(context)["summary"] == expected
    # Privacy redaction is distinct from omission caused by a context budget.
    assert truncated is False


def test_projection_marks_a_single_long_field_before_total_budget_is_reached():
    from ai_mode.diagnosis import MAX_DIAGNOSIS_CONTEXT, _bounded_context, project_evidence

    projected = project_evidence({
        "issues": [{"issue_id": "blocking", "blocking": True, "summary": "x" * 25_000}],
    })
    context, truncated = _bounded_context(projected)
    assert len(context) < MAX_DIAGNOSIS_CONTEXT
    assert json.loads(context)["issues"][0]["summary"] == "x" * 2_000
    assert truncated is True
    assert set(json.loads(context)) == {"issues"}


@pytest.mark.parametrize("field", [
    "summary", "issue", "evidence", "recommendation", "cause", "missing",
    "next_step", "fix", "warning", "change",
])
def test_nested_projection_field_budgets_mark_truncation(field):
    from ai_mode.diagnosis import _bounded_context, project_evidence

    long = "x" * 2_001
    records = {
        "summary": {"summary": {"headline": long}},
        "issue": {"issues": [{"title": long}]},
        "evidence": {"issues": [{"evidence": [{"message": long}]}]},
        "recommendation": {"issues": [{"recommendations": [{"rationale": long}]}]},
        "cause": {"issues": [{"possible_causes": [long]}]},
        "missing": {"missing_evidence": [long]},
        "next_step": {"next_step": {"reason": long}},
        "fix": {"recommended_fixes": [{"strategy": long}]},
        "warning": {"recommended_fixes": [{"warnings": [long]}]},
        "change": {"recommended_fixes": [{"changes": [{"new_value": long}]}]},
    }
    context, truncated = _bounded_context(project_evidence(records[field]))
    assert long not in context
    assert truncated is True
    assert json.loads(context)


@pytest.mark.parametrize(("record", "path", "limit"), [
    ({"missing_evidence": [f"missing-{i}" for i in range(101)]}, ("missing_evidence",), 100),
    ({"issues": [{"possible_causes": [f"cause-{i}" for i in range(33)]}]}, ("issues", 0, "possible_causes"), 32),
    ({"issues": [{"related_issue_ids": [f"i-{i}" for i in range(33)]}]}, ("issues", 0, "related_issue_ids"), 32),
    ({"issues": [{"recommendations": [{"rationale": str(i)} for i in range(33)]}]}, ("issues", 0, "recommendations"), 32),
    ({"recommended_fixes": [{"fix_id": f"f-{i}"} for i in range(33)]}, ("recommended_fixes",), 32),
    ({"recommended_fixes": [{"warnings": [str(i) for i in range(33)]}]}, ("recommended_fixes", 0, "warnings"), 32),
    ({"recommended_fixes": [{"issue_ids": [str(i) for i in range(33)]}]}, ("recommended_fixes", 0, "issue_ids"), 32),
    ({"recommended_fixes": [{"changes": [{"new_value": str(i)} for i in range(33)]}]}, ("recommended_fixes", 0, "changes"), 32),
])
def test_projection_list_budgets_mark_truncation(record, path, limit):
    from ai_mode.diagnosis import MAX_DIAGNOSIS_CONTEXT, _bounded_context, project_evidence

    context, truncated = _bounded_context(project_evidence(record))
    value = json.loads(context)
    for key in path:
        value = value[key]
    assert len(value) == limit
    assert len(context) < MAX_DIAGNOSIS_CONTEXT
    assert truncated is True


def test_projection_exact_limits_and_per_call_state():
    from ai_mode.diagnosis import _bounded_context, project_evidence

    record = {
        "summary": {"headline": "s" * 2_000},
        "issues": [{"summary": "i" * 2_000, "possible_causes": ["cause"] * 32}],
        "missing_evidence": ["missing"] * 100,
    }
    assert _bounded_context(project_evidence({"summary": "x" * 2_001}))[1] is True
    projected = project_evidence(record)
    context, truncated = _bounded_context(projected)
    assert json.loads(context) == record
    assert truncated is False
    assert isinstance(projected, dict)


def test_total_budget_marks_truncation_without_projection_clipping():
    from ai_mode.diagnosis import MAX_DIAGNOSIS_CONTEXT, _bounded_context, project_evidence

    record = {
        "issues": [{"issue_id": "blocking", "blocking": True, "summary": "blocked"}]
        + [{"issue_id": str(i), "summary": "x" * 2_000} for i in range(20)],
        "missing_evidence": ["OUTCAR missing"],
    }
    projected = project_evidence(record)
    assert projected == record
    context, truncated = _bounded_context(projected)
    parsed = json.loads(context)
    assert truncated is True
    assert len(context) <= MAX_DIAGNOSIS_CONTEXT
    assert parsed["issues"][0]["issue_id"] == "blocking"
    assert parsed["missing_evidence"] == record["missing_evidence"]


def test_initial_core_budget_accounts_for_json_escaping():
    from ai_mode.diagnosis import MAX_DIAGNOSIS_CONTEXT, _bounded_context, project_evidence

    projected = project_evidence({
        "summary": {"headline": "\x00" * 2_000},
        "issues": [{"issue_id": "blocking", "blocking": True, "summary": "blocked"}]
        + [{"issue_id": str(i), "summary": "d" * 2_000} for i in range(10)],
        "missing_evidence": ["\x00" * 500 for _ in range(32)],
    })
    context, truncated = _bounded_context(projected)
    parsed = json.loads(context)
    assert truncated is True
    assert len(context) <= MAX_DIAGNOSIS_CONTEXT
    assert parsed["issues"][0]["issue_id"] == "blocking"
    assert parsed["missing_evidence"]
    assert len(parsed["missing_evidence"]) < 32
    assert len(parsed["issues"]) < 11
    # The remaining space cannot hold another missing entry; optional details
    # may fill that smaller remainder after missing evidence gets priority.
    assert len(context) + 3_000 > MAX_DIAGNOSIS_CONTEXT


def test_many_blocking_identities_leave_room_for_core_json_keys():
    from ai_mode.diagnosis import MAX_DIAGNOSIS_CONTEXT, _bounded_context, project_evidence

    projected = project_evidence({
        "issues": [{
            "issue_id": "\x00" * 198 + str(i), "blocking": True, "severity": "critical",
        } for i in range(100)],
        "missing_evidence": ["OUTCAR missing"],
    })
    context, truncated = _bounded_context(projected)
    parsed = json.loads(context)
    assert len(context) <= MAX_DIAGNOSIS_CONTEXT
    assert truncated is True
    assert parsed["issues"]
    assert "missing_evidence" in parsed


@pytest.mark.parametrize("case", ["complete", "field", "list", "total"])
def test_model_prompt_and_response_agree_on_all_truncation_paths(client, monkeypatch, case):
    import ai_mode.diagnosis as diagnosis
    from ai_mode.llm.fake import FakeLLM

    record = _diagnosis()
    if case == "field":
        record["issues"][0]["summary"] = "x" * 25_000
    elif case == "list":
        record["missing_evidence"] = [f"missing-{i}" for i in range(101)]
    elif case == "total":
        record["issues"] += [{"issue_id": str(i), "summary": "x" * 2_000} for i in range(20)]

    captured = []

    class RecordingLLM(FakeLLM):
        def complete(self, messages, **kwargs):
            captured.extend(messages)
            return super().complete(messages, **kwargs)

    monkeypatch.setattr(diagnosis, "fetch_diagnosis", lambda diagnosis_id, client=None: record)
    monkeypatch.setattr(diagnosis, "build_client", lambda *args, **kwargs: RecordingLLM(queue=["解释结果"]))
    response = client.post("/ai/v1/diagnosis/explain", json={"diagnosis_id": "diag_demo", "question": "解释证据"})
    assert response.status_code == 200
    expected_truncated = case != "complete"
    assert response.json()["context_truncated"] is expected_truncated
    prompt = captured[1]["content"]
    marker = "否，已按优先级截断；不得声称未显示证据不存在" if expected_truncated else "是"
    assert f"上下文是否完整：{marker}\n" in prompt
    context = prompt.split("<context>\n", 1)[1].split("\n</context>", 1)[0]
    parsed = json.loads(context)
    assert len(context) <= diagnosis.MAX_DIAGNOSIS_CONTEXT
    assert "truncated" not in parsed
    if case == "field":
        assert len(parsed["issues"][0]["summary"]) == 2_000
    elif case == "list":
        assert len(parsed["missing_evidence"]) == 100
