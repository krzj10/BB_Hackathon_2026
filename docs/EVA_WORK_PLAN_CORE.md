# EVA Block A - Core Platform Implementation Plan

> For agentic workers: execute assigned tasks sequentially using superpowers:executing-plans when available. Do not spawn additional workers or implement Block B without an explicit assignment.

**Version:** 2.0 / 2026-09-19 - final architecture correction
**Goal:** supply the tested backend, Google integrations, STT, persistence and deterministic execution boundary.
**Architecture:** FastAPI services and SQLite on the demo laptop; services share canonical contracts. Only the guarded executor invokes mutating Google adapters.
**Tech stack:** Python, FastAPI, Pydantic, SQLite, Google OAuth/API clients, faster-whisper, whisper.cpp.
**Spec:** EVA_IMPLEMENTATION_PLAN.md version 2.0.
**Coding model:** verified Qwen serving model via self-hosted Unsloth over Tailscale. Expected demo family: Qwen 3.8 Next / Fast / Flash variant. The actual discovered/configured server ID is authoritative, not a fixed marketing label.
**Coding host:** a coding-agent client in the Core Platform worktree, preferably on the developer laptop.
**Branch:** work/core.
**Runtime distinction:** the Core Platform development model writes backend source through a coding client; the model itself is not the FastAPI backend.

## Global constraints

- Mandatory runtime and golden-path acceptance use self-hosted OpenAI-compatible inference. Optional cloud support is separate, disabled by default and never automatic recovery; it must not delay this block.
- HIGH meeting mutations require explicit UI approval.
- Calendar create, reschedule and agenda editing are all mandatory.
- Financial decisions may record internal accept/reject/defer outcomes; no payment, purchase, supplier or contract commitment is executed.
- No competing models, policy engines, registries or executors.
- B directly builds React/TypeScript + Tailwind CSS + shadcn/ui and owns llm/, frontend/, orchestration and named integration services. No frontend export/human handoff is needed.
- A owns backend dependencies/lockfile, canonical contracts, generated TS and main.py.
- No account secrets or real Workspace data in commits, fixtures or model prompts.
- Contract changes require reviewer agreement and updated generated types/fixtures. A00 includes Decision outcome fields, FocusCompletionSummary and default-off optional cloud settings; no cloud adapter is required.
- Do not make an unreviewed merge or begin another stream's files.

## Review focus

1. Concurrent approvals, stale ETags and network ambiguity must not duplicate writes (A04).
2. Initial Gmail backlog and poll overlap must not duplicate notifications or decisions (A06).
3. High-priority meeting agenda edits must remain HIGH even if the LLM labels them medium (A03).
4. Browser audio and silent Polish/English recordings must not block the event loop (A05).
5. Recurrence/all-day/timezone or notification effects must not be silently altered (A02/A04).

## Required task cycle

For each task:
- [ ] Read the task and version 2.0 spec; inspect the current shared contract commit.
- [ ] Add the named tests, run them, and observe the missing behavior.
- [ ] Implement only the assigned slice and owned files.
- [ ] Run focused tests plus affected contract checks.
- [ ] Commit the owned files and report commit SHA, commands/results, API changes and blockers.
- [ ] Wait for integration acknowledgement before starting a task needing unmerged B changes.

Implementation order: establish contracts/minimal durable state, reads, policy and safe writes first; complete the 19-step path before deeper A07 restart/outbox hardening. Digests, expiry, ETags, receipts, atomic claims, idempotency and unknown-outcome replay blocking are never deferred. A owns incremental main.py/dependencies.py wiring as B modules arrive; composition is not postponed to A07.

Test filenames below are planned artifacts. Python test root is backend/tests; commands run from repository root after A00 establishes packaging. Network tests are explicitly opt-in and run by HUMAN using isolated credentials.

## A00 - Contracts and backend skeleton (target 0-1h)

