"""Single inference route for EVA: self-hosted primary + optional explicit
self-hosted fallback (B01). Implements the frozen ``LLMProvider`` protocol.

No cloud provider can ever be constructed here. The only concrete provider type
this module knows is :class:`OpenAICompatibleProvider`, which is allowlist-bound
by construction, and :meth:`LLMRouter.from_settings` builds a route only when
``Settings`` reports it configured AND allowlisted. Both cloud permission flags
are therefore inert inside this module: a private-provider failure is reported to
the caller (which surfaces "inference unavailable"), never silently "recovered"
by a public-cloud retry - plan section 2.1 point 6.

Fallback policy differs deliberately from STT: an LLM timeout, unavailability or
protocol failure DOES try the configured fallback, because no irremovable native
permit is held here and the caller still has budget. The fallback is a separate
explicitly configured self-hosted server; a same-host fallback guards against a
model-level failure only, not a host/network outage - so health detail always
says which route answered.
"""

from __future__ import annotations

import logging

from ..config import Settings
from ..contracts.domain import HealthStatus, ToolDefinition
from ..contracts.providers import ChatMessage, LLMModelInfo, LLMResponse, ProviderHealth
from .base import (
    LLMError,
    LLMNotConfiguredError,
    LLMProtocolError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from .openai_compatible import PROVIDER_NAME, OpenAICompatibleProvider

logger = logging.getLogger("eva.llm.router")

#: Failures that justify trying the configured self-hosted fallback. A config
#: refusal (``LLMNotConfiguredError``) is NOT one of them: it means EVA's own
#: machine setup forbids routing, so nothing may be sent at all.
_FALLBACK_ELIGIBLE = (LLMTimeoutError, LLMUnavailableError, LLMProtocolError)

_NOT_CONFIGURED_DETAIL = (
    "no configured and allowlisted self-hosted inference route is available"
)


class LLMRouter:
    """Provider-independent private inference client over one or two self-hosted routes."""

    def __init__(
        self,
        *,
        primary: OpenAICompatibleProvider | None = None,
        fallback: OpenAICompatibleProvider | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    @classmethod
    def from_settings(cls, settings: Settings) -> "LLMRouter":
        """Build the router from live settings.

        A route exists only when config reports it configured, which already
        requires its endpoint origin to appear in ``EVA_LLM_ALLOWED_ORIGINS``.
        Blank or unapproved endpoints yield no route (fail closed) rather than a
        defaulted - let alone cloud - one."""
        primary = fallback = None
        if settings.self_hosted_configured:
            primary = OpenAICompatibleProvider(
                base_url=settings.eva_llm_base_url,
                model=settings.eva_llm_model,
                api_key=settings.eva_llm_api_key or None,
                allowed_origins=settings.eva_llm_allowed_origins,
            )
        if settings.self_hosted_fallback_configured:
            fallback = OpenAICompatibleProvider(
                base_url=settings.eva_llm_fallback_base_url,
                model=settings.eva_llm_fallback_model,
                api_key=settings.eva_llm_fallback_api_key or None,
                allowed_origins=settings.eva_llm_allowed_origins,
            )
        return cls(primary=primary, fallback=fallback)

    @property
    def primary(self) -> OpenAICompatibleProvider | None:
        return self._primary

    @property
    def fallback(self) -> OpenAICompatibleProvider | None:
        return self._fallback

    # ------------------------------------------------------------------ #
    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        response_schema: dict | None = None,
    ) -> LLMResponse:
        routes = self._routes()
        if not routes:
            raise LLMNotConfiguredError(_NOT_CONFIGURED_DETAIL)
        failures: list[tuple[str, LLMError]] = []
        for label, provider in routes:
            try:
                return await provider.chat(messages, tools=tools, response_schema=response_schema)
            except _FALLBACK_ELIGIBLE as exc:
                # Stable code only - never provider text, URLs or keys.
                logger.error("llm %s route failed (%s)", label, exc.code)
                failures.append((label, exc))
        if len(failures) == 1:
            raise failures[0][1]  # single-route setups keep their exact stable code
        detail = "; ".join(f"{label}: {error.code}" for label, error in failures)
        raise LLMUnavailableError(
            f"self-hosted inference unavailable after {len(failures)} attempts ({detail})"
        )

    async def list_models(self) -> list[LLMModelInfo]:
        routes = self._routes()
        if not routes:
            raise LLMNotConfiguredError(_NOT_CONFIGURED_DETAIL)
        failures: list[tuple[str, LLMError]] = []
        for label, provider in routes:
            try:
                return await provider.list_models()
            except _FALLBACK_ELIGIBLE as exc:
                logger.error("llm %s model discovery failed (%s)", label, exc.code)
                failures.append((label, exc))
        if len(failures) == 1:
            raise failures[0][1]
        raise LLMUnavailableError(
            "self-hosted model discovery failed on every configured route"
        )

    async def health(self) -> ProviderHealth:
        """Aggregated readiness of the configured self-hosted routes.

        Primary ready wins. Otherwise a ready fallback is DEGRADED with an
        explicit "primary unavailable" detail - never presented as full health.
        A route that answered but does not serve its configured model id stays
        DEGRADED. Nothing usable is UNAVAILABLE."""
        primary = await self._probe(self._primary)
        fallback = await self._probe(self._fallback)
        if primary is not None and primary.status is HealthStatus.READY:
            return primary.model_copy(update={"provider": primary.provider or PROVIDER_NAME})
        if fallback is not None and fallback.status is HealthStatus.READY:
            return ProviderHealth(
                status=HealthStatus.DEGRADED,
                detail="primary unavailable; local fallback ready",
                provider=fallback.provider or PROVIDER_NAME,
                model=fallback.model,
            )
        for probe in (primary, fallback):
            if probe is not None and probe.status is HealthStatus.DEGRADED:
                return probe.model_copy(
                    update={
                        "provider": probe.provider or PROVIDER_NAME,
                        "detail": probe.detail or "self-hosted inference route degraded",
                    }
                )
        unavailable = next((probe for probe in (primary, fallback) if probe is not None), None)
        detail = (unavailable.detail if unavailable is not None else None) or _NOT_CONFIGURED_DETAIL
        return ProviderHealth(
            status=HealthStatus.UNAVAILABLE,
            detail=detail,
            provider=PROVIDER_NAME,
            model=unavailable.model if unavailable is not None else None,
        )

    # ------------------------------------------------------------------ #
    def _routes(self) -> list[tuple[str, OpenAICompatibleProvider]]:
        return [
            (label, provider)
            for label, provider in (("primary", self._primary), ("fallback", self._fallback))
            if provider is not None
        ]

    @staticmethod
    async def _probe(provider: OpenAICompatibleProvider | None) -> ProviderHealth | None:
        if provider is None:
            return None
        try:
            return await provider.health()
        except Exception as exc:  # a broken probe stays honest and sanitized
            logger.error("llm health probe failed (%s)", type(exc).__name__)
            return ProviderHealth(
                status=HealthStatus.UNAVAILABLE,
                detail="health probe failed",
                provider=PROVIDER_NAME,
            )
