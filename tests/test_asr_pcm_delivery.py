"""In-memory PCM16 delivery tests: exact samples, fallback and error paths."""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest

from srtp_voice.asr import ASRAdapter
from srtp_voice.config import AppConfig
from srtp_voice.streaming_asr import PCM16ASRTranscriber


class _Segment:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeWhisperModel:
    def __init__(self, text: str = "识别文本") -> None:
        self.text = text
        self.calls: list[dict] = []

    def transcribe(self, source, **kwargs):
        self.calls.append({"source": source, "kwargs": kwargs})
        return iter([_Segment(self.text)]), object()


def _adapter(monkeypatch, model: _FakeWhisperModel) -> ASRAdapter:
    monkeypatch.setattr(ASRAdapter, "_load_faster_whisper_model", lambda self: model)
    return ASRAdapter(AppConfig(asr_backend="faster_whisper"))


def test_pcm16_samples_are_exact_and_untouched(monkeypatch) -> None:
    import numpy as np

    model = _FakeWhisperModel()
    adapter = _adapter(monkeypatch, model)
    samples = [-32768, -1, 0, 1, 32767]
    pcm16 = struct.pack("<" + "h" * len(samples), *samples)

    text = adapter.transcribe_pcm16(pcm16, sample_rate=16000)

    assert text == "识别文本"
    source = model.calls[0]["source"]
    assert isinstance(source, np.ndarray)
    assert source.dtype == np.float32
    assert source.tolist() == [value / 32768.0 for value in samples]


def test_pcm16_uses_the_same_options_as_the_file_path(monkeypatch, tmp_path) -> None:
    model = _FakeWhisperModel()
    adapter = _adapter(monkeypatch, model)
    cfg = adapter.cfg
    wav_path = tmp_path / "snapshot.wav"
    with wave.open(str(wav_path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(struct.pack("<hh", 1, -1))

    adapter.transcribe(wav_path)
    adapter.transcribe_pcm16(struct.pack("<hh", 1, -1), sample_rate=16000)

    file_kwargs = model.calls[0]["kwargs"]
    memory_kwargs = model.calls[1]["kwargs"]
    assert file_kwargs == memory_kwargs
    assert file_kwargs["language"] == cfg.asr_language
    assert file_kwargs["beam_size"] == cfg.asr_beam_size
    assert file_kwargs["vad_filter"] is cfg.asr_vad_filter
    assert file_kwargs["condition_on_previous_text"] is cfg.asr_condition_on_previous_text
    assert file_kwargs["vad_parameters"] == {"min_silence_duration_ms": cfg.asr_min_silence_ms}


def test_unsupported_backend_and_rate_return_none_without_model_calls(monkeypatch) -> None:
    mock_adapter = ASRAdapter(AppConfig(asr_backend="mock"))
    assert mock_adapter.transcribe_pcm16(struct.pack("<h", 1), sample_rate=16000) is None

    model = _FakeWhisperModel()
    adapter = _adapter(monkeypatch, model)
    assert adapter.transcribe_pcm16(struct.pack("<h", 1), sample_rate=8000) is None
    assert adapter.transcribe_pcm16(struct.pack("<h", 1), sample_rate=44100) is None
    assert model.calls == []


def test_empty_and_odd_length_input(monkeypatch) -> None:
    model = _FakeWhisperModel()
    adapter = _adapter(monkeypatch, model)

    assert adapter.transcribe_pcm16(b"", sample_rate=16000) == ""
    assert model.calls == []

    with pytest.raises(ValueError):
        adapter.transcribe_pcm16(b"\x01", sample_rate=16000)
    assert model.calls == []


def test_generator_failure_keeps_the_cause(monkeypatch) -> None:
    class _Boom:
        def transcribe(self, source, **kwargs):
            raise ValueError("synthetic decode failure")

    monkeypatch.setattr(ASRAdapter, "_load_faster_whisper_model", lambda self: _Boom())
    adapter = ASRAdapter(AppConfig(asr_backend="faster_whisper"))

    with pytest.raises(RuntimeError) as error:
        adapter.transcribe_pcm16(struct.pack("<hh", 1, 2), sample_rate=16000)

    assert isinstance(error.value.__cause__, ValueError)
    assert "<pcm16-memory>" in str(error.value)


def test_transcriber_prefers_memory_then_falls_back(tmp_path) -> None:
    class _Adapter:
        def __init__(self, result):
            self.result = result
            self.memory_calls: list[dict] = []
            self.file_calls: list[Path] = []

        def transcribe_pcm16(self, pcm16, *, sample_rate):
            self.memory_calls.append({"bytes": pcm16, "rate": sample_rate})
            return self.result

        def transcribe(self, wav_path):
            self.file_calls.append(Path(wav_path))
            return "文件路径结果"

    memory = _Adapter("内存结果")
    transcriber = PCM16ASRTranscriber(memory, sample_rate=16000, temp_parent=tmp_path)
    assert transcriber(struct.pack("<h", 3)) == "内存结果"
    assert memory.memory_calls == [{"bytes": struct.pack("<h", 3), "rate": 16000}]
    assert memory.file_calls == []

    empty = _Adapter("")
    assert PCM16ASRTranscriber(empty, temp_parent=tmp_path)(struct.pack("<h", 3)) == ""
    assert empty.file_calls == []

    unsupported = _Adapter(None)
    assert PCM16ASRTranscriber(unsupported, temp_parent=tmp_path)(struct.pack("<h", 3)) == "文件路径结果"
    assert len(unsupported.file_calls) == 1
    assert not list(tmp_path.rglob("srtp-voice-asr-*"))

    disabled = _Adapter("内存结果")
    assert (
        PCM16ASRTranscriber(disabled, temp_parent=tmp_path, prefer_in_memory=False)(
            struct.pack("<h", 3)
        )
        == "文件路径结果"
    )
    assert disabled.memory_calls == []


def test_transcriber_type_and_exception_are_not_retried(tmp_path) -> None:
    class _BadType:
        def transcribe_pcm16(self, pcm16, *, sample_rate):
            return 42

        def transcribe(self, wav_path):  # pragma: no cover - must not run
            raise AssertionError("a bad return type must not fall back")

    with pytest.raises(TypeError):
        PCM16ASRTranscriber(_BadType(), temp_parent=tmp_path)(struct.pack("<h", 1))

    class _Explodes:
        def transcribe_pcm16(self, pcm16, *, sample_rate):
            raise RuntimeError("synthetic model failure")

        def transcribe(self, wav_path):  # pragma: no cover - must not run
            raise AssertionError("a failure must not be retried on the file path")

    with pytest.raises(RuntimeError):
        PCM16ASRTranscriber(_Explodes(), temp_parent=tmp_path)(struct.pack("<h", 1))


def test_empty_pcm_never_calls_the_adapter(tmp_path) -> None:
    class _Adapter:
        def transcribe_pcm16(self, pcm16, *, sample_rate):  # pragma: no cover - unused
            raise AssertionError("empty PCM must not reach the adapter")

        def transcribe(self, wav_path):  # pragma: no cover - unused
            raise AssertionError("empty PCM must not reach the adapter")

    assert PCM16ASRTranscriber(_Adapter(), temp_parent=tmp_path)(b"") == ""
