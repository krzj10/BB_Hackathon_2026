# EVA - Executive Virtual Assistant
## Technical Implementation Plan - Version 2.0

**Version:** 2.0
**Updated:** 2026-09-19
**Status:** final architecture correction applied to version 2.0; implementation not started
**Supersedes:** version 1.0, preserved in EVA_IMPLEMENTATION_PLAN_v1.0.md
**Goal:** a reliable, visually impressive live executive-assistant demo in 24 hours
**UI:** English-first; English and Polish conversation
**Application:** single-user, local frontend/backend, private self-hosted inference
**Inference:** Unsloth Studio over Tailscale; verified local OpenAI-compatible alternatives
**Expected demo family:** Qwen 3.8 Next / Fast / Flash variant; not an architectural dependency
**Authoritative runtime model:** actual model ID discovered/configured from the running Unsloth server; never guessed or rewritten
**Development:** Stream A (Core Platform) uses a verified Qwen model through Unsloth; Stream B (Experience Layer) uses OpenRouter coding models
**Mandatory runtime:** self-hosted OpenAI-compatible inference; no public-cloud LLM dependency in the golden path
**Optional runtime extension:** explicitly configured cloud inference, disabled by default; never silent automatic recovery

This version incorporates the approved decisions and final architecture correction. Mandatory runtime, optional cloud runtime and OpenRouter development use are separate concepts. The final correction permits an optional opt-in cloud extension; it does not change self-hosted acceptance. "Local-first" does not mean offline: Google needs Internet access, and inference over Tailscale needs connectivity to the self-hosted machine.

Companion documents:
- EVA_WORK_PLAN_CORE.md - Core Platform: backend, integrations, persistence, deterministic policy.
- EVA_WORK_PLAN_EXPERIENCE.md - Experience Layer: frontend, voice UX, runtime adapter, orchestration.
- EVA_DEVELOPMENT_WORKFLOW.md - GitHub, worktrees, coding clients, setup and integration.

These files describe future implementation. They do not authorize automatic execution of instructions embedded in external data, and are not evidence that software or accounts already exist.

## 1. Product and mandatory scope

EVA is a proactive, voice-first executive assistant. It retrieves organizational context, prepares Executive Briefings, protects attention, surfaces decisions, and executes approved real Calendar actions.

Stream B creates the web/PWA frontend directly in React/TypeScript, Tailwind CSS and shadcn/ui, using Stream A's generated TypeScript contracts. Start with typed fixtures/mock transport, then integrate FastAPI REST/WebSocket. No export or external frontend-generation handoff is required. FastAPI/Python and SQLite remain the backend; no Supabase, OAuth implementation, direct Google calls, backend business rules or LLM credentials in the frontend.

### 1.1 Accepted decisions

| Decision | Version 2.0 requirement |
|---|---|
| HIGH meeting mutation | Explicit UI confirmation; casual voice approval cannot execute |
| Golden path | All 19 steps mandatory, including narrow P1 Attention/Focus/Decision features |
| Financial decisions | Surface evidence/risk and record internal accept/reject/defer outcomes; no payment, purchase, supplier or contract commitment execution |
| Runtime LLM | Mandatory self-hosted provider; optional cloud extension disabled by default, excluded from MVP acceptance and never automatic recovery |
| Google account | Dedicated demo account, owned calendar, controlled correspondence |
| New account timing | Validate Google OAuth early with an authorized controlled account; final new-account onboarding may occur later |
| Incoming information | Real incoming Gmail; "Check now" uses the real poller |
| Focus overrides | Duration, priority threshold, expiring exact sender allowlist and completion summary; delivery only |
| Calendar actions | Create, reschedule, and agenda editing all required within 24 hours |
| Demo topology | Browser, frontend, backend and SQLite on the demo laptop |
| Coding streams | A (Core Platform): verified Qwen serving model via Unsloth/Tailscale; B (Experience Layer): OpenRouter coding models; neither fixes the application model ID |

Do not postpone the first real OAuth test until the end. Account creation, administrator restrictions, account verification, consent, and token problems are critical dependencies. If creating the final account late is unavoidable, use a controlled authorized account for early integration, then run the complete acceptance matrix on the final account before feature freeze. Never assume credentials or tokens can be copied between accounts.

### 1.2 Priorities

P0:
- Backend foundation, contracts, persistence, private inference adapter and health.
- PTT, faster-whisper, whisper.cpp fallback, browser SpeechSynthesis, interruption.
- Google OAuth; Calendar read/create/reschedule/agenda edit; Gmail search/read.
- Tool Registry, Meeting Triage, ActionApprovalEngine, guarded ToolExecutor.
- Source-backed Executive Briefing and EN/PL interaction.

P1 required for golden path:
- Gmail ingestion and Attention classification.
- Protect My Focus with expiring sender overrides and an on-screen completion summary.
- Structured "Why am I seeing this?" explanations.
- Decision Inbox fed only by Attention.
- At least one real proactive incoming-message event.

P1 optional:
- Google Chat, wake word, Morning Briefing, Drive Mode.
- Standalone Previous Interaction and Participant Intelligence beyond briefing needs.
- Broader topic/company Focus rules are deferred.

P2:
- Drive RAG, external search/person enrichment, Sheets watchlist.
- Optional web search is a separate explicit feature, not public-cloud LLM inference; disabled for the mandatory demo.
- Optional cloud runtime support is a time-permitting extension only after the self-hosted golden path and required hardening are stable. Its absence is not a blocker.

