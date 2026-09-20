"""B01 unit tests: the mandatory self-hosted inference route.

Hermetic by construction - every provider runs on ``httpx.MockTransport`` (or a
scripted transport that sleeps/raises), so ZERO real sockets are opened and no
live server, credential or model is needed. The settings routes are exercised on
a minimal FastAPI app with an injected fake router, never through main.py.

Covered invariants: exact-origin allowlist refusal (cloud included, fail closed
at construction AND per call), redirects never followed, actual configured model
id on the wire, canonical <-> OpenAI normalization both directions with hard
rejection of malformed tool arguments, structured output with AT MOST one repair
round, the whole-call deadline, router fallback ordering with no cloud recovery,
secret redaction in exceptions and logs, and sanitized settings envelopes.

Run: python -m pytest backend/tests/unit/test_llm.py -q
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.settings import SYNTHETIC_TEST_PROMPT, router as settings_router
from app.config import Settings
from app.contracts.domain import ActionRisk, HealthStatus, ToolDefinition, ToolEffect
from app.contracts.providers import (
    ChatMessage,
    ChatRole,
    LLMModelInfo,
    LLMProvider,
    LLMResponse,
    ProviderHealth,
)
from app.llm.base import (
    CODE_NOT_CONFIGURED,
    CODE_PROTOCOL,
    CODE_TIMEOUT,
    CODE_UNAVAILABLE,
    LLMNotConfiguredError,
    LLMProtocolError,
    LLMTimeoutError,
    LLMUnavailableError,
    redact,
)
from app.llm.openai_compatible import PROVIDER_NAME, OpenAICompatibleProvider
from app.llm.router import LLMRouter

# Distinctive marker key: its absence is asserted across exceptions and logs.
API_KEY = "sk-proj-EVA-TESTONLY-8f2c41d7b9a6"
ALLOWED_ORIGINS = ["https://infer.tailnet.example", "https://backup.tailnet.example"]
PRIMARY_URL = "https://infer.tailnet.example/v1"
FALLBACK_URL = "https://backup.tailnet.example/v1"
MODEL = "qwen3-next-actual-serving-id"

STRUCTURED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "briefing_plan",
    "required": ["summary", "action_count"],
    "properties": {
        "summary": {"type": "string"},
        "action_count": {"type": "integer"},
    },
}


# --------------------------------------------------------------------------- #
# Scripted transport helpers
# --------------------------------------------------------------------------- #


class Recorder:
    """Collects every outbound request the provider actually made."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    @property
    def hosts(self) -> list[str]:
        return [str(request.url.host) for request in self.requests]

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests if request.content]

    @property
    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


def scripted_transport(
    script: list[Any], recorder: Recorder | None = None
) -> httpx.MockTransport:
    """Serve a queued script per request.

    Script items: ``(status, payload)`` JSON response, an ``httpx.Response``,
    an ``Exception`` to raise, or ``("sleep", seconds)`` to stall (which the
    asyncio deadline guard - not httpx - must cut off)."""

    queue = list(script)

    async def handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.requests.append(request)
        if not queue:
            return httpx.Response(500, json={"error": "script exhausted"})
        item = queue.pop(0)
        if isinstance(item, tuple) and item and item[0] == "sleep":
            await asyncio.sleep(float(item[1]))
            return httpx.Response(500, json={"error": "sleep item needs a follow-up response"})
        if isinstance(item, Exception):
            raise item
        if isinstance(item, httpx.Response):
            return item
        status, payload = item
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def make_provider(
    script: list[Any],
    *,
    base_url: str = PRIMARY_URL,
    model: str = MODEL,
    api_key: str | None = API_KEY,
    allowed_origins: list[str] | None = None,
    timeout_seconds: float = 45.0,
) -> tuple[OpenAICompatibleProvider, Recorder]:
    recorder = Recorder()
    provider = OpenAICompatibleProvider(
        base_url=base_url,
        model=model,
        api_key=api_key,
        allowed_origins=ALLOWED_ORIGINS if allowed_origins is None else allowed_origins,
        timeout_seconds=timeout_seconds,
        transport=scripted_transport(script, recorder),
    )
    return provider, recorder


def completion(
    text: str | None = "Hello from the local model",
    *,
    served_model: str | None = MODEL,
    finish_reason: str = "stop",
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant"}
    if text is not None:
        message["content"] = text
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    choice: dict[str, Any] = {"message": message, "finish_reason": finish_reason}
    payload: dict[str, Any] = {"choices": [choice]}
    if served_model is not None:
        payload["model"] = served_model
    return payload


def tool_definition(name: str = "calendar.update_agenda") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="Reschedule an existing meeting.",
        effect=ToolEffect.EXTERNAL_WRITE,
        input_schema={
            "type": "object",
            "required": ["event_id"],
            "properties": {"event_id": {"type": "string"}},
        },
        output_schema={"type": "object"},
        required_scopes=["calendar.events"],
        risk_floor=ActionRisk.HIGH,
        timeout_seconds=30,
    )


def user_message(text: str = "Summarize my afternoon.") -> ChatMessage:
    return ChatMessage(role=ChatRole.USER, content=text)


# --------------------------------------------------------------------------- #
# Mandatory-route allowlist: fail closed, cloud never reachable
# --------------------------------------------------------------------------- #


def test_cloud_endpoint_is_refused_at_construction() -> None:
    with pytest.raises(LLMNotConfiguredError) as excinfo:
        OpenAICompatibleProvider(
            base_url="https://api.openai.com/v1",
            model=MODEL,
            api_key=API_KEY,
            allowed_origins=ALLOWED_ORIGINS,
        )
    assert excinfo.value.code == CODE_NOT_CONFIGURED
    assert API_KEY not in str(excinfo.value)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.openai.com/v1",
        "https://openrouter.ai/api/v1",
        "http://localhost:11434/v1",
        # Suffix look-alikes must not match an allowlisted origin.
        "https://infer.tailnet.example.evil.com/v1",
        "https://evil.com/https://infer.tailnet.example",
        "ftp://infer.tailnet.example",
        "not-a-url",
    ],
)
def test_non_allowlisted_endpoints_are_refused(base_url: str) -> None:
    with pytest.raises(LLMNotConfiguredError):
        OpenAICompatibleProvider(
            base_url=base_url, model=MODEL, api_key=None, allowed_origins=ALLOWED_ORIGINS
        )


