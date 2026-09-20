"""Decision Inbox routes (B04).

Reads serve persisted decisions. ACCEPT/REJECT always travel as guarded
local_write proposals (policy decides risk; HIGH = UI confirmation only) and
record an INTERNAL outcome - no payment, purchase, supplier, contract or
message can ever be executed through this surface (registry forbids it by
construction). DEFER is internal inbox bookkeeping."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ..contracts.api import (
    DecisionListResponse,
    DecisionOutcomeProposalRequest,
    DecisionResponse,
    DeferDecisionRequest,
)
from ..decisions.service import DecisionConflictError
from .security import require_origin, require_session_header

logger = logging.getLogger("eva.api.decisions")

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


def _service(request: Request):
    service = getattr(request.app.state, "decision_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="decision service not initialized")
    return service


@router.get("", response_model=DecisionListResponse)
def list_decisions(request: Request) -> DecisionListResponse:
    return DecisionListResponse(items=_service(request).list_all())


@router.get("/{decision_id}", response_model=DecisionResponse)
def get_decision(request: Request, decision_id: str) -> DecisionResponse:
    decision = _service(request).get(decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="decision not found")
    return DecisionResponse(decision=decision)


@router.post("/{decision_id}/outcome-proposals", response_model=DecisionResponse)
def propose_outcome(
    request: Request, decision_id: str, body: DecisionOutcomeProposalRequest
) -> DecisionResponse:
    """User-initiated accept/reject proposal. The frozen body carries the
    session/request identity; origin protection applies AND the claimed body
    session must equal the trusted caller header (same binding as /defer and
    /assistant/message), so an idempotency slot can never be opened under a
    session the caller is not presenting."""
    require_origin(request)
    header_session = require_session_header(request)
    if header_session != body.session_id:
        raise HTTPException(status_code=403, detail="session id does not match the caller")
    service = _service(request)
    try:
        action = service.create_outcome_proposal(
            decision_id,
            body.outcome,
            session_id=body.session_id,
            request_id=body.request_id,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="decision not found") from None
    except DecisionConflictError as exc:
        raise HTTPException(status_code=409, detail=f"{exc.code}: {exc.detail}") from None
    decision = service.get(decision_id)
    if decision is None:  # vanished between proposal and read - never fake it
        raise HTTPException(status_code=500, detail="decision state inconsistent")
    # The proposal (not the decision alone) is what the UI confirms for HIGH.
    return DecisionResponse(decision=decision.model_copy(update={
        "proposed_action_id": action.id,
    }))


@router.post("/{decision_id}/defer", response_model=DecisionResponse)
def defer(request: Request, decision_id: str, body: DeferDecisionRequest) -> DecisionResponse:
    require_origin(request)
    require_session_header(request)  # binding only; single-user local app
    try:
        decision = _service(request).defer(decision_id, reason=body.reason)
    except KeyError:
        raise HTTPException(status_code=404, detail="decision not found") from None
    except DecisionConflictError as exc:
        raise HTTPException(status_code=409, detail=f"{exc.code}: {exc.detail}") from None
    return DecisionResponse(decision=decision)
