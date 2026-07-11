from __future__ import annotations

import inspect
import struct
import sys
import types
import wave
from pathlib import Path

import pytest

from srtp_voice.config import AppConfig
from srtp_voice.ser import (
    HeuristicSERBackend,
    SER_LABELS,
    SenseVoiceSERBackend,
    SpeechEmotionRecognizer,
    normalize_emotion_label,
)
from srtp_voice.types import EmotionResult


def _write_wav(path: Path, samples: list[int], sample_rate: int = 16000) -> Path:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        if samples:
            wav_file.writeframes(struct.pack("<" + "h" * len(samples), *samples))
    return path


def _install_fake_funasr(monkeypatch, model_class) -> None:
    module = types.ModuleType("funasr")
    module.AutoModel = model_class
    monkeypatch.setitem(sys.modules, "funasr", module)


def _sensevoice_cfg(model_path: Path, **overrides) -> AppConfig:
    values = {
        "ser_backend": "sensevoice",
        "ser_model": str(model_path),
        "ser_device": "cpu",
        "ser_language": "zh",
        "ser_fallback_to_heuristic": False,
    }
    values.update(overrides)
    return AppConfig(**values)


def test_app_config_default_ser_values() -> None:
    cfg = AppConfig()
    assert cfg.ser_backend == "heuristic"
    assert cfg.ser_model is None
    assert cfg.ser_device == "cpu"
    assert cfg.ser_language == "zh"
    assert cfg.ser_fallback_to_heuristic is True
    assert cfg.ser_timeout_seconds == 30


def test_app_config_parses_ser_environment(monkeypatch) -> None:
    import srtp_voice.config as config_module

    monkeypatch.setattr(config_module, "load_dotenv", None)
    values = {
        "SER_BACKEND": "sensevoice",
        "SER_MODEL": "models/ser/custom",
        "SER_DEVICE": "cuda:0",
        "SER_LANGUAGE": "en",
        "SER_FALLBACK_TO_HEURISTIC": "false",
        "SER_TIMEOUT_SECONDS": "0",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    cfg = AppConfig.from_env()

    assert cfg.ser_backend == "sensevoice"
    assert cfg.ser_model == "models/ser/custom"
    assert cfg.ser_device == "cuda:0"
    assert cfg.ser_language == "en"
    assert cfg.ser_fallback_to_heuristic is False
    assert cfg.ser_timeout_seconds == 1


def test_heuristic_empty_and_normal_audio(tmp_path) -> None:
    backend = HeuristicSERBackend()
    empty = backend.predict(_write_wav(tmp_path / "empty.wav", []))
    normal = backend.predict(_write_wav(tmp_path / "normal.wav", [2000] * 320))

    assert empty.label == "neutral"
    assert empty.features == {"rms": 0.0, "zcr": 0.0, "duration": 0.0}
    assert normal.label == "neutral"
    assert normal.features["rms"] > 0


def test_heuristic_uses_single_normalized_labels(tmp_path) -> None:
    backend = HeuristicSERBackend()
    excited = backend.predict(
        _write_wav(tmp_path / "excited.wav", [12000, -12000] * 160)
    )
    tired = backend.predict(_write_wav(tmp_path / "tired.wav", [100] * 320))

    assert excited.label == "excited"
    assert tired.label == "tired"
    assert excited.label in SER_LABELS
    assert tired.label in SER_LABELS


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("高兴", "happy"),
        ("开心", "happy"),
        ("happy", "happy"),
        ("悲伤", "sad"),
        ("难过", "sad"),
        ("生气", "angry"),
        ("愤怒", "angry"),
        ("中性", "neutral"),
        ("害怕", "fear"),
        ("惊讶", "surprise"),
        ("厌恶", "disgust"),
        ("疲惫", "tired"),
        ("兴奋", "excited"),
        ("<|FEARFUL|>", "fear"),
        ("<|DISGUSTED|>", "disgust"),
        ("<|SURPRISED|>", "surprise"),
        ("not-a-label", "unknown"),
    ],
)
def test_normalize_emotion_label(raw, expected) -> None:
    assert normalize_emotion_label(raw) == expected


