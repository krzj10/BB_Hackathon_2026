"""Shared browser-mutation protections for A04 routes.

Threat model (documented decision): EVA is a single-user local application
with NO cookie authentication, so there is deliberately no CSRF token - a
token not tied to an authentication cookie would be meaningless. Browser
mutations are instead protected by the combination of:

- an exact Origin allowlist (EVA_APP_ALLOWED_ORIGINS; never wildcards, never
  the LLM endpoint allowlist) enforced on every mutating route;
- CORS restricted to those same origins;
- a custom X-EVA-Session-ID header (a cross-site form POST cannot set
  custom headers, and cross-origin fetch requires passing CORS);
- server-side session/action binding: the claimed session must equal
  ProposedAction.session_id.

If cookie-based authentication is ever introduced, explicit CSRF protection
becomes mandatory at this boundary.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

SESSION_HEADER = "X-EVA-Session-ID"
REQUEST_HEADER = "X-EVA-Request-ID"


def require_origin(request: Request) -> None:
    """Mutating routes: when a browser sends Origin it MUST be an exact
    configured app origin. A missing Origin (non-browser client) is allowed,
    but session binding still applies to every action-scoped call."""
    origin = request.headers.get("origin")
    if origin is None:
        return
    settings = request.app.state.settings
    if not settings.is_allowed_app_origin(origin):
        raise HTTPException(status_code=403, detail="origin not allowed for mutations")


def require_session_header(request: Request) -> str:
    session_id = request.headers.get(SESSION_HEADER, "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail=f"{SESSION_HEADER} header is required")
    return session_id


def require_action_session(request: Request, action_session_id: str) -> str:
    """Session/action binding: another session's action must never be
    readable, challengeable or confirmable."""
    session_id = require_session_header(request)
    if session_id != action_session_id:
        raise HTTPException(status_code=403, detail="action belongs to a different session")
    return session_id
