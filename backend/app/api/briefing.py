"""POST /api/briefing/meeting (B03).

Google read failures map to honest 5xx; inference failures degrade INSIDE the
service to a deterministic, fully-grounded briefing (retrieval notes say so).
A successful briefing is announced via the durable outbox (BRIEFING_READY)
for live clients - REST stays authoritative either way."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from ..contracts.api import BriefingRequest, BriefingResponse
from ..google.auth import GoogleAuthError
from ..google.calendar import CalendarReadError
from ..google.http import GoogleHttpError
from .security import require_origin

logger = logging.getLogger("eva.api.briefing")

router = APIRouter(prefix="/api/briefing", tags=["briefing"])


@router.post("/meeting", response_model=BriefingResponse)
async def meeting_briefing(request: Request, body: BriefingRequest) -> BriefingResponse:
    require_origin(request)
    service = getattr(request.app.state, "briefing_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="briefing service not initialized")

    try:
        briefing = await service.generate(body.meeting_ref, body.language)
    except GoogleAuthError:
        raise HTTPException(
            status_code=503, detail="google account not connected; authorize first"
        ) from None
    except (GoogleHttpError, CalendarReadError, ValueError):
        # Provider/contract failures only - exception text may embed private
        # content or URLs and is never echoed.
        raise HTTPException(status_code=502, detail="meeting could not be read") from None

    outbox = getattr(request.app.state, "event_outbox_repository", None)
    if outbox is not None:
        from ..contracts.domain import BriefingReadyPayload, EventEnvelope

        try:
            outbox.append(
                EventEnvelope(
                    event_id=f"evt-briefing-{briefing.id}",
                    sequence=0,
                    session_id="eva-system",
                    occurred_at=datetime.now(timezone.utc),
                    type="briefing_ready",
                    payload=BriefingReadyPayload(briefing=briefing),
                )
            )
        except Exception as exc:  # live delivery hardening is A07
            logger.error("briefing live-event append failed (%s)", type(exc).__name__)

    return BriefingResponse(briefing=briefing)
