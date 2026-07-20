from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest

from srtp_voice.asr import ASRAdapter
from srtp_voice.config import AppConfig


class Segment:
    def __init__(self, text: str):
        self.text = text


def _install_fake_faster_whisper(monkeypatch, segments):
    state = {
        "init_calls": [],
        "transcribe_calls": [],
        "segments_consumed": 0,
    }

    class FakeWhisperModel:
        def __init__(self, model, device, compute_type, cpu_threads):
            state["init_calls"].append({
                "model": model,
                "device": device,
                "compute_type": compute_type,
                "cpu_threads": cpu_threads,
            })

        def transcribe(self, audio_path, **kwargs):
            state["transcribe_calls"].append({
                "audio_path": audio_path,
                "kwargs": kwargs,
            })

            def generate_segments():
                for segment in segments:
                    state["segments_consumed"] += 1
                    yield segment

            return generate_segments(), {"language": kwargs.get("language")}

    fake_module = types.SimpleNamespace(WhisperModel=FakeWhisperModel)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    return state


def test_mock_backend_without_faster_whisper(monkeypatch, tmp_path) -> None:
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    cfg = AppConfig(asr_backend="mock")
    result = ASRAdapter(cfg).transcribe(tmp_path / "missing.wav")
    assert result


@pytest.mark.parametrize(
    "backend",
    ["sensevoice_onnx", "sensevoice", "funasr", "whisper_cpp", "unknown"],
)
def test_unsupported_asr_backends_are_rejected(backend) -> None:
    with pytest.raises(ValueError, match="unsupported ASR_BACKEND") as exc_info:
        ASRAdapter(AppConfig(asr_backend=backend))

    message = str(exc_info.value)
    assert "mock" in message
    assert "faster_whisper" in message


