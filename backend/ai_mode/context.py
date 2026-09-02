"""上下文占有率与快照策略（对齐 MODULE_INTERFACES §5.1：优先 C，重时降 B）。

- 占有率：估算当前快照在 LLM 上下文中占用比例（0~1），界面展示用。
- 策略 C：携带上下文原文；负担过重自动降级 B；B 只带摘要。
"""
from __future__ import annotations

import math

from .schemas import Session, SnapshotPolicy

# 软上限：默认上下文窗口 token 数（后续可接 LLM 配置）。
_DEFAULT_CONTEXT_BUDGET_TOKENS = 32_000
# 触发降级的占有率阈值。
_DOWNGRADE_THRESHOLD = 0.75
# 英文 token ~4 字符，中文按 ~2 字符放宽；取 3 保守。由 len(text)/3 估算。
_CHARS_PER_TOKEN = 3


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / _CHARS_PER_TOKEN))


def _policy_tokens(session: Session) -> int:
    """按当前策略估算上下文 token 占用。"""
    steps = len(session.plan.steps) if session.plan else 0
    jobs = len(session.jobs)
    head = sum(estimate_tokens(m.content) for m in session.messages[:8])  # 最近小结
    if session.snapshot.policy == SnapshotPolicy.C:
        full = sum(estimate_tokens(m.content) for m in session.messages)
        summary = estimate_tokens(session.snapshot.last_summary)
        goal = estimate_tokens(session.requirement.clarified_goal or
                               session.requirement.raw_goal) \
            if session.requirement else 0
        return full + summary + goal + head + steps * 12 + jobs * 8 + 200
    # B：项目+工序步+起止点+作业一览+最近对话小结
    return head + estimate_tokens(session.snapshot.last_summary) + steps * 12 \
        + jobs * 8 + 200


def context_occupancy(session: Session,
                      budget_tokens: int = _DEFAULT_CONTEXT_BUDGET_TOKENS) -> float:
    """返回 0~1 的上下文占有率（budget 为 0 时按 1.0 计，表示爆满）。"""
    if budget_tokens <= 0:
        return 1.0
    return min(1.0, _policy_tokens(session) / budget_tokens)


def decide_snapshot_policy(session: Session,
                           budget_tokens: int = _DEFAULT_CONTEXT_BUDGET_TOKENS,
                           threshold: float = _DOWNGRADE_THRESHOLD) -> SnapshotPolicy:
    """快照策略建议：优先 C；占有率超阈值时才降级 B。"""
    if context_occupancy(session, budget_tokens) >= threshold:
        return SnapshotPolicy.B
    return SnapshotPolicy.C


def apply_snapshot_policy(session: Session, policy: SnapshotPolicy,
                          summary: str = "") -> Session:
    """落定策略与摘要，并刷新占有率；返回同一会话对象。"""
    session.snapshot.policy = policy
    if policy == SnapshotPolicy.B and summary:
        session.snapshot.last_summary = summary
        session.messages = session.messages[:8]   # B 只留最近一段（轻量）
    session.snapshot.occupancy = context_occupancy(session)
    session.touch()
    return session


def occupancy_label(ratio: float) -> str:
    """占有率可读标签（界面展示用）。"""
    if ratio >= _DOWNGRADE_THRESHOLD:
        return "高"
    if ratio >= 0.5:
        return "中"
    return "低"