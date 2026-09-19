"""The single canonical ToolRegistry (A04).

Exactly one registry exists per application; it is the frozen boundary for
what the agent layer may ever call:

- ``get(name)`` returns the ToolDefinition plus validators/metadata, or
  fails closed with RegistryError - unknown tools never execute.
- Effects and risk floors agree with A03 policy (calendar mutations are
  EXTERNAL_WRITE with at least MEDIUM floor; focus commands stay LOW local
  commands; decision.record_outcome is LOCAL_WRITE only).
- Approval is NOT a tool, and no payment/purchase/contract/legal/supplier
  or destructive/send-capable tool can ever be registered: the forbidden
  name set below is rejected by construction. The LLM must never be able to
  invoke approval or commitments.

Risk decisions stay with the A03 engine; this registry only declares what
exists and how it must be treated - it is deliberately NOT a second policy
engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Type

from pydantic import BaseModel, Field

from app.approvals.policy import LoadedPolicy
from app.contracts.domain import (
    ActionRisk,
    CalendarCreateEventArguments,
    CalendarRescheduleEventArguments,
    CalendarUpdateAgendaArguments,
    DecisionRecordOutcomeArguments,
    FocusStartArguments,
    FocusStopArguments,
    ToolDefinition,
    ToolEffect,
)
from app.google.auth import CALENDAR_SCOPE, GMAIL_READONLY_SCOPE


class RegistryError(Exception):
    """Fail-closed registry failure with a stable machine code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


#: Names that can NEVER be registered. Approval is executed only through the
#: A03 confirmation flow; commitments and destructive/send operations are not
#: enabled tools of this demo platform.
FORBIDDEN_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "approval.confirm",
        "approval.override",
        "payment.execute",
        "purchase.execute",
        "contract.sign",
        "supplier.commit",
        "legal.commit",
        "calendar.delete_event",
        "gmail.send",
        "gmail.modify",
        "execute.action",
    }
)


# --------------------------------------------------------------------------- #
# Internal argument models for READ tools (server-internal validation only;
# the canonical mutation argument models come from contracts.domain).
# --------------------------------------------------------------------------- #


class CalendarListEventsArgs(BaseModel):
    model_config = {"extra": "forbid"}

    calendar_id: str = Field(default="primary", min_length=1)
    time_min: str | None = None
    time_max: str | None = None
    max_results: int = Field(default=50, ge=1, le=250)


class CalendarGetEventArgs(BaseModel):
    model_config = {"extra": "forbid"}

    calendar_id: str = Field(default="primary", min_length=1)
    event_id: str = Field(min_length=1)


class GmailSearchArgs(BaseModel):
    model_config = {"extra": "forbid"}

    query: str = Field(min_length=1)
    max_results: int = Field(default=20, ge=1, le=100)


class GmailGetThreadArgs(BaseModel):
    model_config = {"extra": "forbid"}

    thread_id: str = Field(min_length=1)


@dataclass(frozen=True)
class RegisteredTool:
    """Frozen registry entry: definition + server-side validation metadata."""

    definition: ToolDefinition
    args_model: Type[BaseModel]

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def effect(self) -> ToolEffect:
        return self.definition.effect

    @property
    def risk_floor(self) -> ActionRisk:
        return self.definition.risk_floor

    @property
    def required_scopes(self) -> tuple[str, ...]:
        return tuple(self.definition.required_scopes)

    def validate(self, arguments: dict[str, Any]) -> BaseModel:
        try:
            return self.args_model.model_validate(arguments)
        except Exception as exc:  # pydantic ValidationError and friends
            raise RegistryError(
                "invalid_arguments", f"arguments failed canonical validation for {self.name}"
            ) from exc


class ToolRegistry:
    """Immutable tool table. Unknown tools fail closed; forbidden names can
    never be (re)registered."""

    def __init__(self, entries: list[RegisteredTool]) -> None:
        self._by_name: dict[str, RegisteredTool] = {}
        for entry in entries:
            if entry.name in FORBIDDEN_TOOL_NAMES:
                raise RegistryError(
                    "forbidden_tool", f"tool {entry.name!r} may never be registered"
                )
            if entry.name in self._by_name:
                raise RegistryError("duplicate_tool", f"tool {entry.name!r} registered twice")
            self._by_name[entry.name] = entry

    def get(self, name: str) -> RegisteredTool:
        entry = self._by_name.get(name)
        if entry is None:
            raise RegistryError("unknown_tool", f"tool {name!r} is not registered")
        return entry

    def names(self) -> frozenset[str]:
        return frozenset(self._by_name)


