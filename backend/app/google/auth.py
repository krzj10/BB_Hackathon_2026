"""Google OAuth flow, state protection and credential storage (A02).

Design:

- One-time ``state`` tokens issued server-side (secrets.token_urlsafe(32)),
  consumed on callback with expiry; a replayed or unknown state always fails.
- Offline access (refresh token) + consent + include_granted_scopes so the
  granted-scope set can be verified against the P0 requirements.
- Credentials are persisted as a permission-restricted JSON file outside
  source control (default under ``secrets/``, which is gitignored). The file
  contains refresh/access tokens: it must never be committed or logged.
- Exception and status messages are sanitized by construction: they reference
  outcomes, scopes and counts - never token material or raw provider bodies.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import AuthorizedSession, Request

logger = logging.getLogger("eva.google.auth")

# P0 scopes from docs/EVA_IMPLEMENTATION_PLAN.md section 6.
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events.owned"
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
REQUIRED_SCOPES: tuple[str, ...] = (CALENDAR_SCOPE, GMAIL_READONLY_SCOPE)

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


class OAuthOutcome(str, Enum):
    CONNECTED = "connected"
    DENIED = "denied"
    INVALID_STATE = "invalid_state"
    MISSING_SCOPES = "missing_scopes"
    EXCHANGE_FAILED = "exchange_failed"
    NOT_CONFIGURED = "not_configured"


class GoogleAuthError(RuntimeError):
    """Sanitized auth failure. Messages must never contain tokens/secrets."""


@dataclass(frozen=True)
class CallbackResult:
    outcome: OAuthOutcome
    detail: str
    missing_scopes: tuple[str, ...] = field(default_factory=tuple)


class StateStore:
    """One-time OAuth state with expiry. consume() is single-use: replayed or
    unknown values always return False."""

    def __init__(self, ttl_seconds: float = 600.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._issued: dict[str, float] = {}

    def issue(self) -> str:
        now = self._clock()
        self._issued = {s for s in [k for k, exp in self._issued.items() if exp > now]} \
            if False else {k: exp for k, exp in self._issued.items() if exp > now}
        state = secrets.token_urlsafe(32)
        self._issued[state] = now + self._ttl
        return state

    def consume(self, state: str | None) -> bool:
        """Validate and invalidate in one step (replay protection)."""
        if not state:
            return False
        expiry = self._issued.pop(state, None)
        return expiry is not None and expiry > self._clock()

    def pending_count(self) -> int:
        return len(self._issued)


def _default_token_exchanger(client_id: str, client_secret: str, redirect_uri: str):
    """Real code-exchange callable: POST to the Google token endpoint.

    Returns the parsed token response dict. Any transport/HTTP failure raises
    GoogleAuthError with a sanitized message (response bodies may contain
    token material and are never echoed)."""

    def exchange(code: str) -> dict:
        import requests  # local import keeps module import cheap

        try:
            response = requests.post(
                TOKEN_ENDPOINT,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                },
                timeout=15,
            )
        except requests.RequestException as exc:
            raise GoogleAuthError("token endpoint unreachable") from exc
        if response.status_code != 200:
            raise GoogleAuthError(
                f"token exchange rejected (HTTP {response.status_code})"
            )
        try:
            info = response.json()
        except ValueError as exc:
            raise GoogleAuthError("token endpoint returned a malformed response") from exc
        if not isinstance(info, dict) or "access_token" not in info:
            raise GoogleAuthError("token endpoint response missing access token")
        return info

    return exchange


def _default_refresher(credentials: Credentials) -> None:
    """Real refresh path (network). Injectable in tests for hermetic runs."""
    credentials.refresh(Request())


class GoogleAuth:
    """Owns the OAuth flow and the credential file. The only component that
    ever sees raw tokens; everything it hands out is sanitized."""

    def __init__(
        self,
        settings,
        *,
        state_store: StateStore | None = None,
        token_exchanger=None,
        session_factory: Callable[[Credentials], object] | None = None,
        refresher: Callable[[Credentials], None] | None = None,
    ) -> None:
        self._settings = settings
        self.state_store = state_store or StateStore()
        self._credentials_path = Path(settings.google_credentials_path)
        self._session_factory = session_factory
        self._refresher = refresher or _default_refresher
        self._reauth_required = False
        self._last_refresh_error: str | None = None
        self._exchange = (
            token_exchanger
            if token_exchanger is not None
            else _default_token_exchanger(
                settings.google_client_id,
                settings.google_client_secret,
                settings.google_redirect_uri,
            )
        )

    # -- configuration -------------------------------------------------------

    @property
    def configured(self) -> bool:
        return self._settings.google_configured

    # -- flow ------------------------------------------------------------------

    def authorization_url(self) -> str:
        """Exact-redirect offline consent URL with a fresh one-time state."""
        if not self.configured:
            raise GoogleAuthError("Google OAuth client id/secret are not configured")
        params = {
            "client_id": self._settings.google_client_id,
            "redirect_uri": self._settings.google_redirect_uri,
            "response_type": "code",
            "scope": " ".join(REQUIRED_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": self.state_store.issue(),
        }
        return f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}"

    def handle_callback(self, query: dict) -> CallbackResult:
        """Process the callback GET. ``query`` is the raw query mapping."""
        error = query.get("error")
        if error:
            self.state_store.consume(query.get("state"))  # burn any presented state
            if error == "access_denied":
                return CallbackResult(
                    OAuthOutcome.DENIED, "consent was denied; no credentials were stored"
                )
            return CallbackResult(OAuthOutcome.EXCHANGE_FAILED, f"provider error: {error}")

        if not self.configured:
            return CallbackResult(
                OAuthOutcome.NOT_CONFIGURED,
                "Google OAuth client id/secret are not configured",
            )

        if not self.state_store.consume(query.get("state")):
            # Covers missing, unknown, expired AND replayed states.
            return CallbackResult(
                OAuthOutcome.INVALID_STATE,
                "oauth state is missing, expired or already used (possible replay)",
            )

        code = query.get("code")
        if not code:
            return CallbackResult(OAuthOutcome.EXCHANGE_FAILED, "callback carried no authorization code")

        try:
            info = self._exchange(code)
        except GoogleAuthError as exc:
            return CallbackResult(OAuthOutcome.EXCHANGE_FAILED, str(exc))

        granted = tuple(sorted((info.get("scope") or "").split()))
        missing = tuple(s for s in REQUIRED_SCOPES if s not in granted)
        if missing:
            # Do not store partial grants: the flow must be redone honestly.
            return CallbackResult(
                OAuthOutcome.MISSING_SCOPES,
                "required scopes were not granted",
                missing_scopes=missing,
            )

        try:
            self._store_credentials(info, granted)
        except GoogleAuthError as exc:
            return CallbackResult(OAuthOutcome.EXCHANGE_FAILED, str(exc))
        self._reauth_required = False
        self._last_refresh_error = None
        return CallbackResult(OAuthOutcome.CONNECTED, "account connected with required scopes")

    # -- credential storage ------------------------------------------------------

    def _store_credentials(self, info: dict, granted: tuple[str, ...]) -> None:
        self._credentials_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "refresh_token": info.get("refresh_token"),
            "access_token": info.get("access_token"),
            "token_uri": TOKEN_ENDPOINT,
            "client_id": self._settings.google_client_id,
            "scopes": list(granted),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        if payload["refresh_token"] is None:
            # Offline access is mandatory; a grant without a refresh token means
            # the flow must be repeated with consent.
            raise GoogleAuthError(
                "provider returned no refresh token; reconnect with offline access"
            )
        fd = os.open(
            self._credentials_path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        try:
            os.chmod(self._credentials_path, 0o600)
        except OSError:  # best-effort on non-POSIX; path stays gitignored
            pass

    def _load_credentials(self) -> dict | None:
        if not self._credentials_path.exists():
            return None
        try:
            data = json.loads(self._credentials_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("google credential file unreadable; treating as disconnected")
            return None
        return data if isinstance(data, dict) and data.get("refresh_token") else None

    def clear_credentials(self) -> None:
        """Disconnect primitive (no public route in the frozen API)."""
        try:
            self._credentials_path.unlink(missing_ok=True)
        except OSError as exc:
            raise GoogleAuthError("could not remove stored credentials") from exc
        self._reauth_required = False
        self._last_refresh_error = None

    # -- authorized access ---------------------------------------------------------

    def _build_credentials(self, data: dict) -> Credentials:
        return Credentials(
            token=data.get("access_token"),
            refresh_token=data["refresh_token"],
            token_uri=data.get("token_uri", TOKEN_ENDPOINT),
            client_id=data.get("client_id", self._settings.google_client_id),
            client_secret=self._settings.google_client_secret,
            scopes=tuple(data.get("scopes") or REQUIRED_SCOPES),
        )

    def authorized_session(self):
        """An AuthorizedSession that refreshes transparently. On refresh
        failure marks reauthorization-required and raises a sanitized error -
        callers must route the user back through authorization."""
        if not self.configured:
            raise GoogleAuthError("Google OAuth client id/secret are not configured")
        data = self._load_credentials()
        if data is None:
            raise GoogleAuthError("google account not connected; start the OAuth flow")
        credentials = self._build_credentials(data)
        try:
            if not credentials.valid:
                self._refresher(credentials)
        except RefreshError as exc:
            # Token expired/revoked (e.g. Testing-mode 7-day expiry).
            self._reauth_required = True
            self._last_refresh_error = "refresh rejected"
            logger.warning("google refresh failed; reauthorization required")
            raise GoogleAuthError(
                "stored credentials could not be refreshed; reauthorization required"
            ) from exc
        except OSError as exc:
            raise GoogleAuthError("token endpoint unreachable during refresh") from exc
        self._reauth_required = False
        if self._session_factory is not None:
            return self._session_factory(credentials)
        return AuthorizedSession(credentials)

    # -- status -----------------------------------------------------------------------

    def status(self):
        """Sanitized integration status for GET /api/integrations."""
        from ..contracts.api import IntegrationStatus

        data = self._load_credentials()
        if not self.configured:
            return IntegrationStatus(
                name="google",
                connected=False,
                detail="oauth client id/secret not configured",
            )
        if self._reauth_required:
            return IntegrationStatus(
                name="google",
                connected=False,
                detail="credentials expired or revoked; reauthorization required",
            )
        if data is None:
            return IntegrationStatus(
                name="google", connected=False, detail="not connected; start the OAuth flow"
            )
        return IntegrationStatus(
            name="google",
            connected=True,
            detail="offline credentials stored",
            granted_scopes=list(data.get("scopes") or []),
        )
