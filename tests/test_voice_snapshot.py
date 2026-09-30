"""Voice tuning options must survive sanitized measurement provenance."""

from dataclasses import replace

from srtp_voice.config import AppConfig
from tools.workbench_latency import _config_view, _fingerprint


def test_voice_tuning_is_recorded_in_config_fingerprint():
    cfg = AppConfig()
    original = _config_view(cfg)
    for key, changed in {
        "tts_piper_persistent": False,
        "stream_natural_boundaries": False,
        "stream_tts_queue_size": 2,
        "stream_sentence_min_chars": 6,
        "stream_sentence_max_chars": 48,
        "stream_sentence_max_wait_seconds": 0.4,
    }.items():
        updated = _config_view(replace(cfg, **{key: changed}))
        assert original[key] == getattr(cfg, key)
        assert updated[key] == changed
        assert _fingerprint(original) != _fingerprint(updated)
