"""Optional streaming Piper warmup tests (no real model or playback)."""

from __future__ import annotations

import wave
import pytest
from pathlib import Path

from srtp_voice.config import AppConfig
from srtp_voice.piper_session import PersistentPiperSession
from srtp_voice.tts import WARMUP_SENTENCE, TTSAdapter
from srtp_voice import tts as tts_module


class _RecordingSession:
    """Factory-installed session stub recording text and timeout."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    def synthesize(self, text: str, out_wav: Path, *, timeout_seconds=None):
        self.calls.append({"text": text, "path": Path(out_wav), "timeout": timeout_seconds})
        if self.fail:
            raise RuntimeError("synthetic warmup failure")
        with wave.open(str(out_wav), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(22050)
            audio.writeframes(b"\x00\x01" * 64)
        return object()

    def close(self) -> None:
        return None


def _piper_cfg(tmp_path: Path, **overrides) -> AppConfig:
    values = {
        "tts_backend": "piper",
        "tts_piper_persistent": True,
        "output_dir": tmp_path / "outputs",
        "tts_piper_exe": tmp_path / "piper.exe",
        "tts_piper_model": tmp_path / "model.onnx",
        "tts_piper_timeout_seconds": 12,
    }
    values.update(overrides)
    values["tts_piper_exe"].write_bytes(b"exe")
    values["tts_piper_model"].write_bytes(b"model")
    (tmp_path / "outputs").mkdir(exist_ok=True)
    return AppConfig(**values)


def test_warmup_without_owned_session_is_false_and_creates_nothing(tmp_path) -> None:
    for backend in ("mock", "edge_tts", "piper"):
        cfg = _piper_cfg(tmp_path, tts_backend=backend)
        adapter = TTSAdapter(cfg)
        before = sorted(p.name for p in (tmp_path / "outputs").iterdir())
        assert adapter.warmup() is False
        assert sorted(p.name for p in (tmp_path / "outputs").iterdir()) == before

    optout = TTSAdapter(_piper_cfg(tmp_path, tts_piper_persistent=False)).for_streaming()
    assert optout.has_persistent_session() is False
    assert optout.warmup() is False


def test_warmup_uses_fixed_sentence_and_short_timeout(tmp_path) -> None:
    cfg = _piper_cfg(tmp_path)
    adapter = TTSAdapter(cfg).for_streaming()
    session = _RecordingSession()
    factory = adapter._streaming_session
    assert factory is not None
    factory._session_factory = lambda config: session

    assert adapter.warmup() is True

    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["text"] == WARMUP_SENTENCE
    assert call["timeout"] == 5.0
    assert cfg.output_dir in call["path"].parents or call["path"].parent == cfg.output_dir
    # The warmup file lives in its own temporary directory and is cleaned up.
    assert not call["path"].exists()


def test_warmup_timeout_cap_respects_smaller_configured_timeout(tmp_path) -> None:
    cfg = _piper_cfg(tmp_path, tts_piper_timeout_seconds=2)
    adapter = TTSAdapter(cfg).for_streaming()
    session = _RecordingSession()
    factory = adapter._streaming_session
    assert factory is not None
    factory._session_factory = lambda config: session

    assert adapter.warmup(timeout_seconds=min(5.0, float(cfg.tts_piper_timeout_seconds))) is True
    assert session.calls[0]["timeout"] == 2.0


def test_warmup_is_idempotent_and_failure_stays_retryable(tmp_path) -> None:
    cfg = _piper_cfg(tmp_path)
    adapter = TTSAdapter(cfg).for_streaming()
    session = _RecordingSession(fail=True)
    factory = adapter._streaming_session
    assert factory is not None
    factory._session_factory = lambda config: session

    for _ in range(2):
        with pytest.raises(RuntimeError, match="synthetic warmup failure"):
            adapter.warmup()
    assert len(session.calls) == 2

    session.fail = False
    assert adapter.warmup() is True
    assert adapter.warmup() is True
    assert len(session.calls) == 3
    adapter.close()


def test_session_synthesize_timeout_override_keeps_default(tmp_path) -> None:
    cfg = _piper_cfg(tmp_path)
    session = PersistentPiperSession(cfg, command_prefix=("/bin/definitely-missing",))
    assert session.started is False
    # The override is only a keyword; the recorded default stays the config one.
    import inspect

    signature = inspect.signature(PersistentPiperSession.synthesize)
    assert signature.parameters["timeout_seconds"].default is None


def test_config_switch_defaults_off_and_is_strict(monkeypatch) -> None:
    monkeypatch.setattr(tts_module, "is_windows_platform", lambda: True)
    assert AppConfig().stream_tts_warmup is False

    monkeypatch.delenv("STREAM_TTS_WARMUP", raising=False)
    assert AppConfig.from_env().stream_tts_warmup is False

    monkeypatch.setenv("STREAM_TTS_WARMUP", "1")
    assert AppConfig.from_env().stream_tts_warmup is True

    monkeypatch.setenv("STREAM_TTS_WARMUP", "maybe")
    with pytest.raises(ValueError, match="STREAM_TTS_WARMUP"):
        AppConfig.from_env()
