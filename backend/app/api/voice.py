"""Voice API (A05): POST /api/voice/transcribe.

Multipart contract for Stream B (exact stable field names):

    audio       UploadFile  - the recorded audio file
    request_id  form str    - non-empty client request id (echoed back)
    language    form str    - OPTIONAL prior conversation-language hint

Response: TranscribeResponse(request_id, transcript). The route stays thin:
transport/session/origin validation -> bounded upload read -> shared audio
normalizer -> STT service -> response. No provider logic lives here.

Privacy: the multipart body and audio bytes are never logged, persisted or
echoed - not even on error paths (only stable codes travel outward).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from ..contracts.api import TranscribeResponse
from ..voice.audio import (
    CHUNK_SIZE,
    MAX_UPLOAD_BYTES,
    AudioInputError,
    is_supported_mime,
)
from ..voice.stt_base import (
    ProviderUnavailableError,
    SttError,
    TranscriptionTimeoutError,
)
from .security import require_origin, require_session_header

logger = logging.getLogger("eva.api.voice")

router = APIRouter(prefix="/api/voice", tags=["voice"])

_AUDIO_STATUS = {
    "audio_too_large": 413,
    "unsupported_mime_type": 415,
    # Everything else below is an input-quality problem: 422.
}


def _normalizer(request: Request):
    normalizer = getattr(request.app.state, "audio_normalizer", None)
    if normalizer is None:
        raise HTTPException(status_code=503, detail="audio pipeline not initialized")
    return normalizer


def _service(request: Request):
    service = getattr(request.app.state, "stt_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="speech-to-text not initialized")
    return service


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(
    request: Request,
    audio: UploadFile = File(...),
    request_id: str = Form(...),
    language: str | None = Form(default=None),
) -> TranscribeResponse:
    require_origin(request)          # exact app-origin allowlist when present
    require_session_header(request)  # session identity required, no cookies
    rid = request_id.strip()
    if not rid:
        raise HTTPException(status_code=400, detail="request_id must not be empty")

    if not is_supported_mime(audio.content_type):
        raise HTTPException(status_code=415, detail="unsupported_mime_type")

    # Bounded incremental read: stop the moment the 10 MiB limit is passed -
    # arbitrarily large uploads are never buffered (Content-Length is not
    # trusted; this loop is the authority).
    buffer = bytearray()
    while True:
        chunk = await audio.read(CHUNK_SIZE)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="audio_too_large")
    if not buffer:
        raise HTTPException(status_code=422, detail="audio_empty")

    try:
        # Decode runs in a worker thread (subprocess boundary), never on the loop.
        normalized = await run_in_threadpool(
            _normalizer(request).normalize, audio.content_type, bytes(buffer)
        )
    except AudioInputError as exc:
        raise HTTPException(
            status_code=_AUDIO_STATUS.get(exc.code, 422), detail=f"{exc.code}: {exc.message}"
        ) from None

    try:
        transcript = await _service(request).transcribe(normalized, language_hint=language)
    except TranscriptionTimeoutError as exc:
        raise HTTPException(status_code=504, detail=f"{exc.code}: {exc.message}") from None
    except (ProviderUnavailableError, SttError) as exc:
        # Fixed sanitized messages only - no provider output or local paths.
        raise HTTPException(status_code=503, detail=f"{exc.code}: {exc.message}") from None

    return TranscribeResponse(request_id=rid, transcript=transcript)