def test_credential_bearing_endpoint_refused_and_never_echoed() -> None:
    with pytest.raises(LLMNotConfiguredError) as excinfo:
        OpenAICompatibleProvider(
            base_url=f"https://user:{API_KEY}@infer.tailnet.example/v1",
            model=MODEL,
            api_key=None,
            allowed_origins=ALLOWED_ORIGINS,
        )
    assert API_KEY not in str(excinfo.value)


def test_empty_allowlist_refuses_construction() -> None:
    with pytest.raises(LLMNotConfiguredError):
        OpenAICompatibleProvider(
            base_url=PRIMARY_URL, model=MODEL, api_key=API_KEY, allowed_origins=[]
        )


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_endpoint_or_model_refused(blank: str) -> None:
    with pytest.raises(LLMNotConfiguredError):
        OpenAICompatibleProvider(
            base_url=blank, model=MODEL, allowed_origins=ALLOWED_ORIGINS
        )
    with pytest.raises(LLMNotConfiguredError):
        OpenAICompatibleProvider(
            base_url=PRIMARY_URL, model=blank, allowed_origins=ALLOWED_ORIGINS
        )


def test_revoked_origin_stops_subsequent_calls_without_sending() -> None:
    """The allowlist is held by reference: revocation blocks later requests."""
    origins = list(ALLOWED_ORIGINS)
    provider, recorder = make_provider(
        [(200, completion()), (200, completion())], allowed_origins=origins
    )
    first = asyncio.run(provider.chat([user_message()]))
    assert first.text is not None
    origins.remove("https://infer.tailnet.example")
    with pytest.raises(LLMNotConfiguredError):
        asyncio.run(provider.chat([user_message()]))
    assert len(recorder.requests) == 1  # the second call never left the process


def test_redirect_is_never_followed() -> None:
    provider, recorder = make_provider(
        [
            httpx.Response(
                302, headers={"location": "https://api.openai.com/v1/chat/completions"}
            )
        ]
    )
    with pytest.raises(LLMUnavailableError) as excinfo:
        asyncio.run(provider.chat([user_message()]))
    assert "redirect" in excinfo.value.message
    assert len(recorder.requests) == 1


# --------------------------------------------------------------------------- #
# Model discovery
# --------------------------------------------------------------------------- #


def test_list_models_maps_ids_display_names_and_capabilities() -> None:
    provider, recorder = make_provider(
        [
            (
                200,
                {
                    "object": "list",
                    "data": [
                        {"id": "plain-model"},
                        {"id": "friendly-model", "name": "Friendly Model"},
                        {"model": "alt-key-model"},
                        "bare-string-model",
                        {"id": "capable-model", "supports_tools": True, "context_window": 32768},
                        {"id": "duplicate-model"},
                        {"id": "   "},
                        {"no_id": True},
                        42,
                    ],
                },
            )
        ]
    )
    models = asyncio.run(provider.list_models())
    assert [m.id for m in models] == [
        "plain-model",
        "friendly-model",
        "alt-key-model",
        "bare-string-model",
        "capable-model",
        "duplicate-model",
    ]
    by_id = {m.id: m for m in models}
    # display_name falls back to the actual id when no friendly name exists.
    assert by_id["plain-model"].display_name == "plain-model"
    assert by_id["friendly-model"].display_name == "Friendly Model"
    assert by_id["capable-model"].supports_tools is True
    assert by_id["capable-model"].supports_structured_output is None
    assert by_id["capable-model"].context_window == 32768
    assert by_id["plain-model"].context_window is None
    assert recorder.paths == ["/v1/models"]


def test_list_models_sends_bearer_key_and_omits_it_when_unset() -> None:
    provider, recorder = make_provider([(200, {"data": [{"id": MODEL}]})])
    asyncio.run(provider.list_models())
    assert recorder.requests[0].headers["authorization"] == f"Bearer {API_KEY}"

    anonymous, anon_recorder = make_provider([(200, {"data": [{"id": MODEL}]})], api_key=None)
    asyncio.run(anonymous.list_models())
    assert "authorization" not in anon_recorder.requests[0].headers


def test_list_models_without_a_list_payload_is_a_protocol_error() -> None:
    provider, _ = make_provider([(200, {"status": "ok"})])
    with pytest.raises(LLMProtocolError):
        asyncio.run(provider.list_models())


# --------------------------------------------------------------------------- #
# Chat: wire request shape and canonical response
# --------------------------------------------------------------------------- #


def test_chat_sends_the_actual_configured_model_id() -> None:
    provider, recorder = make_provider([(200, completion(text="Sure."))])
    response = asyncio.run(
        provider.chat([ChatMessage(role=ChatRole.SYSTEM, content="Be terse."), user_message()])
    )
    body = recorder.bodies[0]
    assert body["model"] == MODEL
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][1]["content"] == "Summarize my afternoon."
    assert response.provider == PROVIDER_NAME
    assert response.model == MODEL
    assert response.text == "Sure."
    assert response.finish_reason == "stop"
    assert response.tool_calls == []


def test_chat_prefers_the_serving_id_reported_by_the_endpoint() -> None:
    provider, _ = make_provider([(200, completion(served_model="served-id-42"))])
    response = asyncio.run(provider.chat([user_message()]))
    assert response.model == "served-id-42"


def test_chat_falls_back_to_configured_id_when_server_omits_model() -> None:
    payload = completion()
    payload.pop("model")
    provider, _ = make_provider([(200, payload)])
    response = asyncio.run(provider.chat([user_message()]))
    assert response.model == MODEL