P3/roadmap:
- Context Graph, Obsidian, advanced analytics, additional connectors.
- Full Executive Attention Layer, preference learning, enterprise/multi-user support.

## 2. Runtime architecture and topology

~~~text
DEMO LAPTOP
  Browser: React/TypeScript PWA, PTT, SpeechSynthesis
       -> local FastAPI API + event stream
            -> SQLite / policy / executor
            -> faster-whisper or whisper.cpp
            -> Google OAuth -> Calendar + Gmail (Internet)
            -> OpenAI-compatible inference client
                 -> private Tailscale connection
                      -> self-hosted Unsloth Studio -> loaded local model
~~~

Both coding agents may run on the same developer laptop in separate worktrees. Their MODEL endpoints may be elsewhere. No application backend is deployed to OpenRouter. Mandatory inference supports self-hosted OpenAI-compatible endpoints such as Unsloth Studio, vLLM, LM Studio, Ollama in compatible mode, llama.cpp server, or another explicitly configured compatible server. The user's Unsloth instance is primary. Compatibility and actual serving ID are verified against the running endpoint, not inferred from the provider name.

- Backend: loopback port 8000.
- Frontend: loopback dev server; proxy /api and WebSocket to backend.
- OAuth callback: http://localhost:8000/api/auth/google/callback.
- Unsloth port/base URL: read from the actual server; do not assume port 8000.
- Prefer a tailnet-only HTTPS endpoint or private tailnet-bound service; configure host firewall and Tailscale grants for the specific device/port.
- No public tunnel/Funnel is required. The browser never connects directly to Unsloth.
- Keep runtime API credentials in backend secret storage, outside Git.
- Disable Unsloth built-in code execution, terminal and autonomous server-side tools for EVA inference. EVA tools execute only in EVA.
- If the coding client also uses this server, use a separate inference instance/configuration when tool settings cannot be isolated. Client-side coding tools run in the coding client workspace.
- Reserve model capacity for runtime tests. Pause Stream A generation during latency measurements and rehearsals if the same GPU serves both workloads.
- Do not claim offline capability when the inference machine is remote.

### 2.1 Runtime failure behavior

1. Try the configured self-hosted primary.
2. On bounded timeout/failure, use an explicitly configured, verified self-hosted fallback if available.
3. A fallback model on the same unreachable host does not protect against a Tailscale/host outage.
4. Prefer a warmed small model on the demo laptop for network-independent fallback if hardware permits; do not promise it before testing.
5. If no self-hosted provider is available, show inference unavailable; retain direct Calendar UI and already retrieved evidence. Do not fake a generated briefing.
6. Never switch to public-cloud inference automatically. If optional cloud support exists, a user explicitly selects a cloud request after enabling it; a private-provider failure does not grant permission or trigger a cloud retry.
7. Every golden-path and MVP acceptance run succeeds with cloud disabled and no cloud credentials.

### 2.1.1 Settings / AI Engine and optional cloud extension

Mandatory SELF-HOSTED PROVIDER controls:
- Base URL; API key if required; actual Model ID.
- Detect Models; manual model configuration when discovery is unsupported.
- Test Connection; Health; latency when available.
- Optional configured self-hosted fallback, with the same safety boundary.

Optional CLOUD PROVIDER controls, if implemented:
- Disabled / OpenRouter / Custom OpenAI-compatible (including a configured OpenAI endpoint).
- Optional API key, model, Base URL where appropriate, and Test Connection.
- Allow cloud inference: false by default.
- Allow Workspace context to be sent to cloud inference: false by default.

No cloud key, model, endpoint or adapter is required for application startup. If not implemented, retain this Settings design and the existing provider interface; show unavailable controls only as clearly disabled, never as functioning features.

The optional extension is a separate task after mandatory acceptance, not part of B01 or a fallback chain. Reuse the provider interface; separate self-hosted and cloud endpoint configuration. Explicit user opt-in is stored server-side and can be revoked. A cloud request requires cloud inference enabled plus an explicit cloud route; requests containing Workspace-derived context additionally require Workspace cloud inference enabled. This includes raw messages, retrieved snippets, briefings, summaries, tool results and conversation history derived from Workspace. Check again before each provider call or tool-result continuation. If provenance is uncertain, deny the cloud request rather than leak context. The model cannot enable either setting.

Test Connection for a cloud endpoint requires cloud opt-in and uses a synthetic prompt, never Workspace context. Revocation blocks subsequent outbound requests; UI makes the selected provider visible. Development-client OpenRouter credentials do not populate application settings. Backend-origin validation, secret redaction, policy and approval rules apply to optional cloud requests too.

### 2.2 Environment contract

Application variables:
~~~dotenv
EVA_ENV=development
EVA_TIMEZONE=Europe/Warsaw
EVA_DB_URL=sqlite:///./eva.db
EVA_LLM_PROVIDER=openai_compatible
EVA_LLM_BASE_URL=
EVA_LLM_MODEL=
EVA_LLM_API_KEY=
EVA_LLM_FALLBACK_BASE_URL=
EVA_LLM_FALLBACK_MODEL=
EVA_LLM_FALLBACK_API_KEY=
EVA_LLM_ALLOWED_ORIGINS=
EVA_ALLOW_CLOUD_INFERENCE=false
EVA_ALLOW_WORKSPACE_CLOUD_INFERENCE=false
EVA_STT_PROVIDER=faster-whisper
EVA_WHISPER_MODEL=base
EVA_WHISPER_DEVICE=cpu
EVA_WHISPER_COMPUTE_TYPE=int8
EVA_WAKE_WORD_ENABLED=false
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8000/api/auth/google/callback
~~~

