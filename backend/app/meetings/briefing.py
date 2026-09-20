"""Grounded meeting briefing service (B03).

Evidence model: the Meeting (trusted fetch via A02) plus related stored
Attention items. The LLM - when a self-hosted route is configured - only
AUTHORS TEXT against a strict schema and may cite ONLY source ids that this
service handed it; every returned claim is re-validated, claims of kind
"fact" without resolvable sources are dropped, and the frozen
ExecutiveBriefing validator runs last as the integrity gate.

When inference is unavailable or its output cannot be grounded, EVA produces
a DETERMINISTIC briefing from the meeting itself - honest retrieval notes
say so; no invented history, no silent failure."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Callable

from ..contracts.domain import (
    Claim,
    ClaimKind,
    ExecutiveBriefing,
    Language,
    Meeting,
    MeetingRef,
    RetrievalStatus,
    SourceRef,
)
from ..contracts.providers import ChatMessage, ChatRole

logger = logging.getLogger("eva.meetings.briefing")


def briefing_evidence_block(meeting: Meeting, evidence_sources: list[SourceRef], language):
    """Prompt block presenting the meeting + evidence as labeled untrusted data.

    The instructions wrapping it come from agent.prompts; source ids shown
    here are exactly the ids the model is allowed to cite."""
    pl = language is Language.PL
    lines = [
        (
            f"SPOTKANIE: „{meeting.title}”, start {meeting.span.start.isoformat()}"
            if pl else
            f"MEETING: “{meeting.title}”, starts {meeting.span.start.isoformat()}"
        )
    ]
    if meeting.description:
        lines.append(f"description: {meeting.description[:2000]}")
    for p in meeting.attendees[:10]:
        lines.append(f"attendee: {p.name or ''} <{p.email or '?'}>")
    if evidence_sources:
        header = "DOWODY (używaj wyłącznie tych id źródeł):" if pl else \
            "EVIDENCE (cite ONLY these source ids):"
        lines.append(header)
        for source in evidence_sources[:20]:
            title = " ".join((source.title or "").split())[:160]
            lines.append(f"- [{source.id}] {source.kind.value}: {title}")
    return "\n".join(lines)

_CLAIM_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "kind": {"enum": [k.value for k in ClaimKind]},
        "source_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["text", "kind"],
}

BRIEFING_SCHEMA = {
    "type": "object",
    "properties": {
        "previous_interactions": {"type": "array", "items": _CLAIM_SCHEMA},
        "open_topics": {"type": "array", "items": _CLAIM_SCHEMA},
        "previous_decisions": {"type": "array", "items": _CLAIM_SCHEMA},
        "risks": {"type": "array", "items": _CLAIM_SCHEMA},
        "suggestions": {"type": "array", "items": _CLAIM_SCHEMA},
        "spoken_summary": {"type": "string"},
    },
    "required": ["spoken_summary"],
}


class BriefingService:
    def __init__(
        self,
        *,
        llm_router_provider: Callable[[], object],
        calendar_service_factory: Callable[[], object],
        attention_repo,
        clock: Callable[[], datetime] | None = None,
        outbox=None,
    ) -> None:
        self._router_provider = llm_router_provider
        self._calendar_factory = calendar_service_factory
        self._attention_repo = attention_repo
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._outbox = outbox

    # ------------------------------------------------------------------ #
    async def generate(self, ref: MeetingRef, language: Language) -> ExecutiveBriefing:
        # The Google read is blocking; keep the event loop free.
        meeting: Meeting = await asyncio.to_thread(
            lambda: self._calendar_factory().get_event(  # type: ignore[attr-defined]
                calendar_id=ref.calendar_id, event_id=ref.event_id
            )
        )
        related_items, evidence_sources = self._related_evidence(meeting)

        router = self._router_provider()
        llm_available = (
            getattr(router, "primary", None) is not None
            or getattr(router, "fallback", None) is not None
        )
        if llm_available:
            briefing = await self._llm_briefing(router, meeting, language, evidence_sources)
            if briefing is not None:
                return briefing

        return self._deterministic_briefing(meeting, language, evidence_sources)

    # ------------------------------------------------------------------ #
    def _related_evidence(self, meeting: Meeting):
        """Stored Attention items that reference THIS calendar event id."""
        sources: list[SourceRef] = list(meeting.sources)
        items = []
        try:
            recent = self._attention_repo.list_latest(limit=100)
        except Exception as exc:  # evidence is best-effort
            logger.error("attention evidence unavailable (%s)", type(exc).__name__)
            return [], sources
        for item in recent:
            if any(
                s.resource_id == meeting.ref.event_id
                or s.kind.value == "calendar_event" and meeting.ref.event_id in s.resource_id
                for s in item.sources
            ):
                items.append(item)
                sources.extend(item.sources)
        return items[:10], sources

    # ------------------------------------------------------------------ #
    async def _llm_briefing(self, router, meeting: Meeting, language, evidence_sources):
        from ..agent.prompts import briefing_instructions

        valid_ids = {s.id for s in evidence_sources} | {s.id for s in meeting.sources}
        block = briefing_evidence_block(meeting, evidence_sources, language)
        try:
            response = await router.chat(  # type: ignore[attr-defined]
                [
                    ChatMessage(role=ChatRole.SYSTEM, content=briefing_instructions(language)),
                    ChatMessage(role=ChatRole.USER, content=block),
                ],
                response_schema=BRIEFING_SCHEMA,
            )
        except Exception as exc:  # inference outage -> deterministic fallback
            logger.warning("briefing LLM unavailable (%s); deterministic briefing", type(exc).__name__)
            return None

        data = getattr(response, "structured", None)
        if not isinstance(data, dict):
            return None
        spoken = str(data.get("spoken_summary") or "").strip()
        if not spoken:
            return None

        claims_fields = (
            "previous_interactions", "open_topics", "previous_decisions",
            "risks", "suggestions",
        )
        parsed: dict[str, list[Claim]] = {}
        for field in claims_fields:
            parsed[field] = self._sanitize_claims(data.get(field), valid_ids)

        try:
            return ExecutiveBriefing(
                id=f"briefing-{uuid.uuid4()}",
                meeting=meeting,
                language=language,
                generated_at=self._clock(),
                previous_interactions=parsed["previous_interactions"],
                open_topics=parsed["open_topics"],
                previous_decisions=parsed["previous_decisions"],
                risks=parsed["risks"],
                suggestions=parsed["suggestions"],
                spoken_summary=spoken,
                sources=list(evidence_sources),
                retrieval_status=RetrievalStatus.COMPLETE,
                retrieval_notes=["briefing authored by self-hosted inference; "
                                 "facts re-validated against provided source ids"],
            )
        except Exception as exc:  # ValidationError etc. -> never ship ungrounded text
            logger.error("briefing failed grounding validation (%s); deterministic briefing",
                         type(exc).__name__)
            return None

    @staticmethod
    def _sanitize_claims(raw, valid_ids: set[str]) -> list[Claim]:
        claims: list[Claim] = []
        if not isinstance(raw, list):
            return claims
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("text") or "").strip()
            kind_raw = str(entry.get("kind") or "")
            if not text or kind_raw not in {k.value for k in ClaimKind}:
                continue
            source_ids = [
                s for s in (entry.get("source_ids") or [])
                if isinstance(s, str) and s in valid_ids
            ]
            try:
                claim = Claim(text=text[:600], kind=ClaimKind(kind_raw), source_ids=source_ids)
            except Exception:  # fact without sources -> dropped, not laundered
                continue
            claims.append(claim)
        return claims[:8]

    # ------------------------------------------------------------------ #
    def _deterministic_briefing(self, meeting, language, evidence_sources) -> ExecutiveBriefing:
        pl = language is Language.PL
        meeting_source_ids = [s.id for s in meeting.sources]
        facts: list[Claim] = []
        if meeting_source_ids:
            attendees = ", ".join(
                p.email or (p.name or "") for p in meeting.attendees[:6]
            )
            facts.append(Claim(
                text=(
                    f"Spotkanie „{meeting.title}” zaplanowane na "
                    f"{meeting.span.start.isoformat()}"
                    + (f"; uczestnicy: {attendees}" if attendees else "")
                ) if pl else (
                    f"Meeting “{meeting.title}” scheduled at "
                    f"{meeting.span.start.isoformat()}"
                    + (f"; attendees: {attendees}" if attendees else "")
                ),
                kind=ClaimKind.FACT,
                source_ids=meeting_source_ids[:3],
            ))
        spoken = (
            f"Spotkanie: {meeting.title}, start {meeting.span.start.strftime('%H:%M')}. "
            "Autonomiczny briefing wygenerowany bez modelu - szczegóły w opisie spotkania."
            if pl else
            f"Meeting: {meeting.title}, starting {meeting.span.start.strftime('%H:%M')}. "
            "Deterministic briefing generated without inference - see the event description."
        )
        return ExecutiveBriefing(
            id=f"briefing-{uuid.uuid4()}",
            meeting=meeting,
            language=language,
            generated_at=self._clock(),
            open_topics=facts,
            spoken_summary=spoken.strip(),
            sources=list(evidence_sources),
            retrieval_status=RetrievalStatus.COMPLETE,
            retrieval_notes=[
                "self-hosted inference unavailable or ungrounded; deterministic briefing"
            ],
        )
