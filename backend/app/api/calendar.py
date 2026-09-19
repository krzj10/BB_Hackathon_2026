"""Calendar read routes (A02): GET /api/calendar/today and
GET /api/calendar/events/{event_id}?calendar_id=...

Read-only by construction: this module exposes no mutation path, and the
underlying service issues only GET requests. Guarded writes arrive with A04's
executor - never through an API route."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request

from ..contracts.api import MeetingResponse, TodayCalendarResponse
from ..google.auth import GoogleAuth
from ..google.calendar import CalendarReadError, CalendarService
from ..google.http import AuthorizedGoogleHttp, GoogleHttpError

logger = logging.getLogger("eva.api.calendar")

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


def _service(request: Request) -> CalendarService:
    auth: GoogleAuth | None = getattr(request.app.state, "google_auth", None)
    if auth is None:
        raise HTTPException(status_code=503, detail="google auth is not initialized")
    try:
        session = auth.authorized_session()
    except Exception as exc:  # sanitized GoogleAuthError messages only
        raise HTTPException(
            status_code=503,
            detail=f"calendar unavailable: {exc}; start authorization at /api/auth/google/start",
        ) from None
    factory = getattr(request.app.state, "google_http_factory", None)
    http = factory(session) if factory is not None else AuthorizedGoogleHttp(session)
    return CalendarService(http, timezone_name=request.app.state.settings.eva_timezone)


@router.get("/today", response_model=TodayCalendarResponse)
def today(request: Request) -> TodayCalendarResponse:
    try:
        return _service(request).today()
    except (GoogleHttpError, CalendarReadError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None


@router.get("/events/{event_id}", response_model=MeetingResponse)
def get_event(
    request: Request,
    event_id: str,
    calendar_id: str = Query(default="primary"),
) -> MeetingResponse:
    try:
        return MeetingResponse(
            meeting=_service(request).get_event(calendar_id=calendar_id, event_id=event_id)
        )
    except (GoogleHttpError, CalendarReadError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except ValueError as exc:  # malformed payload for the requested event
        raise HTTPException(status_code=502, detail=f"event {event_id!r} could not be read") from None
