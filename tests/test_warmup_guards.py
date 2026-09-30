"""Additional timeout and no-retry guards; independent original tests unchanged."""

from pathlib import Path

import pytest

from srtp_voice.config import AppConfig
from srtp_voice.piper_session import PersistentPiperSession, PiperSessionFactory
from srtp_voice.streaming_runtime import StreamingResponseRuntime
from srtp_voice.tts import TTSAdapter


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True])
def test_invalid_override_never_starts_a_process_or_creates_output(tmp_path, timeout):
    session = PersistentPiperSession(AppConfig(), command_prefix=("not-launched",))
    output = tmp_path / "absent" / "audio.wav"
    with pytest.raises(ValueError, match="finite and positive"):
        session.synthesize("公开短句。", output, timeout_seconds=timeout)
    assert not output.parent.exists()
    assert not session.started


def test_warmup_creates_missing_parent_and_caps_requested_timeout(tmp_path):
    calls = []

    class Session:
        def synthesize(self, text, path, **kwargs):
            calls.append(kwargs)
            Path(path).write_bytes(b"fixture")

        def close(self):
            pass

    cfg = AppConfig(tts_backend="piper", output_dir=tmp_path / "new-output")
    adapter = TTSAdapter(cfg)
    adapter._streaming_session = PiperSessionFactory(cfg, session_factory=lambda cfg: Session())
    try:
        assert adapter.warmup(timeout_seconds=60) is True
        assert calls == [{"timeout_seconds": 5.0}]
        assert list(cfg.output_dir.iterdir()) == []
    finally:
        adapter.close()


def test_internal_type_error_is_not_retried(tmp_path):
    calls = []

    class Owned:
        def warmup(self):
            calls.append("attempt")
            raise TypeError("inside the adapter")

        def synthesize(self, text, path):
            pass

    class Caller:
        def for_streaming(self):
            return Owned()

    runtime = StreamingResponseRuntime(AppConfig(), object(), Caller(), temp_parent=tmp_path, playback_enabled=False)
    try:
        with pytest.raises(TypeError, match="inside the adapter"):
            runtime.warmup_tts()
        assert calls == ["attempt"]
    finally:
        runtime.close()
