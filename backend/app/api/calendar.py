"""Calendar routes.

Reads (A02): GET /api/calendar/today and GET /api/calendar/events/{event_id}.
Proposals (A04): POST /api/calendar/proposals builds a canonical ToolCall,
loads current target state, runs the A03 approval engine and persists the
ProposedAction - it NEVER calls a Google mutation directly. Execution only
happens through the guarded ToolExecutor (after approval, or immediately for
requires_approval=false proposals after durable creation + revalidation +
atomic no-approval claim)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from ..approvals.engine import ActionPolicyError, ProposalContext, arguments_digest, canonical_arguments
from ..contracts.api import (
    MeetingResponse,
    ProposedActionResponse,
    TodayCalendarResponse,
)
from ..contracts.domain import (
    CalendarProposalArguments,
    CalendarRescheduleEventArguments,
    CalendarUpdateAgendaArguments,
    ToolCall,
)
from ..google.auth import GoogleAuth, GoogleAuthError
from ..google.calendar import CalendarReadError, CalendarService
from ..google.http import AuthorizedGoogleHttp, GoogleHttpError
from .security import REQUEST_HEADER, SESSION_HEADER, require_origin, require_session_header

logger = logging.getLogger("eva.api.calendar")

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


def _clock(request: Request):
    """Injectable clock (tests replace app.state.clock); production uses UTC."""
    return getattr(request.app.state, "clock", None) or (lambda: datetime.now(timezone.utc))


def _service(request: Request) -> CalendarService:
    auth: GoogleAuth | None = getattr(request.app.state, "google_auth", None)
    if auth is None:
        raise HTTPException(status_code=503, detail="google auth is not initialized")
    try:
        session = auth.authorized_session()
    except GoogleAuthError as exc:  # sanitized by construction
        raise HTTPException(
            status_code=503,
            detail=f"calendar unavailable: {exc}; start authorization at /api/auth/google/start",
        ) from None
    except Exception as exc:
        # Generic/untrusted exceptions must never have their message, repr or
        # traceback logged - third-party exception text can carry tokens,
        # headers or private provider data. Class name only; fixed message.
        logger.error(
            "unexpected failure preparing calendar session (%s)", type(exc).__name__
        )
        raise HTTPException(
            status_code=503, detail="calendar is temporarily unavailable"
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


# --------------------------------------------------------------------------- #
# A04 proposals (policy path only - mutations never happen here)
# --------------------------------------------------------------------------- #


@router.post("/proposals", response_model=ProposedActionResponse)
def create_proposal(request: Request, body: CalendarProposalArguments) -> ProposedActionResponse:
    """POST /api/calendar/proposals against the frozen discriminated union.

    Transport metadata (session/request identity) comes from trusted headers
    - never from the frozen body contract. Idempotency is enforced at the DB
    boundary: same (session, request) + same canonical digest replays the
    existing action; different content fails closed with 409."""
    require_origin(request)
    session_id = require_session_header(request)
    request_id = (request.headers.get(REQUEST_HEADER) or "").strip()
    if not request_id:
        raise HTTPException(status_code=400, detail=f"{REQUEST_HEADER} header is required")

    engine = getattr(request.app.state, "approval_engine", None)
    repo = getattr(request.app.state, "action_repository", None)
    executor = getattr(request.app.state, "tool_executor", None)
    if engine is None or repo is None or executor is None:
        raise HTTPException(status_code=503, detail="proposal pipeline not initialized")

    tool = body.tool
    arguments = body.model_dump(mode="json")
    try:
        validated = engine.validate_arguments(tool, arguments)
    except ActionPolicyError as exc:
        raise HTTPException(status_code=422, detail=f"{exc.code}: {exc.detail}") from None
    digest = arguments_digest(canonical_arguments(validated))

    existing = repo.get_idempotent_proposal(session_id, request_id)
    if existing is not None:
        existing_digest, existing_action_id = existing
        if existing_digest != digest:
            raise HTTPException(
                status_code=409,
                detail="this request id already produced a different proposal",
            )
        action = repo.get_action(existing_action_id)
        if action is None:  # slot without an action: cannot happen durably
            raise HTTPException(status_code=500, detail="idempotency record inconsistent")
        return ProposedActionResponse(action=action)

    # Current target state for existing-event mutations (never invented).
    meetings = {}
    if isinstance(validated, (CalendarRescheduleEventArguments, CalendarUpdateAgendaArguments)):
        ref = validated.ref
        try:
            meeting = _service(request).get_event(calendar_id=ref.calendar_id, event_id=ref.event_id)
        except HTTPException:
            raise
        except (GoogleHttpError, CalendarReadError, ValueError):
            raise HTTPException(
                status_code=503, detail="proposal target could not be read; try again"
            ) from None
        meetings[(ref.calendar_id, ref.event_id)] = meeting

    context = ProposalContext(
        session_id=session_id,
        request_id=request_id,
        now=_clock(request)(),
        meetings=meetings,
    )
    call = ToolCall(id=f"proposal-{request_id}", name=tool, arguments=arguments)
    try:
        # Policy evaluation WITHOUT persistence (A03 logic unchanged): nothing
        # is durable yet, so a request that loses the idempotency race can
        # never leave an orphan PENDING action behind.
        action = engine.propose(call, context, persist=False)
    except ActionPolicyError as exc:
        raise HTTPException(status_code=422, detail=f"{exc.code}: {exc.detail}") from None

    # ONE SQLite transaction arbitrates the (session, request) slot and
    # inserts the action together; the PK decides the winner across threads,
    # connections and process restarts - never an in-process lock.
    outcome = repo.create_idempotent_action(
        action, session_id=session_id, request_id=request_id, arguments_digest=digest
    )
    if outcome.status == "conflict":
        raise HTTPException(
            status_code=409, detail="this request id already produced a different proposal"
        )
    if outcome.status == "existing":
        winner = repo.get_action(outcome.action_id)
        if winner is None:  # slot without an action cannot happen durably
            raise HTTPException(status_code=500, detail="idempotency record inconsistent")
        return ProposedActionResponse(action=winner)

    if not action.requires_approval:
        # Durable creation -> executor revalidation -> atomic no-approval
        # claim (inside execute()). No synthetic receipt, ever.
        exec_outcome = executor.execute(action.id)
        action = exec_outcome.action or action
    return ProposedActionResponse(action=action)
