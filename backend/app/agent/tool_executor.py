"""The guarded ToolExecutor (A04) - the FINAL authorization boundary.

Permitted architecture (enforced by this module's structure):

    request/tool call -> canonical validation -> A03 policy / ProposedAction
    -> explicit approval if required -> durable receipt if required
    -> ToolExecutor revalidation -> atomic execution claim
    -> Calendar mutation adapter -> read-back verification -> durable result

Only this executor may invoke the mutating Calendar adapter methods. It never
trusts that an action merely "reached APPROVED": before any provider call it
re-checks registration, canonical arguments, recomputed digest, registry
effect/floor, the CURRENT authoritative policy version, expiry, required
Google scopes, and (where approval was required) the durable receipt's
existence, bindings and channel. Execution-time revalidation may raise risk
but NEVER lower it: discovered staleness supersedes the action for a fresh
proposal instead of executing under stale authorization.

Transaction discipline (absolute): every persistence call is its own short
SQLite transaction; ALL network work happens strictly BETWEEN claim and
finalize - never with a write transaction open.

Outcome honesty: SUCCEEDED requires verified read-back. A lost/ambiguous
transport outcome becomes UNKNOWN, which blocks any automatic replay
(A07 owns later reconciliation). 412 precondition failures SUPERSEDE the
action after exactly one mutation request - the user approved the OLD state.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.approvals.engine import (
    ActionApprovalEngine,
    ActionPolicyError,
    ProposalContext,
    arguments_digest,
    canonical_arguments,
)
from app.contracts.domain import (
    ActionRisk,
    AllDaySpan,
    AgendaSectionMode,
    CalendarCreateEventArguments,
    CalendarRescheduleEventArguments,
    CalendarUpdateAgendaArguments,
    Meeting,
    ProposedAction,
    ProposedActionStatus,
    SendUpdates,
    TimedSpan,
    ToolCall,
    ToolError,
    ToolEffect,
    ToolResult,
    ToolResultStatus,
)
from app.db.repositories import ActionRepository
from app.google.auth import GoogleAuthError
from app.google.calendar import (
    EVA_AGENDA_END,
    EVA_AGENDA_START,
    CalendarService,
    apply_agenda_update,
)
from app.google.http import GoogleApiError, GoogleErrorCategory

logger = logging.getLogger("eva.agent.executor")


class ExecutorError(Exception):
    """Hard executor failure with a stable machine code (no provider data)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class _SupersedeRequested(Exception):
    """Internal control flow: staleness discovered AFTER the claim (e.g. a
    412 precondition failure). Carries the honest ToolResult; execute()
    persists SUPERSEDED + closes the attempt with it."""

    def __init__(self, result: ToolResult) -> None:
        super().__init__("supersede requested")
        self.result = result


@dataclass(frozen=True)
class ExecutionOutcome:
    """Internal transport for one execute() call: the durable action state
    plus the ToolResult produced (or replayed from storage)."""

    action: ProposedAction | None
    result: ToolResult | None


_RISK_RANK = {ActionRisk.LOW: 0, ActionRisk.MEDIUM: 1, ActionRisk.HIGH: 2}

#: Categories that prove the mutation did NOT reach Google unchanged-definitive.
_DEFINITIVE_FAILURES = frozenset(
    {
        GoogleErrorCategory.BAD_REQUEST,
        GoogleErrorCategory.UNAUTHORIZED,
        GoogleErrorCategory.FORBIDDEN,
        GoogleErrorCategory.NOT_FOUND,
        GoogleErrorCategory.RATE_LIMITED,
    }
)

#: Terminal states that must never trigger another provider call.
_NO_REPLAY_STATUSES = frozenset(
    {
        ProposedActionStatus.SUCCEEDED,
        ProposedActionStatus.FAILED,
        ProposedActionStatus.UNKNOWN,
        ProposedActionStatus.SUPERSEDED,
        ProposedActionStatus.REJECTED,
        ProposedActionStatus.EXPIRED,
    }
)