Blank endpoint/model values are intentional machine-setup inputs, not invented defaults. Validate required self-hosted settings at startup. Use an explicit allowlist of configured self-hosted origins; refuse unapproved redirects and cloud endpoints in that mandatory route. An optional cloud route has separate endpoint configuration and the opt-in checks above; it is never consulted for startup or automatic recovery. Both cloud permission defaults remain false even if the optional adapter is absent. Origin configuration is controlled by the user/backend, not model output. Development credentials belong only in the coding client's private configuration.

## 3. Canonical ownership and repository layout

~~~text
backend/app/
  contracts/      A owns canonical Pydantic domain/API/provider contracts
  db/             A owns persistence and migrations
  google/         A owns OAuth, Calendar, Gmail
  approvals/      A owns risk, approval, receipts
  meetings/       A owns deterministic triage; B owns briefing.py
  attention/      A owns rules.py, ingest.py; B owns engine.py, focus.py, explanations.py
  decisions/      B owns service.py
  agent/          A owns tool_registry.py and tool_executor.py
                  B owns executive_agent.py, context_builder.py, prompts.py
  llm/            B owns self-hosted provider adapter/router
  voice/          A owns audio normalization and STT providers
  api/            A owns health/auth/calendar/voice/actions
                  B owns assistant/briefing/attention/focus/decisions/settings
  scheduler/      B owns scheduler wiring
  policy/         A owns POLICY.yaml; HUMAN supplies USER.md and ORG.md
  main.py         A owns composition root, including registration of B modules
frontend/         B creates and owns directly: React/TypeScript + Tailwind CSS + shadcn/ui
scripts/
  export_contracts.py       A
  probe_runtime.py         B
  demo_reset.py            A
tests/            Each stream owns tests for its modules; B owns E2E
docs/             Tech lead owns architecture/contracts/change decisions
~~~

A module has one current owner. No duplicate models, registry, executor, provider interface, scheduler or approval implementation. Backend lockfile belongs to A; frontend lockfile belongs to B. B requests backend dependency additions from A. A generates frontend types; B never edits the generated file. Root composition is changed by A using B's explicit router/service registration instructions.

## 4. Canonical contracts and contract gate

A00 must convert this section into Pydantic models and generated TypeScript before application modules diverge. Prior review model sketches are design input, not an independent schema to maintain.

Serialization:
- snake_case JSON, lowercase enums, strict extra-field rejection.
- Time-aware ISO datetimes; dates for all-day events with exclusive end.
- Opaque source IDs and (calendar_id, event_id) meeting identity.
- Integer minor units plus currency for money; no floating-point financial thresholds.
- Null means unknown/not retrieved; never invent zero relationship counts.
- Claim sources must resolve to actual evidence.
- Tool-specific argument/output models are registered once.
- Provider interfaces are backend-only; frontend receives sanitized settings/health.

| Contract | Required fields / invariants |
|---|---|
| Participant | email, nullable name/company/role/internal; nullable nonnegative previous meeting/email/chat counts |
| MeetingRef | calendar_id, event_id |
| Meeting | ref, etag, title, timed/all-day span, attendees, organizer_email, editable, recurring_event_id, description, location, agenda, priority, priority_reasons, sources |
| Meeting span | timed: start/end with offsets and timezone; all_day: start date/end_exclusive date; end must follow start |
| SourceRef | id, kind, resource_id, title, nullable url, retrieved_at |
| Claim | text, kind=fact/inference/suggestion, source_ids; historical facts require sources |
| Reason | code, origin=rule/preference/llm, text, source_ids, nullable policy_version |
| ExecutiveBriefing | id, meeting, language=en/pl, generated_at, previous_interactions/open_topics/previous_decisions/risks/suggestions as Claims, spoken_summary, sources, retrieval_status, retrieval_notes |
| AttentionItem | id, source/source_id, sender_email, title, content_preview, received_at, attention_type, urgent boolean, priority, confidence, reasons, sources, deadline, related_meeting, decision_id, delivery, delivery_reasons |
| Decision | id, attention_item_id, title, money, deadline, context/alternatives/risks/preference_conflicts, suggested_next_step, risk, status, nullable outcome=accept/reject, nullable outcome_recorded_at, nullable proposed_action_id, sources |
| ProposedAction | id/session_id/request_id, revision, tool, arguments, arguments_digest, summary/reason/impact, before/after, resource_version, policy_version, risk, requires_approval, voice_approval_allowed, created_at/expires_at, status |
| FocusSession | id, starts_at/ends_at, threshold, sender overrides, policy_version, stopped_at; active derived from timestamps |
| FocusCompletionSummary | focus_session_id, ended_at, total_received, deferred_count, decision_count, action_count, fyi_count, attention_item_ids; counts from stored Attention items |
| VoiceState | idle/wake_listening/listening/transcribing/thinking/speaking/awaiting_approval/interrupted/error |
| ToolDefinition | name, description, effect=read/local_write/external_write, input/output JSON schema, required_scopes, risk_floor, timeout_seconds |
| ToolCall | id, name, arguments; no authoritative risk or approval fields |
| ToolResult | call_id, tool, status=ok/partial/error/unknown, data, typed error, sources, action_id, duration_ms |
| ApprovalRequest | action_id, revision, arguments_digest, approve/reject, one-time challenge; channel is derived server-side |
| ApprovalReceipt | id, action_id, revision, arguments_digest, channel, approved_at, policy_version; never model-generated |
| ActiveContext | mode, nullable meeting reference/decision_id/section |
| AssistantRequest | request_id, session_id, text, language, active_context |
| EventEnvelope | schema_version=1, event_id, monotonic sequence, session_id, request_id, type, occurred_at, typed payload |
| NormalizedSourceEvent | source/source_id, sender_email, subject, bounded body, received_at, sources; ingestion input, not a second Attention model |
| Transcript | text, language, nullable language_confidence, duration_ms, provider |
| AudioInput | normalized 16 kHz mono PCM WAV bytes; backend-only |

