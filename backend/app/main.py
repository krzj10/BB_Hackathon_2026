"""EVA FastAPI composition root (Stream A owns this file).

A00 registers only the health router. B modules are registered by A here on
explicit written instruction from Stream B (see workflow section 6); B never
edits main.py or dependencies.py.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import __version__
from .api.health import router as health_router
from .config import Settings
from .dependencies import get_settings

logger = logging.getLogger("eva.main")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        missing = settings.missing_required_self_hosted_settings()
        if missing:
            # Honest reporting, not a crash: health must start with external
            # services unavailable (A00). The mandatory provider route refuses
            # to serve while these are blank (enforced by the B01 adapter
            # against this frozen contract).
            logger.warning(
                "self-hosted inference not configured; missing %s", ", ".join(missing)
            )
        yield

    app = FastAPI(title="EVA Core Platform", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.include_router(health_router)
    return app


app = create_app()