def test_chat_serializes_assistant_tool_calls_and_tool_results() -> None:
    provider, recorder = make_provider([(200, completion(text="Done."))])
    history = [
        user_message(),
        ChatMessage(
            role=ChatRole.ASSISTANT,
            tool_calls=[
                {
                    "id": "call_1",
                    "name": "calendar.update_agenda",
                    "arguments": {"event_id": "evt-1"},
                }
            ],
        ),
        ChatMessage(role=ChatRole.TOOL, tool_call_id="call_1", content='{"status":"ok"}'),
    ]
    asyncio.run(provider.chat(history))
    messages = recorder.bodies[0]["messages"]
    assistant = messages[1]
    assert assistant["role"] == "assistant"
    assert "content" not in assistant  # a call-only turn sends no content
    assert assistant["tool_calls"][0]["function"]["name"] == "calendar__update_agenda"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"event_id": "evt-1"}
    assert messages[2] == {"role": "tool", "tool_call_id": "call_1", "content": '{"status":"ok"}'}


def test_chat_without_messages_is_rejected_before_any_request() -> None:
    provider, recorder = make_provider([(200, completion())])
    with pytest.raises(LLMProtocolError):
        asyncio.run(provider.chat([]))
    assert recorder.requests == []


def test_tool_result_message_without_id_is_rejected() -> None:
    provider, _ = make_provider([(200, completion())])
    with pytest.raises(LLMProtocolError):
        asyncio.run(provider.chat([ChatMessage(role=ChatRole.TOOL, content="{}")]))


@pytest.mark.parametrize("status", [401, 429, 500, 503])
def test_http_errors_map_to_unavailable_without_retry_or_leak(status: int) -> None:
    provider, recorder = make_provider([(status, {"error": f"secret body {API_KEY}"})])
    with pytest.raises(LLMUnavailableError) as excinfo:
        asyncio.run(provider.chat([user_message()]))
    assert excinfo.value.code == CODE_UNAVAILABLE
    assert str(status) in excinfo.value.message
    assert API_KEY not in str(excinfo.value)
    assert "secret body" not in excinfo.value.message
    assert len(recorder.requests) == 1  # no retries in this adapter


def test_transport_failure_is_sanitized_no_url_no_key() -> None:
    provider, _ = make_provider(
        [
            httpx.ConnectError(
                f"[Errno 61] Connection refused: {PRIMARY_URL} headers=Bearer {API_KEY}"
            )
        ]
    )
    with pytest.raises(LLMUnavailableError) as excinfo:
        asyncio.run(provider.chat([user_message()]))
    assert API_KEY not in str(excinfo.value)
    assert "tailnet" not in str(excinfo.value)


def test_non_json_response_is_a_protocol_error() -> None:
    provider, _ = make_provider([httpx.Response(200, text="<html>proxy error</html>")])
    with pytest.raises(LLMProtocolError):
        asyncio.run(provider.chat([user_message()]))


def test_empty_completion_is_a_protocol_error() -> None:
    provider, _ = make_provider([(200, completion(text=None))])
    with pytest.raises(LLMProtocolError):
        asyncio.run(provider.chat([user_message()]))


# --------------------------------------------------------------------------- #
# Tool-call normalization (both directions)
# --------------------------------------------------------------------------- #


def test_canonical_tools_normalize_to_openai_function_specs() -> None:
    provider, recorder = make_provider([(200, completion(text="ok"))])
    tool = tool_definition()
    asyncio.run(provider.chat([user_message()], tools=[tool]))
    specs = recorder.bodies[0]["tools"]
    assert specs == [
        {
            "type": "function",
            "function": {
                # Dotted canonical name mapped to a wire-safe spelling.
                "name": "calendar__update_agenda",
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
    ]
    # Backend policy never crosses the wire: the model cannot see or choose it.
    assert "effect" not in json.dumps(specs)
    assert "risk_floor" not in json.dumps(specs)


def test_returned_tool_calls_normalize_to_canonical_calls() -> None:
    provider, _ = make_provider(
        [
            (
                200,
                completion(
                    text=None,
                    finish_reason="tool_calls",
                    tool_calls=[
                        {
                            "id": "call_9",
                            "type": "function",
                            "function": {
                                "name": "calendar__update_agenda",
                                "arguments": '{"event_id": "evt-7"}',
                            },
                        }
                    ],
                ),
            )
        ]
    )
    response = asyncio.run(provider.chat([user_message()], tools=[tool_definition()]))
    assert [call.name for call in response.tool_calls] == ["calendar.update_agenda"]
    assert response.tool_calls[0].id == "call_9"
    assert response.tool_calls[0].arguments == {"event_id": "evt-7"}
    assert response.finish_reason == "tool_calls"


def test_server_echoing_the_dotted_name_still_resolves_canonically() -> None:
    provider, _ = make_provider(
        [
            (
                200,
                completion(
                    text=None,
                    tool_calls=[
                        {
                            "id": "call_1",
                            "function": {"name": "calendar.update_agenda", "arguments": "{}"},
                        }
                    ],
                ),
            )
        ]
    )
    response = asyncio.run(provider.chat([user_message()], tools=[tool_definition()]))
    assert response.tool_calls[0].name == "calendar.update_agenda"


def test_empty_argument_string_means_no_arguments() -> None:
    provider, _ = make_provider(
        [
            (
                200,
                completion(
                    text=None,
                    tool_calls=[
                        {"id": "call_1", "function": {"name": "focus.stop", "arguments": ""}}
                    ],
                ),
            )
        ]
    )
    response = asyncio.run(provider.chat([user_message()], tools=[tool_definition("focus.stop")]))
    assert response.tool_calls[0].arguments == {}


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ("{not valid json", "malformed JSON"),
        ('{"event_id": }', "truncated JSON"),
        ("[1, 2]", "JSON array instead of object"),
        ('"just a string"', "JSON string instead of object"),
        (17, "unsupported argument type"),
    ],
)
def test_malformed_tool_arguments_are_protocol_errors(arguments: Any, reason: str) -> None:
    provider, _ = make_provider(
        [
            (
                200,
                completion(
                    text=None,
                    tool_calls=[
                        {
                            "id": "call_1",
                            "function": {"name": "calendar__update_agenda", "arguments": arguments},
                        }
                    ],
                ),
            )
        ]
    )
    with pytest.raises(LLMProtocolError) as excinfo:
        asyncio.run(provider.chat([user_message()], tools=[tool_definition()]))
    assert excinfo.value.code == CODE_PROTOCOL
    assert reason  # documentation of the parametrized case


