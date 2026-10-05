"""Codex-owned gates: disabling previews never substitutes for full final ASR.

No model or physical microphone. The real collector, frame queue, session and
executor run against a small deterministic input-stream double.
"""

from __future__ import annotations

import struct
import threading
import wave
from types import SimpleNamespace

import pytest

from srtp_voice import config as config_module
from srtp_voice import streaming_runtime as runtime_module
from srtp_voice.audio_io import NoSpeechDetectedError
from srtp_voice.config import AppConfig
from srtp_voice.streaming import StreamEventType
from srtp_voice.streaming_asr import StreamingUtteranceCollector
from srtp_voice.streaming_runtime import TurnCancelledError, TurnHandle
from tools.workbench_latency import _config_view, _fingerprint


def _frame(value: int) -> bytes:
    return struct.pack("<160h", *([value] * 160))


def _run(tmp_path, frames, *, enabled=False, limit=1.0, missing=False,
         fail_final=False, cancelled=False, monkeypatch=None):
    cfg = AppConfig(output_dir=tmp_path, sample_rate=16000, frame_ms=10,
                    min_speech_ms=10, pre_roll_ms=0, vad_calibration_ms=0,
                    silence_ms=20, max_record_seconds=limit,
                    stream_audio_queue_size=16,
                    stream_asr_partial_interval_seconds=0.05)
    cfg.stream_asr_partials_enabled = enabled
    if missing:
        values = vars(cfg).copy()
        del values["stream_asr_partials_enabled"]
        cfg = SimpleNamespace(**values)

    closed = []
    decoded = []
    events = []

    class Input:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def start(self):
            for frame in frames:
                self.callback(frame, 160, {}, SimpleNamespace(input_overflow=False))

        def stop(self):
            closed.append("stop")

        def close(self):
            closed.append("close")

    class ASR:
        def transcribe(self, path):
            with wave.open(str(path), "rb") as wav:
                pcm = wav.readframes(wav.getnframes())
            decoded.append(pcm)
            final_pcm = b"".join(frames)
            if pcm == final_pcm and fail_final:
                raise RuntimeError("final decode failed")
            return "complete final" if pcm == final_pcm else "preview only"

    if monkeypatch is not None:
        original = StreamingUtteranceCollector.pcm16

        def guarded_snapshot(collector):
            if not enabled:
                pytest.fail("final-only mode copied a preview PCM snapshot")
            return original.fget(collector)

        monkeypatch.setattr(StreamingUtteranceCollector, "pcm16", property(guarded_snapshot))

    handle = TurnHandle("independent-input", threading.Event())
    if cancelled:
        handle.cancelled.set()
    runtime = SimpleNamespace(emit=lambda handle, kind, payload=None: events.append(kind))
    path = tmp_path / "recorded.wav"
    try:
        text = runtime_module.capture_streaming_microphone(
            cfg, ASR(), runtime, handle, path,
            sounddevice_module=SimpleNamespace(RawInputStream=Input),
        )
    finally:
        assert closed == ["stop", "close"]
    return text, path, decoded, events


@pytest.mark.parametrize("limit,frames", [
    (1.0, [_frame(4000), _frame(5000), _frame(0), _frame(0)]),
    (0.02, [_frame(4000), _frame(5000)]),
])
def test_final_only_keeps_every_captured_frame_and_decodes_once(tmp_path, monkeypatch, limit, frames):
    text, path, calls, events = _run(tmp_path, frames, limit=limit, monkeypatch=monkeypatch)
    assert text == "complete final"
    assert calls == [b"".join(frames)]
    with wave.open(str(path), "rb") as wav:
        assert wav.getnframes() == 160 * len(frames)
        assert wav.readframes(wav.getnframes()) == b"".join(frames)
    assert events.count(StreamEventType.ASR_FINAL) == 1
    assert StreamEventType.ASR_PARTIAL not in events
    assert events.index(StreamEventType.VAD_STOPPED) < events.index(StreamEventType.ASR_FINAL)


@pytest.mark.parametrize("missing", [False, True])
def test_enabled_and_legacy_namespace_retain_preview_and_independent_final(tmp_path, missing):
    frames = [_frame(4000), _frame(5000), _frame(0), _frame(0)]
    text, _, calls, events = _run(tmp_path, frames, enabled=True, missing=missing)
    assert text == "complete final"
    assert len(calls) >= 2
    assert calls[-1] == b"".join(frames)
    assert any(pcm != calls[-1] for pcm in calls[:-1])
    assert events.count(StreamEventType.ASR_PARTIAL) >= 1
    assert events.count(StreamEventType.ASR_FINAL) == 1


def test_no_speech_does_not_decode_or_write_placeholder(tmp_path):
    with pytest.raises(NoSpeechDetectedError):
        _run(tmp_path, [_frame(0)] * 4, limit=0.04)
    assert not (tmp_path / "recorded.wav").exists()


def test_cancelled_capture_closes_input_without_writing_audio(tmp_path):
    with pytest.raises(TurnCancelledError):
        _run(tmp_path, [_frame(4000)], cancelled=True)
    assert not (tmp_path / "recorded.wav").exists()


@pytest.mark.parametrize("enabled", [False, True])
def test_final_error_is_never_hidden_by_a_successful_preview(tmp_path, enabled):
    with pytest.raises(RuntimeError, match="final decode failed"):
        _run(tmp_path, [_frame(4000), _frame(5000), _frame(0), _frame(0)],
             enabled=enabled, fail_final=True)


def test_provenance_distinguishes_modes_without_private_values():
    original = AppConfig()
    final_only = AppConfig()
    final_only.stream_asr_partials_enabled = False
    left, right = _config_view(original), _config_view(final_only)
    assert left["stream_asr_partials_enabled"] is True
    assert right["stream_asr_partials_enabled"] is False
    assert _fingerprint(left) != _fingerprint(right)
    assert set(left) == set(right)


def test_invalid_mode_value_does_not_leak_environment_contents(monkeypatch):
    invalid = "private-invalid-value-must-not-appear"
    monkeypatch.setenv("STREAM_ASR_PARTIALS_ENABLED", invalid)
    with pytest.raises(ValueError) as error:
        config_module.env_asr_partials_enabled()
    assert "STREAM_ASR_PARTIALS_ENABLED" in str(error.value)
    assert invalid not in str(error.value)
