#!/usr/bin/env python3
"""Export EVA canonical contracts to JSON Schema and TypeScript (A00).

Single source of truth: backend/app/contracts/{domain,api,providers}.py.
There is no second manual model layer; frontend code must only use the
generated file and never edit it (docs/EVA_DEVELOPMENT_WORKFLOW.md section 6).

Artifacts written by this script:
- contracts/schema/eva.schema.json      self-contained Draft 2020-12 bundle
                                        (all $refs resolve at root $defs)
- frontend/src/api/types.generated.ts   sanitized TS types for Stream B

Usage:
    python scripts/export_contracts.py            # regenerate artifacts
    python scripts/export_contracts.py --check    # fail on drift (CI/gates)

Discriminated unions (MeetingSpan, CalendarProposalArguments, EventPayload)
are derived from Pydantic's own discriminator metadata - never hand-mapped:
- JSON Schema: each boundary entry additionally requires the discriminator
  property, so a member without it fails schema validation at the boundary.
- TypeScript: boundaries are emitted as RequireDiscriminator<Member, K> unions;
  EventEnvelope is emitted as an explicit correlated union (outer ``type`` must
  match the payload discriminator), derived from the mapping metadata.

Provider-only models (ChatMessage, LLMResponse, AudioInput, ...) are included
in the schema bundle but excluded from the TypeScript export unless referenced
by a frontend-facing API model: provider interfaces are backend-only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from pydantic import TypeAdapter  # noqa: E402

from app.contracts import api, domain, providers  # noqa: E402

SCHEMA_PATH = REPO_ROOT / "contracts" / "schema" / "eva.schema.json"
TS_PATH = REPO_ROOT / "frontend" / "src" / "api" / "types.generated.ts"

# Frontend-facing contracts, in documentation order. These define the TS export.
DOMAIN_MODELS: list[Any] = [
    domain.MeetingPriority,
    domain.ActionRisk,
    domain.AttentionPriority,
    domain.AttentionType,
    domain.ClaimKind,
    domain.ReasonOrigin,
    domain.Language,
    domain.VoiceState,
    domain.ProposedActionStatus,
    domain.DecisionStatus,
    domain.DecisionOutcome,
    domain.ToolEffect,
    domain.ToolResultStatus,
    domain.HealthStatus,
    domain.SourceKind,
    domain.SourceSystem,
    domain.DeliveryDecision,
    domain.ApprovalChannel,
    domain.ApprovalChoice,
    domain.SendUpdates,
    domain.RetrievalStatus,
    domain.AgendaSectionMode,
    domain.ActiveContextMode,
    domain.EventType,
    domain.Money,
    domain.SourceRef,
    domain.Claim,
    domain.Reason,
    domain.Participant,
    domain.MeetingRef,
    domain.TimedSpan,
    domain.AllDaySpan,
    domain.Meeting,
    domain.ExecutiveBriefing,
    domain.AttentionItem,
    domain.Decision,
    domain.ProposedAction,
    domain.ApprovalRequest,
    domain.ApprovalReceipt,
    domain.FocusSession,
    domain.FocusCompletionSummary,
    domain.ToolDefinition,
    domain.ToolCall,
    domain.ToolError,
    domain.ToolResult,
    domain.CalendarCreateEventArguments,
    domain.CalendarRescheduleEventArguments,
    domain.CalendarUpdateAgendaArguments,
    domain.DecisionRecordOutcomeArguments,
    domain.FocusStartArguments,
    domain.FocusStopArguments,
    domain.ActiveContext,
    domain.AssistantRequest,
    domain.NormalizedSourceEvent,
    domain.Transcript,
    domain.VoiceStateChangedPayload,
    domain.TranscriptReadyPayload,
    domain.BriefingReadyPayload,
    domain.AttentionItemCreatedPayload,
    domain.DecisionCreatedPayload,
    domain.DecisionUpdatedPayload,
    domain.FocusStartedPayload,
    domain.FocusEndedPayload,
    domain.ActionProposedPayload,
    domain.ActionStatusChangedPayload,
    domain.InferenceUnavailablePayload,
    domain.HeartbeatPayload,
    domain.EventEnvelope,
]

API_MODELS: list[Any] = [
    api.HealthResponse,
    api.IntegrationStatus,
    api.IntegrationsResponse,
    api.TodayCalendarResponse,
    api.MeetingResponse,
    api.ProposedActionResponse,
    api.ApprovalChallengeResponse,
    api.ActionConfirmResponse,
    api.ActionResponse,
    api.TranscribeResponse,
    api.AssistantMessageResponse,
    api.BriefingRequest,
    api.BriefingResponse,
    api.AttentionListResponse,
    api.AttentionExplanationResponse,
    api.CheckNowResponse,
    api.DecisionListResponse,
    api.DecisionResponse,
    api.DecisionOutcomeProposalRequest,
    api.DeferDecisionRequest,
    api.FocusStartRequest,
    api.FocusStopRequest,
    api.FocusSessionResponse,
    api.FocusStopResponse,
    api.FocusCurrentResponse,
    api.FocusSummaryResponse,
    api.LlmSettingsResponse,
    api.LlmSettingsUpdateRequest,
    api.LlmTestConnectionResponse,
    api.DetectModelsResponse,
]

# Schema-bundle-only models (backend/provider internal; may still appear in TS
# if referenced by a frontend-facing model - see LLMModelInfo/ProviderHealth).
PROVIDER_MODELS: list[Any] = [
    providers.ChatRole,
    providers.ChatMessage,
    providers.LLMResponse,
    providers.LLMModelInfo,
    providers.ProviderHealth,
    providers.SearchResult,
    domain.AudioInput,
]

#: Canonical discriminated-union boundaries, derived from Pydantic metadata.
UNION_ALIASES: dict[str, Any] = {
    "MeetingSpan": domain.MeetingSpan,
    "CalendarProposalArguments": domain.CalendarProposalArguments,
    "EventPayload": domain.EventPayload,
}

#: Pure reference aliases (same schema truth, frontend-facing name).
REFERENCE_ALIASES: dict[str, dict[str, Any]] = {
    "CalendarProposalRequest": {"$ref": "#/$defs/CalendarProposalArguments"},
}


def _schema_of(model: Any) -> dict[str, Any]:
    return TypeAdapter(model).json_schema(ref_template="#/$defs/{model}")


class DefRegistry:
    """Root $defs registry with conflict detection (no silent overwrite)."""

    def __init__(self) -> None:
        self.defs: dict[str, Any] = {}

    def add(self, name: str, schema: dict[str, Any]) -> None:
        existing = self.defs.get(name)
        if existing is not None and existing != schema:
            raise RuntimeError(f"conflicting definitions for $defs entry {name!r}")
        self.defs[name] = schema

    def absorb(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Strip a schema's private $defs into the root registry and return
        the schema whose refs now resolve against the root."""
        schema = dict(schema)
        for name, sub in (schema.pop("$defs", None) or {}).items():
            self.add(name, sub)
        return schema