def test_sensevoice_is_lazy_and_loads_model_once(monkeypatch, tmp_path) -> None:
    model_path = tmp_path / "SenseVoiceSmall"
    model_path.mkdir()
    wav_path = _write_wav(tmp_path / "input.wav", [1000] * 160)
    calls = {"init": 0, "generate": 0}

    class FakeAutoModel:
        def __init__(self, **kwargs):
            calls["init"] += 1
            assert kwargs == {
                "model": str(model_path),
                "trust_remote_code": True,
                "device": "cpu",
            }

        def generate(self, **kwargs):
            calls["generate"] += 1
            assert kwargs == {"input": str(wav_path), "language": "zh", "use_itn": True}
            return [{"text": "<|zh|><|HAPPY|><|Speech|>你好"}]

    _install_fake_funasr(monkeypatch, FakeAutoModel)
    recognizer = SpeechEmotionRecognizer(_sensevoice_cfg(model_path))
    assert calls == {"init": 0, "generate": 0}

    first = recognizer.predict(wav_path)
    second = recognizer.predict(wav_path)

    assert first.label == second.label == "happy"
    assert calls == {"init": 1, "generate": 2}


def test_default_heuristic_does_not_import_funasr(monkeypatch, tmp_path) -> None:
    monkeypatch.setitem(sys.modules, "funasr", None)
    wav_path = _write_wav(tmp_path / "input.wav", [2000] * 160)

    result = SpeechEmotionRecognizer(AppConfig(ser_backend="heuristic")).predict(wav_path)

    assert result.label == "neutral"


def test_sensevoice_constructor_does_not_import_funasr(monkeypatch, tmp_path) -> None:
    monkeypatch.setitem(sys.modules, "funasr", None)
    model_path = tmp_path / "SenseVoiceSmall"
    model_path.mkdir()

    SpeechEmotionRecognizer(_sensevoice_cfg(model_path))


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ([{"text": "<|zh|><|NEUTRAL|><|Speech|>测试"}], "neutral"),
        ({"emotion": "SAD"}, "sad"),
        ({"result": [{"emotion_label": "愤怒"}]}, "angry"),
        ([{"text": "<|zh|><|DISGUSTED|><|Speech|>测试"}], "disgust"),
    ],
)
def test_sensevoice_common_output_shapes(monkeypatch, tmp_path, result, expected) -> None:
    model_path = tmp_path / "SenseVoiceSmall"
    model_path.mkdir()
    wav_path = _write_wav(tmp_path / "input.wav", [1000] * 160)

    class FakeAutoModel:
        def __init__(self, **kwargs):
            pass

        def generate(self, **kwargs):
            return result

    _install_fake_funasr(monkeypatch, FakeAutoModel)
    emotion = SpeechEmotionRecognizer(_sensevoice_cfg(model_path)).predict(wav_path)
    assert emotion.label == expected
    assert emotion.intensity == 0.5
    assert emotion.confidence == 0.5
    assert emotion.features == {}


def test_sensevoice_missing_dependency_is_clear(monkeypatch, tmp_path) -> None:
    model_path = tmp_path / "SenseVoiceSmall"
    model_path.mkdir()
    wav_path = _write_wav(tmp_path / "input.wav", [1000] * 160)
    monkeypatch.setitem(sys.modules, "funasr", None)

    with pytest.raises(RuntimeError, match="dependency import.*ModuleNotFoundError") as exc_info:
        SpeechEmotionRecognizer(_sensevoice_cfg(model_path)).predict(wav_path)
    assert "requirements-ser.txt" in str(exc_info.value)


def test_sensevoice_model_load_failure_falls_back(monkeypatch, tmp_path) -> None:
    model_path = tmp_path / "SenseVoiceSmall"
    model_path.mkdir()
    wav_path = _write_wav(tmp_path / "input.wav", [2000] * 160)

    class FailingAutoModel:
        def __init__(self, **kwargs):
            raise OSError("bad model")

    _install_fake_funasr(monkeypatch, FailingAutoModel)
    cfg = _sensevoice_cfg(model_path, ser_fallback_to_heuristic=True)

    with pytest.warns(RuntimeWarning, match="model loading.*OSError"):
        result = SpeechEmotionRecognizer(cfg).predict(wav_path)
    assert result.label == "neutral"