MeetingPriority, ActionRisk and AttentionPriority each have low/medium/high values but are distinct concepts. Attention type is fyi/action_required/decision_required/urgent. A financial decision retains decision_required even when urgent=true.

ProposedAction lifecycle:
pending -> approved -> executing -> succeeded / failed / unknown.
Other terminal/non-executable states: rejected, expired, superseded.
A HIGH action always requires approval and never allows voice approval.
Decision lifecycle remains needs_review/deferred/resolved/dismissed. A recorded accept/reject sets status=resolved, outcome=accept/reject and outcome_recorded_at; unresolved items have no outcome. Defer sets status=deferred without recording acceptance. These fields are an additive canonical-contract change owned by A00, including generated TS. A local outcome and its audit receipt are not a payment, purchase or external commitment.

Canonical provider signatures:
~~~python
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
    async def search(self, query: str,
                     max_results: int = 5) -> list[SearchResult]: ...
~~~

ChatMessage supports role, content, tool_calls and tool_call_id. LLMResponse contains provider/model, text, normalized tool_calls, structured object and finish_reason. LLMModelInfo carries actual id, display_name and nullable capability flags. ProviderHealth is ready/degraded/unavailable plus detail. SearchResult contains title/url/snippet/retrieved_at. No runtime OpenRouterSearchProvider.

P0 uses non-streaming structured Chat Completions. A future streaming method must have a separate explicit event type. Tool names map deterministically between internal dotted names and portable wire names.

A00 publishes JSON schema, generated TS, fixtures and docs/api-contracts.md at one shared commit. B reviews that commit before integration. Schema changes thereafter require a versioned fixture and coordinated consumer update.

### 4.1 Service boundaries

- GoogleCalendar.list_events(start, end) -> list[Meeting]
- GoogleCalendar.get_event(ref) -> Meeting
- ToolRegistry.get(name) -> ToolDefinition plus argument/output validators
- MeetingTriage.evaluate(meeting, evidence) -> priority and Reasons
- ActionApprovalEngine.evaluate(call, context) -> policy result / durable proposal
- ActionApprovalEngine.confirm(request, authenticated_context) -> action status
- ToolExecutor.execute(action_id) -> ToolResult; revalidates authorization itself
- Gmail.search(query, limit) -> bounded message refs; get_thread(id) -> normalized evidence
- BriefingService.build(meeting_ref, language) -> ExecutiveBriefing
- AttentionEngine.ingest(source_event) -> AttentionItem
- FocusService.start(duration_minutes, threshold, sender_overrides) -> FocusSession
- FocusService.delivery(item, now) -> decision and delivery Reasons
- DecisionService.from_attention(item) -> Decision or None
- DecisionService.propose_outcome(decision_id, outcome, context) -> ProposedAction for local decision.record_outcome; no external financial tool
- FocusService.completion_summary(focus_session_id) -> FocusCompletionSummary

Concrete names/signatures are frozen in A00; no stream silently invents alternatives.

## 5. Execution and approval architecture

~~~text
voice/text request -> transcription -> validated active context
-> bounded evidence -> LLM reasoning -> structured ToolCall
-> registry argument validation -> deterministic policy evaluation
-> mutation proposal -> approval when required
-> execution-time revalidation -> guarded adapter call
-> persisted ToolResult -> UI update -> truthful spoken response
~~~

Every tool request passes policy, including reads. Reads can execute immediately when authorized. Only ToolExecutor can invoke mutating adapters. API routes, agent, scheduler and Decision Inbox use the same executor.

The LLM may interpret, summarize, plan and classify ambiguity. It cannot choose authoritative risk, issue receipts, rewrite POLICY, execute through runtime built-in tools, or claim success without a result.

Meeting triage:
- Informal optional internal catch-up: LOW.
- Team/project/customer meeting: at least MEDIUM.
- Board/investor/contract negotiation/strategic or explicit high: HIGH.
- Financial decision intent plus amount >= 1000 PLN: HIGH.
- Amount alone is insufficient. Quoted old amounts are not a new request.
- POLICY.yaml owns thresholds. USER.md preferences and ORG.md descriptions do not override it.

Action risk:
- Reads/summaries: LOW.
- Ordinary create, LOW/MEDIUM reschedule, agenda edits: at least MEDIUM.
- ANY mutation of a HIGH-priority meeting: HIGH, including agenda edits.
- New meeting creation is triaged against its proposed content before approval.
- External/sensitive notification effects may escalate risk.
- Financial commitments, deletes and legal commitments are not enabled demo tools.

