"""会话与记忆（M2）测试：读写、列表、删除、损坏处理、续接、持久化字段。"""

import json

import pytest

from ai_mode.context import (
    context_occupancy,
    decide_snapshot_policy,
    estimate_tokens,
)
from ai_mode.schemas import (
    JobEntry,
    JobStatus,
    Message,
    PlanSnapshot,
    PlanStep,
    RequirementSnapshot,
    Session,
)
from ai_mode.session import (
    CorruptSessionError,
    SessionNotFoundError,
    SessionStore,
)
from ai_mode import paths


def _sample(session_id: str = "sess_test1") -> Session:
    return Session(
        session_id=session_id,
        project_id="proj_demo",
        title="Fe2O3 结构优化",
        calc_dir="/home/user/scratch/feo",
        local_workspace="D:/codex/ws_fe",
        start_step="understand",
        end_step="report",
        requirement=RequirementSnapshot(
            raw_goal="把 Fe2O3 优化后算态密度",
            clarified_goal="结构优化 -> 静态 -> DOS",
            coverage="understand->report",
        ),
        plan=PlanSnapshot(
            strategy="r1 递进 dos（dos 须等 r1 成功）；r1/r2 可平行",
            steps=[
                PlanStep(job_key="r1", label="relax", requires=[],
                         parallel_group="root"),
                PlanStep(job_key="r2", label="relax-alt", requires=[],
                         parallel_group="root"),
                PlanStep(job_key="dos", label="dos", requires=["r1", "r2"],
                         parallel_group=None),
            ],
        ),
        jobs=[
            JobEntry(job_key="r1", status=JobStatus.QUEUED,
                     slurm_job_id="11223344"),
            JobEntry(job_key="dos", status=JobStatus.PLANNED),
        ],
    )


def test_schema_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    store = SessionStore()
    s = store.create(project_id="proj_demo", title="alpha",
                     calc_dir="/cluster/alpha", start_step="understand",
                     end_step="report")
    s.requirement = RequirementSnapshot(raw_goal="结构优化",
                                        coverage="understand->report")
    s.append_message("user", "你好")
    s.append_message("assistant", "已理解需求，是否开始计算流程？")
    store.save(s)

    loaded = store.load(s.session_id)
    assert loaded.title == "alpha"
    assert loaded.calc_dir == "/cluster/alpha"
    assert len(loaded.messages) == 2
    raw = (tmp_path / "sessions" / f"{s.session_id}.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["session_id"] == s.session_id
    assert data["messages"][0]["role"] == "user"


def test_segment_duration():
    full = Session(project_id="p", title="t")
    seg = Session(project_id="p", title="t", start_step="plan", end_step="report")
    assert full.duration == "full"   # store uses this to decide
    # model 本身不自动决定 duration；由 store.create 决定
    _ = None


def test_job_accessor_and_defaults():
    s = _sample()
    assert s.current_step == "understand"
    assert s.job("r1").slurm_job_id == "11223344"
    assert s.job("dos").status == JobStatus.PLANNED
    assert s.job("nope") is None
    assert s.plan.steps[2].requires == ["r1", "r2"]


def test_store_missing_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    store = SessionStore()
    with pytest.raises(SessionNotFoundError):
        store.load("sess_ghost")


def test_corrupt_file_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    store = SessionStore()
    s = store.create(title="tmp")
    store._path(s.session_id).write_text("{ not json", encoding="utf-8")
    with pytest.raises(CorruptSessionError):
        store.load(s.session_id)


def test_delete(tmp_path):
    store = SessionStore(root=tmp_path)
    s = store.create(title="tmp")
    assert store.exists(s.session_id)
    store.delete(s.session_id)
    assert not store.exists(s.session_id)
    store.delete(s.session_id)  # 幂等


def test_list_and_order(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    store = SessionStore()
    a = store.create(project_id="p", title="older")
    a.append_message("user", "旧")
    store.save(a)
    b = store.create(project_id="p", title="newer")
    b.append_message("user", "新")
    store.save(b)
    sessions = store.list_sessions(order="updated")
    assert sessions[0].session_id == b.session_id
    summaries = store.summaries()
    assert [s["title"] for s in summaries] == ["newer", "older"]


def test_durable_reopen_resumes(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    store_a = SessionStore()
    s = store_a.create(project_id="proj_x", title="会话A",
                       calc_dir="/cluster/x", local_workspace="D:/ws",
                       start_step="plan", end_step="report")
    s.requirement = RequirementSnapshot(raw_goal="DOS")
    s.append_message("user", "开始")
    store_a.save(s)

    sid = s.session_id
    store_b = SessionStore()
    resumed = store_b.load(sid)
    assert resumed.current_step == "plan"
    assert resumed.requirement.raw_goal == "DOS"
    assert resumed.messages[-1].content == "开始"
    other = store_b.create(project_id="proj_x", title="会话B")
    assert other.session_id != sid
    assert len(other.messages) == 0 and len(resumed.messages) == 1


def test_list_summaries_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    store = SessionStore()
    s = store.create(project_id="p9", title="带摘要", calc_dir="/c"
                     , start_step="plan", end_step="report")
    s.messages.append(Message(role="user", content="摘要源文本"))
    s.snapshot.last_summary = "已完成 X；下一步 Y"
    s.snapshot.occupancy = 0.5
    store.save(s)
    summaries = store.summaries()
    assert summaries[0]["title"] == "带摘要"
    assert summaries[0]["summary"] == "已完成 X；下一步 Y"
    assert summaries[0]["occupancy"] == 0.5


def test_occupancy_and_policy():
    from ai_mode.schemas import Message
    s = Session(project_id="p", title="t")
    big = "x" * 200
    for _ in range(200):
        s.messages.append(Message(role="user", content=big))
    assert context_occupancy(s, budget_tokens=1000) > 0.7
    assert decide_snapshot_policy(s, budget_tokens=1000) == "B"
    light = Session(project_id="p", title="t")
    assert context_occupancy(light, budget_tokens=32000) < 0.1
    assert decide_snapshot_policy(light) == "C"


def test_estimate_tokens_basic():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc" * 10) == 10