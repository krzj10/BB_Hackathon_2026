"""GET /api/health - component readiness without contacting external services.

The endpoint always returns HTTP 200 with an honest status body (liveness vs.
readiness). Overall status aggregates REQUIRED components only; optional
components (the self-hosted fallback) are exposed truthfully but their absence
must not fail mandatory runtime readiness. The demo control plane must be able
to start and answer truthfully while Google, inference and STT are unavailable.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request

from .. import __version__
from ..config import Settings
from ..contracts.api import HealthResponse
from ..contracts.domain import HealthStatus
from ..contracts.providers import ProviderHealth

router = APIRouter(prefix="/api", tags=["health"])

#: Optional components: exposed in the response but excluded from overall
#: readiness aggregation (their absence is a valid configuration).
OPTIONAL_COMPONENTS = frozenset({"llm_fallback"})


def _worst(statuses: list[HealthStatus]) -> HealthStatus:
    if HealthStatus.UNAVAILABLE in statuses:
        return HealthStatus.UNAVAILABLE
    if HealthStatus.DEGRADED in statuses:
        return HealthStatus.DEGRADED
    return HealthStatus.READY


def build_component_health(settings: Settings) -> dict[str, ProviderHealth]:
    components: dict[str, ProviderHealth] = {}

    components["database"] = ProviderHealth(
        status=HealthStatus.DEGRADED,
        detail="persistence layer not initialized yet (A01)",
    )

    if settings.self_hosted_configured:
        components["llm"] = ProviderHealth(
            status=HealthStatus.DEGRADED,
            detail="self-hosted route configured and allowlisted; provider adapter "
            "pending (B01); not probed in A00",
            provider=settings.eva_llm_provider,
            model=settings.eva_llm_model or None,
        )
    else:
        components["llm"] = ProviderHealth(
            status=HealthStatus.UNAVAILABLE,
            detail="self-hosted inference route unusable: "
            + "; ".join(settings.self_hosted_route_blockers()),
            provider=settings.eva_llm_provider,
            model=None,
        )

    # Optional by design. Reported truthfully; never aggregated into overall.
    if settings.self_hosted_fallback_configured:
        components["llm_fallback"] = ProviderHealth(
            status=HealthStatus.DEGRADED,
            detail="self-hosted fallback configured and allowlisted; not probed in A00",
            model=settings.eva_llm_fallback_model or None,
        )
    else:
        components["llm_fallback"] = ProviderHealth(
            status=HealthStatus.UNAVAILABLE,
            detail="optional self-hosted fallback not configured (allowed; "
            "never replaced by cloud)",
        )

    if settings.google_configured:
        components["google"] = ProviderHealth(
            status=HealthStatus.DEGRADED,
            detail="OAuth client configured; flow not implemented yet (A02)",
        )
    else:
        components["google"] = ProviderHealth(
            status=HealthStatus.UNAVAILABLE,
            detail="Google OAuth client id/secret not configured",
        )

    components["stt"] = ProviderHealth(
        status=HealthStatus.DEGRADED,
        detail=f"provider {settings.eva_stt_provider!r} adapter pending (A05)",
    )

    return components


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    settings: Settings = request.app.state.settings
    components = build_component_health(settings)
    required_statuses = [
        component.status
        for name, component in components.items()
        if name not in OPTIONAL_COMPONENTS
    ]
    return HealthResponse(
        status=_worst(required_statuses),
        server_time=datetime.now(timezone.utc),
        version=__version__,
        components=components,
    )
