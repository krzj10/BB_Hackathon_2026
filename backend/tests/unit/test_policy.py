"""A03 deterministic policy kernel tests (unit).

Covers POLICY.yaml strict loading/fail-closed behavior, content-derived
policy_version, meeting triage rules incl. the financial boundary, action
risk floors, canonical digests, proposal creation, challenge binding and
approval/rejection confirmation semantics. No Google, no network.

Run: python -m pytest backend/tests/unit/test_policy.py -q
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.approvals.engine import (
    ActionApprovalEngine,
    ActionPolicyError,
    ChallengeStore,
    ProposalContext,
    arguments_digest,
    canonical_arguments,
)
from app.approvals.policy import PolicyConfigError, load_policy
from app.contracts.domain import (
    ActionRisk,
    AllDaySpan,
    ApprovalChannel,
    ApprovalChoice,
    ApprovalRequest,
    Meeting,
    MeetingPriority,
    MeetingRef,
    Money,
    Participant,
    ProposedActionStatus,
    Reason,
    ReasonOrigin,
    SendUpdates,
    TimedSpan,
    ToolCall,
)
from app.db.repositories import ActionRepository
from app.db.schema import init_schema
from app.db.session import Database
from app.meetings.triage import MeetingTriage, TriageEvidence

REPO_ROOT = Path(__file__).resolve().parents[3]
POLICY_PATH = REPO_ROOT / "backend" / "app" / "policy" / "POLICY.yaml"

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def span() -> TimedSpan:
    return TimedSpan(start=NOW, end=NOW + timedelta(hours=1), timezone="Europe/Warsaw")


def make_meeting(
    *,
    event_id: str = "evt-1",
    title: str = "Quarterly review call",
    priority: MeetingPriority = MeetingPriority.MEDIUM,
    reasons: list[Reason] | None = None,
    etag: str | None = "etag-v1",
    attendees: list[Participant] | None = None,
    description: str | None = None,
) -> Meeting:
    return Meeting(
        ref=MeetingRef(calendar_id="primary", event_id=event_id),
        etag=etag,
        title=title,
        span=span(),
        attendees=attendees or [],
        priority=priority,
        priority_reasons=reasons if reasons is not None else [
            Reason(code="meeting.team_customer_floor", origin=ReasonOrigin.RULE, text="prior")
        ],
        description=description,
    )


@pytest.fixture()
def repo(tmp_path) -> ActionRepository:
    database = Database(f"sqlite:///{tmp_path / 'a03-test.db'}")
    init_schema(database)
    return ActionRepository(database)


@pytest.fixture()
def engine(repo) -> ActionApprovalEngine:
    return ActionApprovalEngine(load_policy(POLICY_PATH), repo)


def context(**overrides) -> ProposalContext:
    base = dict(session_id="sess-1", request_id="req-1", now=NOW)
    base.update(overrides)
    return ProposalContext(**base)


def call(tool: str, arguments: dict) -> ToolCall:
    return ToolCall(id="call-1", name=tool, arguments=arguments)


def create_args(**overrides) -> dict:
    args = {
        "title": "Team sync",
        "span": span().model_dump(mode="json"),
        "attendees": [],
        "send_updates": SendUpdates.NONE.value,
    }
    args.update(overrides)
    return args


def propose(engine: ActionApprovalEngine, tool: str, arguments: dict, **ctx) -> object:
    return engine.propose(call(tool, arguments), context(**ctx))


def request_for(action, *, choice=ApprovalChoice.APPROVE, engine=None, digest=None, revision=None):
    challenge = engine.challenges.issue(
        action.id, action.revision, digest or action.arguments_digest, action.expires_at
    )
    return ApprovalRequest(
        action_id=action.id,
        revision=revision or action.revision,
        arguments_digest=digest or action.arguments_digest,
        choice=choice,
        challenge=challenge,
    )


# ---------------------------------------------------------------------------
# POLICY.yaml loading: strict, fail-closed, content-versioned
# ---------------------------------------------------------------------------


def test_default_policy_loads_and_version_is_deterministic() -> None:
    first = load_policy(POLICY_PATH)
    second = load_policy(POLICY_PATH)
    assert first.version == second.version
    assert first.version.startswith("v1-")
    assert first.config.proposal.ttl_seconds == 300
    assert first.config.financial_high.threshold_minor_units == 100000


def test_policy_version_changes_when_content_changes(tmp_path) -> None:
    text = POLICY_PATH.read_text(encoding="utf-8")
    altered = tmp_path / "POLICY.yaml"
    altered.write_text(text.replace("ttl_seconds: 300", "ttl_seconds: 301"), encoding="utf-8")
    assert load_policy(altered).version != load_policy(POLICY_PATH).version


def _bad_policy(tmp_path, mutate) -> None:
    text = POLICY_PATH.read_text(encoding="utf-8")
    path = tmp_path / "POLICY.yaml"
    path.write_text(mutate(text), encoding="utf-8")
    with pytest.raises(PolicyConfigError):
        load_policy(path)


def test_malformed_yaml_fails_closed(tmp_path) -> None:
    _bad_policy(tmp_path, lambda t: t + "\n  {{{unclosed\n")


def test_missing_financial_threshold_fails_closed(tmp_path) -> None:
    _bad_policy(tmp_path, lambda t: t.replace("threshold_minor_units: 100000\n", ""))


def test_invalid_risk_enum_fails_closed(tmp_path) -> None:
    _bad_policy(tmp_path, lambda t: t.replace("calendar_mutation_floor: medium", "calendar_mutation_floor: dangerous"))


def test_unknown_approval_channel_fails_closed(tmp_path) -> None:
    _bad_policy(tmp_path, lambda t: t.replace('medium_channels: ["ui", "voice"]', 'medium_channels: ["ui", "carrier-pigeon"]'))


def test_zero_ttl_fails_closed(tmp_path) -> None:
    _bad_policy(tmp_path, lambda t: t.replace("ttl_seconds: 300", "ttl_seconds: 0"))


def test_negative_ttl_fails_closed(tmp_path) -> None:
    _bad_policy(tmp_path, lambda t: t.replace("ttl_seconds: 300", "ttl_seconds: -5"))


def test_unexpected_field_fails_closed(tmp_path) -> None:
    _bad_policy(tmp_path, lambda t: t + "\nsneaky_extra: true\n")


def test_policy_cannot_weaken_high_ui_only(tmp_path) -> None:
    _bad_policy(tmp_path, lambda t: t.replace('high_channels: ["ui"]', 'high_channels: ["ui", "voice"]'))
    _bad_policy(tmp_path, lambda t: t.replace("high_requires_approval: true", "high_requires_approval: false"))


def test_low_calendar_risk_floor_is_invalid(tmp_path) -> None:
    _bad_policy(tmp_path, lambda t: t.replace("calendar_mutation_floor: medium", "calendar_mutation_floor: low"))


# ---------------------------------------------------------------------------
# Meeting triage rules (v2.0)
# ---------------------------------------------------------------------------


@pytest.fixture()
def triage() -> MeetingTriage:
    return MeetingTriage(load_policy(POLICY_PATH))


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (99_999, MeetingPriority.MEDIUM),  # below threshold: no financial HIGH
        (100_000, MeetingPriority.HIGH),   # boundary: exactly at threshold -> HIGH
        (100_001, MeetingPriority.HIGH),
    ],
)
def test_financial_threshold_boundaries_with_intent(triage, amount, expected) -> None:
    meeting = make_meeting(title="Vendor decision call", reasons=[])
    evidence = TriageEvidence(
        financial_decision_intent=True,
        money=Money(amount_minor_units=amount, currency="PLN"),
        source_ids=("gmail:msg-77",),
    )
    result = triage.evaluate(meeting, evidence)
    assert result.priority is expected
    if expected is MeetingPriority.HIGH:
        codes = [r.code for r in result.reasons]
        assert "meeting.financial_decision_high" in codes
        financial_reason = next(r for r in result.reasons if r.code == "meeting.financial_decision_high")
        assert financial_reason.source_ids == ["gmail:msg-77"]  # evidence preserved


def test_amount_without_decision_intent_is_not_high(triage) -> None:
    meeting = make_meeting(title="Vendor recap call", reasons=[])
    evidence = TriageEvidence(
        financial_decision_intent=False,
        money=Money(amount_minor_units=5_000_000, currency="PLN"),
    )
    result = triage.evaluate(meeting, evidence)
    assert result.priority is MeetingPriority.MEDIUM
    assert "meeting.financial_amount_without_intent" in [r.code for r in result.reasons]


def test_unknown_currency_never_converted_or_invented(triage) -> None:
    meeting = make_meeting(title="Vendor decision call", reasons=[])
    evidence = TriageEvidence(
        financial_decision_intent=True,
        money=Money(amount_minor_units=9_000_000, currency="USD"),
    )
    result = triage.evaluate(meeting, evidence)
    assert result.priority is MeetingPriority.MEDIUM
    assert "meeting.financial_unsupported_currency" in [r.code for r in result.reasons]


def test_board_investor_and_polish_markers_are_high(triage) -> None:
    for title in ("Board meeting", "Spotkanie zarządu inwestora", "Contract negotiation prep"):
        result = triage.evaluate(make_meeting(title=title, reasons=[]))
        assert result.priority is MeetingPriority.HIGH, title


def test_team_customer_floor_and_informal_low(triage) -> None:
    assert triage.evaluate(make_meeting(title="Projekt Alpha sprint", reasons=[])).priority is MeetingPriority.MEDIUM
    assert triage.evaluate(make_meeting(title="Coffee catch-up", reasons=[])).priority is MeetingPriority.LOW


def test_a02_provisional_placeholder_does_not_block_escalation(triage) -> None:
    meeting = make_meeting(
        title="Board review of strategic contract negotiation",
        priority=MeetingPriority.MEDIUM,  # A02 untriaged placeholder value
        reasons=[Reason(code="untriaged_read_default", origin=ReasonOrigin.RULE, text="provisional")],
    )
    result = triage.evaluate(meeting)
    assert result.priority is MeetingPriority.HIGH


def test_authoritative_priority_is_never_downgraded(triage) -> None:
    meeting = make_meeting(
        title="Quarterly review call",  # evaluates MEDIUM
        priority=MeetingPriority.HIGH,
        reasons=[Reason(code="meeting.board_investor_high", origin=ReasonOrigin.RULE, text="earlier")],
    )
    result = triage.evaluate(meeting)
    assert result.priority is MeetingPriority.HIGH
    assert "meeting.authoritative_priority_retained" in [r.code for r in result.reasons]


def test_reasons_carry_rule_origin_and_policy_version(triage) -> None:
    version = load_policy(POLICY_PATH).version
    result = triage.evaluate(make_meeting(title="Board meeting", reasons=[]))
    assert all(r.origin is ReasonOrigin.RULE for r in result.reasons)
    assert all(r.policy_version == version for r in result.reasons)


# ---------------------------------------------------------------------------
# Action risk floors and proposal creation
# ---------------------------------------------------------------------------


def test_ordinary_create_is_medium_and_requires_approval(engine) -> None:
    action = propose(engine, "calendar.create_event", create_args())
    assert action.risk is ActionRisk.MEDIUM
    assert action.requires_approval is True
    assert action.status is ProposedActionStatus.PENDING
    assert action.resource_version is None  # no Google resource exists yet
    assert action.expires_at - action.created_at == timedelta(minutes=5)


def test_proposed_high_content_create_is_high_before_approval(engine) -> None:
    action = propose(engine, "calendar.create_event", create_args(title="Investor board review"))
    assert action.risk is ActionRisk.HIGH
    assert action.requires_approval is True
    assert action.voice_approval_allowed is False


def test_high_meeting_agenda_edit_is_high_despite_llm_claim(engine) -> None:
    """Mandatory regression: agenda edits are real mutations; a HIGH meeting's
    agenda edit stays HIGH even when the model claims it is low risk."""
    high = make_meeting(title="Board strategy session", priority=MeetingPriority.HIGH, reasons=[])
    action = engine.propose(
        call("calendar.update_agenda", {
            "ref": {"calendar_id": "primary", "event_id": high.ref.event_id},
            "mode": "update",
            "agenda_markdown": "- quick item",
            "send_updates": SendUpdates.NONE.value,
        }),
        context(meetings={("primary", high.ref.event_id): high}, suggested_reason="low risk formatting only"),
    )
    assert action.risk is ActionRisk.HIGH
    assert action.voice_approval_allowed is False


def test_high_meeting_reschedule_is_high(engine) -> None:
    high = make_meeting(title="Board strategy session", priority=MeetingPriority.HIGH, reasons=[])
    action = propose(
        engine,
        "calendar.reschedule_event",
        {"ref": {"calendar_id": "primary", "event_id": high.ref.event_id}, "new_span": span().model_dump(mode="json")},
        meetings={("primary", high.ref.event_id): high},
    )
    assert action.risk is ActionRisk.HIGH


def test_low_medium_reschedule_floor_is_medium(engine) -> None:
    medium = make_meeting(title="Team planning", priority=MeetingPriority.MEDIUM, reasons=[])
    action = propose(
        engine,
        "calendar.reschedule_event",
        {"ref": {"calendar_id": "primary", "event_id": medium.ref.event_id}, "new_span": span().model_dump(mode="json")},
        meetings={("primary", medium.ref.event_id): medium},
    )
    assert action.risk is ActionRisk.MEDIUM


def test_llm_cannot_inject_authoritative_fields_into_arguments(engine) -> None:
    for injected in ({"risk": "low"}, {"requires_approval": False}, {"voice_approval_allowed": True}):
        args = {**create_args(), **injected}
        with pytest.raises(ActionPolicyError) as excinfo:
            propose(engine, "calendar.create_event", args)
        assert excinfo.value.code == "invalid_arguments"


def test_toolcall_contract_rejects_top_level_risk() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ToolCall(id="c", name="calendar.create_event", arguments={}, risk="low")


def test_unknown_tool_fails_closed(engine) -> None:
    with pytest.raises(ActionPolicyError) as excinfo:
        propose(engine, "payment.execute", {"amount": 1})
    assert excinfo.value.code == "unknown_tool"


def test_mismatched_embedded_tool_discriminator_fails_closed(engine) -> None:
    args = {**create_args(), "tool": "focus.stop"}
    with pytest.raises(ActionPolicyError) as excinfo:
        propose(engine, "calendar.create_event", args)
    assert excinfo.value.code == "invalid_arguments"


def test_mutation_of_unknown_meeting_fails_closed(engine) -> None:
    with pytest.raises(ActionPolicyError) as excinfo:
        propose(
            engine,
            "calendar.reschedule_event",
            {"ref": {"calendar_id": "primary", "event_id": "ghost"}, "new_span": span().model_dump(mode="json")},
            meetings={},
        )
    assert excinfo.value.code == "target_meeting_unknown"


def test_etag_binding_snapshot_and_supersede(engine) -> None:
    medium = make_meeting(title="Team planning", etag="etag-current")
    base_args = {"ref": {"calendar_id": "primary", "event_id": medium.ref.event_id}, "new_span": span().model_dump(mode="json")}

    action = propose(engine, "calendar.reschedule_event", base_args, meetings={("primary", medium.ref.event_id): medium})
    assert action.resource_version == "etag-current"          # snapshot for A04 revalidation
    assert action.arguments["expected_etag"] == "etag-current"  # canonicalized into the digest

    stale = {**base_args, "expected_etag": "etag-old"}
    with pytest.raises(ActionPolicyError) as excinfo:
        propose(engine, "calendar.reschedule_event", stale, meetings={("primary", medium.ref.event_id): medium})
    assert excinfo.value.code == "etag_superseded"


def test_missing_target_etag_fails_closed(engine) -> None:
    no_tag = make_meeting(title="Team planning", etag=None)
    with pytest.raises(ActionPolicyError) as excinfo:
        propose(
            engine,
            "calendar.reschedule_event",
            {"ref": {"calendar_id": "primary", "event_id": no_tag.ref.event_id}, "new_span": span().model_dump(mode="json")},
            meetings={("primary", no_tag.ref.event_id): no_tag},
        )
    assert excinfo.value.code == "target_etag_unknown"


def test_focus_commands_are_low_risk_local(engine) -> None:
    action = propose(engine, "focus.start", {"duration_minutes": 60, "threshold": "medium"})
    assert action.risk is ActionRisk.LOW
    assert action.requires_approval is False
    stop = propose(engine, "focus.stop", {})
    assert stop.risk is ActionRisk.LOW


def _decision(risk: ActionRisk):
    from app.contracts.domain import Decision, DecisionStatus

    return Decision(
        id="dec-1", attention_item_id="att-1", title="Supplier quote",
        risk=risk, status=DecisionStatus.NEEDS_REVIEW,
    )


def test_decision_outcome_requires_context_and_high_stays_ui_only(engine) -> None:
    with pytest.raises(ActionPolicyError) as excinfo:
        propose(engine, "decision.record_outcome", {"decision_id": "dec-1", "outcome": "accept"})
    assert excinfo.value.code == "decision_context_missing"

    high_action = propose(
        engine, "decision.record_outcome", {"decision_id": "dec-1", "outcome": "accept"},
        decision=_decision(ActionRisk.HIGH),
    )
    assert high_action.risk is ActionRisk.HIGH
    assert high_action.voice_approval_allowed is False

    medium_action = propose(
        engine, "decision.record_outcome", {"decision_id": "dec-1", "outcome": "reject"},
        decision=_decision(ActionRisk.MEDIUM),
    )
    assert medium_action.risk is ActionRisk.MEDIUM


def test_external_notification_escalates_risk(engine) -> None:
    external = Participant(email="vendor@example.com", name="Vendor", internal=False)
    decision = engine.evaluate(
        call("calendar.create_event", create_args(
            title="Supplier review",
            attendees=[external.model_dump(mode="json")],
            send_updates=SendUpdates.ALL.value,
        )),
        context(),
    )
    assert decision.risk is ActionRisk.HIGH
    assert "action.notification_impact" in [r.code for r in decision.reasons]

    # Unknown internals are never invented as external.
    unknown = Participant(email="person@example.com")
    stay = engine.evaluate(
        call("calendar.create_event", create_args(
            title="Supplier review",
            attendees=[unknown.model_dump(mode="json")],
            send_updates=SendUpdates.ALL.value,
        )),
        context(),
    )
    assert stay.risk is ActionRisk.MEDIUM


def test_high_meeting_mutation_rule_code_is_reported(engine) -> None:
    high = make_meeting(title="Board strategy session", priority=MeetingPriority.HIGH, reasons=[])
    decision = engine.evaluate(
        call("calendar.update_agenda", {
            "ref": {"calendar_id": "primary", "event_id": high.ref.event_id},
            "mode": "add",
            "agenda_markdown": "- x",
        }),
        context(meetings={("primary", high.ref.event_id): high}),
    )
    assert decision.risk is ActionRisk.HIGH
    assert "action.high_meeting_mutation" in [r.code for r in decision.reasons]


def test_impact_includes_notification_choice(engine) -> None:
    action = propose(engine, "calendar.create_event", create_args(send_updates=SendUpdates.ALL.value))
    assert "notifications=all" in action.impact


# ---------------------------------------------------------------------------
# Canonical digest determinism and binding
# ---------------------------------------------------------------------------


def test_digest_is_order_independent_and_semantic() -> None:
    args_a = {"title": "T", "span": span().model_dump(mode="json"), "send_updates": "none"}
    args_b = {"send_updates": "none", "span": span().model_dump(mode="json"), "title": "T"}
    model_a = ActionApprovalEngine.validate_arguments("calendar.create_event", args_a)
    model_b = ActionApprovalEngine.validate_arguments("calendar.create_event", args_b)
    assert arguments_digest(canonical_arguments(model_a)) == arguments_digest(canonical_arguments(model_b))

    later = TimedSpan(
        start=NOW + timedelta(hours=2), end=NOW + timedelta(hours=3), timezone="Europe/Warsaw"
    )
    changed = {"title": "T", "span": later.model_dump(mode="json"), "send_updates": "none"}
    model_c = ActionApprovalEngine.validate_arguments("calendar.create_event", changed)
    assert arguments_digest(canonical_arguments(model_c)) != arguments_digest(canonical_arguments(model_a))


def test_send_updates_change_changes_digest(engine) -> None:
    none_action = propose(engine, "calendar.create_event", create_args(send_updates="none"))
    all_action = propose(engine, "calendar.create_event", create_args(send_updates="all"))
    assert none_action.arguments_digest != all_action.arguments_digest


# ---------------------------------------------------------------------------
# Confirmation: bindings, channels, expiry, challenges
# ---------------------------------------------------------------------------


def _propose_medium(engine):
    return propose(engine, "calendar.create_event", create_args())


def test_medium_ui_approval_issues_bound_receipt(engine) -> None:
    action = _propose_medium(engine)
    result = engine.confirm(request_for(action, engine=engine), channel=ApprovalChannel.UI, now=NOW + timedelta(seconds=30))
    assert result.action.status is ProposedActionStatus.APPROVED
    assert result.receipt is not None
    stored = repo_of(engine).get_receipt(action.id, action.revision)
    assert stored is not None
    assert stored.arguments_digest == action.arguments_digest
    assert stored.policy_version == action.policy_version
    assert stored.channel is ApprovalChannel.UI


def repo_of(engine: ActionApprovalEngine) -> ActionRepository:
    return engine._repo  # test access to the fixture-backed repository


def test_medium_voice_approval_allowed_by_policy(engine) -> None:
    action = _propose_medium(engine)
    result = engine.confirm(request_for(action, engine=engine), channel=ApprovalChannel.VOICE, now=NOW + timedelta(seconds=30))
    assert result.action.status is ProposedActionStatus.APPROVED


def test_high_voice_confirmation_denied_ui_allowed(engine) -> None:
    action = propose(engine, "calendar.create_event", create_args(title="Board strategy session"))
    with pytest.raises(ActionPolicyError) as excinfo:
        engine.confirm(request_for(action, engine=engine), channel=ApprovalChannel.VOICE, now=NOW + timedelta(seconds=30))
    assert excinfo.value.code == "channel_not_allowed"
    # Voice attempt burned its challenge; the UI path gets its own.
    result = engine.confirm(request_for(action, engine=engine), channel=ApprovalChannel.UI, now=NOW + timedelta(seconds=40))
    assert result.action.status is ProposedActionStatus.APPROVED


def test_expired_proposal_cannot_be_approved(engine) -> None:
    action = _propose_medium(engine)
    with pytest.raises(ActionPolicyError) as excinfo:
        engine.confirm(request_for(action, engine=engine), channel=ApprovalChannel.UI, now=action.expires_at)
    assert excinfo.value.code == "expired"
    assert repo_of(engine).get_action(action.id).status is ProposedActionStatus.EXPIRED
    assert repo_of(engine).get_receipt(action.id, action.revision) is None


def test_revision_mismatch_fails(engine) -> None:
    action = _propose_medium(engine)
    with pytest.raises(ActionPolicyError) as excinfo:
        engine.confirm(request_for(action, engine=engine, revision=2), channel=ApprovalChannel.UI, now=NOW + timedelta(seconds=10))
    assert excinfo.value.code == "revision_mismatch"


def test_digest_mismatch_fails(engine) -> None:
    action = _propose_medium(engine)
    with pytest.raises(ActionPolicyError) as excinfo:
        engine.confirm(
            request_for(action, engine=engine, digest="sha256:" + "0" * 64),
            channel=ApprovalChannel.UI, now=NOW + timedelta(seconds=10),
        )
    assert excinfo.value.code == "digest_mismatch"


def test_changed_policy_invalidates_approval(tmp_path, repo) -> None:
    engine_a = ActionApprovalEngine(load_policy(POLICY_PATH), repo)
    action = propose(engine_a, "calendar.create_event", create_args())

    altered = tmp_path / "POLICY.yaml"
    altered.write_text(
        POLICY_PATH.read_text(encoding="utf-8").replace("ttl_seconds: 300", "ttl_seconds: 240"),
        encoding="utf-8",
    )
    engine_b = ActionApprovalEngine(load_policy(altered), repo)
    with pytest.raises(ActionPolicyError) as excinfo:
        engine_b.confirm(request_for(action, engine=engine_b), channel=ApprovalChannel.UI, now=NOW + timedelta(seconds=10))
    assert excinfo.value.code == "policy_changed"
    # The proposal keeps its ORIGINAL policy version; nothing is rewritten.
    assert repo.get_action(action.id).policy_version == action.policy_version


def test_challenge_replay_fails(engine) -> None:
    action = _propose_medium(engine)
    request = request_for(action, engine=engine)
    first = engine.confirm(request, channel=ApprovalChannel.UI, now=NOW + timedelta(seconds=10))
    assert first.action.status is ProposedActionStatus.APPROVED
    replay = engine.confirm(request, channel=ApprovalChannel.UI, now=NOW + timedelta(seconds=20))
    # Replay cannot re-approve; it safely reports the existing approval and
    # never produces a second receipt.
    assert replay.already_approved is True
    receipts = [repo_of(engine).get_receipt(action.id, action.revision)]
    assert receipts[0].id == first.receipt.id


def test_challenge_bound_to_one_action(engine) -> None:
    action_a = _propose_medium(engine)
    action_b = _propose_medium(engine)
    stolen = engine.challenges.issue(action_a.id, action_a.revision, action_a.arguments_digest, action_a.expires_at)
    wrong = ApprovalRequest(
        action_id=action_b.id, revision=action_b.revision,
        arguments_digest=action_b.arguments_digest, choice=ApprovalChoice.APPROVE, challenge=stolen,
    )
    with pytest.raises(ActionPolicyError) as excinfo:
        engine.confirm(wrong, channel=ApprovalChannel.UI, now=NOW + timedelta(seconds=10))
    assert excinfo.value.code == "invalid_challenge"


def test_challenge_store_is_concurrent_single_use() -> None:
    store = ChallengeStore()
    expires = NOW + timedelta(minutes=5)
    token = store.issue("action-x", 1, "digest-1", expires)
    barrier = threading.Barrier(2)
    results: list[bool] = []
    lock = threading.Lock()

    def attempt() -> None:
        barrier.wait()
        won = store.consume(token, "action-x", 1, "digest-1", NOW)
        with lock:
            results.append(won)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(results) == [False, True]


def test_reject_is_final_and_leaves_no_receipt(engine) -> None:
    action = _propose_medium(engine)
    result = engine.confirm(
        request_for(action, engine=engine, choice=ApprovalChoice.REJECT),
        channel=ApprovalChannel.UI, now=NOW + timedelta(seconds=10),
    )
    assert result.action.status is ProposedActionStatus.REJECTED
    assert result.receipt is None
    assert repo_of(engine).get_receipt(action.id, action.revision) is None

    with pytest.raises(ActionPolicyError) as excinfo:
        engine.confirm(request_for(action, engine=engine), channel=ApprovalChannel.UI, now=NOW + timedelta(seconds=20))
    assert excinfo.value.code == "invalid_status"


def test_concurrent_confirms_produce_exactly_one_receipt(engine) -> None:
    action = _propose_medium(engine)
    requests = [request_for(action, engine=engine) for _ in range(2)]
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt(request: ApprovalRequest) -> None:
        barrier.wait()
        try:
            result = engine.confirm(request, channel=ApprovalChannel.UI, now=NOW + timedelta(seconds=10))
            verdict = "already" if result.already_approved else "won"
        except ActionPolicyError as exc:
            verdict = f"error:{exc.code}"
        with lock:
            outcomes.append(verdict)

    threads = [threading.Thread(target=attempt, args=(r,)) for r in requests]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(outcomes) == ["already", "won"]
    assert repo_of(engine).get_action(action.id).status is ProposedActionStatus.APPROVED


def test_focus_and_preferences_cannot_downgrade_high(engine) -> None:
    """Focus/user preference material reaches the engine only as suggested
    reason TEXT; a HIGH meeting mutation stays HIGH regardless."""
    high = make_meeting(title="Board investment review", priority=MeetingPriority.HIGH, reasons=[])
    action = engine.propose(
        call("calendar.update_agenda", {
            "ref": {"calendar_id": "primary", "event_id": high.ref.event_id},
            "mode": "add",
            "agenda_markdown": "- notes",
        }),
        context(
            meetings={("primary", high.ref.event_id): high},
            suggested_reason="user prefers casual tone; informal catch-up, low risk please",
        ),
    )
    assert action.risk is ActionRisk.HIGH
    assert action.requires_approval is True
    assert action.voice_approval_allowed is False
