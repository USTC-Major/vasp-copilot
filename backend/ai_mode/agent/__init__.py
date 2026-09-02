"""AI 决策驱动执行层（M31）：intent 自决 + 工具决策循环。

- protocol：正文内嵌「意图/工具」标记的解析。
- tools：真实操作工具集（含安全门），由 LLM 决策调用。
- runner：多轮决策循环（stream / 非 stream），接入 chat 路由。
"""
from .protocol import INTENT_MARK, TOOL_MARK, ParsedTurn, parse_turn
from .runner import run_agent, run_agent_stream

__all__ = [
    "INTENT_MARK",
    "TOOL_MARK",
    "ParsedTurn",
    "parse_turn",
    "run_agent",
    "run_agent_stream",
]