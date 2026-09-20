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
from .api.assistant import router as assistant_router
from .api.attention import router as attention_router
from .api.auth_google import router as auth_google_router
from .api.briefing import router as briefing_router
from .api.calendar import router as calendar_router
from .api.decisions import router as decisions_router
from .api.events import router as events_router
from .api.focus import router as focus_router
from .api.health import router as health_router
from .api.ingress import VoiceIngressLimiter
from .api.settings import router as settings_router
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
from .llm.router import LLMRouter
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
        # B04 scheduler: one worker for the shared ingestion path (idle-gated).
        poller = getattr(app.state, "poll_scheduler", None)
        if poller is not None:
            poller.start()
        yield
        if poller is not None:
            poller.stop()

    app = FastAPI(title="EVA Core Platform", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    # A02: the auth boundary owns all Google credentials. Tests replace
    # app.state.google_auth (and optionally google_http_factory) with fakes.
    app.state.google_auth = GoogleAuth(settings)
    # B01: ONE inference router for the whole app - self-hosted primary plus an
    # optional explicitly configured self-hosted fallback; unconfigured settings
    # yield a router with no routes (fail closed, never cloud). Tests replace
    # app.state.llm_router with a hermetic double.
    app.state.llm_router = LLMRouter.from_settings(settings)

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
        # Demo mode (EVA_DATA_PROVIDER=demo): deterministic read-only synthetic
        # calendar behind the same shape. Set only in demo mode, so the real
        # path below is byte-for-byte the behavior of a normal deployment.
        demo_calendar = getattr(app.state, "demo_calendar_service", None)
        if demo_calendar is not None:
            return demo_calendar
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

    # -- B04 Attention/Focus/Decision services (exactly one of each). The
    # deterministic rules engine is the ONLY classifier floor; FocusService
    # influences delivery, never priority; Decisions are projected atomically
    # with their DECISION_REQUIRED items and executed only as guarded
    # local_write proposals through the single A04 executor below.
    from .attention.engine import AttentionEngine
    from .attention.focus import FocusService
    from .attention.rules import AttentionRules
    from .db.repositories import (
        AttentionRepository,
        CursorRepository,
        DecisionRepository,
        FocusRepository,
        OutboxRepository,
    )
    from .decisions.service import DecisionService

    app.state.cursor_repository = CursorRepository(db)
    app.state.attention_repository = AttentionRepository(db)
    app.state.decision_repository = DecisionRepository(db)
    app.state.event_outbox_repository = OutboxRepository(db)
    app.state.attention_rules = AttentionRules(
        policy=policy,
        important_senders=getattr(app.state, "important_sender_addresses", ()),
    )
    focus_service = FocusService(
        repo=FocusRepository(db),
        attention_repo=app.state.attention_repository,
        clock=utcnow,
        policy_version=policy.version,
        outbox=app.state.event_outbox_repository,
    )
    app.state.focus_service = focus_service

    # The optional LLM ambiguity classifier is a STRENGTHENER ONLY (clamped);
    # without any configured self-hosted route it answers "no opinion" and the
    # engine is purely deterministic. It reads the LIVE router, so settings
    # updates take effect without rebuilding the engine.
    from .attention.classifier import LLMAmbiguityClassifier

    #: Demo mode swaps ONLY the external Gmail boundary (see app/demo/). It is
    #: also deterministic: the optional LLM strengthener stays off so a reset
    #: always yields the same classification of the same fixtures.
    demo_mode = settings.eva_data_provider == "demo"

    attention_engine = AttentionEngine(
        rules=app.state.attention_rules,
        policy_version=policy.version,
        focus_service=focus_service,
        clock=utcnow,
        classifier=(
            None if demo_mode else LLMAmbiguityClassifier(lambda: app.state.llm_router)
        ),
    )
    app.state.attention_engine = attention_engine
    app.state.attention_sink = attention_engine

    def local_focus_start(args) -> dict:
        session = focus_service.start(args, now=utcnow())
        return {"session": session.model_dump(mode="json")}

    def local_focus_stop(args) -> dict:
        stopped, summary = focus_service.stop(now=utcnow())
        return {
            "session": stopped.model_dump(mode="json"),
            "summary": summary.model_dump(mode="json"),
        }

    def local_decision_record_outcome(args) -> dict:
        return decision_service.record_outcome(args)

    executor = ToolExecutor(
        registry=registry,
        repo=repo,
        engine=engine,
        clock=utcnow,
        calendar_service_factory=calendar_service_factory,
        granted_scopes_provider=granted_scopes_provider,
        local_handlers={
            "focus.start": local_focus_start,
            "focus.stop": local_focus_stop,
            "decision.record_outcome": local_decision_record_outcome,
        },
        read_handlers={
            "calendar.list_events": read_calendar_list,
            "calendar.get_event": read_calendar_get,
            "gmail.search": read_gmail_search,
            "gmail.get_thread": read_gmail_get_thread,
        },
    )
    decision_service = DecisionService(
        repo=app.state.decision_repository,
        action_repo=repo,
        engine=engine,
        executor=executor,
        clock=utcnow,
        outbox=app.state.event_outbox_repository,
    )
    app.state.decision_service = decision_service

    # -- B03 executive assistant + grounded briefing (one of each). Both read
    # the LIVE inference router through providers, so a runtime settings PUT
    # re-routes them without rebuilding.
    from .agent.executive_agent import ExecutiveAgent
    from .meetings.briefing import BriefingService

    app.state.calendar_service_factory = calendar_service_factory
    app.state.assistant_agent = ExecutiveAgent(
        llm_router_provider=lambda: app.state.llm_router,
        registry=registry,
        approval_engine=engine,
        action_repo=repo,
        executor=executor,
        clock=utcnow,
    )
    app.state.briefing_service = BriefingService(
        llm_router_provider=lambda: app.state.llm_router,
        calendar_service_factory=calendar_service_factory,
        attention_repo=app.state.attention_repository,
        clock=utcnow,
        outbox=app.state.event_outbox_repository,
    )

    app.state.db = db
    app.state.action_repository = repo
    app.state.loaded_policy = policy
    app.state.approval_engine = engine
    app.state.tool_registry = registry
    app.state.tool_executor = executor

    # -- A06 Gmail ingestion: the ONE real pipeline shared by the scheduler and
    # POST /api/attention/check-now (never a second path). Its sink is B04's
    # AttentionEngine; DECISION_REQUIRED items project their single linked
    # Decision atomically inside AttentionRepository.add via
    # decision_projection. IMPORTANT SENDERS: exact-address set is an
    # ORG-configuration handoff - empty here on purpose, never hardcoded.
    from .attention.ingest import GmailIngestionService
    from .demo.source import DemoGmailSource

    demo_gmail_source = DemoGmailSource() if demo_mode else None

    def _gmail_source():
        """The ONE external-data seam: real GmailService, or the deterministic
        synthetic mailbox in demo mode. Downstream code is identical."""
        if demo_gmail_source is not None:
            return demo_gmail_source
        return GmailService(_google_http())

    app.state.ingestion_service = GmailIngestionService(
        gmail_source_factory=_gmail_source,
        cursors=app.state.cursor_repository,
        attention_repo=app.state.attention_repository,
        sink=attention_engine,
        query=settings.eva_gmail_query,
        search_limit=settings.eva_gmail_search_limit,
        overlap_seconds=settings.eva_gmail_poll_overlap_seconds,
        clock=utcnow,
        decision_projection=attention_engine.decision_projection,
    )

    # -- B04 scheduler: exactly one in-process poller; it idles with zero
    # network attempts while Gmail is not authorized and never overlaps a
    # check-now run (the ingestion service's own lock decides that).
    from .scheduler.jobs import GmailPollScheduler

    def _gmail_ready() -> bool:
        try:
            return bool(app.state.google_auth.status().connected)
        except Exception:
            return False

    app.state.poll_scheduler = GmailPollScheduler(
        ingestion_service=app.state.ingestion_service,
        outbox_repo=app.state.event_outbox_repository,
        attention_repo=app.state.attention_repository,
        decision_repo=app.state.decision_repository,
        clock=utcnow,
        enabled_provider=_gmail_ready,
    )

    # -- Demo mode controls (EVA_DATA_PROVIDER=demo only). The scheduler stays
    # Google-gated on purpose: synthetic data arrives through reset/inject and
    # check-now, never through an unattended poll.
    if demo_mode:
        from .api.demo import router as demo_router
        from .demo.calendar import DemoCalendarService
        from .demo.service import DemoDataService

        # Set before any request can arrive: calendar_service_factory and the
        # calendar route both resolve through this attribute in demo mode.
        app.state.demo_calendar_service = DemoCalendarService(
            clock=utcnow, timezone_name=settings.eva_timezone
        )
        app.state.demo_service = DemoDataService(
            db=db,
            source=demo_gmail_source,
            ingestion_service=app.state.ingestion_service,
            attention_repo=app.state.attention_repository,
            decision_repo=app.state.decision_repository,
            clock=utcnow,
        )
        app.include_router(demo_router)

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
    app.include_router(attention_router)
    app.include_router(decisions_router)
    app.include_router(focus_router)
    app.include_router(events_router)
    app.include_router(settings_router)
    app.include_router(assistant_router)
    app.include_router(briefing_router)
    app.include_router(voice_router)

    # Voice upload ingress cap runs BEFORE multipart parsing (added last, so
    # it is the outermost ASGI layer): a huge request is rejected on the raw
    # body stream without Starlette ever spooling/parsing the multipart form.
    app.add_middleware(VoiceIngressLimiter)
    return app


app = create_app()
