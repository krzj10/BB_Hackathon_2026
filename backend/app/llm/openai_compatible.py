"""OpenAI-compatible adapter for EVA's MANDATORY self-hosted inference route (B01).

Route invariant (fail closed, checked twice): the constructor refuses a base URL
whose exact origin is not on the allowlist it is given, and every request
re-checks the final URL before a single byte leaves the process. The allowlist is
held by reference, so revoking an origin at runtime immediately stops subsequent
outbound calls. Redirects are never followed - a 3xx is a route change EVA did
not approve, so it is reported as unavailable instead of retried elsewhere. This
class contains no fallback, no retry and no cloud path: routing belongs to
``llm/router.py``, and a private-provider failure is reported, never "recovered"
by sending Workspace content somewhere else.

Identity invariant: requests always carry the configured model id (never an
invented or guessed one). When the endpoint reports the id it actually served,
that value is returned to callers; otherwise the configured id is.

Secret hygiene: the API key exists only inside the per-request ``Authorization``
header built below. Error messages and logs carry EVA-authored text, stable
codes, HTTP status classes and counts only - never keys, header values, URLs or
provider response bodies (a completion may be private Workspace content).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import ValidationError

from ..contracts.domain import HealthStatus, ToolCall, ToolDefinition
from ..contracts.providers import (
    ChatMessage,
    ChatRole,
    LLMModelInfo,
    LLMResponse,
    ProviderHealth,
)
from .base import (
    LLMError,
    LLMNotConfiguredError,
    LLMProtocolError,
    LLMTimeoutError,
    LLMUnavailableError,
    canonical_tool_name,
    require_allowed_self_hosted_origin,
    wire_tool_name,
)

logger = logging.getLogger("eva.llm.openai_compatible")

#: Frozen provider identity for the mandatory self-hosted route.
PROVIDER_NAME = "openai_compatible"

DEFAULT_TIMEOUT_SECONDS = 45.0
#: Health/model-discovery probes are cheap and must not hold a request slot for
#: the full chat budget.
HEALTH_TIMEOUT_SECONDS = 8.0

_REPAIR_INSTRUCTION = (
    "Your previous reply was not valid JSON matching the required schema. "
    "Reply with ONLY the corrected JSON object: no prose, no code fences."
)


class _Deadline:
    """Monotonic whole-call budget shared by the initial request and the single
    repair round, so ``timeout_seconds`` bounds the WHOLE chat call."""

    def __init__(self, seconds: float) -> None:
        self._loop = asyncio.get_running_loop()
        self._expires_at = self._loop.time() + seconds

    def remaining(self) -> float:
        return self._expires_at - self._loop.time()


class OpenAICompatibleProvider:
    """Non-streaming Chat Completions client for one allowlisted self-hosted server."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        allowed_origins: Sequence[str],
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
        health_timeout_seconds: float = HEALTH_TIMEOUT_SECONDS,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if health_timeout_seconds <= 0:
            raise ValueError("health_timeout_seconds must be > 0")
        # Construction-time refusal: an unapproved destination is never even
        # representable as a provider object.
        require_allowed_self_hosted_origin(base_url, allowed_origins)
        if not model.strip():
            raise LLMNotConfiguredError("self-hosted model id is not configured")
        self._allowed_origins = allowed_origins
        self._base_url = base_url.strip().rstrip("/")
        self._model = model.strip()
        # The key is held only to build the Authorization header; it is never
        # formatted into a message, log line or health detail.
        self._api_key = (api_key or "").strip()
        self._timeout_seconds = timeout_seconds
        self._health_timeout_seconds = min(health_timeout_seconds, timeout_seconds)
        self._transport = transport

    # ------------------------------------------------------------------ #
    # Sanitized introspection (route identity only - never the key)
    # ------------------------------------------------------------------ #

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    # ------------------------------------------------------------------ #
    # LLMProvider protocol
    # ------------------------------------------------------------------ #

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        response_schema: dict | None = None,
    ) -> LLMResponse:
        if not messages:
            raise LLMProtocolError("chat requires at least one message")
        offered = {tool.name for tool in tools} if tools else set()
        budget = _Deadline(self._timeout_seconds)
        payload = self._chat_payload(messages, tools=tools, response_schema=response_schema)
        data = await self._json_request("POST", "/chat/completions", budget=budget, body=payload)
        response = self._normalize_chat(data, offered_tool_names=offered)
        if response_schema is None:
            return response

        structured, problem = _match_schema_shape(response.text, response_schema)
        if problem is None:
            return response.model_copy(update={"structured": structured})

        # AT MOST ONE repair round. A second malformed answer is a hard protocol
        # failure: repeated repair attempts would multiply latency and cost
        # without evidence that this endpoint can produce the shape.
        logger.warning("structured output rejected; running the single repair round")
        history = [
            *messages,
            ChatMessage(role=ChatRole.ASSISTANT, content=response.text or ""),
            ChatMessage(role=ChatRole.USER, content=_REPAIR_INSTRUCTION),
        ]
        repair_payload = self._chat_payload(history, tools=None, response_schema=response_schema)
        repair_data = await self._json_request(
            "POST", "/chat/completions", budget=budget, body=repair_payload
        )
        repaired = self._normalize_chat(repair_data, offered_tool_names=set())
        structured, problem = _match_schema_shape(repaired.text, response_schema)
        if problem is not None:
            raise LLMProtocolError(
                "model output still did not match the requested JSON schema "
                "after one repair attempt"
            )
        return repaired.model_copy(update={"structured": structured})

    async def list_models(self) -> list[LLMModelInfo]:
        budget = _Deadline(self._health_timeout_seconds)
        data = await self._json_request("GET", "/models", budget=budget)
        return _parse_model_list(data)

    async def health(self) -> ProviderHealth:
        """Cheap ``GET /models`` probe with the small health budget.

        READY only when the endpoint answers AND lists the configured model id;
        a reachable server that does not serve that id is DEGRADED (honest, and
        never a silent switch to another model)."""
        try:
            models = await self.list_models()
        except LLMError as exc:
            logger.error("health probe failed (%s)", exc.code)
            return ProviderHealth(
                status=HealthStatus.UNAVAILABLE,
                detail=exc.message,
                provider=PROVIDER_NAME,
                model=self._model or None,
            )
        except Exception as exc:  # a broken probe must never break the caller
            logger.error("health probe failed (%s)", type(exc).__name__)
            return ProviderHealth(
                status=HealthStatus.UNAVAILABLE,
                detail="self-hosted health probe failed",
                provider=PROVIDER_NAME,
                model=self._model or None,
            )
        if any(info.id == self._model for info in models):
            return ProviderHealth(
                status=HealthStatus.READY,
                detail="endpoint reachable; configured model id is served",
                provider=PROVIDER_NAME,
                model=self._model,
            )
        return ProviderHealth(
            status=HealthStatus.DEGRADED,
            detail="endpoint reachable but the configured model id was not listed by /models",
            provider=PROVIDER_NAME,
            model=self._model,
        )

    # ------------------------------------------------------------------ #
    # HTTP boundary
    # ------------------------------------------------------------------ #

    async def _json_request(
        self,
        method: str,
        path: str,
        *,
        budget: _Deadline,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        # Per-call re-validation: revocation and any unexpected URL change fail
        # closed here rather than turning into an outbound request.
        require_allowed_self_hosted_origin(url, self._allowed_origins)
        remaining = budget.remaining()
        if remaining <= 0:
            raise LLMTimeoutError("self-hosted inference deadline exceeded before sending")
        timeout = httpx.Timeout(remaining, connect=min(10.0, remaining))
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        logger.debug("sending %s request to the allowlisted self-hosted endpoint", path)
        try:
            async with httpx.AsyncClient(
                timeout=timeout, transport=self._transport, follow_redirects=False
            ) as client:
                response = await asyncio.wait_for(
                    client.request(method, url, json=body, headers=headers), timeout=remaining
                )
        except httpx.TimeoutException:
            # from None: httpx exception text embeds the request URL.
            raise LLMTimeoutError("self-hosted inference request timed out") from None
        except asyncio.TimeoutError:
            raise LLMTimeoutError("self-hosted inference deadline exceeded") from None
        except httpx.HTTPError:
            raise LLMUnavailableError("self-hosted inference endpoint unreachable") from None
        if response.status_code in (301, 302, 303, 307, 308):
            # Following a redirect could move private content to an unapproved
            # origin; the self-hosted route never follows them.
            logger.error("endpoint redirected (HTTP %d); not followed", response.status_code)
            raise LLMUnavailableError(
                "self-hosted endpoint redirected; redirects are not followed on this route"
            )
        if response.status_code >= 300:
            # Status code only - provider error bodies are never read or kept.
            logger.error("endpoint returned HTTP %d", response.status_code)
            raise LLMUnavailableError(
                f"self-hosted endpoint returned HTTP {response.status_code}"
            )
        try:
            data = response.json()
        except ValueError:
            raise LLMProtocolError("endpoint returned a non-JSON response") from None
        if not isinstance(data, dict):
            raise LLMProtocolError("endpoint returned a JSON payload that is not an object")
        return data

    # ------------------------------------------------------------------ #
    # Canonical <-> OpenAI wire normalization
    # ------------------------------------------------------------------ #

    def _chat_payload(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None,
        response_schema: dict | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,  # the ACTUAL configured id - never invented
            "messages": [_serialize_message(message) for message in messages],
        }
        if tools:
            payload["tools"] = [_openai_tool_spec(tool) for tool in tools]
        if response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": _schema_name(response_schema),
                    "schema": response_schema,
                    "strict": True,
                },
            }
        return payload

    def _normalize_chat(
        self, data: dict[str, Any], *, offered_tool_names: set[str]
    ) -> LLMResponse:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMProtocolError("endpoint returned no completion choices")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise LLMProtocolError("completion choice is not an object")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise LLMProtocolError("completion choice carries no message object")
        raw_text = message.get("content")
        if raw_text is not None and not isinstance(raw_text, str):
            raise LLMProtocolError("completion content is not a string")
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise LLMProtocolError("completion tool_calls is not a list")
        tool_calls = [
            _normalize_tool_call(call, offered_tool_names=offered_tool_names)
            for call in raw_calls
        ]
        if raw_text is None and not tool_calls:
            raise LLMProtocolError("endpoint returned an empty completion")
        served_model = data.get("model")
        reported = served_model.strip() if isinstance(served_model, str) else ""
        model = reported or self._model
        finish_reason = choice.get("finish_reason")
        try:
            return LLMResponse(
                provider=PROVIDER_NAME,
                model=model,
                text=raw_text,
                tool_calls=tool_calls,
                finish_reason=str(finish_reason) if finish_reason else "unknown",
            )
        except ValidationError:
            raise LLMProtocolError(
                "completion did not satisfy the canonical response contract"
            ) from None