def generate_google_event_id(action_id: str, revision: int) -> str:
    """Deterministic Google-valid client event id for (action, revision).

    Derived from a SHA-256 digest rendered in lowercase base32hex (charset
    [0-9a-v], URL-safe per Google's event-id rules, length 48). Stable across
    retries and process restarts; unique enough to treat collisions as real.
    Deliberately NOT ``action-<uuid>`` with hyphens."""
    digest = hashlib.sha256(f"{action_id}:{revision}".encode("utf-8")).digest()
    return base64.b32hexencode(digest).decode("ascii").lower().rstrip("=")


class ToolExecutor:
    def __init__(
        self,
        *,
        registry,
        repo: ActionRepository,
        engine: ActionApprovalEngine,
        clock: Callable[[], datetime],
        calendar_service_factory: Callable[[], CalendarService] | None = None,
        granted_scopes_provider: Callable[[], set[str]] | None = None,
        local_handlers: dict[str, Callable[[object], dict]] | None = None,
        read_handlers: dict[str, Callable[[object], ToolResult]] | None = None,
    ) -> None:
        self._registry = registry
        self._repo = repo
        self._engine = engine
        self._clock = clock
        self._calendar_factory = calendar_service_factory
        self._scopes_provider = granted_scopes_provider
        self._local_handlers = local_handlers or {}
        self._read_handlers = read_handlers or {}

    # ------------------------------------------------------------------ #
    # Read dispatch (never enters the approval lifecycle; mutation tools are
    # rejected here so a read path can never obtain write capability).
    # ------------------------------------------------------------------ #

    def execute_read(self, call: ToolCall) -> ToolResult:
        started = self._clock()
        try:
            entry = self._registry.get(call.name)
        except Exception:
            return self._error_result(call.name, "unknown_tool", "tool is not registered", started)
        if entry.effect is not ToolEffect.READ:
            return self._error_result(
                call.name, "not_a_read_tool", "mutations run through the proposal lifecycle", started
            )
        args = entry.validate(call.arguments)
        handler = self._read_handlers.get(call.name)
        if handler is None:
            return self._error_result(call.name, "service_unavailable", "read service not wired", started)
        try:
            return handler(args)
        except GoogleAuthError:
            logger.error("google read unavailable for %s (auth)", call.name)
            return self._error_result(
                call.name, "google_unavailable", "google account not connected", started
            )
        except Exception as exc:
            # Generic exception text is never logged or returned verbatim.
            logger.error("read tool %s failed (%s)", call.name, type(exc).__name__)
            return self._error_result(call.name, "read_failed", "read could not be completed", started)

    # ------------------------------------------------------------------ #
    # Guarded mutation execution
    # ------------------------------------------------------------------ #

    def execute(self, action_id: str) -> ExecutionOutcome:
        """Execute (or replay the durable outcome of) one proposed action.

        No caller may assume APPROVED means executable - everything is
        revalidated here, and the atomic claim decides races."""
        action = self._repo.get_action(action_id)
        if action is None:
            raise ExecutorError("unknown_action", "no such proposed action")

        # --- idempotent replay / terminal-state guards (zero provider calls)
        if action.status in _NO_REPLAY_STATUSES or action.status is ProposedActionStatus.EXECUTING:
            return ExecutionOutcome(action=action, result=self._repo.get_last_result(action.id))

        if action.status not in (
            ProposedActionStatus.PENDING,
            ProposedActionStatus.APPROVED,
        ):
            raise ExecutorError("invalid_status", f"cannot execute a {action.status.value} action")

        # --- pre-claim revalidation (may transition to SUPERSEDED/EXPIRED,
        #     never performs provider mutations) ----------------------------
        try:
            entry = self._registry.get(action.tool)
        except Exception:
            return self._supersede_unstarted(action, "unknown_tool", "tool is not registered")
        block = self._revalidate(action, entry)
        if block is not None:
            return block

        # --- atomic claim (one transaction; no network inside) -------------
        started_at = self._clock()
        allowed_channels = ("ui",) if action.risk is ActionRisk.HIGH else ("ui", "voice")
        if action.requires_approval:
            attempt_id = self._repo.claim_approved_execution(
                action.id,
                expected_revision=action.revision,
                expected_arguments_digest=action.arguments_digest,
                expected_policy_version=self._engine.policy_version,
                allowed_channels=allowed_channels,
                started_at=started_at,
            )
        else:
            attempt_id = self._repo.claim_no_approval_execution(
                action.id,
                expected_revision=action.revision,
                expected_arguments_digest=action.arguments_digest,
                expected_policy_version=self._engine.policy_version,
                started_at=started_at,
            )
        if attempt_id is None:
            # Lost the race, authorization vanished (receipt missing/expired,
            # policy changed, status moved): return current durable state.
            current = self._repo.get_action(action.id)
            return ExecutionOutcome(
                action=current, result=self._repo.get_last_result(action.id) if current else None
            )

        # --- network phase: strictly between claim and finalize -----------
        try:
            result = self._perform(action, entry)
        except _SupersedeRequested as supersede:
            finished_at = self._clock()
            self._repo.supersede_executing_action(
                action.id,
                result=supersede.result,
                attempt_id=attempt_id,
                finished_at=finished_at,
                detail=supersede.result.error.code if supersede.result.error else "superseded",
            )
            final = self._repo.get_action(action.id)
            return ExecutionOutcome(action=final, result=supersede.result)

        # --- finalize (one transaction; no network inside) -----------------
        finished_at = self._clock()
        new_status = {
            ToolResultStatus.OK: ProposedActionStatus.SUCCEEDED,
            ToolResultStatus.ERROR: ProposedActionStatus.FAILED,
            ToolResultStatus.UNKNOWN: ProposedActionStatus.UNKNOWN,
            # PARTIAL is not produced for mutations; treat as error honestly.
        }.get(result.status, ProposedActionStatus.FAILED)
        outcome_word = {
            ProposedActionStatus.SUCCEEDED: "succeeded",
            ProposedActionStatus.FAILED: "failed",
            ProposedActionStatus.UNKNOWN: "unknown",
        }[new_status]
        detail = result.error.code if result.error else None
        self._repo.finish_action_execution(
            action.id,
            new_status=new_status,
            result=result,
            attempt_id=attempt_id,
            outcome=outcome_word,
            finished_at=finished_at,
            detail=detail,
        )
        final = self._repo.get_action(action.id)
        return ExecutionOutcome(action=final, result=result)

    # ------------------------------------------------------------------ #
    # Revalidation helpers
    # ------------------------------------------------------------------ #

    def _revalidate(self, action: ProposedAction, entry) -> ExecutionOutcome | None:
        """Return an ExecutionOutcome to STOP execution (stale/unsafe), or
        None when the action may proceed to claim."""
        try:
            args = self._engine.validate_arguments(action.tool, action.arguments)
        except ActionPolicyError:
            return self._supersede_unstarted(
                action, "invalid_arguments", "stored arguments no longer validate canonically"
            )
        recomputed = arguments_digest(canonical_arguments(args))
        if recomputed != action.arguments_digest:
            return self._supersede_unstarted(
                action, "arguments_digest_mismatch", "stored arguments no longer match their digest"
            )
        if action.policy_version != self._engine.policy_version:
            # PART XXI: policy changed -> old proposal cannot execute; zero
            # Google calls; a fresh proposal is required.
            return self._supersede_unstarted(
                action, "policy_changed", "authoritative policy changed since proposal"
            )
        now = self._clock()
        if now >= action.expires_at:
            self._repo.expire_action(action.id, now=now)
            current = self._repo.get_action(action.id)
            return ExecutionOutcome(
                action=current,
                result=self._error_result(
                    action.tool, "expired", "proposal expired; a fresh proposal is required",
                    action.created_at,
                ),
            )
        if _RISK_RANK[entry.risk_floor] > _RISK_RANK[action.risk]:
            return self._supersede_unstarted(
                action, "risk_floor_increased", "current policy floor exceeds the proposed risk"
            )

        if entry.effect is ToolEffect.EXTERNAL_WRITE:
            block = self._revalidate_calendar_write(action, entry)
            if block is not None:
                return block
        return None

    def _revalidate_calendar_write(self, action: ProposedAction, entry) -> ExecutionOutcome | None:
        """Scope, target-state, recurrence, ETag and risk-escalation checks
        for calendar mutations - all read-only against Google."""
        scopes = self._scopes_provider() if self._scopes_provider else set()
        required = set(entry.required_scopes)
        if not required.issubset(scopes):
            # No mutation, no blind recovery: the action keeps its approved
            # state so execution can succeed after reconnection.
            return ExecutionOutcome(
                action=action,
                result=self._error_result(
                    action.tool, "google_scope_missing",
                    "calendar.events.owned scope is not granted; reconnect the account",
                    action.created_at, retryable=True,
                ),
            )
        service = self._calendar()
        if service is None:
            return ExecutionOutcome(
                action=action,
                result=self._error_result(
                    action.tool, "google_unavailable",
                    "google account not connected", action.created_at, retryable=True,
                ),
            )

        args = self._engine.validate_arguments(action.tool, action.arguments)
        if isinstance(args, CalendarCreateEventArguments):
            current = None
        else:
            ref = args.ref  # reschedule / update_agenda
            try:
                state = service.get_event_mutation_state(
                    calendar_id=ref.calendar_id, event_id=ref.event_id
                )
            except GoogleApiError as exc:
                code = (
                    "target_not_found"
                    if exc.category is GoogleErrorCategory.NOT_FOUND
                    else "target_unreadable"
                )
                return ExecutionOutcome(
                    action=action,
                    result=self._error_result(action.tool, code, "mutation target cannot be read", action.created_at),
                )
            except Exception as exc:
                logger.error("target read failed (%s)", type(exc).__name__)
                return ExecutionOutcome(
                    action=action,
                    result=self._error_result(action.tool, "target_unreadable", "mutation target cannot be read", action.created_at),
                )
            current = state.meeting
            # Recurring-series ambiguity is blocked for demo mutations - both
            # instances (recurringEventId) and series masters (recurrence[]).
            if state.is_recurring:
                return self._supersede_unstarted(
                    action, "recurring_series_ambiguous",
                    "recurring events need a specific-instance proposal",
                )
            # ETag bindings: the approved snapshot must still be current.
            if current.etag is None:
                return self._supersede_unstarted(
                    action, "resource_version_missing", "target has no ETag to authorize against"
                )
            expected = getattr(args, "expected_etag", None)
            if action.resource_version is not None and current.etag != action.resource_version:
                return self._supersede_unstarted(
                    action, "stale_resource_version",
                    "target changed since the proposal; a fresh proposal is required",
                )
            if expected is not None and current.etag != expected:
                return self._supersede_unstarted(
                    action, "stale_resource_version",
                    "target ETag no longer matches the approved expectation",
                )
            # Cross-kind reschedule (all-day <-> timed) is unsupported.
            if isinstance(args, CalendarRescheduleEventArguments):
                same_kind = type(current.span) is type(args.new_span)
                if not same_kind:
                    return self._supersede_unstarted(
                        action, "cross_kind_reschedule_unsupported",
                        "all-day and timed events are never silently converted",
                    )

        # Risk may only go UP at execution time; any increase supersedes.
        meetings = {}
        if current is not None:
            meetings[(current.ref.calendar_id, current.ref.event_id)] = current
        decision = self._engine.evaluate(
            ToolCall(id=f"recheck-{action.id}", name=action.tool, arguments=action.arguments),
            ProposalContext(
                session_id=action.session_id, request_id=action.request_id, now=self._clock(),
                meetings=meetings,
            ),
        )
        if _RISK_RANK[decision.risk] > _RISK_RANK[action.risk]:
            return self._supersede_unstarted(
                action, "risk_increased", "current evaluation is riskier than the approved proposal"
            )
        if decision.requires_approval and not action.requires_approval:
            return self._supersede_unstarted(
                action, "approval_now_required", "current policy requires approval for this action"
            )
        return None

    # ------------------------------------------------------------------ #
    # Network phase
    # ------------------------------------------------------------------ #

    def _perform(self, action: ProposedAction, entry) -> ToolResult:
        started = self._clock()
        args = self._engine.validate_arguments(action.tool, action.arguments)

        if entry.effect is ToolEffect.LOCAL_WRITE:
            handler = self._local_handlers.get(action.tool)
            if handler is None:
                # Fail closed rather than inventing B04 behavior here.
                return self._error_result(
                    action.tool, "handler_unavailable",
                    "no local handler is wired for this tool", started,
                )
            try:
                data = handler(args)
            except Exception as exc:
                logger.error("local handler failed (%s)", type(exc).__name__)
                return self._error_result(action.tool, "handler_failed", "local handler failed", started)
            return self._ok_result(action.tool, dict(data or {}), started, action.id)

        # EXTERNAL_WRITE: calendar mutations.
        service = self._calendar()
        if service is None:  # vanished mid-flight; nothing was sent
            return self._error_result(
                action.tool, "google_unavailable", "google account not connected", started, retryable=True
            )
        try:
            if isinstance(args, CalendarCreateEventArguments):
                return self._perform_create(action, args, service, started)
            if isinstance(args, CalendarRescheduleEventArguments):
                return self._perform_reschedule(action, args, service, started)
            if isinstance(args, CalendarUpdateAgendaArguments):
                return self._perform_agenda(action, args, service, started)
        except _SupersedeRequested:
            raise  # control flow belongs to execute(), never an "unknown" outcome
        except GoogleApiError as exc:
            # A provider error escaping the sub-operations is classified by
            # category; bodies are never inspected or logged.
            logger.error("calendar mutation failed (%s)", exc.category.value)
            return self._classify_mutation_error(action, exc, service, started)
        except Exception as exc:
            # Unknown transport behavior: outcome is genuinely unknown.
            logger.error("calendar mutation raised unexpectedly (%s)", type(exc).__name__)
            return self._unknown_result(action.tool, "mutation transport failed; outcome unverified", started, action.id)
        return self._error_result(action.tool, "unsupported_tool", "unhandled mutation tool", started)

    def _perform_create(self, action, args: CalendarCreateEventArguments, service, started) -> ToolResult:
        # Reserve the client event id durably BEFORE the first request.
        candidate = generate_google_event_id(action.id, action.revision)
        google_event_id = self._repo.get_or_reserve_google_event_id(
            action.id, action.revision, candidate, now=self._clock()
        )
        # Owned-calendar conflict detection (no attendee free/busy claims).
        conflict = self._owned_conflict(service, args)
        if conflict:
            return self._error_result(
                action.tool, "owned_calendar_conflict",
                "an owned event overlaps the proposed time; choose a different time in a new proposal",
                started,
            )
        try:
            meeting = service.create_event(args=args, google_event_id=google_event_id)
        except GoogleApiError as exc:
            if exc.category is GoogleErrorCategory.CONFLICT:
                return self._reconcile_create(action, args, service, google_event_id, started)
            if exc.category not in _DEFINITIVE_FAILURES:
                # Transport / 5xx / malformed response after a POST: the
                # request MAY have been applied server-side. Reconcile against
                # the durably reserved client event id - NEVER a blind retry.
                return self._reconcile_create_timeout(
                    action, args, service, google_event_id, started
                )
            raise
        if _matches_approved_create(meeting, args):
            return self._ok_result(
                action.tool,
                {
                    "calendar_id": meeting.ref.calendar_id,
                    "event_id": meeting.ref.event_id,
                    "etag": meeting.etag,
                    "verified": True,
                },
                started, action.id,
            )
        return self._unknown_result(
            action.tool, "create read-back does not confirm the approved event", started, action.id
        )

    def _perform_reschedule(self, action, args: CalendarRescheduleEventArguments, service, started) -> ToolResult:
        if_match = args.expected_etag or action.resource_version
        try:
            meeting = service.reschedule_event(args=args, if_match=if_match, calendar_id=args.ref.calendar_id)
        except GoogleApiError as exc:
            if exc.category is GoogleErrorCategory.PRECONDITION_FAILED:
                return self._supersede_executing(
                    action, "precondition_failed",
                    "the event changed after approval; a fresh proposal is required", started,
                )
            reconciled = self._try_reconcile_readback(
                lambda: service.get_event(calendar_id=args.ref.calendar_id, event_id=args.ref.event_id),
                lambda m: _matches_reschedule(m, args.new_span),
            )
            if reconciled is True:
                return self._ok_result(
                    action.tool,
                    {"calendar_id": args.ref.calendar_id, "event_id": args.ref.event_id, "verified": True},
                    started, action.id,
                )
            raise  # classified by the caller (definitive vs unknown)
        if _matches_reschedule(meeting, args.new_span):
            return self._ok_result(
                action.tool,
                {
                    "calendar_id": meeting.ref.calendar_id,
                    "event_id": meeting.ref.event_id,
                    "etag": meeting.etag,
                    "verified": True,
                },
                started, action.id,
            )
        return self._unknown_result(
            action.tool, "reschedule read-back does not confirm the approved span", started, action.id
        )

    def _perform_agenda(self, action, args: CalendarUpdateAgendaArguments, service, started) -> ToolResult:
        state = service.get_event_mutation_state(
            calendar_id=args.ref.calendar_id, event_id=args.ref.event_id
        )
        current = state.meeting
        if state.is_recurring:
            return self._supersede_executing(
                action, "recurring_series_ambiguous",
                "recurring events need a specific-instance proposal", started,
            )
        # ADD over an existing EVA section is a deterministic conflict, not a
        # silent duplication or a misleading success: fail closed, zero PATCH.
        if args.mode == AgendaSectionMode.ADD and _has_agenda_section(current.description):
            return self._error_result(
                action.tool, "agenda_section_exists",
                "the event already has an EVA agenda section; use UPDATE in a new proposal",
                started,
            )
        if_match = args.expected_etag or action.resource_version or current.etag
        if if_match is None:
            return self._supersede_executing(
                action, "resource_version_missing", "target has no ETag to authorize against", started
            )
        new_description = apply_agenda_update(current.description, args.agenda_markdown, args.mode)
        try:
            meeting = service.update_agenda_event(
                calendar_id=args.ref.calendar_id,
                event_id=args.ref.event_id,
                new_description=new_description,
                send_updates=args.send_updates,
                if_match=if_match,
            )
        except GoogleApiError as exc:
            if exc.category is GoogleErrorCategory.PRECONDITION_FAILED:
                return self._supersede_executing(
                    action, "precondition_failed",
                    "the event changed after approval; a fresh proposal is required", started,
                )
            reconciled = self._try_reconcile_readback(
                lambda: service.get_event(calendar_id=args.ref.calendar_id, event_id=args.ref.event_id),
                lambda m: _agenda_section_matches(m.description, args.agenda_markdown),
            )
            if reconciled is True:
                return self._ok_result(
                    action.tool,
                    {"calendar_id": args.ref.calendar_id, "event_id": args.ref.event_id, "verified": True},
                    started, action.id,
                )
            raise
        if _agenda_section_matches(meeting.description, args.agenda_markdown):
            return self._ok_result(
                action.tool,
                {
                    "calendar_id": meeting.ref.calendar_id,
                    "event_id": meeting.ref.event_id,
                    "etag": meeting.etag,
                    "verified": True,
                },
                started, action.id,
            )
        return self._unknown_result(
            action.tool, "agenda read-back does not confirm the approved section", started, action.id
        )

    def _reconcile_create(self, action, args, service, google_event_id: str, started) -> ToolResult:
        """409 on create: GET the exact persisted client id. An event that
        matches the approved semantics is reconciled success; anything else
        fails closed - never generate a new id and create again."""
        try:
            existing = service.get_event(calendar_id="primary", event_id=google_event_id)
        except Exception:
            return self._unknown_result(
                action.tool, "create conflict could not be reconciled", started, action.id
            )
        # The lookup key IS the anchor; a payload under another id is not ours.
        if existing.ref.event_id != google_event_id:
            return self._error_result(
                action.tool, "create_conflict_mismatch",
                "an unrelated event already uses this id; no automatic retry is performed", started,
            )
        if _matches_approved_create(existing, args):
            return self._ok_result(
                action.tool,
                {
                    "calendar_id": existing.ref.calendar_id,
                    "event_id": existing.ref.event_id,
                    "etag": existing.etag,
                    "verified": True,
                    "reconciled": True,
                },
                started, action.id,
            )
        return self._error_result(
            action.tool, "create_conflict_mismatch",
            "an unrelated event already uses this id; no automatic retry is performed", started,
        )

    def _reconcile_create_timeout(self, action, args, service, google_event_id: str, started) -> ToolResult:
        """Ambiguous create outcome (lost response/5xx): GET the durably
        reserved client id. Verified match -> SUCCEEDED; anything else stays
        UNKNOWN for later reconciliation - never a blind POST retry."""
        try:
            existing = service.get_event(calendar_id="primary", event_id=google_event_id)
        except Exception as exc:
            logger.error("create reconciliation read failed (%s)", type(exc).__name__)
            return self._unknown_result(
                action.tool, "create outcome unverified; reconciliation read unavailable",
                started, action.id,
            )
        if _matches_approved_create(existing, args):
            return self._ok_result(
                action.tool,
                {
                    "calendar_id": existing.ref.calendar_id,
                    "event_id": existing.ref.event_id,
                    "etag": existing.etag,
                    "verified": True,
                    "reconciled": True,
                },
                started, action.id,
            )
        return self._unknown_result(
            action.tool, "create reconciliation read does not confirm the approved event",
            started, action.id,
        )

    def _classify_mutation_error(self, action, exc: GoogleApiError, service, started) -> ToolResult:
        if exc.category in _DEFINITIVE_FAILURES:
            return self._error_result(
                action.tool, f"google_{exc.category.value}",
                "the mutation was rejected by the provider", started,
                retryable=exc.category is GoogleErrorCategory.RATE_LIMITED,
            )
        # TRANSPORT / SERVER_ERROR / MALFORMED after a mutation: ambiguous.
        return self._unknown_result(
            action.tool, "mutation outcome could not be verified; reconciliation required",
            started, action.id,
        )

    def _try_reconcile_readback(self, fetch: Callable[[], Meeting], matches: Callable[[Meeting], bool]):
        """Best-effort post-failure read-back: True (conclusively applied),
        False (definitely not), None (cannot tell)."""
        try:
            return bool(matches(fetch()))
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # Transitions and result builders
    # ------------------------------------------------------------------ #

    def _supersede_unstarted(self, action, code: str, message: str) -> ExecutionOutcome:
        self._repo.supersede_unstarted_action(action.id)
        current = self._repo.get_action(action.id)
        return ExecutionOutcome(
            action=current,
            result=self._error_result(action.tool, code, message, action.created_at),
        )

    def _supersede_executing(self, action, code: str, message: str, started) -> ToolResult:
        """Staleness discovered after the claim (412, late recurrence/ETag
        discovery): raise control flow so execute() persists SUPERSEDED and
        closes the attempt with this honest result. Never a silent retry."""
        raise _SupersedeRequested(self._error_result(action.tool, code, message, started))

    def _calendar(self) -> CalendarService | None:
        if self._calendar_factory is None:
            return None
        try:
            return self._calendar_factory()
        except GoogleAuthError:
            logger.error("google session unavailable (auth)")
            return None
        except Exception as exc:
            logger.error("google session preparation failed (%s)", type(exc).__name__)
            return None

    def _owned_conflict(self, service: CalendarService, args: CalendarCreateEventArguments) -> bool:
        try:
            time_min, time_max = _span_bounds_utc(args.span)
            meetings, _status, _notes = service.list_events(
                calendar_id="primary", time_min=time_min, time_max=time_max, max_results=50
            )
        except Exception as exc:
            logger.error("conflict check failed (%s)", type(exc).__name__)
            # Cannot prove the slot is free -> fail closed with a conflict.
            return True
        return len(meetings) > 0

    def _ok_result(self, tool: str, data: dict, started: datetime, action_id: str | None) -> ToolResult:
        return ToolResult(
            call_id=f"exec-{tool}", tool=tool, status=ToolResultStatus.OK, data=data,
            action_id=action_id, duration_ms=_ms(self._clock() - started),
        )

    def _unknown_result(self, tool: str, message: str, started: datetime, action_id: str | None) -> ToolResult:
        return ToolResult(
            call_id=f"exec-{tool}", tool=tool, status=ToolResultStatus.UNKNOWN,
            error=ToolError(code="outcome_unknown", message=message, retryable=False),
            action_id=action_id, duration_ms=_ms(self._clock() - started),
        )

    def _error_result(
        self, tool: str, code: str, message: str, started: datetime, *, retryable: bool = False
    ) -> ToolResult:
        return ToolResult(
            call_id=f"exec-{tool}", tool=tool, status=ToolResultStatus.ERROR,
            error=ToolError(code=code, message=message, retryable=retryable),
            duration_ms=_ms(self._clock() - started),
        )


