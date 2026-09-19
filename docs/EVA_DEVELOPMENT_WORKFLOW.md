# EVA Version 2.0 - Two-Stream Development Workflow

**Date:** 2026-09-19 - version 2.0 final architecture correction
**Purpose:** coordinate two coding clients around one repository, one contract and one integration branch.
**Related:** EVA_IMPLEMENTATION_PLAN.md, EVA_WORK_PLAN_CORE.md, EVA_WORK_PLAN_EXPERIENCE.md.
**Status:** setup instructions only; no repository, account, client or service has been created by this planning update.

## 1. Recommended arrangement

Use one private GitHub repository. It is strongly recommended for shared history, review and backup, but two local worktrees can cooperate without GitHub. GitHub does not make the models coordinate automatically.

Run two coding-agent clients on the developer laptop:
- Client A -> Unsloth Studio over Tailscale -> verified Qwen serving model -> Core Platform worktree. Expected demo family: Qwen 3.8 Next / Fast / Flash; use the actual server-discovered/configured ID.
- Client B -> OpenRouter -> verified coding model -> Experience Layer worktree.

Both clients need filesystem editing, shell/test execution, Git support and configurable model endpoints. A chat window or model API endpoint alone cannot own a checkout or run tests. Pick clients that actually support the selected endpoint/tool protocol; validate with a tiny file/test task before dispatching application work. Existing compatible clients are sufficient; no client migration is required by the architecture.

Application runtime is a separate connection:
- EVA FastAPI -> self-hosted Unsloth/private fallback.
- EVA FastAPI -> Google Calendar/Gmail.
- Mandatory golden path: EVA -> self-hosted OpenAI-compatible inference only; no public-cloud LLM dependency.
- Optional later feature: EVA -> explicitly selected/configured cloud API only with runtime opt-in. Cloud inference and Workspace-cloud inference both default to disabled; never silent recovery.

Coding credentials/configuration and runtime credentials/configuration are separate. The Experience Layer coding agent may write/test the self-hosted inference adapter without making the app use OpenRouter. Optional runtime cloud support, if implemented, requires separate application settings/credentials and never inherits development authorization. Unsloth is the demo primary; the provider interface also permits verified self-hosted vLLM, LM Studio, Ollama-compatible and llama.cpp endpoints.

## 2. Decisions already settled

- HIGH meeting changes: UI confirmation.
- Full golden path mandatory; create/reschedule/agenda all mandatory.
- Financial evidence plus internal RECORD ACCEPT/RECORD REJECT/DEFER/ASK EVA. Recording a HIGH financial decision uses explicit UI and an audited local write; no payment, purchase, supplier or contract commitment.
- Controlled Google account and real incoming messages.
- Focus duration/threshold/expiring exact sender exceptions and on-screen completion summary. Exceptions affect delivery only, never action authorization.
- Local browser/frontend/backend; private inference may be on another tailnet machine.
- Self-hosted model substitution allowed when verified.
- OpenRouter is expected for Stream B development; optional runtime cloud support is a distinct, default-off extension, never a golden-path dependency.
- Stream B builds React/TypeScript + Tailwind CSS + shadcn/ui directly, with A-generated types, typed mocks first and FastAPI REST/WebSocket afterward. No frontend export or human-generated frontend handoff.

Remaining SETUP INPUTS, not architecture redesign:
1. Actual Unsloth base URL, API authentication and served model ID.
2. Exact available OpenRouter development model ID and selected coding client.
3. GitHub owner/repository and authentication on coding hosts.
4. Controlled Google accounts, client ID/callback, sender addresses.
5. Demo laptop CPU/GPU/RAM and whether a second local inference fallback fits.

Record secret-free facts in docs/demo-environment.md when implementation starts. Keep secrets in client/OS secret stores.

## 3. Human setup checklist before heavy coding

- [ ] Create an empty PRIVATE GitHub repo, for example eva-hackathon, without a second generated README/history.
- [ ] Initialize the existing workspace once and commit the version 2.0 planning documents.
- [ ] Add .gitignore BEFORE credentials, databases, caches or model files enter the workspace.
- [ ] Authenticate Git on the coding host; no personal access token in prompts or remote URLs.
- [ ] Set up two worktrees and coding clients.
- [ ] Establish Tailscale connectivity and grant only the required inference service/port.
- [ ] Verify Unsloth requires authentication and EVA runtime built-in tools are disabled.
- [ ] Configure Client A's model from actual server discovery/manual verified ID.
- [ ] Configure Client B from the current OpenRouter catalog; do not assume the user-supplied candidate label is an API ID.
- [ ] Create Google Cloud project/client and verify real OAuth early.
- [ ] Prepare primary/fallback STT model downloads.
- [ ] Arrange a second controlled sender account.
- [ ] Run tiny client tooling checks, then dispatch A00 and B00.

