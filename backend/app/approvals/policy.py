"""Authoritative policy configuration (A03): strict loading + versioning.

Security properties enforced here:

- ``yaml.safe_load`` only; no Python-object constructors exist for this file.
- Every field is REQUIRED and unknown fields are forbidden, so a malformed or
  tampered policy fails closed - there is no permissive default anywhere.
- The HIGH approval channel set cannot be weakened by configuration: the
  loader rejects any policy that lets HIGH skip approval or use voice.
- ``policy_version`` is derived from the validated content (canonical JSON ->
  SHA-256), never from a hand-edited label; it changes whenever authoritative
  policy content changes. Callers must treat a version change as invalidating
  outstanding proposals.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.domain import ActionRisk, ApprovalChannel

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[1] / "policy" / "POLICY.yaml"


class PolicyConfigError(ValueError):
    """Policy is missing, malformed or semantically invalid. Writes must fail
    closed whenever this is raised - never fall back to a permissive policy."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PolicyIdentity(_Strict):
    schema_version: int = Field(ge=1)
    name: str = Field(min_length=1)


class ProposalPolicy(_Strict):
    ttl_seconds: int = Field(gt=0, le=86_400)  # zero/negative TTL is invalid


class FinancialHighPolicy(_Strict):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    threshold_minor_units: int = Field(ge=1)

    @model_validator(mode="after")
    def _v1_single_currency(self) -> "FinancialHighPolicy":
        if self.currency != "PLN":
            raise ValueError(
                "policy v1 supports exactly PLN for the financial HIGH rule; "
                "no currency conversion is performed"
            )
        return self


class MeetingTriagePolicy(_Strict):
    high_markers: list[str] = Field(min_length=1)
    medium_markers: list[str] = Field(min_length=1)
    low_markers: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _markers_are_clean(self) -> "MeetingTriagePolicy":
        for group in (self.high_markers, self.medium_markers, self.low_markers):
            for marker in group:
                if not marker.strip() or marker != marker.strip():
                    raise ValueError("markers must be non-empty without padding")
        return self


class ActionRiskPolicy(_Strict):
    read_floor: ActionRisk
    calendar_mutation_floor: ActionRisk
    local_write_floor: ActionRisk

    @model_validator(mode="after")
    def _floors_are_sane(self) -> "ActionRiskPolicy":
        if self.calendar_mutation_floor == ActionRisk.LOW:
            raise ValueError(
                "calendar mutations can never have a LOW risk floor (plan v2.0)"
            )
        return self


class NotificationEscalationPolicy(_Strict):
    notify_values: list[str] = Field(min_length=1)
    escalate_external_notifications_to_high: bool


class ApprovalPolicy(_Strict):
    low_requires_approval: bool
    low_channels: list[ApprovalChannel] = Field(min_length=1)
    medium_requires_approval: bool
    medium_channels: list[ApprovalChannel] = Field(min_length=1)
    high_requires_approval: bool
    high_channels: list[ApprovalChannel] = Field(min_length=1)

    @model_validator(mode="after")
    def _high_is_inviolable(self) -> "ApprovalPolicy":
        if not self.high_requires_approval:
            raise ValueError("HIGH-risk actions always require approval; policy cannot opt out")
        if set(self.high_channels) != {ApprovalChannel.UI}:
            raise ValueError("HIGH-risk approval channels must be exactly [ui]")
        if not self.medium_requires_approval:
            raise ValueError("MEDIUM-risk actions require explicit approval in v1")
        return self


class PolicyConfig(_Strict):
    policy: PolicyIdentity
    proposal: ProposalPolicy
    financial_high: FinancialHighPolicy
    meeting_triage: MeetingTriagePolicy
    action_risk: ActionRiskPolicy
    notification_escalation: NotificationEscalationPolicy
    approval: ApprovalPolicy

    def canonical_json(self) -> str:
        """Canonical serialization used for the content-derived version."""
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )


@dataclass(frozen=True)
class LoadedPolicy:
    config: PolicyConfig
    version: str
    source_path: Path | None = None

    @property
    def schema_version(self) -> int:
        return self.config.policy.schema_version


def compute_version(config: PolicyConfig) -> str:
    digest = hashlib.sha256(config.canonical_json().encode("utf-8")).hexdigest()
    return f"v{config.policy.schema_version}-{digest[:16]}"


def load_policy(path: Path | None = None) -> LoadedPolicy:
    """Load, strictly validate and version the authoritative policy.

    Any failure raises PolicyConfigError; callers must fail closed for writes.
    """
    policy_path = path if path is not None else DEFAULT_POLICY_PATH
    try:
        raw_text = policy_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyConfigError("policy file could not be read") from exc
    try:
        parsed = yaml.safe_load(raw_text)  # safe loader only, ever
    except yaml.YAMLError as exc:
        raise PolicyConfigError("policy file is not valid YAML") from exc
    if not isinstance(parsed, dict):
        raise PolicyConfigError("policy root must be a mapping")
    try:
        config = PolicyConfig.model_validate(parsed)
    except Exception as exc:  # pydantic ValidationError -> our closed error type
        raise PolicyConfigError(f"policy configuration is invalid: {exc}") from exc
    return LoadedPolicy(config=config, version=compute_version(config), source_path=policy_path)
