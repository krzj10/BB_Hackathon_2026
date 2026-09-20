"""Action API routes (A04).

- POST /api/actions/{action_id}/challenge -> ApprovalChallengeResponse
  (frozen A03 contract; direct no-store response, the ONLY place a raw
  one-time challenge is ever returned; never logged, never persisted).
- POST /api/actions/{action_id}/confirm   -> ActionConfirmResponse
  The channel is derived SERVER-SIDE as UI - callers can never choose ui vs
  voice through this browser endpoint (voice confirmation calls the approval
  service internally with a trusted server-side context). APPROVE runs the
  guarded ToolExecutor after A03 confirmation succeeds; REJECT never
  executes. There is no public execute endpoint.
- GET  /api/actions/{action_id}           -> ActionResponse
  Snapshot only: never triggers execution or reconciliation.

All routes enforce Origin allowlisting and session/action binding
(see api/security.py)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..agent.tool_executor import ExecutorError, ToolExecutor
from ..approvals.engine import ActionApprovalEngine, ActionPolicyError
from ..contracts.api import ActionConfirmResponse, ActionResponse, ApprovalChallengeResponse
from ..contracts.domain import ApprovalChannel, ApprovalChoice, ApprovalRequest
from ..db.repositories import ActionRepository
from .security import require_action_session, require_origin

logger = logging.getLogger("eva.api.actions")

router = APIRouter(prefix="/api/actions", tags=["actions"])


def _clock(request: Request):
    """Injectable clock (tests replace app.state.clock); production uses UTC."""
    return getattr(request.app.state, "clock", None) or (lambda: datetime.now(timezone.utc))

_POLICY_ERROR_STATUS = {
    "unknown_action": 404,
    "expired": 410,
    "invalid_status": 409,
    "policy_changed": 409,
    "channel_not_allowed": 403,
    "invalid_challenge": 403,
    "invalid_arguments": 422,
    # Storage invariant violations are server-side faults, never caller hints.
    "receipt_missing": 500,
    "receipt_binding_mismatch": 500,
}


def _repo(request: Request) -> ActionRepository:
    repo = getattr(request.app.state, "action_repository", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="action store is not initialized")
    return repo


def _engine(request: Request) -> ActionApprovalEngine:
    engine = getattr(request.app.state, "approval_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="approval engine is not initialized")
    return engine


def _executor(request: Request) -> ToolExecutor:
    executor = getattr(request.app.state, "tool_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="tool executor is not initialized")
    return executor


def _policy_error(exc: ActionPolicyError) -> HTTPException:
    status = _POLICY_ERROR_STATUS.get(exc.code, 409)
    # Detail carries only the stable code + sanitized text; nothing else.
    return HTTPException(status_code=status, detail=f"{exc.code}: {exc.detail}")


@router.post("/{action_id}/challenge")
def issue_challenge(request: Request, action_id: str):
    """Frozen contract: no request body. Raw challenge travels exactly once
    in this direct authenticated response and nowhere else, ever."""
    require_origin(request)
    action = _repo(request).get_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="unknown action")
    require_action_session(request, action.session_id)
    try:
        issued = _engine(request).issue_challenge(action_id, now=_clock(request)())
    except ActionPolicyError as exc:
        raise _policy_error(exc) from None
    payload = ApprovalChallengeResponse(
        action_id=issued.action_id,
        revision=issued.revision,
        arguments_digest=issued.arguments_digest,
        challenge=issued.challenge,
        expires_at=issued.expires_at,
    )
    # The raw challenge must never be cached or logged - the response body is
    # deliberately not touched by any logger on this path.
    return JSONResponse(
        payload.model_dump(mode="json"),
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/{action_id}/confirm", response_model=ActionConfirmResponse)
def confirm(request: Request, action_id: str, approval: ApprovalRequest) -> ActionConfirmResponse:
    require_origin(request)
    if approval.action_id != action_id:
        raise HTTPException(status_code=400, detail="path/body action id mismatch")
    action = _repo(request).get_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="unknown action")
    require_action_session(request, action.session_id)

    # Channel derivation: this UI endpoint is ALWAYS ApprovalChannel.UI.
    # There is no request parameter/header/body field that selects a channel;
    # voice confirmation uses the approval service internally server-side.
    try:
        result = _engine(request).confirm(
            approval, channel=ApprovalChannel.UI, now=_clock(request)()
        )
    except ActionPolicyError as exc:
        raise _policy_error(exc) from None

    if approval.choice is ApprovalChoice.REJECT:
        # Rejection never executes anything.
        return ActionConfirmResponse(action=result.action, receipt=result.receipt, result=None)

    outcome = _executor(request).execute(result.action.id)
    current = outcome.action or result.action
    return ActionConfirmResponse(action=current, receipt=result.receipt, result=outcome.result)


@router.get("/{action_id}", response_model=ActionResponse)
def get_action(request: Request, action_id: str) -> ActionResponse:
    """Durable snapshot (including the persisted last ToolResult). This
    endpoint never triggers execution or reconciliation."""
    action = _repo(request).get_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="unknown action")
    require_action_session(request, action.session_id)
    return ActionResponse(action=action, last_result=_repo(request).get_last_result(action_id))


# ExecutorError mapping helper for reuse by the proposals route.
def map_executor_error(exc: ExecutorError) -> HTTPException:
    status = 404 if exc.code == "unknown_action" else 500
    return HTTPException(status_code=status, detail=f"{exc.code}: {exc.detail}")
