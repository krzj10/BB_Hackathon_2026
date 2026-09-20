"""Audio normalization boundary (A05).

ONE decode step turns private browser bytes into the canonical
``AudioInput`` (16 kHz mono PCM s16le WAV) that BOTH providers consume -
providers never decode uploads themselves.

Safety boundaries enforced here (never trusted from filenames or headers):

* raw upload size bounded at 10 MiB (chunked reads; stop at limit + 1);
* decoding itself is bounded: ffmpeg decodes AT MOST 31 s of audio, so a tiny
  compressed file containing hours cannot burn unbounded CPU;
* the resulting canonical WAV is VERIFIED (rate/channels/PCM/format/non-empty)
  - ffmpeg exit status alone is never trusted;
* decoded duration must be <= 30 s;
* deterministic silence detection rejects near-zero PCM BEFORE any expensive
  provider call;
* temporary files exist only for the duration of one request and are removed
  on every path; nothing audio-related is ever persisted or logged.

The ffmpeg runner is injectable so tests stay hermetic (no real binary).
"""

from __future__ import annotations

import array
import io
import logging
import os
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from typing import Callable, Sequence

from app.contracts.domain import AudioInput

logger = logging.getLogger("eva.voice.audio")

MAX_UPLOAD_BYTES = 10 * 1024 * 1024          # 10 MiB raw multipart audio
CHUNK_SIZE = 64 * 1024
MAX_DURATION_SECONDS = 30.0                  # accepted recording length
DECODE_CAP_SECONDS = 31.0                    # decode slightly past the window, then reject

#: int16 full-scale peak below which audio is classified as (near-)silence.
#: Deliberately conservative: deterministic all-zero input is always silent,
#: while quiet real speech (peaks in the hundreds/thousands) passes.
SILENCE_PEAK_THRESHOLD = 64

#: Browser MediaRecorder / common container hints. A MIME allowlist HINT only -
#: never proof; undecodable bytes under an allowed type are a 422 at decode.
SUPPORTED_MIME_TYPES = frozenset(
    {"audio/webm", "audio/ogg", "audio/wav", "audio/x-wav", "audio/mp4"}
)


class AudioInputError(Exception):
    """Stable sanitized audio-input failure. ``code`` drives the HTTP mapping;
    no provider output, path or byte content ever appears in the message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RunnerResult:
    returncode: int


def _default_ffmpeg_runner(command: Sequence[str], timeout_seconds: float) -> RunnerResult:
    """Real runner: argument array only (NEVER shell=True), stdin detached by
    -nostdin upstream, output captured but DISCARDED (stderr is never logged
    or returned)."""
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        list(command),
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    return RunnerResult(returncode=completed.returncode)


def mime_of(content_type: str | None) -> str:
    return (content_type or "").split(";")[0].strip().lower()


def is_supported_mime(content_type: str | None) -> bool:
    return mime_of(content_type) in SUPPORTED_MIME_TYPES


def validate_canonical_wav(wav_bytes: bytes) -> AudioInput:
    """Verify the decoder produced exactly the canonical format; build the
    frozen AudioInput. Never trust decoder success alone."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            channels, rate, width, frames = (
                wav.getnchannels(), wav.getframerate(), wav.getsampwidth(), wav.getnframes(),
            )
            comptype = wav.getcomptype()
    except Exception:  # unreadable/no WAV - provider bytes never echoed
        raise AudioInputError("audio_decode_failed", "audio could not be decoded") from None
    if channels != 1 or rate != 16000 or width != 2 or comptype != "NONE":
        raise AudioInputError("audio_decode_failed", "decoded audio is not canonical PCM")
    if frames <= 0:
        raise AudioInputError("audio_empty", "audio contains no samples")
    duration = frames / float(rate)
    if duration > MAX_DURATION_SECONDS + 1e-6:
        raise AudioInputError(
            "audio_too_long", f"recording exceeds {int(MAX_DURATION_SECONDS)} seconds"
        )
    return AudioInput(wav_bytes=wav_bytes, sample_rate_hz=16000, channels=1)


def is_silent(audio: AudioInput) -> bool:
    """Deterministic near-zero PCM detection on the canonical WAV."""
    try:
        with wave.open(io.BytesIO(audio.wav_bytes), "rb") as wav:
            raw = wav.readframes(wav.getnframes())
    except Exception:
        return False  # validation already gates format; not our business here
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
    if not samples:
        return True
    peak = 0
    for value in samples:
        magnitude = -value if value < 0 else value
        if magnitude > peak:
            peak = magnitude
    return peak < SILENCE_PEAK_THRESHOLD


class FfmpegAudioNormalizer:
    """Decode any supported browser upload to canonical AudioInput via ffmpeg.

    The runner and binary are injectable/config-set; no user-controlled
    fragments ever enter the argv (upload bytes go through a temp FILE, never
    the command line)."""

    def __init__(
        self,
        *,
        ffmpeg_binary: str = "ffmpeg",
        decode_timeout_seconds: float = 15.0,
        runner: Callable[[Sequence[str], float], RunnerResult] | None = None,
    ) -> None:
        self._binary = ffmpeg_binary
        self._timeout = decode_timeout_seconds
        self._runner = runner or _default_ffmpeg_runner

    def normalize(self, content_type: str | None, data: bytes) -> AudioInput:
        if not is_supported_mime(content_type):
            raise AudioInputError("unsupported_mime_type", "audio format not supported")
        if not data:
            raise AudioInputError("audio_empty", "no audio was uploaded")

        in_fd, in_path = tempfile.mkstemp(prefix="eva-audio-in-", suffix=".bin")
        out_path = in_path + ".wav"
        try:
            with os.fdopen(in_fd, "wb") as handle:
                handle.write(data)
            command = [
                self._binary,
                "-nostdin",
                "-loglevel", "error",
                "-y",
                "-i", in_path,
                "-t", str(DECODE_CAP_SECONDS),   # bounded decode window
                "-vn",                            # drop any video stream
                "-ac", "1",                       # mono
                "-ar", "16000",                   # 16 kHz
                "-c:a", "pcm_s16le",              # canonical PCM for both providers
                "-f", "wav",
                out_path,
            ]
            try:
                result = self._runner(command, self._timeout)
            except subprocess.TimeoutExpired:
                logger.error("audio decode timed out")
                raise AudioInputError("audio_decode_failed", "audio decoding timed out") from None
            except FileNotFoundError:
                # ffmpeg missing on this machine: an input-processing failure
                # here (we never got provider-side); detail stays sanitized.
                logger.error("audio decoder binary not available")
                raise AudioInputError("audio_decode_failed", "audio decoder unavailable") from None
            except Exception as exc:
                logger.error("audio decode runner failed (%s)", type(exc).__name__)
                raise AudioInputError("audio_decode_failed", "audio could not be decoded") from None
            if result.returncode != 0:
                # Only the numeric exit code is logged - never stderr text.
                logger.error("audio decoder exited with code %s", result.returncode)
                raise AudioInputError("audio_decode_failed", "audio could not be decoded")

            try:
                with open(out_path, "rb") as handle:
                    wav_bytes = handle.read()
            except OSError:
                raise AudioInputError("audio_decode_failed", "decoded audio is missing") from None
            audio = validate_canonical_wav(wav_bytes)
        finally:
            for path in (in_path, out_path):
                try:
                    os.remove(path)
                except OSError:
                    pass  # already gone; nothing to clean

        if is_silent(audio):
            raise AudioInputError("audio_silent", "no speech detected in the recording")
        return audio
