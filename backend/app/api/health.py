"""GET /api/health - component readiness without contacting external services.

The endpoint always returns HTTP 200 with an honest status body (liveness vs.
readiness): components report ready/degraded/unavailable and the overall
status is the worst component value. The demo control plane must be able to
start and answer truthfully while Google, inference and STT are unavailable.
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
            detail="self-hosted endpoint configured; provider adapter pending (B01); "
            "not probed in A00",
        )
    else:
        components["llm"] = ProviderHealth(
            status=HealthStatus.UNAVAILABLE,
            detail="self-hosted endpoint/model not configured: missing "
            + ", ".join(settings.missing_required_self_hosted_settings()),
        )

    if settings.self_hosted_fallback_configured:
        components["llm_fallback"] = ProviderHealth(
            status=HealthStatus.DEGRADED,
            detail="fallback endpoint configured; not probed in A00",
        )
    else:
        components["llm_fallback"] = ProviderHealth(
            status=HealthStatus.UNAVAILABLE, detail="no self-hosted fallback configured"
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
    return HealthResponse(
        status=_worst([component.status for component in components.values()]),
        server_time=datetime.now(timezone.utc),
        version=__version__,
        components=components,
    )