# --------------------------------------------------------------------------- #
# Pure verification helpers
# --------------------------------------------------------------------------- #


def _ms(delta: timedelta) -> int:
    return max(0, int(delta.total_seconds() * 1000))


def _span_bounds_utc(span: TimedSpan | AllDaySpan) -> tuple[datetime, datetime]:
    if isinstance(span, TimedSpan):
        return span.start.astimezone(timezone.utc), span.end.astimezone(timezone.utc)
    start = datetime.combine(span.start_date, datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(span.end_exclusive, datetime.min.time(), tzinfo=timezone.utc)
    return start, end


def _span_matches(current: TimedSpan | AllDaySpan, approved: TimedSpan | AllDaySpan) -> bool:
    """Compare span INSTANTS/dates (provider may normalize timezone labels)."""
    if isinstance(approved, TimedSpan) != isinstance(current, TimedSpan):
        return False
    if isinstance(approved, TimedSpan):
        assert isinstance(current, TimedSpan)
        return (
            approved.start.astimezone(timezone.utc) == current.start.astimezone(timezone.utc)
            and approved.end.astimezone(timezone.utc) == current.end.astimezone(timezone.utc)
        )
    assert isinstance(approved, AllDaySpan) and isinstance(current, AllDaySpan)
    return (
        approved.start_date == current.start_date
        and approved.end_exclusive == current.end_exclusive
    )


def _matches_approved_create(meeting: Meeting, args: CalendarCreateEventArguments) -> bool:
    """Verify EVERY observable approved create semantic, not just title/time.

    An event sharing only the title and span is NOT proof of our insert:
    description, location and the canonical attendee email identity set must
    all agree. sendUpdates is deliberately NOT compared here - it governs
    notification delivery and is not part of the returned event-resource
    state; its exactness is enforced on the outgoing request instead (see the
    mapping tests). Reconciliation can only verify observable state."""
    if meeting.title != args.title:
        return False
    if not _span_matches(meeting.span, args.span):
        return False
    if _norm_text(meeting.description) != _norm_text(args.description):
        return False
    if _norm_text(meeting.location) != _norm_text(args.location):
        return False
    if _attendee_emails(meeting.attendees) != _attendee_emails(args.attendees):
        return False
    return True


def _norm_text(value: str | None) -> str:
    """Absence-normalized text: approved None matches provider-absent/empty,
    non-null values must agree after surrounding-whitespace trim."""
    return (value or "").strip()


def _attendee_emails(attendees) -> list[str]:
    """Canonical attendee identities: case-normalized email SET. Display
    names are not compared because providers may normalize them away; a
    different email set is always a mismatch."""
    return sorted({a.email.strip().lower() for a in attendees or []})


def _matches_reschedule(meeting: Meeting, new_span: TimedSpan | AllDaySpan) -> bool:
    return _span_matches(meeting.span, new_span)


def _has_agenda_section(description: str | None) -> bool:
    return description is not None and EVA_AGENDA_START in description


def _agenda_section_matches(description: str | None, markdown: str) -> bool:
    if description is None:
        return False
    begin = description.find(EVA_AGENDA_START)
    end = description.find(EVA_AGENDA_END)
    if begin == -1 or end == -1 or end < begin:
        return False
    inner = description[begin + len(EVA_AGENDA_START):end].strip("\n")
    return inner.strip() == markdown.strip()
