from __future__ import annotations

import argparse
import json
import struct
import sys
import types
import wave
from pathlib import Path

from srtp_voice.audio_io import NoSpeechDetectedError, record_until_silence
from srtp_voice.config import AppConfig


def _frame(amplitude: int, samples: int = 10) -> bytes:
    return struct.pack("<" + "h" * samples, *([amplitude] * samples))


class FakeRawInputStream:
    frames = []
    reads = 0
    params = {}

    def __init__(self, samplerate, channels, dtype, blocksize):
        type(self).params = {
            "samplerate": samplerate,
            "channels": channels,
            "dtype": dtype,
            "blocksize": blocksize,
        }
        type(self).reads = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, frame_samples):
        cls = type(self)
        index = cls.reads
        cls.reads += 1
        if index < len(cls.frames):
            return cls.frames[index], False
        return _frame(0, frame_samples), False


def _install_fake_sounddevice(monkeypatch, frames):
    FakeRawInputStream.frames = frames
    fake_sd = types.SimpleNamespace(RawInputStream=FakeRawInputStream)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)


def _vad_cfg(**overrides) -> AppConfig:
    values = {
        "sample_rate": 1000,
        "frame_ms": 10,
        "vad_threshold": 0.004,
        "min_speech_ms": 30,
        "silence_ms": 30,
        "max_record_seconds": 0.2,
        "pre_roll_ms": 20,
        "vad_calibration_ms": 20,
        "vad_noise_multiplier": 3.0,
        "vad_release_ratio": 0.6,
        "vad_debug": False,
    }
    values.update(overrides)
    return AppConfig(**values)


def _wav_frame_count(path: Path) -> int:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes()


def test_vad_calibration_dynamic_threshold_and_low_voice_trigger(monkeypatch, tmp_path, capsys) -> None:
    frames = [
        _frame(20),
        _frame(30),
        _frame(260),
        _frame(260),
        _frame(260),
        _frame(0),
        _frame(0),
        _frame(0),
    ]
    _install_fake_sounddevice(monkeypatch, frames)
    path = tmp_path / "input.wav"

    record_until_silence(path, _vad_cfg())

    out = capsys.readouterr().out
    assert "noise_floor=" in out
    assert "configured_threshold=0.00400" in out
    assert "effective_start_threshold=0.00400" in out
    assert "effective_release_threshold=0.00240" in out
    assert path.exists()


def test_single_low_frame_decays_instead_of_resetting(monkeypatch, tmp_path) -> None:
    frames = [
        _frame(20),
        _frame(20),
        _frame(260),
        _frame(260),
        _frame(0),
        _frame(260),
        _frame(260),
        _frame(0),
        _frame(0),
        _frame(0),
    ]
    _install_fake_sounddevice(monkeypatch, frames)
    path = tmp_path / "input.wav"

    record_until_silence(path, _vad_cfg())

    assert path.exists()


def test_vad_release_threshold_and_silence_end(monkeypatch, tmp_path) -> None:
    frames = [
        _frame(20),
        _frame(20),
        _frame(260),
        _frame(260),
        _frame(260),
        _frame(100),
        _frame(100),
        _frame(0),
        _frame(0),
        _frame(0),
    ]
    _install_fake_sounddevice(monkeypatch, frames)
    path = tmp_path / "input.wav"

    record_until_silence(path, _vad_cfg())

    assert path.exists()
    assert _wav_frame_count(path) >= 6 * 10


def test_vad_pre_roll_is_preserved(monkeypatch, tmp_path) -> None:
    frames = [
        _frame(20),
        _frame(20),
        _frame(260),
        _frame(270),
        _frame(280),
        _frame(0),
        _frame(0),
        _frame(0),
    ]
    _install_fake_sounddevice(monkeypatch, frames)
    path = tmp_path / "input.wav"

    record_until_silence(path, _vad_cfg())

    assert _wav_frame_count(path) == 5 * 10


def test_vad_no_speech_raises_and_does_not_create_dummy(monkeypatch, tmp_path) -> None:
    frames = [_frame(20) for _ in range(20)]
    _install_fake_sounddevice(monkeypatch, frames)
    path = tmp_path / "input.wav"

    try:
        record_until_silence(path, _vad_cfg())
    except NoSpeechDetectedError:
        assert not path.exists()
        return
    raise AssertionError("no speech should raise")


