#!/usr/bin/env python3
"""Export EVA canonical contracts to JSON Schema and TypeScript (A00).

Single source of truth: backend/app/contracts/{domain,api,providers}.py.
There is no second manual model layer; frontend code must only use the
generated file and never edit it (docs/EVA_DEVELOPMENT_WORKFLOW.md section 6).

Artifacts written by this script:
- contracts/schema/eva.schema.json      full schema bundle (backend + review)
- frontend/src/api/types.generated.ts   sanitized TS types for Stream B

Usage:
    python scripts/export_contracts.py            # regenerate artifacts
    python scripts/export_contracts.py --check    # fail on drift (CI/gates)

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

from pydantic import BaseModel, TypeAdapter  # noqa: E402

from app.contracts import api, domain, providers  # noqa: E402

SCHEMA_PATH = REPO_ROOT / "contracts" / "schema" / "eva.schema.json"
TS_PATH = REPO_ROOT / "frontend" / "src" / "api" / "types.generated.ts"

# Frontend-facing contracts, in documentation order. These define the TS export.
DOMAIN_MODELS: list[type[BaseModel]] = [
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

API_MODELS: list[type[BaseModel]] = [
    api.HealthResponse,
    api.IntegrationStatus,
    api.IntegrationsResponse,
    api.TodayCalendarResponse,
    api.MeetingResponse,
    api.ProposedActionResponse,
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
PROVIDER_MODELS: list[type[BaseModel]] = [
    providers.ChatRole,
    providers.ChatMessage,
    providers.LLMResponse,
    providers.LLMModelInfo,
    providers.ProviderHealth,
    providers.SearchResult,
    domain.AudioInput,
]

# Named union aliases emitted for discriminated unions used at API boundaries.
TS_ALIASES: list[tuple[str, dict[str, Any]]] = []


def _schema_of(model: Any) -> dict[str, Any]:
    return TypeAdapter(model).json_schema(ref_template="#/$defs/{model}")


# ---------------------------------------------------------------------------
# Schema bundle
# ---------------------------------------------------------------------------


def build_schema_bundle() -> str:
    models: dict[str, Any] = {}
    for group in (DOMAIN_MODELS, API_MODELS, PROVIDER_MODELS):
        for model in group:
            models[model.__name__] = _schema_of(model)
    bundle = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "EVA canonical contracts v2.0 (A00)",
        "generated_by": "scripts/export_contracts.py",
        "serialization": {
            "field_names": "snake_case",
            "enums": "lowercase",
            "extra_fields": "forbid",
            "datetimes": "ISO 8601, timezone-aware",
            "money": "integer minor units + ISO 4217 currency",
        },
        "models": models,
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


def _collect_defs(models: list[dict[str, Any]], defs: dict[str, Any]) -> None:
    for schema in models:
        for name, sub in (schema.get("$defs") or {}).items():
            if name in defs and defs[name] != sub:
                raise RuntimeError(f"conflicting definitions for type {name!r}")
            defs[name] = sub


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
    if "oneOf" in schema:
        return " | ".join(_ts_expr(s) for s in schema["oneOf"])
    if "anyOf" in schema:
        parts = [_ts_expr(s) for s in schema["anyOf"]]
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


def _emit_named(name: str, schema: dict[str, Any]) -> str:
    doc = _doc_comment(schema)
    if "enum" in schema and all(isinstance(v, str) for v in schema["enum"]):
        lines = "\n".join(f"  | {json.dumps(v)}" for v in schema["enum"])
        return f"{doc}export type {name} =\n{lines};\n"
    if schema.get("type") == "object" and "properties" in schema:
        required = set(schema.get("required", []))
        lines: list[str] = []
        for key, sub in schema["properties"].items():
            sub_doc = _doc_comment(sub, indent="  ")
            optional = "" if key in required else "?"
            lines.append(f"{sub_doc}  {key}{optional}: {_ts_expr(sub)};")
        extra = (
            "\n  [key: string]: unknown;"
            if schema.get("additionalProperties") is True
            else ""
        )
        return f"{doc}export interface {name} {{\n" + "\n".join(lines) + extra + "\n}\n"
    return f"{doc}export type {name} = {_ts_expr(schema)};\n"


def build_typescript() -> str:
    exported_schemas = [_schema_of(m) for m in DOMAIN_MODELS + API_MODELS]
    defs: dict[str, Any] = {}
    _collect_defs(exported_schemas, defs)

    roots = {m.__name__: _schema_of(m) for m in DOMAIN_MODELS + API_MODELS}
    order = [m.__name__ for m in DOMAIN_MODELS + API_MODELS]
    leftovers = sorted(n for n in defs if n not in roots)

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
    ]

    for name in order:
        parts.append(_emit_named(name, roots[name]))
        parts.append("\n")
    for name in leftovers:
        parts.append(_emit_named(name, defs[name]))
        parts.append("\n")

    # Named aliases for the discriminated unions used at API boundaries.
    union_defs = {
        "MeetingSpan": ["TimedSpan", "AllDaySpan"],
        "CalendarProposalRequest": [
            "CalendarCreateEventArguments",
            "CalendarRescheduleEventArguments",
            "CalendarUpdateAgendaArguments",
        ],
        "EventPayload": [
            "VoiceStateChangedPayload",
            "TranscriptReadyPayload",
            "BriefingReadyPayload",
            "AttentionItemCreatedPayload",
            "DecisionCreatedPayload",
            "DecisionUpdatedPayload",
            "FocusStartedPayload",
            "FocusEndedPayload",
            "ActionProposedPayload",
            "ActionStatusChangedPayload",
            "InferenceUnavailablePayload",
            "HeartbeatPayload",
        ],
    }
    for alias, members in union_defs.items():
        expr = " | ".join(members)
        parts.append(
            f"/** Discriminated on the \"{'tool' if 'Proposal' in alias else 'kind' if alias == 'MeetingSpan' else 'type'}\" field. */\n"
            f"export type {alias} = {expr};\n\n"
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
