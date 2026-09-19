# EVA Block B - Experience Layer Implementation Plan

> For agentic workers: execute assigned tasks sequentially using superpowers:executing-plans when available. Do not spawn additional workers or implement Block A without an explicit assignment.

**Version:** 2.0 / 2026-09-19 - final architecture correction
**Goal:** deliver the voice-first UI, private inference integration, agent/briefing, Attention/Focus/Decision flow and E2E demo.
**Architecture:** frontend uses FastAPI only. Backend orchestration imports A's contracts and invokes A's guarded registry/executor.
**Tech stack:** React/TypeScript, Tailwind CSS, shadcn/ui, browser audio/TTS, Python orchestration and self-hosted OpenAI-compatible inference client.
**Spec:** EVA_IMPLEMENTATION_PLAN.md version 2.0.
**Coding model:** verified available OpenRouter model selected in the coding-agent client.
**User candidates:** DeepSeek v4.1 or NVIDIA Nemotron Ultra/free variants; exact catalog IDs, availability, capabilities and cost are not assumed.
**Branch:** work/experience.
**Provider separation:** OpenRouter is the Stream B DEVELOPMENT provider. Mandatory EVA runtime is self-hosted. Optional explicit cloud API support is a separate later feature, not required for startup, recovery or MVP acceptance.

## Global constraints

- B01 implements mandatory self-hosted runtime only. Optional OpenRouter/OpenAI/custom cloud support may be added in B07 only if time remains; never silent automatic recovery.
- Default allow_cloud_inference=false and allow_workspace_cloud_inference=false. Cloud code/keys are not required for mandatory acceptance; preserve the provider interface and Settings design if B07 is omitted.
- A owns canonical schema, generated types, backend lockfile, main.py, policy and executor.
- No Google OAuth/token handling or direct Google/LLM requests in frontend.
- No duplicating policy in UI; UI renders backend decisions.
- Financial cards expose evidence/risk and internal RECORD ACCEPT/RECORD REJECT/DEFER/ASK EVA; no payment, purchase, supplier or contract-commitment executor.
- Use synthetic fixtures in development-model context; real-account tests run separately by HUMAN.
- Send backend dependency/router changes to A instead of editing shared files.
- B directly creates and owns frontend source in React/TypeScript + Tailwind CSS + shadcn/ui, using A-generated types. No export/import handoff exists.

## Review focus

1. Late responses and speech callbacks must not restart interrupted speech (B02B/B03).
2. Private inference timeout must never silently route Workspace content to any cloud provider (B01; B07 additionally tests explicit consent).
3. Partial evidence must not become fabricated facts or complete-history claims (B03).
4. Focus delivery and Decision persistence must be independent and deduplicated (B04).
5. Reconnect/restart must restore UI state without replaying notifications (B05).

## Required task cycle

- [ ] Read current spec and contract commit; inspect only relevant code.
- [ ] Add and run the task's focused failing tests.
- [ ] Implement owned files using canonical fixtures/services.
- [ ] Run focused tests, typecheck and affected contract checks.
- [ ] Commit a small slice; report SHA, results, dependencies and integration instructions.
- [ ] Wait for required A merge; do not create an alternate backend to bypass it.

## B00 - Development-client and private runtime probe (target 0-0.5h)

