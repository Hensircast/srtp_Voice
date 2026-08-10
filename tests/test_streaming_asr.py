from __future__ import annotations

import struct
import types
import wave
from pathlib import Path

from srtp_voice.config import AppConfig
from srtp_voice.streaming import AudioChunk
from srtp_voice.streaming_asr import (
    BoundedAudioFrameQueue,
    IncrementalASRSession,
    MicrophoneFrameStream,
    PCM16ASRTranscriber,
    StreamingUtteranceCollector,
)


def _frame(amplitude: int, samples: int = 10) -> bytes:
    return struct.pack("<" + "h" * samples, *([amplitude] * samples))


def _audio(pcm16: bytes, sequence: int, *, sample_rate: int = 1000) -> AudioChunk:
    return AudioChunk(
        pcm16,
        sample_rate,
        turn_id="turn-1",
        sequence_id=sequence,
    )


def test_audio_callback_queue_is_bounded_with_drop_newest_metrics() -> None:
    times = iter([1.0, 1.1, 1.2])
    frames = BoundedAudioFrameQueue(
        sample_rate=16000,
        maxsize=2,
        turn_id="turn-1",
        clock=lambda: next(times),
    )

    assert frames.put(b"one") is True
    assert frames.put(b"two") is True
    assert frames.put(b"three") is False
    first = frames.get(timeout=0)
    second = frames.get(timeout=0)
    frames.task_done()
    frames.task_done()

    assert frames.capacity == 2
    assert frames.dropped_frames == 1
    assert (first.pcm16, first.sequence_id, first.timestamp_ms) == (b"one", 0, 1000)
    assert (second.pcm16, second.sequence_id, second.timestamp_ms) == (b"two", 1, 1100)


def test_microphone_raw_callback_only_enqueues_pcm_frames() -> None:
    created = []

    class FakeRawInputStream:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.started = False
            self.stopped = False
            self.closed = False
            created.append(self)

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

        def close(self):
            self.closed = True

    fake_sd = types.SimpleNamespace(RawInputStream=FakeRawInputStream)
    stream = MicrophoneFrameStream(
        sample_rate=1000,
        frame_ms=10,
        queue_maxsize=2,
        turn_id="turn-mic",
        sounddevice_module=fake_sd,
        clock=lambda: 2.0,
    )
    stream.start()
    raw_stream = created[0]
    status = types.SimpleNamespace(input_overflow=True)
    raw_stream.kwargs["callback"](_frame(100), 10, {}, status)
    stream.stop()
    chunks = list(stream.chunks(timeout=0))

    assert raw_stream.kwargs == {
        "samplerate": 1000,
        "channels": 1,
        "dtype": "int16",
        "blocksize": 10,
        "callback": raw_stream.kwargs["callback"],
    }
    assert raw_stream.started and raw_stream.stopped and raw_stream.closed
    assert len(chunks) == 1
    assert chunks[0].pcm16 == _frame(100)
    assert chunks[0].turn_id == "turn-mic"
    assert stream.callback_calls == 1
    assert stream.overflow_events == 1


def test_streaming_vad_calibration_preroll_and_silence_tail_are_preserved() -> None:
    collector = StreamingUtteranceCollector(
        sample_rate=1000,
        frame_ms=10,
        threshold=0.01,
        min_speech_ms=20,
        silence_ms=20,
        pre_roll_ms=20,
        calibration_ms=20,
        noise_multiplier=2.0,
        release_ratio=0.5,
    )
    silence = _frame(0)
    speech = _frame(1000)
    frames = [silence, silence, silence, speech, speech, speech, silence, silence]
    starts = 0
    final = None
    for sequence, pcm16 in enumerate(frames):
        update = collector.feed(_audio(pcm16, sequence))
        starts += int(update.vad_started)
        if update.completed_pcm16 is not None:
            final = update.completed_pcm16

    assert starts == 1
    assert final == b"".join([silence, speech, speech, speech, silence, silence])
    assert collector.pcm16 == final


def test_streaming_vad_finish_keeps_last_frames_without_trailing_silence() -> None:
    collector = StreamingUtteranceCollector(
        sample_rate=1000,
        frame_ms=10,
        threshold=0.01,
        min_speech_ms=10,
        silence_ms=100,
        pre_roll_ms=10,
        calibration_ms=0,
    )
    first = _frame(1000)
    last = _frame(1200)
    assert collector.feed(_audio(first, 0)).vad_started is True
    collector.feed(_audio(last, 1))

    update = collector.finish()

    assert update.vad_stopped is True
    assert update.completed_pcm16 == first + last