@pytest.mark.parametrize(
    "entry",
    [
        {"function": {"name": "calendar__update_agenda", "arguments": "{}"}},  # no id
        {"id": "", "function": {"name": "calendar__update_agenda"}},
        {"id": "call_1", "function": {"arguments": "{}"}},  # no name
        {"id": "call_1"},  # no function
        "not-an-object",
    ],
)
def test_malformed_tool_call_envelopes_are_protocol_errors(entry: Any) -> None:
    provider, _ = make_provider([(200, completion(text=None, tool_calls=[entry]))])
    with pytest.raises(LLMProtocolError):
        asyncio.run(provider.chat([user_message()], tools=[tool_definition()]))


def test_tool_call_for_an_unoffered_tool_is_rejected() -> None:
    provider, _ = make_provider(
        [
            (
                200,
                completion(
                    text=None,
                    tool_calls=[
                        {"id": "call_1", "function": {"name": "gmail__send", "arguments": "{}"}}
                    ],
                ),
            )
        ]
    )
    with pytest.raises(LLMProtocolError):
        asyncio.run(provider.chat([user_message()], tools=[tool_definition()]))


def test_unexpected_tool_calls_without_offered_tools_are_rejected() -> None:
    provider, _ = make_provider(
        [
            (
                200,
                completion(
                    text=None,
                    tool_calls=[
                        {
                            "id": "call_1",
                            "function": {"name": "calendar__update_agenda", "arguments": "{}"},
                        }
                    ],
                ),
            )
        ]
    )
    with pytest.raises(LLMProtocolError):
        asyncio.run(provider.chat([user_message()]))


# --------------------------------------------------------------------------- #
# Structured output: strict request, one repair round, then hard failure
# --------------------------------------------------------------------------- #


def test_structured_output_requests_strict_json_schema() -> None:
    provider, recorder = make_provider(
        [(200, completion(text='{"summary": "Two meetings", "action_count": 2}'))]
    )
    response = asyncio.run(provider.chat([user_message()], response_schema=STRUCTURED_SCHEMA))
    format_spec = recorder.bodies[0]["response_format"]
    assert format_spec["type"] == "json_schema"
    assert format_spec["json_schema"]["strict"] is True
    assert format_spec["json_schema"]["schema"] == STRUCTURED_SCHEMA
    assert format_spec["json_schema"]["name"] == "briefing_plan"
    assert response.structured == {"summary": "Two meetings", "action_count": 2}
    assert len(recorder.requests) == 1  # valid JSON needs no repair


def test_fenced_json_is_accepted_without_a_repair_round() -> None:
    provider, recorder = make_provider(
        [(200, completion(text='```json\n{"summary": "s", "action_count": 1}\n```'))]
    )
    response = asyncio.run(provider.chat([user_message()], response_schema=STRUCTURED_SCHEMA))
    assert response.structured == {"summary": "s", "action_count": 1}
    assert len(recorder.requests) == 1


def test_repair_round_fixes_invalid_json_with_exactly_one_extra_call() -> None:
    provider, recorder = make_provider(
        [
            (200, completion(text="Sure! The summary is two meetings.")),
            (200, completion(text='{"summary": "Two meetings", "action_count": 2}')),
        ]
    )
    response = asyncio.run(provider.chat([user_message()], response_schema=STRUCTURED_SCHEMA))
    assert len(recorder.requests) == 2
    assert response.structured == {"summary": "Two meetings", "action_count": 2}
    repair_body = recorder.bodies[1]
    # The repair call keeps history, the bad answer and a correction instruction.
    assert [m["role"] for m in repair_body["messages"]] == ["user", "assistant", "user"]
    assert repair_body["messages"][1]["content"] == "Sure! The summary is two meetings."
    assert "JSON" in repair_body["messages"][2]["content"]
    # Still the same actual model id, still strict schema mode.
    assert repair_body["model"] == MODEL
    assert repair_body["response_format"]["json_schema"]["strict"] is True


def test_repair_round_repairs_a_shape_mismatch() -> None:
    provider, recorder = make_provider(
        [
            (200, completion(text='{"summary": "missing counter"}')),
            (200, completion(text='{"summary": "ok", "action_count": 0}')),
        ]
    )
    response = asyncio.run(provider.chat([user_message()], response_schema=STRUCTURED_SCHEMA))
    assert len(recorder.requests) == 2
    assert response.structured == {"summary": "ok", "action_count": 0}


def test_second_failure_raises_after_exactly_one_repair_attempt() -> None:
    provider, recorder = make_provider(
        [
            (200, completion(text="prose again")),
            (200, completion(text='{"summary": "still missing the counter"}')),
            # Queued but must NEVER be reached: a third attempt is forbidden.
            (200, completion(text='{"summary": "late", "action_count": 1}')),
        ]
    )
    with pytest.raises(LLMProtocolError) as excinfo:
        asyncio.run(provider.chat([user_message()], response_schema=STRUCTURED_SCHEMA))
    assert excinfo.value.code == CODE_PROTOCOL
    assert len(recorder.requests) == 2


def test_structured_repair_does_not_retry_on_a_timeout_of_the_whole_call() -> None:
    provider, recorder = make_provider(
        [
            (200, completion(text="prose")),
            ("sleep", 0.5),
        ],
        timeout_seconds=0.15,
    )
    with pytest.raises(LLMTimeoutError):
        asyncio.run(provider.chat([user_message()], response_schema=STRUCTURED_SCHEMA))
    # The repair request started but the SHARED budget cut it off - no third try.
    assert len(recorder.requests) == 2


# --------------------------------------------------------------------------- #
# Whole-call deadline
# --------------------------------------------------------------------------- #


