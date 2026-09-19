"""A02 Google OAuth integration tests - fully hermetic (no live Google).

The token exchange and refresh paths are injected fakes; credential files
live in pytest tmp dirs. Synthetic values only; redaction is asserted with
distinctive marker strings."""

from __future__ import annotations

import json
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
        }
    return GoogleAuth(
        make_settings(tmp_path), token_exchanger=exchanger, refresher=refresher
    )


# ---------------------------------------------------------------------------
# State store: one-time, expiry, replay
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

    status = auth.status()
    assert status.connected is True
    assert set(status.granted_scopes) == set(REQUIRED_SCOPES)


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
        return {"access_token": ACCESS_TOKEN, "scope": " ".join(REQUIRED_SCOPES)}

    auth = make_auth(tmp_path, exchanger=exchanger)
    state = auth.state_store.issue()
    result = auth.handle_callback({"code": "code-1", "state": state})
    assert result.outcome is OAuthOutcome.EXCHANGE_FAILED  # offline access mandatory
    assert not Path(auth._settings.google_credentials_path).exists()


# ---------------------------------------------------------------------------
# Refresh failure -> reauthorization-required
# ---------------------------------------------------------------------------


def test_refresh_failure_marks_reauthorization_required(tmp_path) -> None:
    auth = make_auth(tmp_path)
    state = auth.state_store.issue()
    assert auth.handle_callback({"code": "code-1", "state": state}).outcome is OAuthOutcome.CONNECTED

    # Force the stored credentials to look stale (no access token) and make
    # refresh fail exactly like Google's 7-day testing-mode expiry.
    path = Path(auth._settings.google_credentials_path)
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["access_token"] = None
    path.write_text(json.dumps(stored), encoding="utf-8")

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
