"""whisper.cpp CLI adapter (A05 local fallback provider).

Boundary: a configured executable + model path, invoked as an argument ARRAY
(never shell=True) against a temporary canonical WAV produced by the shared
normalizer. Blank configuration means UNAVAILABLE - never fake success.
Subprocess stderr and filesystem paths are never returned or logged.

The command runner is injectable so deterministic tests exercise command
shape, output parsing, failures and timeouts without any binary or model.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Sequence

from app.contracts.domain import AudioInput, HealthStatus, Transcript
from app.contracts.providers import ProviderHealth

from .language import normalize_language_tag
from .stt_base import (
    ProviderUnavailableError,
    TranscriptionFailedError,
    TranscriptionTimeoutError,
)

logger = logging.getLogger("eva.voice.whisper_cpp")

PROVIDER_NAME = "whisper.cpp"


class RunnerResult:
    __slots__ = ("returncode", "stdout")

    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


def _default_runner(command: Sequence[str], timeout_seconds: float) -> RunnerResult:
    completed = subprocess.run(  # noqa: S603 - fixed argv array, no shell
        list(command), capture_output=True, text=True, timeout=timeout_seconds, check=False
    )
    return RunnerResult(returncode=completed.returncode, stdout=completed.stdout or "")


class WhisperCppProvider:
    def __init__(
        self,
        *,
        binary: str | None = None,
        model_path: str | None = None,
        timeout_seconds: float = 30.0,
        runner: Callable[[Sequence[str], float], RunnerResult] | None = None,
    ) -> None:
        self._binary = (binary or "").strip()
        self._model_path = (model_path or "").strip()
        self._timeout = timeout_seconds
        self._runner = runner or _default_runner

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    def _configured(self) -> bool:
        if not self._binary or not self._model_path:
            return False
        # The MODEL file must exist (it is user-prepared); the binary itself is
        # resolved by the runner at call time (fake runners skip PATH checks).
        return Path(self._model_path).is_file()

    def _command(self, wav_path: str, language: str | None) -> list[str]:
        tag = normalize_language_tag(language) or "auto"
        return [
            self._binary,
            "-m", self._model_path,
            "-f", wav_path,
            "-l", tag,
            "-nt",  # plain text, no timestamps
        ]

    def _run_blocking(self, audio: AudioInput, language: str | None) -> tuple[str, str]:
        fd, path = tempfile.mkstemp(prefix="eva-wcpp-", suffix=".wav")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(audio.wav_bytes)  # the SAME canonical input; no re-decode
            command = self._command(path, language)
            try:
                result = self._runner(command, self._timeout)
            except subprocess.TimeoutExpired:
                raise TranscriptionTimeoutError() from None
            except FileNotFoundError:
                raise ProviderUnavailableError("whisper.cpp binary not found") from None
            except Exception as exc:
                logger.error("whisper.cpp runner failed (%s)", type(exc).__name__)
                raise TranscriptionFailedError() from None
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        if result.returncode != 0:
            # Numeric exit code only; raw stderr is never kept, logged or sent.
            logger.error("whisper.cpp exited with code %s", result.returncode)
            raise TranscriptionFailedError()
        text = _extract_text(result.stdout)
        return text, normalize_language_tag(language) or "auto"

    async def transcribe(self, audio: AudioInput, language: str | None = None) -> Transcript:
        if not self._configured():
            raise ProviderUnavailableError("whisper.cpp is not configured on this machine")
        try:
            text, used_language = await asyncio.to_thread(self._run_blocking, audio, language)
        except (ProviderUnavailableError, TranscriptionFailedError, TranscriptionTimeoutError):
            raise
        except Exception as exc:
            logger.error("whisper.cpp invocation failed (%s)", type(exc).__name__)
            raise TranscriptionFailedError() from None
        return Transcript(
            text=text,
            # The CLI reports no detection probability for auto runs; the
            # language is either the requested tag or undetermined - never a
            # fabricated confidence.
            language=used_language if used_language != "auto" else "und",
            language_confidence=None,
            duration_ms=0,  # measured authoritatively by TranscriptionService
            provider=PROVIDER_NAME,
        )

    async def health(self) -> ProviderHealth:
        if not self._binary or not self._model_path:
            return ProviderHealth(
                status=HealthStatus.UNAVAILABLE,
                detail="whisper.cpp binary/model not configured",
                provider=PROVIDER_NAME,
            )
        if not Path(self._model_path).is_file():
            return ProviderHealth(
                status=HealthStatus.UNAVAILABLE,
                detail="whisper.cpp model file not present",
                provider=PROVIDER_NAME,
            )
        return ProviderHealth(status=HealthStatus.OK, provider=PROVIDER_NAME)


#: whisper.cpp prints informational lines to stdout before the transcript.
_INFO_PREFIXES = (
    "load_backend", "system_info", "whisper_init", "whisper_load", "main:",
    "whisper_model", "print_internal", "%",  # progress like 12% -> line start
)


def _extract_text(stdout: str) -> str:
    """Keep transcript lines, drop informational/progress noise. Raw provider
    output is never logged; only the joined text survives."""
    lines: list[str] = []
    for line in (stdout or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if any(lowered.startswith(prefix) for prefix in _INFO_PREFIXES):
            continue
        lines.append(stripped)
    return " ".join(lines).strip()
