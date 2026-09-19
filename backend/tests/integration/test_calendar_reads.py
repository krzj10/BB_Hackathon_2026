"""A02 Calendar read tests - deterministic, synthetic Google payloads only.

Covers normalization to the frozen Meeting contract (Warsaw timezone, DST,
midnight bounds, all-day exclusive end), ETag/recurrence preservation,
missing optional fields, malformed payload honesty and bounded pagination.
No live Google access."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.contracts.domain import AllDaySpan, TimedSpan
from app.google.calendar import (
    UNTRIAGED_REASON_CODE,
    CalendarService,
    normalize_event,
)

RETRIEVED = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)


class FakeHttp:
    """Queue of JSON responses keyed by request order; records requests."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[tuple[str, dict | None]] = []

    def get_json(self, url, params):
        self.requests.append((url, params))
        if not self._responses:
            raise AssertionError(f"no fake response left for {url}")
        return self._responses.pop(0)


def timed_event(event_id="evt-1", **overrides) -> dict:
    raw = {
        "id": event_id,
        "summary": "Vendor sync",
        "etag": "etag-abc-123",
        "htmlLink": f"https://calendar.example/{event_id}",
        "start": {"dateTime": "2026-09-21T08:30:00+02:00", "timeZone": "Europe/Warsaw"},
        "end": {"dateTime": "2026-09-21T10:00:00+02:00", "timeZone": "Europe/Warsaw"},
        "organizer": {"email": "me@example.com", "displayName": "Me"},
        "attendees": [
            {"email": "cfo@example.com", "displayName": "CFO Person"},
            {"email": "ops@example.com"},
        ],
        "description": "notes",
        "location": "online",
    }
    raw.update(overrides)
    return raw


# ---------------------------------------------------------------------------
# Timed normalization + timezone semantics
# ---------------------------------------------------------------------------


def test_timed_event_preserves_warsaw_timezone_and_instants() -> None:
    meeting = normalize_event("primary", timed_event(), RETRIEVED)
    assert isinstance(meeting.span, TimedSpan)
    assert meeting.span.timezone == "Europe/Warsaw"
    # 08:30+02:00 == 06:30 UTC - instants preserved, never host-local.
    assert meeting.span.start.utcoffset() == timedelta(hours=2)
    assert meeting.span.start.astimezone(timezone.utc).hour == 6
    assert meeting.etag == "etag-abc-123"
    assert meeting.ref.calendar_id == "primary" and meeting.ref.event_id == "evt-1"


def test_dst_transition_span_stays_ordered() -> None:
    """2026-03-29 is the Warsaw spring-forward day (02:00 -> 03:00)."""
    raw = timed_event(
        start={"dateTime": "2026-03-29T01:45:00+01:00", "timeZone": "Europe/Warsaw"},
        end={"dateTime": "2026-03-29T03:15:00+02:00", "timeZone": "Europe/Warsaw"},
    )
    meeting = normalize_event("primary", raw, RETRIEVED)
    assert isinstance(meeting.span, TimedSpan)
    duration = meeting.span.end - meeting.span.start
    assert duration == timedelta(minutes=30)  # wall clock jumps; instants do not


def test_midnight_day_bounds_are_warsaw_aware() -> None:
    service = CalendarService(FakeHttp([]), timezone_name="Europe/Warsaw")
    start, end = service.day_bounds(date(2026, 9, 21))
    assert start.utcoffset() == timedelta(hours=2)  # DST summer +02:00
    assert start.isoformat() == "2026-09-21T00:00:00+02:00"
    assert end - start == timedelta(days=1)
    winter, _ = service.day_bounds(date(2026, 12, 21))
    assert winter.utcoffset() == timedelta(hours=1)  # post-DST +01:00


# ---------------------------------------------------------------------------
# All-day semantics - REAL Google payload shape (start.date / end.date;
# end.date is already the exclusive end). No "endDate" anywhere.
# ---------------------------------------------------------------------------


def test_all_day_one_day_event_maps_exclusive_end_directly() -> None:
    raw = timed_event(start={"date": "2026-09-25"}, end={"date": "2026-09-26"})
    meeting = normalize_event("primary", raw, RETRIEVED)
    assert isinstance(meeting.span, AllDaySpan)
    assert meeting.span.start_date == date(2026, 9, 25)
    assert meeting.span.end_exclusive == date(2026, 9, 26)


