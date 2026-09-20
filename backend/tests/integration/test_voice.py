"""A05 integration tests: audio normalization + STT orchestration.

Hermetic by construction: an injected fake ffmpeg runner produces canonical
WAV payloads, faster-whisper runs against a fake model factory (NEVER the
real package/model), whisper.cpp against an injected command runner. No
network, no model downloads, no real binaries, synthetic PCM only.

Run: python -m pytest backend/tests/integration/test_voice.py -q
"""

from __future__ import annotations

import array
import asyncio
import io
import subprocess
import sys
import threading
import time
import types
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.contracts.domain import AudioInput, HealthStatus, Transcript
from app.main import create_app
from app.voice.audio import (
    MAX_UPLOAD_BYTES,
    AudioInputError,
    FfmpegAudioNormalizer,
    RunnerResult,
    validate_canonical_wav,
)
from app.voice.faster_whisper import FasterWhisperProvider
from app.voice.language import normalize_language_tag, resolve_language
from app.voice.stt_base import (
    ProviderUnavailableError,
    TranscriptionFailedError,
    TranscriptionService,
    TranscriptionTimeoutError,
    canonicalize_stt_provider_name,
)
from app.voice.whisper_cpp import WhisperCppProvider

SESSION = "sess-a05"


# --------------------------------------------------------------------------- #
# Synthetic audio helpers (generated in-memory; nothing committed)
# --------------------------------------------------------------------------- #


