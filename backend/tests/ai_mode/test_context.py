"""上下文占有率与快照策略（M2）独立测试。"""

from ai_mode.context import (
    apply_snapshot_policy,
    context_occupancy,
    decide_snapshot_policy,
    estimate_tokens,
    occupancy_label,
)
from ai_mode.schemas import Message, Session, SnapshotPolicy


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1
    assert estimate_tokens("a" * 30) == 10


def test_fresh_low_occupancy():
    s = Session(project_id="p", title="t")
    assert context_occupancy(s) < 0.1
    assert decide_snapshot_policy(s) == SnapshotPolicy.C


def test_big_context_downgrades_to_B():
    s = Session(project_id="p", title="t")
    assert len(s.messages) == 0
    # 填充大量消息直到超阈值
    while context_occupancy(s) < 0.75:
        s.messages.append(Message(role="user", content="x" * 4000))
    assert decide_snapshot_policy(s) == SnapshotPolicy.B


def test_apply_policy_B_trims_and_keeps_summary():
    s = Session(project_id="p", title="t")
    for _ in range(20):
        s.messages.append(Message(role="user", content="一" * 10))
    before = len(s.messages)
    apply_snapshot_policy(s, SnapshotPolicy.B, summary="已完成 M1；下一步 M2")
    assert s.snapshot.policy == SnapshotPolicy.B
    assert s.snapshot.last_summary == "已完成 M1；下一步 M2"
    assert len(s.messages) <= 8
    assert 0 <= s.snapshot.occupancy <= 1
    assert before > 8


def test_occupancy_label():
    assert occupancy_label(0.2) == "低"
    assert occupancy_label(0.6) == "中"
    assert occupancy_label(0.9) == "高"


def test_policy_C_keeps_full_history():
    s = Session(project_id="p", title="t")
    for _ in range(30):
        s.messages.append(Message(role="user", content="短"))
    apply_snapshot_policy(s, SnapshotPolicy.C, summary="")
    assert len(s.messages) == 30