def _definition(
    name: str,
    description: str,
    effect: ToolEffect,
    args_model: Type[BaseModel],
    *,
    scopes: tuple[str, ...] = (),
    risk_floor: ActionRisk,
    timeout_seconds: int = 30,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        effect=effect,
        input_schema=args_model.model_json_schema(),
        # All tools return the canonical ToolResult envelope (contracts).
        output_schema={"$comment": "canonical ToolResult", "type": "object"},
        required_scopes=list(scopes),
        risk_floor=risk_floor,
        timeout_seconds=timeout_seconds,
    )


def build_default_registry(policy: LoadedPolicy) -> ToolRegistry:
    """The one registry. Floors are taken from the authoritative policy so
    they cannot drift from A03 semantics."""
    cfg = policy.config.action_risk
    calendar_mutation_floor = max(
        (cfg.calendar_mutation_floor, ActionRisk.MEDIUM),
        key=lambda r: {"low": 0, "medium": 1, "high": 2}[r.value],
    )
    entries = [
        # -- reads (immediate when authorized; never enter the approval
        #    lifecycle and never obtain mutation capability) ---------------
        RegisteredTool(
            _definition(
                "calendar.list_events",
                "List calendar events (read-only, bounded).",
                ToolEffect.READ,
                CalendarListEventsArgs,
                scopes=(CALENDAR_SCOPE,),
                risk_floor=ActionRisk.LOW,
            ),
            CalendarListEventsArgs,
        ),
        RegisteredTool(
            _definition(
                "calendar.get_event",
                "Read one calendar event (read-only).",
                ToolEffect.READ,
                CalendarGetEventArgs,
                scopes=(CALENDAR_SCOPE,),
                risk_floor=ActionRisk.LOW,
            ),
            CalendarGetEventArgs,
        ),
        RegisteredTool(
            _definition(
                "gmail.search",
                "Search recent Gmail threads (read-only, bounded).",
                ToolEffect.READ,
                GmailSearchArgs,
                scopes=(GMAIL_READONLY_SCOPE,),
                risk_floor=ActionRisk.LOW,
            ),
            GmailSearchArgs,
        ),
        RegisteredTool(
            _definition(
                "gmail.get_thread",
                "Read one Gmail thread (read-only).",
                ToolEffect.READ,
                GmailGetThreadArgs,
                scopes=(GMAIL_READONLY_SCOPE,),
                risk_floor=ActionRisk.LOW,
            ),
            GmailGetThreadArgs,
        ),
        # -- calendar mutations (EXTERNAL_WRITE; A03 policy governs approval)
        RegisteredTool(
            _definition(
                "calendar.create_event",
                "Create a calendar event (guarded external write).",
                ToolEffect.EXTERNAL_WRITE,
                CalendarCreateEventArguments,
                scopes=(CALENDAR_SCOPE,),
                risk_floor=calendar_mutation_floor,
            ),
            CalendarCreateEventArguments,
        ),
        RegisteredTool(
            _definition(
                "calendar.reschedule_event",
                "Reschedule a calendar event (guarded external write).",
                ToolEffect.EXTERNAL_WRITE,
                CalendarRescheduleEventArguments,
                scopes=(CALENDAR_SCOPE,),
                risk_floor=calendar_mutation_floor,
            ),
            CalendarRescheduleEventArguments,
        ),
        RegisteredTool(
            _definition(
                "calendar.update_agenda",
                "Add/update the EVA agenda section of an event description "
                "(guarded external write; unrelated text preserved).",
                ToolEffect.EXTERNAL_WRITE,
                CalendarUpdateAgendaArguments,
                scopes=(CALENDAR_SCOPE,),
                risk_floor=calendar_mutation_floor,
            ),
            CalendarUpdateAgendaArguments,
        ),
        # -- local writes ----------------------------------------------------
        RegisteredTool(
            _definition(
                "decision.record_outcome",
                "Record a Decision Inbox outcome locally. Executes no payment, "
                "purchase, supplier or contract commitment.",
                ToolEffect.LOCAL_WRITE,
                DecisionRecordOutcomeArguments,
                risk_floor=cfg.local_write_floor,
            ),
            DecisionRecordOutcomeArguments,
        ),
        RegisteredTool(
            _definition(
                "focus.start",
                "Start a local focus window (explicit low-risk command).",
                ToolEffect.LOCAL_WRITE,
                FocusStartArguments,
                risk_floor=ActionRisk.LOW,
            ),
            FocusStartArguments,
        ),
        RegisteredTool(
            _definition(
                "focus.stop",
                "Stop the active local focus window (explicit low-risk command).",
                ToolEffect.LOCAL_WRITE,
                FocusStopArguments,
                risk_floor=ActionRisk.LOW,
            ),
            FocusStopArguments,
        ),
    ]
    return ToolRegistry(entries)
