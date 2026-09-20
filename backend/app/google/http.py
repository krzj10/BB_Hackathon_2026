"""Injected HTTP boundary for Google REST access (A02 reads, A04 mutations).

Keeping the transport this small means google SDK objects never leave the
auth module, adapters stay unit-testable against synthetic payloads, and no
test ever needs live credentials. Implementations must sanitize errors:
status codes and endpoint kinds only - never response bodies, request
headers or anything that could carry token material.

A04 extension: mutation verbs (POST/PATCH) plus a structured, sanitized
error representation (:class:`GoogleApiError`) so the guarded executor can
distinguish transport ambiguity from definitive HTTP outcomes (412 stale,
409 conflict, 403 permission, ...) WITHOUT ever inspecting provider bodies.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol


class GoogleErrorCategory(str, Enum):
    """Sanitized failure classes safe to branch on and to log."""

    TRANSPORT = "transport"  # connection/timeout: outcome AFTER a mutation is ambiguous
    BAD_REQUEST = "bad_request"  # HTTP 400
    UNAUTHORIZED = "unauthorized"  # HTTP 401
    FORBIDDEN = "forbidden"  # HTTP 403 (insufficient permission/scope)
    NOT_FOUND = "not_found"  # HTTP 404
    CONFLICT = "conflict"  # HTTP 409 (e.g. create collision on a client event id)
    PRECONDITION_FAILED = "precondition_failed"  # HTTP 412 (ETag changed)
    RATE_LIMITED = "rate_limited"  # HTTP 429
    SERVER_ERROR = "server_error"  # HTTP 5xx
    MALFORMED_RESPONSE = "malformed_response"  # non-JSON / unexpected shape


def categorize_status(status_code: int) -> GoogleErrorCategory:
    if status_code == 400:
        return GoogleErrorCategory.BAD_REQUEST
    if status_code == 401:
        return GoogleErrorCategory.UNAUTHORIZED
    if status_code == 403:
        return GoogleErrorCategory.FORBIDDEN
    if status_code == 404:
        return GoogleErrorCategory.NOT_FOUND
    if status_code == 409:
        return GoogleErrorCategory.CONFLICT
    if status_code == 412:
        return GoogleErrorCategory.PRECONDITION_FAILED
    if status_code == 429:
        return GoogleErrorCategory.RATE_LIMITED
    if status_code >= 500:
        return GoogleErrorCategory.SERVER_ERROR
    return GoogleErrorCategory.MALFORMED_RESPONSE


class GoogleHttpError(RuntimeError):
    """Sanitized transport failure (HTTP status + endpoint kind only).

    ``category`` is None for legacy construction sites; the authorized
    transport always populates it. Messages never contain response bodies,
    headers or token material."""

    def __init__(self, message: str, *, category: GoogleErrorCategory | None = None) -> None:
        super().__init__(message)
        self.category = category


class GoogleApiError(GoogleHttpError):
    """Structured sanitized Google API failure for guarded mutation logic.
    Carries only the category and HTTP status - never provider content."""

    def __init__(
        self,
        category: GoogleErrorCategory,
        *,
        status_code: int | None = None,
        endpoint_kind: str = "endpoint",
    ) -> None:
        detail = (
            f"google api request failed ({category.value})"
            if status_code is None
            else f"google api returned HTTP {status_code} for {endpoint_kind}"
        )
        super().__init__(detail, category=category)
        self.status_code = status_code
        self.endpoint_kind = endpoint_kind


class GoogleHttp(Protocol):
    """Minimal transport surface. Reads use get_json; guarded A04 mutations
    use post_json/patch_json (headers exist solely for If-Match style
    preconditions and content typing - never for caller-supplied auth)."""

    def get_json(self, url: str, params: dict[str, Any] | None) -> dict[str, Any]:
        """GET and return the parsed JSON object. Raises GoogleHttpError on
        any non-success transport/HTTP outcome."""

    def post_json(
        self,
        url: str,
        params: dict[str, Any] | None,
        body: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...

    def patch_json(
        self,
        url: str,
        params: dict[str, Any] | None,
        body: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...


class AuthorizedGoogleHttp:
    """Real GoogleHttp over an authorized (auto-refreshing) session.

    Errors are sanitized by construction: only the HTTP status and a generic
    endpoint kind are surfaced - response bodies and headers (which could
    carry token material or private content) never enter messages or logs."""

    def __init__(self, session: Any, *, timeout_seconds: float = 20.0) -> None:
        self._session = session
        self._timeout = timeout_seconds

    def get_json(self, url: str, params: dict[str, Any] | None) -> dict[str, Any]:
        return self._request("GET", url, params=params)

    def post_json(
        self,
        url: str,
        params: dict[str, Any] | None,
        body: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._request("POST", url, params=params, body=body, headers=headers)

    def patch_json(
        self,
        url: str,
        params: dict[str, Any] | None,
        body: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._request("PATCH", url, params=params, body=body, headers=headers)

    # ------------------------------------------------------------------ #

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        kind = _kind(url)
        try:
            response = self._session.request(
                method, url, params=params, json=body, headers=headers, timeout=self._timeout
            )
        except Exception:
            # Transport exception types vary (google.auth, requests); their
            # reprs may embed request headers, so nothing is re-raised verbatim.
            raise GoogleApiError(GoogleErrorCategory.TRANSPORT, endpoint_kind=kind) from None
        if response.status_code not in (200, 201, 204):
            raise GoogleApiError(
                categorize_status(response.status_code),
                status_code=response.status_code,
                endpoint_kind=kind,
            )
        if response.status_code == 204:
            return {}
        try:
            data = response.json()
        except ValueError:
            raise GoogleApiError(
                GoogleErrorCategory.MALFORMED_RESPONSE,
                status_code=response.status_code,
                endpoint_kind=kind,
            ) from None
        if not isinstance(data, dict):
            raise GoogleApiError(
                GoogleErrorCategory.MALFORMED_RESPONSE,
                status_code=response.status_code,
                endpoint_kind=kind,
            )
        return data


def _kind(url: str) -> str:
    """Endpoint kind without query/ids beyond the resource family."""
    tail = url.split("://", 1)[-1].split("/", 2)
    return tail[-1] if len(tail) > 2 else "endpoint"
