"""EVA FastAPI composition root (Stream A owns this file).

A04 central wiring: one database, one repository set, one policy load, one
approval engine, one tool registry and one guarded executor per application.
Routes consume these from app.state - tests replace app.state pieces
(google_auth, google_http_factory, tool_executor, clock-backed services) with
hermetic fakes before issuing requests. B modules are registered by A here on
explicit written instruction from Stream B (see workflow section 6)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .agent.tool_executor import ToolExecutor
from .agent.tool_registry import build_default_registry
from .api.actions import router as actions_router
from .api.auth_google import router as auth_google_router
from .api.calendar import router as calendar_router
from .api.health import router as health_router
from .approvals.engine import ActionApprovalEngine
from .approvals.policy import load_policy
from .config import Settings
from .contracts.domain import ToolResult, ToolResultStatus
from .db.repositories import ActionRepository
from .db.schema import init_schema
from .db.session import Database
from .dependencies import get_settings
from .google.auth import GoogleAuth
from .google.calendar import CalendarService
from .google.http import AuthorizedGoogleHttp

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
    # A02: the auth boundary owns all Google credentials. Tests replace
    # app.state.google_auth (and optionally google_http_factory) with fakes.
    app.state.google_auth = GoogleAuth(settings)

    # A01/A03/A04 core state - exactly one of each, shared by every route.
    db = Database(settings.eva_db_url)
    init_schema(db)
    repo = ActionRepository(db)
    policy = load_policy()
    engine = ActionApprovalEngine(policy, repo)
    registry = build_default_registry(policy)

    def utcnow() -> datetime:
        return datetime.now(timezone.utc)

    # Injectable clock for tests (routes and executor share one source).
    app.state.clock = utcnow

    def calendar_service_factory() -> CalendarService:
        """Builds a CalendarService over the currently authorized session.
        Raises GoogleAuthError (sanitized) when disconnected; the guarded
        executor turns that into a no-mutation safe state."""
        auth: GoogleAuth = app.state.google_auth
        session = auth.authorized_session()
        factory = getattr(app.state, "google_http_factory", None)
        http = factory(session) if factory is not None else AuthorizedGoogleHttp(session)
        return CalendarService(http, timezone_name=settings.eva_timezone)

    def granted_scopes_provider() -> set[str]:
        try:
            status = app.state.google_auth.status()
        except Exception:
            return set()
        return set(status.granted_scopes) if status.connected else set()

    # -- read-tool dispatch over the reviewed A02 services (registry reads
    #    never enter the approval lifecycle and gain no mutation capability).
    def _read_result(tool: str, data: dict) -> ToolResult:
        return ToolResult(
            call_id=f"read-{tool}", tool=tool, status=ToolResultStatus.OK,
            data=data, duration_ms=0,
        )

    def read_calendar_list(args) -> ToolResult:
        time_min = datetime.fromisoformat(args.time_min) if args.time_min else None
        time_max = datetime.fromisoformat(args.time_max) if args.time_max else None
        meetings, status, notes = calendar_service_factory().list_events(
            calendar_id=args.calendar_id, time_min=time_min, time_max=time_max,
            max_results=args.max_results,
        )
        return _read_result("calendar.list_events", {
            "meetings": [m.model_dump(mode="json") for m in meetings],
            "retrieval_status": status.value,
            "retrieval_notes": notes,
        })

    def read_calendar_get(args) -> ToolResult:
        meeting = calendar_service_factory().get_event(
            calendar_id=args.calendar_id, event_id=args.event_id
        )
        return _read_result("calendar.get_event", {"meeting": meeting.model_dump(mode="json")})

    def read_gmail_search(args) -> ToolResult:
        from .google.gmail import GmailService

        summaries, status, notes = GmailService(_google_http()).search(
            args.query, limit=args.max_results
        )
        return _read_result("gmail.search", {
            "threads": [
                {"thread_id": s.thread_id, "message_ids": list(s.message_ids), "subject": s.subject}
                for s in summaries
            ],
            "retrieval_status": status.value,
            "retrieval_notes": notes,
        })

    def read_gmail_get_thread(args) -> ToolResult:
        from .google.gmail import GmailService

        evidence = GmailService(_google_http()).get_thread(args.thread_id)
        return _read_result("gmail.get_thread", {
            "thread_id": evidence.thread_id,
            "retrieval_status": evidence.retrieval_status.value,
            "messages": [m.model_dump(mode="json") for m in evidence.messages],
            "retrieval_notes": evidence.notes,
        })

    def _google_http():
        auth: GoogleAuth = app.state.google_auth
        session = auth.authorized_session()
        factory = getattr(app.state, "google_http_factory", None)
        return factory(session) if factory is not None else AuthorizedGoogleHttp(session)

    executor = ToolExecutor(
        registry=registry,
        repo=repo,
        engine=engine,
        clock=utcnow,
        calendar_service_factory=calendar_service_factory,
        granted_scopes_provider=granted_scopes_provider,
        read_handlers={
            "calendar.list_events": read_calendar_list,
            "calendar.get_event": read_calendar_get,
            "gmail.search": read_gmail_search,
            "gmail.get_thread": read_gmail_get_thread,
        },
    )

    app.state.db = db
    app.state.action_repository = repo
    app.state.loaded_policy = policy
    app.state.approval_engine = engine
    app.state.tool_registry = registry
    app.state.tool_executor = executor

    # Browser mutation protection (see api/security.py threat model): CORS is
    # limited to the exact configured app origins; no credentials/cookies.
    if settings.eva_app_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.eva_app_allowed_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-EVA-Session-ID", "X-EVA-Request-ID"],
        )

    app.include_router(health_router)
    app.include_router(auth_google_router)
    app.include_router(calendar_router)
    app.include_router(actions_router)
    return app


app = create_app()