Approval:
- MEDIUM may use fresh explicit yes/no bound to the one pending proposal.
- HIGH requires explicit authenticated UI confirmation.
- Proposal expires after five minutes; bind approval to revision, digest, policy version, resource ETag and impact/notification choice.
- Execution re-fetches relevant state and rechecks policy. Changed scope, content, policy, attendee impact or resource version invalidates old approval.
- Atomic approved->executing claim prevents double-click races.
- Lost response after a write becomes unknown. Reconcile before retry.
- On restart, reconcile executing/unknown actions; do not replay blindly.
- Reject or expire proposals without calling mutating adapters.
- A user interruption stops speech; it cannot undo an already issued external write.

Single-user still needs session-bound routes, exact allowed origins, CSRF protection for cookie-authenticated mutations and WebSocket origin/session checks. Backend binds to loopback. No OAuth/API secrets in browser responses or logs.

### 5.1 Pragmatic implementation order

FIRST deliver a correct and safe golden path. Before the first real write, keep deterministic policy, HIGH UI confirmation, bound receipts/digests, expiry, ETags, atomic execution claiming, persisted create IDs and honest unknown outcomes. Never defer those boundaries.

THEN, after G3, deepen automated recovery: restart reconciliation workers, extended crash-window testing, durable outbox replay, reconnect/gap repair and comprehensive fault injection in A07/B05. A00 freezes the contracts; A01 supplies minimal durable records/constraints. A04 marks uncertain writes unknown, blocks replay and supports deliberate read-back; it need not build a general recovery subsystem. B03/B04 provide basic authenticated live events and snapshots for the working flow; B05 hardens them. This sequencing preserves the final reliability architecture without making rare failure recovery a first-hour dependency.

## 6. Google integration and Calendar semantics

P0 scopes:
- https://www.googleapis.com/auth/calendar.events.owned
- https://www.googleapis.com/auth/gmail.readonly

Owned demo calendar, initially primary, avoids calendar-list scopes. Enable Calendar and Gmail APIs, Web application OAuth client, exact localhost callback, offline access, one-time state validation, granted-scope checks and serialized refresh. Store secrets outside source control; use OS-protected credential storage where available, isolated permission-restricted storage for the demo.

External/Testing: admit demo account as test user. Testing authorization with these scopes expires after seven days, including refresh token. Reauthorize before rehearsal as needed. Workspace admin restrictions and new-account verification may block setup; discover early. Restricted Gmail scope does not imply the hackathon must wait for public launch verification, but the development exception is not a production compliance claim.

### 6.1 Calendar tools - all mandatory

- calendar.list_events, calendar.get_event.
- calendar.create_event: explicit title/time/zone/attendees; validate time and meeting priority.
- calendar.reschedule_event: new start/end; show old/new times and affected attendees.
- calendar.update_agenda: add/update an EVA-delimited section within description, preserving other text.
- Conflict detection for owned calendar events; do not imply complete attendee free/busy.
- Demo uses non-recurring events. Recurring events are readable; ambiguous series mutations are blocked with explanation.
- Retain all-day date boundaries; do not silently turn an all-day event into a timed event.

Create uses a persisted valid Google event ID generated for that action to make retries reconcilable. On timeout, GET that ID before any retry. Reschedule/agenda use If-Match ETag; 412 triggers fresh proposal. Preserve unrelated event fields. Notification choice (sendUpdates) is part of the approved impact; never describe external notifications as an invisible local edit.

Acceptance includes an ordinary meeting creation, its reschedule, the HIGH ACME agenda edit, and HIGH reschedule voice rejection. All three mutation types must work even though the main 19-step scene demonstrates agenda editing.

### 6.2 Gmail and incoming demo data

Search bounded recent correspondence by exact participant addresses; retrieve message/thread content, decode MIME, strip unsafe HTML, return citations and retrieval limits. No send, compose, modify or insert runtime scope.

Incoming demo messages are sent manually from another controlled account. "Check now" invokes the same real ingestion method as polling; it is not a fixture-injection shortcut.

Poll every 15 seconds for the demo, with non-overlapping jobs and bounded lookback. Persist seen IDs, cursor/high-water mark and notification status. Use overlap to tolerate delivery delays; paginate bounded batches without advancing past unprocessed data. Initial backlog import is separate from new-arrival notification. Unique source/source_id prevents duplicates.

### 6.3 Later scopes

- P1 Chat: chat.messages.readonly; chat.spaces.readonly only for discovery.
- P1 contacts enrichment: contacts.readonly only when implemented.
- Drafts, if added later: gmail.compose; this also grants sending capability, still forbidden without application authorization.
- P2 Drive: drive.file for explicitly selected files, or drive.readonly for authorized existing-folder ingestion. A folder allowlist is not an OAuth folder scope.
- P2 Sheets: spreadsheets.readonly.
- Attention, Focus and Decisions require no extra scopes.

## 7. Voice, context and briefing

PTT is P0. Browser records a supported MIME type; backend normalizes to mono 16 kHz PCM WAV. Limit recording to 30 seconds and upload to 10 MB; return clear errors for silence, unsupported/corrupt format, oversized input and timeout.

faster-whisper multilingual base, CPU int8, beam_size=1, VAD enabled is the starting point. whisper.cpp uses the same normalized input and Transcript contract. Download/warm models before rehearsal. Both providers must be tested; fallback unavailability is visible. Run blocking STT and Google work off the async event loop, with bounded concurrency.

Browser SpeechSynthesis uses the active conversation language, short utterances and stop controls. Verify EN/PL voices on the actual laptop. Text input is recovery, not a substitute for voice acceptance.

PTT while speaking:
1. Cancel SpeechSynthesis immediately.
2. Invalidate previous request playback; record a new request_id.
3. Enter interrupted then listening.
4. Retain the validated active meeting/briefing section.
5. Ignore stale responses, speech callbacks and events.
6. Do not resume the old briefing automatically.

