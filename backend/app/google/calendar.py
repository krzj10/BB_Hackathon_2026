"""Read-only Google Calendar access normalized to frozen A00 contracts (A02).

Normalization rules (deterministic; no live Google needed for tests):

- timed events -> TimedSpan with the provider's IANA timezone preserved and
  aware start/end datetimes (Google's offset-bearing ISO strings are parsed,
  never re-based to host-local time);
- all-day events -> AllDaySpan. Real Google all-day payloads carry
  ``{"start": {"date": ...}, "end": {"date": ...}}`` and Google's ``end.date``
  is ALREADY the exclusive end date: it maps directly onto
  ``end_exclusive`` with no reinterpretation. A missing or mixed
  (date/dateTime) span is rejected as malformed, never invented; all-day
  events are NEVER converted to timed;
- organizer email, ETag (resource version), recurringEventId, description and
  location are preserved; attendee counts that Google does not return stay
  null (never invented zeros);
- every meeting carries a SourceRef with calendar/event resource ids and the
  retrieval timestamp (provenance).

Priority: A02 performs no triage (A03 owns it). Read meetings carry an
explicit ``untriaged`` marker reason so downstream surfaces cannot mistake the
placeholder MEDIUM floor for policy output.

Only GET requests are issued here; mutations belong to A04's guarded
executor. The HTTP transport is injected, so Google SDK objects never leak
and tests run hermetically against synthetic payloads.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from ..contracts.domain import (
    AgendaSectionMode,
    AllDaySpan,
    CalendarCreateEventArguments,
    CalendarRescheduleEventArguments,
    Meeting,
    MeetingPriority,
    MeetingRef,
    Participant,
    Reason,
    ReasonOrigin,
    RetrievalStatus,
    SendUpdates,
    SourceKind,
    SourceRef,
    TimedSpan,
)
from .http import GoogleHttp

logger = logging.getLogger("eva.google.calendar")

CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"
#: Placeholder priority for untriaged reads; always paired with the marker
#: reason below so it is never mistaken for A03 policy output.
UNTRIAGED_PRIORITY = MeetingPriority.MEDIUM
UNTRIAGED_REASON_CODE = "untriaged_read_default"


class CalendarReadError(RuntimeError):
    """Sanitized read failure (status codes and ids only)."""


def _untriaged_reason() -> Reason:
    return Reason(
        code=UNTRIAGED_REASON_CODE,
        origin=ReasonOrigin.RULE,
        text=(
            "Calendar read path assigns no priority; A03 policy triage is the "
            "authoritative source. This floor is a placeholder."
        ),
        policy_version="none-untriaged",
    )


def _parse_aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError(f"calendar timestamp without offset: {value!r}")
    return parsed


def normalize_event(calendar_id: str, raw: dict[str, Any], retrieved_at: datetime) -> Meeting:
    """Convert one Google event payload into the frozen Meeting contract.

    Raises ValueError for payloads that cannot be normalized faithfully (e.g.
    mixed timed/all-day spans); callers report skipped events honestly."""
    event_id = raw.get("id")
    if not event_id:
        raise ValueError("event is missing id")
    start, end = raw.get("start"), raw.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        raise ValueError(f"event {event_id!r} is missing start/end")

    span: TimedSpan | AllDaySpan
    if "dateTime" in start and "dateTime" in end:
        tz = start.get("timeZone") or end.get("timeZone") or "Etc/UTC"
        span = TimedSpan(
            start=_parse_aware(start["dateTime"]),
            end=_parse_aware(end["dateTime"]),
            timezone=tz,
        )
    elif (
        "date" in start
        and "dateTime" not in start
        and "dateTime" not in end
        and "date" in end
    ):
        # Real Google all-day shape: {"start": {"date": ...}, "end": {"date":
        # ...}}. Google's end.date is ALREADY the exclusive end date, so it
        # maps straight onto end_exclusive - never reinterpreted or shifted.
        span = AllDaySpan(
            start_date=date.fromisoformat(start["date"]),
            end_exclusive=date.fromisoformat(end["date"]),
        )
    else:
        # Missing ends and mixed timed/all-day representations are rejected
        # (reported as honest partial retrieval by callers), never invented.
        raise ValueError(f"event {event_id!r} has an unsupported span representation")

    organizer = raw.get("organizer") or {}
    attendees = [
        Participant(
            email=att["email"],
            name=att.get("displayName"),
        )
        for att in (raw.get("attendees") or [])
        if isinstance(att, dict) and att.get("email")
    ]
    title = raw.get("summary") or "Untitled event"

    return Meeting(
        ref=MeetingRef(calendar_id=calendar_id, event_id=event_id),
        etag=raw.get("etag"),
        title=title,
        span=span,
        attendees=attendees,
        organizer_email=organizer.get("email") if isinstance(organizer, dict) else None,
        editable=bool(raw.get("editable", False)),
        recurring_event_id=raw.get("recurringEventId"),
        description=raw.get("description"),
        location=raw.get("location"),
        priority=UNTRIAGED_PRIORITY,
        priority_reasons=[_untriaged_reason()],
        sources=[
            SourceRef(
                id=f"cal:{calendar_id}:{event_id}",
                kind=SourceKind.CALENDAR_EVENT,
                resource_id=event_id,
                title=title,
                url=raw.get("htmlLink"),
                retrieved_at=retrieved_at,
            )
        ],
    )


@dataclass
class EventPage:
    meetings: list[Meeting] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    next_page_token: str | None = None


class CalendarService:
    """Read-only Calendar API surface (list/get). Writes are A04 territory."""

    def __init__(self, http: GoogleHttp, *, timezone_name: str = "Europe/Warsaw") -> None:
        self._http = http
        self._timezone_name = timezone_name

    # -- low-level pages -------------------------------------------------------

    def list_events_page(
        self,
        *,
        calendar_id: str = "primary",
        time_min: datetime | None = None,
        time_max: datetime | None = None,
        max_results: int = 50,
        page_token: str | None = None,
        retrieved_at: datetime | None = None,
    ) -> EventPage:
        retrieved_at = retrieved_at or datetime.now(timezone.utc)
        params: dict[str, Any] = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": max(1, min(max_results, 250)),
        }
        if time_min is not None:
            params["timeMin"] = time_min.isoformat()
        if time_max is not None:
            params["timeMax"] = time_max.isoformat()
        if page_token:
            params["pageToken"] = page_token

        payload = self._http.get_json(
            f"{CALENDAR_BASE}/calendars/{calendar_id}/events", params
        )
        page = EventPage(next_page_token=payload.get("nextPageToken"))
        for raw in payload.get("items") or []:
            try:
                page.meetings.append(normalize_event(calendar_id, raw, retrieved_at))
            except (ValueError, KeyError) as exc:  # honest partial retrieval
                page.notes.append(f"skipped malformed event: {exc}")
                logger.warning("calendar: skipped malformed event in %s", calendar_id)
        return page

    def list_events(
        self,
        *,
        calendar_id: str = "primary",
        time_min: datetime | None = None,
        time_max: datetime | None = None,
        max_results: int = 50,
        max_pages: int = 10,
    ) -> tuple[list[Meeting], RetrievalStatus, list[str]]:
        """Bounded listing: never more than ``max_results`` meetings across at
        most ``max_pages`` pages."""
        meetings: list[Meeting] = []
        notes: list[str] = []
        status = RetrievalStatus.COMPLETE
        token: str | None = None
        for _ in range(max(1, max_pages)):
            page = self.list_events_page(
                calendar_id=calendar_id,
                time_min=time_min,
                time_max=time_max,
                max_results=min(max_results - len(meetings), 250),
                page_token=token,
            )
            meetings.extend(page.meetings[: max_results - len(meetings)])
            notes.extend(page.notes)
            if page.notes:
                status = RetrievalStatus.PARTIAL
            token = page.next_page_token
            if token is None or len(meetings) >= max_results:
                break
        # A non-null token after the loop means more pages existed but we
        # stopped at the result limit or page budget: report it honestly.
        if token is not None:
            status = RetrievalStatus.PARTIAL
            notes.append("listing stopped at the requested result/page budget; results are partial")
        return meetings, status, notes

    def get_event(self, *, calendar_id: str = "primary", event_id: str) -> Meeting:
        payload = self._http.get_json(
            f"{CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}", None
        )
        return normalize_event(calendar_id, payload, datetime.now(timezone.utc))

    # -- today view ---------------------------------------------------------------

    def day_bounds(self, day: date | None = None) -> tuple[datetime, datetime]:
        """Aware [midnight, next-midnight) bounds in the configured timezone
        (Europe/Warsaw default; DST-correct via zoneinfo)."""
        tz = ZoneInfo(self._timezone_name)
        anchor = day or datetime.now(tz).date()
        start = datetime(anchor.year, anchor.month, anchor.day, tzinfo=tz)
        return start, start + timedelta(days=1)

    def today(self, *, calendar_id: str = "primary", day: date | None = None) -> Any:
        from ..contracts.api import TodayCalendarResponse

        start, end = self.day_bounds(day)
        meetings, status, notes = self.list_events(
            calendar_id=calendar_id, time_min=start, time_max=end
        )
        return TodayCalendarResponse(
            day=start.date(),
            timezone=self._timezone_name,
            meetings=meetings,
            retrieval_status=status,
            retrieval_notes=notes,
        )

    # ------------------------------------------------------------------ #
    # A04 guarded mutation adapters (INTERNAL: only the ToolExecutor may
    # call these - never an API route or any other surface). Every mutation
    # performs a GET read-back through normalize_event() so a success that
    # cannot be verified is reported by the executor as UNKNOWN, not OK.
    # ------------------------------------------------------------------ #

    def create_event(
        self,
        *,
        args: CalendarCreateEventArguments,
        google_event_id: str,
        calendar_id: str = "primary",
    ) -> Meeting:
        """POST a new event with the DURABLE client-generated event id (the
        reconciliation anchor). sendUpdates is passed exactly as approved."""
        body = create_event_body(args)
        payload = self._http.post_json(
            f"{CALENDAR_BASE}/calendars/{calendar_id}/events",
            {"eventId": google_event_id, "sendUpdates": google_send_updates(args.send_updates)},
            body,
        )
        # Read-back through the SAME normalization path as reads (LVI/LVIII):
        # trust the GET, not only the POST response.
        event_id = payload.get("id") or google_event_id
        return self.get_event(calendar_id=calendar_id, event_id=event_id)

    def reschedule_event(
        self,
        *,
        args: CalendarRescheduleEventArguments,
        if_match: str,
        calendar_id: str = "primary",
    ) -> Meeting:
        """PATCH only the span fields with If-Match on the approved ETag.
        Nothing else on the event is touched."""
        start, end = span_payload(args.new_span)
        self._http.patch_json(
            f"{CALENDAR_BASE}/calendars/{calendar_id}/events/{args.ref.event_id}",
            {"sendUpdates": google_send_updates(args.send_updates)},
            {"start": start, "end": end},
            {"If-Match": if_match},
        )
        return self.get_event(calendar_id=calendar_id, event_id=args.ref.event_id)

    def update_agenda_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        new_description: str,
        send_updates: SendUpdates,
        if_match: str,
    ) -> Meeting:
        """PATCH only the description with the EVA section replaced; all other
        event data is untouched. If-Match enforces the approved snapshot."""
        self._http.patch_json(
            f"{CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}",
            {"sendUpdates": google_send_updates(send_updates)},
            {"description": new_description},
            {"If-Match": if_match},
        )
        return self.get_event(calendar_id=calendar_id, event_id=event_id)


# --------------------------------------------------------------------------- #
# Mutation payload helpers (pure, deterministic, unit-tested)
# --------------------------------------------------------------------------- #

#: Stable EVA agenda-section delimiters inside the event description. HTML
#: comments are invisible in calendar UIs, unambiguous to parse and never
#: invented for user text.
EVA_AGENDA_START = "<!-- EVA-AGENDA-BEGIN -->"
EVA_AGENDA_END = "<!-- EVA-AGENDA-END -->"


def google_send_updates(send: SendUpdates) -> str:
    """Canonical internal enum -> Google sendUpdates parameter."""
    return {
        SendUpdates.ALL: "all",
        SendUpdates.EXTERNAL_ONLY: "externalOnly",
        SendUpdates.NONE: "none",
    }[send]


def span_payload(span: TimedSpan | AllDaySpan) -> tuple[dict[str, str], dict[str, str]]:
    """Contract span -> Google start/end payload.

    Never converts between kinds: timed stays dateTime+timeZone, all-day
    stays date with the EXCLUSIVE end preserved exactly."""
    if isinstance(span, TimedSpan):
        return (
            {"dateTime": span.start.isoformat(), "timeZone": span.timezone},
            {"dateTime": span.end.isoformat(), "timeZone": span.timezone},
        )
    if isinstance(span, AllDaySpan):
        return (
            {"date": span.start_date.isoformat()},
            {"date": span.end_exclusive.isoformat()},  # Google's end.date is exclusive
        )
    raise ValueError("unsupported span representation")


def create_event_body(args: CalendarCreateEventArguments) -> dict[str, Any]:
    start, end = span_payload(args.span)
    body: dict[str, Any] = {
        "summary": args.title,
        "start": start,
        "end": end,
    }
    if args.description is not None:
        body["description"] = args.description
    if args.location is not None:
        body["location"] = args.location
    if args.attendees:
        # Approved canonical addresses only; the executor never re-adds or
        # reclassifies attendees here (Participant.internal stays non-authorization).
        body["attendees"] = [
            {"email": a.email, **({"displayName": a.name} if a.name else {})}
            for a in args.attendees
        ]
    return body


def render_agenda_section(markdown: str) -> str:
    return f"{EVA_AGENDA_START}\n{markdown.strip(chr(10))}\n{EVA_AGENDA_END}"


def _find_agenda_section(description: str) -> tuple[int, int] | None:
    """Locate the EVA section (begin..end inclusive). Returns None when no
    well-formed section exists; a stray END before any BEGIN is treated as
    no section (never user text we did not write)."""
    begin = description.find(EVA_AGENDA_START)
    if begin == -1:
        return None
    end = description.find(EVA_AGENDA_END, begin)
    if end == -1:
        return None
    return begin, end + len(EVA_AGENDA_END)


def apply_agenda_update(
    description: str | None, markdown: str, mode: AgendaSectionMode
) -> str:
    """Deterministic EVA-section merge; unrelated text is byte-preserved.

    ADD: appends exactly one section; if a section already exists the
    description is returned UNCHANGED (idempotent - never duplicates).
    UPDATE: replaces only the existing EVA section, preserving everything
    before/after it exactly; with no existing section it adds one."""
    section = render_agenda_section(markdown)
    base = description or ""
    span = _find_agenda_section(base)
    if mode == AgendaSectionMode.ADD:
        if span is not None:
            return base  # idempotent: never a second EVA section
        return f"{base}\n\n{section}" if base else section
    # UPDATE
    if span is None:
        return f"{base}\n\n{section}" if base else section
    begin, end = span
    return base[:begin] + section + base[end:]
