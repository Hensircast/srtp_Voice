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
from srtp_voice.types import EmotionResult, StrategyResult


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


def _install_failing_sounddevice(monkeypatch):
    class FailingRawInputStream:
        def __init__(self, *args, **kwargs):
            raise AssertionError("RawInputStream should not be opened")

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(RawInputStream=FailingRawInputStream))


def _assert_invalid_vad_timing(monkeypatch, cfg: AppConfig, tmp_path: Path) -> None:
    _install_failing_sounddevice(monkeypatch)
    try:
        record_until_silence(tmp_path / "input.wav", cfg)
    except ValueError as exc:
        message = str(exc)
        assert "MAX_RECORD_SECONDS" in message
        assert "VAD_CALIBRATION_MS" in message
        assert "MIN_SPEECH_MS" in message
        assert "Increase MAX_RECORD_SECONDS" in message
        return
    raise AssertionError("invalid VAD timing should raise before opening RawInputStream")


def _patch_main_success(monkeypatch, tmp_path, args, cfg, asr_cls):
    import main as main_module

    calls = {"asr_init": 0, "asr_transcribe": 0}

    class WrappedASR(asr_cls):
        def __init__(self, cfg):
            calls["asr_init"] += 1
            super().__init__(cfg)

        def transcribe(self, path):
            calls["asr_transcribe"] += 1
            return super().transcribe(path)

    class FakeSER:
        def predict(self, path):
            return EmotionResult(label="neutral", intensity=0.35, confidence=0.5, features={})

    class FakeSmoother:
        def __init__(self, path, alpha, **kwargs):
            pass

        def update(self, emotion):
            return type("Smoothed", (), {
                "label": "neutral",
                "valence": 0.0,
                "arousal": 0.0,
                "dominance": 0.0,
                "to_dict": lambda self: {"label": "neutral"},
            })()

        def decay(self):
            return type("Smoothed", (), {
                "label": "neutral",
                "valence": 0.0,
                "arousal": 0.0,
                "dominance": 0.0,
                "to_dict": lambda self: {"label": "neutral"},
            })()

    class FakeMemory:
        def __init__(self, path, max_turns):
            pass

        def load(self):
            return []

        def append(self, state):
            return None

    class FakeGenerator:
        def __init__(self, cfg):
            pass

        def generate(self, user_text, emotion, history):
            return StrategyResult(reply_text="ok", action={})

    class FakeTTS:
        def __init__(self, cfg):
            pass

        def synthesize(self, text, path):
            Path(path).write_bytes(b"fake wav")

    def fake_record(path, *args, **kwargs):
        Path(path).write_bytes(b"fake wav")

    monkeypatch.setattr(main_module, "parse_args", lambda: args)
    monkeypatch.setattr(main_module.AppConfig, "from_env", classmethod(lambda cls: cfg))
    monkeypatch.setattr(main_module, "ASRAdapter", WrappedASR)
    monkeypatch.setattr(main_module, "SpeechEmotionRecognizer", lambda cfg: FakeSER())
    monkeypatch.setattr(main_module, "EmotionStateSmoother", FakeSmoother)
    monkeypatch.setattr(main_module, "JsonMemory", FakeMemory)
    monkeypatch.setattr(main_module, "StrategyGenerator", FakeGenerator)
    monkeypatch.setattr(main_module, "TTSAdapter", FakeTTS)
    monkeypatch.setattr(main_module, "build_energy_lip_sync", lambda path: {"frames": []})
    monkeypatch.setattr(main_module, "build_serial_packet", lambda action, lip_sync: {"ok": True})
    monkeypatch.setattr(main_module, "save_serial_packet", lambda packet, path: None)
    monkeypatch.setattr(main_module, "record_from_mic", fake_record)
    monkeypatch.setattr(main_module, "record_until_silence", fake_record)
    return main_module, calls


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


def test_vad_invalid_when_max_shorter_than_calibration(monkeypatch, tmp_path) -> None:
    cfg = _vad_cfg(max_record_seconds=0.05, vad_calibration_ms=80, min_speech_ms=30)
    _assert_invalid_vad_timing(monkeypatch, cfg, tmp_path)


def test_vad_invalid_when_max_equals_calibration(monkeypatch, tmp_path) -> None:
    cfg = _vad_cfg(max_record_seconds=0.08, vad_calibration_ms=80, min_speech_ms=30)
    _assert_invalid_vad_timing(monkeypatch, cfg, tmp_path)


