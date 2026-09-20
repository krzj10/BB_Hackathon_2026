"""Read-only Gmail access normalized to NormalizedSourceEvent (A02).

Guarantees:

- bounded search and bounded pagination (never whole-mailbox ingestion);
- base64url body decoding;
- multipart MIME traversal preferring text/plain, with sanitized HTML
  fallback (script/style content dropped, tags stripped - EVA never renders
  mail HTML);
- attachments are skipped with an honest note, not silently ignored;
- bodies longer than the frozen contract limit are truncated and the
  truncation is reported in retrieval notes (never silent);
- source ids, sender address, subject and timestamps preserved; provenance
  via SourceRef with retrieval timestamp;
- raw message bodies are never logged; logs carry ids/counts only.

No send/compose/modify capability exists here or in the requested scopes -
A02 is read-only by scope and by code.
"""

from __future__ import annotations

import base64
import binascii
import html as html_module
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from ..contracts.domain import (
    NormalizedSourceEvent,
    RetrievalStatus,
    SourceKind,
    SourceRef,
    SourceSystem,
)
from .http import GoogleHttp

logger = logging.getLogger("eva.google.gmail")

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"
#: Frozen contract cap (NormalizedSourceEvent.body max_length).
MAX_BODY_CHARS = 20_000
#: Hard bound for a single search request loop.
SEARCH_MAX_RESULTS = 25
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_PAGES = 5
#: Hard budgets for the continuation-aware WINDOW search used by A06 polling
#: (exhausts a bounded TIME window, never the mailbox; remediation PART 17-19).
MAX_WINDOW_THREADS = 100
MAX_WINDOW_PAGES = 8

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def decode_body(data: str) -> str:
    """Decode Gmail's base64url payload (padding-tolerant)."""
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(
            "utf-8", errors="replace"
        )
    except (binascii.Error, ValueError) as exc:
        raise ValueError("message body is not valid base64url") from exc


def sanitize_html(raw_html: str) -> str:
    """MVP text extraction: drop script/style entirely, strip remaining tags,
    unescape entities, collapse whitespace."""
    without_scripts = _SCRIPT_STYLE_RE.sub(" ", raw_html)
    text = _TAG_RE.sub(" ", without_scripts)
    return re.sub(r"\s+", " ", html_module.unescape(text)).strip()


def _header(payload: dict[str, Any], name: str) -> str | None:
    for header in payload.get("headers") or []:
        if isinstance(header, dict) and header.get("name", "").lower() == name.lower():
            return header.get("value")
    return None


@dataclass
class _ExtractedBody:
    text: str | None = None
    format: str | None = None  # "plain" | "html"
    notes: list[str] = field(default_factory=list)


def extract_body(payload: dict[str, Any]) -> _ExtractedBody:
    """Walk the MIME tree preferring text/plain over sanitized text/html."""
    result = _ExtractedBody()
    plain: str | None = None
    html_text: str | None = None

    def walk(node: dict[str, Any], depth: int) -> None:
        nonlocal plain, html_text
        if depth > 10:  # pathological nesting guard
            result.notes.append("MIME nesting too deep; remaining parts skipped")
            return
        mime = (node.get("mimeType") or "").lower()
        body = node.get("body") or {}
        # Attachments (any node with a filename/disposition) are never read as
        # inline text - recorded honestly and skipped.
        if node.get("filename") or "attachment" in (
            _header(node, "content-disposition") or ""
        ).lower():
            result.notes.append(f"attachment skipped: {node.get('filename') or mime}")
            return
        if mime.startswith("multipart/"):
            for part in node.get("parts") or []:
                walk(part, depth + 1)
            # multipart containers may also carry inline data themselves
            if body.get("data") and mime != "multipart/alternative":
                _consume(mime, body["data"])
            return
        if body.get("data"):
            _consume(mime, body["data"])

    def _consume(mime: str, data: str) -> None:
        nonlocal plain, html_text
        try:
            decoded = decode_body(data)
        except ValueError as exc:
            result.notes.append(f"undecodable body part skipped: {exc}")
            return
        if mime == "text/plain" and plain is None:
            plain = decoded
        elif mime == "text/html" and html_text is None:
            html_text = decoded

    walk(payload, 0)
    if plain is not None:
        result.text, result.format = plain, "plain"
    elif html_text is not None:
        result.text, result.format = sanitize_html(html_text), "html"
        result.notes.append("body normalized from HTML (text/plain unavailable)")
    else:
        result.notes.append("message has no text body")
    return result


