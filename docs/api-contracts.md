# EVA API & Contract Reference - A00 Freeze (v2.0)

**Owner:** Stream A (Core Platform). **Status:** A00 canonical freeze.
**Source of truth:** `backend/app/contracts/{domain,api,providers}.py`. This document describes the frozen surface; it is not an independent schema.

## 1. Regeneration, drift control and the three-layer contract gate

```text
python scripts/export_contracts.py            # regenerate artifacts
python scripts/export_contracts.py --check    # artifact drift gate
npm --prefix contracts/tscheck install        # once: pinned TypeScript compiler
npm --prefix contracts/tscheck run typecheck  # TS compile-time contract gate
python -m pytest backend/tests/unit/test_contracts.py -q
```

`--check` alone only proves committed output matches the generator. The A00 gate is therefore three-layered, so a broken generator cannot pass by committing its own broken output:

1. **Pydantic runtime/semantic tests** (`test_contracts.py`) - invariants JSON Schema does not encode (intervals, HIGH approval boundaries, Decision outcome consistency, envelope correlation).
2. **Real Draft 2020-12 JSON Schema validation** of the bundle (meta-validation, `$ref` resolution, named-contract fixture validation, structural negatives including boundary discriminators).
3. **TypeScript compile-time gate** (`contracts/tscheck/`, pinned `typescript@5.6.3`, `@ts-expect-error` type tests) - discriminator-required union boundaries and correlated `EventEnvelope`.

Artifacts published with this contract commit:

| Artifact | Path | Consumers |
|---|---|---|
| Self-contained Draft 2020-12 schema bundle (all `$refs` resolve at root `$defs`) | `contracts/schema/eva.schema.json` | backend, review, JSON Schema tests |
| TypeScript types (sanitized, frontend-safe) | `frontend/src/api/types.generated.ts` | Stream B only |
| Canonical synthetic fixtures | `contracts/fixtures/*.json` | Python + JSON Schema tests now; B02A typed mocks after merge |
| Isolated TS contract typecheck tooling (no frontend infra) | `contracts/tscheck/` | CI/A00 gate |

Bundle structure: `{ "$schema": draft-2020-12, "title", "x-eva-serialization", "$defs": { "<ContractName>": {...}, "MeetingSpan": {...}, "EventPayload": {...}, "CalendarProposalArguments": {...}, "CalendarProposalRequest": {"$ref": ...} } }`. Validate a named contract with `{"$ref": "#/$defs/<Name>", ...bundle}`. Conflicting definitions raise at generation; no model entry retains private `$defs`.

Rules: there is no second manual model layer. Stream B never edits the generated TS file. Any schema change after this commit requires a versioned fixture and a coordinated consumer update (workflow §6). Provider-only models (`ChatMessage`, `LLMResponse`, `AudioInput`, protocols) are in the schema bundle but excluded from TypeScript unless referenced by a frontend-facing model.

## 2. Serialization rules (frozen)

- snake_case JSON field names; lowercase enum values; **strict extra-field rejection** on every contract.
- Timezone-aware ISO 8601 datetimes (`DateTime` in TS). Naive datetimes are invalid.
- All-day events use dates with an **exclusive end** (`end_exclusive > start_date`).
- Meeting identity is `(calendar_id, event_id)`; source IDs are opaque and must resolve to actual evidence.
- Money is `amount_minor_units` (non-negative integer) + ISO-4217 uppercase `currency`. Floats/strings are rejected outright - no floating-point financial values.
- Null means unknown/not retrieved. Relationship counts stay null until actually retrieved; zero is never invented.

## 3. Enum concepts (distinct types, same-looking values are not interchangeable)

`MeetingPriority`, `ActionRisk`, `AttentionPriority` each have `low|medium|high` but remain distinct concepts. `AttentionType` = `fyi|action_required|decision_required|urgent`; a financial decision keeps `decision_required` even when `urgent=true`. `VoiceState` has the nine plan states. `HealthStatus` = `ready|degraded|unavailable`.

## 4. State machines

**ProposedAction:** `pending -> approved -> executing -> succeeded | failed | unknown`; terminal/non-executable: `rejected`, `expired`, `superseded`. Invariants enforced in the model: HIGH risk always implies `requires_approval=true` and `voice_approval_allowed=false`; `expires_at > created_at`.