def make_wav(seconds: float = 2.0, level: int = 3000, rate: int = 16000) -> bytes:
    """Square-ish synthetic PCM s16le mono WAV at the canonical rate."""
    count = int(seconds * rate)
    samples = array.array("h")
    for index in range(count):
        samples.append(level if (index // 80) % 2 == 0 else -level)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())
    return buffer.getvalue()


class FakeFfmpegRunner:
    """Stands in for the ffmpeg subprocess: writes a configurable canonical
    WAV to the argv output path and records every command + touched paths."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.returncode = 0
        self.pcm_seconds = 2.0
        self.level = 3000
        self.garbage_output = False
        self.touched_paths: list[str] = []

    def __call__(self, command, timeout):
        self.commands.append(list(command))
        out_path = command[-1]
        self.touched_paths.append(out_path)
        if self.returncode == 0:
            payload = (
                b"definitely-not-a-wav-file"
                if self.garbage_output
                else make_wav(self.pcm_seconds, self.level)
            )
            Path(out_path).write_bytes(payload)
        return RunnerResult(returncode=self.returncode)


class FakeSttProvider:
    """Async provider double tracking calls, concurrency and identity."""

    def __init__(self, name: str, *, text="Hello there", language="en", confidence=0.9,
                 block_seconds: float = 0.0):
        self.name = name
        self.text = text
        self.language = language
        self.confidence = confidence
        self.fail: str | None = None  # "unavailable" | "failed" | "block"
        self.block_seconds = block_seconds
        self.calls: list[tuple[bytes, str | None]] = []
        self.active = 0
        self.max_observed = 0
        self._lock = threading.Lock()

    async def transcribe(self, audio: AudioInput, language: str | None = None) -> Transcript:
        self.calls.append((audio.wav_bytes, language))  # recorded even when failing
        if self.fail == "unavailable":
            raise ProviderUnavailableError(f"{self.name} unavailable")
        if self.fail == "failed":
            raise TranscriptionFailedError(f"{self.name} inference failed")
        with self._lock:
            self.active += 1
            self.max_observed = max(self.max_observed, self.active)
        try:
            if self.block_seconds:
                await asyncio.to_thread(time.sleep, self.block_seconds)
            return Transcript(
                text=self.text, language=self.language or "und",
                language_confidence=self.confidence, duration_ms=0, provider=self.name,
            )
        finally:
            with self._lock:
                self.active -= 1

    async def health(self):
        from app.contracts.providers import ProviderHealth

        if self.fail == "unavailable":
            return ProviderHealth(status=HealthStatus.UNAVAILABLE, provider=self.name)
        return ProviderHealth(status=HealthStatus.READY, provider=self.name)


class Env(SimpleNamespace):
    pass


@pytest.fixture()
def env(tmp_path) -> Env:
    settings = Settings(
        eva_db_url=f"sqlite:///{tmp_path / 'a05.db'}",
        eva_app_allowed_origins=["http://app.local"],
        _env_file=None,
    )
    app = create_app(settings)
    runner = FakeFfmpegRunner()
    primary = FakeSttProvider("faster-whisper")
    fallback = FakeSttProvider("whisper.cpp", text="Fallback transcript")

    service = TranscriptionService(primary=primary, fallback=fallback, max_concurrency=1, timeout_seconds=5.0)
    app.state.audio_normalizer = FfmpegAudioNormalizer(ffmpeg_binary="ffmpeg-test", runner=runner)
    app.state.stt_service = service
    return Env(
        app=app, client=TestClient(app), runner=runner, primary=primary,
        fallback=fallback, service=service, tmp=tmp_path,
    )


def post_transcribe(env, *, data=b"fake-browser-bytes", mime="audio/webm", request_id="req-1",
                    language=None, session=SESSION, origin=None):
    form = {"request_id": request_id}
    if language is not None:
        form["language"] = language
    headers = {}
    if session:
        headers["X-EVA-Session-ID"] = session
    if origin:
        headers["Origin"] = origin
    return env.client.post(
        "/api/voice/transcribe",
        files={"audio": ("take.webm", data, mime)},
        data=form,
        headers=headers,
    )


# =========================================================================== #
# Happy path + transport metadata (PART 6, 7)
# =========================================================================== #


def test_valid_upload_returns_transcript_and_echoes_request_id(env) -> None:
    response = post_transcribe(env, request_id="req-echo")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["request_id"] == "req-echo"
    assert payload["transcript"]["provider"] == "faster-whisper"
    assert payload["transcript"]["text"] == "Hello there"
    assert payload["transcript"]["language"] == "en"


def test_missing_session_and_request_metadata_rejected(env) -> None:
    assert post_transcribe(env, session=None).status_code == 400
    blank = post_transcribe(env, request_id="   ")
    assert blank.status_code == 400
    # Missing form field entirely: framework-level validation (422/400).
    missing = env.client.post(
        "/api/voice/transcribe",
        files={"audio": ("take.webm", b"x", "audio/webm")},
        headers={"X-EVA-Session-ID": SESSION},
    )
    assert missing.status_code in (400, 422)


def test_origin_guard(env) -> None:
    assert post_transcribe(env, origin="http://evil.example").status_code == 403
    assert post_transcribe(env, origin="http://app.local").status_code == 200


# =========================================================================== #
# Upload / duration / format bounds (PART 8-10, 13, 15, 25)
# =========================================================================== #


def test_upload_exactly_10mb_accepted(env) -> None:
    response = post_transcribe(env, data=b"\x01" * MAX_UPLOAD_BYTES)
    assert response.status_code == 200


def test_oversized_upload_rejected_before_any_processing(env) -> None:
    response = post_transcribe(env, data=b"\x01" * (MAX_UPLOAD_BYTES + 1))
    assert response.status_code == 413
    assert env.runner.commands == []          # never decoded
    assert env.primary.calls == []            # never sent to STT


def test_unsupported_mime_rejected_415(env) -> None:
    response = post_transcribe(env, mime="text/plain")
    assert response.status_code == 415
    assert env.runner.commands == []
    assert env.primary.calls == []


@pytest.mark.parametrize("mime", ["audio/webm", "audio/ogg", "audio/wav", "audio/x-wav", "audio/mp4"])
def test_supported_mime_allowlist(env, mime) -> None:
    response = post_transcribe(env, mime=mime)
    assert response.status_code == 200, mime


def test_corrupt_audio_422_without_provider_call(env) -> None:
    env.runner.returncode = 1  # decoder rejects the bytes despite allowed MIME
    response = post_transcribe(env)
    assert response.status_code == 422
    assert "audio_decode_failed" in response.json()["detail"]
    assert env.primary.calls == []


def test_garbage_decoder_output_422(env) -> None:
    env.runner.garbage_output = True
    response = post_transcribe(env)
    assert response.status_code == 422
    assert env.primary.calls == []


def test_silence_rejected_without_provider_call_or_fallback(env) -> None:
    env.runner.level = 0  # deterministic all-zero PCM
    response = post_transcribe(env)
    assert response.status_code == 422
    assert "audio_silent" in response.json()["detail"]
    assert env.primary.calls == [] and env.fallback.calls == []


def test_quiet_speech_is_not_classified_as_silence(env) -> None:
    env.runner.level = 500  # quiet but real signal, well above the zero floor
    assert post_transcribe(env).status_code == 200


def test_decoded_audio_over_30_seconds_rejected(env) -> None:
    env.runner.pcm_seconds = 31.0  # what the bounded decode window yields for long input
    response = post_transcribe(env)
    assert response.status_code == 422
    assert "audio_too_long" in response.json()["detail"]
    assert env.primary.calls == []


def test_decode_window_is_bounded_in_command(env) -> None:
    post_transcribe(env)
    command = env.runner.commands[0]
    assert "-t" in command
    assert float(command[command.index("-t") + 1]) <= 31.0


# =========================================================================== #
# Normalization (PART 11, 12, 14, 16)
# =========================================================================== #


def test_provider_receives_canonical_16k_mono_pcm(env) -> None:
    assert post_transcribe(env).status_code == 200
    wav_bytes, _hint = env.primary.calls[0]
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        assert wav.getframerate() == 16000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getcomptype() == "NONE"


def test_ffmpeg_command_shape_is_safe(env) -> None:
    post_transcribe(env)
    command = env.runner.commands[0]
    assert isinstance(command, list) and command[0] == "ffmpeg-test"
    for fragment in ("-nostdin", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav"):
        assert fragment in command
    joined = " ".join(command)
    assert ";" not in joined and "&" not in joined  # no shell fragments


def test_temp_files_cleaned_on_success_and_failure(env) -> None:
    post_transcribe(env)
    env.runner.returncode = 1
    post_transcribe(env)
    for path in env.runner.touched_paths:
        assert not Path(path).exists(), path


def test_normalization_happens_once_before_fallback(env) -> None:
    env.primary.fail = "failed"
    response = post_transcribe(env)
    assert response.status_code == 200
    assert response.json()["transcript"]["provider"] == "whisper.cpp"
    assert len(env.runner.commands) == 1                    # decoded exactly once
    assert env.fallback.calls[0][0] == env.primary.calls[0][0]  # SAME AudioInput bytes


# =========================================================================== #
# faster-whisper adapter (PART 17-19, 29)
# =========================================================================== #


class FakeWhisperModel:
    def __init__(self, language="en", probability=0.93, sleep_seconds=0.0):
        self.language = language
        self.probability = probability
        self.sleep_seconds = sleep_seconds
        self.received_streams = 0

    def transcribe(self, audio, language=None, beam_size=None, vad_filter=None):
        assert beam_size == 1 and vad_filter is True  # starting configuration per plan
        self.received_streams += 1
        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)  # simulate BLOCKING native inference
        info = type("Info", (), {"language": self.language, "language_probability": self.probability})()
        segments = iter(
            [type("Seg", (), {"text": " Cześć "})(), type("Seg", (), {"text": "mamo"})()]
        )
        return segments, info


def test_faster_whisper_success_identity_and_confidence() -> None:
    model = FakeWhisperModel(language="en", probability=0.93)
    provider = FasterWhisperProvider(model_factory=lambda: model)
    transcript = asyncio.run(provider.transcribe(AudioInput(wav_bytes=make_wav())))
    assert transcript.provider == "faster-whisper"
    assert transcript.text == "Cześć mamo"
    assert transcript.language == "en"
    assert transcript.language_confidence == 0.93  # provider probability carried through


def test_faster_whisper_polish_detection_normalized() -> None:
    model = FakeWhisperModel(language="pl", probability=0.88)
    provider = FasterWhisperProvider(model_factory=lambda: model)
    transcript = asyncio.run(provider.transcribe(AudioInput(wav_bytes=make_wav())))
    assert transcript.language == "pl"


def test_faster_whisper_model_loaded_once_not_per_request() -> None:
    loads = []

    def factory():
        loads.append(1)
        return FakeWhisperModel()

    provider = FasterWhisperProvider(model_factory=factory)
    audio = AudioInput(wav_bytes=make_wav())
    asyncio.run(provider.transcribe(audio))
    asyncio.run(provider.transcribe(audio))
    assert len(loads) == 1


def test_faster_whisper_load_failure_sanitized_and_health() -> None:
    def factory():
        raise RuntimeError("cannot open C:/Users/private/model.bin")

    provider = FasterWhisperProvider(model_factory=factory)
    with pytest.raises(ProviderUnavailableError) as caught:
        asyncio.run(provider.transcribe(AudioInput(wav_bytes=make_wav())))
    assert "private" not in str(caught.value)  # factory error text never leaks
    health = asyncio.run(provider.health())
    assert health.status is HealthStatus.UNAVAILABLE
    assert "private" not in (health.detail or "")


def test_blocking_inference_does_not_block_event_loop() -> None:
    """PART 28 acceptance: a deliberately blocking provider must run off the
    loop; a ticker task keeps making progress while it computes."""
    model = FakeWhisperModel(sleep_seconds=0.3)
    provider = FasterWhisperProvider(model_factory=lambda: model)
    service = TranscriptionService(primary=provider, timeout_seconds=10.0)

    async def scenario() -> int:
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)

        task = asyncio.create_task(ticker())
        await service.transcribe(AudioInput(wav_bytes=make_wav()))
        task.cancel()
        return ticks

    ticks = asyncio.run(scenario())
    assert ticks >= 5  # loop stayed responsive during the 0.3 s native call


# =========================================================================== #
# whisper.cpp adapter (PART 20-21)
# =========================================================================== #


class FakeCppRunner:
    def __init__(self, returncode=0, stdout="Hello from cpp\n", raise_exc=None):
        self.returncode = returncode
        self.stdout = stdout
        self.raise_exc = raise_exc
        self.commands: list[list[str]] = []
        self.read_wav_bytes: bytes | None = None

    def __call__(self, command, timeout):
        self.commands.append(list(command))
        wav_path = command[command.index("-f") + 1]
        self.read_wav_bytes = Path(wav_path).read_bytes()
        if self.raise_exc is not None:
            raise self.raise_exc
        from app.voice.whisper_cpp import RunnerResult as CppRunnerResult

        return CppRunnerResult(returncode=self.returncode, stdout=self.stdout)


def test_whisper_cpp_command_shape_and_canonical_wav(tmp_path) -> None:
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"synthetic-model")
    runner = FakeCppRunner()
    provider = WhisperCppProvider(binary="whisper-cli-test", model_path=str(model), runner=runner)
    audio = AudioInput(wav_bytes=make_wav())

    transcript = asyncio.run(provider.transcribe(audio, "pl"))
    command = runner.commands[0]
    assert command[0] == "whisper-cli-test"
    assert command[command.index("-m") + 1] == str(model)
    assert command[command.index("-l") + 1] == "pl"
    assert "-nt" in command
    assert runner.read_wav_bytes == audio.wav_bytes  # SAME canonical input, no re-decode
    assert transcript.provider == "whisper.cpp"
    assert transcript.language == "pl"


def test_whisper_cpp_language_auto_without_hint(tmp_path) -> None:
    model = tmp_path / "m.bin"
    model.write_bytes(b"x")
    runner = FakeCppRunner()
    provider = WhisperCppProvider(binary="w", model_path=str(model), runner=runner)
    transcript = asyncio.run(provider.transcribe(AudioInput(wav_bytes=make_wav())))
    assert runner.commands[0][runner.commands[0].index("-l") + 1] == "auto"
    assert transcript.language == "und"          # auto with no probability: honest
    assert transcript.language_confidence is None


def test_whisper_cpp_failure_and_timeout_map_to_stable_errors(tmp_path) -> None:
    model = tmp_path / "m.bin"
    model.write_bytes(b"x")
    failing = WhisperCppProvider(binary="w", model_path=str(model), runner=FakeCppRunner(returncode=1))
    with pytest.raises(TranscriptionFailedError):
        asyncio.run(failing.transcribe(AudioInput(wav_bytes=make_wav())))

    timing_out = WhisperCppProvider(
        binary="w", model_path=str(model),
        runner=FakeCppRunner(raise_exc=subprocess.TimeoutExpired(cmd="w", timeout=1)),
    )
    with pytest.raises(TranscriptionTimeoutError):
        asyncio.run(timing_out.transcribe(AudioInput(wav_bytes=make_wav())))


def test_whisper_cpp_unconfigured_or_missing_model_is_unavailable(tmp_path) -> None:
    unconfigured = WhisperCppProvider(binary="", model_path="")
    with pytest.raises(ProviderUnavailableError):
        asyncio.run(unconfigured.transcribe(AudioInput(wav_bytes=make_wav())))
    assert asyncio.run(unconfigured.health()).status is HealthStatus.UNAVAILABLE

    missing_model = WhisperCppProvider(binary="w", model_path=str(tmp_path / "absent.bin"))
    assert asyncio.run(missing_model.health()).status is HealthStatus.UNAVAILABLE


# =========================================================================== #
# Orchestration: fallback, availability, concurrency, timeout (PART 22-27)
# =========================================================================== #


def test_primary_unavailable_falls_back_once_with_identity(env) -> None:
    env.primary.fail = "unavailable"
    response = post_transcribe(env)
    assert response.status_code == 200
    payload = response.json()["transcript"]
    assert payload["provider"] == "whisper.cpp"   # honest identity, no pretending
    assert payload["text"] == "Fallback transcript"


def test_both_unavailable_returns_503_without_fabrication(env) -> None:
    env.primary.fail = "unavailable"
    env.fallback.fail = "unavailable"
    response = post_transcribe(env)
    assert response.status_code == 503
    assert "stt_unavailable" in response.json()["detail"]
    assert "transcript" not in response.json()   # nothing invented


def test_timeout_maps_to_504_and_is_terminal(env) -> None:
    env.primary.block_seconds = 0.2
    env.fallback.fail = "unavailable"
    env.app.state.stt_service = TranscriptionService(
        primary=env.primary, fallback=env.fallback, max_concurrency=1, timeout_seconds=0.05
    )
    response = post_transcribe(env)
    assert response.status_code == 504
    assert "transcription_timeout" in response.json()["detail"]
    # PART 8: a timeout is TERMINAL - the still-running primary holds its
    # inference lease, so no fallback may start for this request.
    assert env.fallback.calls == []


def test_bounded_concurrency_never_exceeds_configured() -> None:
    provider = FakeSttProvider("faster-whisper", block_seconds=0.05)
    service = TranscriptionService(primary=provider, max_concurrency=1, timeout_seconds=10.0)

    async def scenario():
        return await asyncio.gather(
            *[service.transcribe(AudioInput(wav_bytes=make_wav(0.2))) for _ in range(3)]
        )

    results = asyncio.run(scenario())
    assert len(results) == 3
    assert provider.max_observed == 1  # strictly serialized at concurrency=1


# =========================================================================== #
# Language behavior (PART 31-33)
# =========================================================================== #


def test_language_tag_normalization() -> None:
    assert normalize_language_tag("EN") == "en"
    assert normalize_language_tag("en-US") == "en"
    assert normalize_language_tag("pol") == "pl"
    assert normalize_language_tag("Polish") is None  # never guessed
    assert normalize_language_tag(None) is None


def test_confident_detection_never_overridden_by_hint() -> None:
    language, confidence = resolve_language(
        detected="pl", confidence=0.95, conversation_hint="en",
        text="Zamówienie zostało potwierdzone wczoraj wieczorem",
    )
    assert (language, confidence) == ("pl", 0.95)


def test_short_approval_retains_conversation_language_without_text_change() -> None:
    language, confidence = resolve_language(
        detected="en", confidence=0.2, conversation_hint="pl", text="Tak",
    )
    assert language == "pl"          # prior conversation language retained
    assert confidence == 0.2         # never fabricated upward


def test_route_short_approval_uses_language_hint(env) -> None:
    env.primary.text = "Tak"
    env.primary.language = "en"
    env.primary.confidence = 0.15
    response = post_transcribe(env, language="pl")
    payload = response.json()["transcript"]
    assert payload["text"] == "Tak"      # text untouched
    assert payload["language"] == "pl"   # conversation language retained


def test_unknown_language_reports_und(env) -> None:
    env.primary.language = "klingon"  # unnormalizable provider output
    env.primary.confidence = 0.4
    response = post_transcribe(env)
    assert response.json()["transcript"]["language"] == "und"


def test_en_and_pl_orchestration(env) -> None:
    env.primary.language = "en"
    assert post_transcribe(env, request_id="req-en").json()["transcript"]["language"] == "en"
    env.primary.language = "pol"  # ISO3 output normalized to short tag
    assert post_transcribe(env, request_id="req-pl").json()["transcript"]["language"] == "pl"


# =========================================================================== #
# Duration measurement (PART 34)
# =========================================================================== #


def test_duration_ms_measured_with_monotonic_clock() -> None:
    # Four reads per attempt now: request start, queue-deadline check (zero
    # wait here), inference start, inference end - duration stays INFERENCE-ONLY.
    ticks = iter([100.0, 100.0, 100.0, 100.5])
    provider = FakeSttProvider("faster-whisper")
    service = TranscriptionService(
        primary=provider, timeout_seconds=5.0, clock=lambda: next(ticks)
    )
    transcript = asyncio.run(service.transcribe(AudioInput(wav_bytes=make_wav())))
    assert transcript.duration_ms == 500


# =========================================================================== #
# Privacy (PART 16, 41)
# =========================================================================== #


def test_no_audio_artifacts_left_in_workspace_after_requests(env) -> None:
    post_transcribe(env)                      # success path
    env.runner.returncode = 1
    post_transcribe(env)                      # failure path
    leftovers = [
        p for p in env.tmp.rglob("*")
        if p.is_file() and p.suffix in {".wav", ".webm", ".ogg", ".m4a", ".bin"}
    ]
    assert leftovers == []


# =========================================================================== #
# Config validation (PART 39-40) + provider name canonicalization
# =========================================================================== #


def test_stt_config_validation() -> None:
    with pytest.raises(Exception):
        Settings(eva_stt_max_concurrency=0, _env_file=None)
    with pytest.raises(Exception):
        Settings(eva_stt_timeout_seconds=0, _env_file=None)
    settings = Settings(eva_stt_provider="whisper-cpp", _env_file=None)
    assert settings.eva_stt_provider == "whisper.cpp"
    with pytest.raises(Exception):
        Settings(eva_stt_provider="vosk", _env_file=None)


def test_provider_name_canonicalization_rejects_unknown() -> None:
    assert canonicalize_stt_provider_name("FASTER_WHISPER") == "faster-whisper"
    assert canonicalize_stt_provider_name("Whisper.CPP") == "whisper.cpp"
    with pytest.raises(ValueError):
        canonicalize_stt_provider_name("google-speech")


def test_validate_canonical_wav_rejects_noncanonical() -> None:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:  # stereo 8 kHz -> not canonical
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x01\x00" * 100)
    with pytest.raises(AudioInputError) as caught:
        validate_canonical_wav(buffer.getvalue())
    assert caught.value.code == "audio_decode_failed"


# =========================================================================== #
# REMEDIATION - conversation hint is NEVER an inference-language override
# =========================================================================== #


def test_conversation_hint_never_forces_provider_inference_language(env) -> None:
    response = post_transcribe(env, language="pl")
    assert response.status_code == 200
    # Providers receive language=None (automatic detection). The multipart
    # `language` field is a PRIOR CONVERSATION LANGUAGE only.
    assert env.primary.calls[0][1] is None


def test_long_english_with_polish_hint_stays_english(env) -> None:
    env.primary.text = "Please move the client review to tomorrow afternoon"
    env.primary.language = "en"
    env.primary.confidence = 0.95
    response = post_transcribe(env, language="pl")
    payload = response.json()["transcript"]
    assert payload["language"] == "en"          # confident detection wins
    assert env.primary.calls[0][1] is None      # decoded automatically


def test_long_polish_with_english_hint_stays_polish(env) -> None:
    env.primary.text = "Przenieś przegląd klienta na jutro po południu"
    env.primary.language = "pl"
    env.primary.confidence = 0.97
    response = post_transcribe(env, language="en")
    assert response.json()["transcript"]["language"] == "pl"


def test_orchestrator_drives_whisper_cpp_with_auto_language(tmp_path) -> None:
    """The public conversation hint must not become '-l pl' in the CLI."""
    model = tmp_path / "m.bin"
    model.write_bytes(b"x")
    runner = FakeCppRunner()
    provider = WhisperCppProvider(binary="w", model_path=str(model), runner=runner)
    service = TranscriptionService(primary=provider, timeout_seconds=5.0)
    transcript = asyncio.run(service.transcribe(AudioInput(wav_bytes=make_wav()), "pl"))
    command = runner.commands[0]
    assert command[command.index("-l") + 1] == "auto"
    assert transcript.language == "und"  # auto without probability: honest


# =========================================================================== #
# REMEDIATION - timeout keeps the concurrency LEASE (deterministic events)
# =========================================================================== #


class GatedProvider:
    """Deterministic worker double: each call blocks in a real thread on a
    threading.Event gate. Synchronization authority is EVENTS, not sleeps."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.calls = 0
        self.active = 0
        self.max_observed = 0
        self._lock = threading.Lock()

    async def transcribe(self, audio: AudioInput, language: str | None = None) -> Transcript:
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_observed = max(self.max_observed, self.active)
        try:
            await asyncio.to_thread(self.gate.wait)  # native-style blocking work
            return Transcript(
                text="done", language="en", language_confidence=0.9,
                duration_ms=0, provider="gated",
            )
        finally:
            with self._lock:
                self.active -= 1


def test_timeout_retains_concurrency_lease() -> None:
    async def scenario() -> None:
        provider = GatedProvider()
        fallback = FakeSttProvider("whisper.cpp")
        service = TranscriptionService(
            primary=provider, fallback=fallback, max_concurrency=1, timeout_seconds=0.05
        )
        audio = AudioInput(wav_bytes=make_wav(0.2))

        with pytest.raises(TranscriptionTimeoutError):
            await service.transcribe(audio)
        assert provider.calls == 1              # first worker started...
        assert fallback.calls == []             # ...timeout terminal: no fallback

        second = asyncio.create_task(service.transcribe(audio))
        for _ in range(20):                     # ample scheduling, no sleep-sync
            await asyncio.sleep(0)
        assert provider.calls == 1              # lease STILL held by the worker
        assert fallback.calls == []

        provider.gate.set()                     # first native call REALLY exits
        result = await second                   # only now may the next start
        assert result.text == "done"
        assert provider.calls == 2
        assert provider.max_observed == 1       # real concurrency never exceeded

    asyncio.run(scenario())


def test_cancelled_caller_does_not_release_lease_early() -> None:
    async def scenario() -> None:
        provider = GatedProvider()
        service = TranscriptionService(primary=provider, max_concurrency=1, timeout_seconds=30.0)
        audio = AudioInput(wav_bytes=make_wav(0.2))

        first = asyncio.create_task(service.transcribe(audio))
        while provider.calls < 1:
            await asyncio.sleep(0)
        first.cancel()                          # client disconnect equivalent
        with pytest.raises(asyncio.CancelledError):
            await first

        second = asyncio.create_task(service.transcribe(audio))
        for _ in range(20):
            await asyncio.sleep(0)
        assert provider.calls == 1              # abandoned worker keeps its lease

        provider.gate.set()
        result = await second
        assert result.text == "done"
        assert provider.max_observed == 1

    asyncio.run(scenario())


# =========================================================================== #
# REMEDIATION - faster-whisper runtime loading is LOCAL-FILES-ONLY
# =========================================================================== #


def test_default_factory_enforces_local_files_only(monkeypatch) -> None:
    captured: dict = {}

    class FakeWhisperModel:
        def __init__(self, model, device=None, compute_type=None, local_files_only=False):
            captured.update(
                model=model, device=device, compute_type=compute_type,
                local_files_only=local_files_only,
            )

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)  # no real package

    provider = FasterWhisperProvider(model_name="base")          # DEFAULT factory
    asyncio.run(provider.warmup())
    assert captured["local_files_only"] is True                  # no Hub fetch path
    assert asyncio.run(provider.health()).status is HealthStatus.READY


def test_missing_local_model_is_unavailable_without_download(monkeypatch) -> None:
    attempts = []

    def refusing_factory(model, device=None, compute_type=None, local_files_only=False):
        attempts.append(local_files_only)
        raise OSError(f"Model '{model}' is not available locally")  # like missing cache

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = refusing_factory
    monkeypatch.setitem(sys.modules, "faster_whisper", module)

    provider = FasterWhisperProvider()
    service = TranscriptionService(primary=provider)
    asyncio.run(service.warmup())              # startup path: swallows, never raises
    assert attempts == [True]                  # local-only attempt, no fallback fetch
    health = asyncio.run(provider.health())
    assert health.status is HealthStatus.UNAVAILABLE
    with pytest.raises(ProviderUnavailableError):
        asyncio.run(provider.transcribe(AudioInput(wav_bytes=make_wav())))


# =========================================================================== #
# REMEDIATION - whisper.cpp binary health (real discovery, sanitized detail)
# =========================================================================== #


def test_whisper_cpp_binary_health_matrix(tmp_path) -> None:
    model = tmp_path / "ggml.bin"
    model.write_bytes(b"synthetic-model")

    async def status(**kwargs):
        return (await WhisperCppProvider(**kwargs).health()).status

    unavailable = HealthStatus.UNAVAILABLE
    assert asyncio.run(status(binary="", model_path=str(model))) is unavailable
    assert asyncio.run(status(binary="w", model_path="")) is unavailable
    assert asyncio.run(
        status(binary="w", model_path=str(tmp_path / "absent.bin"), runner=FakeCppRunner())
    ) is unavailable
    # REAL runner: explicit path that does not exist -> unavailable...
    missing_path = WhisperCppProvider(binary=str(tmp_path / "no-such-bin.exe"), model_path=str(model))
    health = asyncio.run(missing_path.health())
    assert health.status is HealthStatus.UNAVAILABLE
    assert str(tmp_path) not in (health.detail or "")            # no private paths out
    # ...and a bare command name that is not on PATH -> unavailable.
    assert asyncio.run(
        status(binary="definitely-not-a-real-command-xyz", model_path=str(model))
    ) is unavailable
    # Injected fake runner + synthetic model: usable in tests (OK).
    usable = WhisperCppProvider(binary="fake", model_path=str(model), runner=FakeCppRunner())
    assert asyncio.run(usable.health()).status is HealthStatus.READY


def test_whisper_cpp_real_runner_missing_binary_raises_unavailable(tmp_path) -> None:
    model = tmp_path / "ggml.bin"
    model.write_bytes(b"x")
    provider = WhisperCppProvider(binary=str(tmp_path / "no-such-bin.exe"), model_path=str(model))
    with pytest.raises(ProviderUnavailableError):
        asyncio.run(provider.transcribe(AudioInput(wav_bytes=make_wav())))


# =========================================================================== #
# REMEDIATION - empty recognition is never a 200
# =========================================================================== #


def test_empty_provider_transcript_never_returns_200(env) -> None:
    env.primary.text = ""      # VAD/STT answered with nothing (realistic silence)
    env.fallback.text = "   "  # whitespace is equally empty
    response = post_transcribe(env)
    assert response.status_code == 422
    assert "no_speech_detected" in response.json()["detail"]
    assert "transcript" not in response.json()   # nothing fabricated


def test_empty_primary_recognition_falls_back_once_to_nonempty(env) -> None:
    env.primary.text = ""
    response = post_transcribe(env)              # fallback has real text
    assert response.status_code == 200
    payload = response.json()["transcript"]
    assert payload["provider"] == "whisper.cpp"
    assert payload["text"] == "Fallback transcript"


# =========================================================================== #
# CORE FIX 2 - ASGI ingress cap BEFORE multipart parsing
# =========================================================================== #

from app.api.ingress import (  # noqa: E402
    MAX_MULTIPART_REQUEST_BYTES,
    MULTIPART_OVERHEAD_BYTES,
    VoiceIngressLimiter,
)


def make_silent_wav(frame_bytes: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
        wav.writeframes(b"\x00" * frame_bytes)
    return buffer.getvalue()


def multipart_body(audio: bytes, boundary: str = "XbNd9", request_id: str = "req-1") -> bytes:
    head = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"request_id\"\r\n\r\n"
        f"{request_id}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; "
        f"filename=\"take.webm\"\r\nContent-Type: audio/webm\r\n\r\n"
    ).encode()
    return head + audio + f"\r\n--{boundary}--\r\n".encode()


def test_oversized_declared_request_rejected_before_handler(env) -> None:
    huge = make_silent_wav(MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES + 4096)
    response = post_transcribe(env, data=huge)
    assert response.status_code == 413
    assert response.json()["detail"] == "request_body_too_large"   # limiter, not handler
    assert env.primary.calls == [] and env.runner.commands == []   # never reached pipeline


def test_chunked_stream_over_cap_rejected_without_content_length(env) -> None:
    body = multipart_body(make_silent_wav(MAX_MULTIPART_REQUEST_BYTES + 50_000))
    chunks = (body[i:i + 1 << 20] for i in range(0, len(body), 1 << 20))
    response = env.client.post(
        "/api/voice/transcribe",
        content=chunks,
        headers={
            "Content-Type": "multipart/form-data; boundary=XbNd9",
            "X-EVA-Session-ID": SESSION,
        },
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "request_body_too_large"
    assert env.primary.calls == []


def test_valid_request_near_audio_cap_still_reaches_handler(env) -> None:
    audio = make_silent_wav(MAX_UPLOAD_BYTES - 32 * 1024)     # request stays under cap
    response = post_transcribe(env, data=audio)
    assert response.status_code == 200, response.text          # parsed and transcribed
    assert len(env.primary.calls) == 1


def test_audio_over_exact_cap_still_rejected_by_file_limit(env) -> None:
    audio = make_silent_wav(MAX_UPLOAD_BYTES + 8 * 1024)       # under ingress cap...
    response = post_transcribe(env, data=audio)                # ...handler enforces exactly
    assert response.status_code == 413
    assert "audio_too_large" in response.json()["detail"]


def test_ingress_rejection_never_logs_body_bytes(env, caplog) -> None:
    marker = b"MARKER-AUDIO-BYTES-DO-NOT-LOG"
    huge = marker + make_silent_wav(MAX_MULTIPART_REQUEST_BYTES + 4096)
    with caplog.at_level("INFO"):
        response = post_transcribe(env, data=huge)
    assert response.status_code == 413
    assert "MARKER-AUDIO-BYTES" not in caplog.text
    assert "MARKER-AUDIO-BYTES" not in response.text


# ---- streaming pass-through proof (raw ASGI; no HTTP client involved) ----- #


class _RecordingDownstream:
    """Minimal ASGI app that records every body chunk it is handed."""

    def __init__(self, *, respond_after_first_chunk=False):
        self.chunks: list[bytes] = []
        self.message_types: list[str] = []
        self.respond_after_first_chunk = respond_after_first_chunk
        self.started = False

    async def __call__(self, scope, receive, send):
        while True:
            message = await receive()
            self.message_types.append(message["type"])
            if message["type"] == "http.disconnect":
                return
            self.chunks.append(message.get("body") or b"")
            if self.respond_after_first_chunk and not self.started:
                self.started = True
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"", "more_body": True})
            if not message.get("more_body", False):
                if not self.started:
                    await send({"type": "http.response.start", "status": 200, "headers": []})
                    await send({"type": "http.response.body", "body": b"ok"})
                return