**Dependencies:** human coding client/OpenRouter configuration; human Tailscale/Unsloth access.
**Own:** scripts/probe_runtime.py, docs/runtime-probe.md; no secrets in either.
**Consumes:** intended LLMProvider contract; A00 schema for final output format.
**Produces:** actual private model ID, capability/latency findings and source-redacted diagnostics.
**Steps:**
- [ ] Verify coding client can read/write its worktree and run a harmless test command.
- [ ] Query available development models; choose an exact ID supporting the client's tools/context needs.
- [ ] Probe self-hosted runtime health, model discovery/manual ID, EN/PL response, tool call and structured output.
- [ ] Probe tailnet reachability from the DEMO laptop, not merely from the inference host.
- [ ] Record whether runtime built-in tools are disabled and authentication enforced.
**Done:** coding provider and application provider are demonstrably separate.
**Tests:** offline synthetic HTTP fixtures for malformed model list, 401, timeout and invalid tool JSON; human live probe prints status/ID/latency only.
**No automatic assumptions:** a model name or a successful hello prompt proves neither coding quality nor representative briefing latency. Expected demo family is Qwen 3.8 Next / Fast / Flash; only the ID exposed/configured by the running server is authoritative. Unsloth is primary; compatible self-hosted vLLM, LM Studio, Ollama or llama.cpp endpoints remain possible.
**Time box:** record unavailable live setup as a probe finding and continue independent B02A shell/theme work; finish the live probe before G1. Do not invent a model ID or block all frontend work on network setup.

## B01 - Self-hosted LLM provider/router (target 2-3h, after B02A foundation)

**Dependencies:** A00; B00 live findings.
**Own:** backend/app/llm/{base,openai_compatible,router}.py, backend/app/api/settings.py, backend/tests/unit/test_llm.py.
**Consumes:** canonical LLMProvider, ChatMessage, ToolDefinition, LLMResponse.
**Produces:** one provider-independent private inference client and approved self-hosted fallback.
**Steps:**
- [ ] Implement Chat Completions normalization and dotted-name wire mapping.
- [ ] Enforce the mandatory self-hosted route allowlist; reject cloud endpoints in this route and unapproved redirects. Public-cloud support is separate, never recovery.
- [ ] Normalize function arguments; reject unknown tools; validate JSON structure.
- [ ] Add a 45 s configurable deadline and one repair attempt.
- [ ] Use only configured self-hosted fallback; same-host fallback health is distinguished from network redundancy.
- [ ] Expose self-hosted Base URL, API key if required, actual Model ID, Detect Models, Test Connection, Health and available latency. Keep secrets redacted.
- [ ] Preserve optional cloud Settings schema/design with both permissions false; cloud keys/settings are not required for startup. No active optional-cloud adapter is needed here.
**Done:** all actual tool execution remains outside this module.
**Tests:** primary success/failure, fallback success/failure, cloud origin rejected in mandatory route, startup without cloud settings, malformed tool call, deadline, built-in tools disabled, secrets redacted.
**Run:** python -m pytest backend/tests/unit/test_llm.py -q

Example acceptance to implement:
~~~python
async def test_no_cloud_recovery(router, primary_down, outbound_requests):
    await router.try_request_or_report_unavailable()
    assert all(req.origin in router.approved_self_hosted_origins
               for req in outbound_requests)
~~~
The helper is a test-harness concept; use the A00-frozen router API, not a new production abstraction.

## B02A - Frontend foundation (target 0.5-2h; starts immediately after B00 time box)