**Decision:** `needs_review | deferred | resolved | dismissed`. A recorded outcome sets `status=resolved`, `outcome=accept|reject` and `outcome_recorded_at` together (enforced in both directions). Defer records no acceptance. An outcome is a local, audited decision only - it executes no payment, purchase, supplier or contract commitment and sends no message.

## 5. Domain contracts (domain.py)

All plan §4 models are implemented: `Participant`, `MeetingRef`, `Meeting` (+ discriminated `TimedSpan`/`AllDaySpan`), `SourceRef`, `Claim` (facts require sources), `Reason`, `ExecutiveBriefing`, `AttentionItem`, `Decision`, `ProposedAction`, `FocusSession` (activity derived via `is_active(now)`; never a serialized field), `FocusCompletionSummary`, `ToolDefinition`, `ToolCall` (no authoritative risk/approval fields - extra-field rejection enforces this), `ToolResult` (+ typed `ToolError`), `ApprovalRequest` (channel is server-derived, not a request field), `ApprovalReceipt` (server-issued, never model-generated), `ActiveContext`, `AssistantRequest`, `NormalizedSourceEvent` (bounded body <= 20k chars; ingestion input, not a second Attention model), `Transcript`, `AudioInput` (backend-only 16 kHz mono PCM WAV).

Discriminated Calendar proposal arguments (`tool` discriminator): `calendar.create_event`, `calendar.reschedule_event`, `calendar.update_agenda` (EVA-delimited section add/update; `send_updates` notification choice is part of the contract and therefore of the approved impact). Local outcome proposal: `decision.record_outcome` (`DecisionRecordOutcomeArguments`) - local_write only, no external financial API. Focus commands: `focus.start`, `focus.stop`. **ActiveContext invariants:** `mode=meeting` requires `meeting`; `mode=decision` requires `decision_id`; neither field may be populated for unrelated modes; `section` stays optional. No attention/focus identifiers beyond the canonical spec were invented.

**EventEnvelope (WebSocket):** `schema_version=1` (literal), unique `event_id`, monotonic per-session `sequence`, `session_id`, nullable `request_id`, aware `occurred_at`, `type` + discriminated typed `payload` (envelope type must match payload discriminator). v1 event catalog: `voice_state_changed`, `transcript_ready`, `briefing_ready`, `attention_item_created`, `decision_created`, `decision_updated`, `focus_started`, `focus_ended`, `action_proposed`, `action_status_changed`, `inference_unavailable`, `heartbeat`.

**Discriminated-union boundaries (cross-language rule):** member models keep their canonical literal defaults (`kind`/`tool`/`type`), but every union boundary makes the discriminator mandatory - in JSON Schema via an added `required` on the boundary entry, and in TypeScript via `RequireDiscriminator<Member, "K">`. **Nested usages reuse the same strengthened boundary definition**: `Meeting.span`, Calendar `span`/`new_span` are `{"$ref": "#/$defs/MeetingSpan"}` and `EventEnvelope.payload` is `{"$ref": "#/$defs/EventPayload"}` in the bundle (no weaker inline duplicates); TypeScript resolves nested unions to the same strengthened aliases, and each envelope variant types its payload as `RequireDiscriminator<Member, "type">`, so a payload without its own discriminator fails compilation even when the outer correlation is correct. The generated `EventEnvelope` is an explicit correlated union (`EventEnvelopeBase & { type: <literal>; payload: RequireDiscriminator<Member, "type"> }` per catalog entry), so a mismatched outer `type`/payload is also a TypeScript compile error and narrowing by `event.type` types `event.payload` correctly; the Pydantic validator remains as runtime defense in depth. JSON Schema enforces that `payload.type` exists at the boundary but intentionally does not encode outer/inner correlation (documented; enforced by Pydantic + TS).

## 6. Provider contracts (providers.py) - backend-only

```python
class LLMProvider(Protocol):
    async def chat(self, messages: list[ChatMessage],
                   tools: list[ToolDefinition] | None = None,
                   response_schema: dict | None = None) -> LLMResponse: ...
    async def list_models(self) -> list[LLMModelInfo]: ...
    async def health(self) -> ProviderHealth: ...

class SpeechToTextProvider(Protocol):
    async def transcribe(self, audio: AudioInput,
                         language: str | None = None) -> Transcript: ...
    async def health(self) -> ProviderHealth: ...

class WebSearchProvider(Protocol):
    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]: ...
```