def _voice_scope(headers=None) -> dict:
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "path": "/api/voice/transcribe", "raw_path": b"/api/voice/transcribe",
        "query_string": b"", "headers": headers or [], "client": ("test", 1), "server": ("test", 80),
        "scheme": "http",
    }


def _req_chunks(parts):
    msgs = []
    for i, part in enumerate(parts):
        msgs.append({"type": "http.request", "body": part,
                     "more_body": i < len(parts) - 1})
    return msgs


def test_valid_streamed_request_forwards_each_chunk_unchanged() -> None:
    # Three chunks totalling well under the cap must reach downstream as THREE
    # separate http.request messages - proof the limiter does not concatenate.
    parts = [b"A" * 1000, b"B" * 1000, b"C" * 500]

    async def run():
        spy = _RecordingDownstream()
        mw = VoiceIngressLimiter(spy)
        incoming = _req_chunks(parts)
        sent = []

        async def receive():
            return incoming.pop(0) if incoming else {"type": "http.disconnect"}

        async def send(m):
            sent.append(m)

        await mw(_voice_scope(), receive, send)
        return spy, sent

    spy, sent = asyncio.run(run())
    assert spy.chunks == parts                    # each chunk forwarded verbatim
    assert spy.message_types == ["http.request", "http.request", "http.request"]
    assert sent[0]["status"] == 200


