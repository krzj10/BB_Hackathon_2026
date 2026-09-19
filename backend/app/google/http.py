"""Minimal injected HTTP boundary for Google REST reads (A02).

Keeping the transport this small means google SDK objects never leave the
auth module, adapters stay unit-testable against synthetic payloads, and no
test ever needs live credentials. Implementations must sanitize errors:
status codes and paths only, never response bodies or headers that could
carry token material.
"""

from __future__ import annotations

from typing import Any, Protocol


class GoogleHttpError(RuntimeError):
    """Sanitized transport failure (HTTP status + endpoint kind only)."""


class GoogleHttp(Protocol):
    def get_json(self, url: str, params: dict[str, Any] | None) -> dict[str, Any]:
        """GET and return the parsed JSON object. Raises GoogleHttpError on
        any non-success transport/HTTP outcome."""


class AuthorizedGoogleHttp:
    """Real GoogleHttp over an authorized (auto-refreshing) session.

    Errors are sanitized by construction: only the HTTP status and a generic
    endpoint kind are surfaced - response bodies and headers (which could
    carry token material or private content) never enter messages or logs."""

    def __init__(self, session: Any, *, timeout_seconds: float = 20.0) -> None:
        self._session = session
        self._timeout = timeout_seconds

    def get_json(self, url: str, params: dict[str, Any] | None) -> dict[str, Any]:
        try:
            response = self._session.get(url, params=params, timeout=self._timeout)
        except Exception:
            # Transport exception types vary (google.auth, requests); their
            # reprs may embed request headers, so nothing is re-raised verbatim.
            raise GoogleHttpError("google api request failed (transport)") from None
        if response.status_code != 200:
            raise GoogleHttpError(
                f"google api returned HTTP {response.status_code} for {_kind(url)}"
            )
        try:
            data = response.json()
        except ValueError:
            raise GoogleHttpError(
                f"google api returned malformed JSON for {_kind(url)}"
            ) from None
        if not isinstance(data, dict):
            raise GoogleHttpError(
                f"google api returned unexpected payload shape for {_kind(url)}"
            )
        return data


def _kind(url: str) -> str:
    """Endpoint kind without query/ids beyond the resource family."""
    tail = url.split("://", 1)[-1].split("/", 2)
    return tail[-1] if len(tail) > 2 else "endpoint"
