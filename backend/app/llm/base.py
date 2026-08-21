from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..schemas.result import DiagnosisResult


@runtime_checkable
class Explainer(Protocol):
    # Produce a plain-language Chinese explanation of a DiagnosisResult.
    def explain(self, result: DiagnosisResult) -> str: ...

    # Answer a follow-up question strictly from the stored DiagnosisResult.
    def chat(self, result: DiagnosisResult, question: str) -> str: ...

    # Generic multi-turn assistant chat (standalone chat panel, no diagnosis context).
    def chat_general(self, question: str, history: 'list | None' = None) -> str: ...


class StubExplainer:
    # Deterministic offline fallback (MVP no-LLM mode): template-style text.
    def __init__(self, text: str = 'zero') -> None:
        self._text = text or 'zero'

    def explain(self, result: DiagnosisResult) -> str:
        return self._text

    def chat(self, result: DiagnosisResult, question: str) -> str:
        return 'zero'

    def chat_general(self, question: str, history: 'list | None' = None) -> str:
        return 'zero'

    def complete(self, messages: list) -> str:
        # Deterministic no-LLM completion for the AgentOrchestrator.
        return 'zero'