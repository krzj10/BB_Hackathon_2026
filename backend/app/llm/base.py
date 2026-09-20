"""Canonical LLM error taxonomy, route-allowlist primitive and secret hygiene (B01).

Every provider in this package raises ONLY the four stable error classes below.
Sanitization is enforced at a single choke point: :class:`LLMError` runs every
message through :func:`redact` and bounds its length, so an accidental
interpolation of a key, an ``Authorization`` value or a credential-bearing URL
cannot survive into an exception string (and therefore not into a log line that
formats the exception either). EVA-authored detail text additionally never
contains URLs or hosts - route identity belongs in configuration, not in
diagnostics.

Consumers branch on ``code`` (stable across releases), never on message prose:

- ``llm_not_configured``  machine-setup problem (blank settings, empty allowlist,
  or an endpoint origin that is not allowlisted). Fail closed - nothing is sent.
- ``llm_unavailable``     the self-hosted endpoint could not serve this request.
- ``llm_timeout``         the whole-call deadline expired.
- ``llm_protocol_error``  the endpoint answered, but outside the frozen
  OpenAI-compatible / canonical contract (malformed tool arguments, no choices,
  schema mismatch after the single repair round, ...).

There is deliberately no cloud error class and no cloud code: this package
implements the mandatory self-hosted route only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from urllib.parse import urlsplit

CODE_NOT_CONFIGURED = "llm_not_configured"
CODE_UNAVAILABLE = "llm_unavailable"
CODE_TIMEOUT = "llm_timeout"
CODE_PROTOCOL = "llm_protocol_error"

#: Placeholder substituted for anything that looks like a credential.
REDACTED = "[redacted]"

#: Hard bound on error detail length: an EVA-authored message is short by
#: design, so anything longer means somebody interpolated provider content.
MAX_MESSAGE_CHARS = 300

# --- redaction patterns -----------------------------------------------------
# Header form: "Authorization: Bearer <value>", "x-api-key: <value>".
_HEADER_SECRET = re.compile(
    r"(?i)\b(authorization|proxy-authorization|x-api-key|api-key)\b"
    r"(\s*[:=]\s*)(?:bearer\s+)?[^\s,;}\]]+"
)
# Assignment form: api_key=..., "access_token": "...", password: '...'.
# Bare "key" is NOT a trigger (it would eat words like "monkey"); compound and
# well-known secret names are.
_ASSIGNMENT_SECRET = re.compile(
    r"""(?ix)
    \b((?:[a-z0-9]+[_-])*(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|
        refresh[_-]?token|id[_-]?token|client[_-]?secret|secret|password|passwd|token))
    (\s*[:=]\s*)
    ("[^"]*"|'[^']*'|[^\s,;}&]+)
    """
)
_BEARER_SECRET = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{6,}")
# Provider key shapes (OpenAI-style and friends).
_PROVIDER_KEY = re.compile(r"\b(?:sk|pk|key|pat)-[A-Za-z0-9_\-]{4,}")
# Any URL userinfo: scheme://user[:pass]@host
_URL_USERINFO = re.compile(r"(?i)([a-z][a-z0-9+.\-]*://)[^\s/@:]+(?::[^\s/@]*)?@")


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Scrub credential-looking content out of ``text``.

    Two independent layers:

    1. exact removal of ``secrets`` (values the caller knows it owns - API keys,
       header material), so even an opaque key with no recognizable shape can
       never survive;
    2. pattern removal of bearer tokens, provider-style keys, ``*key=*`` /
       ``*token=*`` assignments and URL userinfo.

    Never raises and is safe to call on already-sanitized text.
    """
    if not text:
        return text
    result = text
    for secret in secrets:
        if secret and len(secret) >= 4:
            result = result.replace(secret, REDACTED)
    result = _HEADER_SECRET.sub(rf"\1\2{REDACTED}", result)
    result = _ASSIGNMENT_SECRET.sub(rf"\1\2{REDACTED}", result)
    result = _BEARER_SECRET.sub(f"Bearer {REDACTED}", result)
    result = _PROVIDER_KEY.sub(REDACTED, result)
    result = _URL_USERINFO.sub(rf"\1{REDACTED}@", result)
    return result


