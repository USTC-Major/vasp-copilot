from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ...agent.orchestrator import AgentOrchestrator
from ...schemas.api import ApiEnvelope
from .deps import get_request_id, settings, store
from .diagnosis import diagnosis_service

router = APIRouter()
orchestrator = AgentOrchestrator(settings, store, diagnosis_service)


class AgentHandleRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    command: str
    session_id: Optional[str] = None
    diagnosis_id: Optional[str] = None
    structured_call: Optional[dict[str, Any]] = None
    user_confirmed: bool = False


@router.post("/agent/handle", response_model=ApiEnvelope, tags=["agent"])
async def agent_handle(req: AgentHandleRequest,
                       x_request_id: str = Depends(get_request_id)) -> ApiEnvelope:
    structured = req.structured_call
    if structured is not None and isinstance(structured.get("name"), str):
        name = structured.get("name")
        arguments = dict(structured.get("arguments") or {})
        # request-level explicit gesture seeds generate_fix confirmation (MVP 12.2)
        if name == "generate_fix" and "user_confirmed" not in arguments:
            arguments["user_confirmed"] = req.user_confirmed
        structured = {"name": name, "arguments": arguments}
    response = orchestrator.handle(req.command, diagnosis_id=req.diagnosis_id,
                                   structured_call=structured)
    return ApiEnvelope(request_id=x_request_id,
                       data=response.model_dump(exclude_none=True))
