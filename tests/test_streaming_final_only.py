"""Own gate for STREAM_ASR_PARTIALS_ENABLED (first-response priority mode).

Deterministic fakes only: no microphone, no model, no network. The fake ASR
records exactly which PCM snapshots reached preview decoding and which one
reached the final decode, so "no previews" can never be mistaken for "still
streaming partials".
"""

from __future__ import annotations

import hashlib
import math
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from srtp_voice import streaming_runtime as runtime_module
from srtp_voice.audio_io import NoSpeechDetectedError
from srtp_voice.config import AppConfig, env_asr_partials_enabled
from srtp_voice.streaming import AudioChunk
from srtp_voice.streaming_runtime import TurnCancelledError, capture_streaming_microphone

FINAL_TEXT = "final transcript text"
PREVIEW_TEXT = "preview text"


class _FakeRawInputStream:
    """Minimal sounddevice RawInputStream: start(), stop(), close()."""

    def __init__(self, frames: list[bytes], callback, *, frame_samples: int) -> None:
        self.frames = frames
        self.callback = callback
        self.frame_samples = frame_samples
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        self.started = True
        for index, pcm in enumerate(self.frames):
            self.callback(pcm, self.frame_samples, None, None)

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


class _FakeDevice:
    """sounddevice-shaped module handed to the capture as its audio module."""

    instance: "_FakeDevice | None" = None

    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.stream: _FakeRawInputStream | None = None
        self.sample_rate = 16000
        _FakeDevice.instance = self

    def set_frames(self, frames: list[bytes]) -> None:
        self.frames = frames
        self.stream = None

    def RawInputStream(self, *, samplerate, channels, dtype, blocksize, callback):
        del channels, dtype
        self.sample_rate = samplerate
        self.stream = _FakeRawInputStream(
            self.frames, callback, frame_samples=blocksize
        )
        return self.stream


class _FakeASR:
    """Splits preview decoding from the final decode by an explicit phase flag."""

    final_phase = False

    def __init__(self, *, final_text: str = FINAL_TEXT,
                 final_error: Exception | None = None,
                 preview_text: str = PREVIEW_TEXT) -> None:
        self.final_text = final_text
        self.final_error = final_error
        self.preview_text = preview_text
        self.preview_pcm: list[bytes] = []
        self.final_pcm: list[bytes] = []
        self.preview_threads: list[int] = []

    def transcribe_pcm16(self, pcm16: bytes, *, sample_rate: int) -> str | None:
        if not _FakeASR.final_phase:
            self.preview_pcm.append(pcm16)
            self.preview_threads.append(threading.get_ident())
            return self.preview_text
        self.final_pcm.append(pcm16)
        if self.final_error is not None:
            raise self.final_error
        return self.final_text

    def transcribe(self, wav_path) -> str:  # pragma: no cover - in-memory path only
        raise AssertionError("capture must use the in-memory transcriber")


@pytest.fixture(autouse=True)
def _reset_asr_phase():
    _FakeASR.final_phase = False
    _FakeDevice.instance = None
    yield
    _FakeASR.final_phase = False
    _FakeDevice.instance = None


def _speech_frames(count: int, *, frame_ms: int = 20, sample_rate: int = 16000) -> list[bytes]:
    samples = int(sample_rate * frame_ms / 1000)
    frame = b"".join(
        int(12000 * math.sin(2 * math.pi * 200 * i / sample_rate)).to_bytes(
            2, "little", signed=True
        )
        for i in range(samples)
    )
    return [frame] * count


def _silence_frames(count: int, *, frame_ms: int = 20, sample_rate: int = 16000) -> list[bytes]:
    samples = int(sample_rate * frame_ms / 1000)
    return [b"\x00\x00" * samples] * count


