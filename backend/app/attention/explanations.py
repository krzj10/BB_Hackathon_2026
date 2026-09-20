"""B04 Attention explanations: read-only views over STORED classification data.

The explanation endpoint must never newly invent an LLM justification (frozen
contract note in contracts/api.py): everything served here was persisted with
the item at ingestion time - rule codes, origins, evidence pointers and the
policy version that produced them."""

from __future__ import annotations

from ..contracts.api import AttentionExplanationResponse
from ..contracts.domain import AttentionItem


def build_explanation(item: AttentionItem, *, policy_version: str) -> AttentionExplanationResponse:
    return AttentionExplanationResponse(
        item_id=item.id,
        priority_reasons=list(item.reasons),
        delivery_reasons=list(item.delivery_reasons),
        policy_version=policy_version,
        sources=list(item.sources),
    )
