"""Focus routes (B04).

Start/stop are LOCAL explicit commands that still travel the canonical
proposal path (ToolRegistry -> A03 policy -> idempotent durable creation ->
guarded executor), so focus changes are audited exactly like every other
mutation - LOW risk executes immediately, no synthetic receipt. Delivery
policy itself lives in FocusService; current/summary are plain reads over
persisted state and stay available after reload/reconnect."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from ..approvals.engine import (
    ActionPolicyError,
    ProposalContext,
    arguments_digest,
    canonical_arguments,
)
from ..contracts.api import (
    FocusCurrentResponse,
    FocusSessionResponse,
    FocusStartRequest,
    FocusStopRequest,
    FocusStopResponse,
    FocusSummaryResponse,
)
from ..contracts.domain import FocusCompletionSummary, FocusSession, ToolCall
from .security import REQUEST_HEADER, require_origin, require_session_header

logger = logging.getLogger("eva.api.focus")

router = APIRouter(prefix="/api/focus", tags=["focus"])


def _clock(request: Request):
    return getattr(request.app.state, "clock", None) or (
        lambda: datetime.now(timezone.utc)
    )


def _service(request: Request):
    service = getattr(request.app.state, "focus_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="focus service not initialized")
    return service


def _run_local_command(
    request: Request, tool: str, arguments: dict
):
    """Policy-evaluated, idempotently persisted local command executed through
    the single guarded executor (mirrors the calendar proposal flow)."""
    require_origin(request)
    session_id = require_session_header(request)
    request_id = (request.headers.get(REQUEST_HEADER) or "").strip()
    if not request_id:
        raise HTTPException(status_code=400, detail=f"{REQUEST_HEADER} header is required")

    engine = request.app.state.approval_engine
    repo = request.app.state.action_repository
    executor = request.app.state.tool_executor

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
        if action is None:
            raise HTTPException(status_code=500, detail="idempotency record inconsistent")
        result = repo.get_last_result(action.id)
        return action, result

    try:
        proposed = engine.propose(
            ToolCall(id=f"{tool}-{request_id}", name=tool, arguments=arguments),
            ProposalContext(
                session_id=session_id, request_id=request_id, now=_clock(request)()
            ),
            persist=False,
        )
    except ActionPolicyError as exc:
        raise HTTPException(status_code=422, detail=f"{exc.code}: {exc.detail}") from None

    outcome = repo.create_idempotent_action(
        proposed, session_id=session_id, request_id=request_id, arguments_digest=digest
    )
    if outcome.status == "conflict":
        raise HTTPException(
            status_code=409, detail="this request id already produced a different proposal"
        )
    action = repo.get_action(outcome.action_id)
    if action is None:
        raise HTTPException(status_code=500, detail="idempotency record inconsistent")
    if outcome.status == "existing":
        return action, repo.get_last_result(action.id)

    exec_outcome = executor.execute(action.id)  # LOW-risk local command
    return exec_outcome.action or action, exec_outcome.result


@router.post("/start", response_model=FocusSessionResponse)
def start(request: Request, body: FocusStartRequest) -> FocusSessionResponse:
    action, result = _run_local_command(
        request,
        "focus.start",
        {
            "tool": "focus.start",
            "duration_minutes": body.duration_minutes,
            "threshold": body.threshold.value,
            "sender_overrides": body.sender_overrides,
        },
    )
    if result is not None and result.data and "session" in result.data:
        return FocusSessionResponse(
            session=FocusSession.model_validate(result.data["session"])
        )
    if result is not None and result.error is not None:
        # The command ran and was rejected (e.g. already active): honest 409,
        # never a success shape borrowed from the unrelated live session.
        raise HTTPException(
            status_code=409, detail=f"focus could not be started ({result.error.code})"
        )
    # Replay with a lost result: the durable truth is the active session.
    current = _service(request).current(_clock(request)())
    if current is None:
        raise HTTPException(status_code=409, detail="focus could not be started")
    return FocusSessionResponse(session=current)


@router.post("/stop", response_model=FocusStopResponse)
def stop(request: Request, body: FocusStopRequest) -> FocusStopResponse:  # noqa: ARG001
    service = _service(request)
    now = _clock(request)()
    _, result = _run_local_command(request, "focus.stop", {"tool": "focus.stop"})
    if result is not None and result.data and "summary" in result.data:
        return FocusStopResponse(
            session=FocusSession.model_validate(result.data["session"]),
            summary=FocusCompletionSummary.model_validate(result.data["summary"]),
        )
    # Idempotent replay path: an already-stopped session still serves its
    # persisted completion summary via GET; here there is nothing left to stop.
    current = service.current(now)
    if current is None:
        raise HTTPException(
            status_code=409,
            detail="no active focus session; use GET /api/focus/{id}/summary",
        )
    stopped, summary_result = service.stop(now=now)
    return FocusStopResponse(session=stopped, summary=summary_result)


@router.get("/current", response_model=FocusCurrentResponse)
def current(request: Request) -> FocusCurrentResponse:
    return FocusCurrentResponse(session=_service(request).current(_clock(request)()))


@router.get("/{session_id}/summary", response_model=FocusSummaryResponse)
def summary(request: Request, session_id: str) -> FocusSummaryResponse:
    try:
        result = _service(request).summary_for(session_id, now=_clock(request)())
    except KeyError:
        raise HTTPException(status_code=404, detail="focus session not found") from None
    return FocusSummaryResponse(summary=result)