def test_slow_endpoint_hits_the_asyncio_deadline_guard() -> None:
    """MockTransport ignores httpx timeouts, so this proves the asyncio guard."""
    provider, recorder = make_provider([("sleep", 1.0)], timeout_seconds=0.05)
    with pytest.raises(LLMTimeoutError) as excinfo:
        asyncio.run(provider.chat([user_message()]))
    assert excinfo.value.code == CODE_TIMEOUT
    assert len(recorder.requests) == 1


def test_httpx_timeout_maps_to_the_canonical_timeout_error() -> None:
    provider, _ = make_provider([httpx.ReadTimeout(f"timed out reading {PRIMARY_URL}")])
    with pytest.raises(LLMTimeoutError) as excinfo:
        asyncio.run(provider.chat([user_message()]))
    assert "tailnet" not in str(excinfo.value)


def test_default_deadline_is_45_seconds() -> None:
    provider, _ = make_provider([(200, completion())])
    assert provider.timeout_seconds == 45.0  # frozen default per the brief


@pytest.mark.parametrize("bad_timeout", [0, -1])
def test_non_positive_timeout_is_a_programming_error(bad_timeout: float) -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleProvider(
            base_url=PRIMARY_URL,
            model=MODEL,
            allowed_origins=ALLOWED_ORIGINS,
            timeout_seconds=bad_timeout,
        )


# --------------------------------------------------------------------------- #
# Health probe
# --------------------------------------------------------------------------- #


def test_health_ready_when_the_configured_model_is_served() -> None:
    provider, _ = make_provider([(200, {"data": [{"id": MODEL}]})])
    health = asyncio.run(provider.health())
    assert health.status is HealthStatus.READY
    assert health.provider == PROVIDER_NAME
    assert health.model == MODEL


def test_health_degraded_when_the_configured_model_is_not_listed() -> None:
    provider, _ = make_provider([(200, {"data": [{"id": "some-other-model"}]})])
    health = asyncio.run(provider.health())
    assert health.status is HealthStatus.DEGRADED
    assert health.model == MODEL
    assert API_KEY not in (health.detail or "")


@pytest.mark.parametrize(
    "script",
    [
        [(503, {"error": "down"})],
        [httpx.ConnectError("connection refused")],
        [("sleep", 1.0)],
    ],
)
def test_health_unavailable_never_raises(script: list[Any]) -> None:
    provider, _ = make_provider(script, timeout_seconds=0.05)
    health = asyncio.run(provider.health())
    assert health.status is HealthStatus.UNAVAILABLE
    assert health.provider == PROVIDER_NAME


# --------------------------------------------------------------------------- #
# Router: primary/fallback ordering, no cloud recovery, aggregated health
# --------------------------------------------------------------------------- #


def _router_with(
    primary_script: list[Any] | None,
    fallback_script: list[Any] | None,
    **kwargs: Any,
) -> tuple[LLMRouter, Recorder, Recorder]:
    primary_recorder = Recorder()
    fallback_recorder = Recorder()
    primary = (
        None
        if primary_script is None
        else OpenAICompatibleProvider(
            base_url=PRIMARY_URL,
            model=MODEL,
            api_key=API_KEY,
            allowed_origins=ALLOWED_ORIGINS,
            transport=scripted_transport(primary_script, primary_recorder),
            **kwargs,
        )
    )
    fallback = (
        None
        if fallback_script is None
        else OpenAICompatibleProvider(
            base_url=FALLBACK_URL,
            model="backup-model-id",
            api_key=API_KEY,
            allowed_origins=ALLOWED_ORIGINS,
            transport=scripted_transport(fallback_script, fallback_recorder),
            **kwargs,
        )
    )
    return LLMRouter(primary=primary, fallback=fallback), primary_recorder, fallback_recorder


def test_router_prefers_primary_and_never_touches_fallback_on_success() -> None:
    router, primary_rec, fallback_rec = _router_with(
        [(200, completion(text="primary answer"))], [(200, completion(text="fallback answer"))]
    )
    response = asyncio.run(router.chat([user_message()]))
    assert response.text == "primary answer"
    assert primary_rec.hosts == ["infer.tailnet.example"]
    assert fallback_rec.hosts == []


def test_router_falls_back_after_primary_http_failure_in_order() -> None:
    router, primary_rec, fallback_rec = _router_with(
        [(500, {"error": "boom"})],
        [(200, completion(text="fallback answer", served_model="backup-model-id"))],
    )
    response = asyncio.run(router.chat([user_message()]))
    assert response.text == "fallback answer"
    assert response.model == "backup-model-id"
    assert primary_rec.hosts == ["infer.tailnet.example"]
    assert fallback_rec.hosts == ["backup.tailnet.example"]


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("read timed out"),
        (503, {"error": "unavailable"}),
    ],
)
def test_router_falls_back_on_unavailable_and_timeout(failure: Any) -> None:
    router, _, fallback_rec = _router_with(
        [failure], [(200, completion(text="fallback answer"))]
    )
    response = asyncio.run(router.chat([user_message()]))
    assert response.text == "fallback answer"
    assert fallback_rec.hosts == ["backup.tailnet.example"]


def test_router_falls_back_on_protocol_failure() -> None:
    router, _, fallback_rec = _router_with(
        [(200, {"unexpected": "payload"})], [(200, completion(text="fallback answer"))]
    )
    response = asyncio.run(router.chat([user_message()]))
    assert response.text == "fallback answer"
    assert fallback_rec.hosts == ["backup.tailnet.example"]


def test_router_without_fallback_keeps_the_stable_single_route_error() -> None:
    router, primary_rec, _ = _router_with([(500, {"error": "boom"})], None)
    with pytest.raises(LLMUnavailableError) as excinfo:
        asyncio.run(router.chat([user_message()]))
    assert excinfo.value.code == CODE_UNAVAILABLE
    assert "HTTP 500" in excinfo.value.message
    assert len(primary_rec.requests) == 1