class LLMError(RuntimeError):
    """Sanitized inference failure with a stable ``code``.

    The message is scrubbed and length-bounded here - the single place where an
    inference error string is built - so no subclass or caller can leak a
    credential by forgetting to sanitize.
    """

    def __init__(self, code: str, message: str) -> None:
        safe = redact(message)
        if len(safe) > MAX_MESSAGE_CHARS:
            safe = safe[:MAX_MESSAGE_CHARS] + "..."
        super().__init__(f"{code}: {safe}")
        self.code = code
        self.message = safe


class LLMNotConfiguredError(LLMError):
    """The mandatory self-hosted route is unusable, so nothing was sent.

    Covers blank endpoint/model settings, an empty origin allowlist and a
    base URL whose origin is not on that allowlist (including cloud
    endpoints - they are never valid entries for the mandatory route)."""

    def __init__(self, message: str = "self-hosted inference is not configured") -> None:
        super().__init__(CODE_NOT_CONFIGURED, message)


class LLMUnavailableError(LLMError):
    """The self-hosted endpoint could not serve this request (transport error,
    HTTP 4xx/5xx or a redirect). Detail carries status classes only."""

    def __init__(self, message: str = "self-hosted inference endpoint unavailable") -> None:
        super().__init__(CODE_UNAVAILABLE, message)


class LLMTimeoutError(LLMError):
    """The whole-call deadline expired. Terminal for this request."""

    def __init__(self, message: str = "self-hosted inference deadline exceeded") -> None:
        super().__init__(CODE_TIMEOUT, message)


class LLMProtocolError(LLMError):
    """The endpoint answered outside the frozen contract: unparsable payload,
    missing choices, malformed tool-call arguments, an unoffered tool name or a
    structured-output shape that is still wrong after the single repair round."""

    def __init__(
        self, message: str = "inference response violated the OpenAI-compatible contract"
    ) -> None:
        super().__init__(CODE_PROTOCOL, message)


# --------------------------------------------------------------------------- #
# Mandatory-route origin check (mirrors config.Settings semantics exactly)
# --------------------------------------------------------------------------- #


def endpoint_origin(url: str) -> str | None:
    """``scheme+host[:port]`` of ``url``, or ``None`` when the URL is unsafe.

    Unsafe means userinfo/credentials, a non-http(s) scheme, a missing host or
    a malformed/out-of-range port. The returned origin can never contain
    credentials, which is what makes it safe to compare and safe to keep in
    configuration - unlike the raw value."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    try:
        parts.port  # noqa: B018 - attribute access validates
    except ValueError:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def require_allowed_self_hosted_origin(url: str, allowed_origins: Sequence[str]) -> None:
    """Fail closed unless the URL's exact origin is present in ``allowed_origins``.

    Exact match only - no suffix, wildcard or subdomain matching. Messages never
    contain the URL itself (it may carry credentials) and name no host."""
    if not url or not url.strip():
        raise LLMNotConfiguredError("self-hosted endpoint URL is not configured")
    if endpoint_origin(url) is None:
        raise LLMNotConfiguredError(
            "self-hosted endpoint URL is unsafe: an absolute http(s) URL without "
            "credentials, query or fragment is required"
        )
    if not allowed_origins:
        raise LLMNotConfiguredError(
            "self-hosted origin allowlist is empty; refusing to route inference"
        )
    if endpoint_origin(url) not in allowed_origins:
        raise LLMNotConfiguredError(
            "self-hosted endpoint origin is not in the configured allowlist"
        )


# --------------------------------------------------------------------------- #
# Canonical dotted tool names <-> OpenAI function-calling wire names
# --------------------------------------------------------------------------- #

_WIRE_SEPARATOR = "__"


def wire_tool_name(name: str) -> str:
    """Canonical dotted registry name (``calendar.update_agenda``) to the
    conservative ``[A-Za-z0-9_-]`` spelling OpenAI-compatible servers accept.

    EVA's frozen registry uses single-dot names, so this mapping is injective
    for every tool that exists; it never truncates or hashes a name (a silently
    renamed tool would break approval binding)."""
    return name.strip().replace(".", _WIRE_SEPARATOR)


def canonical_tool_name(wire_name: str) -> str:
    """Inverse of :func:`wire_tool_name`. Idempotent for names that arrive
    already dotted, so a server that echoes the canonical spelling still
    resolves to the canonical tool."""
    return wire_name.strip().replace(_WIRE_SEPARATOR, ".")