Briefing uses Calendar + Gmail + relevant USER/ORG context. It distinguishes facts, inference and suggestions; unknown history counts remain null. Sources and partial/unavailable retrieval are shown. Cap evidence size; no raw-mail dump into context. Cache by meeting ETag/evidence version/language and invalidate after mutations.

## 8. Attention, Focus and Decision Inbox

Pipeline:
Gmail normalization -> deterministic rules -> optional LLM ambiguity classifier -> hard-rule enforcement -> persisted AttentionItem -> Decision projection -> Focus notification decision -> UI/TTS.

- HIGH financial decision requires current decision intent and amount >= 1000 PLN.
- Important sender gives a minimum MEDIUM floor; exact address maps to CFO.
- Newsletter/marketing is LOW/FYI unless stronger hard evidence wins.
- Unknown currency/ambiguous amount triggers cautious review, not invented conversion.
- LLM output cannot weaken deterministic floors.
- A sender exception affects delivery, not action authorization.
- Treat email body as untrusted evidence, not instructions.

Focus supports duration, LOW/MEDIUM/HIGH threshold, and exact sender allow_interrupt exceptions only. Ambiguous "CFO" identity requires clarification. Persist one active session; overrides expire with it. Start/stop local Focus commands pass policy and may execute as low-risk explicit commands. User preferences cannot mute non-overridable hard policy.

During Focus, low-priority mail is stored without toast/TTS. A qualifying item surfaces once. If user is recording/speaking, queue a short notification rather than corrupt their utterance.

On expiration or explicit stop, show a Focus completion summary from persisted items received during the session: totals, deferred items, decisions, actions and FYI. Categories are disjoint and deduplicated; no new LLM-generated counts. Return the same summary after reconnect/restart. The on-screen summary is required; spoken narration is optional.

"Why am I seeing this?" reads stored rule codes/evidence/policy version and separate delivery reasons, not newly invented LLM justification.

Decision Inbox is fed exclusively by decision_required Attention items. Create exactly one linked Decision transactionally even if notification is deferred. Financial cards expose amount, deadline, sources, risks, preference conflict and suggested next step. Offer RECORD ACCEPT / RECORD REJECT / DEFER / ASK EVA, with "Records your decision only; no payment, purchase or commitment is executed." Recording accept/reject is an explicit user-initiated local_write proposal through the same ApprovalEngine/ToolExecutor; for a HIGH-risk financial decision it requires explicit UI confirmation. It updates only the linked Decision outcome/timestamp and audit receipt; it invokes no Google mutation, payment, purchase, supplier or contract API and sends no message. Repeated confirmation is idempotent. Decision creation still comes exclusively from Attention; outcome recording is not a second ingestion path. The LLM may suggest an outcome but cannot record acceptance by itself. No external financial-commitment tool is registered.

## 9. Persistence, API and event delivery

SQLite minimum:
settings, sessions, proposed_actions, approval_receipts, execution_attempts,
attention_items, decisions, focus_sessions, ingestion_cursors, seen_sources,
briefing_cache, event_outbox.
Credentials live in isolated secret storage, with references as needed.
Use short transactions, unique constraints, foreign keys and one backend worker/scheduler.
Do not hold a SQLite write transaction across a network/model call.
A01 establishes minimal durable tables and constraints as consumers need them; A07/B05 add outbox recovery/replay and deeper restart handling after the complete safe path works.

Canonical API:
~~~text
GET  /api/health
GET  /api/auth/google/start
GET  /api/auth/google/callback
GET  /api/integrations
GET  /api/calendar/today
GET  /api/calendar/events/{event_id}?calendar_id=...
POST /api/calendar/proposals
POST /api/actions/{action_id}/confirm
GET  /api/actions/{action_id}
POST /api/voice/transcribe
POST /api/assistant/message
POST /api/briefing/meeting
GET  /api/attention
GET  /api/attention/{id}/explanation
POST /api/attention/check-now
GET  /api/decisions
GET  /api/decisions/{id}
POST /api/decisions/{id}/outcome-proposals
POST /api/decisions/{id}/defer
POST /api/focus/start
POST /api/focus/stop
GET  /api/focus/current
GET  /api/focus/{id}/summary
GET  /api/settings/llm
PUT  /api/settings/llm
POST /api/settings/llm/test
POST /api/settings/llm/detect
WS   /api/events
~~~

Calendar proposals use a discriminated create/reschedule/update_agenda request. No generic public execute endpoint. UI confirmation and server-validated voice confirmation both call the same approval service. The LLM is never offered an approval tool.

The target event architecture persists sequence/outbox with important state changes. Basic authenticated live events, event IDs, stale-request rejection and REST snapshots are supplied in B03/B04 for the golden path; durable delivery/replay and reconnect recovery are hardened in A07/B05. Do not replay spoken notifications after reconnect. Pending proposals retain their state until expired/rejected, independently of whether TTS is playing.

## 10. Dependency graph

~~~mermaid
flowchart TD
  Contract[Contract freeze A00] --> Backend[A foundation and Google adapters]
  Contract --> UI[B typed frontend]
  Contract --> Runtime[B private inference adapter]
  Backend --> Policy[A triage and approval]
  Policy --> Exec[A guarded executor]
  Backend --> Brief[B briefing and agent]
  Runtime --> Brief
  Exec --> Brief
  STT[A STT] --> Voice[B voice UX]
  Voice --> Brief
  Backend --> Ingest[A Gmail ingestion and rules]
  Ingest --> Attention[B Attention integration]
  Attention --> Focus[B Focus]
  Attention --> Decision[B Decision Inbox]
  UI --> Voice
  Focus --> Demo[Complete golden demo]
  Decision --> Demo
  Brief --> Demo
  Exec --> Demo