def test_router_reports_unavailable_when_both_routes_fail_sanitized() -> None:
    router, primary_rec, fallback_rec = _router_with(
        [(200, {"unexpected": "payload"})], [(503, {"error": "down"})]
    )
    with pytest.raises(LLMUnavailableError) as excinfo:
        asyncio.run(router.chat([user_message()]))
    assert excinfo.value.code == CODE_UNAVAILABLE
    # Stable codes are informative; hosts and keys are not present.
    assert CODE_PROTOCOL in excinfo.value.message
    assert CODE_UNAVAILABLE in excinfo.value.message
    assert "tailnet" not in str(excinfo.value)
    assert API_KEY not in str(excinfo.value)
    assert len(primary_rec.requests) == 1 and len(fallback_rec.requests) == 1


def test_unconfigured_router_refuses_chat_and_reports_unavailable() -> None:
    router = LLMRouter()
    with pytest.raises(LLMNotConfiguredError):
        asyncio.run(router.chat([user_message()]))
    with pytest.raises(LLMNotConfiguredError):
        asyncio.run(router.list_models())
    assert asyncio.run(router.health()).status is HealthStatus.UNAVAILABLE


def test_router_health_prefers_ready_primary() -> None:
    router, _, _ = _router_with([(200, {"data": [{"id": MODEL}]})], [(200, {"data": []})])
    health = asyncio.run(router.health())
    assert health.status is HealthStatus.READY
    assert health.model == MODEL


def test_router_health_reports_degraded_when_only_fallback_is_ready() -> None:
    router, _, _ = _router_with([(503, {"error": "down"})], [(200, {"data": [{"id": "backup-model-id"}]})])
    health = asyncio.run(router.health())
    assert health.status is HealthStatus.DEGRADED
    assert health.detail == "primary unavailable; local fallback ready"
    assert health.model == "backup-model-id"


def test_router_health_unavailable_when_neither_route_answers() -> None:
    router, _, _ = _router_with([(503, {"error": "down"})], [(503, {"error": "down"})])
    health = asyncio.run(router.health())
    assert health.status is HealthStatus.UNAVAILABLE


def test_router_list_models_falls_back_when_primary_cannot_answer() -> None:
    router, _, fallback_rec = _router_with(
        [(500, {"error": "boom"})], [(200, {"data": [{"id": "backup-model-id"}]})]
    )
    models = asyncio.run(router.list_models())
    assert [m.id for m in models] == ["backup-model-id"]
    assert fallback_rec.hosts == ["backup.tailnet.example"]


# --------------------------------------------------------------------------- #
# from_settings: only configured + allowlisted self-hosted routes exist
# --------------------------------------------------------------------------- #


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "eva_llm_base_url": PRIMARY_URL,
        "eva_llm_model": MODEL,
        "eva_llm_api_key": API_KEY,
        "eva_llm_allowed_origins": ALLOWED_ORIGINS,
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_from_settings_builds_configured_routes() -> None:
    router = LLMRouter.from_settings(
        _settings(
            eva_llm_fallback_base_url=FALLBACK_URL,
            eva_llm_fallback_model="backup-model-id",
        )
    )
    assert router.primary is not None
    assert router.fallback is not None


def test_from_settings_never_builds_a_cloud_primary() -> None:
    settings = _settings(eva_llm_base_url="https://api.openai.com/v1")
    router = LLMRouter.from_settings(settings)
    assert router.primary is None
    with pytest.raises(LLMNotConfiguredError):
        asyncio.run(router.chat([user_message()]))


def test_from_settings_ignores_an_unallowlisted_fallback() -> None:
    settings = _settings(
        eva_llm_fallback_base_url="https://api.openai.com/v1",
        eva_llm_fallback_model="gpt-something",
    )
    router = LLMRouter.from_settings(settings)
    assert router.primary is not None
    assert router.fallback is None


def test_from_settings_with_blank_config_has_no_routes() -> None:
    settings = _settings(eva_llm_base_url="", eva_llm_model="", eva_llm_allowed_origins=[])
    router = LLMRouter.from_settings(settings)
    assert router.primary is None and router.fallback is None


def test_provider_and_router_satisfy_the_frozen_protocol() -> None:
    provider, _ = make_provider([(200, completion())])
    assert isinstance(provider, LLMProvider)
    assert isinstance(LLMRouter(), LLMProvider)


# --------------------------------------------------------------------------- #
# Secret redaction
# --------------------------------------------------------------------------- #


def test_redact_scrubs_credential_shapes() -> None:
    samples = {
        f"Authorization: Bearer {API_KEY} sent": API_KEY,
        f"x-api-key={API_KEY}": API_KEY,
        f"request failed api_key: '{API_KEY}'": API_KEY,
        "opaque-token=abcdef123456": "abcdef123456",
        f"https://user:{API_KEY}@host.example/v1": API_KEY,
    }
    for raw, secret in samples.items():
        cleaned = redact(raw)
        assert secret not in cleaned, raw
        assert "[redacted]" in cleaned


def test_redact_removes_known_secrets_without_recognizable_shape() -> None:
    opaque = "Zx9-priv-value"
    assert opaque not in redact(f"header was {opaque} here", secrets=[opaque])
    # Non-secret prose is left alone (no over-redaction of ordinary words).
    assert redact("monkey=42 model=qwen3-next") == "monkey=42 model=qwen3-next"


@pytest.mark.parametrize(
    "script",
    [
        [(401, {"error": f"bad key {API_KEY}"})],
        [httpx.ConnectError(f"refused; header Bearer {API_KEY}")],
        [
            (
                200,
                completion(
                    text=None,
                    tool_calls=[
                        {
                            "id": "call_1",
                            "function": {"name": "calendar__update_agenda", "arguments": "{oops"},
                        }
                    ],
                ),
            )
        ],
        [(200, {"no_choices": True})],
    ],
)
def test_api_key_never_appears_in_any_failure_path(script: list[Any]) -> None:
    provider, _ = make_provider(script)
    with pytest.raises(Exception) as excinfo:  # canonical LLMError subclasses
        asyncio.run(provider.chat([user_message()], tools=[tool_definition()]))
    assert API_KEY not in str(excinfo.value)
    assert API_KEY not in repr(excinfo.value)