def test_all_day_multi_day_event_preserves_google_exclusive_end() -> None:
    raw = timed_event(start={"date": "2026-09-25"}, end={"date": "2026-09-27"})
    meeting = normalize_event("primary", raw, RETRIEVED)
    assert isinstance(meeting.span, AllDaySpan)
    assert meeting.span.start_date == date(2026, 9, 25)
    # Exactly 2026-09-27 - never reinterpreted as 2026-09-26.
    assert meeting.span.end_exclusive == date(2026, 9, 27)


def test_all_day_missing_end_date_is_rejected_not_invented() -> None:
    raw = timed_event(start={"date": "2026-09-25"}, end={})
    with pytest.raises(ValueError):
        normalize_event("primary", raw, RETRIEVED)


def test_non_google_end_date_field_is_not_honored() -> None:
    """A payload using the non-Google 'endDate' key must be treated as
    malformed (rejected), proving normalization does not depend on it."""
    raw = timed_event(start={"date": "2026-09-25"}, end={"endDate": "2026-09-27"})
    with pytest.raises(ValueError):
        normalize_event("primary", raw, RETRIEVED)


def test_mixed_span_representation_is_rejected() -> None:
    raw = timed_event(start={"date": "2026-09-25"}, end={"dateTime": "2026-09-26T00:00:00+02:00"})
    with pytest.raises(ValueError):
        normalize_event("primary", raw, RETRIEVED)


def test_all_day_span_flows_through_service_as_partial_when_malformed() -> None:
    page = _page(
        [
            timed_event("evt-allday", start={"date": "2026-09-25"}, end={"date": "2026-09-27"}),
            timed_event("evt-noend", start={"date": "2026-10-01"}, end={}),
        ]
    )
    service = CalendarService(FakeHttp([page]))
    meetings, status, notes = service.list_events()
    assert [m.ref.event_id for m in meetings] == ["evt-allday"]
    assert meetings[0].span.end_exclusive == date(2026, 9, 27)
    assert status.value == "partial"


# ---------------------------------------------------------------------------
# Optional fields, provenance, triage marker
# ---------------------------------------------------------------------------


def test_missing_optional_fields_stay_none_not_invented() -> None:
    raw = {
        "id": "evt-min",
        "start": {"dateTime": "2026-09-21T09:00:00+02:00"},
        "end": {"dateTime": "2026-09-21T09:30:00+02:00"},
    }
    meeting = normalize_event("primary", raw, RETRIEVED)
    assert meeting.etag is None
    assert meeting.organizer_email is None
    assert meeting.attendees == []
    # Participant counts are never invented zeros elsewhere either.
    assert meeting.description is None and meeting.location is None
    assert meeting.span.timezone == "Etc/UTC"  # documented fallback, no host-local


def test_attendee_and_organizer_normalization() -> None:
    meeting = normalize_event("primary", timed_event(), RETRIEVED)
    assert meeting.organizer_email == "me@example.com"
    assert meeting.attendees[0].email == "cfo@example.com"
    assert meeting.attendees[0].name == "CFO Person"
    assert meeting.attendees[1].previous_meeting_count is None  # unknown, not 0


def test_recurrence_information_preserved() -> None:
    raw = timed_event(recurringEventId="recurring-77")
    meeting = normalize_event("primary", raw, RETRIEVED)
    assert meeting.recurring_event_id == "recurring-77"


def test_source_provenance_attached() -> None:
    meeting = normalize_event("primary", timed_event(), RETRIEVED)
    (source,) = meeting.sources
    assert source.kind.value == "calendar_event"
    assert source.resource_id == "evt-1"
    assert source.retrieved_at == RETRIEVED


def test_untriaged_priority_marker_present() -> None:
    meeting = normalize_event("primary", timed_event(), RETRIEVED)
    (reason,) = meeting.priority_reasons
    assert reason.code == UNTRIAGED_REASON_CODE  # never mistaken for A03 policy


# ---------------------------------------------------------------------------
# Service: bounded pagination + honest partial retrieval
# ---------------------------------------------------------------------------


def _page(items, next_token=None):
    payload = {"items": items}
    if next_token:
        payload["nextPageToken"] = next_token
    return payload