def _parse_received(value: str | None, retrieved_at: datetime) -> tuple[datetime, list[str]]:
    notes: list[str] = []
    if value:
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
                notes.append("Date header lacked timezone; assumed UTC")
            return parsed, notes
        except (TypeError, ValueError):
            notes.append("unparseable Date header; used retrieval time")
    else:
        notes.append("missing Date header; used retrieval time")
    return retrieved_at, notes


@dataclass
class ThreadSummary:
    thread_id: str
    message_ids: tuple[str, ...]
    subject: str | None
    snippet: str | None  # internal evidence only - never logged, never exposed raw


@dataclass
class ThreadEvidence:
    thread_id: str
    messages: list[NormalizedSourceEvent]
    retrieval_status: RetrievalStatus
    notes: list[str] = field(default_factory=list)


class GmailService:
    """Bounded, read-only Gmail surface consumed by A06 ingestion."""

    def __init__(self, http: GoogleHttp) -> None:
        self._http = http

    def search(
        self,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        *,
        max_pages: int = MAX_SEARCH_PAGES,
    ) -> tuple[list[ThreadSummary], RetrievalStatus, list[str]]:
        """Bounded thread search: at most ``limit`` threads across at most
        ``max_pages`` pages. Never ingests the mailbox."""
        limit = max(1, min(limit, 50))
        summaries: list[ThreadSummary] = []
        notes: list[str] = []
        status = RetrievalStatus.COMPLETE
        token: str | None = None
        for _ in range(max(1, max_pages)):
            params: dict[str, Any] = {
                "q": query,
                "maxResults": min(SEARCH_MAX_RESULTS, limit - len(summaries)),
                "includeSpamTrash": "false",
            }
            if token:
                params["pageToken"] = token
            payload = self._http.get_json(f"{GMAIL_BASE}/users/me/threads", params)
            for raw in payload.get("threads") or []:
                if len(summaries) >= limit:
                    break
                summary = self._summarize(raw)
                if summary is not None:
                    summaries.append(summary)
            token = payload.get("nextPageToken")
            if token is None or len(summaries) >= limit:
                break
        # A non-null continuation token after the loop means results remain
        # behind a deliberate bound (limit or page budget): status must say
        # PARTIAL, not merely carry a note. Exactly `limit` results with no
        # next token stay COMPLETE.
        if token is not None:
            status = RetrievalStatus.PARTIAL
            notes.append(
                "search stopped at the result/page bound while nextPageToken "
                "existed; more matches exist"
            )
        return summaries, status, notes

    def search_window(
        self,
        query: str,
        *,
        max_threads: int = MAX_WINDOW_THREADS,
        max_pages: int = MAX_WINDOW_PAGES,
    ) -> tuple[list[ThreadSummary], RetrievalStatus, list[str]]:
        """INTERNAL (A06 polling): exhaust a bounded TIME window, not the
        mailbox. Follows Gmail continuation tokens within the hard budget so a
        busy poll window is fully consumed in one run instead of repeatedly
        returning the same first page. COMPLETE means the token chain ended
        inside the budget; PARTIAL means the budget was exhausted with data
        potentially remaining (the caller must NOT advance its cursor).
        Page tokens never leave this method. The public ``search``/
        ``get_thread`` boundary is unchanged for all other consumers."""
        summaries: list[ThreadSummary] = []
        notes: list[str] = []
        status = RetrievalStatus.COMPLETE
        token: str | None = None
        for _ in range(max(1, max_pages)):
            params: dict[str, Any] = {
                "q": query,
                "maxResults": SEARCH_MAX_RESULTS,
                "includeSpamTrash": "false",
            }
            if token:
                params["pageToken"] = token
            payload = self._http.get_json(f"{GMAIL_BASE}/users/me/threads", params)
            for raw in payload.get("threads") or []:
                summary = self._summarize(raw)
                if summary is not None:
                    summaries.append(summary)
            token = payload.get("nextPageToken")
            if len(summaries) >= max_threads:
                # Hard thread budget reached; remaining matches (if any) stay
                # unclaimed - honestly PARTIAL, never silently skipped.
                status = RetrievalStatus.PARTIAL
                notes.append(
                    f"window search stopped at the {max_threads}-thread budget "
                    "while more may remain"
                )
                return summaries[:max_threads], status, notes
            if token is None:
                break
        if token is not None:
            status = RetrievalStatus.PARTIAL
            notes.append(
                f"window search stopped at the {max_pages}-page budget while "
                "nextPageToken existed"
            )
        return summaries, status, notes

    def _summarize(self, raw: dict[str, Any]) -> ThreadSummary | None:
        thread_id = raw.get("id")
        if not thread_id:
            return None
        payload = (raw.get("payload") or {}) if isinstance(raw.get("payload"), dict) else {}
        message_ids = tuple(
            m["id"] for m in (raw.get("messages") or []) if isinstance(m, dict) and m.get("id")
        )
        snippet = raw.get("snippet")
        return ThreadSummary(
            thread_id=thread_id,
            message_ids=message_ids,
            subject=_header(payload, "subject"),
            snippet=str(snippet)[:200] if snippet else None,
        )

    def get_thread(self, thread_id: str) -> ThreadEvidence:
        payload = self._http.get_json(
            f"{GMAIL_BASE}/users/me/threads/{thread_id}", {"format": "full"}
        )
        retrieved_at = datetime.now(timezone.utc)
        evidence = ThreadEvidence(
            thread_id=thread_id, messages=[], retrieval_status=RetrievalStatus.COMPLETE
        )
        for raw in payload.get("messages") or []:
            try:
                message = self._normalize_message(raw, retrieved_at, evidence.notes)
            except ValueError as exc:  # malformed message: honest partial result
                evidence.notes.append(f"skipped malformed message: {exc}")
                logger.warning("gmail: skipped malformed message in thread %s", thread_id)
                continue
            if message is not None:
                evidence.messages.append(message)
        if evidence.notes:
            evidence.retrieval_status = RetrievalStatus.PARTIAL
        return evidence

    def _normalize_message(
        self, raw: dict[str, Any], retrieved_at: datetime, shared_notes: list[str]
    ) -> NormalizedSourceEvent | None:
        message_id = raw.get("id")
        if not message_id:
            raise ValueError("message is missing id")
        payload = raw.get("payload")
        if not isinstance(payload, dict):
            raise ValueError(f"message {message_id!r} has no payload")

        _, address = parseaddr(_header(payload, "from") or "")
        if not address or "@" not in address:
            raise ValueError(f"message {message_id!r} has no parsable sender address")

        received_at, date_notes = _parse_received(_header(payload, "date"), retrieved_at)
        shared_notes.extend(date_notes)

        extracted = extract_body(payload)
        shared_notes.extend(
            note for note in extracted.notes if note not in shared_notes
        )
        body: str | None = None
        if extracted.text is not None:
            body = extracted.text
            if len(body) > MAX_BODY_CHARS:
                body = body[:MAX_BODY_CHARS]
                shared_notes.append(
                    f"message {message_id}: body truncated to {MAX_BODY_CHARS} characters"
                )

        subject = _header(payload, "subject")
        return NormalizedSourceEvent(
            source=SourceSystem.GMAIL,
            source_id=message_id,
            sender_email=address,
            subject=subject,
            body=body,
            received_at=received_at,
            sources=[
                SourceRef(
                    id=f"gmail:{message_id}",
                    kind=SourceKind.GMAIL_MESSAGE,
                    resource_id=message_id,
                    title=subject or "(no subject)",
                    retrieved_at=retrieved_at,
                )
            ],
        )