~~~

Google OAuth and runtime/Tailscale access are early human setup gates. B may implement against canonical fixtures while A builds services. Fixture success never substitutes for the real integration gate.

## 11. Delivery schedule and gates

| Time | Stream A - Core Platform | Stream B - Experience Layer | Human/review gate |
|---|---|---|---|
| 0-1h | A00 contracts, skeleton | B00 time-boxed probe/schema review, then B02A visual shell as soon as probe permits | Google project/account access, GitHub and Tailscale |
| 1-4h | A01 minimal persistence, A02 OAuth/Google reads, A03 policy | Finish B02A early; B01 runtime adapter; begin B02B core screens | G1: real reads, private inference, policy tests, polished typed Today/Voice shell |
| 4-8h | A04 safe Calendar writes, A05 STT | Finish B02B by hour 5; B03 agent/briefing and voice hookup | G2: steps 1-12 plus create/reschedule acceptance |
| 8-12h | A06 Gmail ingest and Attention rules | B04 Attention/Focus/Decision integration | G3: all 19 steps on real account |
| 12-16h | A07 deep restart/reconciliation/outbox/reset | B05 event/reconnect recovery and failure UX | G4: failure injection and final-account onboarding |
| 16-20h | Resolve defects, backend latency | B06 core visual polish and E2E | G5: three consecutive passes |
| 20-24h | Feature freeze, fixes only | Feature freeze, fixes only | Final rehearsals and presentation |

Stream B is one sequential stream, not simultaneous runtime/frontend workers. Target order: B00 (0-0.5h), B02A (0.5-2h), B01 (2-3h), B02B (3-5h), B03 (5-8h). Before A00 lands, only layout/theme/orb chrome may be built; typed domain fixtures wait for generated contracts. A delayed live endpoint must not block this independent visual work. B06 is refinement/rehearsal, never the first design pass.

These are target windows, not evidence of measured agent throughput. Limit task batches to one owned slice and tests. If G2 slips, cut optional scope immediately. Never silently drop create/reschedule; escalate schedule risk to the human while completing required work.

## 12. Golden path test matrix

| Step | Acceptance | Failure cases |
|---|---|---|
| 1 Ask today | PTT transcript in EN/PL | Mic denied, silence, unsupported audio |
| 2 Calendar | Correct Warsaw-day meetings | Token failure, timezone/all-day/pagination |
| 3 Prepare ACME | Unique active meeting selected | Ambiguous meeting -> clarify |
| 4 Gmail history | Real messages with sources | MIME, bounded retrieval, absent body |
| 5 Briefing | Grounded facts and partial status | Hallucinated commitments, invalid schema |
| 6 Speak | One concise utterance | Missing voice, duplicate playback |
| 7 Interrupt | Immediate TTS cancel, context retained | Late callbacks, echo, old response resumes |
| 8 Modify agenda | Exact active event targeted | Lost/wrong calendar context |
| 9 Proposal | Immutable before/after with expiry | Lost description, malformed tool args |
| 10 Policy | HIGH ACME edit -> HIGH action | Model downgrade, wrong threshold |
| 11 Approval | Explicit UI; voice denied | Replay, expired challenge, changed args |
| 12 Calendar write | Once, verified by read-back | 403, 412, uncertain timeout, restart |
| 13 Focus | Two hours plus CFO-address exception | Ambiguous sender, duplicate session |
| 14 Low mail | Real arrival silently deferred | Old mail re-notified, duplicate polling |
| 15 Finance mail | Real 12,400 PLN decision evidence | Quotation, display-name spoof, locale parsing |
| 16 Surface | One short interruption | LLM outage, active mic, duplicate event |
| 17 Why | Exact item's stored explanation | Wrong ID, missing evidence |
| 18 Explain | Threshold/deadline/sender plus delivery reason | Invented reasoning, old policy confusion |
| 19 Decision | One linked item, no payment execution | Independent ingestion, duplicate/false approval |

Additional mandatory Calendar acceptance:
- Create a new ordinary meeting after valid approval; verify title, dates, attendees, Google ID.
- Reschedule it after valid approval; verify original event changed without duplication.
- HIGH meeting reschedule refuses casual voice approval and supports explicit UI.
- Stale ETag, recurring-series ambiguity, notification impact and create-timeout reconciliation.

Cross-cutting:
- Threshold 999.99/1000/1000.01 PLN; amount without decision intent.
- Injected email instructions cannot authorize writes.
- Focus restart/expiry and sender overrides cannot weaken approval.
- Mandatory runtime rejects cloud endpoints in its route and passes with cloud disabled and no cloud keys.
- Private primary/fallback failure never silently switches to cloud.
- If optional cloud support is built: default-off, explicit route, both consent gates, derived Workspace history, revocation and tool-result continuations are tested before use.
- RECORD ACCEPT/REJECT persists a local audited outcome only; HIGH confirmation is required and no financial/external mutation adapter runs.
- Focus completion summary counts match deduplicated stored items and survive reload.
- Event reconnect restores state without repeating speech.
- Final account has correct scopes and owned seeded events.