`ChatMessage` (role/content/tool_calls/tool_call_id), `LLMResponse` (provider/model/text/normalized tool_calls/structured object/finish_reason), `LLMModelInfo` (actual server-exposed id + nullable capability flags), `ProviderHealth` (`ready|degraded|unavailable` + detail + optional structured `provider`/`model` carrying the actual configured identity - never secrets, keys or base-URL credentials). P0 is non-streaming; streaming requires a future contract change with its own event type. No runtime OpenRouterSearchProvider exists or may be added without a contract change.

## 7. Frozen service boundary signatures (plan §4.1)

Concrete names frozen at A00; implementations land in the owning tasks:

```text
GoogleCalendar.list_events(start, end) -> list[Meeting]              (A02)
GoogleCalendar.get_event(ref) -> Meeting                             (A02)
Gmail.search(query, limit) -> bounded message refs                   (A02)
Gmail.get_thread(id) -> normalized evidence                          (A02)
ToolRegistry.get(name) -> ToolDefinition + argument/output validators(A04)
MeetingTriage.evaluate(meeting, evidence) -> priority and Reasons    (A03)
ActionApprovalEngine.evaluate(call, context) -> policy result / durable proposal (A03)
ActionApprovalEngine.confirm(request, authenticated_context) -> action status     (A03)
ToolExecutor.execute(action_id) -> ToolResult  (revalidates itself)  (A04)
BriefingService.build(meeting_ref, language) -> ExecutiveBriefing    (B03)
AttentionEngine.ingest(source_event) -> AttentionItem                (B04, A06 rules input)
FocusService.start(duration_minutes, threshold, sender_overrides) -> FocusSession (B04)
FocusService.delivery(item, now) -> decision and delivery Reasons    (B04)
FocusService.completion_summary(focus_session_id) -> FocusCompletionSummary (B04)
DecisionService.from_attention(item) -> Decision or None             (B04)
DecisionService.propose_outcome(decision_id, outcome, context) -> ProposedAction for local decision.record_outcome (B04)
```

## 8. REST surface and envelopes (api.py)

| Endpoint | Request | Response |
|---|---|---|
| GET /api/health | - | `HealthResponse` |
| GET /api/auth/google/start, /callback | redirect flow (A02) | redirect |
| GET /api/integrations | - | `IntegrationsResponse` |
| GET /api/calendar/today | - | `TodayCalendarResponse` |
| GET /api/calendar/events/{event_id}?calendar_id= | - | `MeetingResponse` |
| POST /api/calendar/proposals | `CalendarProposalRequest` (discriminated) | `ProposedActionResponse` |
| POST /api/actions/{action_id}/confirm | `ApprovalRequest` | `ActionConfirmResponse` |
| GET /api/actions/{action_id} | - | `ActionResponse` |
| POST /api/voice/transcribe | multipart field `audio` (A05) | `TranscribeResponse` |
| POST /api/assistant/message | `AssistantRequest` | `AssistantMessageResponse` |
| POST /api/briefing/meeting | `BriefingRequest` | `BriefingResponse` |
| GET /api/attention | - | `AttentionListResponse` |
| GET /api/attention/{id}/explanation | - | `AttentionExplanationResponse` |
| POST /api/attention/check-now | - (same real ingestion as polling) | `CheckNowResponse` |
| GET /api/decisions, /{id} | - | `DecisionListResponse`, `DecisionResponse` |
| POST /api/decisions/{id}/outcome-proposals | `DecisionOutcomeProposalRequest` | `ProposedActionResponse` |
| POST /api/decisions/{id}/defer | `DeferDecisionRequest` | `DecisionResponse` |
| POST /api/focus/start, /stop | `FocusStartRequest`, `FocusStopRequest` | `FocusSessionResponse`, `FocusStopResponse` |
| GET /api/focus/current | - | `FocusCurrentResponse` |
| GET /api/focus/{id}/summary | - | `FocusSummaryResponse` |
| GET/PUT /api/settings/llm | -, `LlmSettingsUpdateRequest` | `LlmSettingsResponse` (sanitized) |
| POST /api/settings/llm/test, /detect | - | `LlmTestConnectionResponse`, `DetectModelsResponse` |
| WS /api/events | - | `EventEnvelope` |