def test_vad_invalid_when_remaining_less_than_min_speech(monkeypatch, tmp_path) -> None:
    cfg = _vad_cfg(max_record_seconds=0.10, vad_calibration_ms=80, min_speech_ms=30)
    _assert_invalid_vad_timing(monkeypatch, cfg, tmp_path)


def test_vad_valid_when_remaining_equals_min_speech(monkeypatch, tmp_path) -> None:
    frames = [
        _frame(20),
        _frame(20),
        _frame(260),
        _frame(260),
        _frame(260),
    ]
    _install_fake_sounddevice(monkeypatch, frames)
    path = tmp_path / "input.wav"

    record_until_silence(
        path,
        _vad_cfg(max_record_seconds=0.05, vad_calibration_ms=20, min_speech_ms=30),
    )

    assert path.exists()


def test_vad_default_timing_is_valid(monkeypatch, tmp_path) -> None:
    frames = [_frame(20, samples=512) for _ in range(25)]
    frames.extend([_frame(260, samples=512) for _ in range(5)])
    frames.extend([_frame(0, samples=512) for _ in range(32)])
    _install_fake_sounddevice(monkeypatch, frames)
    path = tmp_path / "input.wav"

    record_until_silence(path, AppConfig())

    assert path.exists()


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

    cfg = AppConfig(
        output_dir=tmp_path,
        memory_file=tmp_path / "memory.json",
        state_file=tmp_path / "emotion_state.json",
    )
    args = argparse.Namespace(
        mode="vad",
        audio="",
        text="",
        record_seconds=5.0,
        no_play=False,
    )
    inits = {"asr": 0, "llm": 0, "tts": 0}
    calls = {
        "ser": 0,
        "asr": 0,
        "llm": 0,
        "tts": 0,
        "play": 0,
        "decay": 0,
    }

    class FailSER:
        def __init__(self, cfg):
            pass

        def predict(self, path):
            calls["ser"] += 1
            raise AssertionError("SER predict should not be called")

    class FailASR:
        def __init__(self, cfg):
            inits["asr"] += 1

        def transcribe(self, path):
            calls["asr"] += 1
            raise AssertionError("ASR transcribe should not be called")

    class FailLLM:
        def __init__(self, cfg):
            inits["llm"] += 1

        def generate(self, **kwargs):
            calls["llm"] += 1
            raise AssertionError("LLM generate should not be called")

    class FailTTS:
        def __init__(self, cfg):
            inits["tts"] += 1

        def synthesize(self, text, path):
            calls["tts"] += 1
            raise AssertionError("TTS synthesize should not be called")

    def raise_no_speech(path, cfg):
        raise NoSpeechDetectedError("no speech")

    def fail_play(path):
        calls["play"] += 1
        raise AssertionError("audio playback should not be called")

    real_smoother = main_module.EmotionStateSmoother

    class TrackingSmoother(real_smoother):
        def decay(self, now=None):
            calls["decay"] += 1
            return super().decay(now=now)

    monkeypatch.setattr(main_module, "parse_args", lambda: args)
    monkeypatch.setattr(main_module.AppConfig, "from_env", classmethod(lambda cls: cfg))
    monkeypatch.setattr(main_module, "record_until_silence", raise_no_speech)
    monkeypatch.setattr(main_module, "play_wav", fail_play)
    monkeypatch.setattr(main_module, "SpeechEmotionRecognizer", FailSER)
    monkeypatch.setattr(main_module, "EmotionStateSmoother", TrackingSmoother)
    monkeypatch.setattr(main_module, "ASRAdapter", FailASR)
    monkeypatch.setattr(main_module, "StrategyGenerator", FailLLM)
    monkeypatch.setattr(main_module, "TTSAdapter", FailTTS)

    main_module.main()

    state = json.loads((tmp_path / "last_state.json").read_text(encoding="utf-8"))
    emotion_state = json.loads(
        (tmp_path / "emotion_state.json").read_text(encoding="utf-8")
    )
    assert state["stage"] == "Idle"
    assert inits == {"asr": 1, "llm": 1, "tts": 1}
    assert calls == {
        "ser": 0,
        "asr": 0,
        "llm": 0,
        "tts": 0,
        "play": 0,
        "decay": 1,
    }
    assert emotion_state["label"] == "neutral"
    assert emotion_state["updated_at"] is not None
    assert not (tmp_path / "memory.json").exists()
    assert not (tmp_path / "last_action.json").exists()
    assert not (tmp_path / "serial_packet.json").exists()
    assert not (tmp_path / "reply.wav").exists()


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
    inits = {"llm": 0, "tts": 0}
    calls = {"asr": 0, "llm": 0, "tts": 0}

    class FakeSER:
        def predict(self, path):
            return EmotionResult(label="neutral", intensity=0.35, confidence=0.5, features={})

    class FakeSmoother:
        def __init__(self, path, alpha, **kwargs):
            pass

        def update(self, emotion):
            return type("Smoothed", (), {
                "label": "neutral",
                "valence": 0.0,
                "arousal": 0.0,
                "dominance": 0.0,
                "to_dict": lambda self: {"label": "neutral"},
            })()

        def decay(self):
            raise AssertionError("file ASR-empty path should not decay VAD state")

    class EmptyASR:
        def __init__(self, cfg):
            pass

        def transcribe(self, path):
            calls["asr"] += 1
            return ""

    class FailLLM:
        def __init__(self, cfg):
            inits["llm"] += 1

        def generate(self, **kwargs):
            calls["llm"] += 1
            raise AssertionError("LLM generate should not be called")

    class FailTTS:
        def __init__(self, cfg):
            inits["tts"] += 1

        def synthesize(self, text, path):
            calls["tts"] += 1
            raise AssertionError("TTS synthesize should not be called")

    monkeypatch.setattr(main_module, "parse_args", lambda: args)
    monkeypatch.setattr(main_module.AppConfig, "from_env", classmethod(lambda cls: cfg))
    monkeypatch.setattr(main_module, "SpeechEmotionRecognizer", lambda cfg: FakeSER())
    monkeypatch.setattr(main_module, "EmotionStateSmoother", FakeSmoother)
    monkeypatch.setattr(main_module, "ASRAdapter", EmptyASR)
    monkeypatch.setattr(main_module, "StrategyGenerator", FailLLM)
    monkeypatch.setattr(main_module, "TTSAdapter", FailTTS)

    main_module.main()

    state = json.loads((tmp_path / "last_state.json").read_text(encoding="utf-8"))
    assert state["stage"] == "Idle"
    assert inits == {"llm": 1, "tts": 1}
    assert calls == {"asr": 1, "llm": 0, "tts": 0}