def test_logs_contain_no_key_or_header(caplog: pytest.LogCaptureFixture) -> None:
    provider, _ = make_provider([(200, completion()), (500, {"error": "boom"})])
    with caplog.at_level(logging.DEBUG):
        asyncio.run(provider.chat([user_message()]))
        with pytest.raises(LLMUnavailableError):
            asyncio.run(provider.chat([user_message()]))
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert API_KEY not in logged
    assert "Bearer" not in logged


def test_router_logs_contain_no_key_or_url(caplog: pytest.LogCaptureFixture) -> None:
    router, _, _ = _router_with([(500, {"error": "boom"})], [(200, completion())])
    with caplog.at_level(logging.DEBUG):
        asyncio.run(router.chat([user_message()]))
    all_records = "\n".join(record.getMessage() for record in caplog.records)
    # Keys never appear anywhere. httpx's own INFO line carries the request URL
    # by design (a credential-free origin - Settings rejects userinfo URLs);
    # EVA-authored log lines carry no host, path or header material at all.
    assert API_KEY not in all_records
    eva_records = "\n".join(
        record.getMessage() for record in caplog.records if record.name.startswith("eva.")
    )
    assert "tailnet" not in eva_records
    assert "Bearer" not in eva_records


def test_error_messages_are_length_bounded() -> None:
    error = LLMUnavailableError("x" * 5000)
    assert len(error.message) <= 400


# --------------------------------------------------------------------------- #
# Settings routes
# --------------------------------------------------------------------------- #


class FakeRouter:
    """Injected app.state.llm_router double: records calls, canned outcome."""

    def __init__(
        self,
        *,
        response: LLMResponse | None = None,
        error: Exception | None = None,
        models: list[LLMModelInfo] | None = None,
    ) -> None:
        self.response = response or LLMResponse(
            provider=PROVIDER_NAME, model=MODEL, text="ok", finish_reason="stop"
        )
        self.error = error
        self.models = [] if models is None else models
        self.chat_prompts: list[str] = []
        self.chat_calls = 0
        self.list_models_calls = 0

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        response_schema: dict | None = None,
    ) -> LLMResponse:
        self.chat_calls += 1
        self.chat_prompts.extend(message.content or "" for message in messages)
        if self.error is not None:
            raise self.error
        return self.response

    async def list_models(self) -> list[LLMModelInfo]:
        self.list_models_calls += 1
        if self.error is not None:
            raise self.error
        return self.models

    async def health(self) -> ProviderHealth:
        if self.error is not None:
            return ProviderHealth(status=HealthStatus.UNAVAILABLE, provider=PROVIDER_NAME)
        return ProviderHealth(status=HealthStatus.READY, provider=PROVIDER_NAME, model=MODEL)


def make_settings_client(settings: Settings, *, llm_router: Any = None) -> TestClient:
    app = FastAPI()
    app.include_router(settings_router)
    app.state.settings = settings
    if llm_router is not None:
        app.state.llm_router = llm_router
    return TestClient(app)


def test_get_llm_exposes_presence_flags_only() -> None:
    client = make_settings_client(
        _settings(
            eva_llm_fallback_base_url=FALLBACK_URL,
            eva_llm_fallback_model="backup-model-id",
            eva_llm_fallback_api_key="fallback-" + API_KEY,
        )
    )
    response = client.get("/api/settings/llm")
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "openai_compatible"
    assert body["base_url"] == PRIMARY_URL
    assert body["model"] == MODEL
    assert body["api_key_present"] is True
    assert body["fallback_base_url"] == FALLBACK_URL
    assert body["fallback_model"] == "backup-model-id"
    assert body["fallback_api_key_present"] is True
    assert body["configured"] is True
    # Cloud permissions stay false and no adapter exists to flip them.
    assert body["allow_cloud_inference"] is False
    assert body["allow_workspace_cloud_inference"] is False
    assert API_KEY not in response.text
    assert "api_key" not in {key for key in body if not key.endswith("_present")}


def test_get_llm_reports_unconfigured_state() -> None:
    settings = _settings(eva_llm_base_url="", eva_llm_model="", eva_llm_api_key="", eva_llm_allowed_origins=[])
    body = make_settings_client(settings).get("/api/settings/llm").json()
    assert body["configured"] is False
    assert body["base_url"] is None
    assert body["model"] is None
    assert body["api_key_present"] is False


def test_put_rejects_cloud_endpoint_and_changes_nothing() -> None:
    settings = _settings()
    client = make_settings_client(settings)
    response = client.put("/api/settings/llm", json={"base_url": "https://api.openai.com/v1"})
    assert 400 <= response.status_code < 500
    assert settings.eva_llm_base_url == PRIMARY_URL


def test_put_rejects_non_allowlisted_endpoint() -> None:
    client = make_settings_client(_settings())
    response = client.put(
        "/api/settings/llm", json={"base_url": "https://evil.example/v1", "model": "x"}
    )
    assert 400 <= response.status_code < 500


def test_put_rejects_credential_bearing_endpoint_without_echoing_it() -> None:
    client = make_settings_client(_settings())
    response = client.put(
        "/api/settings/llm",
        json={"base_url": f"https://user:{API_KEY}@infer.tailnet.example/v1"},
    )
    assert 400 <= response.status_code < 500
    assert API_KEY not in response.text


def test_put_rejects_relative_and_query_urls() -> None:
    client = make_settings_client(_settings())
    for bad in ["infer.tailnet.example/v1", "http://infer.tailnet.example/v1?token=abc"]:
        response = client.put("/api/settings/llm", json={"base_url": bad})
        assert 400 <= response.status_code < 500


def test_put_with_empty_allowlist_refuses_every_endpoint() -> None:
    settings = _settings(eva_llm_allowed_origins=[], eva_llm_base_url="", eva_llm_model="")
    client = make_settings_client(settings)
    response = client.put("/api/settings/llm", json={"base_url": PRIMARY_URL, "model": MODEL})
    assert 400 <= response.status_code < 500
    assert settings.eva_llm_base_url == ""


