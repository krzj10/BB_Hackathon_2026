"""Attention routes (B04): list, stored explanations and check-now.

check-now invokes the SAME GmailIngestionService as the scheduler - there is
no second pipeline and no fixture path. Live events for new items are
published through the shared publish helper so both entry points behave
identically."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from ..attention.explanations import build_explanation
from ..attention.publish import publish_new_items
from ..contracts.api import (
    AttentionExplanationResponse,
    AttentionListResponse,
    CheckNowResponse,
)
from .security import REQUEST_HEADER, SESSION_HEADER, require_origin, require_session_header

logger = logging.getLogger("eva.api.attention")

router = APIRouter(prefix="/api/attention", tags=["attention"])


def _clock(request: Request):
    return getattr(request.app.state, "clock", None) or (
        lambda: datetime.now(timezone.utc)
    )


@router.get("", response_model=AttentionListResponse)
def list_attention(
    request: Request, limit: int = Query(default=100, ge=1, le=500)
) -> AttentionListResponse:
    repo = request.app.state.attention_repository
    return AttentionListResponse(items=repo.list_latest(limit=limit))


@router.get("/{item_id}/explanation", response_model=AttentionExplanationResponse)
def explain(request: Request, item_id: str) -> AttentionExplanationResponse:
    """Reads stored rule codes/evidence/policy version only - the response is
    assembled from persisted data, never re-derived or LLM-invented."""
    item = request.app.state.attention_repository.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="attention item not found")
    policy = request.app.state.loaded_policy
    return build_explanation(item, policy_version=policy.version)


@router.post("/check-now", response_model=CheckNowResponse)
def check_now(request: Request) -> CheckNowResponse:
    """Triggers the real ingestion path (identical to the scheduled poll)."""
    require_origin(request)
    session_id = require_session_header(request)  # noqa: F841 - binding only
    request_id = (request.headers.get(REQUEST_HEADER) or "").strip()

    ingestion = getattr(request.app.state, "ingestion_service", None)
    if ingestion is None or getattr(request.app.state, "attention_sink", None) is None:
        raise HTTPException(
            status_code=503, detail="attention pipeline is not initialized"
        )
    result = ingestion.check_now()
    if result.retrieval_status == "in_progress":
        raise HTTPException(status_code=409, detail="another ingestion run is active")
    if result.retrieval_status == "failed":
        # Honest failure: never a fake success with zero counts.
        raise HTTPException(status_code=502, detail="gmail retrieval failed")

    publish_new_items(
        getattr(request.app.state, "event_outbox_repository", None),
        request.app.state.attention_repository,
        getattr(request.app.state, "decision_repository", None),
        result,
        now=_clock(request)(),
    )
    return CheckNowResponse(
        checked_count=result.checked_count,
        duplicate_count=result.duplicate_count,
        new_items=list(result.new_items),
    )
