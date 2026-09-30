"""Do not round/coerce an unsupported rate into the 16 kHz fast path."""
from types import SimpleNamespace
import sys

import pytest

from srtp_voice.asr import ASRAdapter
from srtp_voice.config import AppConfig


@pytest.mark.parametrize("rate", [16000.9, "16000", True])
def test_rate_is_not_truncated_or_coerced(monkeypatch, rate):
    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            raise AssertionError("unsupported rate must keep its resampling path")

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    adapter = ASRAdapter(AppConfig(asr_backend="faster_whisper"))
    assert adapter.transcribe_pcm16(b"\x00\x00", sample_rate=rate) is None