def test_main_console_does_not_create_asr_even_with_faster_whisper(monkeypatch, tmp_path) -> None:
    cfg = AppConfig(output_dir=tmp_path, state_file=tmp_path / "emotion_state.json", asr_backend="faster_whisper")
    args = argparse.Namespace(mode="console", audio="", text="", record_seconds=5.0, no_play=True)

    class FailASR:
        def __init__(self, cfg):
            raise AssertionError("console mode should not create ASRAdapter")

    main_module, calls = _patch_main_success(monkeypatch, tmp_path, args, cfg, FailASR)
    monkeypatch.setattr("builtins.input", lambda prompt: "console text")

    main_module.main()

    assert calls == {"asr_init": 0, "asr_transcribe": 0}


def test_main_text_override_does_not_create_asr_even_with_faster_whisper(monkeypatch, tmp_path) -> None:
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"fake wav")
    cfg = AppConfig(output_dir=tmp_path, state_file=tmp_path / "emotion_state.json", asr_backend="faster_whisper")
    args = argparse.Namespace(mode="file", audio=str(audio_path), text="manual text", record_seconds=5.0, no_play=True)

    class FailASR:
        def __init__(self, cfg):
            raise AssertionError("--text should not create ASRAdapter")

    main_module, calls = _patch_main_success(monkeypatch, tmp_path, args, cfg, FailASR)

    main_module.main()

    assert calls == {"asr_init": 0, "asr_transcribe": 0}


def test_main_file_mic_vad_create_asr_once_when_transcribing(monkeypatch, tmp_path) -> None:
    class CountASR:
        def __init__(self, cfg):
            pass

        def transcribe(self, path):
            return "recognized text"

    for mode in ["file", "mic", "vad"]:
        mode_tmp = tmp_path / mode
        mode_tmp.mkdir()
        audio_path = mode_tmp / "input.wav"
        audio_path.write_bytes(b"fake wav")
        cfg = AppConfig(output_dir=mode_tmp, state_file=mode_tmp / "emotion_state.json")
        args = argparse.Namespace(
            mode=mode,
            audio=str(audio_path) if mode == "file" else "",
            text="",
            record_seconds=5.0,
            no_play=True,
        )
        main_module, calls = _patch_main_success(monkeypatch, mode_tmp, args, cfg, CountASR)

        main_module.main()

        assert calls == {"asr_init": 1, "asr_transcribe": 1}
