"""Live-event publication for freshly ingested Attention items (B04 basic).

Shared by the check-now route and the scheduler so BOTH ingestion entry points
emit the same canonical events exactly once per stored item. This is the
"basic authenticated live events + REST snapshot" scope of plan §9: durable
delivery/replay hardening stays A07. Producers deduplicate by deriving stable
event ids from the Attention item id, so a re-run can never double-publish."""

from __future__ import annotations

import logging

from ..contracts.domain import (
    AttentionItemCreatedPayload,
    DecisionCreatedPayload,
    EventEnvelope,
    EventType,
)
from .focus import SYSTEM_EVENT_SESSION

logger = logging.getLogger("eva.attention.publish")


def publish_new_items(outbox, attention_repo, decision_repo, result, *, now) -> None:
    """Append ATTENTION_ITEM_CREATED (+ DECISION_CREATED) for each genuinely
    new item of an IngestionRunResult. Stable event ids make this idempotent
    against accidental double-calls; duplicate-key hits are swallowed."""
    if outbox is None:
        return
    for item in result.new_items:
        try:
            outbox.append(
                EventEnvelope(
                    event_id=f"evt-attention-{item.id}",
                    sequence=0,
                    session_id=SYSTEM_EVENT_SESSION,
                    occurred_at=now,
                    type=EventType.ATTENTION_ITEM_CREATED,
                    payload=AttentionItemCreatedPayload(item=item),
                )
            )
        except Exception as exc:  # IntegrityError on stable-id replay etc.
            _note(outbox, item.id, exc)
        if item.decision_id is not None:
            decision = decision_repo.get_by_attention_item(item.id)
            if decision is None:
                continue
            try:
                outbox.append(
                    EventEnvelope(
                        event_id=f"evt-decision-{decision.id}",
                        sequence=0,
                        session_id=SYSTEM_EVENT_SESSION,
                        occurred_at=now,
                        type=EventType.DECISION_CREATED,
                        payload=DecisionCreatedPayload(decision=decision),
                    )
                )
            except Exception as exc:
                _note(outbox, decision.id, exc)


def _note(outbox, entity_id: str, exc: Exception) -> None:
    """A failed live-event append never undoes durable state; REST snapshots
    stay authoritative and the failure is only logged (class name)."""
    logger.error("live-event append failed for %s (%s)", entity_id, type(exc).__name__)
