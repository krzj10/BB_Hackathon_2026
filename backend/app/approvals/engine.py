"""ActionApprovalEngine (A03): deterministic risk, proposals, challenges and
approval confirmation.

Authority boundary (plan v2.0 section 5): the LLM may propose a ToolCall and
suggest reason TEXT; everything authoritative - risk, requires_approval,
voice_approval_allowed, policy_version, expiry, canonical arguments, digest,
impact and receipts - is derived here from the validated tool contract plus
the loaded POLICY. A ToolCall cannot carry these fields (contract extra=forbid)
and injected keys inside ``arguments`` fail canonical validation rather than
downgrading policy.

A03 never calls a mutating adapter: it evaluates and persists authorization
state only. Execution belongs to A04's guarded executor.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pydantic import BaseModel, ValidationError

from app.approvals.policy import LoadedPolicy
from app.contracts.domain import (
    ActionRisk,
    ApprovalChannel,
    ApprovalChoice,
    ApprovalReceipt,
    ApprovalRequest,
    CalendarCreateEventArguments,
    CalendarProposalArguments,  # noqa: F401  (canonical union re-export)
    CalendarRescheduleEventArguments,
    CalendarUpdateAgendaArguments,
    Decision,
    DecisionRecordOutcomeArguments,
    FocusStartArguments,
    FocusStopArguments,
    Meeting,
    MeetingPriority,
    ProposedAction,
    ProposedActionStatus,
    Reason,
    ReasonOrigin,
    SendUpdates,
    ToolCall,
)
from app.db.repositories import ActionRepository
from app.meetings.triage import MeetingTriage, TriageEvidence

#: Known mutation/command tools with their frozen canonical argument models.
#: Unknown tools fail closed. No payment/purchase/contract/legal tools exist.
_KNOWN_ARG_MODELS: dict[str, type[BaseModel]] = {
    "calendar.create_event": CalendarCreateEventArguments,
    "calendar.reschedule_event": CalendarRescheduleEventArguments,
    "calendar.update_agenda": CalendarUpdateAgendaArguments,
    "decision.record_outcome": DecisionRecordOutcomeArguments,
    "focus.start": FocusStartArguments,
    "focus.stop": FocusStopArguments,
}

_CALENDAR_TOOLS = frozenset(
    {"calendar.create_event", "calendar.reschedule_event", "calendar.update_agenda"}
)


class ActionPolicyError(Exception):
    """Sanitized policy/confirmation failure with a stable machine code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ProposalContext:
    """Server-supplied current-state context. The engine performs NO fetching;
    A04 owns retrieving meetings/decisions (and re-fetching at execution)."""

    session_id: str
    request_id: str
    now: datetime
    revision: int = 1
    #: currently-known meetings keyed by (calendar_id, event_id)
    meetings: dict[tuple[str, str], Meeting] = field(default_factory=dict)
    decision: Decision | None = None
    #: LLM may suggest reason TEXT only; never risk/approval semantics.
    suggested_reason: str | None = None
    triage_evidence: TriageEvidence | None = None


@dataclass(frozen=True)
class ConfirmationResult:
    action: ProposedAction
    receipt: ApprovalReceipt | None
    already_approved: bool = False


@dataclass(frozen=True)
class PolicyDecision:
    """Result of the frozen evaluate() boundary: deterministic policy output,
    including the structured rule Reasons behind it."""

    arguments: BaseModel
    risk: ActionRisk
    requires_approval: bool
    voice_approval_allowed: bool
    reasons: list[Reason]
    target_meeting: Meeting | None = None


# ---------------------------------------------------------------------------
# One-time approval challenges
# ---------------------------------------------------------------------------


