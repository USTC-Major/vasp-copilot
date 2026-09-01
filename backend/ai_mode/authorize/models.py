"""决策与授权模型（M5）：风险等级 / 裁决 / 确认卡片 / 确认结果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RiskLevel(str, Enum):
    """风险分级（低/中/高）。低危险参数 AI 直接采纳，中/高需卡片确认。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class VerdictKind(str, Enum):
    """门卫裁决：放行 / 拒绝（说明原因） / 挂起（弹确认卡片）。"""

    ALLOW = "allow"
    DENY = "deny"
    HOLD = "hold"


@dataclass
class Verdict:
    """单个工具请求的裁决结果。"""

    kind: VerdictKind
    tool: str = ""
    risk: RiskLevel = RiskLevel.LOW
    reason: str = ""
    card: "ConsentCard | None" = None
    granted: bool = False               # 已获用户授权（弹卡同意）后放行
    permits: frozenset[str] = frozenset()  # 所需提权：hold / out_of_bounds_write

    @property
    def allowed(self) -> bool:
        return self.kind is VerdictKind.ALLOW

    @property
    def escalated(self) -> bool:
        return self.kind is VerdictKind.ALLOW and self.granted


@dataclass
class ConsentCard:
    """确认卡片：提供可点选项 + 「其他」自定义入口（前端渲染）。

    :param options: 预设选项；前端总会在末尾追加「其他（自定义…）」。
    :param batch_key: 同批复用放行的作用域键（如作业/命令签名）。
    """

    card_id: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    risk: RiskLevel = RiskLevel.MEDIUM
    reason: str = ""
    options: list[str] = field(default_factory=lambda: ["同意本次", "同意本批", "拒绝"])
    batch_key: str = ""


@dataclass
class ConsentOutcome:
    """用户对一张卡片的处理结果。"""

    card_id: str
    approved: bool
    note: str = ""
    batch_key: str = ""