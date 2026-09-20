"""ASGI request-body ingress limit for the voice upload endpoint (Core fix).

Starlette parses/spools multipart content BEFORE the route handler runs, so
the handler-side bounded ``audio.read()`` loop alone cannot stop a huge HTTP
request from consuming parser/temporary-file resources. This middleware caps
the RAW REQUEST BODY before any multipart parsing happens:

- Content-Length present and above the cap -> immediate 413, body never read;
- absent/chunked -> bytes are counted as they arrive and the request is
  terminated with 413 the moment the cap is exceeded.

TWO deliberate boundaries stay in place: this ingress cap (audio file plus a
SMALL fixed allowance for multipart framing, field parts and headers) and the
exact 10 MiB AUDIO-file cap enforced inside the handler against decoded bytes.
Responses are generic - request body content is never echoed or logged."""

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


class VoiceIngressLimiter:
    """Pure-ASGI middleware guarding POST /api/voice/transcribe."""

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

        buffered = bytearray()
        disconnected = False
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
                break
            buffered.extend(message.get("body") or b"")
            if len(buffered) > self.max_body_bytes:
                # Counted over the cap mid-stream (chunked / lying headers):
                # terminate now, before multipart parsing sees the body.
                logger.warning("voice ingress rejected while streaming (count only)")
                await self._reject(send)
                return
            if not message.get("more_body", False):
                break

        if disconnected:
            return  # client vanished; nothing to answer and nothing parsed.

        messages = [
            {"type": "http.request", "body": bytes(buffered), "more_body": False},
            {"type": "http.disconnect"},
        ]
        index = 0

        async def replay():
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            return {"type": "http.disconnect"}

        await self.app(scope, replay, send)

    # ------------------------------------------------------------------ #
    def _declared_length(self, scope) -> int | None:
        for name, value in scope.get("headers") or []:
            if name == b"content-length":
                try:
                    return int(value)
                except ValueError:
                    return None  # unparseable: fall back to stream counting
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