def test_sensevoice_inference_failure_falls_back(monkeypatch, tmp_path) -> None:
    model_path = tmp_path / "SenseVoiceSmall"
    model_path.mkdir()
    wav_path = _write_wav(tmp_path / "input.wav", [2000] * 160)

    class FailingModel:
        def __init__(self, **kwargs):
            pass

        def generate(self, **kwargs):
            raise TimeoutError("inference timed out")

    _install_fake_funasr(monkeypatch, FailingModel)
    cfg = _sensevoice_cfg(model_path, ser_fallback_to_heuristic=True)

    with pytest.warns(RuntimeWarning, match="inference.*TimeoutError"):
        result = SpeechEmotionRecognizer(cfg).predict(wav_path)
    assert result.label == "neutral"


def test_sensevoice_parse_failure_falls_back(monkeypatch, tmp_path) -> None:
    model_path = tmp_path / "SenseVoiceSmall"
    model_path.mkdir()
    wav_path = _write_wav(tmp_path / "input.wav", [2000] * 160)

    class InvalidOutputModel:
        def __init__(self, **kwargs):
            pass

        def generate(self, **kwargs):
            return [{"text": "transcript without emotion tag"}]

    _install_fake_funasr(monkeypatch, InvalidOutputModel)
    cfg = _sensevoice_cfg(model_path, ser_fallback_to_heuristic=True)

    with pytest.warns(RuntimeWarning, match="output parsing.*ValueError"):
        result = SpeechEmotionRecognizer(cfg).predict(wav_path)
    assert result.label == "neutral"


def test_sensevoice_failure_without_fallback_preserves_context(monkeypatch, tmp_path) -> None:
    wav_path = _write_wav(tmp_path / "input.wav", [2000] * 160)
    cfg = _sensevoice_cfg(tmp_path / "missing-model", ser_fallback_to_heuristic=False)

    with pytest.raises(RuntimeError, match="model loading.*FileNotFoundError") as exc_info:
        SpeechEmotionRecognizer(cfg).predict(wav_path)
    assert str(wav_path) in str(exc_info.value)


@pytest.mark.parametrize("backend", ["unknown-backend", ""])
def test_unknown_ser_backend_is_explicit(backend) -> None:
    with pytest.raises(ValueError, match="unsupported SER_BACKEND"):
        SpeechEmotionRecognizer(AppConfig(ser_backend=backend))


def test_custom_ser_backend_is_explicitly_unimplemented() -> None:
    with pytest.raises(NotImplementedError, match="SER_BACKEND=custom"):
        SpeechEmotionRecognizer(AppConfig(ser_backend="custom"))


def test_result_bounds_and_label_are_normalized(tmp_path) -> None:
    recognizer = SpeechEmotionRecognizer(AppConfig(ser_backend="heuristic"))

    class OutOfRangeBackend:
        def predict(self, wav_path):
            return EmotionResult(
                label="<|ANGRY|>", intensity=2.0, confidence=-1.0, features={}
            )

    recognizer.backend = OutOfRangeBackend()
    result = recognizer.predict(tmp_path / "unused.wav")
    assert result.label == "angry"
    assert result.intensity == 1.0
    assert result.confidence == 0.0


def test_main_constructs_ser_with_config() -> None:
    import main

    assert "SpeechEmotionRecognizer(cfg)" in inspect.getsource(main.main)


def test_env_example_contains_safe_ser_defaults() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env.example"
    raw = env_path.read_bytes()
    text = raw.decode("utf-8")
    active_lines = {
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    assert not raw.startswith(b"\xef\xbb\xbf")
    assert "SER_BACKEND=heuristic" in active_lines
    assert "SER_FALLBACK_TO_HEURISTIC=1" in active_lines
    assert "SER_MODEL=models/ser/SenseVoiceSmall" not in active_lines


def test_requirements_ser_keeps_model_dependencies_optional() -> None:
    path = Path(__file__).resolve().parents[1] / "requirements-ser.txt"
    requirements = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert requirements == ["-r requirements.txt", "funasr"]