def _boundary_metadata(alias_schema: dict[str, Any]) -> tuple[str | None, list[str], dict[str, str]]:
    """(discriminator property, member def names, literal->member mapping)."""
    discriminator = alias_schema.get("discriminator") or {}
    prop = discriminator.get("propertyName")
    members = [s["$ref"].rsplit("/", 1)[-1] for s in alias_schema.get("oneOf", []) if "$ref" in s]
    mapping = {
        value: ref.rsplit("/", 1)[-1]
        for value, ref in (discriminator.get("mapping") or {}).items()
    }
    return prop, members, mapping


def _build_boundaries() -> tuple[
    dict[str, tuple[str | None, list[str], dict[str, str]]], dict[frozenset, str]
]:
    """(alias name -> metadata, member-set signature -> alias name).

    Derived mechanically from Pydantic TypeAdapter metadata - never hand-mapped.
    """
    boundaries: dict[str, tuple[str | None, list[str], dict[str, str]]] = {}
    lookup: dict[frozenset, str] = {}
    for name, alias in UNION_ALIASES.items():
        prop, members, mapping = _boundary_metadata(_schema_of(alias))
        boundaries[name] = (prop, members, mapping)
        if prop and members:
            lookup[frozenset(members)] = name
    return boundaries, lookup


def _normalize_nested_boundaries(node: Any, lookup: dict[frozenset, str]) -> Any:
    """Schema-aware rewrite: any nested discriminated-union node whose member
    set matches a canonical named boundary is replaced with a $ref to that
    strengthened definition. Raw member models and unrelated unions are left
    untouched; the boundary definitions themselves keep their own oneOf."""
    if isinstance(node, dict):
        if "oneOf" in node and "discriminator" in node:
            members = frozenset(
                s["$ref"].rsplit("/", 1)[-1]
                for s in node["oneOf"]
                if isinstance(s, dict) and "$ref" in s
            )
            alias = lookup.get(members)
            if alias is not None and len(members) == len(node["oneOf"]):
                replacement: dict[str, Any] = {"$ref": f"#/$defs/{alias}"}
                if "description" in node:
                    replacement = {"description": node["description"], **replacement}
                return replacement
        return {k: _normalize_nested_boundaries(v, lookup) for k, v in node.items()}
    if isinstance(node, list):
        return [_normalize_nested_boundaries(v, lookup) for v in node]
    return node


