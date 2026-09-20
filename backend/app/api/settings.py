"""GET/PUT /api/settings/llm plus Test Connection and Detect Models (B01).

Sanitization invariant: only the frozen sanitized envelopes are serialized.
Secret values never leave this module - API-key fields are write-only, reads
expose presence flags, and log lines carry booleans, counts and HTTP/status
classes only. Endpoint URLs are safe to echo because ``Settings`` rejects
credential-bearing endpoints at load; REJECTED candidates are never echoed at
all (a rejected value may well carry credentials).

Fail-closed invariant: a candidate endpoint must be an absolute http(s) URL with
no userinfo/query/fragment AND its exact origin must already sit in
``EVA_LLM_ALLOWED_ORIGINS``, which the writable surface cannot extend. Cloud
endpoints are therefore refused on the mandatory route by construction, and an
empty allowlist refuses everything.

Liveness invariant: a successful PUT mutates the live ``Settings`` object and
rebuilds ``app.state.llm_router`` in the same request, so no later inference
call can use a stale route. ``main.py`` does not construct a router yet
(A-wiring pending), so requests build one from the live settings when state is
absent - these routes answer honestly instead of 500ing before that wiring lands.

Probe invariant: Test Connection sends ONE synthetic prompt and never any
Workspace content, and Detect Models only reads a model listing; neither may
ever surface a 500 - an outage is a sanitized ``unavailable`` body.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request

from ..config import Settings
from ..contracts.api import (
    DetectModelsResponse,
    LlmSettingsResponse,
    LlmSettingsUpdateRequest,
    LlmTestConnectionResponse,
)
from ..contracts.domain import HealthStatus
from ..contracts.providers import ChatMessage, ChatRole, LLMProvider, ProviderHealth
from ..llm.base import LLMError
from ..llm.openai_compatible import PROVIDER_NAME
from ..llm.router import LLMRouter
from .security import require_origin

logger = logging.getLogger("eva.api.settings")

router = APIRouter(prefix="/api/settings", tags=["settings"])

#: The ONLY prompt this module may send: a connection probe must never carry
#: Workspace-derived content (plan section 2.1.1).
SYNTHETIC_TEST_PROMPT = "EVA connection probe. Reply with exactly the word: ok."


def _provider_identity(settings: Settings) -> str:
    """Configured provider identity, defaulting to the canonical self-hosted one."""
    return settings.eva_llm_provider.strip() or PROVIDER_NAME


def _sanitized_settings(settings: Settings) -> LlmSettingsResponse:
    """Sanitized read model: presence flags for secrets, never values."""
    return LlmSettingsResponse(
        provider=_provider_identity(settings),
        base_url=settings.eva_llm_base_url.strip() or None,
        model=settings.eva_llm_model.strip() or None,
        api_key_present=bool(settings.eva_llm_api_key.strip()),
        fallback_base_url=settings.eva_llm_fallback_base_url.strip() or None,
        fallback_model=settings.eva_llm_fallback_model.strip() or None,
        fallback_api_key_present=bool(settings.eva_llm_fallback_api_key.strip()),
        configured=settings.self_hosted_configured,
        # Both cloud permissions stay exactly as configured (false by default);
        # this module implements no cloud adapter to flip them.
        allow_cloud_inference=settings.eva_allow_cloud_inference,
        allow_workspace_cloud_inference=settings.eva_allow_workspace_cloud_inference,
    )


def _router(request: Request, settings: Settings) -> LLMProvider:
    """Live router from app state, or one built from live settings (A-wiring of
    ``app.state.llm_router`` is still pending). Either way the result is an
    allowlist-bound self-hosted router - no other implementation exists."""
    existing = getattr(request.app.state, "llm_router", None)
    if isinstance(existing, LLMProvider):
        return existing
    return LLMRouter.from_settings(settings)


def _validated_endpoint(value: str, *, field: str) -> str:
    """Absolute, credential-free http(s) endpoint or a 400 with fixed text.

    The candidate is never echoed back: it may carry credentials in userinfo."""
    candidate = value.strip()
    if not candidate:
        raise HTTPException(status_code=400, detail=f"{field} must not be blank")
    try:
        parts = urlsplit(candidate)
        parts.port  # noqa: B018 - attribute access validates scheme+port
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"{field} must be a well-formed absolute http(s) URL without credentials",
        ) from None
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise HTTPException(
            status_code=400,
            detail=f"{field} must be an absolute http(s) URL without credentials",
        )
    return candidate


def _require_allowlisted(settings: Settings, candidate: str, *, field: str) -> None:
    """Mandatory-route allowlist gate for a candidate endpoint."""
    if not settings.eva_llm_allowed_origins:
        raise HTTPException(
            status_code=400,
            detail="EVA_LLM_ALLOWED_ORIGINS is empty; the self-hosted route stays closed",
        )
    if not settings.is_allowed_self_hosted_origin(candidate):
        # Covers cloud endpoints too: they are never valid entries for the
        # mandatory self-hosted route, whatever the flags say.
        raise HTTPException(
            status_code=400,
            detail=f"{field} origin is not in EVA_LLM_ALLOWED_ORIGINS (self-hosted only)",
        )


@router.get("/llm", response_model=LlmSettingsResponse)
async def get_llm_settings(request: Request) -> LlmSettingsResponse:
    return _sanitized_settings(request.app.state.settings)


@router.put("/llm", response_model=LlmSettingsResponse)
async def put_llm_settings(
    request: Request, payload: LlmSettingsUpdateRequest
) -> LlmSettingsResponse:
    require_origin(request)
    settings: Settings = request.app.state.settings

    base_url = settings.eva_llm_base_url
    model = settings.eva_llm_model
    api_key = settings.eva_llm_api_key
    fallback_base_url = settings.eva_llm_fallback_base_url
    fallback_model = settings.eva_llm_fallback_model
    fallback_api_key = settings.eva_llm_fallback_api_key

    if payload.base_url is not None:
        candidate = _validated_endpoint(payload.base_url, field="base_url")
        _require_allowlisted(settings, candidate, field="base_url")
        base_url = candidate
    if payload.model is not None:
        model = payload.model.strip()
    if payload.api_key is not None:
        # Write-only. An explicit empty string clears the stored key.
        api_key = payload.api_key.strip()
    if payload.fallback_base_url is not None:
        candidate = _validated_endpoint(payload.fallback_base_url, field="fallback_base_url")
        _require_allowlisted(settings, candidate, field="fallback_base_url")
        fallback_base_url = candidate
    if payload.fallback_model is not None:
        fallback_model = payload.fallback_model.strip()
    if payload.fallback_api_key is not None:
        fallback_api_key = payload.fallback_api_key.strip()

    settings.eva_llm_base_url = base_url
    settings.eva_llm_model = model
    settings.eva_llm_api_key = api_key
    settings.eva_llm_fallback_base_url = fallback_base_url
    settings.eva_llm_fallback_model = fallback_model
    settings.eva_llm_fallback_api_key = fallback_api_key

    # Rebuild BEFORE responding: subsequent calls use the new route, never the
    # one that existed when this request arrived.
    app_router = LLMRouter.from_settings(settings)
    request.app.state.llm_router = app_router
    logger.info(
        "llm settings updated (base_url_changed=%s model_changed=%s api_key_written=%s "
        "fallback_configured=%s route_ready=%s)",
        payload.base_url is not None,
        payload.model is not None,
        bool(api_key),
        app_router.fallback is not None,
        settings.self_hosted_configured,
    )
    return _sanitized_settings(settings)


@router.post("/llm/test", response_model=LlmTestConnectionResponse)
async def test_llm_connection(request: Request) -> LlmTestConnectionResponse:
    require_origin(request)
    settings: Settings = request.app.state.settings
    provider = _provider_identity(settings)
    configured_model = settings.eva_llm_model.strip() or None

    if not settings.self_hosted_configured:
        blockers = "; ".join(settings.self_hosted_route_blockers())
        return LlmTestConnectionResponse(
            health=ProviderHealth(
                status=HealthStatus.UNAVAILABLE,
                detail=f"self-hosted route unusable: {blockers}" if blockers else "route unusable",
                provider=provider,
                model=configured_model,
            ),
            latency_ms=None,
            model=configured_model,
        )

    started = time.monotonic()
    try:
        response = await _router(request, settings).chat(
            [ChatMessage(role=ChatRole.USER, content=SYNTHETIC_TEST_PROMPT)]
        )
    except LLMError as exc:
        # A probe never 500s: the outage is the answer, sanitized.
        logger.error("llm connection test failed (%s)", exc.code)
        return LlmTestConnectionResponse(
            health=ProviderHealth(
                status=HealthStatus.UNAVAILABLE,
                detail=exc.message,
                provider=provider,
                model=configured_model,
            ),
            latency_ms=_elapsed_ms(started),
            model=configured_model,
        )
    except Exception as exc:  # never a 500 from a connectivity probe
        logger.error("llm connection test failed (%s)", type(exc).__name__)
        return LlmTestConnectionResponse(
            health=ProviderHealth(
                status=HealthStatus.UNAVAILABLE,
                detail="connection test failed",
                provider=provider,
                model=configured_model,
            ),
            latency_ms=_elapsed_ms(started),
            model=configured_model,
        )
    served_model = response.model or configured_model
    return LlmTestConnectionResponse(
        health=ProviderHealth(
            status=HealthStatus.READY,
            detail="synthetic chat request succeeded",
            provider=response.provider or provider,
            model=served_model,
        ),
        latency_ms=_elapsed_ms(started),
        model=served_model,
    )


@router.post("/llm/detect", response_model=DetectModelsResponse)
async def detect_llm_models(request: Request) -> DetectModelsResponse:
    require_origin(request)
    settings: Settings = request.app.state.settings
    if not settings.self_hosted_configured:
        # Error-free by contract: nothing configured means nothing detected.
        logger.info("llm model detection skipped (route not configured)")
        return DetectModelsResponse(models=[])
    try:
        models = await _router(request, settings).list_models()
    except LLMError as exc:
        logger.error("llm model detection failed (%s)", exc.code)
        return DetectModelsResponse(models=[])
    except Exception as exc:  # detection never surfaces as a 500
        logger.error("llm model detection failed (%s)", type(exc).__name__)
        return DetectModelsResponse(models=[])
    logger.info("llm model detection returned %d models", len(models))
    return DetectModelsResponse(models=models)


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
