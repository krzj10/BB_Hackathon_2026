"""Demo-mode control routes (``EVA_DATA_PROVIDER=demo`` only).

These are deliberately thin: reset and injection delegate to
:class:`app.demo.service.DemoDataService`, which drives the application's one
real ingestion pipeline. The router is mounted ONLY in demo mode, so a normal
deployment never exposes it (404 - not even a 405 that confirms existence).

Response models are server-internal operational shapes (like the internal
argument models in ``agent/tool_registry.py``), not canonical frontend
contracts; adding them changes no exported schema.
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..demo.service import DemoResetError
from .security import require_origin, require_session_header

router = APIRouter(prefix="/api/demo", tags=["demo"])


class DemoInjectRequest(BaseModel):
    model_config = {"extra": "forbid"}

    kind: Literal["normal", "urgent"]


class DemoResetResponse(BaseModel):
    provider: str
    fixtures_loaded: int
    checked: int
    attention_items: int
    decisions: int


class DemoInjectResponse(BaseModel):
    provider: str
    kind: str
    source_id: str
    attention_item_id: Optional[str] = None
    priority: Optional[str] = None
    attention_type: Optional[str] = None
    urgent: bool = False


class DemoStatusResponse(BaseModel):
    provider: str
    messages_available: int = Field(ge=0)
    attention_items: int = Field(ge=0)
    decisions: int = Field(ge=0)


def _service(request: Request):
    service = getattr(request.app.state, "demo_service", None)
    if service is None:  # defensive: router is only mounted in demo mode
        raise HTTPException(status_code=404, detail="demo mode is not enabled")
    return service


@router.get("/status", response_model=DemoStatusResponse)
def demo_status(request: Request) -> DemoStatusResponse:
    return DemoStatusResponse(**_service(request).status())


@router.post("/reset", response_model=DemoResetResponse)
def demo_reset(request: Request) -> DemoResetResponse:
    require_origin(request)
    require_session_header(request)
    try:
        result = _service(request).reset()
    except DemoResetError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return DemoResetResponse(provider="demo", **result)


@router.post("/inject", response_model=DemoInjectResponse)
def demo_inject(request: Request, body: DemoInjectRequest) -> DemoInjectResponse:
    require_origin(request)
    require_session_header(request)
    try:
        result = _service(request).inject(body.kind)
    except DemoResetError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return DemoInjectResponse(provider="demo", **result)
