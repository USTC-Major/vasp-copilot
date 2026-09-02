"""决策与授权门子包（M5）：工具请求 -> 风险分级 -> 放行/拒绝/挂起（确认卡片）。

- 对齐安全边界 §1.3：黑名单/越界一律拒；提交/覆盖/删除等高风险在此要用户同意。
- 一次同意可覆盖整批（每作业仍先单独过检查）。
- 纯数据判定，不碰系统；挂起时由上层（后端/前端）渲染确认卡片。
"""

from .models import RiskLevel, VerdictKind, Verdict, ConsentCard, ConsentOutcome
from .rules import classify
from .gatekeeper import AuthorizationGate, evaluate, confirm

__all__ = [
    "RiskLevel", "VerdictKind", "Verdict",
    "ConsentCard", "ConsentOutcome",
    "classify",
    "AuthorizationGate", "evaluate", "confirm",
]