**Dependencies:** approved version 2.0; human repository initialization.
**Own:** backend/pyproject.toml, backend lockfile, backend/app/contracts/{domain,api,providers}.py, backend/app/{main,config,dependencies}.py, backend/app/api/health.py, scripts/export_contracts.py, frontend/src/api/types.generated.ts, docs/api-contracts.md, backend/tests/unit/test_contracts.py, .env.example.
**Consumes:** section 4 contract inventory and API section.
**Produces:** canonical models/provider protocols, schema bundle, generated TS, serialized fixture bundle, health endpoint, installable backend package.
**Steps:**
- [ ] Implement all required domain contracts, action state enums, source/claim models and discriminated Calendar proposal arguments; add nullable Decision outcome/recorded_at, FocusCompletionSummary and local outcome-proposal schema.
- [ ] Add aware-datetime, interval, extra-field, enum, money and HIGH approval validation tests.
- [ ] Define response envelopes for all version 2.0 endpoints and typed WebSocket payloads.
- [ ] Export JSON schema and generate TS without a second manual model layer.
- [ ] Define mandatory self-hosted settings and optional cloud settings with allow_cloud_inference=false and allow_workspace_cloud_inference=false; no cloud key/model is required at startup. B owns Settings routes and implementation.
- [ ] Publish a commit for B's contract review; accept corrections before service integration.
**Done:** imports work; health starts with external services unavailable; valid fixtures pass Python and TS checks; invalid schema cases fail.
**Tests:** test_contracts.py, schema regeneration --check, health TestClient.
**Run:** python -m pytest backend/tests/unit/test_contracts.py -q
**Handoff:** contract commit SHA, schema/fixture paths, exact service signatures.

Example acceptance to implement:
~~~python
def test_high_action_cannot_allow_voice(valid_high_action):
    payload = {**valid_high_action, "voice_approval_allowed": True}
    with pytest.raises(ValidationError):
        ProposedAction.model_validate(payload)
~~~

## A01 - Minimal SQLite state and safety constraints (target 1-2h)

**Dependencies:** A00.
**Own:** backend/app/db/{session,schema,repositories}.py, backend/tests/integration/test_repositories.py.
**Produces:** minimal durable action/receipt/attempt and session repositories, with canonical storage interfaces for Attention/Decision/Focus/cursors/outbox. Add consumer-specific operations as those features land; no general recovery subsystem in this hour.
**Steps:**
- [ ] Establish schema/version metadata and tables needed for current consumers; preserve the full version 2.0 schema contract without building unused recovery workers.
- [ ] Add unique (source, source_id) and unique Decision.attention_item_id constraints.
- [ ] Provide atomic claim_action(action_id, expected_status) and durable action/receipt state; reserve the transactional outbox boundary for A07 hardening.
- [ ] Return immutable action snapshots; do not expose arbitrary state mutation from API payloads.
- [ ] Persist proposal expiry now; add Focus timestamps, local decision outcome/audit storage and poll cursors with A06/B04. Keep the A00-frozen repository interfaces.
**Done:** basic reopen preserves action records; two claims yield one winner; no network work inside transactions. Automated crash-window reconciliation and outbox replay are A07.
**Tests:** rollback, foreign key failure, expired action, concurrent claim, simple reopen; duplicate source/Decision constraints tested when A06/B04 uses them. Deep restart tests remain A07.
**Run:** python -m pytest backend/tests/integration/test_repositories.py -q

## A02 - OAuth and Google reads (target 2-3h, begin earlier if A01 completes)

**Dependencies:** A00/A01, human OAuth client/account setup.
**Own:** backend/app/google/{auth,calendar,gmail}.py, backend/app/api/{auth_google,calendar}.py, backend/tests/integration/test_{google_auth,calendar_reads,gmail}.py.
**Consumes:** Meeting/SourceRef/NormalizedSourceEvent.
**Produces:** OAuth flow, serialized refresh, granted-scope health; Calendar list/get; Gmail search/get-thread.
**Steps:**
- [ ] Implement one-time state validation, exact callback and offline credentials.
- [ ] Store tokens outside source control and exclude secrets from logs/responses.
- [ ] Normalize owned-calendar timed/all-day events, organizer and ETag.
- [ ] Retrieve bounded Gmail bodies and provenance; report truncation/partial retrieval.
- [ ] Send human a live-smoke procedure that prints IDs/status only, never tokens or bodies.
**Done:** human confirms real Calendar and Gmail reads; mock tests cover failure cases.
**Tests:** callback replay/denial; refresh failure; missing scope; Warsaw midnight/DST; all-day exclusive end; MIME multipart/HTML/base64url; bounded pagination.
**Run:** python -m pytest backend/tests/integration/test_google_auth.py backend/tests/integration/test_calendar_reads.py backend/tests/integration/test_gmail.py -q
**Handoff:** Calendar/Gmail service imports and real-read status to B.

