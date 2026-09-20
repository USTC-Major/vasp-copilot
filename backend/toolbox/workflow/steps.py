"""M8 工序：8 步固定顺序 + 任意起止 + 覆盖范围（对齐 WORKFLOW.md v14 §2）。

工序固定为 8 步，默认全流程，但支持任意起止：可从任意一步进入、到任意一步
结束；开工前必须先明示本次覆盖范围（start->end，只能正序不能倒序）。
本模块只做顺序/覆盖/合法性强约束，不含 LLM 与执行。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: 固定顺序（8 步）。此顺序为唯一权威顺序，改动会影响全链路判定。
STEP_KEYS: list[str] = [
    "understand",
    "plan",
    "prepare_input",
    "setup",
    "precheck",
    "submit_monitor",
    "finish",
    "report",
]

#: 展示名（供 UI/日志）。
STEP_LABELS: dict[str, str] = {
    "understand": "理解需求",
    "plan": "规划作业",
    "prepare_input": "准备输入",
    "setup": "连接超算搭建",
    "precheck": "提交前检查",
    "submit_monitor": "提交与监控",
    "finish": "作业结束确认",
    "report": "结果与报告",
}

_INDEX: dict[str, int] = {key: i for i, key in enumerate(STEP_KEYS)}


def _norm_key(text: str) -> str:
    """规范化别名键：去首尾空白、转小写、把内部中划线转下划线。"""
    return re.sub(r"[\-_]", "_", text.strip().lower())


_ALIASES: dict[str, str] = {
    "理解": "understand",
    "规划": "plan",
    "准备": "prepare_input",
    "搭建": "setup",
    "连接超算": "setup",
    "检查": "precheck",
    "提交前检查": "precheck",
    "提交检查": "precheck",
    "监控": "submit_monitor",
    "提交与监控": "submit_monitor",
    "结束确认": "finish",
    "确认": "finish",
    "报告": "report",
}
_ALIASES.update({label: key for key, label in STEP_LABELS.items()})
_ALIASES.update({
    "理解": "understand", "规划": "plan", "准备": "prepare_input",
    "搭建": "setup", "连接超算": "setup", "检查": "precheck",
    "提交前检查": "precheck", "提交检查": "precheck",
    "监控": "submit_monitor", "提交与监控": "submit_monitor",
    "作业结束确认": "finish", "结束确认": "finish",
    "确认": "finish", "报告": "report",
})
_ALIASES.update({str(i): key for i, key in enumerate(STEP_KEYS, start=1)})
_ALIASES = {_norm_key(k): v for k, v in _ALIASES.items()}


def _lookup(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return STEP_KEYS[value - 1] if 1 <= value <= len(STEP_KEYS) else None
    if value is None:
        return None
    key = _norm_key(str(value))
    if key in _ALIASES:
        return _ALIASES[key]
    if key in _INDEX:
        return STEP_KEYS[_INDEX[key]]
    if key.isdigit() and 1 <= int(key) <= len(STEP_KEYS):
        return STEP_KEYS[int(key) - 1]
    return None


def normalize_step(value: Any, *, default: str | None = None) -> str | None:
    """把别名/位置号/中英文标签解析为规范 key；无法解析返回 default。"""
    result = _lookup(value)
    return result if result is not None else default


def require_step(value: Any) -> str:
    """无法解析时抛 ValueError（供强约束入口使用）。"""
    result = _lookup(value)
    if result is None:
        raise ValueError(f"未知工序步: {value!r}；可选: {STEP_KEYS}")
    return result


def is_step(value: Any) -> bool:
    """判断是否为合法工序步（别名亦可）。"""
    return _lookup(value) is not None


def step_index(value: Any) -> int:
    """返回 0 基索引；无法解析抛 ValueError。"""
    return _INDEX[require_step(value)]


def step_label(key: str) -> str:
    return STEP_LABELS.get(key, key)


def step_after(key: str) -> str | None:
    """返回固定顺序中的后一步；已是最后一步返回 None。"""
    idx = step_index(key)
    return STEP_KEYS[idx + 1] if idx + 1 < len(STEP_KEYS) else None


def step_before(key: str) -> str | None:
    """返回固定顺序中的前一步；已是第一步返回 None。"""
    idx = step_index(key)
    return STEP_KEYS[idx - 1] if idx > 0 else None


@dataclass
class Coverage:
    """本次覆盖的工序范围（任意起止、只能正序）。"""

    start: str
    end: str
    steps: list[str]
    labels: list[str]
    text: str    # 如 "理解需求 -> 提交前检查（6 步）"
    full: bool   # 是否为全流程

    def __str__(self) -> str:
        return self.text


def coverage(start: Any, end: Any) -> Coverage:
    """根据任意起止解析本次覆盖范围；顺序非法或未知步抛 ValueError。"""
    start_key = require_step(start)
    end_key = require_step(end)
    si, ei = step_index(start_key), step_index(end_key)
    if ei < si:
        raise ValueError(
            f"覆盖范围逆序: {start_key!r}(#{si + 1}) -> {end_key!r}(#{ei + 1})"
        )
    steps = STEP_KEYS[si:ei + 1]
    labels = [step_label(s) for s in steps]
    full = steps == list(STEP_KEYS)
    text = " -> ".join([labels[0], labels[-1]]) + f"（{len(steps)} 步）"
    return Coverage(start=start_key, end=end_key, steps=steps,
                    labels=labels, text=text, full=full)


def coverage_text(start: Any, end: Any) -> str:
    """返回覆盖范围的中文描述字串（开工前明示用）。"""
    return str(coverage(start, end))
