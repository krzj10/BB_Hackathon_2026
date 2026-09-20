"""Deterministic in-memory Gmail double for demo mode.

Implements the frozen ``ThreadSource`` surface that
:class:`app.attention.ingest.GmailIngestionService` consumes - including Gmail
``after:/before:`` window semantics and provider ``internalDate`` membership -
so the REAL ingestion pipeline runs unchanged over synthetic data. It performs
no network I/O of any kind.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from ..contracts.domain import (
    NormalizedSourceEvent,
    RetrievalStatus,
    SourceKind,
    SourceRef,
    SourceSystem,
)
from ..google.gmail import ThreadEvidence, ThreadSummary
from .fixtures import DEMO_MESSAGES, DemoMessage

#: Fixture spacing inside the ingestion window: 30 messages * 2s stays well
#: inside EVA_GMAIL_POLL_OVERLAP_SECONDS (default 120).
_FIXTURE_SPACING_SECONDS = 2


def _event(message: DemoMessage, source_id: str, stamp: datetime) -> NormalizedSourceEvent:
    return NormalizedSourceEvent(
        source=SourceSystem.GMAIL,
        source_id=source_id,
        sender_email=message.sender_email,
        subject=message.subject,
        body=message.body,
        received_at=stamp,
        sources=[
            SourceRef(
                id=f"gmail:{source_id}",
                kind=SourceKind.GMAIL_MESSAGE,
                resource_id=source_id,
                title=message.subject,
                retrieved_at=stamp,
            )
        ],
    )


class DemoGmailSource:
    """Synthetic mailbox with Gmail window semantics (no network, no OAuth)."""

    def __init__(self) -> None:
        # source_id -> (event, provider internalDate)
        self._messages: dict[str, tuple[NormalizedSourceEvent, datetime]] = {}
        # thread_id -> ordered source_ids
        self._threads: dict[str, list[str]] = {}
        self._injection_seq = 0

    # -- demo state ------------------------------------------------------- #
    def clear(self) -> None:
        self._messages.clear()
        self._threads.clear()
        self._injection_seq = 0

    def load_fixtures(self, *, stamp: datetime) -> int:
        """Load the canonical dataset newest-first, deterministically spaced
        backwards from ``stamp`` so every fixture is inside one poll window."""
        for index, message in enumerate(DEMO_MESSAGES):
            received = stamp - timedelta(seconds=_FIXTURE_SPACING_SECONDS * index)
            self._put(message, source_id=message.fixture_id, stamp=received)
        return len(self._messages)

    def append(self, message: DemoMessage, *, stamp: datetime) -> str:
        """Inject one live message (used by the Focus interrupt demo)."""
        self._injection_seq += 1
        source_id = f"{message.fixture_id}-{self._injection_seq}"
        self._put(message, source_id=source_id, stamp=stamp)
        return source_id

    def _put(self, message: DemoMessage, *, source_id: str, stamp: datetime) -> None:
        trimmed = stamp.replace(microsecond=0)
        self._messages[source_id] = (_event(message, source_id, trimmed), trimmed)
        self._threads.setdefault(f"thr-{source_id}", []).append(source_id)

    @property
    def message_count(self) -> int:
        return len(self._messages)

    # -- ThreadSource surface --------------------------------------------- #
    def _matching_threads(self, query: str) -> dict[str, list[str]]:
        after = re.search(r"after:(\d+)", query)
        before = re.search(r"before:(\d+)", query)
        low = int(after.group(1)) if after else None
        high = int(before.group(1)) if before else None
        threads: dict[str, list[str]] = {}
        for thread_id, source_ids in self._threads.items():
            for source_id in source_ids:
                internal_at = self._messages[source_id][1]
                stamp = internal_at.timestamp()
                if low is not None and stamp < low:
                    continue
                if high is not None and stamp > high:
                    continue
                threads.setdefault(thread_id, []).append(source_id)
        return threads

    def search_window(
        self, query: str, *, max_threads: int = 100, max_pages: int = 8
    ) -> tuple[list[ThreadSummary], RetrievalStatus, list[str]]:
        summaries = [
            ThreadSummary(
                thread_id=thread_id,
                message_ids=tuple(source_ids),
                subject=None,
                snippet=None,
            )
            for thread_id, source_ids in self._matching_threads(query).items()
        ]
        return summaries[: max(1, int(max_threads))], RetrievalStatus.COMPLETE, []

    def search(
        self, query: str, limit: int = 25, **kwargs: object
    ) -> tuple[list[ThreadSummary], RetrievalStatus, list[str]]:
        summaries, status, notes = self.search_window(query)
        return summaries[: max(1, int(limit))], status, notes

    def get_thread(self, thread_id: str) -> ThreadEvidence:
        source_ids = self._threads.get(thread_id, [])
        messages = [self._messages[source_id][0] for source_id in source_ids]
        internal = {
            source_id: self._messages[source_id][1] for source_id in source_ids
        }
        return ThreadEvidence(
            thread_id=thread_id,
            messages=messages,
            retrieval_status=RetrievalStatus.COMPLETE,
            internal_dates=internal,
        )
