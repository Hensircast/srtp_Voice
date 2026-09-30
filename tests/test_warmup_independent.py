"""Independent optional warmup regressions; no models, mic, or playback."""

from dataclasses import replace
from pathlib import Path

import pytest

from srtp_voice.config import AppConfig
from srtp_voice.piper_session import PiperSessionFactory
from srtp_voice.streaming_runtime import StreamingResponseRuntime
from srtp_voice.tts import TTSAdapter
from tools.workbench_latency import _config_view, _fingerprint


@pytest.mark.parametrize("backend", ["mock", "edge_tts", "piper"])
def test_nonpersistent_adapter_warmup_is_side_effect_free(tmp_path, backend):
    adapter = TTSAdapter(AppConfig(tts_backend=backend, output_dir=tmp_path))
    assert adapter.warmup() is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("configured,expected", [(60, 5), (2, 2)])
def test_warmup_has_its_own_bounded_deadline_and_preserves_normal_timeout(tmp_path, configured, expected):
    cfg = AppConfig(tts_backend="piper", output_dir=tmp_path, tts_piper_timeout_seconds=configured)
    calls = []

    class Session:
        def synthesize(self, text, path, **kwargs):
            calls.append((text, Path(path), kwargs))
            Path(path).write_bytes(b"owned warmup fixture")

        def close(self):
            pass

    adapter = TTSAdapter(cfg)
    adapter._streaming_session = PiperSessionFactory(cfg, session_factory=lambda cfg: Session())
    try:
        assert adapter.warmup() is True
        assert calls[0][0] == "准备好了。"
        assert calls[0][2] == {"timeout_seconds": expected}
        assert not calls[0][1].exists()
        assert list(tmp_path.iterdir()) == []
        assert adapter.warmup() is True
        assert len(calls) == 1
        adapter.synthesize("正式回答。", tmp_path / "normal.wav")
        assert calls[-1][2] == {}
        assert cfg.tts_piper_timeout_seconds == configured
    finally:
        adapter.close()


def test_failed_warmup_cleans_only_owned_temp_and_can_retry(tmp_path):
    cfg = AppConfig(tts_backend="piper", output_dir=tmp_path)
    marker = tmp_path / "keep.txt"
    marker.write_text("user file", encoding="utf-8")
    paths = []

    class Session:
        def synthesize(self, text, path, **kwargs):
            paths.append(Path(path))
            Path(path).write_bytes(b"partial")
            if len(paths) == 1:
                raise TimeoutError("fake warmup timeout")

        def close(self):
            pass

    adapter = TTSAdapter(cfg)
    adapter._streaming_session = PiperSessionFactory(cfg, session_factory=lambda cfg: Session())
    try:
        with pytest.raises(TimeoutError, match="fake warmup"):
            adapter.warmup()
        assert list(tmp_path.iterdir()) == [marker]
        assert adapter.warmup() is True
        assert paths[0] != paths[1]
        assert all(not path.exists() for path in paths)
        assert marker.read_text(encoding="utf-8") == "user file"
    finally:
        adapter.close()


def test_runtime_warmup_uses_owned_adapter_without_turn_events(tmp_path):
    calls = []

    class Owned:
        def synthesize(self, text, path):
            raise AssertionError("warmup must use the dedicated adapter hook")

        def warmup(self):
            calls.append("warmup")
            return True

        def close(self):
            calls.append("close")

    class Caller:
        def for_streaming(self):
            return Owned()

        def warmup(self):
            raise AssertionError("do not warm the shared caller adapter")

    runtime = StreamingResponseRuntime(AppConfig(), object(), Caller(), temp_parent=tmp_path, playback_enabled=False)
    try:
        assert runtime.warmup_tts() is True
        assert calls == ["warmup"]
        assert runtime.controller.history == ()
        runtime.begin_turn()
        with pytest.raises(RuntimeError):
            runtime.warmup_tts()
    finally:
        runtime.close()
    with pytest.raises(RuntimeError, match="closed"):
        runtime.warmup_tts()


def test_warmup_default_and_fingerprint_are_explicit(monkeypatch):
    monkeypatch.delenv("STREAM_TTS_WARMUP", raising=False)
    assert AppConfig.from_env().stream_tts_warmup is False
    monkeypatch.setenv("STREAM_TTS_WARMUP", "1")
    assert AppConfig.from_env().stream_tts_warmup is True
    monkeypatch.setenv("STREAM_TTS_WARMUP", "invalid")
    with pytest.raises(ValueError):
        AppConfig.from_env()
    cfg = AppConfig()
    original = _config_view(cfg)
    enabled = _config_view(replace(cfg, stream_tts_warmup=True))
    assert original["stream_tts_warmup"] is False
    assert enabled["stream_tts_warmup"] is True
    assert _fingerprint(original) != _fingerprint(enabled)