## A03 - Meeting triage and policy kernel (target 3-4h)

**Dependencies:** A00; A01 for persisted proposals.
**Own:** backend/app/meetings/triage.py, backend/app/approvals/{policy,engine}.py, backend/app/policy/POLICY.yaml, backend/tests/unit/test_policy.py.
**Produces:** deterministic priority/risk and approval decisions with structured Reasons.
**Steps:**
- [ ] Make POLICY authoritative; reject invalid policy for writes.
- [ ] Require financial decision intent plus >= 100000 minor units PLN.
- [ ] Escalate every HIGH-priority meeting mutation, including agenda edits.
- [ ] Triage new meetings against proposed content and notification impact.
- [ ] Enforce MEDIUM bound voice confirmation and HIGH UI-only confirmation.
- [ ] Bind proposal expiry, revision, arguments digest and policy version.
**Done:** LLM, USER/ORG or Focus cannot downgrade risk; no mutating adapter call occurs here.
**Tests:** 99999/100000/100001; amount without intent; HIGH agenda/reschedule + voice denied; ordinary create requires approval; expired/changed proposal; invalid policy fails closed.
**Run:** python -m pytest backend/tests/unit/test_policy.py -q

## A04 - Registry, guarded executor and all Calendar writes (target 4-7h)

**Dependencies:** A01-A03; B can consume a registry stub until ready.
**Own:** backend/app/agent/{tool_registry,tool_executor}.py, backend/app/api/actions.py, mutation portions of backend/app/google/calendar.py and api/calendar.py, backend/tests/integration/test_actions.py.
**Produces:** read-tool dispatch; safe propose/confirm flow; create/reschedule/agenda mutations with guarded read-back and unknown-outcome handling. Automated recovery follows in A07.
**Steps:**
- [ ] Register explicit schemas, risk floors, effects and scopes for all allowed tools.
- [ ] Validate every call through policy; never register confirm-approval or financial-commitment as model tools.
- [ ] Claim an approved action atomically and recheck receipt, resource, scopes and current policy.
- [ ] Create meetings with a persisted Google-valid client event ID for reconciliation.
- [ ] Apply reschedule and agenda patches with ETag; preserve unrelated data.
- [ ] Include notification behavior in proposal digest and impact.
- [ ] On timeout, persist unknown and block replay until deliberate resource read-back resolves it; on 412 supersede. Defer automated background restart reconciliation to A07.
- [ ] Make public endpoints call the executor; add a test spy proving denied paths never call Google. Wire B03 routes in main.py as delivered, before G2.
- [ ] Keep a local_write registration boundary for B04 decision.record_outcome; HIGH decision records use explicit UI receipts. This tool never invokes external financial APIs.
**Done:** all three mutation types pass mock integration and human real-account acceptance. Duplicate submissions return the same action state.
**Tests:** HIGH voice rejection, replay/double-click, tampered args, expiry, 403/412, timeout after server success with no blind retry, changed policy, preserve description, recurring-series rejection.
**Run:** python -m pytest backend/tests/integration/test_actions.py -q
**Handoff:** registry names, executor signature, action response/event schemas to B.

## A05 - Audio normalization and STT (target 7-8h)

**Dependencies:** A00; human model downloads and microphone recording samples.
**Own:** backend/app/voice/{audio,stt_base,faster_whisper,whisper_cpp,language}.py, backend/app/api/voice.py, backend/tests/integration/test_voice.py, licensed/self-recorded audio fixtures.
**Produces:** multipart upload -> normalized audio -> Transcript.
**Steps:**
- [ ] Enforce 10 MB/30 s bounds and supported MIME decoding.
- [ ] Normalize once to 16 kHz mono PCM WAV for both providers.
- [ ] Run inference off event loop with bounded concurrency.
- [ ] Implement and warm faster-whisper and whisper.cpp adapter health/fallback.
- [ ] Return language and elapsed time; retain prior conversation language for ambiguous short approvals.
**Done:** real EN/PL samples work; unavailable fallback is reported, not silently simulated.
**Tests:** silence, corrupt audio, too large/long, EN/PL, primary failure, fallback unavailable, event-loop responsiveness.
**Run:** python -m pytest backend/tests/integration/test_voice.py -q
**Handoff:** multipart field name, MIME policy, errors and transcript schema to B.
**Scheduling note:** prepare model downloads during H00 so this hour is not spent downloading.