def _utterance(prefix_silence: int = 40, speech: int = 70,
               tail_silence: int = 50) -> list[bytes]:
    """Frame shape for the production collector defaults (20 ms frames)."""
    return (
        _silence_frames(prefix_silence)
        + _speech_frames(speech)
        + _silence_frames(tail_silence)
    )


class _Runtime:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, handle, event_type, payload=None) -> None:
        self.events.append((event_type.value, dict(payload or {})))

    def event_types(self) -> list[str]:
        return [name for name, _ in self.events]


class _Handle:
    turn_id = "final-only"

    def __init__(self) -> None:
        self.cancelled = threading.Event()


def _capture(tmp_path, cfg, frames: list[bytes], asr: _FakeASR, *,
             handle: _Handle | None = None):
    runtime = _Runtime()
    target = handle or _Handle()
    output_wav = tmp_path / "input.wav"
    _FakeASR.final_phase = False
    original_finalize = runtime_module.IncrementalASRSession.finalize

    def finalize(session, pcm16, **kwargs):
        # Everything after this point is the real final decode; the flag keeps
        # preview calls from being counted as finals.
        _FakeASR.final_phase = True
        return original_finalize(session, pcm16, **kwargs)

    runtime_module.IncrementalASRSession.finalize = finalize
    device = _FakeDevice.instance or _FakeDevice()
    device.set_frames(frames)
    try:
        text = capture_streaming_microphone(
            cfg,
            asr,
            runtime,
            target,
            output_wav,
            sounddevice_module=device,
        )
    finally:
        runtime_module.IncrementalASRSession.finalize = original_finalize
    return runtime, output_wav, text


def _config(**overrides) -> AppConfig:
    values = {
        "sample_rate": 16000,
        "frame_ms": 20,
        "vad_calibration_ms": 0,
        # This synchronous double supplies its full input in start(). Avoid
        # dropping the tail before the real queue consumer begins; actual
        # callback capacity/drop behavior has its own independent tests.
        "stream_audio_queue_size": 512,
        "stream_asr_partial_interval_seconds": 0.05,
    }
    values.update(overrides)
    return AppConfig(**values)


def test_previews_disabled_send_no_snapshot_but_keep_the_full_final(tmp_path) -> None:
    asr = _FakeASR()
    frames = _utterance()
    cfg = _config(stream_asr_partials_enabled=False)

    runtime, output_wav, text = _capture(tmp_path, cfg, frames, asr)

    assert asr.preview_pcm == []  # no snapshot was ever read or decoded
    assert len(asr.final_pcm) == 1
    assert text == FINAL_TEXT
    assert "asr_partial" not in runtime.event_types()
    assert runtime.event_types().count("asr_final") == 1
    assert runtime.event_types().count("vad_stopped") == 1
    assert output_wav.is_file()
    # The recorded utterance (not a preview snapshot) reached the final decode,
    # and the same bytes were written into the turn WAV.
    assert len(asr.final_pcm[0]) > len(b"".join(frames[:40]))
    stored = output_wav.read_bytes()
    assert hashlib.sha256(stored[-len(asr.final_pcm[0]):]).digest() == hashlib.sha256(
        asr.final_pcm[0]
    ).digest()


def test_previews_enabled_keep_streaming_partials_and_one_final(tmp_path) -> None:
    asr = _FakeASR()
    frames = _utterance()
    cfg = _config(stream_asr_partials_enabled=True)

    runtime, _output, text = _capture(tmp_path, cfg, frames, asr)

    assert asr.preview_pcm, "previews must still be submitted when enabled"
    assert len(asr.final_pcm) == 1
    assert text == FINAL_TEXT
    assert runtime.event_types().count("asr_final") == 1
    assert runtime.event_types().count("asr_partial") >= 1