Free OpenRouter models may be rate-limited or unavailable. Choose a tested coding model and a DEVELOPMENT fallback. If a coding model stops working, change the client model and resume the same task/branch; do not restart architecture or enable optional runtime cloud inference as a development workaround.

New Google account creation may require human verification. The human completes it. If final-account creation waits until later, early real OAuth must use another authorized controlled account. Re-run scopes, ownership, seeds and the complete demo on the final account before hour 20.

## 4. Git initialization example - human runs once

The existing workspace has planning files and no observed .git directory. Inspect before running; if it is already a repository, skip initialization and reuse its history.

From the workspace, after creating .gitignore:
~~~powershell
git init -b main
git add .gitignore EVA_IMPLEMENTATION_PLAN.md EVA_IMPLEMENTATION_PLAN_v1.0.md EVA_WORK_PLAN_CORE.md EVA_WORK_PLAN_EXPERIENCE.md EVA_DEVELOPMENT_WORKFLOW.md
git commit -m "docs: define EVA version 2.0 and two-stream delivery"
~~~

Add the actual private repository remote using GitHub's displayed HTTPS or SSH URL, then:
~~~powershell
git push -u origin main
git switch -c integration
git push -u origin integration
git worktree add ..\eva-core -b work/core integration
git worktree add ..\eva-experience -b work/experience integration
~~~

The remote-add command is intentionally not prefilled with an invented account/repository. The user supplies the real URL. Verify sibling directory names are unused before creating worktrees.

Suggested ignore entries:
~~~gitignore
.env
.env.*
!.env.example
*.db
*.db-*
*.sqlite
*.sqlite-*
*.sqlite3
*.sqlite3-*
credentials*.json
client_secret*.json
token*.json
secrets/
private-data/
recordings/
models/
.venv/
__pycache__/
.pytest_cache/
node_modules/
dist/
.next/
playwright-report/
test-results/
~~~

Ignore rules are not access control. Keep real secrets/data OUTSIDE coding-agent worktrees, especially the OpenRouter client's accessible project context. Use a separate human-run demo environment with runtime credentials. Only synthetic fixtures and source code need to enter development model context.

Do not git add the entire home directory or put runtime keys into screenshots, issue bodies or commit messages. A reviewer inspects git diff --cached before the first push.

## 5. Worktree and branch policy

| Location | Branch | Writer |
|---|---|---|
| Original workspace | integration | Human/integration reviewer |
| ../eva-core | work/core | Core Platform (Stream A) |
| ../eva-experience | work/experience | Experience Layer (Stream B) |
| Release history | main | Human merges passing integration |

Do not run two writers in one worktree. Do not check out the same branch in two worktrees. If coding clients run on different computers, use separate clones instead; push/fetch shared commits through GitHub. Tailscale inference access does not synchronize source code.

Worktrees share Git history but have separate working files; keep separate dependency environments and runtime databases. Use separate test ports or serialize live integration tests. Human owns the actual demo backend port 8000.

## 6. Contract-first cooperation

1. A00 publishes the canonical schemas, fixtures, provider/service signatures and generated TS.
2. B reviews the contract commit. Human merges it into integration.
3. B merges current integration into its branch before typed B02A fixtures and B01/B02B integration. Visual shell/theme work may precede A00; frontend domain models must not.
4. Subsequent API changes start as a short contract-change request with producer, consumer and test impact.
5. A updates schema/types; B updates consumers against the same commit.
6. Generated frontend types are never edited by B.
7. B hands router/dependency registration instructions to A; A changes main.py.
8. Human/Codex reviews before integration. No automatic approval of a task merely because a model reports success.

Shared-file owners:
- Backend manifest/lockfile, canonical contracts, generated types, main.py: A.
- Frontend manifest/lockfile and directly authored React/TypeScript + Tailwind CSS + shadcn/ui source: B.
- Architecture, scope changes and integration decisions: HUMAN/CODEX_REVIEW.
- POLICY.yaml executable rules: A, reviewed by CODEX_REVIEW.
- USER.md/ORG.md factual inputs: HUMAN, consumed by both.

## 7. Integration loop every 60-90 minutes or completed slice

Each stream:
1. Complete one bounded task and focused tests.
2. Commit only owned files.
3. Push its branch; provide task ID, SHA, changed interfaces and exact test results.
4. Open/update a draft PR to integration if using GitHub review, or request local review.
5. Human checks diff, tests, contract drift and scope.
6. Human merges only accepted changes into integration.
7. Other stream merges updated integration before dependent work.
8. Main receives only a green milestone.

Prefer ordinary merges while two long-lived stream branches are active. Avoid force-pushing shared branches or both cherry-picking and merging the same changes. If a conflict crosses ownership, the module owner resolves it with the consumer's input; do not accept one entire side blindly.

For a local integration merge (after reviewing and fetching if needed):
~~~powershell
git switch integration
git merge --no-ff work/core
~~~
Then run the affected checks before merging the next slice. The equivalent PR target is integration, not main.

