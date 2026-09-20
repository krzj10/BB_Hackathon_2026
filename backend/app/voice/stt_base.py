"""STT orchestration (A05): primary/fallback selection, bounded concurrency,
timeout, language resolution - one service layer, no competing contracts.

Concurrency model: an ``asyncio.Semaphore`` provides inference LEASES. A
lease is released only when the provider task ACTUALLY finishes - on timeout
or caller cancellation the shielded task keeps running and keeps its lease
(done-callback releases it exactly once). Native inference cannot reliably be
interrupted, so this is what makes ``EVA_STT_MAX_CONCURRENCY`` a true bound;
the default therefore stays conservative (1), and a timed-out request must
retry later once capacity frees up.

Duration: ``Transcript.duration_ms`` is measured by this service with a
MONOTONIC clock around the provider attempt - never wall-clock timestamps.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from app.contracts.domain import AudioInput, HealthStatus, Transcript
from app.contracts.providers import ProviderHealth, SpeechToTextProvider

from .language import resolve_language

logger = logging.getLogger("eva.voice.stt")


class SttError(Exception):
    """Sanitized orchestration failure with a stable code for HTTP mapping."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ProviderUnavailableError(SttError):
    def __init__(self, message: str = "speech-to-text provider unavailable") -> None:
        super().__init__("stt_unavailable", message)


class TranscriptionFailedError(SttError):
    def __init__(self, message: str = "transcription failed") -> None:
        super().__init__("transcription_failed", message)


class TranscriptionTimeoutError(SttError):
    def __init__(self, message: str = "transcription timed out") -> None:
        super().__init__("transcription_timeout", message)


class NoSpeechDetectedError(SttError):
    """A provider answered successfully but produced empty/whitespace text.
    Treated as unsuccessful recognition (fallback allowed once); if no
    provider yields non-empty text this is an input-quality 422, never a
    fabricated 200."""

    def __init__(self, message: str = "no speech was recognized") -> None:
        super().__init__("no_speech_detected", message)


def canonicalize_stt_provider_name(raw: str | None) -> str:
    """Map configured spellings to the two supported identities; unknown
    values are REJECTED clearly (never silently defaulted)."""
    token = (raw or "").strip().lower().replace("_", "-")
    if token in {"faster-whisper", "fasterwhisper"}:
        return "faster-whisper"
    if token in {"whisper.cpp", "whisper-cpp", "whispercpp"}:
        return "whisper.cpp"
    raise ValueError(
        f"unsupported EVA_STT_PROVIDER {raw!r}; expected 'faster-whisper' or 'whisper.cpp'"
    )


