"""Endpoint frames must go straight to final ASR, not start another partial."""
from concurrent.futures import Future
from types import SimpleNamespace
import threading

import pytest

from srtp_voice.config import AppConfig
from srtp_voice.streaming import AudioChunk, StreamEventType
from srtp_voice.streaming_asr import VADStreamUpdate
from srtp_voice.streaming_runtime import TurnHandle, capture_streaming_microphone


@pytest.mark.parametrize("vad_stop", [True, False])
@pytest.mark.parametrize("silence_frames", [0, 4])
def test_endpoint_does_not_schedule_partial(monkeypatch, tmp_path, vad_stop, silence_frames):
    import srtp_voice.streaming_runtime as module

    calls = []
    pcm = b"\x10\x00" * 160

    class Collector:
        partial_ready = False
        frames = 0

        @property
        def pcm16(self):
            raise AssertionError("silence must not copy a full ASR snapshot")

        def feed(self, frame):
            self.frames += 1
            if self.frames <= silence_frames:
                return VADStreamUpdate()
            return VADStreamUpdate(
                vad_stopped=vad_stop,
                completed_pcm16=pcm if vad_stop else None,
            )

        def finish(self):
            return VADStreamUpdate(vad_stopped=True, completed_pcm16=pcm)

    class Microphone:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def chunks(self):
            for _ in range(silence_frames + 1):
                yield AudioChunk(pcm, 16000)

    class Executor:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def submit(self, fn, *args):
            calls.append("partial")
            future = Future()
            future.set_result(fn(*args))
            return future

    monkeypatch.setattr(module.StreamingUtteranceCollector, "from_config", lambda cfg: Collector())
    monkeypatch.setattr(module, "MicrophoneFrameStream", Microphone)
    monkeypatch.setattr(module, "ThreadPoolExecutor", Executor)
    monkeypatch.setattr(module, "PCM16ASRTranscriber", lambda *a, **kw: lambda data: "final text")
    events = []
    runtime = SimpleNamespace(emit=lambda handle, event, payload=None: events.append(event))
    cfg = AppConfig(output_dir=tmp_path, max_record_seconds=0.032 * (silence_frames + 1), frame_ms=32)
    text = capture_streaming_microphone(
        cfg, object(), runtime, TurnHandle("test", threading.Event()), tmp_path / "input.wav"
    )
    assert text == "final text"
    assert calls == []
    assert events.count(StreamEventType.ASR_FINAL) == 1
    assert StreamEventType.ASR_PARTIAL not in events
    assert events.index(StreamEventType.VAD_STOPPED) < events.index(StreamEventType.ASR_FINAL)