**Dependencies:** B00 coding-client readiness. Pure visual shell/theme/orb chrome can start before A00; domain fixtures and typed transport require A00's generated TS. No backend endpoint or external frontend export is required.
**Own:** frontend/package.json and lockfile, frontend toolchain/config, src/styles/*, src/components/ui/*, src/components/layout/*, src/components/voice/VoiceOrb.tsx, src/pages/Today.tsx, src/api/{client,mock}.ts, foundation tests. Never edit api/types.generated.ts.
**Consumes:** A-generated contracts and canonical fixture schema when available.
**Produces:** a visually coherent, navigable product shell with typed mock Today/voice states.
**Steps:**
- [ ] Create React/TypeScript frontend directly with Tailwind CSS and shadcn/ui.
- [ ] Establish typography, spacing, color, elevation and motion tokens; implement dark/light themes from the start.
- [ ] Build app shell, navigation, global EVA Voice Orb and Today dashboard.
- [ ] Use a premium, calm executive-productivity style: modern, clear information hierarchy and minimal visual noise.
- [ ] Avoid generic admin/chatbot layouts, purple AI gradients, cyberpunk styling and excessive cards/borders.
- [ ] Add canonical typed fixtures/mock transport after A00 lands; do not invent local domain models while waiting.
- [ ] Define the API-client transport boundary so REST/WebSocket can replace mocks without rewriting screens.
**Done:** Today and voice-state presentation are visually reviewed in dark/light themes with canonical fixtures, keyboard navigation and responsive layout. Fixture mode is visibly labeled. Backend readiness does not block this review.
**Tests:** frontend build/typecheck; generated-type fixture validation; navigation; theme persistence; keyboard focus; readable loading/empty/error states; orb state rendering.
**Run:** npm --prefix frontend run typecheck
**Run:** npm --prefix frontend test -- --run
**Run:** npm --prefix frontend run build
**Handoff:** screenshots/manual visual-review notes and exact client transport signatures; do not postpone design decisions to B06.

## B02B - Core product screens and voice controller (target 3-5h; real STT hookup by 8h)

**Dependencies:** A00 and B02A; B01 finishes before backend inference integration, but mock screens do not require it. A05 is required only for real STT.
**Own:** frontend/src/components/{briefing,attention,focus,decisions,approvals,voice}/*, src/pages/* beyond Today, src/hooks/useVoiceSession.ts, relevant client/store code and tests. Stream B retains frontend ownership; A retains generated types.
**Consumes:** canonical generated types, typed fixture transport, approval/event/Focus/Decision contracts.
**Produces:** Executive Briefing, Attention, stored explanation view, Protect My Focus/temporary sender exception/completion summary, Decision Inbox and action approval UI.
**Steps:**
- [ ] Build core screens against typed mocks: evidence-backed briefing and "Why am I seeing this?" rendering stored Reasons.
- [ ] Keep priority reasons visually separate from Focus/delivery reasons.
- [ ] Add Focus duration/threshold/exact sender exception controls, expiry state and completion summary.
- [ ] Add RECORD ACCEPT / RECORD REJECT / DEFER / ASK EVA; show that recording a decision executes no financial transaction or commitment.
- [ ] Build HIGH explicit UI confirmation and MEDIUM pending-approval UX without frontend risk computation.
- [ ] Implement PTT recording, multipart transport, transcript/language/error states and browser SpeechSynthesis.
- [ ] Implement immediate cancellation, request invalidation and stale-callback rejection while retaining active context.
- [ ] Show sources, before/after, partial retrieval and unknown action outcomes.
- [ ] Build mandatory AI Engine controls against B01: self-hosted Base URL, optional required-provider key, actual Model ID, Detect Models, Test Connection, Health and available latency; retain the optional cloud Settings design without making it a dependency.
**Done:** core scenes are visually reviewed with mocks by hour 5; real FastAPI/STT/REST/WebSocket integration follows in B03/B04. All domain data uses generated contracts.
**Tests:** source/reason rendering; financial copy and local-outcome state; Focus summary and exception expiry; mic denied/silence/double-click; late TTS callback/stale event; HIGH voice cannot masquerade as UI confirmation.
**Run:** npm --prefix frontend run typecheck
**Run:** npm --prefix frontend test -- --run
**Polish:** use B02A design tokens consistently; B06 is final refinement/rehearsal, not the first visual pass.

## B03 - Executive agent and briefing (target 5-8h)

**Dependencies:** A02 reads, A03 policy, A04 registry/executor, B01/B02A/B02B; A05 for real STT.
**Own:** backend/app/agent/{executive_agent,context_builder,prompts}.py, backend/app/meetings/briefing.py, backend/app/api/{assistant,briefing,events}.py, backend/tests/integration/test_briefing_agent.py.
**Consumes:** canonical active context, registry, Google read services, ApprovalEngine through executor path.
**Produces:** request -> evidence -> structured proposal/read -> result -> concise answer.
**Steps:**
- [ ] Resolve meeting references from backend-validated active context; clarify ambiguity.
- [ ] Retrieve bounded Gmail/Calendar evidence; mark content as untrusted data.
- [ ] Construct claims with provenance and partial retrieval indicators.
- [ ] Ask LLM for registered tools only, with at most four tool rounds.
- [ ] Delegate mutations to A's proposal/execution services; never invoke Google mutation methods directly.
- [ ] Keep pending action state separate from voice playback state.
- [ ] Provide basic authenticated live events and REST snapshots with request/event IDs; hand A router wiring before G2. Durable replay/reconnect hardening belongs to B05.
- [ ] On interruption ignore superseded responses; never hide actual completed mutation outcomes.
**Done:** main steps 1-12 pass with real services and cloud disabled; HIGH agenda approval uses UI. No optional cloud implementation is required.
**Tests:** ambiguous ACME, insufficient evidence, invalid schema, prompt injection, model-generated approval receipt rejected, request supersession, successful result is required before success speech.
**Run:** python -m pytest backend/tests/integration/test_briefing_agent.py -q

## B04 - Attention, Focus and Decision integration (target 8-12h)

**Dependencies:** A01 repositories, A06 ingestion/rules, B01 and generated schemas.
**Own:** backend/app/attention/{engine,focus,explanations}.py, backend/app/decisions/service.py, backend/app/scheduler/{engine,jobs}.py, backend/app/api/{attention,focus,decisions}.py, associated frontend views, backend/tests/integration/test_attention_focus.py.
**Produces:** real polling/check-now -> explanation -> Focus delivery -> Decision Inbox.
**Steps:**
- [ ] Route normalized events through A's rules; use LLM only for unresolved ambiguity.
- [ ] Apply deterministic floors after model classification.
- [ ] Persist item and unique linked Decision in one transaction.
- [ ] Implement one active Focus session with expiration and exact sender exceptions.
- [ ] Keep priority reasons separate from delivery reasons.
- [ ] Make Check now call the same real ingestion service as scheduled polling.
- [ ] Queue spoken notification while mic is active; deduplicate notification delivery.
- [ ] Financial cards record accept/reject through a user-initiated local_write ProposedAction and A's approval/executor path; HIGH financial records require explicit UI. Persist outcome/timestamp plus receipt, with idempotency and no external adapter call. Defer/Ask EVA remain separate.
- [ ] Compute on-screen Focus completion summary from deduplicated persisted session items on expiry/stop, including decisions/actions/FYI and deferred totals. Spoken narration is optional.
- [ ] Publish basic Attention/Focus/Decision events using B03 infrastructure and send A registration instructions before G3.
**Done:** all 19 steps pass on real incoming mail; financial decision persists even when its notification is deferred.
**Tests:** CFO override affects delivery only; expiry/summary counts and reload; low silent deferral; high decision surfaced once; model outage; duplicate source; ambiguous sender; record accept/reject persists only locally with UI receipt; replay does not duplicate records; no payment/purchase/supplier/contract call. Deep crash recovery follows in B05.
**Run:** python -m pytest backend/tests/integration/test_attention_focus.py -q
**Handoff:** give A exact scheduler and router registration instructions; do not edit main.py.

## B05 - Event stream, reconnect and failure UX (target 12-16h)

**Dependencies:** B03/B04 and G3; uses A00-frozen repository/outbox interfaces. Coordinate with A07 without waiting for its completion; integrate producer/consumer implementations before G4.
**Own:** backend/app/api/events.py, frontend event client/store, backend/tests/integration/test_events.py, frontend reconnect tests.
**Steps:**
- [ ] Harden the existing B03/B04 live events with durable envelopes via A07's outbox; this is not the first WebSocket integration.
- [ ] Deduplicate event IDs and reject stale request IDs.
- [ ] Reconnect with authoritative snapshots; do not replay old TTS.
- [ ] Render pending/executing/unknown/failed action states truthfully.
- [ ] Show private inference/Tailscale loss and available local-only recovery.
- [ ] Test Focus expiration and provider recovery without full-page reset.
**Done:** restart/reconnect never causes duplicate action submission or spoken notification.
**Tests:** dropped socket, sequence gap, stale proposal, unknown write outcome, no model available, invalid session/origin.
**Run:** python -m pytest backend/tests/integration/test_events.py -q
**Run:** npm --prefix frontend test -- --run

## B06 - Final visual refinement, full demo rehearsal and handoff (target 16-20h)

**Dependencies:** all integrated A/B tasks; final demo account.
**Own:** refinement of B02A/B02B visual design, frontend/e2e/*, docs/demo-script.md, docs/demo-acceptance.md. Do not begin the design system or product screens here.
**Steps:**
- [ ] Automate deterministic 19-step flow using labeled fixtures and instrumented adapters.
- [ ] Add create/reschedule scenarios, HIGH voice rejection, local financial outcome recording and Focus completion summary.
- [ ] Human runs the equivalent flow with actual microphone, Google account and private model.
- [ ] Verify Polish/English voices, citations, agenda diff and financial explanation are legible on presentation display.
- [ ] Record three consecutive live passes, one including restart.
- [ ] Freeze features at hour 20; fix only reproducible blockers and demo defects.
**Done:** report actual results separately for fixture E2E and live acceptance.
**Run:** npm --prefix frontend run typecheck
**Run:** npm --prefix frontend run build
**Run:** npm --prefix frontend run test:e2e
**Never:** turn off policy or swap in unlabeled fake data to obtain a passing demo.

## B07 - Optional explicit cloud runtime extension (unallocated; only if time remains)

**Priority:** optional, never a G1-G5 dependency and never required for MVP acceptance. Skip completely if self-hosted golden path, required recovery or rehearsal work would be delayed; no work after feature freeze except fixes.
**Dependencies:** stable self-hosted G5 acceptance; A00 provider/settings contracts; human-enabled configuration only at use time.
**Own:** optional backend/app/llm/cloud.py, B-owned settings/router/UI and tests; A handles any shared dependency/config wiring.
**Produces:** optional OpenRouter/OpenAI/custom compatible cloud route, separate from the mandatory self-hosted primary/fallback.
**Steps:**
- [ ] Retain Disabled as default, optional cloud key/model/Test Connection and both privacy settings false.
- [ ] Require explicit cloud inference enablement and explicit route selection per request; never retry a failed private request through cloud automatically.
- [ ] Require separate Workspace-cloud permission for raw or derived Workspace context, including history, summaries and subsequent tool results. Recheck every outbound request; ambiguous provenance fails closed.
- [ ] Keep cloud secrets server-side and separate from development-client credentials; Test Connection uses synthetic content after opt-in.
- [ ] Apply the same tool/policy boundary; revocation blocks subsequent calls; show the active provider.
**Tests:** startup without cloud settings, disabled defaults, explicit route, synthetic test connection, Workspace opt-out with derived history, tool-result continuation, revoked permission, no silent fallback.
**Done:** optional tests pass and all mandatory acceptance still passes with cloud disabled.
**If omitted:** preserve the LLMProvider interface and Settings design; unavailable controls are hidden or clearly disabled, never fake-connected. Omission is not a blocker.

## Initial prompt for Stream B - Experience Layer

Read EVA_IMPLEMENTATION_PLAN.md version 2.0 and this work plan. You are Stream B (Experience Layer) only.
OpenRouter is your CODING provider. Mandatory EVA inference is self-hosted;
optional cloud runtime is a separate B07 extension, disabled by default and never
automatic recovery. Begin with time-boxed B00, then B02A visual foundation early.
Use A00's generated contracts for fixtures; continue B01 -> B02B -> B03. Use generated types,
synthetic fixtures, A's registry/executor and the assigned files. Do not edit A's
contracts, lockfile, policy or main.py. Return a small commit, test results,
registration instructions and blockers after each task. Do not implement all
features at once.
