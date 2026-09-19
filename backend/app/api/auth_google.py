"""Google OAuth routes: start / callback / integrations (A02).

Frozen routes from docs/EVA_IMPLEMENTATION_PLAN.md section 9:
GET /api/auth/google/start, GET /api/auth/google/callback, GET /api/integrations.

No token, code or credential material is ever serialized in a response body;
failure responses carry typed outcomes and sanitized details only."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..contracts.api import IntegrationsResponse
from ..google.auth import GoogleAuth, GoogleAuthError, OAuthOutcome

logger = logging.getLogger("eva.api.auth_google")

router = APIRouter(prefix="/api", tags=["auth-google"])


def _auth(request: Request) -> GoogleAuth:
    auth: GoogleAuth | None = getattr(request.app.state, "google_auth", None)
    if auth is None:  # defensive; create_app always wires one
        raise HTTPException(status_code=503, detail="google auth is not initialized")
    return auth


@router.get("/auth/google/start")
def start(request: Request) -> dict[str, str]:
    """Issue a fresh one-time state and return the exact offline consent URL."""
    try:
        return {"authorize_url": _auth(request).authorization_url()}
    except GoogleAuthError as exc:
        # GoogleAuthError messages are sanitized by construction.
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except Exception:
        # Unexpected failures must never leak str(exc) to the client; nothing
        # secret is logged either (no args rendered).
        logger.exception("unexpected failure issuing oauth start url")
        raise HTTPException(
            status_code=503, detail="authorization start is temporarily unavailable"
        ) from None


@router.get("/auth/google/callback")
def callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> JSONResponse:
    try:
        result = _auth(request).handle_callback(
            {"code": code, "state": state, "error": error}
        )
    except GoogleAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception:
        logger.exception("unexpected failure handling oauth callback")
        raise HTTPException(
            status_code=500, detail="authorization callback failed unexpectedly"
        ) from None
    body = {
        "outcome": result.outcome.value,
        "detail": result.detail,
        "missing_scopes": list(result.missing_scopes),
    }
    if result.outcome is OAuthOutcome.CONNECTED:
        logger.info("google oauth connected")  # no scopes content beyond count
        return JSONResponse(status_code=200, content=body)
    if result.outcome is OAuthOutcome.DENIED:
        # A truthful user decision, not a server fault.
        return JSONResponse(status_code=200, content=body)
    raise HTTPException(status_code=400, detail=body)


@router.get("/integrations", response_model=IntegrationsResponse)
def integrations(request: Request) -> IntegrationsResponse:
    auth = _auth(request)
    return IntegrationsResponse(integrations=[auth.status()])
