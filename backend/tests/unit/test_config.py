"""Config loading regressions: REAL environment and .env loading (not only
constructor kwargs) for EVA_LLM_ALLOWED_ORIGINS, plus credential-bearing URL
rejection with no-secret-leak assertions.

Run: python -m pytest backend/tests/unit/test_config.py -q
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, UnsafeEndpointConfigurationError
from app.main import create_app

SECRET_MARKER = "TOP_SECRET_PASSWORD_7391"


def _clean_env(monkeypatch) -> None:
    for name in (
        "EVA_LLM_ALLOWED_ORIGINS",
        "EVA_LLM_BASE_URL",
        "EVA_LLM_FALLBACK_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# FIX 1: documented comma-separated format loads from real ENV and .env
# ---------------------------------------------------------------------------


def test_blank_env_origin_string_loads_as_empty_list(monkeypatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("EVA_LLM_ALLOWED_ORIGINS", "")
    assert Settings(_env_file=None).eva_llm_allowed_origins == []


def test_comma_separated_env_string_is_normalized(monkeypatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(
        "EVA_LLM_ALLOWED_ORIGINS",
        "https://infer.tailnet.example, https://backup.tailnet.example/",
    )
    assert Settings(_env_file=None).eva_llm_allowed_origins == [
        "https://infer.tailnet.example",
        "https://backup.tailnet.example",
    ]


def test_single_origin_env_string(monkeypatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("EVA_LLM_ALLOWED_ORIGINS", "https://infer.tailnet.example")
    assert Settings(_env_file=None).eva_llm_allowed_origins == [
        "https://infer.tailnet.example"
    ]


def test_origins_load_from_a_real_dotenv_file(tmp_path, monkeypatch) -> None:
    _clean_env(monkeypatch)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "EVA_LLM_BASE_URL=https://infer.tailnet.example/v1\n"
        "EVA_LLM_MODEL=actual-server-model-id\n"
        "EVA_LLM_ALLOWED_ORIGINS=https://infer.tailnet.example/, https://backup.tailnet.example\n",
        encoding="utf-8",
    )
    settings = Settings(_env_file=str(dotenv))
    assert settings.eva_llm_allowed_origins == [
        "https://infer.tailnet.example",
        "https://backup.tailnet.example",
    ]
    # E) allowlist semantics keep working end-to-end from .env values.
    assert settings.self_hosted_configured is True


def test_blank_origins_from_dotenv(tmp_path, monkeypatch) -> None:
    _clean_env(monkeypatch)
    dotenv = tmp_path / ".env"
    dotenv.write_text("EVA_LLM_ALLOWED_ORIGINS=\n", encoding="utf-8")
    assert Settings(_env_file=str(dotenv)).eva_llm_allowed_origins == []


def test_invalid_scheme_rejected_from_env(monkeypatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("EVA_LLM_ALLOWED_ORIGINS", "ftp://nope")
    with pytest.raises(UnsafeEndpointConfigurationError):
        Settings(_env_file=None)


def test_allowlist_semantics_unchanged_from_env(monkeypatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("EVA_LLM_BASE_URL", "https://infer.tailnet.example/v1")
    monkeypatch.setenv("EVA_LLM_MODEL", "model-x")
    monkeypatch.setenv("EVA_LLM_ALLOWED_ORIGINS", "https://infer.tailnet.example")
    settings = Settings(_env_file=None)
    assert settings.self_hosted_configured is True
    assert settings.is_allowed_self_hosted_origin("https://infer.tailnet.example/v1")
    assert not settings.is_allowed_self_hosted_origin("https://api.openai.com/v1")


# ---------------------------------------------------------------------------
# FIX 2: credential-bearing URLs rejected, never echoed anywhere
# ---------------------------------------------------------------------------


def test_credential_bearing_base_url_rejected_without_leak(monkeypatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(
        "EVA_LLM_BASE_URL", f"https://user:{SECRET_MARKER}@host.example/v1"
    )
    with pytest.raises(UnsafeEndpointConfigurationError) as excinfo:
        Settings(_env_file=None)
    assert SECRET_MARKER not in str(excinfo.value)
    assert "credentials are not allowed" in str(excinfo.value)


def test_credential_bearing_fallback_url_rejected_without_leak(monkeypatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(
        "EVA_LLM_FALLBACK_BASE_URL", f"https://user:{SECRET_MARKER}@host.example/v1"
    )
    with pytest.raises(UnsafeEndpointConfigurationError) as excinfo:
        Settings(_env_file=None)
    assert SECRET_MARKER not in str(excinfo.value)


def test_credential_bearing_allowlist_entry_rejected_without_leak(monkeypatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(
        "EVA_LLM_ALLOWED_ORIGINS", f"https://user:{SECRET_MARKER}@host.example"
    )
    with pytest.raises(UnsafeEndpointConfigurationError) as excinfo:
        Settings(_env_file=None)
    assert SECRET_MARKER not in str(excinfo.value)


def test_endpoint_origin_never_contains_userinfo() -> None:
    leaked = Settings._endpoint_origin(f"https://user:{SECRET_MARKER}@host.example/v1")
    assert leaked is None
    assert Settings.is_allowed_self_hosted_origin(
        Settings(_env_file=None), f"https://user:{SECRET_MARKER}@host.example/v1"
    ) is False


def test_health_never_exposes_credential_marker() -> None:
    settings = Settings(
        _env_file=None,
        EVA_LLM_BASE_URL="https://infer.tailnet.example/v1",
        EVA_LLM_MODEL="model-x",
        EVA_LLM_ALLOWED_ORIGINS="https://infer.tailnet.example",
    )
    client = TestClient(create_app(settings))
    response = client.get("/api/health")
    assert response.status_code == 200
    assert SECRET_MARKER not in response.text
    # A runtime URL carrying credentials is refused by the allowlist check.
    assert not settings.is_allowed_self_hosted_origin(
        f"https://user:{SECRET_MARKER}@host.example/v1"
    )


def test_health_wording_is_current_not_stale_milestones() -> None:
    settings = Settings(
        _env_file=None,
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
    )
    body = TestClient(create_app(settings)).get("/api/health").json()
    text = str(body)
    assert "not initialized yet" not in text  # stale A01 wording gone
    assert "flow not implemented yet" not in text  # stale A02 wording gone
    assert "persistence layer implemented" in body["components"]["database"]["detail"]
    assert (
        "live account connection not probed"
        in body["components"]["google"]["detail"]
    )


# ---------------------------------------------------------------------------
# Malformed ports are rejected without echoing the configured URL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://host.example:not-a-port",
        "https://host.example:99999",
    ],
)
class TestMalformedPorts:
    def test_origin_with_bad_port_rejected_without_leak(self, monkeypatch, bad_url) -> None:
        _clean_env(monkeypatch)
        monkeypatch.setenv("EVA_LLM_ALLOWED_ORIGINS", bad_url)
        with pytest.raises(UnsafeEndpointConfigurationError) as excinfo:
            Settings(_env_file=None)
        assert bad_url not in str(excinfo.value)

    def test_base_url_with_bad_port_rejected_without_leak(self, monkeypatch, bad_url) -> None:
        _clean_env(monkeypatch)
        monkeypatch.setenv("EVA_LLM_BASE_URL", f"{bad_url}/v1")
        with pytest.raises(UnsafeEndpointConfigurationError) as excinfo:
            Settings(_env_file=None)
        assert bad_url not in str(excinfo.value)

    def test_fallback_with_bad_port_rejected_without_leak(self, monkeypatch, bad_url) -> None:
        _clean_env(monkeypatch)
        monkeypatch.setenv("EVA_LLM_FALLBACK_BASE_URL", f"{bad_url}/v1")
        with pytest.raises(UnsafeEndpointConfigurationError) as excinfo:
            Settings(_env_file=None)
        assert bad_url not in str(excinfo.value)


def test_valid_port_still_accepted(monkeypatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("EVA_LLM_ALLOWED_ORIGINS", "http://100.64.0.2:8321")
    assert Settings(_env_file=None).eva_llm_allowed_origins == ["http://100.64.0.2:8321"]
