"""One-click demo reset and Focus-interrupt injection for demo mode.

Both operations drive the REAL pipeline: they only swap the synthetic Gmail
source and call the application's single ``GmailIngestionService``, so items go
through the real rules, the real Decision projection and the real Focus
delivery policy. Nothing here talks to Google, and nothing here approves or
executes anything - the guarded approval lifecycle stays untouched.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from ..db.repositories import AttentionRepository, DecisionRepository
from .fixtures import FOCUS_NORMAL, FOCUS_URGENT, DemoMessage
from .source import DemoGmailSource

logger = logging.getLogger("eva.demo")

#: Wipe order: FK children before parents. ``meta`` (schema version) and
#: ``sessions`` stay untouched - they are not demo content.
_WIPE_ORDER: tuple[str, ...] = (
    "approval_receipts",
    "execution_attempts",
    "google_event_ids",
    "action_execution_results",
    "proposal_idempotency",
    "proposed_actions",
    "decisions",
    "attention_items",
    "focus_completion_summaries",
    "focus_sessions",
    "event_outbox",
    "seen_sources",
    "ingestion_cursors",
)

INJECTABLE: dict[str, DemoMessage] = {"normal": FOCUS_NORMAL, "urgent": FOCUS_URGENT}


class DemoResetError(RuntimeError):
    """Raised when the real ingestion run could not complete a demo step."""


class DemoDataService:
    def __init__(
        self,
        *,
        db,
        source: DemoGmailSource,
        ingestion_service,
        attention_repo: AttentionRepository,
        decision_repo: DecisionRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self._db = db
        self._source = source
        self._ingestion = ingestion_service
        self._attention = attention_repo
        self._decisions = decision_repo
        self._clock = clock

    # ------------------------------------------------------------------ #
    def wipe(self) -> None:
        with self._db.connect() as conn:
            for table in _WIPE_ORDER:
                conn.execute(f"DELETE FROM {table}")

    def reset(self) -> dict:
        """Restore the canonical demo state deterministically (repeatable)."""
        self.wipe()
        self._source.clear()

        # 1) Establish the ingestion cursor on an empty mailbox, exactly like a
        #    real first connection; this run emits zero items by design.
        baseline = self._ingestion.poll_recent()
        if baseline.retrieval_status not in ("complete",):
            raise DemoResetError(f"demo baseline incomplete: {baseline.retrieval_status}")

        # 2) Load the canonical dataset and let the real pipeline classify it.
        loaded = self._source.load_fixtures(stamp=self._clock())
        run = self._ingestion.poll_recent()
        if run.retrieval_status not in ("complete",):
            raise DemoResetError(f"demo ingestion incomplete: {run.retrieval_status}")

        items = self._attention.list_latest()
        decisions = self._decisions.list_all()
        logger.info(
            "demo reset: fixtures=%d items=%d decisions=%d", loaded, len(items), len(decisions)
        )
        return {
            "fixtures_loaded": loaded,
            "checked": run.checked_count,
            "attention_items": len(items),
            "decisions": len(decisions),
        }

    def inject(self, kind: str) -> dict:
        """Inject one live message while the app runs (Focus interrupt demo)."""
        message = INJECTABLE.get(kind)
        if message is None:
            raise ValueError(f"unknown demo injection kind: {kind!r}")
        source_id = self._source.append(message, stamp=self._clock())
        run = self._ingestion.poll_recent()
        if run.retrieval_status not in ("complete", "partial"):
            raise DemoResetError(f"demo injection run failed: {run.retrieval_status}")

        created = [item for item in run.new_items if item.source_id == source_id]
        item = created[0] if created else None
        return {
            "kind": kind,
            "source_id": source_id,
            "attention_item_id": item.id if item is not None else None,
            "priority": item.priority.value if item is not None else None,
            "attention_type": item.attention_type.value if item is not None else None,
            "urgent": bool(item.urgent) if item is not None else False,
        }

    def status(self) -> dict:
        items = self._attention.list_latest()
        return {
            "provider": "demo",
            "messages_available": self._source.message_count,
            "attention_items": len(items),
            "decisions": len(self._decisions.list_all()),
        }
