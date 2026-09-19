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

from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_origins(value: Any) -> Any:
    if isinstance(value, str):
        return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]
    return value


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
    eva_llm_allowed_origins: list[str] = Field(default_factory=list)

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

    @field_validator("eva_llm_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        return _parse_origins(value)

    @field_validator("eva_llm_allowed_origins")
    @classmethod
    def _validate_origins(cls, value: list[str]) -> list[str]:
        for origin in value:
            if not origin.startswith(("http://", "https://")):
                raise ValueError(
                    f"invalid self-hosted origin {origin!r}: absolute http(s) origin required"
                )
            if "/" in origin[len("https://") :]:
                raise ValueError(
                    f"invalid self-hosted origin {origin!r}: must be scheme+host[:port] only"
                )
        return value

    # ------------------------------------------------------------------ #

    def missing_required_self_hosted_settings(self) -> list[str]:
        """Environment variable names that must be configured before the
        mandatory inference route can serve requests."""
        missing: list[str] = []
        if not self.eva_llm_base_url.strip():
            missing.append("EVA_LLM_BASE_URL")
        if not self.eva_llm_model.strip():
            missing.append("EVA_LLM_MODEL")
        return missing

    @property
    def self_hosted_configured(self) -> bool:
        return not self.missing_required_self_hosted_settings()

    @property
    def self_hosted_fallback_configured(self) -> bool:
        return bool(
            self.eva_llm_fallback_base_url.strip() and self.eva_llm_fallback_model.strip()
        )

    def is_allowed_self_hosted_origin(self, url: str) -> bool:
        """True only for URLs whose scheme+host[:port] exactly matches a
        configured allowlist origin. Cloud endpoints must never be added to
        the allowlist for the mandatory route."""
        normalized = url.rstrip("/")
        return any(normalized == origin or normalized.startswith(origin + "/")
                   for origin in self.eva_llm_allowed_origins)

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id.strip() and self.google_client_secret.strip())