## A06 - Gmail normalization, deduplication and Attention rules (target 8-10h)

**Dependencies:** A00/A01/A02/A03; shared ingestion signatures frozen in A00. B04 completion is not a prerequisite.
**Own:** backend/app/attention/{ingest,rules}.py, backend/tests/unit/test_attention_rules.py, backend/tests/integration/test_ingestion.py.
**Produces:** poll_recent()/check_now shared ingestion; deterministic classifications and Reason evidence.
**Steps:**
- [ ] Implement overlap/pagination/cursor logic and initial-backlog baseline.
- [ ] Preserve actual source IDs, sender address and current-message evidence.
- [ ] Extract supported PLN formats without interpreting quoted old amounts as a new decision.
- [ ] Encode rule precedence and deterministic minimum priority.
- [ ] Pass normalized events to B's AttentionEngine through the frozen interface; wire B04 routes/scheduler when available, before G3. Supply deduplicated session-item queries for Focus completion counts.
**Done:** repeated polls do not duplicate items; rules pass regardless of LLM availability.
**Tests:** 12,400 PLN / 12 400 PLN / Polish decimal examples; decision intent absent; spoofed display name; duplicate poll; late arrival; pagination; initial backlog.
**Run:** python -m pytest backend/tests/unit/test_attention_rules.py backend/tests/integration/test_ingestion.py -q

## A07 - Deep recovery, durable outbox and controlled reset (target 12-16h, after G3)

**Dependencies:** A04/A06, B04 and G3. Uses A00 outbox/event contracts; does not wait for B05 completion. A07 and B05 implement producer/consumer hardening against the shared interface.
**Own:** backend/app/main.py, dependencies.py, backend/app/db/{session,schema,repositories}.py, scripts/demo_reset.py, backend/tests/integration/test_recovery.py, docs/backend-operations.md.
**Steps:**
- [ ] Review the already integrated B routers/services; incremental wiring occurred in A04/A06 rather than waiting for this task.
- [ ] Add durable transactional outbox delivery/recovery and the deeper restart reconciliation worker.
- [ ] Ensure one scheduler/worker and short DB transactions.
- [ ] Exercise crash windows around external writes and local outcome receipts; preserve mutation cache invalidation already required by A04/B03.
- [ ] Add allowlisted reset for demo-owned IDs; baseline Gmail without deleting messages.
- [ ] Document token refresh, reauthorization, model warmup and local-only outage recovery.
**Done:** full app restarts without duplicate actions/notifications; reset cannot touch arbitrary resources.
**Tests:** restart during write, expired focus, stale cache, out-of-allowlist reset rejected, credential redaction, denied origin/session.
**Run:** python -m pytest backend/tests/integration/test_recovery.py -q

## A08 - Final backend acceptance (target 16-20h; fixes only after 20h)

**Dependencies:** all A tasks, integrated B.
**Own:** backend defect fixes within existing ownership, docs/backend-acceptance.md.
- [ ] Run complete deterministic backend suite.
- [ ] Human verifies create/reschedule/agenda against final account.
- [ ] Review all mutating call sites for executor enforcement.
- [ ] Verify mandatory acceptance passes with all cloud settings disabled and no cloud keys. If the separate optional extension exists, verify explicit cloud routing and both privacy gates; never treat its absence as failure.
- [ ] Supply evidence for three complete demo passes and known limitations.
**Done:** no mandatory failures or unverified claims.
**Run:** python -m pytest backend/tests -q
**Do not claim:** a fixture-only run proves Google, Tailscale, microphone or actual-model performance.

## Initial prompt for Stream A - Core Platform

Read EVA_IMPLEMENTATION_PLAN.md version 2.0 and this work plan. You are Stream A (Core Platform) only.
Begin with A00 after repository setup; do not implement all tasks at once.
Use the verified server model ID for Qwen/Unsloth development. Mandatory EVA runtime
is self-hosted; optional explicit cloud support is a separate later feature, not a
recovery path or acceptance dependency.
Use the canonical schema and the assigned files. Do not implement B's provider,
frontend or orchestration modules. Write the specified focused tests, implement
the smallest complete task, and return changed files, tests, commit SHA, and
any interface questions. Never publish credentials or real Workspace content.
