"""A06 Gmail ingestion: shared poll_recent()/check_now() path with durable
cursor, durable dedup and honest partial/failed reporting.

Pipeline (frozen architecture):

    Gmail -> A02 GmailService -> NormalizedSourceEvent -> dedup/poll/cursor
          -> AttentionEngine.ingest(source_event) -> AttentionItem

Guarantees:

- ONE non-overlap lock: poll_recent() and check_now() never run Gmail work
  concurrently (scheduling protection only; durable dedup stays SQLite);
- the cursor advances ONLY after a fully processed, COMPLETELY retrieved
  batch - partial retrieval, Gmail failure or downstream failure NEVER moves
  it past known-unprocessed data;
- first run performs a BASELINE: discovered ids are marked seen, nothing is
  emitted as new, and the cursor is set only on complete retrieval;
- per-message dedup via the durable seen_sources ledger plus the canonical
  UNIQUE attention_items(source, source_id) constraint - never an in-memory
  set as authority;
- ingestion-window membership uses Gmail PROVIDER internalDate (remediation):
  a thread matching the window query never resurfaces older thread history,
  and a message without a trustworthy internalDate is treated as uncertain
  (excluded, retrieval PARTIAL, cursor held) - never backfilled from the
  sender-controlled RFC Date header;
- a source is marked seen ONLY AFTER successful downstream ingestion, so a
  transient failure retries on the next overlapping poll;
- cursor advancement tracks INGESTION completeness (no potential message was
  structurally lost or left un-window-qualifiable), NOT evidence fidelity:
  benign A02 notes - HTML fallback normalization, attachment skipped by
  design, body bounded to the canonical size, missing/malformed RFC Date with
  a valid internalDate, or an empty-but-canonical message (body=None is a
  valid NormalizedSourceEvent; content is never fabricated) - never trap the
  cursor, while lost/unnormalizable messages or unknown provider timestamps
  always hold it;
- NO Gmail HTTP request and NO AttentionEngine call ever happens while a
  SQLite write transaction is open (repository methods are individually
  short-lived);
- emails are evidence only: this module has no approval/executor/mutation
  path of any kind, and logs carry ids/counts only - never message bodies.

check_now() IS poll_recent(): the exact same ingestion path, no fixtures.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal, Protocol, Sequence

from ..contracts.domain import AttentionItem, NormalizedSourceEvent, RetrievalStatus
from ..db.repositories import AttentionRepository, CursorRepository
from ..google.gmail import (
    DEFAULT_SEARCH_LIMIT,
    MAX_WINDOW_PAGES,
    MAX_WINDOW_THREADS,
    ThreadEvidence,
    ThreadSummary,
)

logger = logging.getLogger("eva.attention.ingest")

#: Single documented cursor key for Gmail (PART XXVII).
GMAIL_CURSOR_KEY = "gmail"
#: Conservative demo overlap: late-arriving messages are rediscovered safely.
DEFAULT_OVERLAP_SECONDS = 120


class ThreadSource(Protocol):
    """The A02 read-only Gmail surface this service consumes (frozen public
    boundary). ``search_window`` is the internal continuation-aware helper
    (A06 remediation); when a source does not provide it, the bounded public
    ``search`` is used instead."""

    def search(
        self, query: str, limit: int = ..., **kwargs: object
    ) -> tuple[Sequence[ThreadSummary], RetrievalStatus, list[str]]: ...

    def search_window(
        self, query: str, *, max_threads: int = ..., max_pages: int = ...
    ) -> tuple[Sequence[ThreadSummary], RetrievalStatus, list[str]]: ...

    def get_thread(self, thread_id: str) -> ThreadEvidence: ...


class AttentionSink(Protocol):
    """B04's AttentionEngine boundary - the frozen ingestion signature.

    A06 never implements the engine; it delivers normalized events and stores
    the canonical items the sink returns."""

    def ingest(self, source_event: NormalizedSourceEvent) -> AttentionItem: ...


RunStatus = Literal["complete", "partial", "failed", "in_progress"]


@dataclass(frozen=True)
class IngestionRunResult:
    """INTERNAL ingestion outcome (NOT a frontend contract). B's route maps
    this into the frozen CheckNowResponse (checked_count/duplicate_count/
    new_items); the extra fields stay backend-side."""

    checked_count: int = 0
    duplicate_count: int = 0
    new_items: tuple[AttentionItem, ...] = ()
    baseline_count: int = 0
    retrieval_status: RunStatus = "complete"
    notes: tuple[str, ...] = field(default_factory=tuple)


class GmailIngestionService:
    """Shared, non-overlapping Gmail ingestion with durable cursor + dedup."""

    def __init__(
        self,
        *,
        gmail_source_factory: Callable[[], ThreadSource],
        cursors: CursorRepository,
        attention_repo: AttentionRepository,
        sink: AttentionSink | None = None,
        query: str = "in:inbox",
        search_limit: int = DEFAULT_SEARCH_LIMIT,
        overlap_seconds: int = DEFAULT_OVERLAP_SECONDS,
        max_poll_threads: int = MAX_WINDOW_THREADS,
        max_poll_pages: int = MAX_WINDOW_PAGES,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if overlap_seconds < 0:
            raise ValueError("overlap_seconds must be >= 0")
        self._gmail_source_factory = gmail_source_factory
        self._cursors = cursors
        self._attention_repo = attention_repo
        self._sink = sink
        self._query = query
        self._search_limit = max(1, search_limit)
        self._overlap_seconds = overlap_seconds
        # Hard per-run budgets for exhausting ONE bounded time window -
        # never whole-mailbox ingestion (remediation PART 17-19).
        self._max_poll_threads = max(1, max_poll_threads)
        self._max_poll_pages = max(1, max_poll_pages)
        self._clock = clock
        # Scheduling protection ONLY - durable dedup remains SQLite.
        self._run_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    def _search_window(
        self, gmail: ThreadSource, query: str
    ) -> tuple[Sequence[ThreadSummary], RetrievalStatus, list[str]]:
        """Continuation-aware window search when available; bounded fallback
        otherwise. Page tokens never escape this call."""
        window_search = getattr(gmail, "search_window", None)
        if callable(window_search):
            return window_search(
                query, max_threads=self._max_poll_threads, max_pages=self._max_poll_pages
            )
        return gmail.search(query, limit=self._search_limit)

    def _window_query(self, start: datetime, end: datetime) -> str:
        return f"{self._query} after:{int(start.timestamp())} before:{int(end.timestamp())}"

    # ------------------------------------------------------------------ #
    @staticmethod
    def _window_qualified(
        evidence: ThreadEvidence, window_start: datetime, window_end: datetime
    ) -> tuple[list[tuple[NormalizedSourceEvent, datetime]], list[str]]:
        """Messages of a thread whose GMAIL internalDate falls inside the
        poll window - never the whole thread history (remediation PART 2-9).

        Gmail returns a thread when ANY message matches ``after:/before:``,
        and ``threads.get`` then hands back older messages too. Provider
        internalDate owns membership: the RFC Date header is sender-controlled
        and only feeds the canonical event, never this filter. Convention:
        INCLUSIVE on both ends - the overlap plus durable dedup guarantee no
        loss at boundaries. Returns (eligible, uncertain) where uncertain
        means some message lacked a trustworthy internalDate."""
        eligible: list[tuple[NormalizedSourceEvent, datetime]] = []
        uncertain_ids: list[str] = []
        for event in evidence.messages:
            internal_at = evidence.internal_dates.get(event.source_id)
            if internal_at is None:
                # No provider timestamp: not safely window-qualified. The A02
                # note already marks the retrieval PARTIAL; never guess.
                uncertain_ids.append(event.source_id)
                continue
            if window_start <= internal_at <= window_end:
                eligible.append((event, internal_at))
            # Older thread history is intentionally ignored this run - it
            # stays untouched (not seen, not emitted) for its own window.
        return eligible, uncertain_ids

    # ------------------------------------------------------------------ #
    def check_now(self) -> IngestionRunResult:
        """The SAME real ingestion path as polling - never a fixture."""
        return self.poll_recent()

    def poll_recent(self) -> IngestionRunResult:
        if not self._run_lock.acquire(blocking=False):
            # Stable busy result; nothing Gmail-related started (PART XXXIII).
            return IngestionRunResult(
                retrieval_status="in_progress",
                notes=("another Gmail ingestion run is active",),
            )
        try:
            return self._run()
        finally:
            self._run_lock.release()

    # ------------------------------------------------------------------ #
    def _run(self) -> IngestionRunResult:
        raw_now = self._clock()
        if raw_now.tzinfo is None or raw_now.tzinfo.utcoffset(raw_now) is None:
            raise ValueError("ingestion clock must return timezone-aware datetimes")
        # ONE canonical UTC representation for queries and persisted cursors.
        poll_started_at = raw_now.astimezone(timezone.utc)

        # Short DB READ (transaction closed before any network work, PART XXXIV).
        cursor_raw = self._cursors.get(GMAIL_CURSOR_KEY)
        if cursor_raw is None:
            return self._baseline(poll_started_at)
        try:
            high_water = datetime.fromisoformat(cursor_raw)
            # A naive stored value must NEVER silently become host-local time.
            if high_water.tzinfo is None or high_water.tzinfo.utcoffset(high_water) is None:
                raise ValueError("stored cursor is not timezone-aware")
        except (ValueError, TypeError):
            logger.error("gmail cursor is unreadable; refusing to guess a window")
            return IngestionRunResult(
                retrieval_status="failed",
                notes=("stored cursor is unreadable or not timezone-aware",),
            )
        return self._poll_window(high_water.astimezone(timezone.utc), poll_started_at)

    # -- first run ------------------------------------------------------ #
    def _baseline(self, poll_started_at: datetime) -> IngestionRunResult:
        """Establish 'everything before now is historical' from a BOUNDED
        recent window (remediation PART 11-13).

        The baseline deliberately does NOT enumerate the historical mailbox:
        it queries [poll_started_at - overlap, poll_started_at], marks the
        discovered message ids seen, emits zero Attention items and sets the
        cursor only when that bounded retrieval completed. Older mail stays
        un-fetched on purpose - subsequent windows start from the cursor."""
        window_start = poll_started_at - timedelta(seconds=self._overlap_seconds)
        uncertain_ids: list[str] = []
        try:
            gmail = self._gmail_source_factory()
            summaries, status, _notes = self._search_window(
                gmail, self._window_query(window_start, poll_started_at)
            )
            discovered: list[str] = []
            for summary in summaries:
                evidence = gmail.get_thread(summary.thread_id)
                # CURSOR SAFETY, not evidence fidelity: benign A02 notes (HTML
                # fallback, skipped attachment, bounded body, missing RFC Date)
                # never hold the cursor; structural loss (lost/unqualifiable
                # message) does. Search-level PARTIAL still blocks below.
                if not getattr(evidence, "ingestion_complete", False):
                    status = RetrievalStatus.PARTIAL
                # Baseline marks seen ONLY messages whose provider timestamp
                # belongs to the baseline window - thread history stays
                # historical and untouched (remediation PART 8).
                qualified, uncertain = self._window_qualified(
                    evidence, window_start, poll_started_at
                )
                discovered.extend(event.source_id for event, _ in qualified)
                uncertain_ids.extend(uncertain)
        except Exception as exc:  # sanitized: class name only, never bodies
            logger.error("gmail baseline retrieval failed (%s)", type(exc).__name__)
            return IngestionRunResult(
                retrieval_status="failed", notes=("gmail baseline retrieval failed",)
            )

        marked = 0
        for source_id in discovered:
            if self._cursors.mark_seen("gmail", source_id, now=poll_started_at):
                marked += 1

        notes: list[str] = []
        for source_id in dict.fromkeys(uncertain_ids):
            notes.append(
                f"gmail:{source_id}: no usable provider internalDate; treated as uncertain"
            )
        if status is RetrievalStatus.COMPLETE:
            # Cursor advances ONLY on a safely completed baseline.
            self._cursors.set(GMAIL_CURSOR_KEY, poll_started_at.isoformat(), now=poll_started_at)
        else:
            notes.append("baseline incomplete: cursor not advanced")
        logger.info(
            "gmail baseline: discovered=%d marked_seen=%d status=%s",
            len(discovered), marked, status.value,
        )
        return IngestionRunResult(
            checked_count=len(discovered),
            duplicate_count=len(discovered) - marked,
            new_items=(),
            baseline_count=len(discovered),
            retrieval_status="complete" if status is RetrievalStatus.COMPLETE else "partial",
            notes=tuple(notes),
        )

    # -- steady-state poll ---------------------------------------------- #
    def _poll_window(self, high_water: datetime, poll_started_at: datetime) -> IngestionRunResult:
        window_start = high_water - timedelta(seconds=self._overlap_seconds)
        uncertain_ids: list[str] = []
        try:
            gmail = self._gmail_source_factory()
            summaries, status, _notes = self._search_window(
                gmail, self._window_query(window_start, poll_started_at)
            )
            # Only messages whose Gmail internalDate belongs to THIS window
            # are eligible - thread history returned by threads.get is not
            # (remediation PART 7/9). Processing order follows the provider
            # arrival clock, never the sender-controlled Date header.
            events: list[tuple[NormalizedSourceEvent, datetime]] = []
            for summary in summaries:
                evidence = gmail.get_thread(summary.thread_id)
                # Cursor safety uses the INGESTION-completeness signal only:
                # PARTIAL retrieval_status from benign fidelity notes (HTML
                # normalized, attachment skipped, bounded body, missing RFC
                # Date with valid internalDate) must NOT stall the cursor -
                # advancing means every window message was canonically
                # represented, not that every byte was retrieved perfectly.
                if not getattr(evidence, "ingestion_complete", False):
                    status = RetrievalStatus.PARTIAL
                qualified, uncertain = self._window_qualified(
                    evidence, window_start, poll_started_at
                )
                events.extend(qualified)
                uncertain_ids.extend(uncertain)
        except Exception as exc:  # sanitized class name only
            logger.error("gmail retrieval failed (%s)", type(exc).__name__)
            return IngestionRunResult(
                retrieval_status="failed", notes=("gmail retrieval failed",)
            )

        # Durable-dedup READ (short, closed) then stable processing order:
        # Gmail internalDate ASC, then source_id (PART XXXVII + remediation).
        fresh: list[tuple[NormalizedSourceEvent, datetime]] = []
        duplicates = 0
        seen_in_batch: set[str] = set()  # within-run repeat guard only; the
        for event, internal_at in events:  # durable authority stays SQLite.
            if event.source_id in seen_in_batch or self._cursors.is_seen("gmail", event.source_id):
                duplicates += 1
                continue
            seen_in_batch.add(event.source_id)
            fresh.append((event, internal_at))
        fresh.sort(key=lambda pair: (pair[1], pair[0].source_id))

        new_items: list[AttentionItem] = []
        notes: list[str] = []
        for source_id in dict.fromkeys(uncertain_ids):
            notes.append(
                f"gmail:{source_id}: no usable provider internalDate; treated as uncertain"
            )
        failed_source: str | None = None
        for event, _internal_at in fresh:
            if self._sink is None:
                # Downstream not wired yet (B04 handoff): leave UNSEEN.
                notes.append("attention sink not wired; sources left unseen")
                failed_source = event.source_id
                break
            try:
                item = self._sink.ingest(event)
                stored = self._attention_repo.add(item)
            except Exception as exc:  # sanitized id + class, never the body
                logger.error(
                    "downstream ingestion failed for gmail:%s (%s)",
                    event.source_id, type(exc).__name__,
                )
                notes.append(f"downstream ingestion failed for gmail:{event.source_id}")
                # Stop the batch: later messages must not advance past N.
                failed_source = event.source_id
                break
            if stored:
                new_items.append(item)
            else:
                duplicates += 1  # canonical UNIQUE already holds this source
            # ONLY after successful downstream handling (PART XXXII).
            self._cursors.mark_seen("gmail", event.source_id, now=poll_started_at)

        retrieval = "complete" if status is RetrievalStatus.COMPLETE else "partial"
        if failed_source is not None:
            retrieval = "partial"
        else:
            # Advance ONLY when the whole batch processed AND retrieval was
            # complete - never past known-unprocessed data (PART XXVIII).
            if status is RetrievalStatus.COMPLETE:
                self._cursors.set(GMAIL_CURSOR_KEY, poll_started_at.isoformat(), now=poll_started_at)
            else:
                notes.append("retrieval partial: cursor not advanced")

        logger.info(
            "gmail poll: checked=%d duplicates=%d new=%d status=%s",
            len(events), duplicates, len(new_items), retrieval,
        )
        return IngestionRunResult(
            checked_count=len(events),
            duplicate_count=duplicates,
            new_items=tuple(new_items),
            baseline_count=0,
            retrieval_status=retrieval,
            notes=tuple(notes),
        )
