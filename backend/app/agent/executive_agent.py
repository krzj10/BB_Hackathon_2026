"""EVA's bounded executive agent loop (B03).

Hard boundaries enforced in code (the prompt only reinforces them):

- AT MOST 4 model rounds per request; the loop never re-enters after the cap.
- Tools come exclusively from the frozen ToolRegistry - unknown names fail
  closed and the adapter additionally refuses anything that was not offered.
- READ tools execute directly (no capability gain: the registry marks them).
- MUTATIONS never execute from this loop. Calendar mutations produce a
  ProposedAction through the A03/A04 path (policy-derived risk, idempotent
  slot, human confirmation); focus start/stop are the plan's explicit LOW-risk
  local commands and may complete immediately. decision.record_outcome is NOT
  offered at all: accept/reject is user-initiated only.
- No approval tool exists anywhere; the agent structurally cannot approve,
  lower risk, fabricate ids/etags (arguments are re-validated by the engine)
  or bypass the ToolExecutor.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from ..approvals.engine import (
    ActionPolicyError,
    ProposalContext,
    arguments_digest,
    canonical_arguments,
)
from ..contracts.domain import (
    ActiveContext,
    AssistantRequest,
    ProposedAction,
    ToolCall,
    ToolEffect,
    ToolError,
    ToolResult,
    ToolResultStatus,
)
from ..contracts.providers import ChatMessage, ChatRole
from .context_builder import ContextBundle
from .prompts import system_prompt

logger = logging.getLogger("eva.agent.executive")

#: Model rounds per request (plan B03: max 4).
MAX_MODEL_ROUNDS = 4

#: focus.start/stop are explicit LOW-risk local commands allowed to complete.
_FOCUS_COMMANDS = frozenset({"focus.start", "focus.stop"})


class AssistantUnavailableError(Exception):
    """No usable self-hosted inference route; the caller answers honestly."""


@dataclass
class AgentResult:
    reply_text: str
    active_context: ActiveContext | None = None
    proposed_action: ProposedAction | None = None
    tool_results: list[ToolResult] = field(default_factory=list)


class ExecutiveAgent:
    def __init__(
        self,
        *,
        llm_router_provider: Callable[[], object],
        registry,
        approval_engine,
        action_repo,
        executor,
        clock: Callable[[], datetime],
    ) -> None:
        # Provider (not a captured instance): PUT /api/settings/llm rebuilds
        # app.state.llm_router at runtime; the agent must always see the live
        # route - never a stale one.
        self._router = llm_router_provider
        self._registry = registry
        self._engine = approval_engine
        self._repo = action_repo
        self._executor = executor
        self._clock = clock

    # ------------------------------------------------------------------ #
    def _tool_definitions(self):
        return [
            self._registry.get(name).definition
            for name in sorted(self._registry.names())
            if name not in ("decision.record_outcome",)  # user-initiated only
        ]

    async def handle(
        self, request: AssistantRequest, context: ContextBundle | None = None
    ) -> AgentResult:
        router = self._router()
        if getattr(router, "primary", None) is None and (
            getattr(router, "fallback", None) is None
        ):
            raise AssistantUnavailableError("self-hosted inference route not configured")

        messages = [
            ChatMessage(
                role=ChatRole.SYSTEM,
                content=system_prompt(request.language, context.text if context else ""),
            ),
            ChatMessage(role=ChatRole.USER, content=request.text),
        ]
        definitions = self._tool_definitions()
        result = AgentResult(reply_text="", active_context=request.active_context)

        for round_no in range(MAX_MODEL_ROUNDS):
            response = await router.chat(messages, tools=definitions)
            if not response.tool_calls:
                result.reply_text = (response.text or "").strip()
                return result

            messages.append(
                ChatMessage(
                    role=ChatRole.ASSISTANT,
                    content=response.text,
                    tool_calls=list(response.tool_calls),
                )
            )
            for call in response.tool_calls:
                tool_result, action = await self._dispatch(call, request, round_no)
                result.tool_results.append(tool_result)
                if action is not None:
                    result.proposed_action = action
                messages.append(
                    ChatMessage(
                        role=ChatRole.TOOL,
                        tool_call_id=call.id,
                        content=tool_result.model_dump_json(),
                    )
                )

        # Round budget exhausted WITHOUT a fifth model call: honest wrap-up.
        result.reply_text = (
            "Osiągnąłem limit kroków rozumowania dla tej prośby; nic nie zostało "
            "zatwierdzone ani wykonane automatycznie."
            if request.language.value == "pl"
            else (
                "I reached the reasoning-step limit for this request; nothing was "
                "approved or executed automatically."
            )
        )
        return result

    # ------------------------------------------------------------------ #
    async def _dispatch(
        self, call: ToolCall, request: AssistantRequest, round_no: int
    ) -> tuple[ToolResult, ProposedAction | None]:
        try:
            entry = self._registry.get(call.name)
        except Exception:  # RegistryError and anything else: fail closed
            return (
                self._error_result(
                    call, "unknown_tool", "tool is not registered; nothing was executed"
                ),
                None,
            )

        if entry.effect is ToolEffect.READ:
            try:
                tool_result = await asyncio.to_thread(self._executor.execute_read, call)
            except Exception as exc:  # executor already sanitizes; belt and braces
                logger.error("agent read dispatch failed (%s)", type(exc).__name__)
                tool_result = self._error_result(
                    call, "read_failed", "read could not be completed"
                )
            return tool_result, None

        if entry.effect is ToolEffect.EXTERNAL_WRITE:
            # Calendar mutations: proposal ONLY - a human confirms in the UI.
            action, err = self._propose(call, request, round_no, execute=False)
            if err is not None:
                return err, None
            return self._proposal_result(call, action), action

        # LOCAL_WRITE: only the explicit focus commands reach here (decision
        # outcome is not offered to the agent). LOW-risk per policy.
        if call.name in _FOCUS_COMMANDS:
            action, err = self._propose(call, request, round_no, execute=True)
            if err is not None:
                return err, None
            last = self._repo.get_last_result(action.id)
            payload = (last.data if last and last.data else {}) or {}
            return (
                ToolResult(
                    call_id=call.id,
                    tool=call.name,
                    status=(
                        ToolResultStatus.OK
                        if action.status.value == "succeeded"
                        else ToolResultStatus.ERROR
                    ),
                    data=payload,
                    error=None
                    if action.status.value == "succeeded"
                    else ToolError(
                        code="focus_command_failed", message="focus command did not apply"
                    ),
                    duration_ms=0,
                ),
                None,
            )

        return (
            self._error_result(call, "tool_not_offered", "this tool is not usable here"),
            None,
        )

    # ------------------------------------------------------------------ #
    def _propose(
        self, call: ToolCall, request: AssistantRequest, round_no: int, *, execute: bool
    ) -> tuple[ProposedAction | None, ToolResult | None]:
        """Canonical A04 proposal path with a per-call idempotency slot."""
        try:
            validated = self._engine.validate_arguments(call.name, call.arguments)
        except ActionPolicyError as exc:
            return None, self._error_result(call, exc.code, "arguments rejected by policy")
        digest = arguments_digest(canonical_arguments(validated))
        slot_request_id = f"{request.request_id}:r{round_no}:{call.id}"

        existing = self._repo.get_idempotent_proposal(request.session_id, slot_request_id)
        if existing is not None:
            existing_digest, existing_action_id = existing
            if existing_digest != digest:
                return None, self._error_result(
                    call, "request_replay_mismatch", "slot already holds a different proposal"
                )
            action = self._repo.get_action(existing_action_id)
            if action is not None:
                return action, None

        try:
            proposed = self._engine.propose(
                call,
                ProposalContext(
                    session_id=request.session_id,
                    request_id=slot_request_id,
                    now=self._clock(),
                ),
                persist=False,
            )
        except ActionPolicyError as exc:
            return None, self._error_result(call, exc.code, "proposal refused by policy")

        outcome = self._repo.create_idempotent_action(
            proposed,
            session_id=request.session_id,
            request_id=slot_request_id,
            arguments_digest=digest,
        )
        if outcome.status == "conflict":
            return None, self._error_result(
                call, "request_replay_mismatch", "slot already holds a different proposal"
            )
        action = self._repo.get_action(outcome.action_id)
        if action is None:
            return None, self._error_result(call, "internal", "proposal state inconsistent")

        if execute and not action.requires_approval:
            executed = self._executor.execute(action.id)
            action = executed.action or action
        # NOTE: EXTERNAL_WRITE proposals are NEVER executed from this path,
        # regardless of the approval flag - human confirmation is mandatory.
        return action, None

    # ------------------------------------------------------------------ #
    def _proposal_result(self, call: ToolCall, action: ProposedAction) -> ToolResult:
        awaiting = action.status.value == "pending"
        return ToolResult(
            call_id=call.id,
            tool=call.name,
            status=ToolResultStatus.OK,
            data={
                "action_id": action.id,
                "status": action.status.value,
                "risk": action.risk.value,
                "requires_user_confirmation": awaiting,
                "summary": action.summary,
            },
            duration_ms=0,
        )

    def _error_result(self, call: ToolCall, code: str, message: str) -> ToolResult:
        return ToolResult(
            call_id=call.id,
            tool=call.name,
            status=ToolResultStatus.ERROR,
            error=ToolError(code=code, message=message),
            duration_ms=0,
        )