def test_vad_debug_default_and_env_parsing(monkeypatch) -> None:
    import srtp_voice.config as config_module

    names = [
        "VAD_THRESHOLD",
        "MIN_SPEECH_MS",
        "SILENCE_MS",
        "MAX_RECORD_SECONDS",
        "PRE_ROLL_MS",
        "VAD_CALIBRATION_MS",
        "VAD_NOISE_MULTIPLIER",
        "VAD_RELEASE_RATIO",
        "VAD_DEBUG",
    ]
    monkeypatch.setattr(config_module, "load_dotenv", None)
    for name in names:
        monkeypatch.delenv(name, raising=False)

    cfg = AppConfig.from_env()
    assert cfg.vad_threshold == 0.004
    assert cfg.min_speech_ms == 160
    assert cfg.silence_ms == 1000
    assert cfg.max_record_seconds == 15
    assert cfg.pre_roll_ms == 400
    assert cfg.vad_calibration_ms == 800
    assert cfg.vad_noise_multiplier == 3.0
    assert cfg.vad_release_ratio == 0.60
    assert cfg.vad_debug is False

    monkeypatch.setenv("VAD_THRESHOLD", "-1")
    monkeypatch.setenv("MIN_SPEECH_MS", "0")
    monkeypatch.setenv("SILENCE_MS", "0")
    monkeypatch.setenv("MAX_RECORD_SECONDS", "0")
    monkeypatch.setenv("PRE_ROLL_MS", "-1")
    monkeypatch.setenv("VAD_CALIBRATION_MS", "-1")
    monkeypatch.setenv("VAD_NOISE_MULTIPLIER", "0.5")
    monkeypatch.setenv("VAD_RELEASE_RATIO", "2")
    monkeypatch.setenv("VAD_DEBUG", "1")
    cfg = AppConfig.from_env()
    assert cfg.vad_threshold > 0
    assert cfg.min_speech_ms == 1
    assert cfg.silence_ms == 1
    assert cfg.max_record_seconds > 0
    assert cfg.pre_roll_ms == 0
    assert cfg.vad_calibration_ms == 0
    assert cfg.vad_noise_multiplier == 1.0
    assert cfg.vad_release_ratio == 1.0
    assert cfg.vad_debug is True


def test_main_vad_no_speech_returns_idle_without_asr_llm_tts(monkeypatch, tmp_path) -> None:
    import main as main_module

    cfg = AppConfig(output_dir=tmp_path, state_file=tmp_path / "emotion_state.json")
    args = argparse.Namespace(
        mode="vad",
        audio="",
        text="",
        record_seconds=5.0,
        no_play=True,
    )
    calls = {"asr": 0, "llm": 0, "tts": 0}

    class FailASR:
        def __init__(self, cfg):
            calls["asr"] += 1
            raise AssertionError("ASR should not be called")

    class FailLLM:
        def __init__(self, cfg):
            calls["llm"] += 1
            raise AssertionError("LLM should not be called")

    class FailTTS:
        def __init__(self, cfg):
            calls["tts"] += 1
            raise AssertionError("TTS should not be called")

    def raise_no_speech(path, cfg):
        raise NoSpeechDetectedError("no speech")

    monkeypatch.setattr(main_module, "parse_args", lambda: args)
    monkeypatch.setattr(main_module.AppConfig, "from_env", classmethod(lambda cls: cfg))
    monkeypatch.setattr(main_module, "record_until_silence", raise_no_speech)
    monkeypatch.setattr(main_module, "ASRAdapter", FailASR)
    monkeypatch.setattr(main_module, "StrategyGenerator", FailLLM)
    monkeypatch.setattr(main_module, "TTSAdapter", FailTTS)

    main_module.main()

    state = json.loads((tmp_path / "last_state.json").read_text(encoding="utf-8"))
    assert state["stage"] == "Idle"
    assert calls == {"asr": 0, "llm": 0, "tts": 0}


def test_main_file_empty_asr_returns_idle_without_llm_tts(monkeypatch, tmp_path) -> None:
    import main as main_module
    from srtp_voice.types import EmotionResult

    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"fake wav")
    cfg = AppConfig(output_dir=tmp_path, state_file=tmp_path / "emotion_state.json")
    args = argparse.Namespace(
        mode="file",
        audio=str(audio_path),
        text="",
        record_seconds=5.0,
        no_play=True,
    )
    calls = {"asr": 0, "llm": 0, "tts": 0}

    class FakeSER:
        def predict(self, path):
            return EmotionResult(label="neutral", intensity=0.35, confidence=0.5, features={})

    class FakeSmoother:
        def __init__(self, path, alpha):
            pass

        def update(self, emotion):
            return type("Smoothed", (), {
                "label": "neutral",
                "valence": 0.0,
                "arousal": 0.0,
                "dominance": 0.0,
                "to_dict": lambda self: {"label": "neutral"},
            })()

    class EmptyASR:
        def __init__(self, cfg):
            pass

        def transcribe(self, path):
            calls["asr"] += 1
            return ""

    class FailLLM:
        def __init__(self, cfg):
            calls["llm"] += 1
            raise AssertionError("LLM should not be called")

    class FailTTS:
        def __init__(self, cfg):
            calls["tts"] += 1
            raise AssertionError("TTS should not be called")

    monkeypatch.setattr(main_module, "parse_args", lambda: args)
    monkeypatch.setattr(main_module.AppConfig, "from_env", classmethod(lambda cls: cfg))
    monkeypatch.setattr(main_module, "SpeechEmotionRecognizer", lambda: FakeSER())
    monkeypatch.setattr(main_module, "EmotionStateSmoother", FakeSmoother)
    monkeypatch.setattr(main_module, "ASRAdapter", EmptyASR)
    monkeypatch.setattr(main_module, "StrategyGenerator", FailLLM)
    monkeypatch.setattr(main_module, "TTSAdapter", FailTTS)

    main_module.main()

    state = json.loads((tmp_path / "last_state.json").read_text(encoding="utf-8"))
    assert state["stage"] == "Idle"
    assert calls == {"asr": 1, "llm": 0, "tts": 0}
