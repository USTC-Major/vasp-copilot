# -*- coding: utf-8 -*-
"""agent 决策协议：从 LLM 正文里解析「意图/工具」标记（M31）。

为了让执行链路完全由 LLM 决策驱动、又不依赖各家网关是否支持 native
tool_calls，采用「正文内嵌 JSON 标记」的 prompt 协议，由决策循环执行：

- 意图标记（可选；触发计算时放在正文最前，其余情况可省略，缺省视为 chat）：
      <<<INTENT>>>{"intent": "compute"}
  缺省/普通聊天均视为 chat（普通对话、概念问题直接回答）。
- 工具标记（可多个、可穿插在正文里）：
      <<<TOOL>>>{"name": "plan", "args": {...}}

解析用「花括号配平 + 字符串转义感知」的扫描，容忍嵌套对象/数组；
返回剥离标记后的干净正文与工具请求列表（未命中任何标记即纯聊天正文）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..llm.base import ToolRequest

INTENT_MARK = "<<<INTENT>>>"
TOOL_MARK = "<<<TOOL>>>"

_KNOWN_INTENTS = {"chat", "compute", "view"}


@dataclass
class ParsedTurn:
    """一次 LLM 完整回复的解析结果。"""

    intent: str = "chat"
    tools: list[ToolRequest] = field(default_factory=list)
    prose: str = ""


def _find_json_object(text: str, start: int):
    """从 start 起找首个 '{'，配平括号并给出 (结束下标, JSON 原文)；感知字符串转义。"""
    i = text.find("{", start)
    if i < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    j = i
    while j < len(text):
        ch = text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return j + 1, text[i:j + 1]
        j += 1
    return None


def _extract_spans(text: str, marker: str) -> list[tuple[int, int, str]]:
    """找所有 ``marker`` 标记及其后的配平 JSON，返回 [(起, 止, json原文), ...]。"""
    out: list[tuple[int, int, str]] = []
    i = 0
    while True:
        idx = text.find(marker, i)
        if idx < 0:
            break
        res = _find_json_object(text, idx + len(marker))
        if res is None:
            i = idx + len(marker)
            continue
        end, obj = res
        out.append((idx, end, obj))
        i = end
    return out


def _strip_spans(text: str, spans: list[tuple[int, int, str]]) -> str:
    if not spans:
        return text.strip()
    pieces: list[str] = []
    last = 0
    for start, end, _obj in sorted(spans, key=lambda s: s[0]):
        pieces.append(text[last:start])
        last = end
    pieces.append(text[last:])
    cleaned = re.sub(r"[ \t]*\n{2,}", "\n\n", "".join(pieces))
    return cleaned.strip()


def _load_json(obj: str, default):
    try:
        data = json.loads(obj)
        return data if isinstance(data, dict) else default
    except (json.JSONDecodeError, TypeError):
        return default


def parse_turn(text: str) -> ParsedTurn:
    """解析一轮回复：意图（缺省 chat）+ 工具请求列表 + 干净正文。"""
    text = text or ""
    intent = "chat"
    intent_spans: list[tuple[int, int, str]] = []
    for start, end, obj in _extract_spans(text, INTENT_MARK):
        intent_spans.append((start, end, obj))
        raw = str(_load_json(obj, {}).get("intent") or "").strip().lower()
        if raw in _KNOWN_INTENTS:
            intent = raw
        break  # 只取第一条意图标记
    tool_spans = _extract_spans(text, TOOL_MARK)
    tools: list[ToolRequest] = []
    for _start, _end, obj in tool_spans:
        data = _load_json(obj, {})
        name = str(data.get("name") or "").strip()
        if not name:
            continue
        args = data.get("args")
        if not isinstance(args, dict):
            args = {}
        rationale = str(data.get("reason") or data.get("rationale") or "").strip()
        tools.append(ToolRequest(name=name, args=args, rationale=rationale))
    prose = _strip_spans(text, intent_spans + tool_spans)
    return ParsedTurn(intent=intent, tools=tools, prose=prose)
def has_unclosed_marker(text: str) -> bool:
    """是否残留未闭合/被截断的协议标记（LLM 输出被 max_tokens 切断）。

    有标记但其后没有配平 JSON 的，视为输出被截断，需要提示 LLM 重发完整请求，
    而不是把裸标记当正文输出给用户。
    """
    for marker in (TOOL_MARK, INTENT_MARK):
        idx = 0
        while True:
            idx = text.find(marker, idx)
            if idx < 0:
                break
            if _find_json_object(text, idx + len(marker)) is None:
                return True
            idx += len(marker)
    return False