Release requires three full real-account passes, including a restart, plus create/reschedule acceptance. Labeled fixtures/recordings are fallback material only, not a live-pass claim.

## 13. Demo preparation and reset

Seed relative to demo date in Europe/Warsaw:
- Team Sync (MEDIUM), ACME Contract Review (HIGH), Catch-up (LOW), Board Call (HIGH).
- ACME pricing history, an unresolved migration date, a prior proposal promise.
- Exact controlled CFO sender address, financial request for 12,400 PLN.
- Newsletter and routine FYI.
- Another controlled sender delivers low/high messages during demo.

Reset restores only known demo-owned resources by allowlisted IDs and clears only local demo state. Never bulk-delete a real calendar/inbox. A reset is an explicit human maintenance action outside the agent tool list. Incoming messages stay in Gmail; reset establishes a fresh baseline so historical items are not announced again. Do not commit raw messages, tokens or the SQLite database.

Demo sequence uses HIGH agenda UI confirmation. Create/reschedule may be demonstrated immediately afterward or in a short pre-demo acceptance check; both must be working deliverables.

## 14. Cut order

KEEP: complete golden path; Calendar read/create/reschedule/agenda; approval safety; real Gmail; EN/PL voice; self-hosted inference; minimal Focus/Attention/Decision; core polish and rehearsal.

CUT FIRST: Optional cloud runtime support, Graph, Obsidian, RAG, external search, finance watchlist, advanced analytics.
CUT SECOND: Chat, wake word, People enrichment, standalone interaction intelligence, drafts/send, Morning Briefing/Drive Mode.
CUT THIRD: topic-based Focus, token streaming, extra theme customization beyond required dark/light, advanced settings, full calendar editor, focus summary speech. Keep the on-screen Focus completion summary and B02A dark/light foundation.

Never cut mandatory Calendar operations, policy enforcement or real-source acceptance to save optional features. If all optional work is removed and required scope still cannot fit, report the delivery risk rather than redefine success.

## 15. Observability and reliability

Log request/session/action IDs, tool/provider/model, latency, rule codes, action risk and approval outcome. Do not log tokens, API keys or raw sensitive bodies. Health reports component readiness, actual provider/model and fallback availability.

Targets to measure on demo hardware:
- PTT visual feedback and interruption response: under 100 ms target.
- STT after speech: approximately 0.5-2 s target, not a guarantee.
- TTS start after accepted text: under 500 ms target.
- Real incoming-item detection: next successful 15 s poll, or Check now.
- Inference: measure with representative briefing evidence, not a tiny hello prompt alone.

Model requests have a 45 s initial configurable deadline, one structured-output repair and at most four tool rounds. Read APIs use bounded retries. Mutations never blindly retry uncertain outcomes.

## 16. Roadmap retained

Full Executive Attention Layer: cross-channel ranking, learned preferences with hard-policy boundaries, delegation, drafting, adaptive Focus.
Knowledge: Drive RAG, organizational graph, Obsidian, confidence/provenance.
Connectors: Microsoft 365, Slack/Teams, CRM, ticketing.
Meeting intelligence: during/after transcription, tasks, follow-ups.
Enterprise: multi-user, roles, shared policy and organizational administration.
No Kafka, Kubernetes, Neo4j, unnecessary microservices or distributed queues in the hackathon.

## 17. Version 2.0 change log

- Mandatory runtime remains self-hosted; final correction permits optional explicit cloud support with both privacy defaults off, never silent recovery or a demo dependency.
- OpenRouter development use remains separate from optional application configuration.
- Removed fixed-model-label dependency; actual server-discovered/configured ID is authoritative.
- Stream B directly builds React/TypeScript + Tailwind CSS + shadcn/ui; no export dependency.
- Split early frontend work into B02A foundation and B02B core screens; B06 remains refinement.
- Added internal financial outcome recording and required on-screen Focus completion summary.
- Kept safety checks early while moving deeper crash/replay recovery after the safe golden path.
- Defined local app plus Tailscale-hosted private inference, network dependency and fallback constraints.
- Kept all Calendar write operations mandatory.
- Corrected HIGH ACME approval and financial-decision semantics.
- Made full Attention/Focus/Decision demo a release gate.
- Added contracts gate, ownership, execution lifecycle, OAuth details, polling, event recovery and reset.
- Replaced broad implementation streams with two dependency-aware work plans.
- Moved Google account/OAuth verification to early setup, while permitting final new-account onboarding later.
- Archived the original plan without modifying its historical contents.

## 18. Verified reference material

References checked 2026-09-19; actual installed/provider behavior must still be probed.
- [Unsloth authenticated API, model IDs and tools](https://unsloth.ai/docs/basics/api)
- [Tailscale grants and port restrictions](https://tailscale.com/docs/features/access-control/grants)
- [Git worktrees](https://git-scm.com/docs/git-worktree)
- [OpenRouter model catalog - development only](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties)
- [OpenRouter support and free-model limits](https://openrouter.ai/support)
- [Calendar scopes](https://developers.google.com/workspace/calendar/api/auth)
- [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [OAuth web-server flow](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Google testing audience and token expiry](https://support.google.com/cloud/answer/15549945?hl=en)
- [Verification exceptions](https://support.google.com/cloud/answer/13464323?hl=en)
- [Calendar conditional resource updates](https://developers.google.com/workspace/calendar/api/guides/version-resources)
- [Calendar event patch and notifications](https://developers.google.com/workspace/calendar/api/v3/reference/events/patch)
