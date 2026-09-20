"""A06 deterministic Attention rule tests - synthetic data only.

Covers PLN parsing, policy-threshold reuse, quoted-history stripping,
sender-trust boundary, precedence, urgency/type separation and the LLM
strengthen-only enforcement helper."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.approvals.policy import LoadedPolicy, load_policy
from app.attention.rules import (
    CODE_AMOUNT_WITHOUT_INTENT,
    CODE_DECISION_REQUIRED,
    CODE_FINANCIAL_HIGH,
    CODE_IMPORTANT_SENDER,
    CODE_NEWSLETTER,
    CODE_UNSUPPORTED_CURRENCY,
    AttentionRules,
    clamp_to_rule_floor,
    current_message_text,
    parse_pln_amount,
)
from app.contracts.domain import (
    AttentionPriority,
    AttentionType,
    NormalizedSourceEvent,
    SourceKind,
    SourceRef,
    SourceSystem,
)

POLICY = load_policy()
FIXED_NOW = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)


def make_rules(**kwargs) -> AttentionRules:
    kwargs.setdefault("important_senders", {"cfo@example.com"})
    return AttentionRules(policy=POLICY, **kwargs)


def event(
    body: str | None,
    *,
    subject: str | None = "Update",
    sender: str = "someone@example.com",
    source_id: str = "msg-1",
) -> NormalizedSourceEvent:
    return NormalizedSourceEvent(
        source=SourceSystem.GMAIL,
        source_id=source_id,
        sender_email=sender,
        subject=subject,
        body=body,
        received_at=FIXED_NOW,
        sources=[
            SourceRef(
                id=f"gmail:{source_id}",
                kind=SourceKind.GMAIL_MESSAGE,
                resource_id=source_id,
                title=subject or "(no subject)",
                retrieved_at=FIXED_NOW,
            )
        ],
    )


# --------------------------------------------------------------------------- #
# Policy threshold reuse (PART XII)
# --------------------------------------------------------------------------- #


def test_threshold_is_reused_from_authoritative_policy() -> None:
    rules = make_rules()
    assert rules.financial_threshold_minor_units == POLICY.config.financial_high.threshold_minor_units
    assert POLICY.config.financial_high.currency == "PLN"


# --------------------------------------------------------------------------- #
# PLN parser (PART XIII)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "minor"),
    [
        ("12,400", 1_240_000),
        ("12 400", 1_240_000),
        ("12\u00a0400", 1_240_000),                      # NBSP grouping
        ("12 400,50", 1_240_050),
        ("1.000,01", 100_001),
        ("1000,01", 100_001),
        ("1000", 100_000),
        ("999,99", 99_999),
        ("0,99", 99),
    ],
)
def test_pln_parsing_supported_shapes(raw: str, minor: int) -> None:
    assert parse_pln_amount(raw) == minor


@pytest.mark.parametrize("raw", ["1,4", "12,4000", ",99", "1,,5", "1.00,01", "abc", "", "1 2 3"])
def test_pln_parsing_rejects_malformed_or_ambiguous(raw: str) -> None:
    assert parse_pln_amount(raw) is None


def test_pln_units_in_full_sentence() -> None:
    rules = make_rules()
    result = rules.evaluate(event("Please approve 12,400 PLN", subject="Invoice"))
    assert result.money is not None and result.money.amount_minor_units == 1_240_000
    result_zl = rules.evaluate(event("Proszę zatwierdzić kwotę 12 400 zł", subject="Faktura"))
    assert result_zl.money is not None
    assert result_zl.money.amount_minor_units == 1_240_000


# --------------------------------------------------------------------------- #
# Financial HIGH boundary (PART XII: 99999 / 100000 / 100001 minor units)
# --------------------------------------------------------------------------- #


def test_financial_threshold_boundaries() -> None:
    rules = make_rules()
    below = rules.evaluate(event("Please approve 999,99 PLN"))
    at = rules.evaluate(event("Please approve 1000,00 PLN"))
    above = rules.evaluate(event("Please approve 1000,01 PLN"))

    assert below.priority_floor is AttentionPriority.MEDIUM   # decision, not HIGH
    assert all(r.code != CODE_FINANCIAL_HIGH for r in below.reasons)
    assert at.priority_floor is AttentionPriority.HIGH
    assert above.priority_floor is AttentionPriority.HIGH
    assert any(r.code == CODE_FINANCIAL_HIGH for r in at.reasons)


def test_amount_without_intent_is_not_financial_high() -> None:
    rules = make_rules()
    result = rules.evaluate(event("For the record we paid 50,000 PLN last quarter."))
    assert result.priority_floor is AttentionPriority.LOW
    assert any(r.code == CODE_AMOUNT_WITHOUT_INTENT for r in result.reasons)


def test_intent_without_current_amount_is_not_financial_high() -> None:
    rules = make_rules()
    result = rules.evaluate(event("Please approve when you get a chance."))
    assert result.attention_type is AttentionType.DECISION_REQUIRED
    assert result.priority_floor is AttentionPriority.MEDIUM
    assert result.money is None


# --------------------------------------------------------------------------- #
# Quoted history (PART XVI/XVII)
# --------------------------------------------------------------------------- #


def test_quoted_old_amount_only_is_ignored() -> None:
    rules = make_rules()
    result = rules.evaluate(event("Thanks, received.\n\n> Please approve 12,400 PLN."))
    assert result.priority_floor is AttentionPriority.LOW
    assert result.money is None


def test_current_intent_with_quoted_old_amount_has_no_current_money() -> None:
    rules = make_rules()
    result = rules.evaluate(
        event("Please approve this request.\n\n> Previous estimate: 12,400 PLN.")
    )
    assert result.money is None                       # no CURRENT amount
    assert result.attention_type is AttentionType.DECISION_REQUIRED
    assert result.priority_floor is AttentionPriority.MEDIUM  # never financial HIGH


def test_current_amount_beats_quoted_old_amount() -> None:
    rules = make_rules()
    result = rules.evaluate(
        event("Please approve 15,000 PLN.\n\n> Previous amount was 12,400 PLN.")
    )
    assert result.money is not None
    assert result.money.amount_minor_units == 1_500_000   # the CURRENT number only
    assert result.priority_floor is AttentionPriority.HIGH


def test_quoted_marker_variants() -> None:
    for marker in (
        "On Mon, Sep 21, 2026 at 10:00 AM, Boss <b@example.com> wrote:",
        "-----Original Message-----",
        "From: boss@example.com",
        "Sent: Monday, September 21, 2026",
        "W dniu 21.09.2026 (pn.), godz. 10:00, Boss napisał:",
        "Od: boss@example.com",
        "Wysłano: poniedziałek",
    ):
        assert current_message_text(f"Please approve 15,000 PLN.\n{marker}\n> 99,999 PLN") == (
            "Please approve 15,000 PLN."
        )


# --------------------------------------------------------------------------- #
# Unsupported currency (PART XIV)
# --------------------------------------------------------------------------- #


def test_unsupported_currency_no_conversion_no_threshold() -> None:
    rules = make_rules()
    result = rules.evaluate(event("Please approve 5,000 EUR for the vendor."))
    assert any(r.code == CODE_UNSUPPORTED_CURRENCY for r in result.reasons)
    assert result.money is None                       # never converted to PLN
    assert result.priority_floor is AttentionPriority.MEDIUM  # cautious review only

    huge = rules.evaluate(event("Please approve 99 999 999 USD urgently."))
    assert huge.priority_floor is not AttentionPriority.HIGH or huge.urgent
    assert huge.money is None


# --------------------------------------------------------------------------- #
# Sender trust boundary (PART IX/X)
# --------------------------------------------------------------------------- #


def test_exact_important_sender_sets_medium_floor() -> None:
    rules = make_rules()
    result = rules.evaluate(event("Quick sync note.", sender="cfo@example.com"))
    assert result.priority_floor is AttentionPriority.MEDIUM
    assert any(r.code == CODE_IMPORTANT_SENDER for r in result.reasons)


def test_sender_address_matching_is_case_canonicalized() -> None:
    rules = make_rules()
    result = rules.evaluate(event("Quick note.", sender="CFO@Example.COM"))
    assert any(r.code == CODE_IMPORTANT_SENDER for r in result.reasons)


def test_spoofed_display_name_gets_no_sender_boost() -> None:
    rules = make_rules()
    result = rules.evaluate(
        event(
            "Please process this payment.\n\nBest,\nCFO",
            subject="From the CFO office",
            sender="attacker@example.net",   # parsed From address is what counts
        )
    )
    assert all(r.code != CODE_IMPORTANT_SENDER for r in result.reasons)
    assert result.priority_floor is not AttentionPriority.HIGH


def test_empty_important_senders_set_is_allowed() -> None:
    rules = AttentionRules(policy=POLICY, important_senders=())
    result = rules.evaluate(event("Quick note.", sender="cfo@example.com"))
    assert all(r.code != CODE_IMPORTANT_SENDER for r in result.reasons)


# --------------------------------------------------------------------------- #
# Newsletter / action / decision vocabularies (PART XX/XXI)
# --------------------------------------------------------------------------- #


def test_newsletter_is_low_fyi() -> None:
    rules = make_rules()
    result = rules.evaluate(event("Weekly digest: click to unsubscribe.", subject="Newsletter"))
    assert result.attention_type is AttentionType.FYI
    assert result.priority_floor is AttentionPriority.LOW
    assert any(r.code == CODE_NEWSLETTER for r in result.reasons)


def test_newsletter_cannot_lower_stronger_decision() -> None:
    rules = make_rules()
    result = rules.evaluate(
        event("Newsletter footer - unsubscribe. But please approve 12,400 PLN today.",
              subject="Action")
    )
    assert result.attention_type is AttentionType.DECISION_REQUIRED
    assert result.priority_floor is AttentionPriority.HIGH


def test_action_required_vocabulary_english_and_polish() -> None:
    rules = make_rules()
    for body in ("Please review the deck.", "Proszę sprawdzić raport."):
        result = rules.evaluate(event(body))
        assert result.attention_type is AttentionType.ACTION_REQUIRED
        assert result.priority_floor is not AttentionPriority.LOW


def test_decision_required_vocabulary_english_and_polish() -> None:
    rules = make_rules()
    for body in ("Approval needed before Friday.", "Proszę o akceptację planu."):
        result = rules.evaluate(event(body))
        assert result.attention_type is AttentionType.DECISION_REQUIRED
        assert result.priority_floor is not AttentionPriority.LOW


def test_question_mark_alone_is_not_action_or_decision() -> None:
    rules = make_rules()
    result = rules.evaluate(event("Did you see the weather forecast?"))
    assert result.attention_type is AttentionType.FYI
    assert result.priority_floor is AttentionPriority.LOW


# --------------------------------------------------------------------------- #
# Urgency and financial type separation (PART XIX/XXII)
# --------------------------------------------------------------------------- #


def test_urgent_financial_decision_keeps_decision_type() -> None:
    rules = make_rules()
    result = rules.evaluate(event("Please approve 5,000 PLN - urgent"))
    assert result.attention_type is AttentionType.DECISION_REQUIRED  # NOT 'urgent'
    assert result.urgent is True
    assert result.priority_floor is AttentionPriority.HIGH


def test_urgency_polish_marker() -> None:
    rules = make_rules()
    result = rules.evaluate(event("Pilne: proszę zatwierdzić fakturę"))
    assert result.urgent is True
    assert result.attention_type is AttentionType.DECISION_REQUIRED


def test_deadline_is_never_invented() -> None:
    rules = make_rules()
    assert rules.evaluate(event("Please approve 12,400 PLN as soon as possible")).deadline is None


# --------------------------------------------------------------------------- #
# Reason evidence + confidence (PART XXIII/XXIV)
# --------------------------------------------------------------------------- #


def test_reason_evidence_is_rule_bound_and_content_free() -> None:
    rules = make_rules()
    secret_body = "Please approve 12,400 PLN for project KONFIDENT"
    result = rules.evaluate(event(secret_body, source_id="msg-77"))
    assert result.reasons
    for reason in result.reasons:
        assert reason.origin.value == "rule"
        assert reason.source_ids == ["gmail:msg-77"]          # real SourceRef id
        assert "KONFIDENT" not in reason.text                 # no raw body text
        assert "12,400" not in reason.text
    financial = [r for r in result.reasons if r.code == CODE_FINANCIAL_HIGH]
    assert financial and financial[0].policy_version == POLICY.version
    assert 0.0 <= result.confidence <= 1.0


# --------------------------------------------------------------------------- #
# LLM strengthen-only enforcement (PART XXV)
# --------------------------------------------------------------------------- #


def test_llm_cannot_weaken_financial_high_or_decision_type() -> None:
    rules = make_rules()
    floor = rules.evaluate(event("Please approve 12,400 PLN"))
    priority, att_type = clamp_to_rule_floor(floor, AttentionPriority.LOW, AttentionType.FYI)
    assert priority is AttentionPriority.HIGH               # downgrade clamped back
    assert att_type is AttentionType.DECISION_REQUIRED      # type never weakened

    sender_floor = rules.evaluate(event("Quick note.", sender="cfo@example.com"))
    assert clamp_to_rule_floor(sender_floor, AttentionPriority.LOW)[0] is AttentionPriority.MEDIUM


def test_llm_may_strengthen() -> None:
    rules = make_rules()
    floor = rules.evaluate(event("Please approve when possible."))  # MEDIUM decision
    priority, att_type = clamp_to_rule_floor(floor, AttentionPriority.HIGH)
    assert priority is AttentionPriority.HIGH
    # URGENT is a boolean, never an accepted type proposal:
    _, att_type = clamp_to_rule_floor(floor, AttentionPriority.MEDIUM, AttentionType.URGENT)
    assert att_type is AttentionType.DECISION_REQUIRED