# ---------------------------------------------------------------------------
# Schema bundle (self-contained Draft 2020-12)
# ---------------------------------------------------------------------------


def build_schema_bundle() -> str:
    registry = DefRegistry()
    _boundaries, boundary_lookup = _build_boundaries()

    for group in (DOMAIN_MODELS, API_MODELS, PROVIDER_MODELS):
        for model in group:
            # Normalize the whole document (root + private defs) so nested
            # discriminated unions reuse the strengthened named boundary
            # definitions and every merged copy of a def is identical.
            schema = _normalize_nested_boundaries(_schema_of(model), boundary_lookup)
            registry.add(model.__name__, registry.absorb(schema))

    # Discriminated-union boundary entries: keep Pydantic's oneOf+discriminator
    # metadata and additionally require the discriminator property at the
    # boundary (the member models keep their canonical defaults).
    for name, alias in UNION_ALIASES.items():
        alias_schema = dict(_schema_of(alias))
        # Private member defs are normalized like every other schema; only the
        # boundary root keeps its canonical inline oneOf.
        for dname, sub in (alias_schema.pop("$defs", None) or {}).items():
            registry.add(dname, _normalize_nested_boundaries(sub, boundary_lookup))
        prop, _members, _mapping = _boundary_metadata(alias_schema)
        if prop:
            registry.add(name, {"allOf": [alias_schema, {"required": [prop]}]})
        else:  # defensive: not a discriminated union as expected
            registry.add(name, alias_schema)

    for name, ref in REFERENCE_ALIASES.items():
        registry.add(name, ref)

    bundle = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "EVA canonical contracts v2.0 (A00)",
        "description": (
            "Self-contained contract bundle. Every internal '#/$defs/...' ref "
            "resolves inside this document. Validate a named contract with "
            '{\"$ref\": \"#/$defs/<Name>\", ...this document}.'
        ),
        "x-eva-serialization": {
            "field_names": "snake_case",
            "enums": "lowercase",
            "extra_fields": "forbid",
            "datetimes": "ISO 8601, timezone-aware",
            "money": "integer minor units + ISO 4217 currency",
        },
        "$defs": registry.defs,
    }
    return json.dumps(bundle, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# TypeScript generation
# ---------------------------------------------------------------------------

FORMAT_ALIASES = {
    "date-time": "DateTime",
    "date": "DateOnly",
    "email": "Email",
    "uri": "Uri",
    "binary": "string",
}

#: Set at build time: member-set signature -> emitted boundary alias name.
_UNION_LOOKUP: dict[frozenset, str] = {}


def _ts_expr(schema: Any) -> str:
    if schema is True or schema == {}:
        return "unknown"
    if schema is False:
        return "never"
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(v) for v in schema["enum"])
    if "oneOf" in schema or "anyOf" in schema:
        parts_raw = schema.get("oneOf") or schema.get("anyOf")
        # Inline discriminated unions reference their emitted boundary alias so
        # the discriminator stays mandatory everywhere, not just via the alias.
        if "oneOf" in schema and "discriminator" in schema:
            members = frozenset(
                s["$ref"].rsplit("/", 1)[-1] for s in parts_raw if isinstance(s, dict) and "$ref" in s
            )
            alias = _UNION_LOOKUP.get(members)
            if alias is not None:
                return alias
        parts = [_ts_expr(s) for s in parts_raw]
        non_null = [p for p in parts if p != "null"]
        nulls = [p for p in parts if p == "null"]
        return " | ".join(non_null + nulls) if nulls else " | ".join(parts)
    if "allOf" in schema:
        if len(schema["allOf"]) == 1:
            return _ts_expr(schema["allOf"][0])
        return " & ".join(f"({_ts_expr(s)})" for s in schema["allOf"])

    typ = schema.get("type")
    if isinstance(typ, list):
        return " | ".join(_ts_expr({**schema, "type": t}) for t in typ)
    if typ == "string":
        fmt = schema.get("format")
        if fmt in FORMAT_ALIASES:
            return FORMAT_ALIASES[fmt]
        return "string"
    if typ in ("integer", "number"):
        return "number"
    if typ == "boolean":
        return "boolean"
    if typ == "null":
        return "null"
    if typ == "array":
        item = _ts_expr(schema.get("items", {}))
        return f"({item})[]" if ("|" in item or "&" in item) else f"{item}[]"
    if typ == "object":
        props = schema.get("properties")
        if props:
            required = set(schema.get("required", []))
            lines = [
                f"  {k}{'' if k in required else '?'}: {_ts_expr(v)};"
                for k, v in props.items()
            ]
            return "{\n" + "\n".join(lines) + "\n}"
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            return f"Record<string, {_ts_expr(additional)}>"
        return "Record<string, unknown>"
    return "unknown"


