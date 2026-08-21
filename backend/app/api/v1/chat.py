from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ...llm import get_explainer
from ...schemas.api import ApiEnvelope
from ...services.chat_store import ChatHistoryStore
from .deps import get_request_id, settings

router = APIRouter(tags=['chat'])


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra='ignore')
    role: str = 'user'
    content: str = ''


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra='ignore')
    message: str = Field(..., min_length=1, max_length=4000)
    history: list[ChatMessage] = []


class ChatHistoryRequest(BaseModel):
    model_config = ConfigDict(extra='ignore')
    messages: list[ChatMessage] = []


def _history_store() -> ChatHistoryStore:
    return ChatHistoryStore(Path(settings.data_dir) / 'chat_history.json')


@router.post('/chat', response_model=ApiEnvelope)
async def chat(req: ChatRequest,
               x_request_id: str = Depends(get_request_id)) -> ApiEnvelope:
    if not req.message.strip():
        return ApiEnvelope(request_id=x_request_id,
                           data={'answer': '请输入内容。', 'usable': True})
    explainer = get_explainer(settings)
    if explainer is None:
        return ApiEnvelope(request_id=x_request_id, data={
            'answer': 'AI 助手未启用。请先在右上角「模型设置」填写接口地址 / Key / 模型名并保存。',
            'usable': False,
        })
    history = [m.model_dump() for m in req.history
               if m.role in ('user', 'assistant') and m.content.strip()]
    try:
        answer = explainer.chat_general(req.message, history)
        return ApiEnvelope(request_id=x_request_id,
                           data={'answer': answer, 'usable': True})
    except Exception:  # noqa: BLE001 - 对话失败返回可读消息而非异常
        return ApiEnvelope(request_id=x_request_id, data={
            'answer': '大模型服务暂不可用，请检查右上角「模型设置」后重试。',
            'degraded': True,
            'usable': True,
        })


@router.get('/chat/history', response_model=ApiEnvelope)
async def get_chat_history(x_request_id: str = Depends(get_request_id)) -> ApiEnvelope:
    return ApiEnvelope(request_id=x_request_id,
                       data={'messages': _history_store().get(), 'persisted': True})


@router.post('/chat/history', response_model=ApiEnvelope)
async def save_chat_history(req: ChatHistoryRequest,
                            x_request_id: str = Depends(get_request_id)) -> ApiEnvelope:
    saved = _history_store().save([m.model_dump() for m in req.messages])
    return ApiEnvelope(request_id=x_request_id,
                       data={'messages': saved, 'persisted': True})


@router.delete('/chat/history', response_model=ApiEnvelope)
async def clear_chat_history(x_request_id: str = Depends(get_request_id)) -> ApiEnvelope:
    _history_store().clear()
    return ApiEnvelope(request_id=x_request_id,
                       data={'messages': [], 'persisted': True})