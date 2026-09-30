"""Independent memory-ASR contracts; no weights, microphone or private audio."""

import struct
import sys
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from srtp_voice.asr import ASRAdapter
from srtp_voice.config import AppConfig
from srtp_voice.streaming_asr import PCM16ASRTranscriber
from tools.workbench_latency import _config_view, _fingerprint


def _adapter(monkeypatch, *, broken=False):
    calls = []

    class Model:
        def __init__(self, *args, **kwargs):
            calls.append("init")

        def transcribe(self, audio, **kwargs):
            calls.append((audio, kwargs))

            def segments():
                if broken:
                    raise ValueError("fixture generator failure")
                yield SimpleNamespace(text=" 公开句子 ")

            return segments(), None

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    cfg = AppConfig(asr_backend="faster_whisper", asr_beam_size=2, asr_min_silence_ms=700)
    return ASRAdapter(cfg), calls


def test_exact_little_endian_float32_conversion_and_shared_options(monkeypatch, tmp_path):
    adapter, calls = _adapter(monkeypatch)
    values = [-32768, -1, 0, 1, 32767]
    pcm = struct.pack("<5h", *values)
    assert adapter.transcribe_pcm16(pcm, sample_rate=16000) == "公开句子"
    array, kwargs = calls[-1]
    assert array.dtype == np.float32 and array.ndim == 1
    np.testing.assert_array_equal(array, np.array(values, dtype=np.float32) / 32768)
    path = tmp_path / "fixture.wav"
    path.write_bytes(b"fake model never reads this fixture")
    assert adapter.transcribe(path) == "公开句子"
    assert calls[-1][0] == str(path)
    assert calls[-1][1] == kwargs
    assert kwargs["beam_size"] == 2 and kwargs["vad_parameters"] == {"min_silence_duration_ms": 700}
    assert calls.count("init") == 1


def test_unsupported_rate_empty_and_misaligned_pcm_are_not_decoded(monkeypatch):
    adapter, calls = _adapter(monkeypatch)
    assert adapter.transcribe_pcm16(b"", sample_rate=16000) == ""
    assert adapter.transcribe_pcm16(b"\x00\x00", sample_rate=48000) is None
    with pytest.raises(ValueError):
        adapter.transcribe_pcm16(b"x", sample_rate=16000)
    assert calls == ["init"]
    assert ASRAdapter(AppConfig()).transcribe_pcm16(b"\x00\x00", sample_rate=16000) is None


def test_generator_failure_has_cause_and_is_not_silently_retried(monkeypatch):
    adapter, calls = _adapter(monkeypatch, broken=True)
    with pytest.raises(RuntimeError) as caught:
        adapter.transcribe_pcm16(b"\x00\x00", sample_rate=16000)
    assert isinstance(caught.value.__cause__, ValueError)
    assert len(calls) == 2


@pytest.mark.parametrize("result", ["final", ""])
def test_memory_result_never_creates_temporary_files(tmp_path, result):
    calls = []

    class Adapter:
        def transcribe_pcm16(self, data, *, sample_rate):
            calls.append((data, sample_rate))
            return result

        def transcribe(self, path):
            raise AssertionError("successful memory path must not touch WAV")

    parent = tmp_path / "absent"
    transcribe = PCM16ASRTranscriber(Adapter(), temp_parent=parent)
    assert transcribe(b"\x00\x00") == result
    assert calls == [(b"\x00\x00", 16000)]
    assert not parent.exists()


@pytest.mark.parametrize("unsupported", [True, False])
def test_only_unsupported_or_disabled_hook_falls_back_and_cleans(tmp_path, unsupported):
    paths = []
    hook_calls = []

    class Adapter:
        def transcribe_pcm16(self, data, *, sample_rate):
            hook_calls.append(True)
            return None

        def transcribe(self, path):
            assert path.is_file()
            paths.append(path)
            return "file result"

    transcribe = PCM16ASRTranscriber(Adapter(), temp_parent=tmp_path, prefer_in_memory=unsupported)
    assert transcribe(b"\x00\x00") == "file result"
    assert hook_calls == ([True] if unsupported else [])
    assert len(paths) == 1 and not paths[0].exists()


@pytest.mark.parametrize("bad", [TypeError("internal hook error"), 42])
def test_hook_failure_or_invalid_result_never_retries_file(tmp_path, bad):
    class Adapter:
        def transcribe_pcm16(self, data, *, sample_rate):
            if isinstance(bad, Exception):
                raise bad
            return bad

        def transcribe(self, path):
            raise AssertionError("failure must not trigger duplicate decode")

    with pytest.raises(TypeError):
        PCM16ASRTranscriber(Adapter(), temp_parent=tmp_path)(b"\x00\x00")
    assert list(tmp_path.iterdir()) == []


def test_config_default_and_snapshot_gate(monkeypatch):
    monkeypatch.delenv("STREAM_ASR_IN_MEMORY", raising=False)
    assert AppConfig.from_env().stream_asr_in_memory is True
    monkeypatch.setenv("STREAM_ASR_IN_MEMORY", "0")
    assert AppConfig.from_env().stream_asr_in_memory is False
    cfg = AppConfig()
    old = _config_view(cfg)
    disabled = _config_view(replace(cfg, stream_asr_in_memory=False))
    assert old["stream_asr_in_memory"] is True
    assert _fingerprint(old) != _fingerprint(disabled)
