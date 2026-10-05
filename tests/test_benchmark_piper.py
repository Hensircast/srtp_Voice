"""Offline benchmark interface tests, not real model measurements."""

import struct
import wave
from pathlib import Path

import pytest

from srtp_voice.config import AppConfig
from tools import benchmark_piper as module


@pytest.mark.parametrize("mode", ["one-shot", "persistent"])
def test_benchmark_uses_public_text_and_closes_owned_adapter(tmp_path, monkeypatch, mode):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"fake-model")
    calls = []
    closed = []

    class Adapter:
        def __init__(self, cfg):
            assert cfg.tts_backend == "piper"
            assert cfg.tts_piper_persistent is (mode == "persistent")

        def for_streaming(self):
            calls.append("factory")
            return self

        def synthesize(self, text, path):
            calls.append(text)
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(struct.pack("<hh", 100, 200))

        def close(self):
            closed.append(True)

    monkeypatch.setattr(module, "TTSAdapter", Adapter)
    output = tmp_path / "new-benchmark"
    report = module.benchmark(AppConfig(tts_piper_model=model), output, mode)
    assert calls == (["factory"] if mode == "persistent" else []) + list(module.PUBLIC_TEXTS)
    assert report["microphone"] is False
    assert report["playback"] is False
    assert report["subjective_listening_verified"] is False
    assert len(report["rows"]) == 6
    assert len(list(output.glob("public-*.wav"))) == 6
    assert closed == [True]


def test_output_validation_rejects_existing_or_outside_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    existing = outputs / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        module.new_output_directory(str(existing))
    with pytest.raises(ValueError):
        module.new_output_directory(str(outputs))
    with pytest.raises(ValueError):
        module.new_output_directory(str(tmp_path / "another"))
    assert module.new_output_directory(str(outputs / "fresh")) == outputs / "fresh"
