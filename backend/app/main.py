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
from .api.ingress import VoiceIngressLimiter
from .api.voice import router as voice_router
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
from .google.gmail import GmailService
from .google.http import AuthorizedGoogleHttp
from .voice.audio import FfmpegAudioNormalizer
from .voice.faster_whisper import FasterWhisperProvider
from .voice.stt_base import TranscriptionService, canonicalize_stt_provider_name
from .voice.whisper_cpp import WhisperCppProvider

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
        # A05 best-effort STT warmup: failures are recorded as sanitized
        # provider-unavailable state and NEVER crash unrelated startup.
        service = getattr(app.state, "stt_service", None)
        if service is not None:
            await service.warmup()
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

    # -- A06 Gmail ingestion + deterministic Attention rules. The Attention
    # SINK is B04's AttentionEngine (frozen ingest(source_event) -> AttentionItem):
    # until B04 wires it, app.state.attention_sink stays None and ingestion
    # honestly leaves new sources unseen instead of faking classification.
    # IMPORTANT SENDERS: exact-address set is a B04/ORG configuration handoff;
    # empty here on purpose - no real addresses are hardcoded in source.
    from .attention.ingest import GmailIngestionService
    from .attention.rules import AttentionRules
    from .db.repositories import AttentionRepository, CursorRepository

    app.state.cursor_repository = CursorRepository(db)
    app.state.attention_repository = AttentionRepository(db)
    app.state.attention_rules = AttentionRules(
        policy=policy,
        important_senders=getattr(app.state, "important_sender_addresses", ()),
    )
    app.state.attention_sink = None  # B04 injects its AttentionEngine here
    app.state.ingestion_service = GmailIngestionService(
        gmail_source_factory=lambda: GmailService(_google_http()),
        cursors=app.state.cursor_repository,
        attention_repo=app.state.attention_repository,
        sink=None,  # replaced at B04 wiring alongside app.state.attention_sink
        query=settings.eva_gmail_query,
        search_limit=settings.eva_gmail_search_limit,
        overlap_seconds=settings.eva_gmail_poll_overlap_seconds,
        clock=utcnow,
    )

    # -- A05 voice pipeline: one normalizer + one STT service per application.
    # Tests replace app.state.audio_normalizer / stt_service (and the inject
    # hooks below) with hermetic fakes - no real ffmpeg, binaries or models.
    primary_name = canonicalize_stt_provider_name(settings.eva_stt_provider)
    whisper_provider = FasterWhisperProvider(
        model_name=settings.eva_whisper_model,
        device=settings.eva_whisper_device,
        compute_type=settings.eva_whisper_compute_type,
        model_factory=getattr(app.state, "whisper_model_factory", None),
    )
    whisper_cpp = WhisperCppProvider(
        binary=settings.eva_whisper_cpp_binary or None,
        model_path=settings.eva_whisper_cpp_model_path or None,
        timeout_seconds=settings.eva_stt_timeout_seconds,
        runner=getattr(app.state, "whisper_cpp_runner", None),
    )
    if primary_name == "faster-whisper":
        stt_primary, stt_fallback = whisper_provider, whisper_cpp
    else:  # canonicalized to 'whisper.cpp'
        stt_primary, stt_fallback = whisper_cpp, whisper_provider

    app.state.audio_normalizer = FfmpegAudioNormalizer(
        ffmpeg_binary=settings.eva_ffmpeg_binary
    )
    app.state.stt_service = TranscriptionService(
        primary=stt_primary,
        fallback=stt_fallback,
        max_concurrency=settings.eva_stt_max_concurrency,
        timeout_seconds=settings.eva_stt_timeout_seconds,
    )

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
    app.include_router(voice_router)

    # Voice upload ingress cap runs BEFORE multipart parsing (added last, so
    # it is the outermost ASGI layer): a huge request is rejected on the raw
    # body stream without Starlette ever spooling/parsing the multipart form.
    app.add_middleware(VoiceIngressLimiter)
    return app


app = create_app()
