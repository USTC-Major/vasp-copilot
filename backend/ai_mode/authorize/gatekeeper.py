"""门卫（M5→M47）：对工具请求分级放行/拒绝/挂起（弹卡=申请提权）；同意按批覆盖但每条先检。

- 放行（ALLOW）：直接执行（含已获授权 grant 的高风险项，附所需提权 permits）。
- 挂起（HOLD）：生成 ConsentCard 交上层渲染；用户「同意本次/同意本批」后，
  把作用域键写入持久化 grants，重放该请求即 ALLOW+granted；
  「拒绝」写入 denials，再次出现同请求直接 DENY（不再骚扰用户）。
- 拒绝（DENY）：红线/敏感/已拒绝项，不产生卡片。

纯数据判定；授权落点在执行器与提交入口。
"""
from __future__ import annotations

import uuid
from typing import Callable, Iterable, Sequence

from ai_mode.llm.base import ToolRequest

from .models import ConsentCard, ConsentOutcome, RiskLevel, Verdict, VerdictKind
from .rules import classify as _classify

__all__ = ["AuthorizationGate", "evaluate", "authorize_batch", "confirm"]

DEFAULT_OPTIONS = ["同意本次", "同意本批", "拒绝"]


def _batch_key(tool: ToolRequest) -> str:
    """同一批同意作用域键：工具名 + 规范化参数签名。"""
    token = repr(sorted((tool.args or {}).items()))
    return f"{tool.name}|{_stable(token)}"


def _stable(text: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, text).hex


class AuthorizationGate:
    """每批任务建议新建实例；可注入外部 grants/denials（持久化的同意/拒绝记录）。"""

    def __init__(self, *, cwd=None, classify_callable: Callable | None = None):
        self.cwd = cwd
        self.classify = classify_callable or _classify
        self._approved: dict[str, str] = {}

    def evaluate(self, tool: ToolRequest, *, cwd=None, auto: bool = False,
                 grants: Iterable[str] | None = None,
                 denials: Iterable[str] | None = None) -> Verdict:
        """单条评估。

        - auto=True：挂起项不发卡片（测试/离线）。
        - grants/denials：持久化的同意/拒绝 batch_key 集合。
        """
        cwd = cwd or self.cwd
        if cwd is None:
            raise ValueError("评估命令类工具必须提供 cwd（计算目录）")
        risk, kind, reason, _card, permits = self.classify(tool, cwd=cwd)
        if kind is not VerdictKind.HOLD:
            return Verdict(kind, tool.name, risk, reason, permits=permits)
        key = _batch_key(tool)
        deny_set = set(denials or ())
        grant_set = set(grants or ())
        if key in self._approved:
            return Verdict(VerdictKind.ALLOW, tool.name, risk,
                           "本批已同意（作用域覆盖，仍已完成安全检查）",
                           granted=True, permits=permits)
        if key in deny_set:
            return Verdict(VerdictKind.DENY, tool.name, risk,
                           f"你已拒绝该操作授权：{reason}", permits=frozenset())
        if key in grant_set:
            self._approved[key] = "granted"
            return Verdict(VerdictKind.ALLOW, tool.name, risk,
                           "已获用户授权（弹卡同意），放行执行",
                           granted=True, permits=permits)
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
        """逐条评估整批（每条都先过检查）；已同意作用域自动复用。"""
        return [self.evaluate(t, cwd=cwd, auto=auto, grants=grants,
                              denials=denials) for t in tools]

    def grant(self, batch_keys: Iterable[str]) -> None:
        """用户同意后，把作用域键记入本批已批准。"""
        for key in batch_keys or ():
            self._approved.setdefault(key, "batch-granted")

    def confirm(self, card: ConsentCard, choice: str = "同意本次",
                note: str = "") -> ConsentOutcome:
        """处理一张卡片：同意/拒绝记录；同意则登记作用域（同意本批=整批复用）。"""
        choice = (choice or "").strip()
        if choice in ("拒绝", "deny", "Decline"):
            return ConsentOutcome(card.card_id, False, note or "用户拒绝",
                                  card.batch_key)
        if choice in ("同意本批", "allow_batch"):
            self.grant([card.batch_key])
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
    """无状态确认（不登记记忆；需要批内记忆请用 AuthorizationGate）。"""
    return AuthorizationGate().confirm(card, choice=choice, note=note)