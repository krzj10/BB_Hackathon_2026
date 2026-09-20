"""Trusted-context assembly for the executive agent (B03).

The builder is the ONLY place EVA turns stored state into prompt text. Every
snippet is labeled with its source id so claims can cite real evidence, and
every fetched block is wrapped as untrusted data by the prompt layer. Nothing
here authorizes anything: Decisions arrive as neutral inbox facts, focus as a
plain status line, meeting content as evidence - the model cannot promote any
of it into permission."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from ..contracts.domain import (
    ActiveContext,
    ActiveContextMode,
    AttentionItem,
    Decision,
    DecisionStatus,
    Language,
    SourceRef,
)

logger = logging.getLogger("eva.agent.context")

MAX_ATTENTION_ITEMS = 5
MAX_DECISIONS = 3
SNIPPET_CHARS = 240


@dataclass(frozen=True)
class ContextBundle:
    text: str
    sources: list[SourceRef] = field(default_factory=list)


def _snippet(text: str) -> str:
    clean = " ".join((text or "").split())
    return clean[:SNIPPET_CHARS] + ("…" if len(clean) > SNIPPET_CHARS else "")


def _attention_lines(items: list[AttentionItem], language: Language) -> list[str]:
    if not items:
        return []
    header = "Ostatnie sprawy (mail):" if language is Language.PL else "Recent attention items:"
    lines = [header]
    for item in items[:MAX_ATTENTION_ITEMS]:
        src = item.sources[0].id if item.sources else f"gmail:{item.source_id}"
        lines.append(
            f"- [{src}] {item.sender_email} — \"{_snippet(item.title)}\" "
            f"({item.priority.value}, {item.attention_type.value})"
        )
    return lines


def _decision_lines(decisions: list[Decision], language: Language) -> list[str]:
    open_items = [d for d in decisions if d.status is not DecisionStatus.RESOLVED]
    if not open_items:
        return []
    header = (
        "Otwarte decyzje w skrzynce (tylko do odczytu):"
        if language is Language.PL
        else "Open items in the decision inbox (read-only context):"
    )
    lines = [header]
    for decision in open_items[:MAX_DECISIONS]:
        money = (
            f" kwota {decision.money.amount_minor_units / 100:.2f} {decision.money.currency};"
            if language is Language.PL and decision.money
            else (
                f" amount {decision.money.amount_minor_units / 100:.2f}"
                f" {decision.money.currency};"
                if decision.money
                else ""
            )
        )
        lines.append(
            f"- [{decision.id}] {_snippet(decision.title)}; status: "
            f"{decision.status.value};{money} recorded outcome executes NOTHING external"
        )
    return lines


def build_context_block(
    *,
    language: Language,
    active_context: ActiveContext | None,
    attention_repo,
    decision_repo,
    focus_service,
    calendar_service_factory=None,
    now: datetime | None = None,
) -> ContextBundle:
    parts: list[str] = []
    sources: list[SourceRef] = []

    if active_context is not None and active_context.mode is ActiveContextMode.MEETING:
        ref = active_context.meeting
        if ref is not None and calendar_service_factory is not None:
            try:
                meeting = calendar_service_factory().get_event(
                    calendar_id=ref.calendar_id, event_id=ref.event_id
                )
            except Exception as exc:  # sanitized: context degrades, request proceeds
                logger.error("meeting context unavailable (%s)", type(exc).__name__)
                parts.append(
                    "Kontekst spotkania: niedostępny."
                    if language is Language.PL
                    else "Meeting context: unavailable right now."
                )
            else:
                label = (
                    "Aktywne spotkanie" if language is Language.PL else "Active meeting"
                )
                attendees = ", ".join(
                    p.email or (p.name or "") for p in meeting.attendees[:8]
                )
                parts.append(
                    f"{label}: \"{meeting.title}\"; start {meeting.span.start.isoformat()}; "
                    f"attendees: {attendees or 'brak/none'}; "
                    f"description: {_snippet(meeting.description or '')}"
                )
                sources.extend(meeting.sources)
        else:
            parts.append(
                "Kontekst spotkania: wskazano spotkanie bez możliwości pobrania."
                if language is Language.PL
                else "Meeting context: referenced but not fetchable here."
            )

    focus = focus_service.current(now) if focus_service is not None else None
    if focus is not None:
        parts.append(
            (
                f"Aktywny tryb skupienia do {focus.ends_at.isoformat()} "
                f"(próg: {focus.threshold.value})."
            )
            if language is Language.PL
            else (
                f"Focus session active until {focus.ends_at.isoformat()} "
                f"(threshold {focus.threshold.value})."
            )
        )

    try:
        parts.extend(_attention_lines(attention_repo.list_latest(), language))
        parts.extend(_decision_lines(decision_repo.list_all(), language))
    except Exception as exc:  # context is best-effort, never fatal
        logger.error("inbox context unavailable (%s)", type(exc).__name__)

    return ContextBundle(text="\n".join(parts), sources=sources)