def _doc_comment(schema: dict[str, Any], indent: str = "") -> str:
    desc = schema.get("description")
    if not desc:
        return ""
    one_line = " ".join(str(desc).split())
    return f"{indent}/** {one_line} */\n"


def _emit_interface(name: str, schema: dict[str, Any], drop_props: tuple[str, ...] = ()) -> str:
    doc = _doc_comment(schema)
    required = set(schema.get("required", [])) - set(drop_props)
    lines: list[str] = []
    for key, sub in schema["properties"].items():
        if key in drop_props:
            continue
        sub_doc = _doc_comment(sub, indent="  ")
        optional = "" if key in required else "?"
        lines.append(f"{sub_doc}  {key}{optional}: {_ts_expr(sub)};")
    return f"{doc}export interface {name} {{\n" + "\n".join(lines) + "\n}\n"


def build_typescript() -> str:
    # Boundary metadata first: aliases and the inline-union lookup.
    boundaries, boundary_lookup = _build_boundaries()
    _UNION_LOOKUP.update(boundary_lookup)

    exported_schemas = [_schema_of(m) for m in DOMAIN_MODELS + API_MODELS]
    defs: dict[str, Any] = {}
    for schema in exported_schemas:
        for dname, sub in (schema.get("$defs") or {}).items():
            if dname in defs and defs[dname] != sub:
                raise RuntimeError(f"conflicting definitions for type {dname!r}")
            defs[dname] = sub

    roots = {m.__name__: _schema_of(m) for m in DOMAIN_MODELS + API_MODELS}
    order = [m.__name__ for m in DOMAIN_MODELS + API_MODELS]
    leftovers = sorted(n for n in defs if n not in roots and n != "EventEnvelope")

    parts: list[str] = [
        "/* eslint-disable */\n",
        "/**\n"
        " * EVA canonical contract types - version 2.0 (A00).\n"
        " *\n"
        " * GENERATED FILE - DO NOT EDIT.\n"
        " * Source of truth: backend/app/contracts/{domain,api,providers}.py\n"
        " * Regenerate: python scripts/export_contracts.py\n"
        " * Drift check: python scripts/export_contracts.py --check\n"
        " * Stream B must never edit this file (EVA_DEVELOPMENT_WORKFLOW.md section 6).\n"
        " */\n\n",
        "/** ISO 8601 date-time with timezone offset. Null means unknown/not retrieved. */\n"
        "export type DateTime = string;\n"
        "/** Calendar date YYYY-MM-DD. All-day spans use an exclusive end date. */\n"
        "export type DateOnly = string;\n"
        "export type Email = string;\n"
        "export type Uri = string;\n\n",
        "/** Makes the discriminator property mandatory at a union boundary. */\n"
        "export type RequireDiscriminator<T, K extends keyof T> = T & Required<Pick<T, K>>;\n\n",
    ]

    for name in order:
        if name == "EventEnvelope":
            # Correlated envelope is emitted after the named types; here only
            # the shared base fields (without the correlated type/payload pair).
            parts.append(
                "/** EventEnvelope fields without the correlated type/payload pair. */\n"
                + _emit_interface("EventEnvelopeBase", roots[name], drop_props=("type", "payload"))
            )
            parts.append("\n")
            continue
        schema = roots[name]
        if "enum" in schema and all(isinstance(v, str) for v in schema["enum"]):
            lines = "\n".join(f"  | {json.dumps(v)}" for v in schema["enum"])
            parts.append(f"{_doc_comment(schema)}export type {name} =\n{lines};\n")
        elif schema.get("type") == "object" and "properties" in schema:
            parts.append(_emit_interface(name, schema))
        else:
            parts.append(f"{_doc_comment(schema)}export type {name} = {_ts_expr(schema)};\n")
        parts.append("\n")

    for name in leftovers:
        schema = defs[name]
        if "enum" in schema and all(isinstance(v, str) for v in schema["enum"]):
            lines = "\n".join(f"  | {json.dumps(v)}" for v in schema["enum"])
            parts.append(f"{_doc_comment(schema)}export type {name} =\n{lines};\n")
        elif schema.get("type") == "object" and "properties" in schema:
            parts.append(_emit_interface(name, schema))
        else:
            parts.append(f"{_doc_comment(schema)}export type {name} = {_ts_expr(schema)};\n")
        parts.append("\n")

    # Discriminated-union boundaries: discriminators are mandatory here even
    # though member models carry canonical literal defaults.
    for name, (prop, members, _mapping) in boundaries.items():
        if not prop or not members:
            continue
        lines = "\n".join(
            f"  | RequireDiscriminator<{member}, {json.dumps(prop)}>" for member in members
        )
        parts.append(
            f"/** Discriminated union boundary: \"{prop}\" is mandatory. */\n"
            f"export type {name} =\n{lines};\n\n"
        )

    for name, target in REFERENCE_ALIASES.items():
        parts.append(f"export type {name} = {target['$ref'].rsplit('/', 1)[-1]};\n\n")

    # Correlated EventEnvelope: outer `type` must match the payload's own
    # discriminator, and the payload itself crosses the EventPayload boundary
    # so its discriminator is mandatory too. Derived mechanically from Pydantic
    # mapping metadata.
    event_prop, _event_members, event_mapping = boundaries["EventPayload"]
    if event_prop and event_mapping:
        variants = "\n".join(
            f"  | (EventEnvelopeBase & {{ type: {json.dumps(value)}; "
            f"payload: RequireDiscriminator<{member}, {json.dumps(event_prop)}> }})"
            for value, member in event_mapping.items()
        )
        parts.append(
            "/** WebSocket envelope with correlated `type`/payload: a mismatch is a\n"
            " * compile-time error. Pydantic enforces the same invariant at runtime. */\n"
            f"export type EventEnvelope =\n{variants};\n\n"
        )

    return "".join(parts)


# ---------------------------------------------------------------------------


def artifacts() -> dict[Path, str]:
    return {SCHEMA_PATH: build_schema_bundle(), TS_PATH: build_typescript()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify committed artifacts match the models; exit 1 on drift",
    )
    args = parser.parse_args(argv)

    drift = False
    for path, content in artifacts().items():
        if args.check:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if current != content:
                print(f"DRIFT: {path.relative_to(REPO_ROOT)} is out of date")
                drift = True
            else:
                print(f"ok: {path.relative_to(REPO_ROOT)}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
            print(f"wrote {path.relative_to(REPO_ROOT)}")
    return 1 if drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