def _serialize_message(message: ChatMessage) -> dict[str, Any]:
    """Canonical ChatMessage -> OpenAI chat message."""
    payload: dict[str, Any] = {"role": message.role.value}
    if message.role is ChatRole.TOOL:
        if not message.tool_call_id:
            raise LLMProtocolError("tool result message without tool_call_id")
        payload["tool_call_id"] = message.tool_call_id
        payload["content"] = message.content or ""
        return payload
    if message.content is not None:
        payload["content"] = message.content
    elif message.tool_calls:
        # Assistant turn carrying calls only: content stays absent (null).
        pass
    else:
        payload["content"] = ""
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": wire_tool_name(call.name),
                    "arguments": _dump_arguments(call.arguments),
                },
            }
            for call in message.tool_calls
        ]
    return payload


def _dump_arguments(arguments: dict[str, Any]) -> str:
    try:
        return json.dumps(arguments)
    except (TypeError, ValueError):
        raise LLMProtocolError("tool call arguments are not JSON serializable") from None


def _openai_tool_spec(tool: ToolDefinition) -> dict[str, Any]:
    """Canonical ToolDefinition -> OpenAI function-calling schema.

    Only name/description/parameters cross the wire: EVA's effect, risk floor,
    required scopes and timeout are backend policy the model must never see or
    choose. ``strict`` is deliberately not asserted here - self-hosted servers
    vary, and a strictness mismatch would fail the request rather than tighten
    it (structural validation happens on the way back in)."""
    return {
        "type": "function",
        "function": {
            "name": wire_tool_name(tool.name),
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _normalize_tool_call(entry: Any, *, offered_tool_names: set[str]) -> ToolCall:
    """OpenAI tool call -> canonical ToolCall, rejecting anything malformed.

    A model cannot introduce a tool EVA did not offer, and arguments must be a
    JSON object: both are hard failures rather than best-effort coercion,
    because downstream approval binding digests exactly these arguments."""
    if not isinstance(entry, dict):
        raise LLMProtocolError("tool call entry is not an object")
    function = entry.get("function")
    if not isinstance(function, dict):
        raise LLMProtocolError("tool call carries no function object")
    raw_name = function.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise LLMProtocolError("tool call has no function name")
    name = canonical_tool_name(raw_name)
    if name not in offered_tool_names:
        raise LLMProtocolError("model called a tool that was not offered")
    raw_id = entry.get("id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise LLMProtocolError("tool call has no id")
    arguments = _parse_arguments(function.get("arguments"))
    try:
        return ToolCall(id=raw_id.strip(), name=name, arguments=arguments)
    except ValidationError:
        raise LLMProtocolError(
            "tool call did not satisfy the canonical tool-call contract"
        ) from None


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}  # servers legitimately send "" for an argument-less tool
    if isinstance(raw, dict):
        return raw  # tolerant of servers that already hand back a parsed object
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            raise LLMProtocolError("tool call arguments are not valid JSON") from None
        if not isinstance(parsed, dict):
            raise LLMProtocolError("tool call arguments are not a JSON object")
        return parsed
    raise LLMProtocolError("tool call arguments have an unsupported type")


def _parse_model_list(data: dict[str, Any]) -> list[LLMModelInfo]:
    """Map a served model listing to canonical LLMModelInfo.

    Individually unusable entries are skipped (a server may include junk), but
    a payload with no recognizable list at all is a protocol failure - never an
    empty success that would look like "no models available"."""
    raw = data.get("data")
    if not isinstance(raw, list):
        raw = data.get("models")
    if not isinstance(raw, list):
        raise LLMProtocolError("model discovery returned no model list")
    models: list[LLMModelInfo] = []
    seen: set[str] = set()
    for entry in raw:
        info = _model_info(entry)
        if info is None or info.id in seen:
            continue
        seen.add(info.id)
        models.append(info)
    return models


def _model_info(entry: Any) -> LLMModelInfo | None:
    if isinstance(entry, str):
        entry_id, fields = entry.strip(), {}
    elif isinstance(entry, dict):
        raw_id = entry.get("id") or entry.get("model") or entry.get("name")
        entry_id = raw_id.strip() if isinstance(raw_id, str) else ""
        fields = entry
    else:
        return None
    if not entry_id:
        return None
    friendly = fields.get("display_name") or fields.get("name")
    display_name = friendly.strip() if isinstance(friendly, str) and friendly.strip() else entry_id
    return LLMModelInfo(
        id=entry_id,
        display_name=display_name,
        supports_tools=_bool_or_none(fields.get("supports_tools")),
        supports_structured_output=_bool_or_none(fields.get("supports_structured_output")),
        context_window=_positive_int_or_none(
            fields.get("context_window"),
            fields.get("max_model_len"),
            fields.get("n_ctx"),
            fields.get("context_length"),
        ),
    )


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _positive_int_or_none(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def _schema_name(schema: dict) -> str:
    """OpenAI-compatible response-format name: conservative charset, bounded."""
    title = schema.get("title") if isinstance(schema, dict) else None
    candidate = "".join(
        char if char.isalnum() or char in "_-" else "_" for char in str(title or "")
    )[:64]
    return candidate or "eva_structured_response"


# --------------------------------------------------------------------------- #
# Structured-output shape check (deliberately light: no new dependencies)
# --------------------------------------------------------------------------- #


def _match_schema_shape(text: str | None, schema: dict) -> tuple[dict[str, Any] | None, str | None]:
    """Return ``(value, None)`` or ``(None, sanitized problem)`` for the JSON body
    the model produced against ``response_schema``."""
    if text is None or not text.strip():
        return None, "model returned no content"
    raw = _strip_code_fence(text.strip())
    try:
        value = json.loads(raw)
    except ValueError:
        return None, "model output was not valid JSON"
    if not isinstance(value, dict):
        return None, "model output was not a JSON object"
    problem = _check_shape(value, schema)
    return (None, problem) if problem else (value, None)


def _strip_code_fence(raw: str) -> str:
    """Drop a markdown fence some servers wrap JSON in (``` / ```json)."""
    if not raw.startswith("```"):
        return raw
    body = raw[3:]
    if body.lower().startswith("json"):
        body = body[4:]
    return body.rsplit("```", 1)[0].strip()


def _check_shape(value: Any, schema: dict) -> str | None:
    """Structural validation of ``value`` against a JSON-Schema-shaped dict.

    Covers type, required properties and recursion into declared
    properties/items - enough to prove the shape a caller asked for without
    pulling in a validator dependency. Unknown keywords are ignored."""
    if not isinstance(schema, dict):
        return None
    expected = schema.get("type")
    if expected == "object" or (expected is None and "properties" in schema):
        if not isinstance(value, dict):
            return "is not a JSON object"
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    return f"is missing required property '{key}'"
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, sub_schema in properties.items():
                if key in value:
                    problem = _check_shape(value[key], sub_schema)
                    if problem:
                        return f"property '{key}' {problem}"
        return None
    if expected == "array":
        if not isinstance(value, list):
            return "is not a JSON array"
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                problem = _check_shape(item, items)
                if problem:
                    return f"item {index} {problem}"
        return None
    if expected == "string":
        return None if isinstance(value, str) else "is not a string"
    if expected == "boolean":
        return None if isinstance(value, bool) else "is not a boolean"
    if expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return "is not an integer"
        return None
    if expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "is not a number"
        return None
    return None
