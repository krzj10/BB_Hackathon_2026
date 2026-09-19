"""A02 Gmail read tests - synthetic payloads only, no live Google.

Covers bounded search/pagination, base64url decoding, multipart traversal,
HTML fallback sanitization, attachment honesty, truncation reporting,
malformed-message partial retrieval and provenance preservation."""

from __future__ import annotations

import base64
from datetime import datetime, timezone

from app.contracts.domain import RetrievalStatus
from app.google.gmail import (
    MAX_BODY_CHARS,
    GmailService,
    decode_body,
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
    return {
        "id": msg_id,
        "snippet": "Hello EVA",
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
