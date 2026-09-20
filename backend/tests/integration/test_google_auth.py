"""A02 Google OAuth integration tests - fully hermetic (no live Google).

The token exchange and refresh paths are injected fakes; credential files
live in pytest tmp dirs. Synthetic values only; redaction is asserted with
distinctive marker strings."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from google.auth.exceptions import RefreshError

from app.config import Settings
from app.google.auth import (
    GMAIL_READONLY_SCOPE,
    CALENDAR_SCOPE,
    REQUIRED_SCOPES,
    GoogleAuth,
    GoogleAuthError,
    OAuthOutcome,
    StateStore,
)

ACCESS_TOKEN = "SECRET-ACCESS-TOKEN-9138"
REFRESH_TOKEN = "SECRET-REFRESH-TOKEN-4242"
CLIENT_SECRET = "secret-client-secret-777"


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        google_client_id="eva-test-client.apps.googleusercontent.com",
        google_client_secret=CLIENT_SECRET,
        google_redirect_uri="http://localhost:8000/api/auth/google/callback",
        google_credentials_path=str(tmp_path / "google_credentials.json"),
    )


def make_auth(tmp_path: Path, *, exchanger=None, refresher=None) -> GoogleAuth:
    if exchanger is None:
        exchanger = lambda code: {  # noqa: E731 - deterministic test double
            "access_token": ACCESS_TOKEN,
            "refresh_token": REFRESH_TOKEN,
            "scope": " ".join(REQUIRED_SCOPES),
            "expires_in": 3600,
        }
    return GoogleAuth(
        make_settings(tmp_path), token_exchanger=exchanger, refresher=refresher
    )


# ---------------------------------------------------------------------------
# State store: one-time, expiry, replay, thread-safety
# ---------------------------------------------------------------------------


def test_state_is_single_use() -> None:
    store = StateStore()
    state = store.issue()
    assert store.consume(state) is True
    assert store.consume(state) is False  # replay fails
    assert store.consume("never-issued") is False
    assert store.consume(None) is False
    assert store.consume("") is False


def test_state_expires() -> None:
    now = [1000.0]
    store = StateStore(ttl_seconds=60, clock=lambda: now[0])
    state = store.issue()
    now[0] += 61
    assert store.consume(state) is False


def test_concurrent_consume_yields_exactly_one_winner() -> None:
    import threading

    store = StateStore()
    state = store.issue()
    barrier = threading.Barrier(2)
    results: list[bool] = []
    lock = threading.Lock()

    def attempt() -> None:
        barrier.wait()  # deterministic simultaneous start, no sleeps
        won = store.consume(state)
        with lock:
            results.append(won)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(results) == [False, True]


# ---------------------------------------------------------------------------
# Authorization URL
# ---------------------------------------------------------------------------


def test_authorization_url_exact_params(tmp_path) -> None:
    auth = make_auth(tmp_path)
    url = auth.authorization_url()
    query = parse_qs(urlparse(url).query)
    assert urlparse(url).scheme == "https"
    assert query["redirect_uri"] == ["http://localhost:8000/api/auth/google/callback"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["include_granted_scopes"] == ["true"]
    assert query["response_type"] == ["code"]
    assert set(query["scope"][0].split()) == set(REQUIRED_SCOPES)
    assert query["state"][0]
    # client secret never appears in the URL; state is fresh per call.
    assert CLIENT_SECRET not in url
    assert query["state"][0] != parse_qs(urlparse(auth.authorization_url()).query)["state"][0]


# ---------------------------------------------------------------------------
# Callback outcomes
# ---------------------------------------------------------------------------


def test_callback_success_stores_offline_credentials(tmp_path) -> None:
    auth = make_auth(tmp_path)
    state = auth.state_store.issue()
    result = auth.handle_callback({"code": "code-1", "state": state})
    assert result.outcome is OAuthOutcome.CONNECTED
    path = Path(auth._settings.google_credentials_path)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["refresh_token"] == REFRESH_TOKEN  # file itself is gitignored

    # expires_in became an aware absolute UTC expiry (~1h ahead), persisted.
    expiry = datetime.fromisoformat(stored["expiry"])
    assert expiry.tzinfo is not None and expiry.utcoffset() == timedelta(0)
    lead = expiry - datetime.now(timezone.utc)
    assert timedelta(minutes=55) < lead <= timedelta(minutes=61)

    status = auth.status()
    assert status.connected is True
    assert set(status.granted_scopes) == set(REQUIRED_SCOPES)


def test_callback_without_expires_in_stores_nothing(tmp_path) -> None:
    def exchanger(code):
        return {
            "access_token": ACCESS_TOKEN,
            "refresh_token": REFRESH_TOKEN,
            "scope": " ".join(REQUIRED_SCOPES),  # no expires_in
        }

    auth = make_auth(tmp_path, exchanger=exchanger)
    state = auth.state_store.issue()
    result = auth.handle_callback({"code": "code-1", "state": state})
    assert result.outcome is OAuthOutcome.EXCHANGE_FAILED  # fail closed
    assert not Path(auth._settings.google_credentials_path).exists()


def test_callback_malformed_expires_in_stores_nothing(tmp_path) -> None:
    def exchanger(code):
        return {
            "access_token": ACCESS_TOKEN,
            "refresh_token": REFRESH_TOKEN,
            "scope": " ".join(REQUIRED_SCOPES),
            "expires_in": "soon",  # malformed
        }

    auth = make_auth(tmp_path, exchanger=exchanger)
    state = auth.state_store.issue()
    result = auth.handle_callback({"code": "code-1", "state": state})
    assert result.outcome is OAuthOutcome.EXCHANGE_FAILED
    assert not Path(auth._settings.google_credentials_path).exists()


def test_callback_replay_fails(tmp_path) -> None:
    auth = make_auth(tmp_path)
    state = auth.state_store.issue()
    assert auth.handle_callback({"code": "code-1", "state": state}).outcome is OAuthOutcome.CONNECTED
    replay = auth.handle_callback({"code": "code-2", "state": state})
    assert replay.outcome is OAuthOutcome.INVALID_STATE


def test_callback_unknown_state_fails(tmp_path) -> None:
    auth = make_auth(tmp_path)
    result = auth.handle_callback({"code": "code-1", "state": "attacker-state"})
    assert result.outcome is OAuthOutcome.INVALID_STATE


def test_callback_consent_denied_is_truthful_and_stores_nothing(tmp_path) -> None:
    auth = make_auth(tmp_path)
    state = auth.state_store.issue()
    result = auth.handle_callback({"state": state, "error": "access_denied"})
    assert result.outcome is OAuthOutcome.DENIED
    assert not Path(auth._settings.google_credentials_path).exists()


# State binding applies to EVERY callback kind - denials and provider errors
# included. Forged error callbacks must never be presented as genuine.


def test_denial_with_unknown_state_is_invalid_state(tmp_path) -> None:
    auth = make_auth(tmp_path)
    result = auth.handle_callback({"state": "attacker-state", "error": "access_denied"})
    assert result.outcome is OAuthOutcome.INVALID_STATE


def test_denial_without_state_is_invalid_state(tmp_path) -> None:
    auth = make_auth(tmp_path)
    result = auth.handle_callback({"error": "access_denied"})
    assert result.outcome is OAuthOutcome.INVALID_STATE


def test_denial_state_replay_is_invalid_state(tmp_path) -> None:
    auth = make_auth(tmp_path)
    state = auth.state_store.issue()
    first = auth.handle_callback({"state": state, "error": "access_denied"})
    assert first.outcome is OAuthOutcome.DENIED  # valid denial consumes state
    replay = auth.handle_callback({"state": state, "error": "access_denied"})
    assert replay.outcome is OAuthOutcome.INVALID_STATE


def test_other_provider_error_requires_valid_state(tmp_path) -> None:
    auth = make_auth(tmp_path)
    forged = auth.handle_callback({"state": "never-issued", "error": "server_error"})
    assert forged.outcome is OAuthOutcome.INVALID_STATE  # not provider-error
    state = auth.state_store.issue()
    genuine = auth.handle_callback({"state": state, "error": "server_error"})
    assert genuine.outcome is OAuthOutcome.EXCHANGE_FAILED


def test_callback_missing_scope_is_reported_and_stores_nothing(tmp_path) -> None:
    def exchanger(code):
        return {
            "access_token": ACCESS_TOKEN,
            "refresh_token": REFRESH_TOKEN,
            "scope": CALENDAR_SCOPE,  # gmail.readonly not granted
        }

    auth = make_auth(tmp_path, exchanger=exchanger)
    state = auth.state_store.issue()
    result = auth.handle_callback({"code": "code-1", "state": state})
    assert result.outcome is OAuthOutcome.MISSING_SCOPES
    assert result.missing_scopes == (GMAIL_READONLY_SCOPE,)
    assert not Path(auth._settings.google_credentials_path).exists()


def test_callback_exchange_failure_is_sanitized(tmp_path) -> None:
    def exchanger(code):
        raise GoogleAuthError("token exchange rejected (HTTP 400)")

    auth = make_auth(tmp_path, exchanger=exchanger)
    state = auth.state_store.issue()
    result = auth.handle_callback({"code": "bad-code", "state": state})
    assert result.outcome is OAuthOutcome.EXCHANGE_FAILED
    assert ACCESS_TOKEN not in result.detail


def test_callback_without_refresh_token_is_rejected(tmp_path) -> None:
    def exchanger(code):
        return {
            "access_token": ACCESS_TOKEN,
            "scope": " ".join(REQUIRED_SCOPES),
            "expires_in": 3600,
        }

    auth = make_auth(tmp_path, exchanger=exchanger)
    state = auth.state_store.issue()
    result = auth.handle_callback({"code": "code-1", "state": state})
    assert result.outcome is OAuthOutcome.EXCHANGE_FAILED  # offline access mandatory
    assert not Path(auth._settings.google_credentials_path).exists()


# ---------------------------------------------------------------------------
# Refresh: expiry-driven, persistence write-back, reauthorization-required
# ---------------------------------------------------------------------------


def _force_expired_credentials(auth: GoogleAuth) -> None:
    """Rewrite the stored credential file so the (still non-empty) access
    token is expired. Expiry - not token presence - decides validity."""
    path = Path(auth._settings.google_credentials_path)
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["expiry"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    path.write_text(json.dumps(stored), encoding="utf-8")


def test_expired_access_token_triggers_refresher(tmp_path) -> None:
    auth = make_auth(tmp_path)
    state = auth.state_store.issue()
    auth.handle_callback({"code": "code-1", "state": state})
    _force_expired_credentials(auth)

    calls: list[str] = []

    def succeeding_refresher(credentials):
        calls.append("refresh")
        credentials.token = "REFRESHED-ACCESS-TOKEN"
        # google-auth convention: naive UTC expiry internally.
        credentials.expiry = datetime.now(timezone.utc).replace(
            tzinfo=None
        ) + timedelta(hours=1)

    auth._refresher = succeeding_refresher
    session = auth.authorized_session()
    assert session is not None
    assert calls == ["refresh"]  # non-empty but expired token was NOT trusted


def test_successful_refresh_persists_token_and_expiry(tmp_path) -> None:
    auth = make_auth(tmp_path)
    state = auth.state_store.issue()
    auth.handle_callback({"code": "code-1", "state": state})
    _force_expired_credentials(auth)

    def succeeding_refresher(credentials):
        credentials.token = "REFRESHED-ACCESS-TOKEN"
        credentials.expiry = datetime.now(timezone.utc).replace(
            tzinfo=None
        ) + timedelta(hours=1)
        # Google usually omits refresh_token on refresh: the stored one stays.

    auth._refresher = succeeding_refresher
    auth.authorized_session()

    path = Path(auth._settings.google_credentials_path)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["access_token"] == "REFRESHED-ACCESS-TOKEN"  # new token persisted
    expiry = datetime.fromisoformat(stored["expiry"])
    assert expiry.tzinfo is not None and expiry > datetime.now(timezone.utc)
    assert stored["refresh_token"] == REFRESH_TOKEN  # preserved, not rotated away
    assert auth.status().connected is True


def test_refresh_failure_marks_reauthorization_required(tmp_path) -> None:
    auth = make_auth(tmp_path)
    state = auth.state_store.issue()
    auth.handle_callback({"code": "code-1", "state": state})
    _force_expired_credentials(auth)  # access token stays non-empty; expiry past

    def failing_refresher(credentials):
        raise RefreshError("Token has been expired or revoked.")

    auth._refresher = failing_refresher

    with pytest.raises(GoogleAuthError) as excinfo:
        auth.authorized_session()
    message = str(excinfo.value)
    assert "reauthorization required" in message
    # Sanitized: no token material, no provider detail leakage.
    assert REFRESH_TOKEN not in message and ACCESS_TOKEN not in message

    status = auth.status()
    assert status.connected is False
    assert "reauthorization required" in status.detail


def test_unconnected_status_and_session(tmp_path) -> None:
    auth = make_auth(tmp_path)
    status = auth.status()
    assert status.connected is False and status.granted_scopes == []
    with pytest.raises(GoogleAuthError):
        auth.authorized_session()


# ---------------------------------------------------------------------------
# Redaction sweep
# ---------------------------------------------------------------------------


def test_secrets_never_appear_in_status_or_errors(tmp_path) -> None:
    auth = make_auth(tmp_path)
    state = auth.state_store.issue()
    result = auth.handle_callback({"code": "code-1", "state": state})
    body = json.dumps(
        {
            "outcome": result.outcome.value,
            "detail": result.detail,
            "status": auth.status().model_dump(),
        }
    )
    for secret in (ACCESS_TOKEN, REFRESH_TOKEN, CLIENT_SECRET):
        assert secret not in body


def test_clear_credentials(tmp_path) -> None:
    auth = make_auth(tmp_path)
    state = auth.state_store.issue()
    auth.handle_callback({"code": "code-1", "state": state})
    auth.clear_credentials()
    assert auth.status().connected is False


# ---------------------------------------------------------------------------
# API routes (frozen: start / callback / integrations)
# ---------------------------------------------------------------------------


def make_client(tmp_path, auth=None):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(make_settings(tmp_path))
    app.state.google_auth = auth or make_auth(tmp_path)
    return TestClient(app)


def test_route_start_returns_authorize_url(tmp_path) -> None:
    client = make_client(tmp_path)
    response = client.get("/api/auth/google/start")
    assert response.status_code == 200
    url = response.json()["authorize_url"]
    assert "access_type=offline" in url and "state=" in url
    assert CLIENT_SECRET not in url


def test_route_start_unexpected_failure_is_generic(tmp_path, caplog) -> None:
    import logging

    class ExplodingAuth:
        def authorization_url(self):
            raise RuntimeError(f"internal detail {REFRESH_TOKEN} leaked context")

    client = make_client(tmp_path, auth=ExplodingAuth())
    with caplog.at_level(logging.DEBUG):
        response = client.get("/api/auth/google/start")
    assert response.status_code == 503
    assert response.json()["detail"] == "authorization start is temporarily unavailable"
    # The secret marker appears in neither the response nor the logs.
    assert REFRESH_TOKEN not in response.text
    assert REFRESH_TOKEN not in caplog.text
    assert "unexpected failure issuing oauth start url" in caplog.text


def test_route_callback_unexpected_failure_logs_no_secrets(tmp_path, caplog) -> None:
    import logging

    class ExplodingAuth:
        def handle_callback(self, query):
            raise RuntimeError(f"callback blew up near {ACCESS_TOKEN}")

    client = make_client(tmp_path, auth=ExplodingAuth())
    with caplog.at_level(logging.DEBUG):
        response = client.get("/api/auth/google/callback?code=x&state=y")
    assert response.status_code == 500
    assert response.json()["detail"] == "authorization callback failed unexpectedly"
    assert ACCESS_TOKEN not in response.text
    assert ACCESS_TOKEN not in caplog.text
    assert "unexpected failure handling oauth callback" in caplog.text


def test_route_callback_flow_and_replay(tmp_path) -> None:
    client = make_client(tmp_path)
    start = client.get("/api/auth/google/start").json()["authorize_url"]
    state = parse_qs(urlparse(start).query)["state"][0]

    ok = client.get(f"/api/auth/google/callback?code=code-1&state={state}")
    assert ok.status_code == 200 and ok.json()["outcome"] == "connected"

    replay = client.get(f"/api/auth/google/callback?code=code-2&state={state}")
    assert replay.status_code == 400
    assert replay.json()["detail"]["outcome"] == "invalid_state"


def test_route_callback_denied_is_truthful(tmp_path) -> None:
    client = make_client(tmp_path)
    start = client.get("/api/auth/google/start").json()["authorize_url"]
    state = parse_qs(urlparse(start).query)["state"][0]
    denied = client.get(f"/api/auth/google/callback?state={state}&error=access_denied")
    assert denied.status_code == 200 and denied.json()["outcome"] == "denied"


def test_route_integrations_reports_google_status_without_secrets(tmp_path) -> None:
    client = make_client(tmp_path)
    body = client.get("/api/integrations").text
    assert '"google"' in body
    for secret in (ACCESS_TOKEN, REFRESH_TOKEN, CLIENT_SECRET):
        assert secret not in body


# ---------------------------------------------------------------------------
# CORE FIX 5 - serialized refresh + atomic credential persistence
# ---------------------------------------------------------------------------


def _connected_auth(tmp_path, refresher=None):
    auth = make_auth(tmp_path, refresher=refresher)
    state = auth.state_store.issue()
    result = auth.handle_callback({"code": "synthetic-code", "state": state})
    assert result.outcome is OAuthOutcome.CONNECTED
    return auth


def _credentials_file(tmp_path) -> Path:
    return tmp_path / "google_credentials.json"


def _force_expired(tmp_path) -> None:
    path = _credentials_file(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["expiry"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    path.write_text(json.dumps(data), encoding="utf-8")


def test_concurrent_expired_requests_trigger_exactly_one_refresh(tmp_path) -> None:
    import threading
    import time as _time

    refresh_calls: list[int] = []

    def slow_refresher(credentials) -> None:
        refresh_calls.append(1)
        _time.sleep(0.25)                      # widen the race window without a lock
        credentials.token = "rotated-access"
        credentials.expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    auth = _connected_auth(tmp_path, refresher=slow_refresher)
    _force_expired(tmp_path)

    barrier = threading.Barrier(2)
    sessions: list[object] = []

    def call() -> None:
        barrier.wait()                         # deterministic simultaneous start
        sessions.append(auth.authorized_session())

    threads = [threading.Thread(target=call) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(refresh_calls) == 1             # re-read under lock skipped the second
    assert len(sessions) == 2                  # both callers got a usable session
    tokens = {s.credentials.token for s in sessions}
    assert tokens == {"rotated-access"}


def test_interrupted_credential_write_never_truncates(tmp_path, monkeypatch) -> None:
    import os as _os

    import app.google.auth as auth_module

    auth = _connected_auth(tmp_path)
    original_text = _credentials_file(tmp_path).read_text(encoding="utf-8")

    def boom(src, dst):  # simulate an interrupted atomic move
        raise OSError("interrupted write")

    monkeypatch.setattr(auth_module.os, "replace", boom)
    with pytest.raises(GoogleAuthError):
        auth._write_credentials({"access_token": "brand-new-payload"})

    # The previously stored credentials survive byte-identical (never truncated)
    assert _credentials_file(tmp_path).read_text(encoding="utf-8") == original_text
    # ...and the temporary file is cleaned up, leaving no partial artifacts.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".credentials-")]
    assert leftovers == []


def test_credential_write_is_atomic_and_restricted(tmp_path) -> None:
    auth = _connected_auth(tmp_path)
    payload = json.loads(_credentials_file(tmp_path).read_text(encoding="utf-8"))
    payload["access_token"] = "second-write"
    auth._write_credentials(payload)
    stored = json.loads(_credentials_file(tmp_path).read_text(encoding="utf-8"))
    assert stored["access_token"] == "second-write"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".credentials-")] == []
