"""Deterministic synthetic calendar for demo mode (read-only).

Implements the read surface of ``app.google.calendar.CalendarService`` that the
Experience layer consumes (``today``, ``day_bounds``, ``list_events``,
``get_event``) so Today, meeting context and the grounded briefing work without
Google. Events are anchored to the current moment, which keeps the demo
repeatable at any hour of the day.

No network, no OAuth, no mutation: writes are refused explicitly rather than
silently faked, so the guarded execution path can never claim a demo calendar
was changed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from ..contracts.api import TodayCalendarResponse
from ..contracts.domain import (
    Meeting,
    MeetingPriority,
    MeetingRef,
    Participant,
    Reason,
    ReasonOrigin,
    RetrievalStatus,
    SourceKind,
    SourceRef,
    TimedSpan,
)
from ..google.calendar import CalendarReadError

_CALENDAR_ID = "primary"


def _spec(*, minutes_from_now: int, duration_minutes: int) -> tuple[timedelta, timedelta]:
    return timedelta(minutes=minutes_from_now), timedelta(minutes=duration_minutes)


#: (event_id, title, offset, duration, priority, description, attendees)
_EVENT_SPECS: tuple[dict[str, Any], ...] = (
    {
        "event_id": "demo-acme-review",
        "title": "ACME - przeglad komercyjny (wycena)",
        "offset": 35,
        "duration": 45,
        "priority": MeetingPriority.HIGH,
        "description": "Przeglad zaktualizowanej wyceny i harmonogramu wdrozenia.",
        "attendees": [
            ("anna.kowalska@acme.example", "Anna Kowalska", "ACME", "Commercial", False),
            ("piotr.zielinski@firma.example", "Piotr Zieliński", "Firma", "CFO", True),
        ],
        "organizer": "anna.kowalska@acme.example",
        "location": "Teams",
    },
    {
        # Deliberately overlaps the ACME review (+65 < +35+45): a real conflict.
        "event_id": "demo-investor-call",
        "title": "Investor update call",
        "offset": 65,
        "duration": 40,
        "priority": MeetingPriority.HIGH,
        "description": "Kwartalny update dla inwestorow: przychod, pipeline, ryzyka.",
        "attendees": [("irena.wojcik@fund.example", "Irena Wójcik", "Fund", "Partner", False)],
        "organizer": "piotr.zielinski@firma.example",
        "location": "Zoom",
    },
    {
        "event_id": "demo-deep-work",
        "title": "Deep Work - strategia Q4",
        "offset": 120,
        "duration": 120,
        "priority": MeetingPriority.MEDIUM,
        "description": (
            "Protected focus block. Interrupt only for genuinely urgent matters."
        ),
        "attendees": [],
        "organizer": None,
        "location": None,
    },
    {
        "event_id": "demo-product-weekly",
        "title": "Product weekly",
        "offset": 300,
        "duration": 30,
        "priority": MeetingPriority.MEDIUM,
        "description": "Status roadmapy, ryzyka integracji platniczej.",
        "attendees": [("ewa.lose@produkt.example", "Ewa Lose", "Produkt", "Head of Product", True)],
        "organizer": "ewa.lose@produkt.example",
        "location": "Meet",
    },
    {
        "event_id": "demo-train-warsaw",
        "title": "Pociag do Warszawy",
        "offset": 420,
        "duration": 165,
        "priority": MeetingPriority.LOW,
        "description": "Przejazd na spotkanie z klientem.",
        "attendees": [],
        "organizer": None,
        "location": "Krakow Glowny, peron 4",
    },
)


def _meeting(spec: dict[str, Any], timezone_name: str, anchor: datetime) -> Meeting:
    start = (anchor + timedelta(minutes=spec["offset"])).astimezone(timezone.utc)
    end = (start + timedelta(minutes=spec["duration"])).astimezone(timezone.utc)
    event_id = spec["event_id"]
    return Meeting(
        ref=MeetingRef(calendar_id=_CALENDAR_ID, event_id=event_id),
        etag=f"demo-{event_id}-1",
        title=spec["title"],
        span=TimedSpan(start=start, end=end, timezone=timezone_name),
        attendees=[
            Participant(
                email=email,
                name=name,
                company=company,
                role=role,
                internal=internal,
            )
            for (email, name, company, role, internal) in spec["attendees"]
        ],
        organizer_email=spec["organizer"],
        editable=False,
        description=spec["description"],
        location=spec["location"],
        priority=spec["priority"],
        priority_reasons=[
            Reason(
                code="demo.calendar.priority",
                origin=ReasonOrigin.RULE,
                text="deterministic demo fixture priority",
                source_ids=[f"demo-event:{event_id}"],
            )
        ],
        sources=[
            SourceRef(
                id=f"demo-event:{event_id}",
                kind=SourceKind.CALENDAR_EVENT,
                resource_id=event_id,
                title=spec["title"],
                retrieved_at=anchor.astimezone(timezone.utc),
            )
        ],
    )


class DemoCalendarService:
    """Read-only synthetic calendar behind the existing CalendarService shape."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        timezone_name: str = "Europe/Warsaw",
    ) -> None:
        self._clock = clock
        self._timezone_name = timezone_name

    # -- helpers ----------------------------------------------------------- #
    def _anchor(self) -> datetime:
        return self._clock()

    def _all(self) -> list[Meeting]:
        anchor = self._anchor()
        return [_meeting(spec, self._timezone_name, anchor) for spec in _EVENT_SPECS]

    # -- read surface ------------------------------------------------------- #
    def day_bounds(self, day: date | None = None) -> tuple[datetime, datetime]:
        tz = ZoneInfo(self._timezone_name)
        anchor = day or self._anchor().astimezone(tz).date()
        start = datetime(anchor.year, anchor.month, anchor.day, tzinfo=tz)
        return start, start + timedelta(days=1)

    def list_events(
        self,
        *,
        calendar_id: str = _CALENDAR_ID,
        time_min: datetime | None = None,
        time_max: datetime | None = None,
        max_results: int = 50,
        max_pages: int = 10,
    ) -> tuple[list[Meeting], RetrievalStatus, list[str]]:
        meetings = sorted(self._all(), key=lambda m: m.span.start)  # type: ignore[return-value]
        if time_min is not None:
            meetings = [m for m in meetings if m.span.end > time_min]  # type: ignore[union-attr]
        if time_max is not None:
            meetings = [m for m in meetings if m.span.start < time_max]  # type: ignore[union-attr]
        return meetings[: max(1, int(max_results))], RetrievalStatus.COMPLETE, []

    def get_event(self, *, calendar_id: str = _CALENDAR_ID, event_id: str) -> Meeting:
        for meeting in self._all():
            if meeting.ref.event_id == event_id:
                return meeting
        raise CalendarReadError(f"demo calendar has no event {event_id!r}")

    def today(self, *, calendar_id: str = _CALENDAR_ID, day: date | None = None) -> Any:
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

    # -- writes are refused explicitly (never silently faked) --------------- #
    def create_event(self, **kwargs: Any) -> Any:  # pragma: no cover - guard
        raise CalendarReadError("demo calendar is read-only")

    def reschedule_event(self, **kwargs: Any) -> Any:  # pragma: no cover - guard
        raise CalendarReadError("demo calendar is read-only")

    def update_agenda_event(self, **kwargs: Any) -> Any:  # pragma: no cover - guard
        raise CalendarReadError("demo calendar is read-only")
