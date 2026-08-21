from __future__ import annotations

from ..core.config import Settings
from . import runtime
from .base import Explainer
from .openai_provider import OpenAiExplainer

_current: 'Explainer | None' = None


def get_explainer(settings: 'Settings | None' = None) -> 'Explainer | None':
    # Return the injected/current explainer; else build an OpenAI-compatible one
    # from the runtime override when present, otherwise env config
    # (MVP ENABLE_LLM gate).
    if _current is not None:
        return _current
    if settings is not None:
        cfg = runtime.resolve(settings.llm)
        if cfg.usable:
            return OpenAiExplainer(cfg)
    return None


def set_explainer(explainer: 'Explainer | None') -> None:
    global _current
    _current = explainer


def reset_explainer() -> None:
    global _current
    _current = None