def test_put_updates_live_settings_and_rebuilds_router() -> None:
    settings = _settings()
    client = make_settings_client(settings)
    response = client.put(
        "/api/settings/llm",
        json={
            "base_url": FALLBACK_URL,
            "model": "new-actual-model-id",
            "api_key": API_KEY,
            "fallback_base_url": PRIMARY_URL,
            "fallback_model": MODEL,
        },
    )
    assert response.status_code == 200, response.text
    assert settings.eva_llm_base_url == FALLBACK_URL
    assert settings.eva_llm_model == "new-actual-model-id"
    assert settings.eva_llm_api_key == API_KEY
    router = client.app.state.llm_router
    assert isinstance(router, LLMRouter)
    assert router.primary is not None
    assert router.primary.model_id == "new-actual-model-id"
    assert router.primary.base_url == FALLBACK_URL
    assert router.fallback is not None
    # Secret values never appear in the sanitized response.
    assert API_KEY not in response.text
    assert response.json()["api_key_present"] is True


def test_put_rebuilds_router_identity_on_every_update() -> None:
    settings = _settings()
    client = make_settings_client(settings)
    client.put("/api/settings/llm", json={"model": "first-id"})
    first = client.app.state.llm_router
    client.put("/api/settings/llm", json={"model": "second-id"})
    assert client.app.state.llm_router is not first
    assert client.app.state.llm_router.primary.model_id == "second-id"


def test_put_api_key_is_write_only_and_never_echoed() -> None:
    settings = _settings(eva_llm_api_key="")
    client = make_settings_client(settings)
    response = client.put("/api/settings/llm", json={"api_key": API_KEY})
    assert response.status_code == 200
    assert settings.eva_llm_api_key == API_KEY
    assert API_KEY not in response.text
    assert response.json()["api_key_present"] is True


def test_put_empty_api_key_clears_the_stored_key() -> None:
    settings = _settings(eva_llm_api_key=API_KEY)
    client = make_settings_client(settings)
    response = client.put("/api/settings/llm", json={"api_key": ""})
    assert response.status_code == 200
    assert settings.eva_llm_api_key == ""
    assert response.json()["api_key_present"] is False


def test_test_connection_uses_only_the_synthetic_prompt() -> None:
    fake = FakeRouter()
    client = make_settings_client(_settings(), llm_router=fake)
    response = client.post("/api/settings/llm/test")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["health"]["status"] == "ready"
    assert body["model"] == MODEL
    assert isinstance(body["latency_ms"], int) and body["latency_ms"] >= 0
    # Exactly one synthetic message: no Workspace content, no history.
    assert fake.chat_calls == 1
    assert fake.chat_prompts == [SYNTHETIC_TEST_PROMPT]
    probe = SYNTHETIC_TEST_PROMPT.lower()
    assert not any(word in probe for word in ("meeting", "gmail", "email", "attention", "briefing"))


@pytest.mark.parametrize(
    "error",
    [
        LLMUnavailableError("self-hosted endpoint returned HTTP 503"),
        LLMTimeoutError(),
        LLMProtocolError(),
    ],
)
def test_test_connection_maps_failures_to_unavailable_never_500(error: Exception) -> None:
    fake = FakeRouter(error=error)
    client = make_settings_client(_settings(), llm_router=fake)
    response = client.post("/api/settings/llm/test")
    assert response.status_code == 200
    body = response.json()
    assert body["health"]["status"] == "unavailable"
    assert body["health"]["detail"]
    assert API_KEY not in response.text


def test_test_connection_on_unconfigured_route_makes_no_call() -> None:
    fake = FakeRouter()
    settings = _settings(eva_llm_base_url="", eva_llm_model="", eva_llm_allowed_origins=[])
    client = make_settings_client(settings, llm_router=fake)
    response = client.post("/api/settings/llm/test")
    assert response.status_code == 200
    body = response.json()
    assert body["health"]["status"] == "unavailable"
    assert body["latency_ms"] is None
    assert fake.chat_calls == 0


def test_detect_models_returns_the_served_listing() -> None:
    fake = FakeRouter(
        models=[
            LLMModelInfo(id="model-a", display_name="Model A", context_window=8192),
            LLMModelInfo(id="model-b", display_name="model-b"),
        ]
    )
    client = make_settings_client(_settings(), llm_router=fake)
    response = client.post("/api/settings/llm/detect")
    assert response.status_code == 200
    models = response.json()["models"]
    assert [m["id"] for m in models] == ["model-a", "model-b"]
    assert models[0]["display_name"] == "Model A"
    assert fake.list_models_calls == 1


@pytest.mark.parametrize(
    "error", [LLMUnavailableError("endpoint unreachable"), LLMProtocolError()]
)
def test_detect_models_failure_is_an_empty_sanitized_response(error: Exception) -> None:
    client = make_settings_client(_settings(), llm_router=FakeRouter(error=error))
    response = client.post("/api/settings/llm/detect")
    assert response.status_code == 200
    assert response.json() == {"models": []}


def test_detect_models_on_unconfigured_route_is_empty_and_silent() -> None:
    fake = FakeRouter()
    settings = _settings(eva_llm_allowed_origins=[], eva_llm_base_url="", eva_llm_model="")
    client = make_settings_client(settings, llm_router=fake)
    response = client.post("/api/settings/llm/detect")
    assert response.status_code == 200
    assert response.json() == {"models": []}
    assert fake.list_models_calls == 0


def test_mutating_settings_routes_enforce_the_app_origin_allowlist() -> None:
    settings = _settings(eva_app_allowed_origins=["http://app.local"])
    client = make_settings_client(settings, llm_router=FakeRouter())
    blocked = client.put(
        "/api/settings/llm", json={"model": "x"}, headers={"Origin": "https://evil.example"}
    )
    assert blocked.status_code == 403
    allowed = client.put(
        "/api/settings/llm", json={"model": "ok-model"}, headers={"Origin": "http://app.local"}
    )
    assert allowed.status_code == 200
    # Reads are not mutation-guarded.
    assert (
        client.get("/api/settings/llm", headers={"Origin": "https://evil.example"}).status_code
        == 200
    )


def test_settings_routes_are_mounted_under_the_api_prefix() -> None:
    client = make_settings_client(_settings())
    assert client.get("/settings/llm").status_code == 404
    assert client.get("/api/settings/llm").status_code == 200
