"""Stateless risk gate used before creating exact, single-use actions.

Legacy grant/denial inputs are accepted for API compatibility but never alter
the verdict. A HOLD is only a request to create one consent action; it is not
a reusable permission and never replays the original LLM request.
"""
from __future__ import annotations

import uuid
from typing import Callable, Iterable, Sequence

from ai_mode.llm.base import ToolRequest

from .models import ConsentCard, ConsentOutcome, RiskLevel, Verdict, VerdictKind
from .rules import classify as _classify

__all__ = ["AuthorizationGate", "evaluate", "authorize_batch", "confirm"]

DEFAULT_OPTIONS = ["同意本次", "拒绝"]


def _batch_key(tool: ToolRequest) -> str:
    """同一批同意作用域键：工具名 + 规范化参数签名。"""
    token = repr(sorted((tool.args or {}).items()))
    return f"{tool.name}|{_stable(token)}"


def _stable(text: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, text).hex


class AuthorizationGate:
    """Stateless compatibility gate; historical grants never authorize work."""

    def __init__(self, *, cwd=None, classify_callable: Callable | None = None):
        self.cwd = cwd
        self.classify = classify_callable or _classify

    def evaluate(self, tool: ToolRequest, *, cwd=None, auto: bool = False,
                 grants: Iterable[str] | None = None,
                 denials: Iterable[str] | None = None) -> Verdict:
        """单条评估。

        - auto=True：挂起项不发卡片（测试/离线）。
        - grants/denials：兼容参数；不会影响裁决。
        """
        cwd = cwd or self.cwd
        if cwd is None:
            raise ValueError("评估命令类工具必须提供 cwd（计算目录）")
        risk, kind, reason, _card, permits = self.classify(tool, cwd=cwd)
        if kind is not VerdictKind.HOLD:
            return Verdict(kind, tool.name, risk, reason, permits=permits)
        key = _batch_key(tool)
        # v0.2.1：历史 grants/denials/batch memory 不再改变裁决。每个副作用
        # 必须由 consent 层的精确、单次 action 执行，绝不重放原 LLM 请求。
        if auto:
            return Verdict(VerdictKind.DENY, tool.name, risk,
                           "自动模式不放行未批准操作")
        card = ConsentCard(
            card_id=uuid.uuid4().hex,
            tool=tool.name,
            args=dict(tool.args or {}),
            risk=risk,
            reason=reason,
            options=list(DEFAULT_OPTIONS),
            batch_key=key,
        )
        return Verdict(VerdictKind.HOLD, tool.name, risk, reason,
                       card=card, permits=permits)

    def authorize_batch(self, tools: Sequence[ToolRequest], *, cwd=None,
                        auto: bool = False,
                        grants: Iterable[str] | None = None,
                        denials: Iterable[str] | None = None) -> list[Verdict]:
        """逐条独立评估整批；不会复用任何历史确认。"""
        return [self.evaluate(t, cwd=cwd, auto=auto, grants=grants,
                              denials=denials) for t in tools]

    def grant(self, batch_keys: Iterable[str]) -> None:
        """兼容空实现：v0.2.1 禁止批量或可复用 grant。"""
        return None

    def confirm(self, card: ConsentCard, choice: str = "同意本次",
                note: str = "") -> ConsentOutcome:
        """Resolve one compatibility card; batch authorization is rejected."""
        choice = (choice or "").strip()
        if choice in ("拒绝", "deny", "Decline"):
            return ConsentOutcome(card.card_id, False, note or "用户拒绝",
                                  card.batch_key)
        if choice in ("同意本批", "allow_batch"):
            return ConsentOutcome(card.card_id, False,
                                  note or "批量授权已禁用", card.batch_key)
        return ConsentOutcome(card.card_id, True, note or "用户同意",
                              card.batch_key)


def evaluate(tool: ToolRequest, *, cwd, grants=None, denials=None) -> Verdict:
    """无状态便捷评估（不含本批同意记忆；grants/denials 由调用方注入）。"""
    return AuthorizationGate(cwd=cwd).evaluate(tool, cwd=cwd, grants=grants,
                                               denials=denials)


def authorize_batch(tools, *, cwd, auto=False, grants=None, denials=None):
    """无状态批量评估（每条独立检查，无批内记忆）。"""
    return AuthorizationGate(cwd=cwd).authorize_batch(tools, cwd=cwd, auto=auto,
                                                      grants=grants,
                                                      denials=denials)


def confirm(card: ConsentCard, choice: str = "同意本次",
            note: str = "") -> ConsentOutcome:
    """无状态确认；不登记、复用或继承任何授权。"""
    return AuthorizationGate().confirm(card, choice=choice, note=note)
