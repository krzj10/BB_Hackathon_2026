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
- a source is marked seen ONLY AFTER successful downstream ingestion, so a
  transient failure retries on the next overlapping poll;
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
from ..google.gmail import DEFAULT_SEARCH_LIMIT, ThreadEvidence, ThreadSummary

logger = logging.getLogger("eva.attention.ingest")

#: Single documented cursor key for Gmail (PART XXVII).
GMAIL_CURSOR_KEY = "gmail"
#: Conservative demo overlap: late-arriving messages are rediscovered safely.
DEFAULT_OVERLAP_SECONDS = 120


class ThreadSource(Protocol):
    """The A02 read-only Gmail surface this service consumes (frozen)."""

    def search(
        self, query: str, limit: int = ..., **kwargs: object
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
        self._clock = clock
        # Scheduling protection ONLY - durable dedup remains SQLite.
        self._run_lock = threading.Lock()

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
        poll_started_at = self._clock()
        if poll_started_at.tzinfo is None or poll_started_at.tzinfo.utcoffset(poll_started_at) is None:
            raise ValueError("ingestion clock must return timezone-aware datetimes")

        # Short DB READ (transaction closed before any network work, PART XXXIV).
        cursor_raw = self._cursors.get(GMAIL_CURSOR_KEY)
        if cursor_raw is None:
            return self._baseline(poll_started_at)
        try:
            high_water = datetime.fromisoformat(cursor_raw)
        except ValueError:
            logger.error("gmail cursor is unreadable; refusing to guess a window")
            return IngestionRunResult(
                retrieval_status="failed", notes=("stored cursor is unreadable",)
            )
        return self._poll_window(high_water, poll_started_at)

    # -- first run ------------------------------------------------------ #
    def _baseline(self, poll_started_at: datetime) -> IngestionRunResult:
        """Mark discovered ids seen WITHOUT emitting them as new (PART XXX)."""
        try:
            gmail = self._gmail_source_factory()
            summaries, status, _notes = gmail.search(self._query, limit=self._search_limit)
            discovered: list[str] = []
            for summary in summaries:
                evidence = gmail.get_thread(summary.thread_id)
                if evidence.retrieval_status is not RetrievalStatus.COMPLETE:
                    status = RetrievalStatus.PARTIAL
                discovered.extend(m.source_id for m in evidence.messages)
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
        start_epoch = int((high_water - timedelta(seconds=self._overlap_seconds)).timestamp())
        end_epoch = int(poll_started_at.timestamp())
        windowed_query = f"{self._query} after:{start_epoch} before:{end_epoch}"

        try:
            gmail = self._gmail_source_factory()
            summaries, status, _notes = gmail.search(windowed_query, limit=self._search_limit)
            events: list[NormalizedSourceEvent] = []
            for summary in summaries:
                evidence = gmail.get_thread(summary.thread_id)
                if evidence.retrieval_status is not RetrievalStatus.COMPLETE:
                    status = RetrievalStatus.PARTIAL
                events.extend(evidence.messages)
        except Exception as exc:  # sanitized class name only
            logger.error("gmail retrieval failed (%s)", type(exc).__name__)
            return IngestionRunResult(
                retrieval_status="failed", notes=("gmail retrieval failed",)
            )

        # Durable-dedup READ (short, closed) then stable processing order.
        fresh: list[NormalizedSourceEvent] = []
        duplicates = 0
        seen_in_batch: set[str] = set()  # within-run repeat guard only; the
        for event in events:             # durable authority stays SQLite.
            if event.source_id in seen_in_batch or self._cursors.is_seen("gmail", event.source_id):
                duplicates += 1
                continue
            seen_in_batch.add(event.source_id)
            fresh.append(event)
        fresh.sort(key=lambda e: (e.received_at, e.source_id))  # PART XXXVII

        new_items: list[AttentionItem] = []
        notes: list[str] = []
        failed_source: str | None = None
        for event in fresh:
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