def test_list_events_is_bounded_and_paginates() -> None:
    page1 = _page([timed_event("evt-1"), timed_event("evt-2")], next_token="tok-2")
    page2 = _page([timed_event("evt-3"), timed_event("evt-4")])
    service = CalendarService(FakeHttp([page1, page2]))
    meetings, status, notes = service.list_events(max_results=10)
    assert [m.ref.event_id for m in meetings] == ["evt-1", "evt-2", "evt-3", "evt-4"]
    assert status.value == "complete"


def test_list_events_respects_result_limit_and_reports_partial() -> None:
    page1 = _page([timed_event("evt-1"), timed_event("evt-2")], next_token="tok-2")
    service = CalendarService(FakeHttp([page1]))
    meetings, status, notes = service.list_events(max_results=2)
    assert len(meetings) == 2
    assert status.value == "partial"  # more pages existed behind the bound
    assert any("budget" in note for note in notes)


def test_malformed_events_skipped_with_honest_notes() -> None:
    page = _page([timed_event("evt-ok"), {"id": "evt-broken"}])
    service = CalendarService(FakeHttp([page]))
    meetings, status, notes = service.list_events()
    assert [m.ref.event_id for m in meetings] == ["evt-ok"]
    assert status.value == "partial"
    assert any("malformed" in note for note in notes)


def test_today_builds_bounds_into_request() -> None:
    service = CalendarService(FakeHttp([_page([])]), timezone_name="Europe/Warsaw")
    response = service.today(day=date(2026, 9, 21))
    assert response.day == date(2026, 9, 21)
    assert response.timezone == "Europe/Warsaw"
    url, params = service._http.requests[0]
    assert "/calendars/primary/events" in url
    assert params["timeMin"] == "2026-09-21T00:00:00+02:00"
    assert params["timeMax"] == "2026-09-22T00:00:00+02:00"


def test_get_event_uses_calendar_and_event_path() -> None:
    service = CalendarService(FakeHttp([timed_event("evt-9")]))
    meeting = service.get_event(calendar_id="cal-x", event_id="evt-9")
    assert meeting.ref.calendar_id == "cal-x" and meeting.ref.event_id == "evt-9"
    assert "/calendars/cal-x/events/evt-9" in service._http.requests[0][0]


# ---------------------------------------------------------------------------
# API routes (read-only; fakes injected through app.state)
# ---------------------------------------------------------------------------


class FakeAuth:
    def __init__(self, connected=True):
        self._connected = connected

    def authorized_session(self):
        if not self._connected:
            from app.google.auth import GoogleAuthError

            raise GoogleAuthError("google account not connected; start the OAuth flow")
        return object()  # replaced by google_http_factory in tests


def make_client(fake_http, connected=True):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    app = create_app(Settings())
    app.state.google_auth = FakeAuth(connected)
    app.state.google_http_factory = lambda session: fake_http
    return TestClient(app)


def test_route_today_returns_frozen_envelope() -> None:
    client = make_client(FakeHttp([_page([timed_event("evt-r")])]))
    response = client.get("/api/calendar/today")
    assert response.status_code == 200
    body = response.json()
    assert body["retrieval_status"] in {"complete", "partial"}
    assert body["meetings"][0]["ref"]["event_id"] == "evt-r"


def test_route_event_get() -> None:
    client = make_client(FakeHttp([timed_event("evt-g")]))
    response = client.get("/api/calendar/events/evt-g?calendar_id=primary")
    assert response.status_code == 200
    assert response.json()["meeting"]["etag"] == "etag-abc-123"


def test_route_calendar_requires_connection() -> None:
    client = make_client(FakeHttp([]), connected=False)
    response = client.get("/api/calendar/today")
    assert response.status_code == 503
    assert "reauthorization" in response.json()["detail"] or "not connected" in response.json()["detail"]


def test_route_calendar_prep_unexpected_failure_logs_no_secrets(caplog) -> None:
    import logging

    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    marker = "SECRET-REFRESH-TOKEN-4242"

    class ExplodingAuth:
        def authorized_session(self):
            raise RuntimeError(f"session prep failed with {marker} in headers")

    app = create_app(Settings())
    app.state.google_auth = ExplodingAuth()
    client = TestClient(app)
    with caplog.at_level(logging.DEBUG):
        response = client.get("/api/calendar/today")
    assert response.status_code == 503
    assert response.json()["detail"] == "calendar is temporarily unavailable"
    assert marker not in response.text
    assert marker not in caplog.text
    assert "unexpected failure preparing calendar session" in caplog.text
