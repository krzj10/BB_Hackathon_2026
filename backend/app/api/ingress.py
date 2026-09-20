"""ASGI request-body ingress limit for the voice upload endpoint (Core fix).

Starlette parses/spools multipart content BEFORE the route handler runs, so
the handler-side bounded ``audio.read()`` loop alone cannot stop a huge HTTP
request from consuming parser/temporary-file resources. This middleware caps
the RAW REQUEST BODY before any multipart parsing happens:

- Content-Length present and above the cap -> immediate 413, body never read;
- absent/chunked/untrusted -> bytes are counted AS THEY STREAM through a
  receive() wrapper and the request is terminated with 413 the moment the
  cumulative count exceeds the cap.

STREAMING (final hardening): valid requests are forwarded chunk-for-chunk -
the ORIGINAL ASGI messages pass through untouched; this middleware never
accumulates or copies the body into its own buffer. Counting is additive only.

TWO deliberate boundaries stay in place: this ingress cap (audio file plus a
SMALL fixed allowance for multipart framing, field parts and headers) and the
exact 10 MiB AUDIO-file cap enforced inside the handler against decoded bytes.
Content-Length is never the sole enforcement mechanism - malformed or negative
headers fall back to stream counting. Responses are generic: request body
content is never echoed or logged."""

from __future__ import annotations

import logging

from ..voice.audio import MAX_UPLOAD_BYTES

logger = logging.getLogger("eva.api.ingress")

#: Bounded multipart-overhead allowance: framing boundaries, the request_id /
#: language field parts and headers. Deliberately small; the audio cap itself
#: stays exactly MAX_UPLOAD_BYTES inside the handler.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
MAX_MULTIPART_REQUEST_BYTES = MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES

VOICE_TRANSCRIBE_PATH = "/api/voice/transcribe"

_REJECT_BODY = b'{"detail":"request_body_too_large"}'


class _RequestBodyTooLarge(BaseException):
    """Internal control-flow signal raised by the counting receive wrapper.

    Inherits BaseException deliberately: modern Starlette consumes the body
    inside an anyio task group, which wraps ordinary Exceptions into
    ExceptionGroups; a BaseException crosses structured-concurrency frames
    unwrapped. The limiter still defensively unwraps groups and exception
    chains around the downstream call."""


def _is_body_too_large(exc: BaseException) -> bool:
    if isinstance(exc, _RequestBodyTooLarge):
        return True
    for sub in getattr(exc, "exceptions", ()) or ():  # (Base)ExceptionGroup
        if isinstance(sub, BaseException) and _is_body_too_large(sub):
            return True
    inner = exc.__cause__ if exc.__cause__ is not None else exc.__context__
    hops = 0
    while inner is not None and hops < 20:
        if isinstance(inner, _RequestBodyTooLarge):
            return True
        inner = inner.__cause__ if inner.__cause__ is not None else inner.__context__
        hops += 1
    return False


class VoiceIngressLimiter:
    """Pure-ASGI STREAMING body cap guarding POST /api/voice/transcribe."""

    def __init__(
        self,
        app,
        *,
        path: str = VOICE_TRANSCRIBE_PATH,
        max_body_bytes: int = MAX_MULTIPART_REQUEST_BYTES,
    ) -> None:
        self.app = app
        self.path = path
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != self.path
        ):
            await self.app(scope, receive, send)
            return

        declared = self._declared_length(scope)
        if declared is not None and declared > self.max_body_bytes:
            # Clearly over the cap before a single body byte is read.
            logger.warning("voice ingress rejected by declared length (count only)")
            await self._reject(send)
            return

        state = {"counted": 0, "over": False, "started": False}

        async def counting_receive():
            if state["over"]:  # already tripped: never resurrect the stream
                raise _RequestBodyTooLarge()
            message = await receive()
            if message.get("type") == "http.request":
                state["counted"] += len(message.get("body") or b"")
                if state["counted"] > self.max_body_bytes:
                    # Counted over the cap mid-stream (chunked / lying
                    # headers): abort now, before multipart parsing continues.
                    state["over"] = True
                    logger.warning("voice ingress rejected while streaming (count only)")
                    raise _RequestBodyTooLarge()
            return message  # forwarded UNCHANGED: no copy, no concatenation

        async def lifecycle_send(message):
            if message.get("type") == "http.response.start":
                state["started"] = True
            await send(message)

        try:
            await self.app(scope, counting_receive, lifecycle_send)
        except BaseException as exc:
            if not _is_body_too_large(exc):
                raise
            if state["started"]:
                # A response is already in flight; ASGI forbids a second one.
                # Fail closed: end the exchange without inventing a response,
                # logging counts only.
                logger.error("voice ingress over cap after response start (count only)")
                return
            await self._reject(send)

    # ------------------------------------------------------------------ #
    def _declared_length(self, scope) -> int | None:
        for name, value in scope.get("headers") or []:
            if name == b"content-length":
                try:
                    parsed = int(value)
                except ValueError:
                    return None  # unparseable: stream counting decides
                return parsed if parsed >= 0 else None  # negative: distrust
        return None

    async def _reject(self, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_REJECT_BODY)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _REJECT_BODY})
