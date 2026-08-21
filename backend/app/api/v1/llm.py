from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ...core.config import LlmConfig
from ...llm import runtime
from ...llm.openai_provider import OpenAiExplainer
from ...schemas.api import ApiEnvelope
from .deps import get_request_id, settings

router = APIRouter(tags=['llm'])


def _config_path() -> Path:
    return Path(settings.data_dir) / 'llm_config.json'


def _config_summary(cfg: LlmConfig, source: str) -> dict:
    key = cfg.api_key
    masked = (key[:4] + '****' + key[-4:]) if len(key) > 8 else ('****' if key else '')
    return {
        'enabled': cfg.enabled,
        'base_url': cfg.base_url,
        'model': cfg.model,
        'api_key_set': bool(key),
        'api_key_masked': masked,
        'source': source,
        'usable': cfg.usable,
        'timeout_seconds': cfg.timeout_seconds,
        'max_retries': cfg.max_retries,
        'max_tokens': cfg.max_tokens,
        'temperature': cfg.temperature,
    }


def _active_summary() -> dict:
    active = runtime.get_active()
    if active is not None:
        return _config_summary(active, 'runtime')
    return _config_summary(settings.llm, 'env')


class LlmConfigUpdate(BaseModel):
    model_config = ConfigDict(extra='ignore')
    enabled: bool = True
    base_url: str = ''
    api_key: str = ''
    model: str = ''
    timeout_seconds: float = 30.0
    max_retries: int = 1
    max_tokens: int = 1024
    temperature: float = 0.2


class LlmTestRequest(BaseModel):
    model_config = ConfigDict(extra='ignore')
    enabled: 'bool | None' = None
    base_url: 'str | None' = None
    api_key: 'str | None' = None
    model: 'str | None' = None
    timeout_seconds: 'float | None' = None


@router.get('/llm/config', response_model=ApiEnvelope)
async def get_llm_config(x_request_id: str = Depends(get_request_id)) -> ApiEnvelope:
    return ApiEnvelope(request_id=x_request_id, data=_active_summary())


@router.post('/llm/config', response_model=ApiEnvelope)
async def update_llm_config(req: LlmConfigUpdate,
                            x_request_id: str = Depends(get_request_id)) -> ApiEnvelope:
    base_url = req.base_url.strip() or settings.llm.base_url
    model = req.model.strip() or settings.llm.model
    prev = runtime.get_active()
    api_key = req.api_key or (prev.api_key if prev is not None else settings.llm.api_key)
    cfg = LlmConfig(
        enabled=req.enabled,
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=req.timeout_seconds,
        max_retries=req.max_retries,
        max_tokens=req.max_tokens,
        temperature=req.temperature,
    )
    runtime.set_active(cfg)
    runtime.save(_config_path(), cfg)
    return ApiEnvelope(request_id=x_request_id, data=_config_summary(cfg, 'runtime'))


@router.delete('/llm/config', response_model=ApiEnvelope)
async def reset_llm_config(x_request_id: str = Depends(get_request_id)) -> ApiEnvelope:
    runtime.set_active(None)
    runtime.clear_disk(_config_path())
    return ApiEnvelope(request_id=x_request_id, data=_active_summary())


@router.post('/llm/config/test', response_model=ApiEnvelope)
async def test_llm_config(req: LlmTestRequest,
                          x_request_id: str = Depends(get_request_id)) -> ApiEnvelope:
    base = runtime.get_active() or settings.llm
    cfg = LlmConfig(
        enabled=True,
        base_url=(req.base_url or base.base_url).strip(),
        api_key=req.api_key if req.api_key is not None else base.api_key,
        model=(req.model or base.model).strip(),
        timeout_seconds=req.timeout_seconds or base.timeout_seconds,
        max_retries=1,
        max_tokens=16,
        temperature=0.0,
    )
    if not cfg.usable:
        return ApiEnvelope(request_id=x_request_id,
                           data={'ok': False, 'message': '未配置可用的 API Key'})
    try:
        reply = OpenAiExplainer(cfg).test()
        return ApiEnvelope(request_id=x_request_id,
                           data={'ok': True, 'message': '连接成功', 'reply': reply})
    except Exception as exc:  # noqa: BLE001 - 测试失败返回消息而非异常
        return ApiEnvelope(request_id=x_request_id,
                           data={'ok': False, 'message': str(exc)})