def test_same_pcm_reaches_the_final_decode_in_both_modes(tmp_path) -> None:
    frames = _utterance()

    enabled = _FakeASR()
    _capture(tmp_path / "a", _config(stream_asr_partials_enabled=True), frames, enabled)
    disabled = _FakeASR()
    _capture(tmp_path / "b", _config(stream_asr_partials_enabled=False), frames, disabled)

    assert enabled.final_pcm[0] == disabled.final_pcm[0]
    assert enabled.preview_pcm and disabled.preview_pcm == []


def _legacy_config(**overrides):
    """A complete pre-existing config shape without the new field."""

    values = vars(_config()).copy()
    del values["stream_asr_partials_enabled"]
    values.update(overrides)
    return SimpleNamespace(**values)


def test_missing_field_keeps_the_old_behaviour(tmp_path) -> None:
    asr = _FakeASR()
    _runtime, _path, text = _capture(tmp_path, _legacy_config(), _utterance(), asr)
    assert text == FINAL_TEXT
    assert asr.preview_pcm, "a config without the new field must keep previews on"


def test_max_frames_path_is_unchanged_without_previews(tmp_path) -> None:
    asr = _FakeASR()
    cfg = _config(stream_asr_partials_enabled=False, max_record_seconds=1.0)

    runtime, output_wav, text = _capture(tmp_path, cfg, _speech_frames(200), asr)

    assert text == FINAL_TEXT
    assert len(asr.final_pcm) == 1
    assert asr.preview_pcm == []
    assert output_wav.is_file()
    assert runtime.event_types().count("vad_stopped") == 1


def test_no_speech_decodes_nothing(tmp_path) -> None:
    asr = _FakeASR()
    with pytest.raises(NoSpeechDetectedError):
        _capture(
            tmp_path,
            _config(stream_asr_partials_enabled=False, max_record_seconds=1.0),
            _silence_frames(50),
            asr,
        )
    assert asr.preview_pcm == []
    assert asr.final_pcm == []


def test_cancellation_cleans_up_without_previews(tmp_path) -> None:
    asr = _FakeASR()
    handle = _Handle()
    handle.cancelled.set()
    with pytest.raises(TurnCancelledError):
        _capture(
            tmp_path,
            _config(stream_asr_partials_enabled=False),
            _speech_frames(120),
            asr,
            handle=handle,
        )
    assert asr.preview_pcm == []
    assert asr.final_pcm == []
    assert _FakeDevice.instance is not None
    assert _FakeDevice.instance.stream is not None
    assert _FakeDevice.instance.stream.stopped is True


def test_final_error_propagates_and_is_never_replaced_by_a_preview(tmp_path) -> None:
    asr = _FakeASR(final_error=RuntimeError("synthetic final failure"))
    with pytest.raises(RuntimeError):
        _capture(
            tmp_path,
            _config(stream_asr_partials_enabled=True),
            _utterance(),
            asr,
        )
    assert asr.preview_pcm, "previews ran before the failing final decode"
    assert len(asr.final_pcm) == 1


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("No", False), ("off", False),
])
def test_strict_env_parsing(monkeypatch, value, expected) -> None:
    monkeypatch.setenv("STREAM_ASR_PARTIALS_ENABLED", value)
    assert env_asr_partials_enabled() is expected


def test_env_default_and_invalid_value(monkeypatch) -> None:
    monkeypatch.delenv("STREAM_ASR_PARTIALS_ENABLED", raising=False)
    assert env_asr_partials_enabled() is True
    assert AppConfig().stream_asr_partials_enabled is True

    monkeypatch.setenv("STREAM_ASR_PARTIALS_ENABLED", "maybe")
    with pytest.raises(ValueError):
        env_asr_partials_enabled()
    with pytest.raises(ValueError):
        AppConfig.from_env()


def test_snapshot_whitelist_records_the_switch() -> None:
    # The snapshot whitelist is explicit, not derived from dataclass fields.
    assert "stream_asr_partials_enabled" in AppConfig.__dataclass_fields__

    from tools import workbench_latency

    assert "stream_asr_partials_enabled" in workbench_latency.CONFIG_KEYS
