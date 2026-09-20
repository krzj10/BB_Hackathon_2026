"""faster-whisper adapter (A05 default STT provider).

Lifecycle: ONE model instance per application/provider, loaded lazily and
reused - never per request. The model factory is injectable so tests never
import the real package.

RUNTIME MODEL LOADING IS LOCAL-FILES-ONLY: the default factory passes
``local_files_only=True``, so a missing local model fails fast as an
unavailable provider instead of silently downloading from the Hub. A human
must prepare/download and cache the model before rehearsal; application code
never performs model acquisition.

faster-whisper inference is BLOCKING native code: it always runs via
``asyncio.to_thread`` so the FastAPI event loop stays responsive.
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any, Callable

from app.contracts.domain import AudioInput, HealthStatus, Transcript
from app.contracts.providers import ProviderHealth

from .language import normalize_language_tag
from .stt_base import ProviderUnavailableError, TranscriptionFailedError

logger = logging.getLogger("eva.voice.faster_whisper")

PROVIDER_NAME = "faster-whisper"


def _default_model_factory(model_name: str, device: str, compute_type: str) -> Any:
    """Import happens HERE only (module import must not require the package).

    ``local_files_only=True`` is the runtime guarantee: a model that is not
    already present on this machine produces a load failure (sanitized
    unavailable state) - NEVER an implicit network download. Model acquisition
    belongs to human setup, not to application startup or request handling."""
    from faster_whisper import WhisperModel  # deferred: heavy native wheel

    return WhisperModel(
        model_name, device=device, compute_type=compute_type, local_files_only=True
    )


class FasterWhisperProvider:
    def __init__(
        self,
        *,
        model_name: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
        model_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._factory = model_factory or (
            lambda: _default_model_factory(model_name, device, compute_type)
        )
        self._model: Any | None = None
        self._load_failed = False

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    # ------------------------------------------------------------------ #
    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        if self._load_failed:
            raise ProviderUnavailableError("model could not be loaded")
        try:
            self._model = self._factory()
        except Exception as exc:
            # Class name only - factory exceptions may embed local paths.
            logger.error("whisper model load failed (%s)", type(exc).__name__)
            self._load_failed = True
            raise ProviderUnavailableError("model could not be loaded") from None
        return self._model

    def _run_blocking(self, audio: AudioInput, language: str | None) -> tuple[str, Any, Any]:
        model = self._ensure_model()
        # Starting inference configuration per the plan: beam_size=1, VAD on.
        segments, info = model.transcribe(
            io.BytesIO(audio.wav_bytes),
            language=language,       # None => multilingual auto-detect
            beam_size=1,
            vad_filter=True,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return text, getattr(info, "language", None), getattr(info, "language_probability", None)

    async def transcribe(self, audio: AudioInput, language: str | None = None) -> Transcript:
        hint = normalize_language_tag(language)
        try:
            text, detected, probability = await asyncio.to_thread(
                self._run_blocking, audio, hint
            )
        except ProviderUnavailableError:
            raise
        except Exception as exc:  # native failures sanitized to class name only
            logger.error("whisper inference failed (%s)", type(exc).__name__)
            raise TranscriptionFailedError() from None
        return Transcript(
            text=text,
            language=normalize_language_tag(detected) or "und",
            # The provider's own probability is carried through unchanged.
            language_confidence=float(probability) if probability is not None else None,
            duration_ms=0,  # measured authoritatively by TranscriptionService
            provider=PROVIDER_NAME,
        )

    async def warmup(self) -> None:
        await asyncio.to_thread(self._ensure_model)

    async def health(self) -> ProviderHealth:
        if self._model is not None:
            return ProviderHealth(
                status=HealthStatus.READY, provider=PROVIDER_NAME, model=self._model_name
            )
        if self._load_failed:
            return ProviderHealth(
                status=HealthStatus.UNAVAILABLE,
                detail="model could not be loaded",
                provider=PROVIDER_NAME,
                model=self._model_name,
            )
        return ProviderHealth(
            status=HealthStatus.DEGRADED,
            detail="model not warmed yet",
            provider=PROVIDER_NAME,
            model=self._model_name,
        )
