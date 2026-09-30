"""Startup wiring tests for the optional streaming Piper warmup."""

from __future__ import annotations

from pathlib import Path
import pytest

from srtp_voice import streaming_runtime as runtime_module
from srtp_voice.config import AppConfig
from srtp_voice.streaming_runtime import StreamingResponseRuntime


class _Generator:
    def generate_stream(self, *args, turn_id=""):
        return iter(())

    def default_stream_action(self):
        return {}


class _WarmableAdapter:
    """Mirrors TTSAdapter.for_streaming: a separate owned adapter is returned."""

    def __init__(self, *, result: bool = True) -> None:
        self.calls: list[dict] = []
        self.result = result
        self.owned = False

    def for_streaming(self):
        if self.owned:
            return self
        owned = _WarmableAdapter(result=self.result)
        owned.owned = True
        owned.calls = self.calls
        return owned

    def warmup(self, *, timeout_seconds: float = 5.0) -> bool:
        self.calls.append({"timeout": timeout_seconds})
        return self.result

    def synthesize(self, text: str, path: Path) -> None:  # pragma: no cover - unused
        raise AssertionError("warmup must not synthesize through the runtime adapter path")


def test_warmup_tts_uses_owned_adapter_once(tmp_path) -> None:
    adapter = _WarmableAdapter()
    runtime = StreamingResponseRuntime(
        AppConfig(), _Generator(), adapter, temp_parent=tmp_path, playback_enabled=False
    )
    try:
        assert runtime.warmup_tts() is True
        assert adapter.calls == [{"timeout": 5.0}]
    finally:
        runtime.close()


def test_warmup_tts_skips_after_close_and_without_capability(tmp_path) -> None:
    adapter = _WarmableAdapter()
    runtime = StreamingResponseRuntime(
        AppConfig(), _Generator(), adapter, temp_parent=tmp_path, playback_enabled=False
    )
    runtime.close()
    with pytest.raises(RuntimeError, match="closed"):
        runtime.warmup_tts()
    assert adapter.calls == []

    class _Plain:
        def for_streaming(self):
            return _Plain()

        def synthesize(self, text: str, path: Path) -> None:  # pragma: no cover - unused
            raise AssertionError("unused")

    plain = _Plain()
    other = StreamingResponseRuntime(
        AppConfig(), _Generator(), plain, temp_parent=tmp_path, playback_enabled=False
    )
    try:
        assert other.warmup_tts() is False
    finally:
        other.close()


def test_main_runs_warmup_before_turns_only_when_enabled(monkeypatch, tmp_path) -> None:
    import main as main_module

    calls: list[str] = []

    class _Runtime:
        def warmup_tts(self):
            calls.append("warmup")
            return True

    def fake_initialize(*args, **kwargs):
        calls.append("init")
        return _Runtime()

    monkeypatch.setattr(main_module, "_initialize_streaming_runtime", fake_initialize)

    enabled = AppConfig(stream_tts_warmup=True, output_dir=tmp_path)
    runtime = main_module._initialize_streaming_runtime(
        enabled, None, None, playback_enabled=False
    )
    main_module._maybe_warmup_streaming(enabled, runtime)
    assert calls == ["init", "warmup"]

    calls.clear()
    disabled = AppConfig(stream_tts_warmup=False, output_dir=tmp_path)
    runtime_off = main_module._initialize_streaming_runtime(
        disabled, None, None, playback_enabled=False
    )
    main_module._maybe_warmup_streaming(disabled, runtime_off)
    assert calls == ["init"]

    # A failing warmup is reported and never propagates.
    class _Broken:
        def warmup_tts(self):
            raise RuntimeError("synthetic warmup failure")

    main_module._maybe_warmup_streaming(enabled, _Broken())
    assert calls == ["init"]
