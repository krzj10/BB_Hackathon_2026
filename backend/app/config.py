"""Backend configuration (Stream A - Core Platform).

Field names map 1:1 (case-insensitively) to the environment contract in
docs/EVA_IMPLEMENTATION_PLAN.md v2.0 section 2.2; see .env.example.

Startup policy (A00 decision, documented in docs/api-contracts.md):
- No cloud key, model, endpoint or adapter is required for startup. Both cloud
  permission defaults are false and the model cannot enable them.
- Missing self-hosted base URL/model do not crash the skeleton during A00 so
  health can start with external services unavailable; they are reported as
  unconfigured via missing_required_self_hosted_settings() and surfaced in
  /api/health. The provider adapter (B01, against this frozen contract) must
  refuse to serve requests while required settings are blank, and A-wiring may
  escalate that to a hard startup failure once adapters land.
- EVA_LLM_ALLOWED_ORIGINS is the explicit self-hosted origin allowlist for the
  mandatory route; it is controlled by user/backend configuration, never by
  model output, and cloud endpoints must never appear in it.
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class UnsafeEndpointConfigurationError(RuntimeError):
    """Credential-bearing or structurally invalid endpoint/origin configuration.

    Deliberately NOT a ValueError: it propagates from validators without being
    wrapped into a pydantic ValidationError, so the error text stays exactly
    the fixed generic message below and can never echo a credential-bearing
    input value."""


_CREDENTIAL_REJECT = (
    "invalid self-hosted endpoint configuration: URL userinfo/credentials are not allowed"
)
_ORIGIN_REJECT = (
    "invalid self-hosted origin: absolute http(s) scheme+host[:port] required, "
    "without path, query or fragment"
)
_ENDPOINT_REJECT = "invalid self-hosted endpoint URL: safe http(s) URL with host required"


def _split_origins(value: Any) -> Any:
    if isinstance(value, str):
        return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]
    return value


def _validated_origin(raw: str) -> str:
    """Structurally validate one allowlist entry and return the normalized
    origin. Credential-bearing entries are rejected outright (never sanitized
    and kept); error text never echoes the input value."""
    try:
        parts = urlsplit(raw)
    except ValueError:
        raise UnsafeEndpointConfigurationError(_ORIGIN_REJECT) from None
    if parts.username is not None or parts.password is not None:
        raise UnsafeEndpointConfigurationError(_CREDENTIAL_REJECT)
    _require_valid_port(parts)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeEndpointConfigurationError(_ORIGIN_REJECT)
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise UnsafeEndpointConfigurationError(_ORIGIN_REJECT)
    # netloc is safe here: userinfo was rejected above.
    return f"{parts.scheme}://{parts.netloc}"


def _validated_endpoint(raw: str) -> str:
    """Validate an endpoint base URL (a path such as /v1 IS allowed). Blank is
    allowed (reported unconfigured); anything unsafe is rejected without
    echoing the value."""
    if not raw.strip():
        return raw
    try:
        parts = urlsplit(raw.strip())
    except ValueError:
        raise UnsafeEndpointConfigurationError(_ENDPOINT_REJECT) from None
    if parts.username is not None or parts.password is not None:
        raise UnsafeEndpointConfigurationError(_CREDENTIAL_REJECT)
    _require_valid_port(parts)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeEndpointConfigurationError(_ENDPOINT_REJECT)
    if parts.query or parts.fragment:
        raise UnsafeEndpointConfigurationError(_ENDPOINT_REJECT)
    return raw.strip()


def _require_valid_port(parts: Any) -> None:
    """Reject malformed/out-of-range ports (urlsplit keeps them but .port
    raises ValueError). Fixed generic error; the URL is never echoed."""
    try:
        parts.port  # noqa: B018 - access validates
    except ValueError:
        raise UnsafeEndpointConfigurationError(
            "invalid self-hosted endpoint configuration: malformed or out-of-range port"
        ) from None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- application ------------------------------------------------------
    eva_env: str = "development"
    eva_timezone: str = "Europe/Warsaw"
    eva_db_url: str = "sqlite:///./eva.db"

    # --- mandatory self-hosted inference ----------------------------------
    eva_llm_provider: str = "openai_compatible"
    eva_llm_base_url: str = ""
    eva_llm_model: str = ""
    eva_llm_api_key: str = Field(default="", repr=False)
    eva_llm_fallback_base_url: str = ""
    eva_llm_fallback_model: str = ""
    eva_llm_fallback_api_key: str = Field(default="", repr=False)
    #: NoDecode keeps the raw environment/.env string out of pydantic-settings'
    #: JSON pre-decoding for complex fields, so the documented comma-separated
    #: format reaches _split_origins verbatim ("" -> [], "a,b" -> [a, b]).
    eva_llm_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    #: Exact browser origins allowed to reach EVA's mutating API (A04). This is
    #: deliberately SEPARATE from eva_llm_allowed_origins, which protects the
    #: private inference destination - not browser access to EVA. Comma-
    #: separated exact http(s) origins; no wildcards; same credential/path/
    #: query safety rules as every other origin in this file.
    eva_app_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- optional cloud extension (disabled by default) --------------------
    eva_allow_cloud_inference: bool = False
    eva_allow_workspace_cloud_inference: bool = False

    # --- speech-to-text -----------------------------------------------------
    eva_stt_provider: str = "faster-whisper"
    eva_whisper_model: str = "base"
    eva_whisper_device: str = "cpu"
    eva_whisper_compute_type: str = "int8"
    eva_wake_word_enabled: bool = False

    # --- Google ---------------------------------------------------------------
    google_client_id: str = ""
    google_client_secret: str = Field(default="", repr=False)
    google_redirect_uri: str = "http://localhost:8000/api/auth/google/callback"
    #: Offline credentials (refresh/access tokens) live OUTSIDE source control.
    #: The default sits under secrets/, which is gitignored; the file is
    #: written 0600 best-effort and never read into API responses or logs.
    google_credentials_path: str = "secrets/google_credentials.json"

    @field_validator("eva_llm_base_url", "eva_llm_fallback_base_url")
    @classmethod
    def _validate_endpoints(cls, value: str) -> str:
        return _validated_endpoint(value)

    @field_validator("eva_llm_allowed_origins", "eva_app_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        return _split_origins(value)

    @field_validator("eva_llm_allowed_origins", "eva_app_allowed_origins")
    @classmethod
    def _validate_origins(cls, value: list[str]) -> list[str]:
        # Structural validation + normalization; rejects credentials, paths,
        # queries, fragments and non-http(s) with fixed generic messages.
        return [_validated_origin(origin) for origin in value]

    # ------------------------------------------------------------------ #

    @staticmethod
    def _endpoint_origin(url: str) -> str | None:
        """scheme+host[:port] of a URL, normalized (trailing slash removed).

        Returns None for anything unsafe - including URLs carrying userinfo -
        so extracted origins can never contain credentials, and callers that
        embed this value in diagnostics stay secret-free."""
        try:
            parts = urlsplit(url.strip())
        except ValueError:
            return None
        if parts.username is not None or parts.password is not None:
            return None
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None
        return f"{parts.scheme}://{parts.netloc}"

    def missing_required_self_hosted_settings(self) -> list[str]:
        """Environment variable names that must be configured before the
        mandatory inference route can serve requests."""
        missing: list[str] = []
        if not self.eva_llm_base_url.strip():
            missing.append("EVA_LLM_BASE_URL")
        if not self.eva_llm_model.strip():
            missing.append("EVA_LLM_MODEL")
        return missing

    def self_hosted_route_blockers(self) -> list[str]:
        """Why the mandatory self-hosted route is not usable yet.

        Usable requires: base URL + model configured, an explicit non-empty
        origin allowlist, and the endpoint's origin present in that allowlist.
        Cloud endpoints must never be added to the allowlist; it is
        user/backend configuration, never model output.
        """
        blockers = [f"missing {name}" for name in self.missing_required_self_hosted_settings()]
        if not self.eva_llm_allowed_origins:
            blockers.append("EVA_LLM_ALLOWED_ORIGINS is empty (explicit allowlist required)")
        elif self.eva_llm_base_url.strip():
            origin = self._endpoint_origin(self.eva_llm_base_url)
            if origin is None:
                blockers.append(
                    "EVA_LLM_BASE_URL is not a valid safe http(s) URL"
                )  # never echoes the value (it may carry credentials)
            elif origin not in self.eva_llm_allowed_origins:
                blockers.append(
                    f"EVA_LLM_BASE_URL origin {origin} is not in EVA_LLM_ALLOWED_ORIGINS"
                )
        return blockers

    @property
    def self_hosted_configured(self) -> bool:
        """True only when the mandatory self-hosted route is usable: endpoint,
        model and an explicit matching private-origin allowlist entry."""
        return not self.self_hosted_route_blockers()

    @property
    def self_hosted_fallback_configured(self) -> bool:
        """The optional fallback obeys the same private-origin requirement:
        it is only considered configured when its endpoint origin appears in
        EVA_LLM_ALLOWED_ORIGINS. Absence is allowed and never enables cloud."""
        base = self.eva_llm_fallback_base_url.strip()
        if not base or not self.eva_llm_fallback_model.strip():
            return False
        origin = self._endpoint_origin(base)
        return origin is not None and origin in self.eva_llm_allowed_origins

    def is_allowed_self_hosted_origin(self, url: str) -> bool:
        """True only for URLs whose scheme+host[:port] exactly matches a
        configured allowlist origin. Cloud endpoints must never be added to
        the allowlist for the mandatory route."""
        origin = self._endpoint_origin(url)
        return origin is not None and origin in self.eva_llm_allowed_origins

    def is_allowed_app_origin(self, origin: str | None) -> bool:
        """Exact-match browser-origin check for EVA's own mutating routes
        (A04). The compared value must already be a bare normalized origin;
        wildcards are not supported by construction (closed list)."""
        if not origin:
            return False
        try:
            normalized = _validated_origin(origin)
        except UnsafeEndpointConfigurationError:
            return False
        return normalized in self.eva_app_allowed_origins

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id.strip() and self.google_client_secret.strip())
