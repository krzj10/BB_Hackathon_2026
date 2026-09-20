"""POST /api/assistant/message (B03).

The route is transport only: origin protection, session binding (the claimed
body session MUST equal the trusted header), context assembly from stored
state, then the bounded agent loop. An unconfigured self-hosted route answers
503 honestly - never a canned reply."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from ..agent.context_builder import build_context_block
from ..agent.executive_agent import AssistantUnavailableError
from ..contracts.api import AssistantMessageResponse
from ..contracts.domain import AssistantRequest
from .security import require_origin, require_session_header

logger = logging.getLogger("eva.api.assistant")

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


@router.post("/message", response_model=AssistantMessageResponse)
async def assistant_message(request: Request, body: AssistantRequest) -> AssistantMessageResponse:
    require_origin(request)
    header_session = require_session_header(request)
    if header_session != body.session_id:
        raise HTTPException(status_code=403, detail="session id does not match the caller")

    agent = getattr(request.app.state, "assistant_agent", None)
    if agent is None:
        raise HTTPException(status_code=503, detail="assistant is not initialized")

    clock = getattr(request.app.state, "clock", None) or (
        lambda: datetime.now(timezone.utc)
    )
    context = build_context_block(
        language=body.language,
        active_context=body.active_context,
        attention_repo=request.app.state.attention_repository,
        decision_repo=request.app.state.decision_repository,
        focus_service=request.app.state.focus_service,
        calendar_service_factory=getattr(request.app.state, "calendar_service_factory", None),
        now=clock(),
    )
    try:
        result = await agent.handle(body, context)
    except AssistantUnavailableError:
        raise HTTPException(
            status_code=503,
            detail="inference unavailable: no configured self-hosted route",
        ) from None

    return AssistantMessageResponse(
        request_id=body.request_id,
        session_id=body.session_id,
        reply_text=result.reply_text,
        language=body.language,
        active_context=result.active_context,
        proposed_action=result.proposed_action,
        tool_results=result.tool_results,
    )