def test_faster_whisper_missing_dependency(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    cfg = AppConfig(asr_backend="faster_whisper")
    try:
        ASRAdapter(cfg)
    except RuntimeError as exc:
        message = str(exc)
        assert "faster-whisper 未安装" in message
        assert "python -m pip install faster-whisper" in message
        return
    raise AssertionError("missing faster-whisper should raise")


def test_faster_whisper_model_init_and_transcribe_config(monkeypatch, tmp_path) -> None:
    wav_path = tmp_path / "input.wav"
    wav_path.write_bytes(b"fake wav")
    state = _install_fake_faster_whisper(
        monkeypatch,
        [Segment(" 你好"), Segment(" 世界 ")],
    )

    cfg = AppConfig(
        asr_backend="faster_whisper",
        asr_model="small",
        asr_device="cpu",
        asr_compute_type="int8",
        asr_language="zh",
        asr_cpu_threads=6,
        asr_beam_size=2,
        asr_vad_filter=True,
        asr_min_silence_ms=700,
        asr_condition_on_previous_text=False,
    )
    adapter = ASRAdapter(cfg)
    result = adapter.transcribe(wav_path)

    assert result == "你好 世界"
    assert state["init_calls"] == [{
        "model": "small",
        "device": "cpu",
        "compute_type": "int8",
        "cpu_threads": 6,
    }]
    assert len(state["transcribe_calls"]) == 1
    call = state["transcribe_calls"][0]
    assert call["audio_path"] == str(wav_path)
    assert call["kwargs"]["language"] == "zh"
    assert call["kwargs"]["task"] == "transcribe"
    assert call["kwargs"]["beam_size"] == 2
    assert call["kwargs"]["vad_filter"] is True
    assert call["kwargs"]["condition_on_previous_text"] is False
    assert call["kwargs"]["vad_parameters"] == {"min_silence_duration_ms": 700}
    assert state["segments_consumed"] == 2


def test_faster_whisper_model_created_once(monkeypatch, tmp_path) -> None:
    wav_path = tmp_path / "input.wav"
    wav_path.write_bytes(b"fake wav")
    state = _install_fake_faster_whisper(monkeypatch, [Segment("ok")])
    adapter = ASRAdapter(AppConfig(asr_backend="faster_whisper"))

    adapter.transcribe(wav_path)
    adapter.transcribe(wav_path)

    assert len(state["init_calls"]) == 1
    assert len(state["transcribe_calls"]) == 2


def test_faster_whisper_without_internal_vad(monkeypatch, tmp_path) -> None:
    wav_path = tmp_path / "input.wav"
    wav_path.write_bytes(b"fake wav")
    state = _install_fake_faster_whisper(monkeypatch, [Segment("ok")])
    cfg = AppConfig(
        asr_backend="faster_whisper",
        asr_vad_filter=False,
        asr_condition_on_previous_text=True,
    )
    result = ASRAdapter(cfg).transcribe(wav_path)

    kwargs = state["transcribe_calls"][0]["kwargs"]
    assert result == "ok"
    assert kwargs["vad_filter"] is False
    assert kwargs["condition_on_previous_text"] is True
    assert "vad_parameters" not in kwargs


def test_faster_whisper_missing_wav(monkeypatch, tmp_path) -> None:
    _install_fake_faster_whisper(monkeypatch, [Segment("ok")])
    adapter = ASRAdapter(AppConfig(asr_backend="faster_whisper"))
    missing = tmp_path / "missing.wav"
    try:
        adapter.transcribe(missing)
    except FileNotFoundError as exc:
        assert "faster_whisper" in str(exc)
        assert str(missing) in str(exc)
        return
    raise AssertionError("missing wav should raise")


def test_faster_whisper_empty_segments_returns_empty_text(monkeypatch, tmp_path) -> None:
    wav_path = tmp_path / "input.wav"
    wav_path.write_bytes(b"fake wav")
    _install_fake_faster_whisper(monkeypatch, [])
    result = ASRAdapter(AppConfig(asr_backend="faster_whisper")).transcribe(wav_path)
    assert result == ""


def test_asr_env_config_parsing(monkeypatch) -> None:
    import srtp_voice.config as config_module

    names = [
        "ASR_BACKEND",
        "ASR_MODEL",
        "ASR_DEVICE",
        "ASR_COMPUTE_TYPE",
        "ASR_LANGUAGE",
        "ASR_CPU_THREADS",
        "ASR_BEAM_SIZE",
        "ASR_VAD_FILTER",
        "ASR_MIN_SILENCE_MS",
        "ASR_CONDITION_ON_PREVIOUS_TEXT",
    ]
    old_load_dotenv = config_module.load_dotenv
    monkeypatch.setattr(config_module, "load_dotenv", None)
    for name in names:
        monkeypatch.delenv(name, raising=False)

    cfg = AppConfig.from_env()
    assert cfg.asr_backend == "mock"
    assert cfg.asr_model == "small"
    assert cfg.asr_language == "zh"

    monkeypatch.setenv("ASR_BACKEND", "faster_whisper")
    monkeypatch.setenv("ASR_MODEL", " ")
    monkeypatch.setenv("ASR_DEVICE", "cpu")
    monkeypatch.setenv("ASR_COMPUTE_TYPE", "int8")
    monkeypatch.setenv("ASR_LANGUAGE", " ")
    monkeypatch.setenv("ASR_CPU_THREADS", "0")
    monkeypatch.setenv("ASR_BEAM_SIZE", "-3")
    monkeypatch.setenv("ASR_VAD_FILTER", "false")
    monkeypatch.setenv("ASR_MIN_SILENCE_MS", "0")
    monkeypatch.setenv("ASR_CONDITION_ON_PREVIOUS_TEXT", "1")
    cfg = AppConfig.from_env()
    monkeypatch.setattr(config_module, "load_dotenv", old_load_dotenv)

    assert cfg.asr_backend == "faster_whisper"
    assert cfg.asr_model == "small"
    assert cfg.asr_device == "cpu"
    assert cfg.asr_compute_type == "int8"
    assert cfg.asr_language == "zh"
    assert cfg.asr_cpu_threads == 1
    assert cfg.asr_beam_size == 1
    assert cfg.asr_vad_filter is False
    assert cfg.asr_min_silence_ms == 1
    assert cfg.asr_condition_on_previous_text is True


def test_requirements_asr_file() -> None:
    text = Path("requirements-asr.txt").read_text(encoding="utf-8").lower()
    assert "-r requirements.txt" in text
    assert "faster-whisper" in text
    assert "sherpa" not in text
    assert "funasr" not in text
    assert "torch" not in text


def test_env_example_asr_and_bom() -> None:
    data = Path(".env.example").read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8")
    assert "ASR_BACKEND=mock" in text
    assert "# ASR_BACKEND=faster_whisper" in text
    assert "ASR_MODEL=small" in text
    assert "ASR_DEVICE=cpu" in text
    assert "ASR_COMPUTE_TYPE=int8" in text
    assert "ASR_LANGUAGE=zh" in text
    assert "ASR_CPU_THREADS=4" in text
    assert "ASR_BEAM_SIZE=1" in text
    assert "ASR_VAD_FILTER=1" in text
    assert "ASR_MIN_SILENCE_MS=500" in text
    assert "ASR_CONDITION_ON_PREVIOUS_TEXT=0" in text
