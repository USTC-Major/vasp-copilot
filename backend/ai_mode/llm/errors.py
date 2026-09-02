"""LLM 客户端错误（对齐安全边界：LLM 不可用 = 智能模式整体瘫痪）。"""
from __future__ import annotations


class LLMError(Exception):
    """LLM 调用通用错误基类。"""


class LLMUnavailableError(LLMError):
    """LLM 不可用（连接失败/超时/服务端 5xx/429）。

    按安全边界：LLM 不可用 = 智能模式整体瘫痪，无规则兜底；由中枢转成提示。
    """


class LLMBadRequestError(LLMError):
    """请求本身有问题（4xx，重试无益）。"""