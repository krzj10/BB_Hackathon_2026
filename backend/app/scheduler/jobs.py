"""One background poller per application driving the REAL A06 ingestion path.

The scheduler is deliberately thin: it calls the SAME
GmailIngestionService.poll_recent() as POST /api/attention/check-now (never a
second Gmail pipeline), and non-overlap is owned by the ingestion service's
own run lock - if check-now is mid-run the poll gets an honest in_progress
result. Every failure is logged sanitized and merely retried next interval;
the worker never crashes the app and never advances state on partial runs
(cursor semantics live in A06).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Callable

from ..attention.publish import publish_new_items

logger = logging.getLogger("eva.scheduler")

DEFAULT_POLL_INTERVAL_SECONDS = 30


class GmailPollScheduler:
    def __init__(
        self,
        *,
        ingestion_service,
        outbox_repo=None,
        attention_repo=None,
        decision_repo=None,
        interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        enabled_provider: Callable[[], bool] | None = None,
    ) -> None:
        if interval_seconds < 1:
            raise ValueError("poll interval must be >= 1 second")
        self._ingestion = ingestion_service
        self._outbox = outbox_repo
        self._attention_repo = attention_repo
        self._decision_repo = decision_repo
        self._interval = interval_seconds
        self._clock = clock
        # Optional gate: while Gmail is not authorized the worker idles without
        # provoking failing network attempts (honest no-op, zero requests).
        self._enabled = enabled_provider
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return  # exactly one worker per instance
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="eva-gmail-poller", daemon=True
        )
        self._thread.start()
        logger.info("gmail poll scheduler started (every %ss)", self._interval)

    def stop(self, *, timeout_seconds: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout_seconds)
        self._thread = None

    # ------------------------------------------------------------------ #
    def run_once(self):
        """One poll cycle + live-event publication (also used directly in
        tests). Returns the ingestion result for inspection."""
        result = self._ingestion.poll_recent()
        if result.new_items and self._outbox is not None:
            publish_new_items(
                self._outbox,
                self._attention_repo,
                self._decision_repo,
                result,
                now=self._clock(),
            )
        return result

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            if self._enabled is not None and not self._enabled():
                continue
            try:
                result = self.run_once()
                if result.retrieval_status == "failed":
                    logger.warning("gmail poll cycle reported failed retrieval")
            except Exception as exc:  # the worker must never die on one cycle
                logger.error("gmail poll cycle raised (%s)", type(exc).__name__)
