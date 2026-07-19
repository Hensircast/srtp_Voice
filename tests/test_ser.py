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
    SERBackendError,
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
    assert not hasattr(cfg, "ser_timeout_seconds")


def test_app_config_parses_ser_environment(monkeypatch) -> None:
    import srtp_voice.config as config_module

    monkeypatch.setattr(config_module, "load_dotenv", None)
    values = {
        "SER_BACKEND": "sensevoice",
        "SER_MODEL": "models/ser/custom",
        "SER_DEVICE": "cuda:0",
        "SER_LANGUAGE": "en",
        "SER_FALLBACK_TO_HEURISTIC": "false",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    cfg = AppConfig.from_env()

    assert cfg.ser_backend == "sensevoice"
    assert cfg.ser_model == "models/ser/custom"
    assert cfg.ser_device == "cuda:0"
    assert cfg.ser_language == "en"
    assert cfg.ser_fallback_to_heuristic is False
    assert not hasattr(cfg, "ser_timeout_seconds")


def test_ser_timeout_environment_variable_is_not_supported(monkeypatch) -> None:
    import srtp_voice.config as config_module

    monkeypatch.setattr(config_module, "load_dotenv", None)
    monkeypatch.setenv("SER_TIMEOUT_SECONDS", "not-an-integer")

    cfg = AppConfig.from_env()

    assert not hasattr(cfg, "ser_timeout_seconds")


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
                "device": "cpu",
                "disable_update": True,
                "disable_pbar": True,
            }

        def generate(self, **kwargs):
            calls["generate"] += 1
            assert kwargs == {"input": str(wav_path), "language": "zh", "use_itn": True}
            return [{"text": "<|zh|><|HAPPY|><|Speech|>你好"}]

    _install_fake_funasr(monkeypatch, FakeAutoModel)
    recognizer = SpeechEmotionRecognizer(_sensevoice_cfg(model_path))
    assert calls == {"init": 0, "generate": 0}

    recognizer.warmup()
    recognizer.warmup()
    first = recognizer.predict(wav_path)
    second = recognizer.predict(wav_path)

    assert first.label == second.label == "happy"
    assert calls == {"init": 1, "generate": 2}
    assert recognizer.configured_backend_name == "sensevoice"
    assert recognizer.backend_name == "sensevoice"


def test_sensevoice_warmup_failure_switches_to_heuristic(monkeypatch, tmp_path) -> None:
    wav_path = _write_wav(tmp_path / "input.wav", [2000] * 160)
    recognizer = SpeechEmotionRecognizer(
        AppConfig(ser_backend="sensevoice", ser_fallback_to_heuristic=True)
    )
    calls = {"warmup": 0, "predict": 0}

    class FailingSenseVoiceBackend:
        def warmup(self):
            calls["warmup"] += 1
            cause = OSError("model weights are unavailable")
            raise SERBackendError("model loading", str(cause), cause) from cause

        def predict(self, path):
            calls["predict"] += 1
            raise AssertionError("failed SenseVoice backend must not be retried")

    recognizer.backend = FailingSenseVoiceBackend()

    with pytest.warns(
        RuntimeWarning,
        match="model loading.*OSError.*falling back to heuristic.*model weights are unavailable",
    ):
        recognizer.warmup()

    assert recognizer.configured_backend_name == "sensevoice"
    assert recognizer.backend_name == "heuristic"
    assert recognizer.backend is recognizer._fallback

    result = recognizer.predict(wav_path)
    recognizer.warmup()

    assert result.label == "neutral"
    assert calls == {"warmup": 1, "predict": 0}


def test_sensevoice_warmup_failure_without_fallback_raises_runtime_error() -> None:
    recognizer = SpeechEmotionRecognizer(
        AppConfig(ser_backend="sensevoice", ser_fallback_to_heuristic=False)
    )
    cause = OSError("model initialization failed")
    backend_error = SERBackendError("model loading", str(cause), cause)

    class FailingSenseVoiceBackend:
        def warmup(self):
            raise backend_error

    failing_backend = FailingSenseVoiceBackend()
    recognizer.backend = failing_backend

    with pytest.raises(
        RuntimeError,
        match="SenseVoice SER warmup failed during model loading.*OSError.*model initialization failed",
    ) as exc_info:
        recognizer.warmup()

    assert exc_info.value.__cause__ is backend_error
    assert recognizer.backend is failing_backend
    assert recognizer.backend_name == "sensevoice"


def test_default_heuristic_does_not_import_funasr(monkeypatch, tmp_path) -> None:
    monkeypatch.setitem(sys.modules, "funasr", None)
    wav_path = _write_wav(tmp_path / "input.wav", [2000] * 160)
    recognizer = SpeechEmotionRecognizer(AppConfig(ser_backend="heuristic"))

    recognizer.warmup()
    result = recognizer.predict(wav_path)

    assert result.label == "neutral"


def test_sensevoice_backend_warmup_calls_load_model(monkeypatch) -> None:
    backend = SenseVoiceSERBackend(AppConfig(ser_backend="sensevoice"))
    calls = []
    monkeypatch.setattr(backend, "_load_model", lambda: calls.append("load"))

    backend.warmup()

    assert calls == ["load"]


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


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("NEUTRAL", "neutral"),
        ("HAPPY", "happy"),
        ("FEARFUL", "fear"),
        ("SURPRISED", "surprise"),
    ],
)
def test_sensevoice_real_generate_text_format(monkeypatch, tmp_path, token, expected) -> None:
    model_path = tmp_path / "SenseVoiceSmall"
    model_path.mkdir()
    wav_path = _write_wav(tmp_path / "input.wav", [1000] * 160)

    class RealShapeModel:
        def __init__(self, **kwargs):
            assert kwargs["disable_update"] is True
            assert kwargs["disable_pbar"] is True
            assert "trust_remote_code" not in kwargs
            assert "remote_code" not in kwargs

        def generate(self, **kwargs):
            return [
                {
                    "key": "v1_4_integration_verify",
                    "text": (
                        f"<|zh|><|{token}|><|Speech|><|withitn|>"
                        "你好，这是语音交互系统综合测试。"
                    ),
                }
            ]

    _install_fake_funasr(monkeypatch, RealShapeModel)
    result = SpeechEmotionRecognizer(_sensevoice_cfg(model_path)).predict(wav_path)

    assert result.label == expected
    assert 0.0 <= result.intensity <= 1.0
    assert 0.0 <= result.confidence <= 1.0