## 8. Dispatch order

First hour:
- HUMAN: repository, clients, Tailscale, Google account/Cloud setup.
- A: A00 schema and backend foundation.
- B: B00 time-boxed endpoint/tooling probe (0-0.5h), schema feedback, then B02A visual foundation. If live setup is unavailable, record it and continue independent shell/theme work; finish the live probe by G1. No competing contracts.

Hours 1-4:
- A: A01 minimal persistence -> A02 -> A03.
- B: finish B02A foundation (0.5-2h), B01 self-hosted adapter (2-3h), start B02B core screens (3-5h), using generated types and mocks. One sequential stream, no assumed extra worker.
- G1: real Google reads, private inference probe, policy tests and a visually reviewed dark/light Today/Voice shell.

Hours 4-8:
- A: A04 safe Calendar mutations -> A05 STT; wire B routes incrementally before G2.
- B: complete B02B by hour 5, then B03 (5-8h) real briefing/agent, basic live events and STT hookup.
- G2: main path steps 1-12 plus create/reschedule live checks.

Hours 8-12:
- A: A06 normalization/rules; wire B04 services before G3.
- B: B04 Attention/Focus/Decision, completion summary and local decision-outcome recording.
- G3: all 19 steps with actual incoming Gmail.

Later:
- A07/B05 deepen restart reconciliation, durable outbox and reconnect recovery after G3, against the A00-frozen interfaces. Neither waits for the other to finish; integrate both by G4.
- A08/B06 acceptance, final visual refinement and rehearsal; B06 is not the first design pass.
- Optional cloud B07 is unallocated and may start only after stable G5 if it does not delay rehearsal/freeze. Missing cloud support is not a blocker; preserve Settings/interface design.
- No optional integrations before G3. Freeze at hour 20.

FIRST make the full flow correct and safe; THEN deepen rare-path recovery. HIGH UI approval, digests/expiry/receipts, ETags, idempotent claiming and uncertain-write replay blocking are required before any real mutation. Basic events and incremental composition cannot be postponed to A07/B05.

## 9. Standard coding-task envelope

~~~text
Task ID:
Authoritative spec: EVA_IMPLEMENTATION_PLAN.md version 2.0
Stream:
Base contract/integration commit:
Objective:
Allowed files:
Read-only dependencies:
Required interface signatures:
Acceptance cases:
Commands to run:
Forbidden changes:
Return: commit SHA, changed files, exact test results, unresolved blockers.
Stop after this task; do not invent or implement adjacent features.
~~~

Use the starter prompts in each work plan and include only the next task. Do not ask either stream to build "the entire backend" or "the entire frontend and agent" in one run.

## 10. Milestone verification commands

After A00/B02A define packaging/scripts:
~~~text
python -m pytest backend/tests -q
python scripts/export_contracts.py --check
npm --prefix frontend run typecheck
npm --prefix frontend test -- --run
npm --prefix frontend run build
npm --prefix frontend run test:e2e
~~~

Human live checks separately validate actual Google permissions, private inference, EN/PL microphone/TTS, real mail arrivals and create/reschedule/agenda. Fixture E2E is not a live-network test. Mandatory acceptance runs with cloud disabled and no cloud keys; also verify local financial outcome recording and Focus completion summary. If B07 exists, test its opt-in/privacy/explicit-route boundaries separately without adding it to MVP acceptance.

Do not run a write smoke test against an arbitrary account. Use explicitly identified demo-owned resources and approved proposals. Calendar reset is a narrow human maintenance operation.

## 11. Settings handoff and cloud extension boundary

Mandatory AI Engine: self-hosted Base URL, API key if required, actual Model ID, Detect Models, Test Connection, Health and latency when available.
Optional: Disabled/OpenRouter/Custom cloud provider, optional key/model/connection test, Allow cloud inference=false, Allow Workspace context to cloud=false. OpenAI may use the optional custom-compatible route.
No optional field/key is required for startup. If omitted, keep the design and provider interface; do not implement a fake connection or treat omission as a blocker.
Explicit cloud routing requires user opt-in; Workspace-derived snippets, summaries, history and tool results additionally require Workspace-cloud permission, checked before every outbound call. A runtime outage never grants consent or silently reroutes. See implementation plan section 2.1.1.

## 12. Practical first action

Prepare the private repository and two coding clients, verify Unsloth from the demo laptop, and test Google OAuth before broad implementation. Then dispatch A00 and B00. Codex remains architecture/integration reviewer unless explicitly assigned a specific repair.

References:
- [Git worktree semantics](https://git-scm.com/docs/git-worktree)
- [Unsloth API](https://unsloth.ai/docs/basics/api)
- [Tailscale grants](https://tailscale.com/docs/features/access-control/grants)
- [OpenRouter model catalog](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties)
