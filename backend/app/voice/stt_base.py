"""STT orchestration (A05): primary/fallback selection, bounded concurrency,
timeout, language resolution - one service layer, no competing contracts.

Concurrency model: an ``asyncio.Semaphore`` bounds SIMULTANEOUS provider
calls. Known limitation (accepted deliberately per the plan): cancelling a
timed-out ``asyncio.to_thread`` does NOT terminate native inference
immediately - the worker may keep computing after the permit is released.
That is exactly why the default concurrency stays conservative (1) and the
timeout exists to free the CALLER even when the native call cannot be freed.

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
        """Run the primary provider; on unavailability/failure/timeout try
        the known local fallback EXACTLY ONCE with the SAME normalized
        AudioInput (never re-normalized, never a fabricated transcript)."""
        chain = [p for p in (self._primary, self._fallback) if p is not None]
        if not chain:
            raise ProviderUnavailableError("no speech-to-text provider configured")

        last_error: SttError | None = None
        for index, provider in enumerate(chain):
            try:
                transcript = await self._attempt(provider, audio, language_hint)
            except (ProviderUnavailableError, TranscriptionFailedError, TranscriptionTimeoutError) as exc:
                # Never log provider text - only position and stable code.
                logger.error("stt attempt %d failed (%s)", index + 1, exc.code)
                # Keep the MOST informative failure: timeout > failure >
                # unavailable, independent of fallback ordering.
                if last_error is None or self._severity(exc) >= self._severity(last_error):
                    last_error = exc
                continue
            return self._finalize(transcript, language_hint)
        raise last_error or ProviderUnavailableError()

    @staticmethod
    def _severity(error: SttError) -> int:
        if isinstance(error, TranscriptionTimeoutError):
            return 3
        if isinstance(error, TranscriptionFailedError):
            return 2
        return 1

    async def _attempt(
        self, provider: SpeechToTextProvider, audio: AudioInput, language_hint: str | None
    ) -> Transcript:
        async with self._semaphore:
            started = self._clock()
            try:
                transcript = await asyncio.wait_for(
                    provider.transcribe(audio, language_hint), timeout=self._timeout
                )
            except asyncio.TimeoutError as exc:
                raise TranscriptionTimeoutError() from exc
            elapsed_ms = max(0, int((self._clock() - started) * 1000))
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
        if primary is not None and primary.status is HealthStatus.OK:
            return ProviderHealth(
                status=HealthStatus.OK, provider=primary.provider, model=primary.model
            )
        if fallback is not None and fallback.status is HealthStatus.OK:
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