No generic public execute endpoint exists. **`FocusStopResponse.summary` is required:** an explicit stop always returns the completion summary computed from persisted session items (the same summary remains available via `GET /api/focus/{id}/summary` after reconnect/restart). Settings responses serialize secret presence flags only (`api_key_present`), never values; API keys in update requests are write-only. B owns the Settings routes/implementation against these envelopes.

## 9. Configuration contract (config.py, .env.example)

Mandatory self-hosted settings: `EVA_LLM_BASE_URL`, `EVA_LLM_MODEL` (+ optional key) plus an **explicit non-empty `EVA_LLM_ALLOWED_ORIGINS` allowlist that contains the endpoint's origin**. `Settings.self_hosted_configured` is true only when all hold (`self_hosted_route_blockers()` lists why not); `is_allowed_self_hosted_origin()` is the frozen per-request check for the mandatory route. Cloud endpoints must never be allowlisted; the allowlist is user/backend configuration, never model output. The optional fallback obeys the same private-origin requirement. Optional cloud extension: `EVA_ALLOW_CLOUD_INFERENCE=false`, `EVA_ALLOW_WORKSPACE_CLOUD_INFERENCE=false` defaults; no cloud key/model/endpoint/adapter is required at startup and the model cannot enable either flag.

**A00 startup decision (documented deviation risk):** a not-yet-usable self-hosted route does not abort process start during A00, because "health starts with external services unavailable" must hold before adapters exist. `/api/health` reports `llm=unavailable` with the concrete blockers, and the B01 provider router must refuse every mandatory-route request unless `self_hosted_configured` is true (enforcement at provider-call time). Escalation to hard startup failure is an A-owned wiring change once B01 lands.

## 10. Health semantics

`GET /api/health` always returns HTTP 200 with an honest body: per-component `ProviderHealth` (`database`, `llm`, `llm_fallback`, `google`, `stt`) including structured `provider`/`model` where configured (never secrets). **Overall status aggregates required components only; `llm_fallback` is optional** - its absence is reported truthfully as `unavailable` but never fails mandatory runtime readiness (`OPTIONAL_COMPONENTS` in `api/health.py`). It never contacts external services in A00; adapters add bounded probes later without changing the envelope.

## 11. Fixture index (all synthetic - example.com addresses, demo-owned IDs)

Meetings: `meeting_acme_high` (HIGH contract review), `meeting_team_sync_medium`. Briefing: `briefing_acme_pl` (partial retrieval). Attention/Decision: `attention_finance_decision` (decision_required + urgent, deferred delivery), `decision_finance_pln_needs_review` (12 400 PLN = 1 240 000 minor units), `decision_finance_pln_resolved`. Actions/approval: `proposed_action_agenda_high` (HIGH agenda, voice forbidden, 5-minute expiry), `approval_request_ui`, `approval_receipt_ui`. Focus: `focus_session_active` (CFO exact-address override), `focus_completion_summary`, `focus_stop_response` (session + required summary). Tools: `tool_definition_calendar_update_agenda`, `tool_call_agenda`, `tool_result_agenda_ok`. Proposals: `calendar_proposal_{create,reschedule,update_agenda}`, `decision_outcome_proposal_request`. Misc: `assistant_request_meeting`, `normalized_source_event_gmail`, `transcript_pl`, `event_envelope_attention_created`, `health_response` (honest default-boot state incl. structured llm provider/model), `llm_settings_response` (cloud flags false).

## 12. Handoff to Stream B (contract review)

The generated contracts are safe to consume only with all three gate layers green (§1): Pydantic tests, real JSON Schema validation, and the TypeScript compile-time gate - plus a clean `export_contracts.py --check`. With those holding, `frontend/src/api/types.generated.ts` is the generated frontend contract source of truth: discriminated unions require their discriminators, `EventEnvelope` is type/payload correlated and narrows correctly.

1. Review this commit; corrections before service integration are expected and accepted via a follow-up contract commit from A.
2. After human merge into `integration`, pull and run `python scripts/export_contracts.py --check`, `npm --prefix contracts/tscheck install && npm --prefix contracts/tscheck run typecheck` and `npm --prefix frontend run typecheck` in B02A.
3. Build typed mocks directly from `types.generated.ts` + fixtures; never edit the generated file or invent parallel domain models. Stream B must not reimplement backend policy or authorization logic.
4. Service signatures in §7 and provider protocols in §6 are frozen - code against them; changes need a contract-change request (workflow §6.4).
