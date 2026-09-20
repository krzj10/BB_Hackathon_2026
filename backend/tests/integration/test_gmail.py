"""A02 Gmail read tests - synthetic payloads only, no live Google.

Covers bounded search/pagination, base64url decoding, multipart traversal,
HTML fallback sanitization, attachment honesty, truncation reporting,
malformed-message partial retrieval and provenance preservation."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest

from app.contracts.domain import RetrievalStatus
from app.google.gmail import (
    MAX_BODY_CHARS,
    GmailService,
    decode_body,
    parse_internal_date,
    sanitize_html,
)


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


class FakeHttp:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[tuple[str, dict | None]] = []

    def get_json(self, url, params):
        self.requests.append((url, params))
        if not self._responses:
            raise AssertionError(f"no fake response left for {url}")
        return self._responses.pop(0)


def message(msg_id="msg-1", *, subject="Q3 invoice", sender="CFO <cfo@example.com>",
            date="Mon, 21 Sep 2026 10:30:00 +0200", body_text="Hello EVA") -> dict:
    from email.utils import parsedate_to_datetime

    arrival = parsedate_to_datetime(date)
    return {
        "id": msg_id,
        "snippet": "Hello EVA",
        # Gmail always provides provider arrival time on full-format reads.
        "internalDate": str(int(arrival.timestamp() * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": date},
            ],
            "body": {"data": b64(body_text)},
        },
    }


# ---------------------------------------------------------------------------
# Decoding primitives
# ---------------------------------------------------------------------------


def test_base64url_decode_padding_tolerant() -> None:
    assert decode_body(b64("Zażół gęś żółw")) == "Zażół gęś żółw"


def test_html_sanitization_drops_scripts_and_tags() -> None:
    dirty = "<div><script>steal()</script><style>p{}</style><b>Bold &amp; free</b></div>"
    assert sanitize_html(dirty) == "Bold & free"


# ---------------------------------------------------------------------------
# Bounded search + pagination
# ---------------------------------------------------------------------------


def test_search_is_bounded_by_limit() -> None:
    page = {
        "threads": [
            {"id": "t1", "snippet": "a"},
            {"id": "t2", "snippet": "b"},
            {"id": "t3", "snippet": "c"},
        ],
        "nextPageToken": "more",
    }
    service = GmailService(FakeHttp([page]))
    summaries, status, notes = service.search("from:cfo@example.com", limit=2)
    assert [s.thread_id for s in summaries] == ["t1", "t2"]
    # Limit stopped retrieval while a continuation token existed -> PARTIAL.
    assert status is RetrievalStatus.PARTIAL
    assert any("nextPageToken" in note for note in notes)


def test_search_complete_when_no_continuation_token() -> None:
    page = {"threads": [{"id": "t1"}, {"id": "t2"}]}  # no nextPageToken
    service = GmailService(FakeHttp([page]))
    summaries, status, notes = service.search("subject:x", limit=2)
    assert [s.thread_id for s in summaries] == ["t1", "t2"]
    assert status is RetrievalStatus.COMPLETE


def test_search_paginates_until_limit_or_pages() -> None:
    pages = [
        {"threads": [{"id": "t1"}, {"id": "t2"}], "nextPageToken": "p2"},
        {"threads": [{"id": "t3"}]},
    ]
    service = GmailService(FakeHttp(list(pages)))
    summaries, status, notes = service.search("subject:agenda", limit=10)
    assert [s.thread_id for s in summaries] == ["t1", "t2", "t3"]
    assert status is RetrievalStatus.COMPLETE


def test_search_page_budget_reports_partial() -> None:
    pages = [
        {"threads": [{"id": "t1"}], "nextPageToken": "p2"},
        {"threads": [{"id": "t2"}]},  # never consumed: budget is 1 page
    ]
    service = GmailService(FakeHttp(pages))
    summaries, status, notes = service.search("q", limit=50, max_pages=1)
    assert [s.thread_id for s in summaries] == ["t1"]
    assert status is RetrievalStatus.PARTIAL


def test_search_sends_bounded_params() -> None:
    service = GmailService(FakeHttp([{"threads": []}]))
    service.search("hello", limit=5)
    url, params = service._http.requests[0]
    assert "/users/me/threads" in url
    assert params["q"] == "hello"
    assert int(params["maxResults"]) <= 25


# ---------------------------------------------------------------------------
# Continuation-aware window search (A06 polling helper; internal surface)
# ---------------------------------------------------------------------------


def test_search_window_exhausts_continuation_chain() -> None:
    pages = [
        {"threads": [{"id": "t1"}], "nextPageToken": "p2"},
        {"threads": [{"id": "t2"}], "nextPageToken": "p3"},
        {"threads": [{"id": "t3"}]},
    ]
    service = GmailService(FakeHttp(pages))
    summaries, status, _notes = service.search_window("q", max_threads=100, max_pages=5)
    assert [s.thread_id for s in summaries] == ["t1", "t2", "t3"]
    assert status is RetrievalStatus.COMPLETE  # token chain ended within budget


def test_search_window_thread_budget_reports_partial() -> None:
    page = {
        "threads": [{"id": f"t{index}"} for index in range(4)],
        "nextPageToken": "more",
    }
    service = GmailService(FakeHttp([page]))
    summaries, status, notes = service.search_window("q", max_threads=2)
    assert len(summaries) == 2
    assert status is RetrievalStatus.PARTIAL   # budget hit: never claims complete
    assert any("budget" in note for note in notes)


def test_search_window_page_budget_reports_partial() -> None:
    pages = [
        {"threads": [{"id": f"t{index}"}], "nextPageToken": f"p{index + 1}"}
        for index in range(5)
    ]
    service = GmailService(FakeHttp(pages))
    summaries, status, _notes = service.search_window("q", max_threads=100, max_pages=2)
    assert len(summaries) == 2
    assert status is RetrievalStatus.PARTIAL


def test_search_window_exact_budget_without_token_is_complete() -> None:
    # A result set that ENDS exactly at the budget is proven complete.
    page = {"threads": [{"id": "t1"}, {"id": "t2"}]}  # no nextPageToken
    service = GmailService(FakeHttp([page]))
    summaries, status, _notes = service.search_window("q", max_threads=2)
    assert len(summaries) == 2
    assert status is RetrievalStatus.COMPLETE


def test_search_window_exact_budget_with_token_is_partial() -> None:
    page = {"threads": [{"id": "t1"}, {"id": "t2"}], "nextPageToken": "p3"}
    service = GmailService(FakeHttp([page]))
    summaries, status, notes = service.search_window("q", max_threads=2)
    assert len(summaries) == 2
    assert status is RetrievalStatus.PARTIAL
    assert any("budget" in note for note in notes)


def test_search_window_truncates_over_budget_page() -> None:
    pages = [
        {"threads": [{"id": "t1"}, {"id": "t2"}], "nextPageToken": "p2"},
        {"threads": [{"id": "t3"}, {"id": "t4"}]},  # overshoots the 3-thread budget
    ]
    service = GmailService(FakeHttp(pages))
    summaries, status, _notes = service.search_window("q", max_threads=3)
    assert [s.thread_id for s in summaries] == ["t1", "t2", "t3"]  # truncated to budget
    assert status is RetrievalStatus.PARTIAL


# ---------------------------------------------------------------------------
# Provider internalDate extraction (A06 window membership source)
# ---------------------------------------------------------------------------


def test_internal_date_parsed_to_aware_utc() -> None:
    arrival = datetime(2026, 9, 21, 8, 30, tzinfo=timezone.utc)
    raw = message("msg-id-1")
    raw["internalDate"] = str(int(arrival.timestamp() * 1000))
    service = GmailService(FakeHttp([{"messages": [raw]}]))
    evidence = service.get_thread("t")
    parsed = evidence.internal_dates["msg-id-1"]
    assert parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)
    assert parsed == arrival
    assert evidence.retrieval_status is RetrievalStatus.COMPLETE


@pytest.mark.parametrize("bad", [None, "", "abc", "-5", "9" * 18])
def test_missing_or_invalid_internal_date_flagged_safely(bad) -> None:
    raw = message("msg-bad")
    if bad is None:
        del raw["internalDate"]
    else:
        raw["internalDate"] = bad
    service = GmailService(FakeHttp([{"messages": [raw]}]))
    evidence = service.get_thread("t")
    assert "msg-bad" not in evidence.internal_dates      # never guessed
    assert evidence.retrieval_status is RetrievalStatus.PARTIAL
    assert evidence.ingestion_complete is False          # cursor-unsafe loss
    assert any("internalDate" in note for note in evidence.notes)
    # Notes carry ids only - no body content:
    assert all("Hello EVA" not in note for note in evidence.notes)


def test_parse_internal_date_helper_is_utc_and_strict() -> None:
    parsed, note = parse_internal_date({"id": "m", "internalDate": "1789985280000"})
    assert note is None and parsed is not None
    assert parsed == datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
        milliseconds=1789985280000
    )
    for bad in ({}, {"internalDate": "x"}, {"internalDate": "-1"}):
        value, note = parse_internal_date({"id": "m", **bad})
        assert value is None and note is not None


# ---------------------------------------------------------------------------
# Evidence fidelity vs ingestion completeness (cursor-safety split)
# ---------------------------------------------------------------------------


def test_html_only_partial_fidelity_but_ingestion_complete() -> None:
    raw = {
        "id": "msg-html2",
        "internalDate": "1789985280000",
        "payload": {
            "mimeType": "text/html",
            "headers": [{"name": "From", "value": "a@example.com"}],
            "body": {"data": b64("<p>Invoice <b>1000</b></p>")},
        },
    }
    service = GmailService(FakeHttp([{"messages": [raw]}]))
    evidence = service.get_thread("t")
    assert evidence.retrieval_status is RetrievalStatus.PARTIAL   # fidelity lost
    assert evidence.ingestion_complete is True                    # cursor-safe


def test_attachment_partial_fidelity_but_ingestion_complete() -> None:
    raw = {
        "id": "msg-att2",
        "internalDate": "1789985280000",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [{"name": "From", "value": "a@example.com"}],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": b64("see attached")}},
                {"mimeType": "application/pdf", "filename": "invoice.pdf", "body": {}},
            ],
        },
    }
    service = GmailService(FakeHttp([{"messages": [raw]}]))
    evidence = service.get_thread("t")
    assert any("attachment skipped" in note for note in evidence.notes)
    assert evidence.retrieval_status is RetrievalStatus.PARTIAL
    assert evidence.ingestion_complete is True


def test_truncated_body_partial_fidelity_but_ingestion_complete() -> None:
    big = "x" * (MAX_BODY_CHARS + 500)
    service = GmailService(FakeHttp([{"messages": [message(body_text=big)]}]))
    evidence = service.get_thread("t")
    assert any("truncated" in note for note in evidence.notes)
    assert evidence.retrieval_status is RetrievalStatus.PARTIAL
    assert evidence.ingestion_complete is True   # the bounded body IS the contract


def test_missing_rfc_date_with_valid_internal_is_ingestion_complete() -> None:
    raw = message("msg-nd2")  # helper carries a valid internalDate
    raw["payload"]["headers"] = [h for h in raw["payload"]["headers"] if h["name"] != "Date"]
    service = GmailService(FakeHttp([{"messages": [raw]}]))
    evidence = service.get_thread("t")
    assert any("Date header" in note for note in evidence.notes)
    assert evidence.retrieval_status is RetrievalStatus.PARTIAL
    assert evidence.ingestion_complete is True   # internalDate owns membership


def test_malformed_canonical_message_marks_ingestion_incomplete() -> None:
    broken = {"id": "msg-broken2", "internalDate": "1789985280000",
              "payload": {"mimeType": "text/plain", "headers": [], "body": {}}}
    service = GmailService(FakeHttp([{"messages": [broken, message("msg-ok2")]}]))
    evidence = service.get_thread("t")
    assert [m.source_id for m in evidence.messages] == ["msg-ok2"]
    assert evidence.retrieval_status is RetrievalStatus.PARTIAL
    assert evidence.ingestion_complete is False  # a potential message was lost


def test_non_object_message_entry_marks_ingestion_incomplete_without_crash() -> None:
    service = GmailService(FakeHttp([{"messages": ["not-a-dict", message("msg-ok3")]}]))
    evidence = service.get_thread("t")
    assert [m.source_id for m in evidence.messages] == ["msg-ok3"]
    assert any("non-object" in note for note in evidence.notes)
    assert evidence.retrieval_status is RetrievalStatus.PARTIAL
    assert evidence.ingestion_complete is False


# ---------------------------------------------------------------------------
# Thread content: MIME handling
# ---------------------------------------------------------------------------


def test_plain_message_normalized_with_provenance() -> None:
    service = GmailService(FakeHttp([{"messages": [message()]}]))
    evidence = service.get_thread("thread-1")
    (event,) = evidence.messages
    assert event.source.value == "gmail"
    assert event.source_id == "msg-1"
    assert event.sender_email == "cfo@example.com"  # display name stripped
    assert event.subject == "Q3 invoice"
    assert event.body == "Hello EVA"
    # RFC2822 +02:00 preserved as an instant: 10:30+02:00 == 08:30 UTC.
    assert event.received_at.astimezone(timezone.utc).hour == 8
    (source,) = event.sources
    assert source.kind.value == "gmail_message" and source.resource_id == "msg-1"


def test_multipart_prefers_plain_over_html() -> None:
    raw = {
        "id": "msg-mime",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [{"name": "From", "value": "a@example.com"}],
            "parts": [
                {"mimeType": "text/html", "body": {"data": b64("<b>HTML body</b>")}},
                {"mimeType": "text/plain", "body": {"data": b64("Plain body")}},
            ],
        },
    }
    service = GmailService(FakeHttp([{"messages": [raw]}]))
    evidence = service.get_thread("t")
    assert evidence.messages[0].body == "Plain body"


def test_html_only_fallback_is_sanitized_and_reported() -> None:
    raw = {
        "id": "msg-html",
        "payload": {
            "mimeType": "text/html",
            "headers": [{"name": "From", "value": "a@example.com"}],
            "body": {"data": b64("<p>Invoice <b>1000</b></p><script>x()</script>")},
        },
    }
    service = GmailService(FakeHttp([{"messages": [raw]}]))
    evidence = service.get_thread("t")
    assert evidence.messages[0].body == "Invoice 1000"
    assert any("HTML" in note for note in evidence.notes)
    assert evidence.retrieval_status is RetrievalStatus.PARTIAL


def test_attachment_skipped_with_note() -> None:
    raw = {
        "id": "msg-att",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [{"name": "From", "value": "a@example.com"}],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": b64("see attached")}},
                {"mimeType": "application/pdf", "filename": "invoice.pdf", "body": {}},
            ],
        },
    }
    service = GmailService(FakeHttp([{"messages": [raw]}]))
    evidence = service.get_thread("t")
    assert evidence.messages[0].body == "see attached"
    assert any("attachment skipped" in note and "invoice.pdf" in note for note in evidence.notes)


def test_missing_body_is_reported_not_invented() -> None:
    raw = {
        "id": "msg-empty",
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": "From", "value": "a@example.com"}],
            "body": {},
        },
    }
    service = GmailService(FakeHttp([{"messages": [raw]}]))
    evidence = service.get_thread("t")
    assert evidence.messages[0].body is None
    assert any("no text body" in note for note in evidence.notes)


def test_long_body_truncated_and_reported() -> None:
    big = "x" * (MAX_BODY_CHARS + 500)
    service = GmailService(FakeHttp([{"messages": [message(body_text=big)]}]))
    evidence = service.get_thread("t")
    assert len(evidence.messages[0].body) == MAX_BODY_CHARS
    assert any("truncated" in note for note in evidence.notes)


def test_undecodable_body_part_reported() -> None:
    raw = {
        "id": "msg-bad",
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": "From", "value": "a@example.com"}],
            "body": {"data": "%%%not-base64%%%" },
        },
    }
    service = GmailService(FakeHttp([{"messages": [raw]}]))
    evidence = service.get_thread("t")
    assert evidence.messages[0].body is None
    assert any("undecodable" in note for note in evidence.notes)


def test_malformed_messages_skipped_others_survive() -> None:
    broken_no_sender = {
        "id": "msg-broken",
        "payload": {"mimeType": "text/plain", "headers": [], "body": {}},
    }
    service = GmailService(
        FakeHttp([{"messages": [broken_no_sender, message("msg-good")]}])
    )
    evidence = service.get_thread("t")
    assert [m.source_id for m in evidence.messages] == ["msg-good"]
    assert evidence.retrieval_status is RetrievalStatus.PARTIAL
    assert any("malformed" in note for note in evidence.notes)


def test_missing_date_header_uses_retrieval_time_honestly() -> None:
    raw = message("msg-nd")
    raw["payload"]["headers"] = [h for h in raw["payload"]["headers"] if h["name"] != "Date"]
    service = GmailService(FakeHttp([{"messages": [raw]}]))
    evidence = service.get_thread("t")
    assert evidence.messages[0].received_at.tzinfo is not None
    assert any("Date header" in note for note in evidence.notes)