class TranscriptionService:
    def __init__(
        self,
        *,
        primary: SpeechToTextProvider | None,
        fallback: SpeechToTextProvider | None = None,
        max_concurrency: int = 1,
        timeout_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        self._primary = primary
        self._fallback = fallback
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._timeout = timeout_seconds
        self._clock = clock

    # ------------------------------------------------------------------ #
    async def transcribe(self, audio: AudioInput, language_hint: str | None = None) -> Transcript:
        """Transcribe with AUTOMATIC language detection on the providers.

        The optional ``language_hint`` is a PRIOR CONVERSATION LANGUAGE used
        only after transcription to resolve ambiguous short approvals - it is
        never passed into provider inference (a confident English sentence
        spoken in a Polish conversation must stay English).

        Fallback policy: primary unavailability or an ordinary recognition
        failure (including an empty transcript) tries the known local
        fallback EXACTLY ONCE with the SAME normalized AudioInput. A TIMEOUT
        IS TERMINAL for this request - the primary native worker may still be
        running and holds its inference permit, so starting a fallback would
        defeat the concurrency boundary."""
        chain = [p for p in (self._primary, self._fallback) if p is not None]
        if not chain:
            raise ProviderUnavailableError("no speech-to-text provider configured")

        last_error: SttError | None = None
        for index, provider in enumerate(chain):
            try:
                transcript = await self._attempt(provider, audio)
            except TranscriptionTimeoutError:
                logger.error("stt attempt %d timed out", index + 1)
                raise  # terminal: no fallback while native work may continue
            except (ProviderUnavailableError, TranscriptionFailedError, NoSpeechDetectedError) as exc:
                # Never log provider text - only position and stable code.
                logger.error("stt attempt %d failed (%s)", index + 1, exc.code)
                # Keep the MOST informative failure: no-speech > failure >
                # unavailable, independent of fallback ordering.
                if last_error is None or self._severity(exc) >= self._severity(last_error):
                    last_error = exc
                continue
            return self._finalize(transcript, language_hint)
        raise last_error or ProviderUnavailableError()

    @staticmethod
    def _severity(error: SttError) -> int:
        if isinstance(error, NoSpeechDetectedError):
            return 3
        if isinstance(error, TranscriptionFailedError):
            return 2
        return 1

    async def _attempt(self, provider: SpeechToTextProvider, audio: AudioInput) -> Transcript:
        """One inference attempt holding the concurrency LEASE honestly.

        The permit is acquired BEFORE the provider task starts and released
        only when that task ACTUALLY finishes - never when the caller gives
        up. On timeout (or caller cancellation/disconnect) the shielded task
        keeps running with its permit; a done-callback releases it exactly
        once and consumes any background exception so no unhandled-task
        warnings appear. This is what makes ``max_concurrency`` a real bound
        even across timeouts, where cancelling asyncio plumbing does NOT stop
        native inference threads.

        Providers always receive ``language=None``: automatic detection."""
        await self._semaphore.acquire()
        task = asyncio.create_task(provider.transcribe(audio, None))
        released = False

        def _release(_finished: asyncio.Task) -> None:
            nonlocal released
            if not released:
                released = True
                self._semaphore.release()
            if not _finished.cancelled():
                # Retrieve (and silence) any background exception.
                exc = _finished.exception()
                if exc is not None:
                    logger.error("stt background task ended with (%s)", type(exc).__name__)

        task.add_done_callback(_release)
        started = self._clock()
        try:
            transcript = await asyncio.wait_for(asyncio.shield(task), timeout=self._timeout)
        except asyncio.TimeoutError as exc:
            # Permit intentionally stays held until the native worker exits.
            raise TranscriptionTimeoutError() from exc
        elapsed_ms = max(0, int((self._clock() - started) * 1000))
        if not transcript.text or not transcript.text.strip():
            raise NoSpeechDetectedError()
        # duration_ms is OUR measured provider elapsed time (monotonic).
        return transcript.model_copy(update={"duration_ms": elapsed_ms})

    def _finalize(self, transcript: Transcript, language_hint: str | None) -> Transcript:
        language, confidence = resolve_language(
            detected=transcript.language,
            confidence=transcript.language_confidence,
            conversation_hint=language_hint,
            text=transcript.text,
        )
        # The frozen contract requires a BCP-47 tag; "und" (undetermined) is
        # the honest value when neither detection nor hint resolves one.
        return transcript.model_copy(
            update={"language": language or "und", "language_confidence": confidence}
        )

    # ------------------------------------------------------------------ #
    async def health(self) -> ProviderHealth:
        """Aggregated sanitized readiness (no paths, exception text or raw
        provider detail beyond the providers' own sanitized messages)."""
        primary = await self._health_of(self._primary)
        fallback = await self._health_of(self._fallback)
        if primary is not None and primary.status is HealthStatus.READY:
            return ProviderHealth(
                status=HealthStatus.READY, provider=primary.provider, model=primary.model
            )
        if fallback is not None and fallback.status is HealthStatus.READY:
            return ProviderHealth(
                status=HealthStatus.DEGRADED,
                detail="primary unavailable; local fallback ready",
                provider=fallback.provider,
                model=fallback.model,
            )
        return ProviderHealth(status=HealthStatus.UNAVAILABLE, detail="no STT provider ready")

    async def _health_of(self, provider: SpeechToTextProvider | None) -> ProviderHealth | None:
        if provider is None:
            return None
        try:
            return await provider.health()
        except Exception as exc:  # a broken health() must never break the API
            logger.error("stt health probe failed (%s)", type(exc).__name__)
            return ProviderHealth(status=HealthStatus.UNAVAILABLE)

    async def warmup(self) -> None:
        """Best-effort startup warmup. Failures NEVER crash application
        startup: they are recorded as sanitized provider-unavailable state by
        the providers themselves; only a fixed message + exception class is
        logged."""
        for provider in (self._primary, self._fallback):
            if provider is None:
                continue
            warm = getattr(provider, "warmup", None)
            if warm is None:
                continue
            try:
                await warm()
            except Exception as exc:
                logger.error("stt warmup failed for a provider (%s)", type(exc).__name__)
