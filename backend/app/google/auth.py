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
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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
    """One-time OAuth state with expiry, safe for concurrent handlers.

    consume() is atomic and single-use: replayed or unknown values always
    return False. A lock covers the mutations because FastAPI synchronous
    route handlers may execute on different worker threads."""

    def __init__(self, ttl_seconds: float = 600.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._issued: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue(self) -> str:
        with self._lock:
            now = self._clock()
            self._issued = {
                state: expiry for state, expiry in self._issued.items() if expiry > now
            }
            state = secrets.token_urlsafe(32)
            self._issued[state] = now + self._ttl
            return state

    def consume(self, state: str | None) -> bool:
        """Validate and invalidate in one atomic step (replay protection)."""
        if not state:
            return False
        with self._lock:
            expiry = self._issued.pop(state, None)
        return expiry is not None and expiry > self._clock()

    def pending_count(self) -> int:
        with self._lock:
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


def _fsync_parent_directory(path: Path) -> None:
    """Best-effort durability for the RENAME itself.

    After os.replace() has moved the fully-fsynced temp file onto the target,
    syncing the PARENT DIRECTORY makes the new directory entry durable across
    an abrupt crash on POSIX-like filesystems. This is strictly additional to
    the file-level fsync and MUST NEVER fail the caller: platforms without
    O_DIRECTORY (e.g. Windows) or that refuse the open/fsync simply return.
    No path or credential value is ever surfaced here."""
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        dir_fd = os.open(str(path.parent), directory_flags)
    except (OSError, ValueError):  # unsupported platform / refused open
        return
    try:
        os.fsync(dir_fd)
    except OSError:  # directory fsync unsupported here - harmless
        pass
    finally:
        try:
            os.close(dir_fd)
        except OSError:
            pass


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
        # Serializes load->decide->refresh->persist within this process: two
        # concurrent expired-token requests perform AT MOST one refresh (the
        # second caller re-reads the freshly persisted state under the lock).
        self._refresh_lock = threading.Lock()
        # Same-instance credential lifecycle generation. clear_credentials()
        # increments it UNDER _refresh_lock; an OAuth callback snapshots it
        # before the network exchange and rechecks it at the persistence
        # boundary, so a disconnect that lands mid-exchange invalidates that
        # callback instead of letting it resurrect credentials afterwards.
        self._credential_epoch = 0
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
        """Process the callback GET. ``query`` is the raw query mapping.

        The one-time state is validated and consumed FIRST, unconditionally -
        provider errors and consent denials included. A callback carrying an
        unknown, expired or already-consumed state is always INVALID_STATE, so
        forged error/denial callbacks cannot be presented as genuine."""
        if not self.state_store.consume(query.get("state")):
            # Covers missing, unknown, expired AND replayed states, for every
            # callback kind (success, denial, provider error).
            return CallbackResult(
                OAuthOutcome.INVALID_STATE,
                "oauth state is missing, expired or already used (possible replay)",
            )

        error = query.get("error")
        if error == "access_denied":
            return CallbackResult(
                OAuthOutcome.DENIED, "consent was denied; no credentials were stored"
            )
        if error:
            return CallbackResult(OAuthOutcome.EXCHANGE_FAILED, f"provider error: {error}")

        if not self.configured:
            return CallbackResult(
                OAuthOutcome.NOT_CONFIGURED,
                "Google OAuth client id/secret are not configured",
            )

        code = query.get("code")
        if not code:
            return CallbackResult(OAuthOutcome.EXCHANGE_FAILED, "callback carried no authorization code")

        # Snapshot the credential lifecycle generation BEFORE the network
        # exchange. A clear_credentials() that lands while this exchange is in
        # flight increments the epoch under the shared lock, and the commit
        # below refuses to write a grant older than that disconnect.
        epoch_at_start = self._credential_epoch
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

        # Final credential commit is serialized with authorized_session() and
        # clear_credentials() under the SAME lifecycle lock (never held during
        # the exchange). If a newer disconnect bumped the epoch, this callback
        # is stale: store nothing and report a sanitized non-connected result.
        try:
            with self._refresh_lock:
                if self._credential_epoch != epoch_at_start:
                    logger.warning(
                        "oauth callback superseded by a newer credential change; nothing stored"
                    )
                    return CallbackResult(
                        OAuthOutcome.EXCHANGE_FAILED,
                        "authorization is no longer current; start the flow again",
                    )
                self._store_credentials(info, granted)
                self._reauth_required = False
                self._last_refresh_error = None
        except GoogleAuthError as exc:
            return CallbackResult(OAuthOutcome.EXCHANGE_FAILED, str(exc))
        return CallbackResult(OAuthOutcome.CONNECTED, "account connected with required scopes")

    # -- credential storage ------------------------------------------------------

    @staticmethod
    def _expiry_from_token_response(info: dict) -> datetime:
        """Convert Google's ``expires_in`` seconds into an aware absolute UTC
        expiry. Missing/non-positive/non-numeric values fail closed: storing a
        token whose validity cannot be determined would defeat the refresh
        flow (an existing access token is never proof of validity)."""
        raw = info.get("expires_in")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0:
            raise GoogleAuthError(
                "token response missing a valid expires_in; refusing to store "
                "credentials whose expiry is unknown"
            )
        return datetime.now(timezone.utc) + timedelta(seconds=float(raw))

    def _write_credentials(self, payload: dict) -> None:
        """Atomic credential persistence: the COMPLETE JSON goes to a
        temporary file in the same directory (0600 best-effort, flushed and
        fsynced for the local demo durability boundary), then os.replace()
        moves it onto the target atomically. An interrupted write can never
        leave a truncated or half-written credential file. Token values are
        never logged."""
        self._credentials_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path: Path | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                dir=str(self._credentials_path.parent),
                prefix=".credentials-",
                suffix=".tmp",
            )
            tmp_path = Path(tmp_name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:  # best-effort on non-POSIX; path stays gitignored
                pass
            os.replace(tmp_path, self._credentials_path)
            tmp_path = None
        except OSError as exc:
            raise GoogleAuthError("could not persist credentials") from exc
        finally:
            if tmp_path is not None:  # interrupted write: no litter, no truncation
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
        # Post-rename durability (best-effort, never fatal): sync the parent
        # directory so the rename itself survives an abrupt crash on platforms
        # that support it, and re-assert restricted permissions on the final
        # target where supported. Neither may expose paths or token values.
        _fsync_parent_directory(self._credentials_path)
        try:
            os.chmod(self._credentials_path, 0o600)
        except OSError:  # best-effort on non-POSIX; path stays gitignored
            pass

    def _store_credentials(self, info: dict, granted: tuple[str, ...]) -> None:
        if info.get("refresh_token") is None:
            # Offline access is mandatory; a grant without a refresh token means
            # the flow must be repeated with consent.
            raise GoogleAuthError(
                "provider returned no refresh token; reconnect with offline access"
            )
        expiry = self._expiry_from_token_response(info)
        self._write_credentials(
            {
                "refresh_token": info["refresh_token"],
                "access_token": info.get("access_token"),
                # Explicit aware UTC ISO-8601 ("+00:00"); validity of the
                # stored access token is decided by this, never by presence.
                "expiry": expiry.isoformat(),
                "token_uri": TOKEN_ENDPOINT,
                "client_id": self._settings.google_client_id,
                "scopes": list(granted),
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def _persist_refreshed(self, credentials: Credentials, previous_refresh_token: str) -> None:
        """Persist a successful refresh: new access token + expiry, keeping the
        previous refresh token when Google does not rotate it."""
        expiry = credentials.expiry
        if expiry is None:
            raise GoogleAuthError("refreshed credentials arrived without an expiry")
        if expiry.tzinfo is None:  # google-auth uses naive-UTC internally
            expiry = expiry.replace(tzinfo=timezone.utc)
        self._write_credentials(
            {
                "refresh_token": credentials.refresh_token or previous_refresh_token,
                "access_token": credentials.token,
                "expiry": expiry.astimezone(timezone.utc).isoformat(),
                "token_uri": TOKEN_ENDPOINT,
                "client_id": self._settings.google_client_id,
                "scopes": list(credentials.scopes or REQUIRED_SCOPES),
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
        )

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
        """Disconnect primitive (no public route in the frozen API).

        Serialized with authorized_session() under the SAME refresh lock: a
        concurrent in-flight refresh can no longer re-persist credentials AFTER
        this disconnect (same-instance resurrection race). The final state is
        deterministic - whoever takes the lock last decides. clear_credentials
        itself performs no network work and acquires no other lock, so no
        deadlock ordering exists."""
        with self._refresh_lock:
            try:
                self._credentials_path.unlink(missing_ok=True)
            except OSError as exc:
                raise GoogleAuthError("could not remove stored credentials") from exc
            self._reauth_required = False
            self._last_refresh_error = None
            # Invalidate any OAuth callback whose exchange started before this
            # disconnect: its commit rechecks the epoch under this same lock
            # and refuses to resurrect credentials.
            self._credential_epoch += 1

    # -- authorized access ---------------------------------------------------------

    #: Sentinel expiry for stored credentials whose expiry is missing or
    #: corrupt: treat the access token as expired and force a refresh rather
    #: than trusting mere presence of a token string.
    _EXPIRED_SENTINEL = datetime(1970, 1, 1)

    def _build_credentials(self, data: dict) -> Credentials:
        expiry = self._EXPIRED_SENTINEL
        raw_expiry = data.get("expiry")
        if isinstance(raw_expiry, str):
            try:
                parsed = datetime.fromisoformat(raw_expiry)
            except ValueError:
                parsed = None
            if parsed is not None:
                if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
                    parsed = None
                else:
                    # google-auth compares against naive UTC internally.
                    expiry = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return Credentials(
            token=data.get("access_token"),
            refresh_token=data["refresh_token"],
            token_uri=data.get("token_uri", TOKEN_ENDPOINT),
            client_id=data.get("client_id", self._settings.google_client_id),
            client_secret=self._settings.google_client_secret,
            scopes=tuple(data.get("scopes") or REQUIRED_SCOPES),
            expiry=expiry,
        )

    def authorized_session(self):
        """An AuthorizedSession, pre-refreshed when the persisted expiry says
        the access token is no longer valid.

        On a successful refresh the new access token and expiry are written
        back to the credential file (the previous refresh token is preserved
        when Google does not rotate it). On a rejected refresh the store is
        marked reauthorization-required and only a sanitized error is raised.

        Known boundary: a late 401-triggered auto-refresh inside
        AuthorizedSession can still fail mid-request; that surfaces as a
        sanitized transport/read error here. Flipping reauth state from inside
        the SDK's auth flow needs custom transport plumbing deliberately
        deferred to A04/A07 - the common expired-token path is handled above
        through the persisted expiry before any request is made."""
        if not self.configured:
            raise GoogleAuthError("Google OAuth client id/secret are not configured")
        with self._refresh_lock:
            # The persisted state is (re)read INSIDE the refresh lock: when a
            # concurrent caller refreshed while we waited, its new expiry is
            # visible here and no second refresh happens for the same expired
            # token state.
            data = self._load_credentials()
            if data is None:
                raise GoogleAuthError("google account not connected; start the OAuth flow")
            credentials = self._build_credentials(data)
            if not credentials.valid:
                try:
                    self._refresher(credentials)
                except RefreshError as exc:
                    # Token revoked or expired beyond refresh (e.g. Testing-mode
                    # 7-day expiry). Never surface the provider message verbatim.
                    self._reauth_required = True
                    self._last_refresh_error = "refresh rejected"
                    logger.warning("google refresh failed; reauthorization required")
                    raise GoogleAuthError(
                        "stored credentials could not be refreshed; reauthorization required"
                    ) from exc
                except OSError as exc:
                    raise GoogleAuthError("token endpoint unreachable during refresh") from exc
                try:
                    self._persist_refreshed(credentials, previous_refresh_token=data["refresh_token"])
                except GoogleAuthError:
                    # A usable session exists but cannot be durably re-stored; the
                    # next process start will simply refresh again. Not fatal for
                    # this request; recorded for honesty in logs only.
                    logger.warning("google refreshed credentials could not be persisted")
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
