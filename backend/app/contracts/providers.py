"""EVA canonical provider contracts (Stream A - Core Platform).

Provider interfaces are backend-only. The frontend never imports these and
receives only sanitized settings/health responses (see contracts/api.py).

Signatures are frozen by A00 per docs/EVA_IMPLEMENTATION_PLAN.md v2.0 section 4.
P0 uses non-streaming structured Chat Completions; any future streaming method
requires a separate explicit event type and a coordinated contract change.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import AwareDatetime, Field

from .domain import (
    AudioInput,
    ContractModel,
    HealthStatus,
    ToolCall,
    ToolDefinition,
    Transcript,
)


class ChatRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ChatMessage(ContractModel):
    role: ChatRole
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None


class LLMResponse(ContractModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1, description="actual serving model id")
    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    structured: dict[str, Any] | None = None
    finish_reason: str = Field(min_length=1)


class LLMModelInfo(ContractModel):
    id: str = Field(min_length=1, description="actual id exposed by the running server")
    display_name: str = Field(min_length=1)
    supports_tools: bool | None = None
    supports_structured_output: bool | None = None
    context_window: int | None = Field(default=None, gt=0)


class ProviderHealth(ContractModel):
    """Component readiness with structured identity where known.

    ``provider``/``model`` carry the actual configured provider and serving
    model id (never guessed). Secrets, API keys, tokens and base-URL
    credentials must never appear here - only presence flags belong in
    sanitized responses elsewhere.
    """

    status: HealthStatus
    detail: str | None = None
    provider: str | None = None
    model: str | None = None


class SearchResult(ContractModel):
    title: str
    url: str
    snippet: str
    retrieved_at: AwareDatetime


@runtime_checkable
class LLMProvider(Protocol):
    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        response_schema: dict | None = None,
    ) -> LLMResponse: ...

    async def list_models(self) -> list[LLMModelInfo]: ...

    async def health(self) -> ProviderHealth: ...


@runtime_checkable
class SpeechToTextProvider(Protocol):
    async def transcribe(
        self, audio: AudioInput, language: str | None = None
    ) -> Transcript: ...

    async def health(self) -> ProviderHealth: ...


@runtime_checkable
class WebSearchProvider(Protocol):
    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]: ...