class ChallengeStore:
    """Single-process one-time challenge store for confirm().

    Only SHA-256 hashes of challenge tokens are kept (raw values are never
    stored or logged). A challenge is bound to exactly one
    (action_id, revision, arguments_digest) triple and expires no later than
    the proposal. consume() is atomic under a lock: replays always fail."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_hash: dict[str, tuple[str, int, str, datetime]] = {}

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def issue(
        self, action_id: str, revision: int, arguments_digest: str, expires_at: datetime
    ) -> str:
        token = secrets.token_urlsafe(32)  # cryptographically strong, not a counter
        with self._lock:
            self._by_hash[self._hash(token)] = (action_id, revision, arguments_digest, expires_at)
        return token

    def consume(
        self,
        token: str | None,
        action_id: str,
        revision: int,
        arguments_digest: str,
        now: datetime,
    ) -> bool:
        if not token:
            return False
        with self._lock:
            binding = self._by_hash.pop(self._hash(token), None)
        if binding is None:
            return False
        bound_action, bound_revision, bound_digest, expires_at = binding
        return (
            bound_action == action_id
            and bound_revision == revision
            and bound_digest == arguments_digest
            and now < expires_at
        )


# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------


def canonical_arguments(model: BaseModel) -> dict:
    """Canonical JSON-safe dict of a validated contract (stable field names,
    enum values, ISO datetimes, discriminator tags)."""
    return model.model_dump(mode="json")


def arguments_digest(canonical: dict) -> str:
    """SHA-256 over canonical serialization: sorted keys, compact separators,
    UTF-8. Input dict ordering cannot change the digest; any semantic argument
    change (including send_updates and expected_etag) does change it."""
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ActionApprovalEngine:
    def __init__(
        self,
        policy: LoadedPolicy,
        repo: ActionRepository,
        challenge_store: ChallengeStore | None = None,
    ) -> None:
        self._policy = policy
        self._repo = repo
        self.challenges = challenge_store or ChallengeStore()
        self._triage = MeetingTriage(policy)

    @property
    def policy_version(self) -> str:
        return self._policy.version

    # -- canonical validation ----------------------------------------------------

    @staticmethod
    def validate_arguments(tool: str, arguments: dict) -> BaseModel:
        model_cls = _KNOWN_ARG_MODELS.get(tool)
        if model_cls is None:
            # Unknown mutation tools fail closed; no dynamic registry here (A04).
            raise ActionPolicyError("unknown_tool", f"tool {tool!r} is not a known policy tool")
        declared = arguments.get("tool")
        if declared is not None and declared != tool:
            # A mismatched embedded discriminator is an injection attempt, not
            # something to normalize away.
            raise ActionPolicyError(
                "invalid_arguments", "arguments declare a different tool than the call"
            )
        try:
            return model_cls.model_validate({**arguments, "tool": tool})
        except ValidationError as exc:
            # Injected authoritative keys ("risk", "requires_approval", ...)
            # land here: extra=forbid rejects them instead of downgrading policy.
            raise ActionPolicyError(
                "invalid_arguments", f"arguments failed canonical validation for {tool}"
            ) from exc

    # -- risk evaluation -----------------------------------------------------------

    def evaluate(self, call: ToolCall, context: ProposalContext) -> PolicyDecision:
        """Frozen service boundary: deterministic policy result for a call.

        No persistence and no adapter call; propose() builds on this."""
        args = self.validate_arguments(call.name, call.arguments)
        risk, reasons, meeting = self._risk_assessment(args, context)
        requires_approval, voice_allowed = self._approval_flags(risk)
        return PolicyDecision(
            arguments=args,
            risk=risk,
            requires_approval=requires_approval,
            voice_approval_allowed=voice_allowed,
            reasons=reasons,
            target_meeting=meeting,
        )

    def _risk_assessment(
        self, args: BaseModel, context: ProposalContext
    ) -> tuple[ActionRisk, list[Reason], Meeting | None]:
        cfg = self._policy.config
        reasons: list[Reason] = []

        def rule(code: str, text: str) -> Reason:
            return Reason(
                code=code, origin=ReasonOrigin.RULE, text=text, policy_version=self._policy.version
            )

        if isinstance(args, (FocusStartArguments, FocusStopArguments)):
            # Local explicit commands execute as low-risk per plan; focus state
            # itself never feeds back into risk.
            return ActionRisk.LOW, [rule("action.focus_command", "local focus command floor LOW")], None

        if isinstance(args, DecisionRecordOutcomeArguments):
            decision = context.decision
            if decision is None or decision.id != args.decision_id:
                raise ActionPolicyError(
                    "decision_context_missing",
                    "recording a decision outcome requires the referenced decision in context",
                )
            risk = _max_risk(cfg.action_risk.local_write_floor, decision.risk)
            reasons.append(
                rule(
                    "action.decision_outcome",
                    f"local outcome record for decision {decision.id}; local write only, "
                    "executes no payment, purchase or commitment",
                )
            )
            if decision.risk == ActionRisk.HIGH:
                reasons.append(
                    rule("action.high_decision_record_ui_only", "HIGH-risk decision outcome requires UI approval")
                )
            return risk, reasons, None

        # Calendar mutations
        floor = cfg.action_risk.calendar_mutation_floor
        risk = floor

        if isinstance(args, CalendarCreateEventArguments):
            proposed = self._triage.evaluate_proposed(
                title=args.title,
                attendees=args.attendees,
                description=args.description,
                location=args.location,
                evidence=context.triage_evidence,
            )
            reasons.extend(proposed.reasons)
            if proposed.priority == MeetingPriority.HIGH:
                risk = ActionRisk.HIGH
                reasons.append(
                    rule("action.high_proposed_meeting", "proposed meeting content triages HIGH")
                )
            if self._notifies_externally(args.send_updates, [a.internal for a in args.attendees]):
                risk = _max_risk(risk, ActionRisk.HIGH)
                reasons.append(
                    rule(
                        "action.notification_impact",
                        f"mutation notifies external parties (send_updates={args.send_updates.value})",
                    )
                )
            return risk, reasons, None

        target_ref = getattr(args, "ref", None)  # reschedule / update_agenda
        meeting = context.meetings.get((target_ref.calendar_id, target_ref.event_id))
        if meeting is None:
            raise ActionPolicyError(
                "target_meeting_unknown",
                "mutation targets a meeting absent from the supplied current-state context",
            )
        triaged = self._triage.evaluate(meeting)
        reasons.extend(triaged.reasons)
        if triaged.priority == MeetingPriority.HIGH:
            risk = ActionRisk.HIGH  # non-overridable, agenda edits included
            reasons.append(
                rule(
                    "action.high_meeting_mutation",
                    f"mutation of HIGH-priority meeting {meeting.ref.event_id!r} is always HIGH",
                )
            )
        if self._notifies_externally(args.send_updates, [a.internal for a in meeting.attendees]):
            risk = _max_risk(risk, ActionRisk.HIGH)
            reasons.append(
                rule(
                    "action.notification_impact",
                    f"mutation notifies external parties (send_updates={args.send_updates.value})",
                )
            )
        return risk, reasons, meeting

    def _notifies_externally(self, send_updates: SendUpdates, internals: list[bool | None]) -> bool:
        esc = self._policy.config.notification_escalation
        if not esc.escalate_external_notifications_to_high:
            return False
        if send_updates.value not in esc.notify_values:
            return False
        # Only explicit external evidence escalates; unknown internals are
        # never invented as "external".
        return any(flag is False for flag in internals)

    def _approval_flags(self, risk: ActionRisk) -> tuple[bool, bool]:
        ap = self._policy.config.approval
        if risk == ActionRisk.HIGH:
            return True, False  # UI-only, always
        if risk == ActionRisk.MEDIUM:
            return True, ApprovalChannel.VOICE in ap.medium_channels
        return ap.low_requires_approval, ApprovalChannel.VOICE in ap.low_channels

    # -- proposal --------------------------------------------------------------------

    def propose(self, call: ToolCall, context: ProposalContext) -> ProposedAction:
        """Validate, evaluate and durably persist a PENDING proposal.

        Every authoritative field below is server-derived; created/expires come
        from the server clock only (context.now), never from the caller."""
        args = self.validate_arguments(call.name, call.arguments)
        risk, reasons, meeting = self._risk_assessment(args, context)
        requires_approval, voice_allowed = self._approval_flags(risk)

        canonical = canonical_arguments(args)
        digest = arguments_digest(canonical)

        resource_version: str | None = None
        before: dict | None = None
        after: dict | None = None
        if isinstance(args, (CalendarRescheduleEventArguments, CalendarUpdateAgendaArguments)):
            # Bind the approval to the exact current resource state A04 will
            # later re-fetch and compare. No Google call happens here.
            if meeting is None or not meeting.etag:
                raise ActionPolicyError(
                    "target_etag_unknown",
                    "cannot bind a mutation proposal without the target's current ETag",
                )
            resource_version = meeting.etag
            if args.expected_etag is None:
                canonical["expected_etag"] = meeting.etag  # becomes part of the digest
                digest = arguments_digest(canonical)
            elif args.expected_etag != meeting.etag:
                raise ActionPolicyError(
                    "etag_superseded", "provided expected_etag does not match current state"
                )
            if isinstance(args, CalendarRescheduleEventArguments):
                before = {"span": canonical_arguments(meeting.span)}
                after = {"new_span": canonical["new_span"]}
            else:
                before = {"description": meeting.description}
                after = {"mode": canonical["mode"], "agenda_markdown": args.agenda_markdown}
        elif isinstance(args, CalendarCreateEventArguments):
            # No Google resource exists yet; none is invented.
            resource_version = None
            after = {"title": canonical["title"], "span": canonical["span"]}

        now = context.now
        expires_at = now + timedelta(seconds=self._policy.config.proposal.ttl_seconds)
        impact = self._impact(args, risk, meeting)
        reason_text = (
            f"{context.suggested_reason} | {reasons[0].text}"
            if context.suggested_reason
            else reasons[0].text if reasons
            else "deterministic policy evaluation"
        )

        action = ProposedAction(
            id=f"action-{uuid.uuid4()}",
            session_id=context.session_id,
            request_id=context.request_id,
            revision=context.revision,
            tool=call.name,
            arguments=canonical,
            arguments_digest=digest,
            summary=f"{call.name} ({risk.value})",
            reason=reason_text,
            impact=impact,
            before=before,
            after=after,
            resource_version=resource_version,
            policy_version=self._policy.version,
            risk=risk,
            requires_approval=requires_approval,
            voice_approval_allowed=voice_allowed,
            created_at=now,
            expires_at=expires_at,
            status=ProposedActionStatus.PENDING,
        )
        self._repo.create_action(action)
        return action

    def _impact(self, args: BaseModel, risk: ActionRisk, meeting: Meeting | None) -> str:
        send = getattr(args, "send_updates", None)
        parts = [f"risk={risk.value}"]
        if isinstance(args, CalendarCreateEventArguments):
            parts.append(f"creates calendar event {args.title!r}")
        elif isinstance(args, CalendarRescheduleEventArguments):
            parts.append(f"reschedules event {args.ref.event_id}")
        elif isinstance(args, CalendarUpdateAgendaArguments):
            parts.append(f"edits agenda of event {args.ref.event_id}")
        elif isinstance(args, DecisionRecordOutcomeArguments):
            parts.append(f"records local outcome {args.outcome.value} for decision {args.decision_id}")
        if send is not None:
            # Notification choice is part of the approved impact and digest.
            parts.append(f"notifications={send.value}")
        if meeting is not None:
            parts.append(f"target priority={meeting.priority.value}")
        return "; ".join(parts)

    # -- confirmation ------------------------------------------------------------

    def confirm(
        self, request: ApprovalRequest, *, channel: ApprovalChannel, now: datetime
    ) -> ConfirmationResult:
        """Validate every binding, then atomically approve+receipt or reject.

        ``channel`` is the SERVER-DERIVED authenticated channel (A04 routes
        pass it from the trusted session); ApprovalRequest deliberately has no
        channel field and nothing here infers it from request content."""
        action = self._repo.get_action(request.action_id)
        if action is None:
            raise ActionPolicyError("unknown_action", "no such proposal")
        if request.revision != action.revision:
            raise ActionPolicyError("revision_mismatch", "approval targets a different revision")
        if request.arguments_digest != action.arguments_digest:
            raise ActionPolicyError("digest_mismatch", "approval targets different arguments")

        if action.policy_version != self._policy.version:
            raise ActionPolicyError(
                "policy_changed", "authoritative policy changed; a new proposal is required"
            )

        if action.status == ProposedActionStatus.APPROVED:
            # Already approved (concurrent confirmation winner): represent the
            # existing state safely; never issue a second receipt.
            existing = self._repo.get_receipt(action.id, action.revision)
            return ConfirmationResult(action=action, receipt=existing, already_approved=True)
        if action.status != ProposedActionStatus.PENDING:
            raise ActionPolicyError(
                "invalid_status", f"proposal is {action.status.value}, not pending"
            )

        # Expiry gates approvals (>= expires_at fails), but rejecting an
        # expired-but-pending proposal always stays possible.
        if request.choice == ApprovalChoice.APPROVE and now >= action.expires_at:
            self._repo.expire_action(action.id, now=now)
            raise ActionPolicyError("expired", "proposal expired; a new proposal is required")

        # One-time challenge: consumed atomically; replay/wrong-binding/expired
        # all fail. A valid challenge never overrides the channel rules below.
        if not self.challenges.consume(
            request.challenge, action.id, action.revision, action.arguments_digest, now
        ):
            raise ActionPolicyError("invalid_challenge", "challenge missing, used, mismatched or expired")

        if request.choice == ApprovalChoice.REJECT:
            won = self._repo.reject_action(
                action.id,
                expected_revision=action.revision,
                expected_arguments_digest=action.arguments_digest,
            )
            if not won:
                raise ActionPolicyError("reject_failed", "rejection did not apply")
            updated = self._repo.get_action(action.id)
            assert updated is not None
            return ConfirmationResult(action=updated, receipt=None)

        # Approval channel policy: HIGH is UI-only; rejection above stays
        # possible on any authenticated channel (safe direction).
        allowed_channels = self._allowed_channels(action.risk)
        if channel not in allowed_channels:
            raise ActionPolicyError(
                "channel_not_allowed",
                f"{action.risk.value} risk cannot be approved over {channel.value}",
            )

        receipt = ApprovalReceipt(
            id=f"receipt-{uuid.uuid4()}",
            action_id=action.id,
            revision=action.revision,
            arguments_digest=action.arguments_digest,
            channel=channel,
            approved_at=now,
            policy_version=action.policy_version,
        )
        won = self._repo.approve_with_receipt(
            receipt,
            expected_revision=action.revision,
            expected_arguments_digest=action.arguments_digest,
            expected_policy_version=self._policy.version,
            now=now,
        )
        if not won:
            current = self._repo.get_action(action.id)
            if current is not None and current.status == ProposedActionStatus.APPROVED:
                existing = self._repo.get_receipt(current.id, current.revision)
                return ConfirmationResult(
                    action=current, receipt=existing, already_approved=True
                )
            raise ActionPolicyError("approval_failed", "approval did not apply to the durable state")
        approved = self._repo.get_action(action.id)
        assert approved is not None
        return ConfirmationResult(action=approved, receipt=receipt)

    def _allowed_channels(self, risk: ActionRisk) -> set[ApprovalChannel]:
        ap = self._policy.config.approval
        if risk == ActionRisk.HIGH:
            return {ApprovalChannel.UI}  # HIGH: authenticated UI only, always
        if risk == ActionRisk.MEDIUM:
            return set(ap.medium_channels)
        return set(ap.low_channels)


def _max_risk(a: ActionRisk, b: ActionRisk) -> ActionRisk:
    order = {ActionRisk.LOW: 0, ActionRisk.MEDIUM: 1, ActionRisk.HIGH: 2}
    return max((a, b), key=lambda r: order[r])