def test_incremental_asr_emits_changed_partials_and_exactly_one_final() -> None:
    replies = iter(["你", "你", "你好", "你好啊"])
    transcribed_sizes = []

    def transcribe(pcm16):
        transcribed_sizes.append(len(pcm16))
        return next(replies)

    session = IncrementalASRSession(
        transcribe,
        turn_id="turn-asr",
        partial_interval_seconds=0.5,
    )

    first = session.maybe_partial(b"a", now=1.0)
    too_soon = session.maybe_partial(b"ab", now=1.2)
    unchanged = session.maybe_partial(b"abc", now=1.5)
    changed = session.maybe_partial(b"abcd", now=2.0)
    final = session.finalize(b"abcde", now=2.1)
    duplicate_final = session.finalize(b"abcdef", now=2.2)
    after_final_partial = session.maybe_partial(b"abcdef", now=3.0)

    assert first is not None and (first.text, first.is_final) == ("你", False)
    assert too_soon is None
    assert unchanged is None
    assert changed is not None and (changed.text, changed.is_final) == ("你好", False)
    assert final is not None and (final.text, final.is_final) == ("你好啊", True)
    assert duplicate_final is None
    assert after_final_partial is None
    assert [first.sequence_id, changed.sequence_id, final.sequence_id] == [0, 1, 2]
    assert all(chunk.turn_id == "turn-asr" for chunk in [first, changed, final])
    assert transcribed_sizes == [1, 3, 4, 5]

    llm_submissions = [
        chunk.text for chunk in [first, changed, final] if chunk.is_final and chunk.text
    ]
    assert llm_submissions == ["你好啊"]


def test_pcm_transcriber_uses_valid_temporary_wav_and_cleans_it(tmp_path) -> None:
    observed_paths = []

    class FakeAdapter:
        def transcribe(self, path):
            observed_paths.append(path)
            assert path.exists()
            with wave.open(str(path), "rb") as wav_file:
                assert wav_file.getframerate() == 1000
                assert wav_file.getnchannels() == 1
                assert wav_file.getsampwidth() == 2
                assert wav_file.readframes(wav_file.getnframes()) == _frame(500)
            return "最终文本"

    transcribe = PCM16ASRTranscriber(
        FakeAdapter(),
        sample_rate=1000,
        temp_parent=tmp_path,
    )

    assert transcribe(_frame(500)) == "最终文本"
    assert len(observed_paths) == 1
    assert not observed_paths[0].exists()


def test_streaming_config_defaults_and_bounds(monkeypatch) -> None:
    import srtp_voice.config as config_module

    names = [
        "STREAM_AUDIO_QUEUE_SIZE",
        "STREAM_TTS_QUEUE_SIZE",
        "STREAM_SENTENCE_MIN_CHARS",
        "STREAM_SENTENCE_MAX_CHARS",
        "STREAM_SENTENCE_MAX_WAIT_SECONDS",
        "STREAM_ASR_PARTIAL_INTERVAL_SECONDS",
        "STREAM_BARGE_IN_ENABLED",
    ]
    monkeypatch.setattr(config_module, "load_dotenv", None)
    for name in names:
        monkeypatch.delenv(name, raising=False)

    cfg = AppConfig.from_env()
    assert cfg.stream_audio_queue_size == 32
    assert cfg.stream_tts_queue_size == 4
    assert cfg.stream_sentence_min_chars == 12
    assert cfg.stream_sentence_max_chars == 80
    assert cfg.stream_sentence_max_wait_seconds == 0.8
    assert cfg.stream_asr_partial_interval_seconds == 0.8
    assert cfg.stream_barge_in_enabled is False

    monkeypatch.setenv("STREAM_AUDIO_QUEUE_SIZE", "0")
    monkeypatch.setenv("STREAM_TTS_QUEUE_SIZE", "999")
    monkeypatch.setenv("STREAM_SENTENCE_MIN_CHARS", "0")
    monkeypatch.setenv("STREAM_SENTENCE_MAX_CHARS", "invalid")
    monkeypatch.setenv("STREAM_SENTENCE_MAX_WAIT_SECONDS", "0")
    monkeypatch.setenv("STREAM_ASR_PARTIAL_INTERVAL_SECONDS", "nan")
    monkeypatch.setenv("STREAM_BARGE_IN_ENABLED", "1")
    cfg = AppConfig.from_env()

    assert cfg.stream_audio_queue_size == 1
    assert cfg.stream_tts_queue_size == 256
    assert cfg.stream_sentence_min_chars == 1
    assert cfg.stream_sentence_max_chars == 80
    assert cfg.stream_sentence_max_wait_seconds == 0.01
    assert cfg.stream_asr_partial_interval_seconds == 0.8
    assert cfg.stream_barge_in_enabled is True

    env_example = Path(".env.example").read_text(encoding="utf-8")
    for name in names:
        assert f"{name}=" in env_example