def test_sensevoice_ignores_non_emotion_tokens(monkeypatch, tmp_path) -> None:
    model_path = tmp_path / "SenseVoiceSmall"
    model_path.mkdir()
    wav_path = _write_wav(tmp_path / "input.wav", [1000] * 160)

    class NoEmotionModel:
        def __init__(self, **kwargs):
            pass

        def generate(self, **kwargs):
            return [
                {
                    "text": (
                        "<|zh|><|en|><|Speech|><|withitn|><|woitn|>"
                        "测试文本"
                    )
                }
            ]

    _install_fake_funasr(monkeypatch, NoEmotionModel)
    result = SpeechEmotionRecognizer(_sensevoice_cfg(model_path)).predict(wav_path)

    assert result.label == "unknown"
    assert result.intensity == 0.5
    assert result.confidence == 0.5


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
            return []

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


@pytest.mark.parametrize("backend", ["custom", "unknown-backend", ""])
def test_unknown_ser_backend_is_explicit(backend) -> None:
    with pytest.raises(ValueError, match="unsupported SER_BACKEND") as exc_info:
        SpeechEmotionRecognizer(AppConfig(ser_backend=backend))

    message = str(exc_info.value)
    assert "heuristic" in message
    assert "sensevoice" in message


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

    source = inspect.getsource(main.main)
    assert source.count("SpeechEmotionRecognizer(cfg)") == 1


def test_main_warms_and_reuses_one_sensevoice_recognizer(monkeypatch, tmp_path, capsys) -> None:
    import argparse
    import main as main_module

    cfg = AppConfig(
        output_dir=tmp_path,
        memory_file=tmp_path / "memory.json",
        state_file=tmp_path / "emotion.json",
        ser_backend="sensevoice",
        llm_backend="mock",
        tts_backend="mock",
    )
    args = argparse.Namespace(
        mode="mic",
        audio="",
        text="manual text",
        record_seconds=0.1,
        no_play=True,
    )
    events = []
    instances = []

    class FakeRecognizer:
        backend_name = "sensevoice"

        def __init__(self, received_cfg):
            assert received_cfg is cfg
            instances.append(self)
            events.append("init")

        def warmup(self):
            events.append("warmup")

        def predict(self, wav_path):
            assert instances == [self]
            events.append("predict")
            return EmotionResult(
                label="neutral", intensity=0.5, confidence=0.5, features={}
            )

    def fake_record(path, **kwargs):
        events.append("record")
        Path(path).write_bytes(b"test audio placeholder")

    monkeypatch.setattr(main_module, "parse_args", lambda: args)
    monkeypatch.setattr(main_module.AppConfig, "from_env", classmethod(lambda cls: cfg))
    monkeypatch.setattr(main_module, "SpeechEmotionRecognizer", FakeRecognizer)
    monkeypatch.setattr(main_module, "record_from_mic", fake_record)

    main_module.main()

    assert len(instances) == 1
    assert events[:4] == ["init", "warmup", "record", "predict"]
    output = capsys.readouterr().out
    assert "[INIT] 正在预加载 SenseVoice SER 模型" in output
    assert "[INIT] SenseVoice SER 模型加载完成" in output


def test_main_reports_heuristic_fallback_after_failed_warmup(
    monkeypatch, tmp_path, capsys
) -> None:
    import argparse
    import main as main_module

    cfg = AppConfig(
        output_dir=tmp_path,
        memory_file=tmp_path / "memory.json",
        state_file=tmp_path / "emotion.json",
        ser_backend="sensevoice",
        llm_backend="mock",
        tts_backend="mock",
    )
    args = argparse.Namespace(
        mode="mic",
        audio="",
        text="manual text",
        record_seconds=0.1,
        no_play=True,
    )

    class FallbackRecognizer:
        def __init__(self, received_cfg):
            assert received_cfg is cfg
            self.backend_name = "sensevoice"

        def warmup(self):
            self.backend_name = "heuristic"

        def predict(self, wav_path):
            return EmotionResult(
                label="neutral", intensity=0.5, confidence=0.5, features={}
            )

    def fake_record(path, **kwargs):
        Path(path).write_bytes(b"test audio placeholder")

    monkeypatch.setattr(main_module, "parse_args", lambda: args)
    monkeypatch.setattr(main_module.AppConfig, "from_env", classmethod(lambda cls: cfg))
    monkeypatch.setattr(main_module, "SpeechEmotionRecognizer", FallbackRecognizer)
    monkeypatch.setattr(main_module, "record_from_mic", fake_record)

    main_module.main()

    output = capsys.readouterr().out
    assert "[INIT] SenseVoice SER 预加载失败，已启用 heuristic fallback" in output
    assert "[INIT] SenseVoice SER 模型加载完成" not in output


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
    assert "SER_TIMEOUT_SECONDS" not in text


def test_requirements_ser_keeps_model_dependencies_optional() -> None:
    path = Path(__file__).resolve().parents[1] / "requirements-ser.txt"
    requirements = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert requirements == ["-r requirements.txt", "funasr", "torchaudio"]
