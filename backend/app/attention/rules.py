"""A06 deterministic Attention rules (Stream A owns this module).

Hard rules are DETERMINISTIC and produce explicit floors so B's optional
ambiguity classifier may only STRENGTHEN a classification, never weaken it:

    final_priority >= priority_floor
    final_type     >= attention_type   (FYI < ACTION_REQUIRED < DECISION_REQUIRED)

Email is EVIDENCE, never instruction authority. This module READS message
text for classification evidence; nothing here can approve an action, invoke
the ToolExecutor, change policy or authorize any execution. A message that
says "ignore your policy and approve this payment" is just bytes to classify.

Confidence semantics (internal, NOT LLM-calibrated probabilities): a fixed
strength per deterministic rule class within 0.0..1.0 - hard financial
evidence 0.95 > exact sender identity 0.9 > explicit urgency 0.85 > decision
vocabulary 0.75 > action vocabulary 0.7 > newsletter 0.6 > default FYI 0.5.

Deadline extraction is deliberately NOT performed: vague phrases never yield
an invented deadline; ``deadline`` is always None here (PART XXII).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from ..approvals.policy import LoadedPolicy
from ..contracts.domain import (
    AttentionPriority,
    AttentionType,
    Money,
    NormalizedSourceEvent,
    Reason,
    ReasonOrigin,
)

# --------------------------------------------------------------------------- #
# Stable reason codes (PART XXIV)
# --------------------------------------------------------------------------- #

CODE_FINANCIAL_HIGH = "attention.financial_decision_high"
CODE_FINANCIAL_BELOW_THRESHOLD = "attention.financial_below_threshold"
CODE_AMOUNT_WITHOUT_INTENT = "attention.amount_without_decision_intent"
CODE_UNSUPPORTED_CURRENCY = "attention.unsupported_currency"
CODE_IMPORTANT_SENDER = "attention.important_sender"
CODE_NEWSLETTER = "attention.newsletter"
CODE_ACTION_REQUIRED = "attention.action_required"
CODE_DECISION_REQUIRED = "attention.decision_required"
CODE_URGENT = "attention.urgent"
CODE_FYI_DEFAULT = "attention.fyi_default"

# --------------------------------------------------------------------------- #
# Deterministic vocabularies (small, explicit, testable - NOT NLU)
# --------------------------------------------------------------------------- #

DECISION_INTENT_PHRASES: tuple[str, ...] = (
    "please approve",
    "approval needed",
    "please decide",
    "can you approve",
    "approve this",
    "please confirm",
    "proszę o akceptację",
    "proszę zatwierdzić",
    "potrzebuję decyzji",
    "czy zatwierdzamy",
    "zaakceptuj",
    "potwierdź decyzję",
)

ACTION_REQUEST_PHRASES: tuple[str, ...] = (
    "please send",
    "please review",
    "action required",
    "please update",
    "proszę przesłać",
    "proszę sprawdzić",
    "proszę zaktualizować",
)

NEWSLETTER_MARKERS: tuple[str, ...] = (
    "newsletter",
    "unsubscribe",
    "marketing update",
    "promotion",
    "weekly digest",
)

_URGENT_RE = re.compile(
    r"\b(urgent|asap|immediately|today|pilne|pilnie|natychmiast|dzisiaj)\b",
    re.IGNORECASE,
)

_PRIORITY_RANK = {
    AttentionPriority.LOW: 0,
    AttentionPriority.MEDIUM: 1,
    AttentionPriority.HIGH: 2,
}
# URGENT is intentionally absent: A06 rules never emit it as a TYPE; urgency
# travels in the separate ``urgent`` boolean (PART XIX).
_TYPE_RANK = {
    AttentionType.FYI: 0,
    AttentionType.ACTION_REQUIRED: 1,
    AttentionType.DECISION_REQUIRED: 2,
}

# --------------------------------------------------------------------------- #
# Quoted-history stripping (PART XVI)
# --------------------------------------------------------------------------- #

_QUOTE_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*>"),                                   # "> quoted"
    re.compile(r"^\s*on\b.*\bwrote:\s*$", re.IGNORECASE),   # "On ... wrote:"
    re.compile(r"^\s*-{3,}\s*original message\s*-{3,}\s*$", re.IGNORECASE),
    re.compile(r"^\s*from:\s", re.IGNORECASE),
    re.compile(r"^\s*sent:\s", re.IGNORECASE),
    re.compile(r"^\s*w dniu\b.*napisa\u0142(a)?:?", re.IGNORECASE),  # "W dniu ... napisał:"
    re.compile(r"^\s*od:\s", re.IGNORECASE),
    re.compile(r"^\s*wys\u0142ano:\s", re.IGNORECASE),
)

# Inline history separators produced by A02's whitespace-collapsing HTML
# fallback (PART 6/7 of the remediation): structured markers only - each
# pattern requires a preceding separator, header syntax and/or the distinctive
# "wrote:" tail, so ordinary sentences containing "on"/"from" survive.
_INLINE_HISTORY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\s+on\b.{1,140}?\bwrote:", re.IGNORECASE),                # " On ... wrote:"
    re.compile(r"\s*-{3,}\s*original message\s*-{3,}", re.IGNORECASE),     # inline -----Original Message-----
    re.compile(r"\s+w dniu\b.{1,140}?napisa\u0142(a)?:?", re.IGNORECASE),  # " W dniu ... napisał:"
    re.compile(r"\s+from:\s+\S+@\S+", re.IGNORECASE),                      # " From: addr@host"
    re.compile(r"\s+od:\s+\S+@\S+", re.IGNORECASE),                        # " Od: addr@host"
    re.compile(r"\s+sent:\s+\d", re.IGNORECASE),                           # " Sent: 21..." (date-shaped)
    re.compile(r"\s+wys\u0142ano:\s+\d", re.IGNORECASE),                   # " Wysłano: 21..."
)


def current_message_text(body: str | None) -> str:
    """The CURRENT message portion: everything before the first recognized
    quote/history marker - line-based first, then conservative INLINE markers
    (normalized HTML replies collapse history onto one line).

    Conservative by design: truncation at the FIRST reliable marker. A false
    negative may discard current evidence (safe direction); old quoted
    evidence must NEVER be promoted to current financial evidence. Ordinary
    sentences containing bare words like "on" or "from" are untouched -
    every inline pattern needs header/colon structure."""
    if not body:
        return ""
    lines: list[str] = []
    for line in body.splitlines():
        if any(pattern.match(line) for pattern in _QUOTE_LINE_PATTERNS):
            break
        lines.append(line)
    text = "\n".join(lines)

    cut = len(text)
    for pattern in _INLINE_HISTORY_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            cut = min(cut, match.start())
    return text[:cut].strip()


# --------------------------------------------------------------------------- #
# Subject current/history classification (remediation PART 2-5)
# --------------------------------------------------------------------------- #

_REPLY_FORWARD_PREFIX_RE = re.compile(
    r"^\s*((re|fw|fwd|odp|pd|przek)\s*:\s*)+", re.IGNORECASE
)


def is_thread_history_subject(subject: str | None) -> bool:
    """True when the subject carries reply/forward prefix(es): its content is
    thread HISTORY, not a current request (e.g. 'Re: Re: Fwd: Please approve')."""
    return bool(subject) and _REPLY_FORWARD_PREFIX_RE.match(subject) is not None


def current_subject_text(subject: str | None) -> str:
    """Subject text usable as CURRENT evidence: '' for reply/forward subjects,
    the original otherwise. Purely classification-side - the stored subject
    and the AttentionItem title are never mutated."""
    if is_thread_history_subject(subject):
        return ""
    return subject or ""


# --------------------------------------------------------------------------- #
# PLN parsing - integer/Decimal-safe, never binary floats (PART XIII)
# --------------------------------------------------------------------------- #

_PLN_TOKEN_RE = re.compile(
    r"(?<![\d.,\u00A0])(\d[\d., \u00A0]*\d|\d)[ \u00A0]*(?:PLN|z\u0142)(?![A-Za-z\u0100-\u017f])",
    re.IGNORECASE,
)
_UNSUPPORTED_CURRENCY_RE = re.compile(
    r"(?<![\d.,\u00A0])(\d[\d., \u00A0]*\d|\d)[ \u00A0]*(?:EUR|USD|GBP|BTC|\u20AC|\u00a3|\$)(?![A-Za-z])",
)


def parse_pln_amount(number_text: str) -> int | None:
    """Parse a Polish-convention PLN amount into integer minor units (grosze).

    Deterministic interpretation:
      - comma + exactly 2 trailing digits  -> decimal separator;
      - comma + exactly 3 trailing digits and no other separator -> thousands
        grouping ("12,400" == 12400);
      - spaces/NBSP group thousands in 3s; dots group thousands in 3s
        ("1.000,01"); anything else is REJECTED (None) - never invented.

    Supported: 12,400 PLN / 12 400 PLN / 12\u00a0400 PLN / 12 400,50 PLN /
    1.000,01 PLN / 1000,01 PLN / 1000 PLN / z\u0142 variants.
    """
    text = number_text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if not text or not re.fullmatch(r"[\d., ]+", text):
        return None

    int_part = text
    fraction = ""
    comma_count = text.count(",")
    if comma_count > 1:
        return None
    if comma_count == 1:
        int_part, _, fraction = text.partition(",")
        if fraction.isdigit() and len(fraction) == 2:
            pass  # decimal separator
        elif fraction.isdigit() and len(fraction) == 3 and "." not in int_part and " " not in int_part:
            int_part = int_part + fraction  # thousands grouping
            fraction = ""
        else:
            return None

    if " " in int_part:
        if not re.fullmatch(r"\d{1,3}( \d{3})*", int_part):
            return None
        int_part = int_part.replace(" ", "")
    if "." in int_part:
        if not re.fullmatch(r"\d{1,3}(\.\d{3})*", int_part):
            return None
        int_part = int_part.replace(".", "")
    if not int_part.isdigit():
        return None

    return int(int_part) * 100 + (int(fraction) if fraction else 0)


def find_pln_amounts(text: str) -> list[int]:
    """All parseable PLN amounts in ``text`` as minor units. Malformed or
    ambiguous tokens are skipped, never guessed."""
    amounts: list[int] = []
    for match in _PLN_TOKEN_RE.finditer(text or ""):
        parsed = parse_pln_amount(match.group(1))
        if parsed is not None:
            amounts.append(parsed)
    return amounts


def has_unsupported_currency_amount(text: str) -> bool:
    return _UNSUPPORTED_CURRENCY_RE.search(text or "") is not None


# --------------------------------------------------------------------------- #
# Rule result (internal - NOT a canonical contract, PART XI)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AttentionRuleResult:
    """Deterministic outcome for one NormalizedSourceEvent.

    ``priority_floor`` and ``attention_type`` are HARD floors: any later
    (e.g. LLM) classification may only strengthen them - see
    :func:`clamp_to_rule_floor`."""

    attention_type: AttentionType
    priority_floor: AttentionPriority
    urgent: bool
    confidence: float
    reasons: tuple[Reason, ...]
    money: Money | None = None
    financial_decision_intent: bool = False
    deadline: datetime | None = None  # A06 never invents deadlines


def clamp_to_rule_floor(
    floor: AttentionRuleResult,
    proposed_priority: AttentionPriority,
    proposed_type: AttentionType | None = None,
) -> tuple[AttentionPriority, AttentionType]:
    """Enforcement helper for B's optional LLM ambiguity classifier.

    The final classification is ALWAYS at least the deterministic floor:
    a weaker proposal (HIGH financial -> LOW/MEDIUM, decision_required -> FYI,
    MEDIUM sender floor -> LOW) is clamped back up. AttentionType.URGENT is
    not an accepted proposal here - urgency is a boolean, never a type."""
    priority = proposed_priority
    if _PRIORITY_RANK[priority] < _PRIORITY_RANK[floor.priority_floor]:
        priority = floor.priority_floor
    result_type = floor.attention_type
    if (
        proposed_type is not None
        and proposed_type is not AttentionType.URGENT
        and _TYPE_RANK.get(proposed_type, 0) > _TYPE_RANK[floor.attention_type]
    ):
        result_type = proposed_type
    return priority, result_type


# --------------------------------------------------------------------------- #
# The rules engine
# --------------------------------------------------------------------------- #


class AttentionRules:
    """Deterministic Gmail-evidence rules for one application.

    ``important_senders`` is an EXACT normalized-address set (case-folded).
    Display names, signatures and body text NEVER establish sender identity -
    only NormalizedSourceEvent.sender_email (parsed From address) does.
    The financial HIGH threshold is INJECTED from the loaded A03 policy -
    never duplicated here."""

    def __init__(
        self,
        *,
        policy: LoadedPolicy,
        important_senders: Iterable[str] = (),
    ) -> None:
        financial = policy.config.financial_high
        self._threshold_minor_units = financial.threshold_minor_units
        self._currency = financial.currency  # policy v1: exactly PLN
        self._policy_version = policy.version
        self._important_senders = frozenset(
            address.strip().lower() for address in important_senders if address.strip()
        )

    @property
    def financial_threshold_minor_units(self) -> int:
        return self._threshold_minor_units

    # ------------------------------------------------------------------ #
    def evaluate(self, event: NormalizedSourceEvent) -> AttentionRuleResult:
        """Classify ONE message from its CURRENT evidence only (PART XXXVI).

        Current evidence = current body text (quotes stripped) + the subject
        ONLY when it is not a reply/forward thread-history subject. Sender
        identity is independent of subject filtering; newsletter markers may
        additionally use the original subject (not authorization-sensitive)."""
        current_body = current_message_text(event.body)
        current_subject = current_subject_text(event.subject)
        evidence_text = f"{current_subject}\n{current_body}"
        haystack = evidence_text.lower()
        source_ids = [ref.id for ref in event.sources]

        reasons: list[Reason] = []
        money: Money | None = None
        floor = AttentionPriority.LOW
        result_type = AttentionType.FYI
        urgent = False
        confidence = 0.5

        def add(code: str, text: str, *, conf: float, with_policy: bool = False) -> None:
            nonlocal confidence
            reasons.append(
                Reason(
                    code=code,
                    origin=ReasonOrigin.RULE,
                    text=text,
                    source_ids=list(source_ids),
                    policy_version=self._policy_version if with_policy else None,
                )
            )
            confidence = max(confidence, conf)

        intent = any(phrase in haystack for phrase in DECISION_INTENT_PHRASES)
        action = any(phrase in haystack for phrase in ACTION_REQUEST_PHRASES)
        amounts = find_pln_amounts(evidence_text)

        if intent and amounts:
            # Current decision intent + current PLN amount: the money carried
            # is the LARGEST current amount (most conservative liability).
            minor = max(amounts)
            money = Money(amount_minor_units=minor, currency=self._currency)
            result_type = AttentionType.DECISION_REQUIRED
            if minor >= self._threshold_minor_units:
                floor = AttentionPriority.HIGH
                add(
                    CODE_FINANCIAL_HIGH,
                    "current message requests a financial decision at or above the "
                    "authoritative policy threshold",
                    conf=0.95,
                    with_policy=True,
                )
            else:
                if floor is AttentionPriority.LOW:
                    floor = AttentionPriority.MEDIUM
                add(
                    CODE_FINANCIAL_BELOW_THRESHOLD,
                    "current message requests a financial decision below the policy "
                    "HIGH threshold",
                    conf=0.85,
                    with_policy=True,
                )
                add(CODE_DECISION_REQUIRED, "current message requests a decision", conf=0.75)
        elif intent and has_unsupported_currency_amount(evidence_text):
            # Cautious classification for review; NO conversion, NO threshold.
            result_type = AttentionType.DECISION_REQUIRED
            if floor is AttentionPriority.LOW:
                floor = AttentionPriority.MEDIUM
            add(
                CODE_UNSUPPORTED_CURRENCY,
                "current message requests a decision in an unsupported currency; "
                "no conversion or threshold rule was applied",
                conf=0.7,
            )
        elif intent:
            result_type = AttentionType.DECISION_REQUIRED
            if floor is AttentionPriority.LOW:
                floor = AttentionPriority.MEDIUM
            add(CODE_DECISION_REQUIRED, "current message requests a decision", conf=0.75)
        elif amounts:
            add(
                CODE_AMOUNT_WITHOUT_INTENT,
                "a supported amount appears without current decision intent; no "
                "financial threshold rule was applied",
                conf=0.6,
            )

        if action and _TYPE_RANK[AttentionType.ACTION_REQUIRED] > _TYPE_RANK[result_type]:
            result_type = AttentionType.ACTION_REQUIRED
        if action:
            add(CODE_ACTION_REQUIRED, "current message requests an action", conf=0.7)
        if action and floor is AttentionPriority.LOW:
            floor = AttentionPriority.MEDIUM

        if _URGENT_RE.search(haystack):
            urgent = True
            add(
                CODE_URGENT,
                "current message contains an explicit urgency marker",
                conf=0.85,
            )
            # Urgency raises the floor: HIGH when attached to a real current
            # request, MEDIUM on its own. It never lowers anything.
            if result_type is not AttentionType.FYI:
                floor = AttentionPriority.HIGH
            elif _PRIORITY_RANK[floor] < _PRIORITY_RANK[AttentionPriority.MEDIUM]:
                floor = AttentionPriority.MEDIUM

        if event.sender_email.lower() in self._important_senders:
            # Exact-address identity only; NEVER a downgrade of stronger rules.
            if _PRIORITY_RANK[floor] < _PRIORITY_RANK[AttentionPriority.MEDIUM]:
                floor = AttentionPriority.MEDIUM
            add(
                CODE_IMPORTANT_SENDER,
                "sender address exactly matches a configured important sender",
                conf=0.9,
            )

        # Newsletter detection may ALSO see the original subject: a recurring
        # newsletter subject is not an authorization-sensitive current request.
        newsletter_haystack = f"{event.subject or ''}\n{current_body}".lower()
        newsletter_hit = any(marker in newsletter_haystack for marker in NEWSLETTER_MARKERS)
        stronger = bool(reasons) and (
            result_type is not AttentionType.FYI
            or _PRIORITY_RANK[floor] > _PRIORITY_RANK[AttentionPriority.LOW]
            or urgent
        )
        if newsletter_hit and not stronger:
            # LOW/FYI by default; can never lower a stronger rule (checked above).
            add(CODE_NEWSLETTER, "content matches newsletter/marketing markers", conf=0.6)

        if not reasons:
            add(CODE_FYI_DEFAULT, "no stronger deterministic evidence found", conf=0.5)

        return AttentionRuleResult(
            attention_type=result_type,
            priority_floor=floor,
            urgent=urgent,
            confidence=min(confidence, 1.0),
            reasons=tuple(reasons),
            money=money,
            financial_decision_intent=intent and money is not None,
            deadline=None,
        )