def test_streaming_over_cap_aborts_with_single_413_no_double_start() -> None:
    # Chunked stream with NO content-length crosses the cap mid-flight.
    big = b"X" * (MAX_MULTIPART_REQUEST_BYTES + 1)

    async def run():
        spy = _RecordingDownstream()
        mw = VoiceIngressLimiter(spy)
        incoming = _req_chunks([big[: len(big) // 2], big[len(big) // 2:]])
        sent: list[dict] = []

        async def receive():
            return incoming.pop(0) if incoming else {"type": "http.disconnect"}

        async def send(m):
            sent.append(m)

        await mw(_voice_scope(), receive, send)   # downstream must NOT raise here
        return spy, sent

    spy, sent = asyncio.run(run())
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert len(starts) == 1 and starts[0]["status"] == 413   # exactly one response


def test_disconnect_before_cap_passes_through_without_response() -> None:
    async def run():
        spy = _RecordingDownstream()
        mw = VoiceIngressLimiter(spy)
        incoming = [{"type": "http.request", "body": b"partial", "more_body": True},
                    {"type": "http.disconnect"}]
        sent: list[dict] = []

        async def receive():
            return incoming.pop(0) if incoming else {"type": "http.disconnect"}

        async def send(m):
            sent.append(m)

        await mw(_voice_scope(), receive, send)
        return spy, sent

    spy, sent = asyncio.run(run())
    assert [m for m in sent if m["type"] == "http.response.start"] == []  # nothing answered


def test_non_voice_or_get_scopes_are_not_counted() -> None:
    get_scope = _voice_scope()
    get_scope["method"] = "GET"

    async def run():
        spy = _RecordingDownstream()
        mw = VoiceIngressLimiter(spy)
        big = b"Y" * (MAX_MULTIPART_REQUEST_BYTES + 10_000)   # would trip a POST cap
        incoming = _req_chunks([big])
        sent: list[dict] = []

        async def receive():
            return incoming.pop(0) if incoming else {"type": "http.disconnect"}

        async def send(m):
            sent.append(m)

        await mw(get_scope, receive, send)         # GET is passed through untouched
        return spy, sent

    spy, sent = asyncio.run(run())                 # no 413 for a non-POST scope
    assert spy.chunks == [b"Y" * (MAX_MULTIPART_REQUEST_BYTES + 10_000)]
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert all(m["status"] != 413 for m in starts)  # untouched: downstream's own answer


def test_over_cap_after_downstream_started_sends_no_second_response() -> None:
    # Downstream emits response.start on the first chunk, then keeps reading;
    # a later chunk crosses the cap. The limiter must NOT invent a second
    # http.response.start (ASGI forbids it) - it fails closed silently.
    big = b"Q" * (MAX_MULTIPART_REQUEST_BYTES + 1)

    async def run():
        spy = _RecordingDownstream(respond_after_first_chunk=True)
        mw = VoiceIngressLimiter(spy)
        incoming = _req_chunks([b"head", big])
        sent: list[dict] = []

        async def receive():
            return incoming.pop(0) if incoming else {"type": "http.disconnect"}

        async def send(m):
            sent.append(m)

        await mw(_voice_scope(), receive, send)   # must not raise to the caller
        return spy, sent

    spy, sent = asyncio.run(run())
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert len(starts) == 1 and starts[0]["status"] == 200   # downstream's own start


def test_malformed_or_negative_content_length_still_stream_counted() -> None:
    for bad_len in (b"not-a-number", b"-5"):
        scope = _voice_scope(headers=[(b"content-length", bad_len)])

        async def run(scope=scope):
            spy = _RecordingDownstream()
            mw = VoiceIngressLimiter(spy)
            incoming = _req_chunks([b"Z" * (MAX_MULTIPART_REQUEST_BYTES + 1)])
            sent: list[dict] = []

            async def receive():
                return incoming.pop(0) if incoming else {"type": "http.disconnect"}

            async def send(m):
                sent.append(m)

            await mw(scope, receive, send)
            return spy, sent

        spy, sent = asyncio.run(run())             # bad header must NOT bypass counting
        starts = [m for m in sent if m["type"] == "http.response.start"]
        assert len(starts) == 1 and starts[0]["status"] == 413


# =========================================================================== #
# CORE FIX 3 - bounded queue-wait deadline (no unbounded semaphore hang)
# =========================================================================== #


class EventGatedProvider:
    """Holds the inference permit on a threading.Event until released."""

    def __init__(self, name: str = "faster-whisper") -> None:
        self.name = name
        self.event = threading.Event()
        self.calls = 0

    async def transcribe(self, audio, language=None):
        self.calls += 1
        await asyncio.to_thread(self.event.wait)
        return Transcript(text="done", language="en", language_confidence=0.9,
                          duration_ms=0, provider=self.name)

    async def health(self):
        from app.contracts.providers import ProviderHealth

        return ProviderHealth(status=HealthStatus.READY, provider=self.name)


def test_second_request_times_out_boundedly_while_permit_held() -> None:
    provider = EventGatedProvider()
    service = TranscriptionService(primary=provider, max_concurrency=1, timeout_seconds=0.25)

    async def scenario():
        first = asyncio.create_task(service.transcribe(AudioInput(wav_bytes=make_wav(0.1))))
        await asyncio.sleep(0.05)                       # first holds the only permit
        with pytest.raises(TranscriptionTimeoutError):  # request 1 times out
            await first
        start = time.monotonic()                        # native worker STILL running
        with pytest.raises(TranscriptionTimeoutError):  # request 2: bounded, no hang
            await service.transcribe(AudioInput(wav_bytes=make_wav(0.1)))
        waited = time.monotonic() - start
        assert waited < 1.5                             # not indefinite
        assert provider.calls == 1                      # NO second provider task started
        provider.event.set()                            # native worker exits -> permit freed
        transcript = await asyncio.wait_for(
            service.transcribe(AudioInput(wav_bytes=make_wav(0.1))), timeout=2.0
        )
        assert transcript.text == "done"

    asyncio.run(scenario())


def test_cancelled_queue_wait_neither_leaks_nor_double_releases_permit() -> None:
    provider = EventGatedProvider()
    service = TranscriptionService(primary=provider, max_concurrency=1, timeout_seconds=5.0)

    async def scenario():
        holder = asyncio.create_task(service.transcribe(AudioInput(wav_bytes=make_wav(0.1))))
        await asyncio.sleep(0.05)
        waiter = asyncio.create_task(service.transcribe(AudioInput(wav_bytes=make_wav(0.1))))
        await asyncio.sleep(0.05)                       # waiter queued on the semaphore
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        provider.event.set()                            # holder finishes, releases lease
        await holder
        transcript = await asyncio.wait_for(             # permit exactly one - still usable
            service.transcribe(AudioInput(wav_bytes=make_wav(0.1))), timeout=2.0
        )
        assert transcript.text == "done"

    asyncio.run(scenario())


# =========================================================================== #
# CORE FIX 4 - health reports actual A05/STT readiness
# =========================================================================== #


def test_health_stt_ready_when_primary_ready(env) -> None:
    body = env.client.get("/api/health").json()
    stt = body["components"]["stt"]
    assert stt["status"] == "ready"
    assert stt["provider"] == "faster-whisper"
    assert "adapter pending (A05)" not in str(body)      # stale wording gone


def test_health_stt_degraded_when_only_fallback_ready(env) -> None:
    env.primary.fail = "unavailable"
    stt = env.client.get("/api/health").json()["components"]["stt"]
    assert stt["status"] == "degraded"
    assert stt["detail"] == "primary unavailable; local fallback ready"


def test_health_stt_unavailable_when_neither_provider_ready(env) -> None:
    env.primary.fail = "unavailable"
    env.fallback.fail = "unavailable"
    body = env.client.get("/api/health").json()
    assert body["components"]["stt"]["status"] == "unavailable"


def test_health_stt_sanitizes_broken_provider_probe(env) -> None:
    class BrokenHealthProvider(FakeSttProvider):
        async def health(self):
            raise RuntimeError("private path C:\\Users\\secret\\model.bin")

    env.app.state.stt_service = TranscriptionService(
        primary=BrokenHealthProvider("faster-whisper"), max_concurrency=1, timeout_seconds=5.0
    )
    body = env.client.get("/api/health").json()
    assert body["components"]["stt"]["status"] == "unavailable"
    assert "private path" not in str(body)               # no local/private leakage
